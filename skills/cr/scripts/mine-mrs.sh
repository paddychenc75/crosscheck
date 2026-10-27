#!/bin/sh
# mine-mrs.sh — 只读地拉取最近合并的 MR 和上面的人工评论,整理成供 cr-rules 归纳规则的素材。
#
# 用法: mine-mrs.sh [--days N] [--limit N] [--repo DIR] [--platform gitlab|github]
#
#   --days N     最近 N 天合并的 MR(默认 90,约一个季度)
#   --limit N    最多取多少个 MR(默认 200,取合并时间最近的)
#   --repo DIR   仓库所在的目录(默认:当前目录)
#   --platform   默认按 origin 的地址判断:含 github.com 用 gh,其余用 glab
#
# 只读:只调用 glab api / gh api 的 GET 请求和 git show,不改仓库,也不往平台写任何东西。
# 缓存在 ${CROSSCHECK_HOME:-~/.crosscheck}/mining/<项目名>/mrs/<编号>.json,MR 没有更新过就不再拉评论。
# 缓存只存 MR 标题、作者和人工评论(不存 MR 描述原文)。
#
# 每次运行新建工作目录 mining/<项目名>/run-<时间>/,写入:
#   batch-<n>.json   按目录分好的评论:mr、note、author、file、line、code(评论处上下几行代码)、body,
#                    resolved(讨论是否已解决,不知道为 null)、changed_after(评论处的代码在 MR 最终版本里是否改了)
#   feedback.json    crosscheck 自己记下的判断(成立 / 不成立 / 不修)及原因,也是候选的素材
#   existing-rules.txt  仓库里已有的规则,归纳时不重复
#   meta.txt         repo_root、days、mrs、notes、reviewers、batches 等
# 输出统计;最后一行是工作目录。
# 需要 python3,以及已登录的 glab(GitHub 用 gh)。

set -eu

die() { printf 'mine-mrs: %s\n' "$*" >&2; exit 1; }
days=90; limit=200; repo_arg=""; platform=""
while [ $# -gt 0 ]; do
  case "$1" in
    --days) [ $# -ge 2 ] || die "--days 需要一个值"; days=$2; shift 2 ;;
    --limit) [ $# -ge 2 ] || die "--limit 需要一个值"; limit=$2; shift 2 ;;
    --repo) [ $# -ge 2 ] || die "--repo 需要一个值"; repo_arg=$2; shift 2 ;;
    --platform) [ $# -ge 2 ] || die "--platform 需要一个值"; platform=$2; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不认识的参数: $1" ;;
  esac
done
case "$days" in ''|*[!0-9]*) die "--days 应为正整数" ;; esac
case "$limit" in ''|*[!0-9]*) die "--limit 应为正整数" ;; esac
command -v python3 >/dev/null 2>&1 || die "需要 python3(3.8 或更新)"
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库,请用 --repo 指定"
root=$(git rev-parse --show-toplevel)

script_dir=$(cd "$(dirname "$0")" && pwd)
repo_id=$(python3 "$script_dir/crlib.py" repo-id)   # 项目的身份;原始的远程地址不会被输出
if [ -z "$platform" ]; then
  case "$repo_id" in *github.com*) platform=github ;; *) platform=gitlab ;; esac
fi
case "$platform" in
  gitlab) command -v glab >/dev/null 2>&1 || die "需要 glab(并已登录,可运行 glab auth login)" ;;
  github) command -v gh >/dev/null 2>&1 || die "需要 gh(并已登录,可运行 gh auth login)" ;;
  *) die "--platform 应为 gitlab 或 github" ;;
esac

exec python3 "$script_dir/mine_mrs.py" "$root" "$repo_id" "$platform" "$days" "$limit"
