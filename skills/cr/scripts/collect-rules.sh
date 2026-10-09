#!/bin/sh
# collect-rules.sh — 从各个 coding agent 的规则文件中收集项目的书面约定,
# 只保留作用域覆盖改动文件的那些。
#
# 用法: collect-rules.sh [changed-files-list]
#   changed-files-list: 每行一个仓库相对路径的文件(默认:stdin)
#
# 向 stdout 打印若干块:
#   === RULES source=<origin> file=<path> scope=<glob> [changed=yes] ===
#   <规则正文,已去掉 frontmatter>
# changed=yes 表示该规则文件本身在改动文件列表中。
#
# 来源: REVIEW.md、.cr/ignore.md、CLAUDE.md、.claude/rules、AGENTS.md、
# .cursor/rules、.cursorrules、GitHub Copilot instructions、GEMINI.md、
# .windsurfrules、CONVENTIONS.md。内容完全相同的文件只输出一次。

set -eu

list=${1:--}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/cr-rules.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
changed="$tmp/changed"
seen="$tmp/seen"
: > "$seen"
if [ "$list" = "-" ]; then cat > "$changed"; else cat "$list" > "$changed"; fi

# 仓库根目录:优先取 collect-diff.sh 记在 meta.txt 里的,这样不要求从仓库目录里运行。
root=""
if [ "$list" != "-" ] && [ -f "$(dirname "$list")/meta.txt" ]; then
  root=$(sed -n 's/^repo_root=//p' "$(dirname "$list")/meta.txt" | head -n 1)
fi
[ -n "$root" ] && [ -d "$root" ] || root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$root"

