#!/bin/sh
# collect-diff.sh — 把待审查的改动收集到一个由纯文本文件组成的目录中。
#
# 用法: collect-diff.sh [target] [--depth quick|standard|deep] [--only LIST] [--full]
#                        [--repo DIR] [--base REF] [--platform github|gitlab] [--out DIR]
#
#   --repo DIR   仓库所在的目录(默认:当前目录)
#   --base REF   PR/MR 的目标分支,只在无法通过 gh/glab 读取时使用(默认:默认分支)
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
#   hashes.txt      hash<TAB>path          — 每个文件 diff 的哈希,用于判断下次是否需要重审
#
# 增量:同一仓库、同一目标之前有过一次覆盖范围不小于本次的审查时(记录在
# ${CROSSCHECK_HOME:-~/.crosscheck}/reviews/<项目名>/<目标>/ 下,由 save-review.sh 写入),diff.patch 等文件
# 只保留自那次以来 diff 发生变化的文件,并额外写出:
#   incremental.txt     status<TAB>path    — unchanged / changed / new / removed
#   diff.full.patch、files.full.txt        — 完整的改动
#   prev-findings.json                     — 上次留下的发现
# --full 忽略历史,全量审查。

set -eu

die() { printf 'collect-diff: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$2 需要 '$1',但在 PATH 中找不到"; }

target=""
platform="${CR_PLATFORM:-}"
out=""
depth=standard
only=all
full=no
local_path=""
ref_key=""
repo_arg=""
base_override=""
pr_source=""
gap_vars=""
gap_file=""
auth_via=""
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) [ $# -ge 2 ] || die "--platform 需要一个值"; platform=$2; shift 2 ;;
    --out) [ $# -ge 2 ] || die "--out 需要一个值"; out=$2; shift 2 ;;
    --depth) [ $# -ge 2 ] || die "--depth 需要一个值"; depth=$2; shift 2 ;;
    --only) [ $# -ge 2 ] || die "--only 需要一个值"; only=$2; shift 2 ;;
    --full) full=yes; shift ;;
    --repo) [ $# -ge 2 ] || die "--repo 需要一个值"; repo_arg=$2; shift 2 ;;
    --base) [ $# -ge 2 ] || die "--base 需要一个值"; base_override=$2; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "未知选项: $1" ;;
    *) [ -z "$target" ] || die "只支持一个 target"; target=$1; shift ;;
  esac
done

case "$depth" in quick|standard|deep) ;; *) die "--depth 应为 quick、standard 或 deep" ;; esac
only=$(printf '%s' "$only" | tr ',' '\n' | sed 's/^ *//; s/ *$//' | grep -v '^$' | sort -u | tr '\n' ',' | sed 's/,$//')
case ",$only," in
  ,all,|,bug,rules,security,simplify,) only=all ;;
esac

script_dir=$(cd "$(dirname "$0")" && pwd)
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$(pwd) 不是 git 仓库。审查需要本地仓库:请在仓库目录里运行,或用 --repo <目录> 指定。不知道仓库在哪里时问用户,不要在磁盘上到处找"
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
  local_path=$1
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
  ref_key="branch-${ref#origin/}"
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

# origin 的地址,去掉协议、账号和凭证。任何要显示或保存远程地址的地方都用它,不要用原始地址。
origin_id() {
  u=$(git remote get-url origin 2>/dev/null || true)
  [ -n "$u" ] || return 1
  printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##'
}

