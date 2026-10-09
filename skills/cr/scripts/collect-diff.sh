#!/bin/sh
# collect-diff.sh — 把待审查的改动收集到一个由纯文本文件组成的目录中。
#
# 用法: collect-diff.sh [target] [--platform github|gitlab] [--out DIR]
#
#   省略 target        当前分支相对其与默认分支的 merge-base 的改动,
#                      加上未提交和未跟踪的改动
#   target = A..B      该 commit 范围
#   target = 路径      同"省略",但仅限该路径
#   target = 分支/ref  该分支相对其与默认分支的 merge-base 的改动(不需要检出它)
#   target = N 或 URL  第 N 号 pull request / merge request
#
# 写入 DIR(默认:新建的临时目录),并在最后一行打印其路径:
#   diff.patch      过滤后的 unified diff
#   files.txt       改动的文件,每行一个
#   ranges.txt      path<TAB>start<TAB>end  — 每个 hunk 覆盖的新侧行范围
#   skipped.txt     path<TAB>reason        — 被排除的文件(lock、生成、vendored、二进制)
#   batches.txt     batch<TAB>path         — 文件所属的批次;batch-<n>.patch 是该批次的 diff
#                   (每批约 CR_BATCH_LINES 行改动,默认 1000)
#   meta.txt        关于审查目标的 key=value 信息
#   state.txt       审查开始时的仓库状态,供 check-state.sh 在结束时核对
#   description.md  PR/MR 的标题和描述(本地审查时为空)

set -eu

die() { printf 'collect-diff: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$2 需要 '$1',但在 PATH 中找不到"; }

target=""
platform="${CR_PLATFORM:-}"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) [ $# -ge 2 ] || die "--platform 需要一个值"; platform=$2; shift 2 ;;
    --out) [ $# -ge 2 ] || die "--out 需要一个值"; out=$2; shift 2 ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "未知选项: $1" ;;
    *) [ -z "$target" ] || die "只支持一个 target"; target=$1; shift ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前不在 git 仓库中"
script_dir=$(cd "$(dirname "$0")" && pwd)
prefix=$(git rev-parse --show-prefix)
root=$(git rev-parse --show-toplevel)

if [ -z "$out" ]; then
  out=$(mktemp -d "${TMPDIR:-/tmp}/cr.XXXXXX")
else
  mkdir -p "$out"
fi
out=$(cd "$out" && pwd)
raw="$out/raw.patch"
: > "$raw"
: > "$out/description.md"

cd "$root"

mode=local
number=""
repo=""
title=""
url=""
base_sha=""
start_sha=""
head_sha=""
code_ref=""

GITDIFF="git diff --no-color --no-ext-diff --src-prefix=a/ --dst-prefix=b/"

default_branch_ref() {
  ref=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)
  if [ -n "$ref" ]; then printf '%s\n' "$ref"; return 0; fi
  for r in origin/main origin/master main master; do
    if git rev-parse -q --verify "$r^{commit}" >/dev/null 2>&1; then printf '%s\n' "$r"; return 0; fi
  done
  return 1
}

# 取 JSON 文件中点分路径(如 .diff_refs.base_sha)处的值。
json_get() {
  if command -v jq >/dev/null 2>&1; then
    jq -r "$2 // empty" "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].strip(".").split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" "$2"
  else
    die "读取 GitLab API 响应需要 'jq' 或 'python3'"
  fi
}

collect_local() {
  pathspec=$1
  head_sha=$(git rev-parse -q --verify HEAD 2>/dev/null || true)
  if [ -n "$head_sha" ]; then
    if base_ref=$(default_branch_ref); then
      base_sha=$(git merge-base "$base_ref" HEAD 2>/dev/null || printf '%s' "$head_sha")
    else
      base_sha=$head_sha
    fi
    if [ -n "$pathspec" ]; then $GITDIFF "$base_sha" -- "$pathspec" >> "$raw"
    else $GITDIFF "$base_sha" >> "$raw"; fi
  else
    # 仓库还没有任何 commit:只存在已暂存的内容。
    if [ -n "$pathspec" ]; then $GITDIFF --cached -- "$pathspec" >> "$raw"
    else $GITDIFF --cached >> "$raw"; fi
  fi
  # 未跟踪的文件同样属于进行中的工作。
  if [ -n "$pathspec" ]; then git ls-files --others --exclude-standard -- "$pathspec"
  else git ls-files --others --exclude-standard; fi > "$out/untracked.tmp"
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    $GITDIFF --no-index -- /dev/null "$f" >> "$raw" 2>/dev/null || true
  done < "$out/untracked.tmp"
  rm -f "$out/untracked.tmp"
  code_ref=WORKTREE
}

