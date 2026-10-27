#!/bin/sh
# settings.sh — 读取项目 REVIEW.md 头部的审查设置,并执行其中能由脚本执行的部分。
#
# 用法: settings.sh apply DIR ROOT [REF]
#         由 collect-diff.sh 在过滤文件之后调用:写出 DIR/settings.json,
#         并把 skip 命中的文件从 diff.patch / files.txt 里移到 skipped.txt(原因 review-skip)。
#         REF 为空时读工作区的 REVIEW.md,否则读该 commit 里的(PR/MR、分支、范围审查都读 base)。
#       settings.sh show ROOT [REF]   只打印解析结果(JSON)
#
# REVIEW.md 头部支持的设置:
#   skip:       ["src/gen/**"]                   不审查的路径
#   limits:     { defects: 10, rules: 5, minor: 3 }   条数上限(defects/intent/rules/simplify/critical/major/minor)

set -eu

mode=${1:-}
case "$mode" in
  apply) [ $# -ge 3 ] || { echo "settings: 用法 settings.sh apply DIR ROOT [REF]" >&2; exit 2; } ;;
  show) [ $# -ge 2 ] || { echo "settings: 用法 settings.sh show ROOT [REF]" >&2; exit 2; } ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_ARGS=$(printf '%s\n' "$@") python3 - <<'PY'
import json, os, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

args = os.environ["CR_ARGS"].split("\n")
mode = args[0]
if mode == "apply":
    out, root, ref = args[1], args[2], (args[3] if len(args) > 3 else "")
else:
    out, root, ref = "", args[1], (args[2] if len(args) > 2 else "")

if ref and subprocess.run(["git", "-C", root, "cat-file", "-e", ref + "^{commit}"], capture_output=True).returncode:
    print("settings: base 无法读取，不回退工作区", file=sys.stderr)
    sys.exit(1)
src = crlib.Source(root, ref or None)
text = src.read("REVIEW.md")
fm = crlib.parse_frontmatter(text)[0] if text else {}
settings = {
    "source": ("base:" + ref[:12]) if ref else "worktree",
    "found": text is not None,
    "skip": crlib.as_list(fm.get("skip")),
    "limits": crlib.as_map(fm.get("limits")),
}

if mode == "show":
    print(json.dumps(settings, ensure_ascii=False, indent=2))
    sys.exit(0)

crlib.save_json(os.path.join(out, "settings.json"), settings)
if not settings["skip"]:
    sys.exit(0)

# 按文件把 diff.patch 拆开,skip 命中的移出去。
patch = os.path.join(out, "diff.patch")
chunks, cur = [], None
with open(patch, encoding="utf-8", errors="surrogateescape") as f:
    for line in f:
        if line.startswith("diff --git "):
            cur = [line]
            chunks.append(cur)
        elif cur is not None:
            cur.append(line)
keep, moved = [], []
for c in chunks:
    path = c[0].rstrip("\n").split(" b/", 1)[-1]
    (moved if crlib.any_match(settings["skip"], path) else keep).append((path, c))
if not moved:
    sys.exit(0)
with open(patch, "w", encoding="utf-8", errors="surrogateescape") as f:
    for _, c in keep:
        f.writelines(c)
with open(os.path.join(out, "files.txt"), "w", encoding="utf-8") as f:
    for p, _ in keep:
        f.write(p + "\n")
with open(os.path.join(out, "skipped.txt"), "a", encoding="utf-8") as f:
    for p, _ in moved:
        f.write("%s\treview-skip\n" % p)
PY
