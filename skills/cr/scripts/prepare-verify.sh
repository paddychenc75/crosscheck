#!/bin/sh
# prepare-verify.sh — 把合并好的候选分成 verifier 的批次。
#
# 用法: prepare-verify.sh DIR [--per N]
#
#   读取 DIR/candidates.json:编排者合并去重后的全部候选(含 found_by、pass_hits;
#   增量时还有带 from_prev 的上次发现)。给每条候选编号(cid),按文件分批,每批最多 N 条(默认 5),
#   写出 DIR/verify/batch-<n>.json。批次里去掉了 found_by 和 pass_hits,verifier 看不到是谁、查到几遍。
#   「规则可能过时:」「规则冲突:」这类说明不是候选,移到 rules-notes.json,不送去核实。
#
# 输出每个批次文件的路径,最后一行是 batches=<n> candidates=<n>。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "prepare-verify: 需要 python3" >&2; exit 2; }
dir=${1:-}
[ -f "$dir/meta.txt" ] || { echo "prepare-verify: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
shift
per=5
while [ $# -gt 0 ]; do
  case "$1" in
    --per) per=${2:-5}; shift 2 ;;
    *) echo "prepare-verify: 未知参数 $1" >&2; exit 2 ;;
  esac
done

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$dir" && pwd) CR_PER=$per python3 - <<'PY'
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

d = os.environ["CR_DIR"]
per = max(1, int(os.environ["CR_PER"]))
cands = crlib.load_json(os.path.join(d, "candidates.json"), [])
notes = crlib.load_json(os.path.join(d, "rules-notes.json"), [])
keep = []
for c in cands:
    s = str(c.get("summary", ""))
    if s.startswith(("规则可能过时", "规则冲突")):
        if any(n.get("summary") == s and n.get("file") == c.get("file", "") for n in notes):
            continue
        notes.append({"kind": "stale" if s.startswith("规则可能过时") else "conflict", "summary": s,
                      "rule_file": c.get("rule_file", ""), "file": c.get("file", ""), "detail": c.get("failure_scenario", "")})
        continue
    keep.append(c)
for i, c in enumerate(keep, 1):
    c.setdefault("cid", "c%d" % i)
crlib.save_json(os.path.join(d, "candidates.json"), keep)
crlib.save_json(os.path.join(d, "rules-notes.json"), notes)

groups = {}
for c in keep:
    groups.setdefault(c.get("file", ""), []).append(c)
batches, cur = [], []
for f in sorted(groups):
    for c in groups[f]:
        if len(cur) >= per:
            batches.append(cur); cur = []
        cur.append({k: v for k, v in c.items() if k not in ("found_by", "pass_hits")})
if cur:
    batches.append(cur)
vd = os.path.join(d, "verify")
os.makedirs(vd, exist_ok=True)
for name in os.listdir(vd):
    if name.startswith(("batch-", "result-")):
        os.remove(os.path.join(vd, name))
# 新一轮核实不得误用上一轮的报告和展示分组。
for name in ("final.json", "findings.json", "rejected.json", "report.json", "display-groups.json"):
    path = os.path.join(d, name)
    if os.path.isfile(path): os.remove(path)
for i, b in enumerate(batches, 1):
    p = os.path.join(vd, "batch-%d.json" % i)
    crlib.save_json(p, b)
    print(p)
print("batches=%d candidates=%d" % (len(batches), len(keep)))
PY
