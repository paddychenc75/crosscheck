#!/bin/sh
# dismiss.sh — 旧的入口,保留兼容。等同于 feedback.sh invalid。
#
# 用法: dismiss.sh DIR FILE LINE [原因]
#   DIR 只用来确定是哪个仓库,判断会记在项目级,对所有分支、MR 和范围都有效。

set -eu
[ $# -ge 3 ] || { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
dir=$1; file=$2; line=$3; reason=${4:-}
root=$(sed -n 's/^repo_root=//p' "$dir/meta.txt" 2>/dev/null | head -n 1)
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "$root" ]; then exec sh "$here/feedback.sh" invalid "$file:$line" "$reason" --repo "$root"; fi
exec sh "$here/feedback.sh" invalid "$file:$line" "$reason"
