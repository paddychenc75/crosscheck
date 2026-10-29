#!/bin/sh
# feedback.sh — 记录人对某条发现的判断;审查时把相关的历史判断交给 verifier 参考。
#
# 用法: feedback.sh VERDICT TARGET [原因] [--repo DIR]
#       feedback.sh history REVIEW_DIR OUT.json
#
#   VERDICT  invalid   不成立(误报)          必须给原因
#            wontfix   成立,但不打算修        必须给原因
#            valid     成立(修了,或认可)
#            disputed  有争议,还没有结论
#   TARGET   发现的 id(报告和记录里都有);也可以写 file:line,但多条记录对得上时会要求改用 id
#
# 判断记在项目级的 ~/.crosscheck/reviews/<项目名>/feedback.jsonl,对这个项目的所有分支、MR 和范围
# 都有效,不需要知道是哪一次审查。每条记录带着发现的身份:文件、维度、摘要、出问题的那一行,
# 以及那一行上下各 2 行代码的哈希;还记着是谁判断的(邮箱的哈希,不存人名)。
#
# history 由 collect-diff.sh 在每次审查开始时调用:取出这次改动涉及的文件上的历史判断,并标明
# 那段代码现在是否原样还在(same_code),写进 OUT.json。它不删除任何候选,怎么用由 verifier 判断:
# 重新核对当前场景、调用环境和历史前提后才可抑制重复提醒;代码原样未变本身不够。
# 不修不否定事实或置信度,不成立须当前反证仍成立。需要 python3。

set -eu

case "${1:-}" in -h|--help|"") sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
command -v python3 >/dev/null 2>&1 || { echo "feedback: 需要 python3。这次判断没有被记录,请如实告诉用户" >&2; exit 1; }
dir=$(cd "$(dirname "$0")" && pwd)
if [ "$1" = history ]; then shift; exec python3 "$dir/feedback_history.py" "$@"; fi
exec python3 "$dir/feedback_record.py" "$@"
