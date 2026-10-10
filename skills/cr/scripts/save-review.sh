#!/bin/sh
# save-review.sh — 把一次审查的报告和发现存入审查记录,供以后查看和做增量审查。
#
# 用法: save-review.sh DIR [--finders N] [--verifiers N] [--candidates N] [--passed N]
#
#   DIR  collect-diff.sh 的输出目录,其中需要已经写好:
#          report.md      给用户看的报告
#          findings.json  本次审查结束时仍然成立的全部发现(含从上次带过来的)
#
# 写入 ${CROSSCHECK_HOME:-~/.crosscheck}/reviews/<项目名>/<目标>/<时间>/,并更新同级的两个指针文件:
#   latest    最近一次审查
#   baseline  最近一次覆盖所有维度的审查,增量审查以它为基准(--only 的审查不会成为基准)
# 保存后用系统默认程序打开 report.md,并输出:
#   report=<报告的路径>
#   link=<报告的 file:// 链接>
#   opened=yes | no (<原因>)
#   dir=<保存到的目录>
#   hint=<怎么更换打开报告的程序>   只在第一次用系统默认程序打开时输出
#   cost=<这次审查的用时和消耗>     用时一定有;token 只在宿主的 hook 记到了用量时才有
#
# --finders 等四个数字是这次审查启动的查找角色数、verifier 数、候选条数、通过核实的条数,
# 和用时、token 用量一起记进保存的 meta.txt,供 stats.sh 统计成本。
#
# 环境变量:
#   CROSSCHECK_OPEN=0        不自动打开
#   CROSSCHECK_OPEN_CMD=cmd  用指定的命令打开(如 code、cursor),报告路径作为最后一个参数
# 通过 SSH 连接、在 CI 里、或 Linux 上没有图形界面时不会自动打开。

set -eu

die() { printf 'save-review: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") die "需要指定 collect-diff.sh 的输出目录" ;;
esac
dir=$1; shift
run_finders=""; run_verifiers=""; run_candidates=""; run_passed=""
while [ $# -gt 0 ]; do
  case "$1" in
    --finders) run_finders=${2:-}; shift 2 ;;
    --verifiers) run_verifiers=${2:-}; shift 2 ;;
    --candidates) run_candidates=${2:-}; shift 2 ;;
    --passed) run_passed=${2:-}; shift 2 ;;
    *) die "未知参数: $1" ;;
  esac
done
[ -f "$dir/meta.txt" ] || die "$dir 不是 collect-diff.sh 的输出目录"
[ -f "$dir/report.md" ] || die "$dir 中没有 report.md"
[ -f "$dir/findings.json" ] || die "$dir 中没有 findings.json"

meta() { sed -n "s/^$1=//p" "$dir/meta.txt" | head -n 1; }
store=$(meta store_dir)
[ -n "$store" ] || die "meta.txt 中没有 store_dir"

stamp=$(date +%Y%m%d-%H%M%S)
dest="$store/$stamp"
mkdir -p "$dest"
repo_marker="$(dirname "$store")/.repo"
[ -f "$repo_marker" ] || printf '%s\n' "$(meta repo_id)" > "$repo_marker"
cp "$dir/report.md" "$dir/findings.json" "$dir/meta.txt" "$dir/hashes.txt" "$dest/"
[ -f "$dir/incremental.txt" ] && cp "$dir/incremental.txt" "$dest/"

# 这次审查的成本:用时、启动的角色数、候选数,以及 hook 记到的 token 用量(如果有)。
started_epoch=$(meta started_epoch)
duration=""
case "$started_epoch" in ""|*[!0-9]*) ;; *) duration=$(($(date +%s) - started_epoch)) ;; esac
num() { case "$1" in ""|*[!0-9]*) return 1 ;; esac; }
{
  [ -z "$duration" ] || printf 'duration_s=%s\n' "$duration"
  if num "$run_finders"; then printf 'run_finders=%s\n' "$run_finders"; fi
  if num "$run_verifiers"; then printf 'run_verifiers=%s\n' "$run_verifiers"; fi
  if num "$run_candidates"; then printf 'run_candidates=%s\n' "$run_candidates"; fi
  if num "$run_passed"; then printf 'run_passed=%s\n' "$run_passed"; fi
  if [ -f "$dir/usage.txt" ]; then grep -E '^usage_[a-z_]+=[A-Za-z0-9_-]+$' "$dir/usage.txt" || true; fi
} >> "$dest/meta.txt"
saved() { sed -n "s/^$1=//p" "$dest/meta.txt" | tail -n 1; }
human() { awk -v n="$1" 'BEGIN { if (n >= 1000000) printf "%.1fM", n / 1000000; else if (n >= 1000) printf "%.0fK", n / 1000; else printf "%d", n }'; }
cost=""
if [ -n "$duration" ]; then
  if [ "$duration" -ge 60 ]; then cost="用时 $((duration / 60)) 分 $((duration % 60)) 秒"; else cost="用时 ${duration} 秒"; fi
