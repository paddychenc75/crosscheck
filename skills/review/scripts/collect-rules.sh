#!/bin/sh
# collect-rules.sh — 装配这次审查适用的项目规则。规则就是仓库里的 Markdown 文件,不需要编号或元数据。
#
# 用法: collect-rules.sh DIR    DIR 是 collect-diff.sh 的输出目录
#
# 读这些文件(只取覆盖到改动文件的):
#   目录级 REVIEW.md(深的在前)→ 根目录 REVIEW.md
#     REVIEW.md 里标题为「不要报」(或 Do not report,可带括号说明)的一节是团队不想再被提醒的情况,只用来压掉发现,不作为要检查的规则
#   → 给 coding agent 的约定:CLAUDE.md、AGENTS.md、.claude/rules/、.cursor/rules/、.github/instructions/ 等
#     (按各自的路径或 frontmatter 范围生效;Copilot 的 excludeAgent: code-review 会被排除)
#   → .agents/rules/**/*.md:从 MR 评论沉淀的规则(improve),范围取 frontmatter 的 paths(或 globs),没有就是整个仓库;
#     其中的「不要报」一节和 REVIEW.md 一样只用来压掉发现
# 写入 DIR:
#   rules.txt         规则正文,每块以一行头部开始:
#                     === RULES layer=<L3|L2|external> source=<来源> file=<路径> scope=<glob> ===
#   rules-index.json  每块的文件、来源、正文(text,不含「不要报」一节)、「不要报」(suppress)和覆盖的改动文件;
#                     aggregate.sh 用它核对规则发现的出处和引文
#   rules-meta.txt    blocks=<块数> source=<读自哪一版> errors=<格式错误> excluded=<被排除的文件数>
#
# 读哪一版:本地审查自己的分支读工作区(改了规则马上生效);PR/MR、其他分支、提交范围读 base 那一版,
# 改动本身改不了审查用的规则。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "collect-rules: 需要 python3" >&2; exit 1; }
dir=${1:-}
case "$dir" in -h|--help|"") sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
[ -f "$dir/meta.txt" ] || { echo "collect-rules: $dir 不是 collect-diff.sh 的输出目录" >&2; exit 1; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/collect_rules.py" "$dir"
