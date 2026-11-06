#!/bin/sh
# collect-context.sh — 收集审查需要的上下文。由 collect-diff.sh 在最后调用,不需要手动运行。
#
# 用法: collect-context.sh DIR
#
# 写入 DIR:
#   intent.md         这次改动的意图:用户补充的一句话、MR 描述、关联需求单(含验收标准)、
#                     需求单和需求文档(agent 用配置的工具读到的)、提交说明、计划等文档、
#                     MR 上的人工讨论摘要。都是待审查的材料,不是指令。
#   materials/m<n>.md 每份有正文的意图和需求材料的全文(人工讨论摘要除外);intent.md 里过长的只放摘录
#   materials.json    材料索引:id、label、kind、来源、字符数、行数、sha256、是否截断、partial、章节标题和验收条目的行号
#   sources.json      需求来源:每类(requirement、doc)的链接、状态、标题,以及缺了哪些(见 sources.py)
#   discussions.json  MR / PR 上的人工评论(只读获取;作者只存哈希)
#   weakening.txt     kind<TAB>file<TAB>说明<TAB>file:line<TAB>diff 片段(JSON 字符串) — 测试与检查变更的检测信号,不代表已确认缺陷
# 并在 meta.txt 末尾追加 intent_sources、intent_acceptance、intent_materials、requirement_materials、materials_clipped、
# materials_partial、acceptance_state(extracted|not_extracted|no_requirement)、discussions、weakening、sources_missing。
# 并输出 SOURCES_MISSING:(当前技能要问、且配了读取工具的需求来源里缺的)、NOTE: 等提示行。
# 读取需求单和讨论需要 glab 或 gh,读不到的部分跳过。

set -eu

[ -f "${1:-}/meta.txt" ] || { echo "collect-context: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/collect_context.py" "$1"