# 不在工作区里的分支或其他 ref:只看它已提交的内容,不检出。
collect_ref() {
  ref=$1
  if [ "$(git rev-parse "$ref^{commit}")" = "$(git rev-parse -q --verify HEAD 2>/dev/null || true)" ]; then
    collect_local ""   # 就是当前检出的 commit,按当前分支处理,带上未提交的改动
    return 0
  fi
  base_ref=$(default_branch_ref) || die "找不到默认分支,无法确定 '$ref' 的对比基准;请用 A..B 指定范围"
  collect_range "$base_ref...$ref"
}

collect_range() {
  mode=range
  $GITDIFF "$1" >> "$raw" || die "git 无法对 '$1' 做 diff"
  case "$1" in
    *...*) a=${1%%...*}; b=${1##*...}; base_sha=$(git merge-base "${a:-HEAD}" "${b:-HEAD}") ;;
    *) a=${1%%..*}; b=${1##*..}; base_sha=$(git rev-parse "${a:-HEAD}^{commit}") ;;
  esac
  head_sha=$(git rev-parse "${b:-HEAD}^{commit}")
  code_ref=$head_sha
}

detect_platform() {
  [ -n "$platform" ] && return 0
  case "$target" in
    *"/pull/"*) platform=github; return 0 ;;
    *"/merge_requests/"*) platform=gitlab; return 0 ;;
  esac
  remote=$(git remote get-url origin 2>/dev/null || true)
  case "$remote" in
    *github.com*) platform=github ;;
    *gitlab*) platform=gitlab ;;
    *) die "无法判断 '$remote' 是 GitHub 还是 GitLab;请传入 --platform github|gitlab" ;;
  esac
}

collect_github() {
  need gh "GitHub pull request"
  mode=pr
  case "$target" in
    http*://*/pull/*)
      url_path=${target#*://*/}
      repo=${url_path%%/pull/*}
      number=${target##*/pull/}; number=${number%%[!0-9]*}
      ;;
    *)
      number=$target
      repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner) || die "gh 无法解析仓库"
      ;;
  esac
  info=$(gh pr view "$number" -R "$repo" --json title,url,baseRefOid,headRefOid \
    --jq '[.title, .url, .baseRefOid, .headRefOid] | @tsv') || die "gh 无法读取 ${repo} 中的 PR #${number}"
  tab=$(printf '\t')
  title=$(printf '%s' "$info" | cut -d "$tab" -f1)
  url=$(printf '%s' "$info" | cut -d "$tab" -f2)
  base_sha=$(printf '%s' "$info" | cut -d "$tab" -f3)
  head_sha=$(printf '%s' "$info" | cut -d "$tab" -f4)
  { printf '# %s\n\n' "$title"; gh pr view "$number" -R "$repo" --json body --jq .body; } > "$out/description.md"
  gh pr diff "$number" -R "$repo" >> "$raw" || die "gh 无法获取 PR #${number} 的 diff"
  git fetch -q origin "pull/$number/head" 2>/dev/null || true
}

