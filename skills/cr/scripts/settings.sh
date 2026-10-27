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
exec python3 "$(cd "$(dirname "$0")" && pwd)/settings_cli.py" "$@"
