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
#   WEAKEN:    测试与检查变更的检测信号,非确认缺陷;首行是 file:line(删除行带「旧版」)和说明,紧跟 diff 片段代码块,原样写进报告
#   SKIPPED:   跳过的文件按原因计数
#   CHECKED:   「已检查:」那一行
#   TRIMMED:   因条数上限没有展示的条数
#   INCOMPLETE: 查找角色的回复或核实结果缺了哪些(没有缺就不输出),放在消耗行之前
#   MARK:      每条展示的发现怎么确认的:<编号><TAB>已复现 | 读代码推断,写进该条标题
#   DISCUSSED: 讨论中已有人提出、代码仍未改的发现:<编号><TAB>说明,写进该条
#   PREEXIST:  改动代码里的原有问题(基线里就存在、不是本次引入的):<位置><TAB><摘要><TAB><核实的判断>,
#              报告末尾单列一节「改动代码里的原有问题」,没有就不输出
#   UNCLEAR:   需求核对里交付范围待明确的条目(不计入发现):<位置><TAB><摘要><TAB><要求原文><TAB><限定原文><TAB><还缺什么判断>
#   SCOPE_CONFLICT: 同一处要求上不同核实结论的交付范围不一致:<要求位置> 上…:<scope>=<编号,…>;…;只提示,不改去向
#   FEEDBACK:  仅展示条目可对应 findings 记录时输出,示例用实际编号

set -eu

command -v python3 >/dev/null 2>&1 || { echo "report-lines: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "report-lines: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/report_lines.py" "$1"