# gh/glab 不可用或没登录时,只用 git 把 PR/MR 拉下来。git 会用仓库自己配置好的凭证,
# 不需要任何人去读取或传递它。拿不到标题、描述和目标分支,对比基准按 --base 或默认分支计算。
collect_via_git() { # 远端 ref,给人看的名称
  oid=$(origin_id) || die "当前仓库没有 origin 远程,无法拉取 $2"
  if [ -n "$repo" ]; then
    case "$oid" in
      */"$repo") ;;
      *) die "$2 属于 $repo,但当前仓库的 origin 是 ${oid}。请在对应的本地仓库里运行,或用 --repo 指定它的目录" ;;
    esac
  fi
  if ! git fetch -q origin "$1" >/dev/null 2>&1; then
    [ -z "$gap_vars" ] || printf 'collect-diff: gh/glab 看起来没有登录,很可能只是环境没带上:%s 里设置了 %s,而这个终端里没有(非交互终端不读这个文件)。把它们挪到 ~/.zshenv 或宿主的环境变量配置里;不要为了排查去打印这些变量或查看这个文件\n' "$gap_file" "$gap_vars" >&2
    die "无法从 origin(${oid})拉取 $2。可能是编号不对、没有访问权限,或者当前环境连不上这个地址(网络或沙箱限制)"
  fi
  head_sha=$(git rev-parse FETCH_HEAD)
  if [ -n "$base_override" ]; then
    base_ref=$base_override
    git rev-parse -q --verify "$base_ref^{commit}" >/dev/null 2>&1 || base_ref="origin/$base_override"
    git fetch -q origin "${base_ref#origin/}" >/dev/null 2>&1 || true
    git rev-parse -q --verify "$base_ref^{commit}" >/dev/null 2>&1 || die "找不到 --base 指定的分支: $base_override"
  else
    base_ref=$(default_branch_ref) || die "找不到默认分支,请用 --base <分支> 指定 $2 的目标分支"
    git fetch -q origin "${base_ref#origin/}" >/dev/null 2>&1 || true
  fi
  base_sha=$(git merge-base "$base_ref" "$head_sha" 2>/dev/null) || die "$2 与 $base_ref 没有共同的祖先,请用 --base 指定正确的目标分支"
  $GITDIFF "$base_sha" "$head_sha" >> "$raw"
  printf '(未能读取 %s 的标题和描述:gh/glab 不可用或未登录,本次只用 git 拉取了代码。)\n' "$2" > "$out/description.md"
  pr_source="git;base=$base_ref"
}

# 登录配置写在 shell 的 rc 文件里、而当前终端没有加载它的情况:非交互终端(很多 agent 的终端
# 都是)不读 .zshrc / .bashrc,于是 gh/glab 明明登录过却读不到 token 或配置目录。
# 这里只判断变量名有没有出现、当前环境里有没有值,绝不读取、保存或输出任何值。
env_gap() { # 空格分隔的变量名
  gap_vars=""; gap_file=""
  for v in $1; do
    if eval "[ -n \"\${$v:-}\" ]"; then continue; fi
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zprofile" "$HOME/.profile"; do
      [ -f "$rc" ] || continue
      if grep -qE "(^|[[:space:];])$v=" "$rc" 2>/dev/null; then
        gap_vars="${gap_vars:+$gap_vars、}$v"
        gap_file="~/${rc##*/}"
        break
      fi
    done
  done
}

# 在用户自己的交互 shell 里运行一条命令,让它带上 rc 文件里的登录配置。
# 命令必须自己把结果写进文件;这个 shell 的标准输出和标准错误全部丢弃,
# 所以 rc 里的任何内容、任何环境变量都到不了终端。超过 20 秒就放弃。
via_user_shell() { # 命令串,参数...
  [ "${CR_USER_SHELL:-auto}" != 0 ] || return 1
  sh_bin=${SHELL:-}
  case "${sh_bin##*/}" in zsh|bash) ;; *) return 1 ;; esac
  cmd_str=$1; shift
  "$sh_bin" -ic "$cmd_str" cr-shell "$@" </dev/null >/dev/null 2>&1 &
  shell_pid=$!
  # 交互 shell 会忽略 TERM,所以超时后直接 KILL,连同它启动的子进程。
  ( sleep 20; pkill -9 -P "$shell_pid" 2>/dev/null; kill -9 "$shell_pid" 2>/dev/null ) >/dev/null 2>&1 &
  watch_pid=$!
  if wait "$shell_pid" 2>/dev/null; then shell_rc=0; else shell_rc=$?; fi
  pkill -P "$watch_pid" 2>/dev/null || true
  kill "$watch_pid" 2>/dev/null || true
  wait "$watch_pid" 2>/dev/null || true
  return "$shell_rc"
}

detect_platform() {
  [ -n "$platform" ] && return 0
  case "$target" in
    *"/pull/"*) platform=github; return 0 ;;
    *"/merge_requests/"*) platform=gitlab; return 0 ;;
  esac
  remote=$(origin_id || true)
  case "$remote" in
    *github.com*) platform=github ;;
    *gitlab*) platform=gitlab ;;
    *) die "无法判断 '$remote' 是 GitHub 还是 GitLab;请传入 --platform github|gitlab" ;;
  esac
}

