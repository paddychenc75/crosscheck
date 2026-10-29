#!/bin/sh
# snippet.sh — 从被审查的那一版代码里摘出某一行附近的原文,带行号,供报告引用。
#
# 用法: snippet.sh DIR FILE LINE [BEFORE] [AFTER]
#
#   DIR     collect-diff.sh 的输出目录
#   FILE    仓库相对路径,同 files.txt 中的写法
#   LINE    出问题的行号(新侧)
#   BEFORE  往前带几行,默认 3
#   AFTER   往后带几行,默认 3
#
# 输出形如:
#      40 |   const total = rows.length;
#   >  42 |   const pageCount = Math.floor(total / pageSize);
#      43 |   return rows.slice(0, pageCount * pageSize);
# 出问题的那一行以 > 标记。被审查的代码不在工作区时从 code_ref 读取,不会读到别的版本。

set -eu

die() { printf 'snippet: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
[ $# -ge 3 ] || die "用法: snippet.sh DIR FILE LINE [BEFORE] [AFTER]"
dir=$1; file=$2; line=$3; before=${4:-3}; after=${5:-3}
[ -f "$dir/meta.txt" ] || die "$dir 不是 collect-diff.sh 的输出目录"
for n in "$line" "$before" "$after"; do
  case "$n" in ""|*[!0-9]*) die "行号和行数必须是非负整数" ;; esac
done
[ "$line" -ge 1 ] || die "行号从 1 开始"

meta() { sed -n "s/^$1=//p" "$dir/meta.txt" | head -n 1; }
root=$(meta repo_root)
code_ref=$(meta code_ref)
start=$((line - before)); [ "$start" -ge 1 ] || start=1
end=$((line + after))

number() {
  awk -v s="$start" -v e="$end" -v t="$line" '
    NR > e { exit }
    NR >= s { printf "%s%5d | %s\n", (NR == t ? ">" : " "), NR, $0; shown = 1 }
    END { if (!shown) exit 3 }'
}

if [ "$(meta head_checked_out)" = yes ]; then
  [ -f "$root/$file" ] || die "工作区里没有 $file"
  number < "$root/$file" || die "$file 没有第 $line 行"
else
  [ "$code_ref" != UNAVAILABLE ] && [ -n "$code_ref" ] || die "被审查的代码不在本地,请从 diff.patch 中摘录"
  git -C "$root" show "$code_ref:$file" 2>/dev/null > "$dir/.snippet.tmp" || { rm -f "$dir/.snippet.tmp"; die "$code_ref 中没有 $file"; }
  number < "$dir/.snippet.tmp" || { rm -f "$dir/.snippet.tmp"; die "$file 没有第 $line 行"; }
  rm -f "$dir/.snippet.tmp"
fi
