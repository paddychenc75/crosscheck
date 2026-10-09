#!/bin/sh
# save-review.sh — 把一次审查的报告和发现存入审查记录,供以后查看和做增量审查。
#
# 用法: save-review.sh DIR
#
#   DIR  collect-diff.sh 的输出目录,其中需要已经写好:
#          report.md      给用户看的报告
#          findings.json  本次审查结束时仍然成立的全部发现(含从上次带过来的)
#
# 写入 ${CROSSCHECK_HOME:-~/.crosscheck}/reviews/<仓库>/<目标>/<时间>/,并更新同级的两个指针文件:
#   latest    最近一次审查
#   baseline  最近一次覆盖所有维度的审查,增量审查以它为基准(--only 的审查不会成为基准)
# 最后一行输出保存到的目录。

set -eu

die() { printf 'save-review: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") die "需要指定 collect-diff.sh 的输出目录" ;;
esac
dir=$1
[ -f "$dir/meta.txt" ] || die "$dir 不是 collect-diff.sh 的输出目录"
[ -f "$dir/report.md" ] || die "$dir 中没有 report.md"
[ -f "$dir/findings.json" ] || die "$dir 中没有 findings.json"

meta() { sed -n "s/^$1=//p" "$dir/meta.txt" | head -n 1; }
store=$(meta store_dir)
[ -n "$store" ] || die "meta.txt 中没有 store_dir"

stamp=$(date +%Y%m%d-%H%M%S)
dest="$store/$stamp"
mkdir -p "$dest"
cp "$dir/report.md" "$dir/findings.json" "$dir/meta.txt" "$dir/hashes.txt" "$dest/"
[ -f "$dir/incremental.txt" ] && cp "$dir/incremental.txt" "$dest/"
printf '%s\n' "$stamp" > "$store/latest"
if [ "$(meta only)" = all ]; then printf '%s\n' "$stamp" > "$store/baseline"; fi
printf '%s\n' "$dest"
