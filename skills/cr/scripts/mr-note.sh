#!/bin/sh
# mr-note.sh — 生成 MR 说明(给 MR 评审人看,开发者贴进 MR 描述),见 mr_note.py。
#
# 用法: mr-note.sh DIR [--latest]
#   DIR  save-review.sh 输出的记录目录(dir=);表态或 --fix 之后加 --latest 重新生成最近一次的
set -eu
command -v python3 >/dev/null 2>&1 || { echo "mr-note: 需要 python3" >&2; exit 1; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/mr_note.py" "$@"
