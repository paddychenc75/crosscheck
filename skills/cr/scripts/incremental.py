"""增量审查:和上次的审查比较每个文件 diff 的哈希,写出 incremental.txt。由 collect-diff.sh 调用。

用法: python3 incremental.py OUT PREV_DIR ROOT [REF]
  incremental.txt  status<TAB>path,status 是 unchanged / changed / new / removed / dependent
  dependent:文件本身没变,但引用了这次被改动的符号(函数、类型等),一起重审。按名字匹配,宁宽勿漏。
REF 是被审查的提交;审查工作区时为空。
"""
import os
import re
import subprocess
import sys

# 刻意宽泛:标识符至少 4 个字符的各种定义写法
DEF = re.compile(r"\b(?:def|function|func|class|interface|type|struct|enum|trait|fn|const|let|var)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]{3,})")


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
    syms = sorted(syms)[:60]
    unchanged = [p for s, p in status if s == "unchanged"]
    if not syms or not unchanged:
        return set()
    cmd = ["git", "-C", root, "grep", "-l", "-w", "-F"] + sum([["-e", x] for x in syms], [])
    cmd += ([ref] if ref else []) + ["--"] + unchanged
    r = subprocess.run(cmd, capture_output=True, text=True)
    return {line.split(":", 1)[1] if ref and line.startswith(ref + ":") else line for line in r.stdout.splitlines()}


def main(out, prev, root, ref=""):
    status = compare(read_hashes(os.path.join(prev, "hashes.txt")), read_hashes(os.path.join(out, "hashes.txt")))
    try:
        dep = dependents(out, root, ref, status)
    except (OSError, ValueError):
        dep = set()
    with open(os.path.join(out, "incremental.txt"), "w", encoding="utf-8") as f:
        for s, p in status:
            f.write("%s\t%s\n" % ("dependent" if s == "unchanged" and p in dep else s, p))


if __name__ == "__main__":
    main(*sys.argv[1:5])
