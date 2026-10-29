#!/bin/sh
# collect.sh — 为 PR/MR 描述收集材料(diff、需求来源、PR/MR 信息、讨论、描述用的事实),见 collect.py。
#
# 用法: collect.sh [target] [collect-diff.sh 的选项]...
#   选项和握手提示同 <plugin_dir>/skills/review/scripts/collect-diff.sh;最后一行是材料目录
set -eu
command -v python3 >/dev/null 2>&1 || { echo "describe: 需要 python3" >&2; exit 1; }
exec python3 "$(cd "$(dirname "$0")" && pwd -P)/collect.py" "$@"
