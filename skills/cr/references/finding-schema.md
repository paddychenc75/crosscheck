# 发现结构

查找角色以 JSON 数组返回候选。没有可报告的内容时返回 `[]` —— 结果为空是正常的、好的结果。

```json
[
  {
    "file": "src/orders/paginate.ts",
    "line": 42,
    "category": "bug",
    "severity": "major",
    "summary": "total 恰好是 pageSize 的整数倍时,最后一页被丢掉",
    "failure_scenario": "total=40, pageSize=20 → pageCount 算出 1,第 21-40 条永远不会返回",
    "suggestion": "改用 Math.ceil(total / pageSize)"
  }
]
```

## 查找角色填写的字段

| 字段 | 规则 |
|---|---|
| `file` | 相对仓库根目录的路径,与 `files.txt` 中的写法完全一致。 |
| `line` | diff **新**侧的行号,且落在 `ranges.txt` 中该文件的某个范围内。指向出错的那一行,而不是函数的起始行。 |
| `category` | `bug`、`security`、`intent`(没有满足明确的验收标准)、`rules` 或 `simplify`。 |
| `severity` | `critical` — 数据丢失、安全漏洞、常见路径上崩溃。`major` — 用户或调用方会遇到的错误行为。`minor` — 真实存在但影响小。`simplify` 一律 `minor`;`rules` 按规则或 `REVIEW.md` 里对严重程度的定义写,没有定义就写 `minor`。 |
| `summary` | 用一句话陈述缺陷。不要用含糊的措辞("可能"、"或许会"、"建议考虑")。 |
| `failure_scenario` | 具体的输入或状态 → 错误的结果。`security` 写出攻击者可控的值从哪里进入、经过哪几步、得到什么。`intent` 写出是哪一条验收标准、代码哪里没有做到。`simplify` 指出让这段改动变得多余的已有代码,或可以合并的具体行。 |
| `suggestion` | 可选。能修复问题的最小改动,优先选不改变接口和既有行为的修法。能确定时直接给出替换后的代码,并说明替换的是哪几行;只能确定方向时用一句话描述;不确定怎么修就省略。 |
| `needs_check` | 可选,只在候选阶段使用。还没确认的那一环,写成 verifier 可以直接去核对的一句话。 |
| `rule_file` | 只有 `rules`:规则所在的文件,照抄 `rules.txt` 块头部的 `file=`。 |
| `rule_quote` | 只有 `rules`:逐字引用的规则原文。脚本会核对它真的出现在 `rule_file` 的规则里、那块规则覆盖这个文件,对不上的发现直接作废。 |

`category`、`severity` 等枚举值保持英文原样。`summary`、`failure_scenario`、`suggestion` 用中文撰写;代码、标识符、路径和引用的规则原文保持原样。

## verifier 填写的字段

verifier 保留候选的全部字段(包括 `cid`)，修正有偏差的 `line`、`severity`、`summary`、`failure_scenario`，去掉 `needs_check`，再加上:

| 字段 | 规则 |
|---|---|
| `confidence` | 0–100 的整数,按 `scoring-rubric.md` 打分。 |
| `verdict` | 一句话:检查了什么、看到了什么,足以支撑这个分数。 |
| `evidence` | 依据:核实时读过、能支撑结论的代码位置,`[{"file": "...", "lines": "38-47", "note": "改动处"}]`。脚本会核对这些位置在被审查的那一版里真实存在(引用审查材料时写文件名即可,如 `{"file": "intent.md", "lines": "验收标准 2"}`);没给依据或引用不存在的,分数最高 50。 |
| `ruled_out` | 排除了哪些可能,字符串数组,例如「base 版本用的是 Math.ceil,问题由本次改动引入」「唯一调用方没有对 total 取整」。 |
| `verification` | 怎么确认的:`{"method": "inferred"}`(读代码判断,可省略),或 `{"method": "executed", "runs": ["r1"], "outcome": "reproduced" \| "refuted" \| "inconclusive"}`(deep 下经 `run-check.sh` 运行过,`runs` 是它输出的编号)。脚本核对运行记录,对不上的改为 `inferred`、分数最高 60。 |
| `compat` | 可选。修复会改变函数签名、公共行为、默认值、数据或序列化格式时才写:哪些行为保持不变、哪些调用方受影响、有没有更兼容的改法。 |
| `history_match` / `previously` | 前者表示和项目里已有的「不成立/不修」判断是同一个问题且代码没变;后者表示以前被否定过、代码变了之后重新评估仍然成立。 |
| `suppressed_by` | 因为命中 `REVIEW.md` 的「不要报」(写 `review`)、误报清单(`false-positives`)或历史判断(`history`)而打低分时填写,报告和被否决的记录里能看出是被什么挡掉的。 |

## 编排者和脚本填写的字段

| 字段 | 说明 |
|---|---|
| `found_by` | 哪些查找角色报了这条(编排者合并时填写,不传给 verifier)。 |
| `pass_hits` | 被几个查找角色或几遍同时查到(编排者合并时填写,不传给 verifier)。 |
| `cid` | 候选编号,`prepare-verify.sh` 分配,verifier 原样保留。 |
| `from_prev` | 增量时,上次的发现在变了的文件里,需要重新核实。 |
| `id`、`since`、`no` | `aggregate.sh` 分配:固定编号、首次报告的时间、报告里的序号。 |
| `verification_status`、`mark` | `aggregate.sh` 核对运行记录后填写:`executed`(有宿主见证)→ 已复现;`self_reported`(有记录但没有 hook 见证)→ 已复现(自报);`inferred` / `failed` → 读代码推断。 |
| `repro` | `aggregate.sh` 从运行记录里取:运行编号、命令、退出码、测试数、日志末尾 5 行、是否有见证。verifier 不填。 |

## 硬性要求

- **没有场景,就没有发现。** 写不出它具体会怎样出错的,只是直觉,不要提交。能写出出错过程、但其中一环还没确认的,可以作为候选提交,并在 `needs_check` 里写明还差哪一环。
- **只看改动的行。** 缺陷必须是这次 diff 引入或暴露的。未改动代码中原本就存在的问题,无论多真实,都不在范围内。
- **一个缺陷一条发现。** 候选只有源于同一个错误判断、破坏同一行为约束，且一个局部修复能消除全部场景时才合并；位置相近、措辞相似或都采用同类修法不够。同类别适用此规则；`bug`、`security`、`intent` 之间如果只是分类不同、描述的是同一失败事实，也可合并，选择准确表达主要后果的类别并保留验收出处。指向主要改动处，在 `failure_scenario` 保留不同输入、时序及相关位置，保留各自的 `needs_check` 内容；不因合并丢掉仍需核实的场景。独立失效路径、需要分别修复的多处错误保持独立，拿不准就不合并。规范违反及简化建议与其他判断维度先分别核实，避免丢失规则原文或混淆成立条件。同一条规则在同文件的重复违反由 rules-auditor 合并成一条;同一规则文件里的不同要求分别报告。

核实完成后的跨维度重复由主 agent 按同一失败事实生成 `display-groups.json`，仅在 `report.json` 合并展示。原始发现、编号及独立核实依据留在 `final.json` / `findings.json`，规范原文作为关联依据展示。角色不得自行写 `related_findings` 或 `display_members` 绕过核实；这些字段由报告脚本从通过核实的独立发现生成。
