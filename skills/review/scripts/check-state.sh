#!/bin/sh
# check-state.sh — 记录并核对仓库状态,确认审查过程没有改动仓库。
#
# 用法: check-state.sh --save DIR   记录当前状态到 DIR/state.txt(由 collect-diff.sh 调用)
#       check-state.sh DIR          与记录的状态对比,并结束本次审查的只读阶段
#
# 对比的内容:当前分支、HEAD、stash、暂存区,以及每个已修改、已删除、未跟踪文件的内容哈希。
# 状态一致时输出 OK 并以 0 退出;不一致时输出 CHANGED 和差异(< 审查开始时, > 现在)并以 1 退出。
# 本脚本只读取仓库,不做任何恢复。

set -eu

die() { printf 'check-state: %s\n' "$*" >&2; exit 2; }

save=no
if [ "${1:-}" = "--save" ]; then save=yes; shift; fi
case "${1:-}" in
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") die "需要指定 collect-diff.sh 的输出目录" ;;
esac
dir=$1
[ -d "$dir" ] || die "目录不存在: $dir"
dir=$(cd "$dir" && pwd)

root=""
[ -f "$dir/meta.txt" ] && root=$(sed -n 's/^repo_root=//p' "$dir/meta.txt" | head -n 1)
[ -n "$root" ] || root=$(git rev-parse --show-toplevel 2>/dev/null) || die "当前不在 git 仓库中"
cd "$root"

snapshot() {
  printf 'branch=%s\n' "$(git symbolic-ref -q --short HEAD 2>/dev/null || echo DETACHED)"
  printf 'head=%s\n' "$(git rev-parse -q --verify HEAD 2>/dev/null || echo none)"
  printf 'stash=%s\n' "$(git rev-parse -q --verify refs/stash 2>/dev/null || echo none)"
  printf 'index=%s\n' "$(git diff --cached --no-ext-diff | git hash-object --stdin)"
  git ls-files -m -d -o --exclude-standard | sort -u | while IFS= read -r f; do
    if [ -f "$f" ]; then h=$(git hash-object -- "$f" 2>/dev/null || echo unreadable); else h=missing; fi
    printf 'file\t%s\t%s\n' "$f" "$h"
  done
}

if [ "$save" = yes ]; then
  snapshot > "$dir/state.txt"
  exit 0
fi

[ -f "$dir/state.txt" ] || die "$dir 中没有 state.txt,无法对比"
snapshot > "$dir/state-now.txt"
if cmp -s "$dir/state.txt" "$dir/state-now.txt"; then
  echo "OK: 仓库状态与审查开始时一致"
  exit 0
fi
echo "CHANGED: 仓库状态在审查过程中发生了变化(< 审查开始时, > 现在):"
diff "$dir/state.txt" "$dir/state-now.txt" | grep '^[<>]' || true
exit 1
