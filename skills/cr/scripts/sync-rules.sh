#!/bin/sh
# sync-rules.sh — 把组织规则仓库同步到本机。
#
# 用法: sync-rules.sh           克隆或更新,并切到配置的版本
#       sync-rules.sh --check   只看状态:是否配置、本地版本、多久没同步
#
# 读 ~/.crosscheck/config 里的:
#   rules_repo=<地址>    组织规则仓库(GitLab 上的独立仓库),地址里不要带账号和 token
#   rules_ref=<tag 或分支>  跟踪的版本,建议用 tag(默认 main)
#   rules_dir=<目录>     本地克隆的位置(默认 ~/.crosscheck/org-rules)
# 用的是本机已经配置好的 git 凭证;脚本不读取、不打印凭证,显示地址时去掉账号部分。
# 本地副本是独立的克隆,只读使用,不会动你的任何项目仓库。

set -eu

die() { printf 'sync-rules: %s\n' "$*" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(sh "$here/config.sh" get rules_repo)
ref=$(sh "$here/config.sh" get rules_ref); ref=${ref:-main}
dir=${CROSSCHECK_RULES_DIR:-$(sh "$here/config.sh" get rules_dir)}
dir=${dir:-${CROSSCHECK_HOME:-$HOME/.crosscheck}/org-rules}
safe() { printf '%s\n' "$1" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##'; }

case "${1:-}" in
  -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --check)
    if [ -z "$repo" ] && [ ! -d "$dir" ]; then echo "未配置组织规则仓库(在 $(sh "$here/config.sh" path) 里加 rules_repo=<地址>)"; exit 0; fi
    [ -z "$repo" ] || echo "仓库:$(safe "$repo")"
    echo "跟踪:$ref"
    if [ -d "$dir/.git" ]; then
      echo "本地:$dir @ $(git -C "$dir" describe --tags --always 2>/dev/null || echo unknown)"
      if [ -f "$dir/.last-sync" ]; then
        age=$(( $(date +%s) - $(cat "$dir/.last-sync" 2>/dev/null || echo 0) ))
        echo "上次同步:$((age / 3600)) 小时前"
      fi
    else
      echo "本地:还没有克隆(运行 sync-rules.sh)"
    fi
    exit 0 ;;
  "") ;;
  *) die "未知参数: $1" ;;
esac

[ -n "$repo" ] || die "没有配置 rules_repo。在 $(sh "$here/config.sh" path) 里加一行 rules_repo=<组织规则仓库的地址>"
if [ ! -d "$dir/.git" ]; then
  mkdir -p "$(dirname "$dir")"
  git clone -q "$repo" "$dir" >/dev/null 2>&1 || die "无法克隆 $(safe "$repo")。可能是地址不对、没有访问权限,或者当前环境连不上(网络或沙箱限制)"
else
  git -C "$dir" fetch -q --tags origin >/dev/null 2>&1 || die "无法从 $(safe "$repo") 拉取更新(网络、权限或沙箱限制)"
fi
if git -C "$dir" rev-parse -q --verify "refs/tags/$ref^{commit}" >/dev/null 2>&1; then target="refs/tags/$ref"
elif git -C "$dir" rev-parse -q --verify "origin/$ref^{commit}" >/dev/null 2>&1; then target="origin/$ref"
else die "规则仓库里没有 $ref 这个 tag 或分支"; fi
git -C "$dir" -c advice.detachedHead=false checkout -q --detach "$target" || die "切换到 $ref 失败"
date +%s > "$dir/.last-sync"
printf 'ok: 组织规则已同步到 %s(%s)\n' "$(git -C "$dir" describe --tags --always)" "$dir"