collect_gitlab() {
  need glab "GitLab merge request"
  mode=pr
  proj=":id"
  case "$target" in
    http*://*/merge_requests/*)
      host=${target#*://}; host=${host%%/*}
      url_path=${target#*://*/}
      repo=${url_path%%/-/merge_requests/*}; repo=${repo%%/merge_requests/*}
      number=${target##*/merge_requests/}; number=${number%%[!0-9]*}
      proj=$(printf '%s' "$repo" | sed 's#/#%2F#g')
      GITLAB_HOST=$host; export GITLAB_HOST
      ;;
    *) number=$target ;;
  esac
  glab api "projects/$proj/merge_requests/$number" > "$out/mr.json" || die "glab 无法读取 MR !${number}"
  title=$(json_get "$out/mr.json" .title)
  url=$(json_get "$out/mr.json" .web_url)
  base_sha=$(json_get "$out/mr.json" .diff_refs.base_sha)
  start_sha=$(json_get "$out/mr.json" .diff_refs.start_sha)
  head_sha=$(json_get "$out/mr.json" .diff_refs.head_sha)
  target_branch=$(json_get "$out/mr.json" .target_branch)
  [ -n "$repo" ] || repo=$(json_get "$out/mr.json" .references.full | sed 's/![0-9]*$//')
  { printf '# %s\n\n' "$title"; json_get "$out/mr.json" .description; } > "$out/description.md"
  rm -f "$out/mr.json"
  git fetch -q origin "merge-requests/$number/head" 2>/dev/null || true
  git cat-file -e "$base_sha^{commit}" 2>/dev/null || git fetch -q origin "$target_branch" 2>/dev/null || true
  if git cat-file -e "$base_sha^{commit}" 2>/dev/null && git cat-file -e "$head_sha^{commit}" 2>/dev/null; then
    $GITDIFF "$base_sha" "$head_sha" >> "$raw"
  elif [ "$proj" = ":id" ]; then
    glab mr diff "$number" --raw >> "$raw" || die "glab 无法获取 MR !${number} 的 diff"
  else
    glab mr diff "$number" --raw -R "$repo" >> "$raw" || die "glab 无法获取 MR !${number} 的 diff"
  fi
}

case "$target" in
  "") collect_local "" ;;
  http://*|https://*) detect_platform; "collect_$platform" ;;
  *[!0-9]*)
    if [ -e "$prefix$target" ]; then collect_local "$prefix$target"
    else
      case "$target" in
        *..*) collect_range "$target" ;;
        *)
          if git rev-parse -q --verify "$target^{commit}" >/dev/null 2>&1; then collect_ref "$target"
          elif git rev-parse -q --verify "origin/$target^{commit}" >/dev/null 2>&1; then collect_ref "origin/$target"
          else die "'$target' 不是路径、分支、commit 范围(A..B)或 PR/MR 编号"; fi
          ;;
      esac
    fi
    ;;
  *) detect_platform; "collect_$platform" ;;
esac

case "$platform" in github|gitlab|"") ;; *) die "未知平台 '$platform'(应为 github 或 gitlab)" ;; esac

