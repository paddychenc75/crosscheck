#!/bin/sh
# usage.sh — 尽力统计一次审查消耗的 token。由 guard.sh 在审查开始和保存时调用,不需要手动运行。
#
# 用法: usage.sh start  STATE TRANSCRIPT   记下会话记录当前的位置,作为这次审查的起点
#       usage.sh finish STATE OUT          汇总起点之后的用量,以 key=value 写入 OUT
#
# 只统计 Claude Code:读它的会话记录里每次模型调用的 usage(子代理的记录在 <会话>/subagents/ 下,一并计入),
# 只读用量数字,不读对话内容。Codex、Cursor 不统计。
# 只由 hook 调用、读 hook 给出的那份会话记录;不会去猜或扫描其他会话的记录。读不到就什么都不写,不影响审查。这些字段都没有写进宿主的正式文档,版本变化后可能失效。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || exit 0
[ $# -ge 3 ] || exit 0
python3 "$(cd "$(dirname "$0")" && pwd)/usage.py" "$@" || exit 0