collect_github() {
  mode=pr
  case "$target" in
    http*://*/pull/*)
      url_path=${target#*://*/}
      repo=${url_path%%/pull/*}
      number=${target##*/pull/}; number=${number%%[!0-9]*}
      ;;
    *) number=$target ;;
  esac
  if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
    env_gap "GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GH_CONFIG_DIR XDG_CONFIG_HOME"
    collect_via_git "pull/$number/head" "PR #$number"
    return 0
  fi
  [ -n "$repo" ] || repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner) || die "gh 无法解析仓库"
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
  mr_api="projects/$proj/merge_requests/$number"
  mr_ok() { [ -s "$out/mr.json" ] && [ -n "$(json_get "$out/mr.json" .diff_refs.head_sha 2>/dev/null)" ]; }
  if command -v glab >/dev/null 2>&1 && glab api "$mr_api" > "$out/mr.json" 2>/dev/null && mr_ok; then :
  else
    rm -f "$out/mr.json"
    env_gap "GITLAB_TOKEN GITLAB_ACCESS_TOKEN OAUTH_TOKEN GLAB_CONFIG_DIR XDG_CONFIG_HOME"
    # 只有确认 rc 文件里有相关配置、而当前环境没有时,才借用户的 shell 再试一次。
    if { [ -n "$gap_vars" ] || [ "${CR_USER_SHELL:-auto}" = always ]; } \
      && via_user_shell 'glab api "$1" > "$2" 2>/dev/null' "$mr_api" "$out/mr.json" && mr_ok; then
      auth_via=shell
    else
      rm -f "$out/mr.json"
      collect_via_git "merge-requests/$number/head" "MR !$number"
      return 0
    fi
  fi
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

# 每个文件 diff 的哈希。内容没变则哈希不变,下次审查据此跳过。
split="$out/split"
rm -rf "$split" "$out/hashes.txt" "$out/incremental.txt" "$out/diff.full.patch" "$out/files.full.txt" "$out/prev-findings.json"
mkdir -p "$split"
: > "$split/index"
awk -v dir="$split" '
function flush(   f) {
  if (!open) return
  n++; f = dir "/" n ".patch"
  print buf > f; close(f)
  print n "\t" path >> (dir "/index")
  open = 0
}
/^diff --git / { flush(); buf = $0; path = $0; open = 1; sub(/^diff --git a\/.* b\//, "", path); next }
open { buf = buf "\n" $0 }
END { flush() }
' "$out/diff.patch"
tab=$(printf '\t')
: > "$out/hashes.txt"
while IFS="$tab" read -r n path; do
  printf '%s\t%s\n' "$(git hash-object "$split/$n.patch")" "$path" >> "$out/hashes.txt"
done < "$split/index"

# 这个目标的审查记录放在哪里。
case "$mode" in
  pr) if [ "$platform" = gitlab ]; then target_key="mr-$number"; else target_key="pr-$number"; fi ;;
  range) target_key=${ref_key:-range-$target} ;;
  *)
    branch=$(git symbolic-ref -q --short HEAD 2>/dev/null || true)
    [ -n "$branch" ] || branch="detached-$(git rev-parse --short HEAD 2>/dev/null || echo none)"
    target_key="branch-$branch${local_path:+--path-$local_path}"
    ;;
esac
target_key=$(printf '%s' "$target_key" | sed 's/[^A-Za-z0-9._-]/_/g')
# 记录按项目名存放。项目的身份优先取 origin 的地址,没有远程时取主工作区的路径;
# 两者在同一仓库的各个 worktree 里都一样,所以它们共用一份记录。
if repo_id=$(origin_id); then :
else
  common=$(git rev-parse --git-common-dir)
  repo_id=$(dirname "$(cd "$common" && pwd)")
fi
reviews_dir="${CROSSCHECK_HOME:-$HOME/.crosscheck}/reviews"
repo_key=$(basename "$repo_id" | sed 's/[^A-Za-z0-9._-]/_/g')
# 早期版本用"目录名-路径哈希"做键;新位置还没有记录时,把旧记录挪过来。
old_key="$(basename "$root" | sed 's/[^A-Za-z0-9._-]/_/g')-$(printf '%s' "$root" | git hash-object --stdin | cut -c1-8)"
if [ -d "$reviews_dir/$old_key" ] && [ ! -e "$reviews_dir/$repo_key" ]; then
  mv "$reviews_dir/$old_key" "$reviews_dir/$repo_key" && printf '%s\n' "$repo_id" > "$reviews_dir/$repo_key/.repo"
fi
# 另一个同名的项目已经占用了这个目录时,加上哈希区分。
if [ -f "$reviews_dir/$repo_key/.repo" ] && [ "$(head -n 1 "$reviews_dir/$repo_key/.repo")" != "$repo_id" ]; then
  repo_key="$repo_key-$(printf '%s' "$repo_id" | git hash-object --stdin | cut -c1-8)"
fi
store_dir="$reviews_dir/$repo_key/$target_key"