# 按文件拆分原始 patch;保留可审查的文件,其余的另行记录。
rm -f "$out/diff.patch" "$out/files.txt" "$out/skipped.txt" # --out 指向已有目录时不要追加到旧结果上
awk -v patch="$out/diff.patch" -v files="$out/files.txt" -v skipped="$out/skipped.txt" '
function reason(p) {
  if (binary) return "binary"
  if (p ~ /(^|\/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Cargo\.lock|Gemfile\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock|go\.sum)$/) return "lockfile"
  if (p ~ /\.lock$/) return "lockfile"
  if (p ~ /\.(min\.js|min\.css|map|snap)$/ || p ~ /\.pb\.go$/ || p ~ /_pb2(_grpc)?\.py$/ || p ~ /\.generated\./ || p ~ /\.g\.dart$/) return "generated"
  if (p ~ /(^|\/)(node_modules|vendor|third_party|dist)\//) return "vendored-or-built"
  return ""
}
function flush(   r) {
  if (!open) return
  r = reason(path)
  if (r != "") print path "\t" r >> skipped
  else { print buf >> patch; print path >> files }
  open = 0
}
/^diff --git / {
  flush()
  buf = $0; path = $0; binary = 0; open = 1
  sub(/^diff --git a\/.* b\//, "", path)
  next
}
open {
  buf = buf "\n" $0
  if ($0 ~ /^Binary files / || $0 ~ /^GIT binary patch/) binary = 1
}
END { flush() }
' "$raw"
rm -f "$raw"
for f in diff.patch files.txt skipped.txt; do [ -f "$out/$f" ] || : > "$out/$f"; done

# 每个 hunk 的新侧行范围,用于锚定发现。
awk '
/^\+\+\+ b\// { path = substr($0, 7); next }
/^\+\+\+ / { path = ""; next }
/^@@ / && path != "" {
  n = split($3, a, ",")
  start = substr(a[1], 2) + 0
  len = (n > 1) ? a[2] + 0 : 1
  if (len > 0) print path "\t" start "\t" (start + len - 1)
}
' "$out/diff.patch" > "$out/ranges.txt"

# 把改动按路径顺序切成若干批,每批不超过 batch_lines 行改动(单个超限的文件独占一批)。
# 同目录的文件在 diff 中相邻,因此会落在同一批或相邻批次。
batch_lines=${CR_BATCH_LINES:-1000}
case "$batch_lines" in ""|*[!0-9]*|0) die "CR_BATCH_LINES 必须是正整数" ;; esac
rm -f "$out"/batch-*.patch "$out/batches.txt"
: > "$out/batches.txt"
batch_count=$(awk -v dir="$out" -v budget="$batch_lines" '
function flush(   f) {
  if (!open) return
  if (n == 0 || (used > 0 && used + lines > budget)) { n++; used = 0 }
  f = dir "/batch-" n ".patch"
  print buf >> f; close(f)
  print n "\t" path >> (dir "/batches.txt")
  used += lines; open = 0
}
/^diff --git / {
  flush()
  buf = $0; path = $0; lines = 0; inhunk = 0; open = 1
  sub(/^diff --git a\/.* b\//, "", path)
  next
}
open {
  buf = buf "\n" $0
  if ($0 ~ /^@@ /) inhunk = 1
  else if (inhunk && $0 ~ /^[+-]/) lines++
}
END { flush(); print n + 0 }
' "$out/diff.patch")

if [ "$mode" = pr ]; then
  if git cat-file -e "$head_sha^{commit}" 2>/dev/null; then code_ref=$head_sha; else code_ref=UNAVAILABLE; fi
fi
current=$(git rev-parse -q --verify HEAD 2>/dev/null || true)
if [ "$code_ref" = WORKTREE ] || { [ -n "$head_sha" ] && [ "$current" = "$head_sha" ]; }; then
  head_checked_out=yes
else
  head_checked_out=no
fi

file_count=$(wc -l < "$out/files.txt" | tr -d ' ')
skipped_count=$(wc -l < "$out/skipped.txt" | tr -d ' ')
changed_lines=$(grep -c '^[+-][^+-]' "$out/diff.patch" || true)

{
  printf 'mode=%s\n' "$mode"
  printf 'platform=%s\n' "${platform:-none}"
  printf 'target=%s\n' "$target"
  printf 'number=%s\n' "$number"
  printf 'repo=%s\n' "$repo"
  printf 'url=%s\n' "$url"
  printf 'title=%s\n' "$title"
  printf 'base_sha=%s\n' "$base_sha"
  printf 'start_sha=%s\n' "$start_sha"
  printf 'head_sha=%s\n' "$head_sha"
  printf 'code_ref=%s\n' "$code_ref"
  printf 'head_checked_out=%s\n' "$head_checked_out"
  printf 'repo_root=%s\n' "$root"
  printf 'files=%s\n' "$file_count"
  printf 'changed_lines=%s\n' "$changed_lines"
  printf 'skipped=%s\n' "$skipped_count"
  printf 'batches=%s\n' "$batch_count"
} > "$out/meta.txt"

sh "$script_dir/check-state.sh" --save "$out"

printf 'files=%s changed_lines=%s skipped=%s batches=%s head_checked_out=%s\n' \
  "$file_count" "$changed_lines" "$skipped_count" "$batch_count" "$head_checked_out"
[ "$file_count" -gt 0 ] || printf 'EMPTY: 没有可审查的内容\n'
printf '%s\n' "$out"
