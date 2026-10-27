#!/bin/sh
# save-review.sh — 把一次审查的报告和发现存入审查记录,供以后查看和做增量审查。
#
# 用法: save-review.sh DIR [--finders N] [--verifiers N] [--candidates N] [--passed N]
#
#   DIR  collect-diff.sh 的输出目录,其中需要已经写好:
#          report.md      给用户看的报告
#          findings.json  本次审查结束时仍然成立的全部发现(aggregate.sh 写出,含从上次带过来的)
#        另外会一并保存(有就存):final.json、rejected.json(没通过核实的候选)、intent.md、weakening.txt、
#        settings.json、rules-index.json、rules-meta.txt、rules-notes.json,以及 MR 说明用的
#        sources.json、analysis-facts.json、analyst.json、analysis.json
#
# 写入 ${CROSSCHECK_HOME:-~/.crosscheck}/reviews/<项目名>/<目标>/<时间>/,并更新同级的两个指针文件:
#   latest    最近一次审查
#   baseline  最近一次覆盖所有维度的审查,增量审查以它为基准(--only 的审查不会成为基准)
# 输出:
#   report=<报告的路径>
#   link=<报告的 file:// 链接>
#   dir=<保存到的目录>
#   cost=<这次审查的用时和消耗>     用时一定有;token 只在宿主的 hook 记到了用量时才有
#
# --finders 等四个数字是这次审查启动的查找角色数、verifier 数、候选条数、通过核实的条数,
# 和用时、token 用量一起记进保存的 meta.txt,供 stats.sh 统计成本。候选和通过的条数没给时取自 final.json。

set -eu

die() { printf 'save-review: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
[ ! -f "$dir/verification-errors.json" ] || die "核实未完成，不能保存为完整审查记录"
[ -f "$dir/findings.json" ] || die "$dir 中没有 findings.json"

meta() { sed -n "s/^$1=//p" "$dir/meta.txt" | tail -n 1; }
store=$(meta store_dir)
[ -n "$store" ] || die "meta.txt 中没有 store_dir"

stamp=$(date +%Y%m%d-%H%M%S)
dest="$store/$stamp"
mkdir -p "$dest"
repo_marker="$(dirname "$store")/.repo"
[ -f "$repo_marker" ] || printf '%s\n' "$(meta repo_id)" > "$repo_marker"
cp "$dir/report.md" "$dir/findings.json" "$dir/meta.txt" "$dir/hashes.txt" "$dest/"
[ -f "$dir/incremental.txt" ] && cp "$dir/incremental.txt" "$dest/"
for f in context-hash.txt final.json rejected.json intent.md weakening.txt settings.json rules-index.json rules-meta.txt rules-notes.json sources.json analysis-facts.json analyst.json analysis.json; do
  [ -f "$dir/$f" ] && cp "$dir/$f" "$dest/"
done
if { [ -z "$run_candidates" ] || [ -z "$run_passed" ]; } && [ -f "$dir/final.json" ]; then
  counts=$(python3 -c 'import json,sys; c=json.load(open(sys.argv[1])).get("counts",{}); print(c.get("candidates",""), c.get("passed",""))' "$dir/final.json" 2>/dev/null || true)
  [ -n "$run_candidates" ] || run_candidates=${counts% *}
  [ -n "$run_passed" ] || run_passed=${counts#* }
fi

# 给每条发现记下它指向的那一行代码,作为跨审查识别"同一个问题"的依据;
# 增量审查时,判断上次的每条发现去哪了:代码改了、代码没变但这次没报、还是文件已不在改动里。
python3 "$(cd "$(dirname "$0")" && pwd)/review_store.py" "$dir" "$dest" || true


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
if ! num "$tok"; then
  case "$(saved usage_status)" in
    no_start) why="hook 在运行,但审查开始时没拿到会话记录" ;;
    no_records) why="hook 在运行,但会话记录里读不到用量" ;;
    unsupported) why="Cursor 的会话记录里没有用量,不统计 token" ;;
    *) why="hook 没有运行:没有以插件方式安装,或在 Codex 里还没有用 /hooks 信任" ;;
  esac
  cost="${cost:+$cost · }token 未统计(${why})"
elif true; then
  case "$(saved usage_subagents)" in included) sub="含子代理" ;; none-seen) sub="没有发现子代理的用量" ;; *) sub="是否含子代理未知" ;; esac
  cost="${cost:+$cost · }token 约 $(human "$tok")(新输入 $(human "$(saved usage_input_tokens)")、缓存读取 $(human "$(saved usage_cache_read_tokens)")、输出 $(human "$(saved usage_output_tokens)");${sub})"
fi
# 保存的报告里也补上这一行,这样打开文件就能看到,不用去翻 meta.txt。
[ -z "$cost" ] || printf '\n消耗:%s\n' "$cost" >> "$dest/report.md"
printf '%s\n' "$stamp" > "$store/latest"
if [ "$(meta only)" = all ]; then printf '%s\n' "$stamp" > "$store/baseline"; fi
report="$dest/report.md"
link="file://$(printf '%s' "$report" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/?/%3F/g')"

printf 'report=%s\n' "$report"
printf 'link=%s\n' "$link"
printf 'dir=%s\n' "$dest"
[ -z "$cost" ] || printf 'cost=%s\n' "$cost"
