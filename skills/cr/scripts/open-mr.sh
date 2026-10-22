#!/bin/sh
# open-mr.sh — 在临时 worktree 里改几个文件,推送到新分支并创建 MR。不碰你当前的工作区和分支。
#
# 用法: open-mr.sh --repo DIR --branch NAME --title 标题 --put 相对路径=来源文件 [--put ...]
#                  [--base 目标分支] [--body 说明文件] [--label 标签] [--check 命令] [--yes]
#
#   不带 --yes 时只打印将要做的改动(diff)和操作,什么都不推送。
#   带 --yes 时:从 origin/<目标分支> 建一个临时 worktree → 写入文件 → 提交到新分支 → 推送 →
#   有 glab 时创建 MR(GitHub 仓库有 gh 时创建 PR),只创建、不合并;没有 glab/gh 时只推送分支,
#   提示你去 GitLab 页面上创建。最后删除临时 worktree 和本地的临时分支。
#   --check 命令  写入文件之后、提交之前在临时 worktree 上运行(worktree 路径作为 $1),失败就不推送。
#   目标分支默认是 origin 的默认分支。推送用的是本机已经配置好的 git 凭证。

set -eu

die() { printf 'open-mr: %s\n' "$*" >&2; exit 1; }
repo=""; branch=""; title=""; base=""; body=""; label=""; check=""; yes=no; puts=""; expected_base=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo=${2:-}; shift 2 ;;
    --branch) branch=${2:-}; shift 2 ;;
    --title) title=${2:-}; shift 2 ;;
    --expected-base) expected_base=${2:-}; shift 2 ;;
    --base) base=${2:-}; shift 2 ;;
    --body) body=${2:-}; shift 2 ;;
    --label) label=${2:-}; shift 2 ;;
    --check) check=${2:-}; shift 2 ;;
    --put) puts="${puts}${2:-}
"; shift 2 ;;
    --yes) yes=yes; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done
[ -n "$repo" ] && [ -n "$branch" ] && [ -n "$title" ] && [ -n "$puts" ] || die "需要 --repo、--branch、--title 和至少一个 --put"
repo=$(cd "$repo" && git rev-parse --show-toplevel) || die "$repo 不是 git 仓库"
if [ -z "$base" ]; then
  base=$(git -C "$repo" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)
  [ -n "$base" ] || base=main
fi
git -C "$repo" fetch -q origin "$base" >/dev/null 2>&1 || die "无法从 origin 拉取 $base(网络、权限或沙箱限制)"

if [ -n "$expected_base" ] && [ "$(git -C "$repo" rev-parse "origin/$base")" != "$expected_base" ]; then
  die "目标分支已改变，请重新预览并确认；没有提交或推送"
fi
base_sha=$(git -C "$repo" rev-parse "origin/$base")

tmp=$(mktemp -d "${TMPDIR:-/tmp}/cr-mr.XXXXXX")
wt="$tmp/wt"
cleanup() {
  git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
git -C "$repo" worktree add -q --detach "$wt" "$base_sha" >/dev/null 2>&1 || die "无法创建临时 worktree"

printf '%s' "$puts" > "$tmp/puts"
while IFS= read -r spec; do
  [ -n "$spec" ] || continue
  rel=${spec%%=*}; from=${spec#*=}
  [ -f "$from" ] || die "找不到 $from"
  python3 - "$wt" "$rel" <<'CHECK' || die "目标路径无效"
import os, sys
root, path = sys.argv[1:]
if os.path.isabs(path) or any(x in ('', '.', '..') for x in path.split('/')):
    sys.exit(1)
cur = root
for part in path.split('/'):
    cur = os.path.join(cur, part)
    if os.path.islink(cur): sys.exit(1)
CHECK
  mkdir -p "$wt/$(dirname "$rel")"
  cp "$from" "$wt/$rel"
done < "$tmp/puts"
if git -C "$wt" diff --quiet && [ -z "$(git -C "$wt" ls-files --others --exclude-standard)" ]; then
  echo "没有改动,不需要创建 MR"; exit 0
fi
if [ -n "$check" ]; then
  sh -c "$check" sh "$wt" || die "检查没通过,没有推送任何东西"
fi
git -C "$wt" add -A
if [ "$yes" != yes ]; then
  echo "将要在 $base 上提交以下改动,推送到新分支 $branch 并创建 MR「${title}」:"
  git -C "$wt" diff --cached --stat
  git -C "$wt" diff --cached
  echo "确认后加 --yes 再运行一次。"
  exit 0
fi
git -C "$wt" switch -q -c "$branch" || die "无法创建分支 $branch(可能已经存在)"
git -C "$wt" commit -q -m "$title" || die "提交失败"
git -C "$wt" push -q -u origin "$branch" >/dev/null 2>&1 || die "推送分支 $branch 失败(权限或网络)"
url=""
remote=$(git -C "$repo" remote get-url origin 2>/dev/null | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' || true)
case "$remote" in
  *github.com*)
    if command -v gh >/dev/null 2>&1; then
      url=$(cd "$wt" && gh pr create --head "$branch" --base "$base" --title "$title" --body "$(cat "${body:-/dev/null}")" 2>/dev/null | tail -n 1 || true)
    fi ;;
  *)
    if command -v glab >/dev/null 2>&1; then
      set -- mr create --source-branch "$branch" --target-branch "$base" --title "$title" --description "$(cat "${body:-/dev/null}")" --yes
      [ -z "$label" ] || set -- "$@" --label "$label"
      url=$(cd "$wt" && glab "$@" 2>/dev/null | grep -Eo 'https?://[^ ]+' | tail -n 1 || true)
    fi ;;
esac
git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || true
git -C "$repo" branch -D "$branch" >/dev/null 2>&1 || true
if [ -n "$url" ]; then
  echo "ok: 已创建 MR $url(只创建,没有合并)"
else
  echo "ok: 已推送分支 ${branch}。没有 glab/gh 或创建失败,请在 GitLab 页面上从这个分支创建 MR${label:+,并加上标签 $label}"
fi
