#!/bin/sh
# describe.sh — 生成 PR/MR 描述(describe.md、describe.html),只写本地文件,见 describe.py。
#
# 用法: describe.sh DIR [--tested TEXT] [--focus JSON]
#   DIR  collect.sh 输出的材料目录;有 analyst.json(或分批的 analyst-<批次>.json)时先校验再渲染
set -eu
command -v python3 >/dev/null 2>&1 || { echo "describe: 需要 python3" >&2; exit 1; }
exec python3 "$(cd "$(dirname "$0")" && pwd -P)/describe.py" "$@"