# 增量:上次的审查覆盖了所有维度、深度不低于本次时,只留下 diff 变了的文件。
rank() { case "$1" in quick) echo 1 ;; deep) echo 3 ;; *) echo 2 ;; esac; }
review_scope=full
prev_review=""
unchanged_count=0
if [ "$full" = no ] && [ -f "$store_dir/baseline" ]; then
  prev_review=$(head -n 1 "$store_dir/baseline")
  prev="$store_dir/$prev_review"
  if [ -f "$prev/hashes.txt" ] && [ -f "$prev/meta.txt" ]; then
    prev_depth=$(sed -n 's/^depth=//p' "$prev/meta.txt" | head -n 1)
    if [ "$(rank "$prev_depth")" -ge "$(rank "$depth")" ]; then review_scope=incremental; fi
  fi
  [ "$review_scope" = incremental ] || prev_review=""
fi
if [ "$review_scope" = incremental ]; then
  awk -F '\t' '
    NR == FNR { old[$2] = $1; next }
    { seen[$2] = 1
      if (!($2 in old)) print "new\t" $2
      else if (old[$2] == $1) print "unchanged\t" $2
      else print "changed\t" $2 }
    END { for (p in old) if (!(p in seen)) print "removed\t" p }
  ' "$prev/hashes.txt" "$out/hashes.txt" > "$out/incremental.txt"
  unchanged_count=$(grep -c "^unchanged$tab" "$out/incremental.txt" || true)
  mv "$out/diff.patch" "$out/diff.full.patch"
  mv "$out/files.txt" "$out/files.full.txt"
  : > "$out/diff.patch"
  : > "$out/files.txt"
  while IFS="$tab" read -r n path; do
    if grep -qxF "unchanged$tab$path" "$out/incremental.txt"; then continue; fi
    cat "$split/$n.patch" >> "$out/diff.patch"
    printf '%s\n' "$path" >> "$out/files.txt"
  done < "$split/index"
  [ -f "$prev/findings.json" ] && cp "$prev/findings.json" "$out/prev-findings.json"
fi
rm -rf "$split"

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
  printf 'depth=%s\n' "$depth"
  printf 'only=%s\n' "$only"
  printf 'review_scope=%s\n' "$review_scope"
  printf 'prev_review=%s\n' "$prev_review"
  if [ -n "$prev_review" ] && [ -f "$store_dir/$prev_review/report.md" ]; then printf 'prev_report=%s\n' "$store_dir/$prev_review/report.md"; fi
  printf 'unchanged_files=%s\n' "$unchanged_count"
  printf 'repo_id=%s\n' "$repo_id"
  printf 'pr_source=%s\n' "${pr_source:-api}"
  printf 'auth_via=%s\n' "${auth_via:-direct}"
  printf 'env_gap=%s\n' "$gap_vars"
  printf 'target_key=%s\n' "$target_key"
  printf 'store_dir=%s\n' "$store_dir"
  printf 'started_at=%s\n' "$(date '+%Y-%m-%d %H:%M')"
} > "$out/meta.txt"

sh "$script_dir/check-state.sh" --save "$out"

printf 'files=%s changed_lines=%s skipped=%s batches=%s head_checked_out=%s scope=%s unchanged=%s\n' \
  "$file_count" "$changed_lines" "$skipped_count" "$batch_count" "$head_checked_out" "$review_scope" "$unchanged_count"
case "$pr_source" in
  git*) printf 'NOTE: 没有通过 gh/glab 读到这个 PR/MR 的信息,已改用 git 直接拉取代码。标题和描述不可用;对比基准是 %s,目标分支不是它时请加 --base <分支> 重新运行\n' "${pr_source#git;base=}" ;;
esac
if [ -n "$gap_vars" ]; then
  if [ "$auth_via" = shell ]; then
    printf 'NOTE: 当前终端没有加载你的 shell 配置:%s 里设置了 %s,而这个终端里没有(非交互终端不读这个文件)。已改为通过你的登录 shell 调用 glab,读取成功。\n' "$gap_file" "$gap_vars"
  else
    printf 'NOTE: gh/glab 看起来没有登录,很可能只是环境没带上:%s 里设置了 %s,而这个终端里没有(非交互终端不读这个文件)。\n' "$gap_file" "$gap_vars"
  fi
  printf 'NOTE: 想一劳永逸,把这几个变量的设置从 %s 挪到 ~/.zshenv(zsh 的所有终端都会读)或宿主的环境变量配置里。不要为了排查去打印这些变量或查看这个文件。\n' "$gap_file"
fi
if [ "$file_count" -eq 0 ]; then
  if [ "$unchanged_count" -gt 0 ]; then printf 'UNCHANGED: 自上次审查(%s)以来没有变化\n' "$prev_review"
  else printf 'EMPTY: 没有可审查的内容\n'; fi
fi
printf '%s\n' "$out"
