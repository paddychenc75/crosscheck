"""增量审查:选比较对象,再比较每个文件 diff 的哈希,写出 incremental.txt。由 collect-diff.sh 调用。

用法: python3 incremental.py pick STORE OUT DEPTH    打印比较对象的目录名,没有可用的记录时不打印
      python3 incremental.py OUT PREV_DIR ROOT [REF]
  incremental.txt  status<TAB>path,status 是 unchanged / changed / new / removed / dependent
  dependent:文件本身没变,但引用了这次被改动的符号(函数、类型等),一起重审。按名字匹配,宁宽勿漏。
REF 是被审查的提交;审查工作区时为空。
"""
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import crlib

RANK = {"quick": 1, "standard": 2, "deep": 3}

# 刻意宽泛:标识符至少 4 个字符的各种定义写法
DEF = re.compile(r"\b(?:def|function|func|class|interface|type|struct|enum|trait|fn|const|let|var)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]{3,})")


def complete(rdir):
    """有完整性检查结果且没有任何缺项:缺回复、覆盖受限、预算达限的审查没有看完改动,不能把它的文件当作审过。"""
    text = crlib.read_text(os.path.join(rdir, "completeness.txt"))
    return bool(text) and any(line.startswith("OK") for line in text.splitlines()) and "INCOMPLETE:" not in text


def pick(store, out, depth):
    """最近一次覆盖所有维度、深度不低于本次、上下文相同、流程完整的审查。没有单独的指针:记录本身就是依据。"""
    context = crlib.read_text(os.path.join(out, "context-hash.txt"))
    names = os.listdir(store) if os.path.isdir(store) else []
    for name in sorted((n for n in names if crlib.STAMP.fullmatch(n)), reverse=True):
        rdir = os.path.join(store, name)
        meta = crlib.read_kv(os.path.join(rdir, "meta.txt"))
        if (meta.get("only") == "all" and RANK.get(meta.get("depth"), 2) >= RANK.get(depth, 2)
                and crlib.read_text(os.path.join(rdir, "context-hash.txt")) == context
                and os.path.isfile(os.path.join(rdir, "hashes.txt")) and complete(rdir)):
            return name
    return ""


def read_hashes(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "\t" in line:
                h, p = line.rstrip("\n").split("\t", 1)
                out[p] = h
    return out


def compare(old, new):
    status = []
    for p, h in new.items():
        status.append(["new" if p not in old else "unchanged" if old[p] == h else "changed", p])
    status += [["removed", p] for p in old if p not in new]
    return status


def dependents(out, root, ref, status):
    split = os.path.join(out, "split")
    with open(os.path.join(split, "index"), encoding="utf-8") as f:
        idx = dict(l.rstrip("\n").split("\t", 1)[::-1] for l in f if "\t" in l)
    syms = set()
    for s, p in status:
        if s in ("changed", "new") and p in idx:
            with open(os.path.join(split, idx[p] + ".patch"), encoding="utf-8", errors="replace") as f:
                for line in f:
                    # 改动行、hunk 里的上下文行和 hunk 头里的函数名:改了函数体时,定义那一行通常只是上下文
                    if (line[:1] in "+- " and not line.startswith(("+++", "---"))) or line.startswith("@@"):
                        syms.update(DEF.findall(line))
    syms = sorted(syms)
    unchanged = [p for s, p in status if s == "unchanged"]
    # 删除文件时当前 split 已没有它的定义;没有依据缩小范围,让剩余文件全部重审。
    if any(s == "removed" for s, _ in status):
        return set(unchanged)
    if not syms or not unchanged:
        return set()
    # 避免超长参数,每组都搜索;命令失败时保守地重审全部未变文件。
    found = set()
    for start in range(0, len(syms), 60):
        cmd = ["git", "-C", root, "grep", "-l", "-w", "-F"]
        if not ref:
            cmd += ["--untracked"]
        cmd += sum([["-e", x] for x in syms[start:start + 60]], [])
        cmd += ([ref] if ref else []) + ["--"] + unchanged
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode not in (0, 1):
            return set(unchanged)
        found.update(line[len(ref) + 1:] if ref and line.startswith(ref + ":") else line for line in r.stdout.splitlines())
    return found


def main(out, prev, root, ref=""):
    status = compare(read_hashes(os.path.join(prev, "hashes.txt")), read_hashes(os.path.join(out, "hashes.txt")))
    try:
        dep = dependents(out, root, ref, status)
    except (OSError, ValueError):
        dep = {p for s, p in status if s == "unchanged"}
    # 未变发现仍可能有新反馈或外部证据变化;让主文件走现有完整审查流程。
    history = crlib.load_json(os.path.join(out, "history.json"), [])
    source = crlib.Source(root, ref or None)
    states = {p: "dependent" if s == "unchanged" and p in dep else s for s, p in status}
    for old in crlib.load_json(os.path.join(prev, "findings.json"), []):
        if crlib.recheck_reason(old, states, history, source):
            dep.add(old.get("file"))
    with open(os.path.join(out, "incremental.txt"), "w", encoding="utf-8") as f:
        for s, p in status:
            f.write("%s\t%s\n" % ("dependent" if s == "unchanged" and p in dep else s, p))


if __name__ == "__main__":
    if sys.argv[1] == "pick":
        print(pick(*sys.argv[2:5]))
    else:
        main(*sys.argv[1:5])
