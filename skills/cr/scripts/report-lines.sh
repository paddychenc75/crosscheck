#!/bin/sh
# report-lines.sh — 生成报告里由数据决定的那几行,编排者原样放进报告,不用自己拼。
#
# 用法: report-lines.sh DIR
#
# 输出若干行,每行以一个标签开头(标签本身不写进报告):
#   SCOPE:     报告第一行:审查范围、深度、全量还是增量
#   INTENT:    意图取自哪些来源
#   RULES:     用了哪些规则文件、违反几条
#   RULES_NOTE: 规则相关的提示(可能过时、冲突、格式有误、被排除),可能有多行
#   WEAKEN:    测试或检查被削弱的每一处,可能有多行(没有就不输出)
#   SKIPPED:   跳过的文件按原因计数
#   CHECKED:   「已检查:」那一行
#   TRIMMED:   因条数上限没有展示的条数
#   GUARD:     没有检测到只读拦截时说明一句(检测到就不输出)
#   MARK:      每条展示的发现怎么确认的:<编号><TAB>已复现 | 读代码推断,写进该条标题
#   DISCUSSED: 讨论中已有人提出、代码仍未改的发现:<编号><TAB>说明,写进该条
#   FEEDBACK:  报告末尾提示怎么快速表态

set -eu

command -v python3 >/dev/null 2>&1 || { echo "report-lines: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "report-lines: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/report_lines.py" "$1"
