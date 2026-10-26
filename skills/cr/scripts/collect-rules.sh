#!/bin/sh
# collect-rules.sh — 装配这次审查适用的项目规则。规则就是仓库里的 Markdown 文件,不需要编号或元数据。
#
# 用法: collect-rules.sh DIR    DIR 是 collect-diff.sh 的输出目录
#
# 读这些文件(只取覆盖到改动文件的):
#   目录级 REVIEW.md(深的在前)→ 根目录 REVIEW.md → .cr/ignore.md(团队不想再被提醒的模式)
#   → 给 coding agent 的约定:CLAUDE.md、AGENTS.md、.claude/rules/、.cursor/rules/、.github/instructions/ 等
#     (按各自的路径或 frontmatter 范围生效;Copilot 的 excludeAgent: code-review 会被排除)
# 写入 DIR:
#   rules.txt         规则正文,每块以一行头部开始:
#                     === RULES layer=<L3|L2|ignore|external> source=<来源> file=<路径> scope=<glob> ===
#   rules-index.json  每块的文件、来源、正文和它覆盖的改动文件;aggregate.sh 用它核对规则发现的出处和引文
#   rules-meta.txt    blocks=<块数> source=<读自哪一版> errors=<格式错误> excluded=<被排除的文件数>
#
# 读哪一版:本地审查自己的分支读工作区(改了规则马上生效);PR/MR、其他分支、提交范围读 base 那一版,
# 改动本身改不了审查用的规则。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "collect-rules: 需要 python3" >&2; exit 1; }
dir=${1:-}
case "$dir" in -h|--help|"") sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
[ -f "$dir/meta.txt" ] || { echo "collect-rules: $dir 不是 collect-diff.sh 的输出目录" >&2; exit 1; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$dir" && pwd) python3 - <<'PY'
import os, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
import project_rules

d = os.environ["CR_DIR"]
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
root, base = meta.get("repo_root", ""), meta.get("base_sha", "")

ref, source_note = None, "worktree"
if meta.get("mode", "local") != "local":
    if not base or subprocess.run(["git", "-C", root, "cat-file", "-e", base + "^{commit}"], capture_output=True).returncode:
        print("collect-rules: base 无法读取,不回退工作区", file=sys.stderr)
        sys.exit(1)
    ref, source_note = base, "base:" + base[:12]
src = crlib.Source(root, ref)

try:
    changed = [x.strip() for x in open(os.path.join(d, "files.txt"), encoding="utf-8") if x.strip()]
except OSError:
    changed = []

blocks, errors, excluded = [], [], []
def add(layer, record, scope, applies):
    body = record["body"].strip("\n")
    if body.strip() and applies:
        blocks.append({"layer": layer, "source": record["source"], "file": record["file"], "scope": scope,
                       "applies_files": applies, "text": body, "offset": record["offset"]})

records = {r["file"]: r for r in project_rules.discover(src)}
# 目录级 REVIEW.md:改动文件所在的每一级目录,深的在前
dirs = set()
for f in changed:
    p = os.path.dirname(f)
    while p:
        dirs.add(p); p = os.path.dirname(p)
for dd in sorted(dirs, key=lambda x: (-x.count("/"), x)):
    r = records.get(dd + "/REVIEW.md")
    if r:
        add("L3", r, dd + "/**", [f for f in changed if f.startswith(dd + "/")])
for path, layer in (("REVIEW.md", "L2"), (".cr/ignore.md", "ignore")):
    if path in records:
        add(layer, records[path], "**", list(changed))
for path, r in sorted(records.items()):
    if r["source"] in ("review", "ignore"):
        continue
    errors.extend(path + ": " + x for x in r["issues"])
    if r["excluded"]:
        excluded.append(path); continue
    add("external", r, ",".join(r["patterns"]), [f for f in changed if project_rules.matches(r, f)])

out = []
for b in blocks:
    out.append("=== RULES layer=%s source=%s file=%s scope=%s ===" % (b["layer"], b["source"], b["file"], b["scope"]))
    out.append(b["text"])
    out.append("")
with open(os.path.join(d, "rules.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
crlib.save_json(os.path.join(d, "rules-index.json"), blocks)
with open(os.path.join(d, "rules-meta.txt"), "w", encoding="utf-8") as f:
    f.write("blocks=%d\nsource=%s\nerrors=%s\nexcluded=%s\n" % (len(blocks), source_note, ";".join(errors), ",".join(excluded)))
for e in errors:
    print("collect-rules: " + e, file=sys.stderr)
print("rules: blocks=%d files=%s source=%s" % (len(blocks), ",".join(b["file"] for b in blocks) or "-", source_note))
PY
