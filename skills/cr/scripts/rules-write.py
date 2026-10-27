#!/usr/bin/env python3
"""rules-write.py — 把用户选中的规则候选写进仓库的 .agents/rules/<主题>.md。

用法: python3 rules-write.py WORK_DIR --pick 1,3,5 [--edit 3=新的措辞] [--repo DIR]

  WORK_DIR  mine-mrs.sh 的工作目录(里面有 mine-check.py 写的 candidates.json)
  --pick    用户选中的编号
  --edit    用户改过措辞的,按 编号=规则 传入(可多次)
  --repo    仓库根目录,默认取 WORK_DIR/meta.txt 的 repo_root

按主题写文件,frontmatter 的 paths 写适用范围(整个仓库时不写 frontmatter);同一主题范围不同时另起
<主题>-2.md 这样的文件。每条规则一个「## 标题」块:规则、原因、严重级别、反例 / 正例(有才写)、不要报,
最后一行 <!-- 来源:!123 !145 --> 记下证据所在的 MR。「不要报」类的候选写进文件末尾的「## 不要报」一节。
一个文件超过 15 条规则或 4000 字符时提示拆分(Copilot 这类工具只读前 4000 字符,规则太多重要的会被稀释)。
只改工作区,不 git add、不提交。打印改过的文件和写进去的内容。
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True
import crlib

SUPPRESS_HEAD = "## 不要报"
MAX_RULES, MAX_CHARS = 15, 4000


def source_note(c):
    refs = ["!%s" % m for m in c.get("mrs", [])]
    if any(e.get("feedback") for e in c.get("evidence", [])):
        refs.append("crosscheck 判断记录")
    return "<!-- 来源:%s -->" % " ".join(refs) if refs else ""


def code(s):
    s = str(s).strip()
    return "`%s`" % s.replace("`", "'") if s else ""


def block_of(c):
    """一条规则一个 ## 块。"""
    lines = ["## " + c.get("title", c["rule"][:24]), ""]
    lines.append("- **规则**:" + c["rule"])
    if c.get("why"):
        lines.append("- **原因**:" + c["why"])
    lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
    if c.get("bad_example"):
        lines.append("- **反例**:" + code(c["bad_example"]))
    if c.get("good_example"):
        lines.append("- **正例**:" + code(c["good_example"]))
    lines.append("- **不要报**:" + c.get("exceptions", ""))
    note = source_note(c)
    if note:
        lines.append(note)
    return lines


def suppress_line(c):
    note = source_note(c)
    return "- " + c["rule"] + (("(" + c["why"] + ")") if c.get("why") else "") + (" " + note if note else "")


def pick_file(rules_dir, topic, paths):
    want = sorted(paths)
    n = 1
    while True:
        name = topic + (".md" if n == 1 else "-%d.md" % n)
        p = os.path.join(rules_dir, name)
        if not os.path.exists(p):
            return p, True
        fm, _ = crlib.parse_frontmatter(open(p, encoding="utf-8").read())
        have = sorted(crlib.as_list(fm.get("paths") or fm.get("globs")) or ["**"])
        if have == want:
            return p, False
        n += 1


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("work")
    ap.add_argument("--pick", required=True)
    ap.add_argument("--edit", action="append", default=[])
    ap.add_argument("--repo", default="")
    a = ap.parse_args()

    cands = {c["no"]: c for c in crlib.load_json(os.path.join(a.work, "candidates.json"), [])}
    if not cands:
        print("rules-write: %s 里没有候选,请先运行 mine-check.py" % a.work, file=sys.stderr)
        return 1
    try:
        picks = [int(x) for x in a.pick.replace("，", ",").split(",") if x.strip()]
    except ValueError:
        print("rules-write: --pick 应为逗号分隔的编号", file=sys.stderr)
        return 2
    missing = [n for n in picks if n not in cands]
    if missing:
        print("rules-write: 没有这些编号:%s" % ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    for e in a.edit:
        k, _, v = e.partition("=")
        if not k.strip().isdigit() or int(k) not in cands or not v.strip():
            print("rules-write: --edit 应为 编号=新的规则,且编号在候选里: %s" % e, file=sys.stderr)
            return 2
        cands[int(k)] = dict(cands[int(k)], rule=crlib.norm(v))

    root = a.repo or crlib.read_kv(os.path.join(a.work, "meta.txt")).get("repo_root", "")
    if not root or not os.path.isdir(root):
        print("rules-write: 找不到仓库目录,请用 --repo 指定", file=sys.stderr)
        return 1
    rules_dir = os.path.join(root, ".agents", "rules")
    os.makedirs(rules_dir, exist_ok=True)

    touched = {}
    for n in picks:
        c = cands[n]
        paths = c.get("paths") or ["**"]
        path, new = pick_file(rules_dir, c.get("topic") or "general", paths)
        if path not in touched:
            if new:
                head = ""
                if paths != ["**"]:
                    head = "---\npaths: [%s]\n---\n" % ", ".join('"%s"' % p for p in paths)
                title = os.path.splitext(os.path.basename(path))[0]
                text = head + "# %s\n\n由 crosscheck 从 MR 评论沉淀,经人工挑选。每条规则后面的注释是证据所在的 MR。\n" % title
            else:
                text = open(path, encoding="utf-8").read()
            touched[path] = text.rstrip("\n").split("\n")
        lines = touched[path]
        if c["kind"] == "suppress":
            if SUPPRESS_HEAD not in [x.strip() for x in lines]:
                lines += ["", SUPPRESS_HEAD, ""]
            lines.append(suppress_line(c))
        else:
            heads = [i for i, x in enumerate(lines) if x.strip() == SUPPRESS_HEAD]
            at = heads[0] if heads else len(lines)
            while at > 0 and not lines[at - 1].strip():
                at -= 1
            lines[at:at] = [""] + block_of(c) + ([""] if heads else [])
    for path, lines in touched.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
        rel = os.path.relpath(path, root)
        print("WROTE: %s" % rel)
        print("\n".join(lines))
        print()
        text = "\n".join(lines)
        n = sum(1 for x in lines if x.startswith("## ") and x.strip() != SUPPRESS_HEAD)
        if n > MAX_RULES or len(text) > MAX_CHARS:
            print("WARN: %s 已有 %d 条规则、%d 个字符,超过约 %d 条或 %d 字符,建议按目录或主题拆成几个文件" % (rel, n, len(text), MAX_RULES, MAX_CHARS))
    print("NOTE: 只改了工作区,没有 git add 或提交。crosscheck 审查时会读 .agents/rules/;"
          "Claude Code、Cursor 写代码时不会自动读这个目录,需要的话可以在 AGENTS.md 或 CLAUDE.md 里加一行 "
          "`@.agents/rules/<文件>` 引入(先问用户)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
