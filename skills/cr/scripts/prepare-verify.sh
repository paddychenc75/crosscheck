#!/bin/sh
# prepare-verify.sh — 把合并好的候选分成 verifier 的批次,并抽出几条规则检查做抽查。
#
# 用法: prepare-verify.sh DIR [--per N] [--spot N]
#
#   读取 DIR/candidates.json:编排者合并去重后的全部候选(含 found_by、pass_hits;
#   增量时还有带 from_prev 的上次发现)。给每条候选编号(cid),按文件分批,每批最多 N 条(默认 5),
#   写出 DIR/verify/batch-<n>.json。批次里去掉了 found_by 和 pass_hits,verifier 看不到是谁、查到几遍。
#   --spot N  从 rules-coverage.json 里判为「没违反」的检查中随机抽 N 条(默认 3)交给 verifier 独立复查。
#   「规则可能过时:」「规则冲突:」这类说明不是候选,移到 rules-notes.json,不送去核实。
#
# 输出每个批次文件的路径,最后一行是 batches=<n> candidates=<n> spot=<n>。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "prepare-verify: 需要 python3" >&2; exit 2; }
dir=${1:-}
[ -f "$dir/meta.txt" ] || { echo "prepare-verify: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
shift
per=5; spot=3
while [ $# -gt 0 ]; do
  case "$1" in
    --per) per=${2:-5}; shift 2 ;;
    --spot) spot=${2:-3}; shift 2 ;;
    *) echo "prepare-verify: 未知参数 $1" >&2; exit 2 ;;
  esac
done

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$dir" && pwd) CR_PER=$per CR_SPOT=$spot python3 - <<'PY'
import os, random, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

d = os.environ["CR_DIR"]
per, spot_n = max(1, int(os.environ["CR_PER"])), max(0, int(os.environ["CR_SPOT"]))
cands = crlib.load_json(os.path.join(d, "candidates.json"), [])
notes = crlib.load_json(os.path.join(d, "rules-notes.json"), [])
keep = []
for c in cands:
    s = str(c.get("summary", ""))
    if s.startswith(("规则可能过时", "规则冲突")):
        if any(n.get("summary") == s and n.get("file") == c.get("file", "") for n in notes):
            continue
        notes.append({"kind": "stale" if s.startswith("规则可能过时") else "conflict", "summary": s,
                      "rule_id": c.get("rule_id", ""), "file": c.get("file", ""), "detail": c.get("failure_scenario", "")})
        continue
    keep.append(c)
for i, c in enumerate(keep, 1):
    c.setdefault("cid", "c%d" % i)
crlib.save_json(os.path.join(d, "candidates.json"), keep)
crlib.save_json(os.path.join(d, "rules-notes.json"), notes)

spots = []
cov = crlib.load_json(os.path.join(d, "rules-coverage.json"), {})
pool = cov.get("checked_ok") or []
for i, e in enumerate(random.SystemRandom().sample(pool, min(spot_n, len(pool))), 1):
    a = crlib.parse_lines(e.get("lines")) or (1, 1)
    spots.append({
        "cid": "s%d" % i, "kind": "spot", "category": "rules", "rule_id": e["unit"], "file": e["file"], "line": a[0],
        "severity": "minor",
        "summary": "抽查:%s 在 %s:%s 是否被违反" % (e["unit"], e["file"], e["lines"]),
        "failure_scenario": "rules-auditor 判断 %s 第 %s 行没有违反这条规则:%s" % (e["file"], e["lines"], e.get("text", "")),
        "needs_check": "独立对照规则原文读这几行:确实违反就按规则发现打分并写清违反在哪一行,没有违反打 0 分",
    })

groups = {}
for c in keep + spots:
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
print("batches=%d candidates=%d spot=%d" % (len(batches), len(keep), len(spots)))
PY
