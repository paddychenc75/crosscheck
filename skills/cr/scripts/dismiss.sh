#!/bin/sh
# dismiss.sh — 把一条被用户否定的发现从保存的记录里移到"被否定"清单。
#
# 用法: dismiss.sh DIR FILE LINE [原因]
#
#   DIR   保存审查记录的目录(save-review.sh 输出的 dir)
#   FILE  发现所在的文件,同 findings.json 里的写法
#   LINE  发现所在的行号
#
# 从 DIR/findings.json 里去掉这条发现,追加到 DIR/dismissed.json,并记下时间和原因。
# 之后的增量审查不会再把它当作遗留问题,stats.sh 会把它计为"被否定"。需要 python3。

set -eu

die() { printf 'dismiss: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
[ $# -ge 3 ] || die "用法: dismiss.sh DIR FILE LINE [原因]"
dir=$1; file=$2; line=$3; reason=${4:-}
[ -f "$dir/findings.json" ] || die "$dir 中没有 findings.json"
case "$line" in ""|*[!0-9]*) die "行号必须是数字" ;; esac
command -v python3 >/dev/null 2>&1 || die "需要 python3。没有的话,手动把这条发现从 $dir/findings.json 里删掉"

CR_DIR="$dir" CR_FILE="$file" CR_LINE="$line" CR_REASON="$reason" python3 - <<'PY'
import datetime, json, os, sys
d = os.environ["CR_DIR"]; file = os.environ["CR_FILE"]; line = int(os.environ["CR_LINE"]); reason = os.environ.get("CR_REASON", "")
fp = os.path.join(d, "findings.json"); dp = os.path.join(d, "dismissed.json")
with open(fp, encoding="utf-8") as f:
    findings = json.load(f)
try:
    with open(dp, encoding="utf-8") as f:
        dismissed = json.load(f)
except Exception:
    dismissed = []
hit = [x for x in findings if x.get("file") == file and int(x.get("line", -1)) == line]
if not hit:
    print("dismiss: 在 findings.json 里没有找到 %s:%d" % (file, line), file=sys.stderr); sys.exit(1)
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
for x in hit:
    x = dict(x); x["dismissed_at"] = now
    if reason: x["dismiss_reason"] = reason
    dismissed.append(x)
keep = [x for x in findings if x not in hit]
with open(dp, "w", encoding="utf-8") as f:
    json.dump(dismissed, f, ensure_ascii=False, indent=2); f.write("\n")
with open(fp, "w", encoding="utf-8") as f:
    json.dump(keep, f, ensure_ascii=False, indent=2); f.write("\n")
print("已标记为被否定:%s:%d(%d 条)" % (file, line, len(hit)))
PY