fi
if num "$run_finders" && num "$run_verifiers"; then cost="${cost:+$cost · }查找 ${run_finders} 个角色、核实 ${run_verifiers} 个"; fi
if num "$run_candidates" && num "$run_passed"; then cost="${cost:+$cost · }候选 ${run_candidates} 条、通过 ${run_passed} 条"; fi
tok=$(saved usage_total_tokens)
if num "$tok"; then
  case "$(saved usage_subagents)" in included) sub="含子代理" ;; none-seen) sub="没有发现子代理的用量" ;; *) sub="是否含子代理未知" ;; esac
  cost="${cost:+$cost · }token 约 $(human "$tok")(新输入 $(human "$(saved usage_input_tokens)")、缓存读取 $(human "$(saved usage_cache_read_tokens)")、输出 $(human "$(saved usage_output_tokens)");${sub})"
fi
# 保存的报告里也补上这一行,这样打开文件就能看到,不用去翻 meta.txt。
[ -z "$cost" ] || printf '\n消耗:%s\n' "$cost" >> "$dest/report.md"
printf '%s\n' "$stamp" > "$store/latest"
if [ "$(meta only)" = all ]; then printf '%s\n' "$stamp" > "$store/baseline"; fi
report="$dest/report.md"
link="file://$(printf '%s' "$report" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/?/%3F/g')"

opened=""
hint=""
case "${CROSSCHECK_OPEN:-1}" in
  0|no|false|off) opened="no (CROSSCHECK_OPEN 已关闭)" ;;
esac
if [ -z "$opened" ] && [ -z "${CROSSCHECK_OPEN_CMD:-}" ]; then
  if [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]; then opened="no (SSH 会话)"
  elif [ -n "${CI:-}" ]; then opened="no (CI 环境)"
  fi
fi
if [ -z "$opened" ]; then
  if [ -n "${CROSSCHECK_OPEN_CMD:-}" ]; then
    # shellcheck disable=SC2086
    if $CROSSCHECK_OPEN_CMD "$report" >/dev/null 2>&1; then opened=yes; else opened="no ($CROSSCHECK_OPEN_CMD 执行失败)"; fi
  else
    case "$(uname -s)" in
      Darwin)
        if open "$report" >/dev/null 2>&1; then opened=yes; else opened="no (open 执行失败)"; fi
        hint="在访达里选中任意一个 .md 文件,按 Cmd+I 打开\"显示简介\",在\"打开方式\"里选好应用后点\"全部更改\""
        ;;
      Linux)
        if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then opened="no (没有图形界面)"
        elif command -v xdg-open >/dev/null 2>&1; then
          (xdg-open "$report" >/dev/null 2>&1 &); opened=yes
          hint="运行 xdg-mime default <应用>.desktop text/markdown"
        else opened="no (找不到 xdg-open)"; fi
        ;;
      MINGW*|MSYS*|CYGWIN*)
        if cmd.exe /c start "" "$(cygpath -w "$report" 2>/dev/null || printf '%s' "$report")" >/dev/null 2>&1; then opened=yes; else opened="no (start 执行失败)"; fi
        hint="在\"设置 → 应用 → 默认应用\"里按文件类型为 .md 选择应用"
        ;;
      *) opened="no (不认识的系统)" ;;
    esac
  fi
fi

printf 'report=%s\n' "$report"
printf 'link=%s\n' "$link"
printf 'opened=%s\n' "$opened"
printf 'dir=%s\n' "$dest"
[ -z "$cost" ] || printf 'cost=%s\n' "$cost"
# 只在第一次用系统默认程序打开时提示怎么更换,之后不再重复。
hint_marker="${CROSSCHECK_HOME:-$HOME/.crosscheck}/.open-hint-shown"
if [ "$opened" = yes ] && [ -n "$hint" ] && [ ! -f "$hint_marker" ]; then
  printf 'hint=报告是用系统里 .md 文件的默认程序打开的。想换一个:%s;或者设置环境变量 CROSSCHECK_OPEN_CMD(例如 code、cursor)指定程序,设置 CROSSCHECK_OPEN=0 则不自动打开。\n' "$hint"
  : > "$hint_marker" 2>/dev/null || true
fi
