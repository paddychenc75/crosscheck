#!/bin/sh
# prepare-verify.sh — 把合并好的候选分成 verifier 的批次。
#
# 用法: prepare-verify.sh DIR [--per N]
#
#   读取 DIR/candidates.json:编排者合并去重后的全部候选(含 found_by、pass_hits;
#   增量时还有带 from_prev 的上次发现)。给每条候选编号(cid),按文件分批,每批最多 N 条(默认 3);
#   严重(critical)的候选单独一批,
#   写出 DIR/verify/batch-<n>.json。批次里去掉了 found_by、pass_hits 和 pass,verifier 看不到是谁、哪一遍、查到几遍。
#   带 from_prev 的上次发现,去掉上次核实留下的分数、结论、依据、编号等,只留发现本身,保证这次是独立核实。
#   「规则可能过时:」「规则冲突:」这类说明不是候选,移到 rules-notes.json,不送去核实。
#
# 输出每个批次文件的路径,最后一行是 batches=<n> candidates=<n>。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "prepare-verify: 需要 python3" >&2; exit 2; }
dir=${1:-}
[ -f "$dir/meta.txt" ] || { echo "prepare-verify: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
shift
per=3
while [ $# -gt 0 ]; do
  case "$1" in
    --per) per=${2:-3}; shift 2 ;;
    *) echo "prepare-verify: 未知参数 $1" >&2; exit 2 ;;
  esac
done
exec python3 "$(cd "$(dirname "$0")" && pwd)/prepare_verify.py" "$dir" "$per"