# 某个 frontmatter key 的值,每行一个。支持标量、逗号列表、
# [行内, 列表]、"- item" 列表,以及 {a,b} 花括号备选。
fm_values() {
  awk -v key="$1" '
  function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
  function unquote(s,   q) { q = sprintf("%c", 39); s = trim(s); gsub(/^"|"$/, "", s); gsub("^" q "|" q "$", "", s); return s }
  function expand(p,   i, c, depth, s, e, pre, post, body, n, parts, k, cur) {
    s = 0; depth = 0
    for (i = 1; i <= length(p); i++) {
      c = substr(p, i, 1)
      if (c == "{") { if (depth == 0) s = i; depth++ }
      else if (c == "}") { depth--; if (depth == 0) { e = i; break } }
    }
    if (s == 0 || depth != 0) { print p; return }
    pre = substr(p, 1, s - 1); post = substr(p, e + 1); body = substr(p, s + 1, e - s - 1)
    n = 0; cur = ""; depth = 0
    for (i = 1; i <= length(body); i++) {
      c = substr(body, i, 1)
      if (c == "{") depth++
      if (c == "}") depth--
      if (c == "," && depth == 0) { parts[++n] = cur; cur = "" } else cur = cur c
    }
    parts[++n] = cur
    for (k = 1; k <= n; k++) expand(pre parts[k] post)
  }
  function emit(v,   i, c, depth, cur) {
    v = trim(v)
    sub(/^\[/, "", v); sub(/\]$/, "", v)
    cur = ""; depth = 0
    for (i = 1; i <= length(v); i++) {
      c = substr(v, i, 1)
      if (c == "{") depth++
      if (c == "}") depth--
      if (c == "," && depth == 0) { cur = unquote(cur); if (cur != "") expand(cur); cur = "" } else cur = cur c
    }
    cur = unquote(cur); if (cur != "") expand(cur)
  }
  NR == 1 && $0 !~ /^---[ \t]*$/ { exit }
  NR == 1 { next }
  /^---[ \t]*$/ { exit }
  {
    if (index($0, key) == 1 && substr($0, length(key) + 1) ~ /^[ \t]*:/) {
      inkey = 1; v = $0; sub(/^[^:]*:/, "", v); emit(v); next
    }
    if (inkey && $0 ~ /^[ \t]*-[ \t]+/) { v = $0; sub(/^[ \t]*-[ \t]+/, "", v); emit(v); next }
    if ($0 ~ /^[^ \t#]/) inkey = 0
  }' "$2"
}

body() {
  awk 'NR == 1 && /^---[ \t]*$/ { fm = 1; next } fm && /^---[ \t]*$/ { fm = 0; next } !fm' "$1"
}

# 是否有改动的文件匹配该 glob?
glob_hits() {
  g=${1#./}; g=${g#/}
  g2=$(printf '%s' "$g" | sed 's#\*\*/##g')
  while IFS= read -r f; do
    # shellcheck disable=SC2254
    case "$f" in $g|$g2) return 0 ;; esac
  done < "$changed"
  return 1
}

emit() { # source file scope
  [ -f "$2" ] && [ -s "$2" ] || return 0
  h=$(git hash-object "$2" 2>/dev/null || cksum < "$2")
  if grep -qxF "$h" "$seen"; then
    printf '=== DUPLICATE file=%s (内容与上面某个块相同,已跳过) ===\n\n' "$2"
    return 0
  fi
  printf '%s\n' "$h" >> "$seen"
  mark=""
  if grep -qxF "$2" "$changed"; then mark=" changed=yes"; fi
  printf '=== RULES source=%s file=%s scope=%s%s ===\n' "$1" "$2" "$3" "$mark"
  body "$2"
  printf '\n'
}

# 输出一个作用域来自 frontmatter glob 列表的文件。没有 glob 时由 $4 决定:
# "always" 表示处处适用,"never" 表示跳过。
emit_scoped() { # source file key default
  globs=$(fm_values "$3" "$2")
  if [ -z "$globs" ]; then
    [ "$4" = always ] && emit "$1" "$2" "**"
    return 0
  fi
  hit=""
  old_ifs=$IFS; IFS='
'
  set -f # glob 必须原样(不展开)传给 glob_hits
  for g in $globs; do
    IFS=$old_ifs
    if glob_hits "$g"; then hit="${hit:+$hit,}$g"; fi
  done
  set +f
  IFS=$old_ifs
  if [ -n "$hit" ]; then emit "$1" "$2" "$hit"; fi
  return 0
}

# 改动文件的各级祖先目录,由浅到深。
{
  echo .
  while IFS= read -r f; do
    d=$(dirname "$f")
    while [ "$d" != "." ] && [ "$d" != "/" ]; do echo "$d"; d=$(dirname "$d"); done
  done < "$changed"
} | sort -u | awk -F/ '{ print ($0 == "." ? 0 : NF) "\t" $0 }' | sort -n | cut -f2- > "$tmp/dirs"

nearest() { # source filename
  while IFS= read -r d; do
    if [ "$d" = "." ]; then emit "$1" "$2" "**"; else emit "$1" "$d/$2" "$d/**"; fi
  done < "$tmp/dirs"
}

# review 专用文件排在最前,去重时以它们为准。
nearest review REVIEW.md
emit ignore .cr/ignore.md "**"

nearest claude CLAUDE.md
emit claude .claude/CLAUDE.md "**"
if [ -d .claude/rules ]; then
  find .claude/rules -type f -name '*.md' | sort | while IFS= read -r f; do
    emit_scoped claude "${f#./}" paths always
  done
fi

nearest agents AGENTS.override.md
nearest agents AGENTS.md

if [ -d .cursor/rules ]; then
  find .cursor/rules -type f \( -name '*.mdc' -o -name '*.md' \) | sort | while IFS= read -r f; do
    f=${f#./}
    if [ "$(fm_values alwaysApply "$f")" = true ]; then emit cursor "$f" "**"
    else emit_scoped cursor "$f" globs never; fi
  done
fi
emit cursor .cursorrules "**"

emit copilot .github/copilot-instructions.md "**"
if [ -d .github/instructions ]; then
  find .github/instructions -type f -name '*.instructions.md' | sort | while IFS= read -r f; do
    emit_scoped copilot "${f#./}" applyTo always
  done
fi

nearest gemini GEMINI.md
emit windsurf .windsurfrules "**"
emit conventions CONVENTIONS.md "**"
