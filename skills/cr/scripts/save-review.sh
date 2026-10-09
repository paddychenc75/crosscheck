#!/bin/sh
# save-review.sh — 把一次审查的报告和发现存入审查记录,供以后查看和做增量审查。
#
# 用法: save-review.sh DIR
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
#
# 环境变量:
#   CROSSCHECK_OPEN=0        不自动打开
#   CROSSCHECK_OPEN_CMD=cmd  用指定的命令打开(如 code、cursor),报告路径作为最后一个参数
# 通过 SSH 连接、在 CI 里、或 Linux 上没有图形界面时不会自动打开。

set -eu

die() { printf 'save-review: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") die "需要指定 collect-diff.sh 的输出目录" ;;
esac
dir=$1
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
# 只在第一次用系统默认程序打开时提示怎么更换,之后不再重复。
hint_marker="${CROSSCHECK_HOME:-$HOME/.crosscheck}/.open-hint-shown"
if [ "$opened" = yes ] && [ -n "$hint" ] && [ ! -f "$hint_marker" ]; then
  printf 'hint=报告是用系统里 .md 文件的默认程序打开的。想换一个:%s;或者设置环境变量 CROSSCHECK_OPEN_CMD(例如 code、cursor)指定程序,设置 CROSSCHECK_OPEN=0 则不自动打开。\n' "$hint"
  : > "$hint_marker" 2>/dev/null || true
fi
