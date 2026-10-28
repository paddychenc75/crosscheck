#!/bin/sh
# aggregate.sh — 汇总核实结果:校验依据、套用审查设置、过滤、排序、编号,写出报告和记录要用的文件。
#
# 用法: aggregate.sh DIR
#
# 读取 DIR 里的 candidates.json(prepare-verify.sh 编过号的候选)和 verify/result-*.json(编排者把每个
# verifier 的回复原样存成这些文件),以及 meta.txt、ranges.txt、settings.json、rules-index.json、
# incremental.txt、prev-findings.json。按顺序做这些事,都不需要判断:
#   1. 依据校验:verifier 给出的 evidence 引用的文件和行在被审查的那一版里必须真实存在
#      (review_dir 里的 intent.md、rules.txt 等材料只核对文件存在;位置写成文字的,核对那段文字在文件里);
#      引用不存在或者干脆没给依据的,置信度最高 50。
#   2. 规则发现:rule_file 必须是这次适用的规则文件、覆盖这个文件;rule_quote 必须逐字出现在那份规则里
#      (「不要报」一节不算规则)。
#      对不上的置信度为 0。严重程度用核实后的分级,没有就是轻微。
#   3. 只看改动的行;REVIEW.md 的 skip 路径不报;带 history_match 的单独列为「已按历史判断忽略」。
#   4. 置信度 80 分以上的才报,其余连同分数和理由写进 rejected.json。
#   5. 增量:上次的发现分成遗留(文件没变的原样带入;文件变了且再次通过核实的)和已解决。
#   6. 同一条规则在同一个文件里的多处合并成一条;按严重程度、置信度排序;套用条数上限
#      (默认缺陷 10 条、quick 5 条,需求核对、规范各 5 条;REVIEW.md 的 limits 可覆盖,
#      还可以按 critical/major/minor 限制缺陷和需求核对的条数),超出的不展示但仍写进 findings.json。
#   7. 给展示的发现编号(no),给新发现分配 id 和 since。
# 写出:
#   completeness.txt  流程是否完整(completeness.py 写):缺了哪些查找角色的回复或核实结果;只提示,不阻止汇总
#   final.json     报告要用的全部内容:groups(defects/intent/rules,每条带 no 和 status:new|carried)、
#                  resolved、ignored_by_history、trimmed、counts、notes
#   findings.json  本次结束时仍然成立的全部发现(含因条数上限没展示的),供 save-review.sh 保存和下次增量
#   rejected.json  没通过的候选,带分数和理由,用于校准阈值
# 最后一行输出:passed=… rejected=… new=… carried=… resolved=… trimmed=… ignored_by_history=…

set -eu

command -v python3 >/dev/null 2>&1 || { echo "aggregate: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "aggregate: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
# 先查流程是否完整(只写 completeness.txt,即使下面的汇总因核实缺失而停下也会写),不阻止汇总
python3 "$here/completeness.py" "$1" || echo "aggregate: 流程完整性检查失败,不影响汇总" >&2
exec python3 "$here/aggregate.py" "$1"
