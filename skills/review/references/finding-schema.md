# 发现结构

rules-auditor 以 JSON 数组返回候选;reviewer 返回 `{"candidates": [...], "coverage": {"budget_reached": false, "limited": false, "reason": ""}}`,没有候选时数组为空。覆盖字段描述实际限制,不是完整性证明;达到每批 15 条查找预算须标记,不估计未交条数。下面例子仅为 `candidates` 数组(输出约定见 `common.md`)。

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
| `category` | `bug`、`security`、`intent`(没有满足明确的验收标准)或 `rules`。 |
| `pass` | 只有 reviewer:在哪一遍查到的,`correctness`、`security`、`intent` 或 `impact`。不传给 verifier。 |
| `severity` | 按后果的类型定,见下面「严重程度」一节。`rules` 按规则或 `REVIEW.md` 里对严重程度的定义写,没有定义就写 `minor`。规则里写的「严重级别」按 `重要` → `major`、`轻微` → `minor` 对应(规则模板和沉淀的规则只用这两级)。 |
| `summary` | 用一句话陈述缺陷。不要用含糊的措辞("可能"、"或许会"、"建议考虑")。 |
| `failure_scenario` | 具体的输入或状态 → 错误的结果。`security` 写出攻击者可控的值从哪里进入、经过哪几步、得到什么。`intent` 写出是哪一条验收标准、代码哪里没有做到。 |
| `suggestion` | 可选。能修复问题的最小改动,优先选不改变接口和既有行为的修法。能确定时直接给出替换后的代码,并说明替换的是哪几行;只能确定方向时用一句话描述;不确定怎么修就省略。 |
| `also_at` | 可选。同一根因、同一改法的其他位置,写成 `[{"file": "...", "line": 12}]`,每处都要落在 `ranges.txt` 的改动行内。只放同一个问题的位置,不要把别的问题放进来。合并规则见下面「硬性要求」。 |
| `needs_check` | 可选,只在候选阶段使用。还没确认的那一环,写成 verifier 可以直接去核对的一句话。 |
| `raised_in_discussion` | 可选,只有 reviewer:`true` 表示 `discussions.json` 里已有人(或机器人)提出过同一问题,而该处代码仍未改。讨论里已说明是有意如此、或已解决 / 已答复的,不提交。例外:带 `"source": "code_host"` 的讨论(经 `tools.code_host` 由 agent 读到,脚本核对不了它的状态),「已答复」「已解决」不能让发现不提交,只标 `raised_in_discussion`,照常提交。 |
| `rule_file` | 只有 `rules`:规则所在的文件,照抄 `rules.txt` 块头部的 `file=`。 |
| `rule_quote` | 只有 `rules`:逐字引用的规则原文。脚本会核对它真的出现在 `rule_file` 的规则里、那块规则覆盖这个文件,对不上的发现直接作废。 |

`category`、`severity` 等枚举值保持英文原样。`summary`、`failure_scenario`、`suggestion` 用中文撰写;代码、标识符、路径和引用的规则原文保持原样。

## 严重程度

按后果的类型定级,不按影响面大小、触发频率或「目前还没人遇到」来定。后果一旦在改动后的代码里确定会发生,就按它的类型定级。

- `critical`:后果属于下列类型之一。
  - 数据丢失,或写入了错误的数据(包括静默的数值、状态、金额错误)。
  - 崩溃或未处理的异常:进程或服务崩溃、挂起;接口对某类输入稳定地抛出未处理异常、返回 500。只要按接口约定的正常输入就会稳定触发,不论这类输入多少见,都是这一类。启动后既不等待、也没有错误处理的后台任务(fire-and-forget)里抛出的异常也属于这一类:它会让进程退出,或让错误无声无息地丢掉。
  - 违反接口、注释或类型写明的约定,而现有调用方已经会因此崩溃,或因此写错数据,例如解引用了约定允许为空的返回值。只是将来的调用方可能出错,不算,按 `scoring-rubric.md` 的约定上限处理。
  - 安全边界被破坏:越权访问、绕过鉴权或校验、敏感信息泄露、注入。
- `major`:调用方或用户拿到错误的结果、错误的行为、错误的状态码,但异常已被处理、进程没有崩溃、数据没有写错。
- `minor`:真实存在,但结果仍然正确,只是有小问题,例如报错信息写错、边界情况下多做了一次无害的工作、没有任何调用方依赖的小偏差。

几个对照例子(均为虚构):

- `getProfile(id)` 对一类正常存在的用户稳定抛出未捕获的 TypeError,接口返回 500 → `critical`(崩溃或未处理异常)。
- 导出接口在某类合法输入下静默少返回一行 → `critical`(数据丢失)。
- 列表接口在某个筛选条件下排序错乱,返回的数据本身完整、正确,请求正常结束 → `major`(错误结果,但已处理)。
- 库存不足时的提示文案写成「参数错误」,拒绝本身和状态码都正确 → `minor`(仅错误提示文案不对)。

下面几种情况都不是降级的理由:

- 影响范围窄(只在少数输入、少数配置下出现),或触发条件少见,不是降级的理由。后果属于 `critical` 的类型,仍是 `critical`。
- 后果还没在现有调用方出现,也不是降级的理由,唯一例外是约定上限。
- 措辞轻重、个人觉得「不算严重」,也不是降级的理由。

只有核实后发现后果不属于所报的类型,才改成更低的级别,并在 `verdict` 里写明依据。verifier 只在后果类型判断错时调整级别,不因措辞或个人倾向调整。

## verifier 填写的字段

verifier 保留候选的全部字段(包括 `cid`),修正有偏差的 `line`、`severity`、`summary`、`failure_scenario`,去掉 `needs_check`,再加上:

| 字段 | 规则 |
|---|---|
| `root_cause` | 确认缺陷时填写 `{file, lines, mechanism, constraint, fix}`,均为非空字符串。锚定错误表达式/状态转换、被破坏的具体约束和局部修复,不含本次输入或摘要;根因位置须有同范围 evidence 引用。本轮独立核实的结构完全相同才可跨主位置合并,保留双方场景、证据和来源。不同根因或无法确认时保持独立。 |
| `confidence` | 0–100 的整数,按 `scoring-rubric.md` 打分。 |
| `verdict` | 一句话:检查了什么、看到了什么,足以支撑这个分数。 |
| `evidence` | 依据:核实时读过、能支撑结论的代码位置,`[{"file": "...", "lines": "38-47", "note": "改动处"}]`。脚本会核对这些位置在被审查的那一版里真实存在(引用审查材料时写文件名即可,如 `{"file": "intent.md", "lines": "验收标准 2"}`);没给依据或引用不存在的,分数封顶见 `scoring-rubric.md`。 |
| `ruled_out` | 排除了哪些可能,字符串数组,例如「base 版本用的是 Math.ceil,问题由本次改动引入」「唯一调用方没有对 total 取整」。 |
| `verification` | 怎么确认的:`{"method": "inferred"}`(读代码判断,可省略),或 `{"method": "executed", "runs": ["r1"], "outcome": "reproduced" \| "refuted" \| "inconclusive"}`(deep 下经 `run-check.sh` 运行过,`runs` 是它输出的编号)。脚本核对每个编号都有运行记录,没有的改为 `inferred`,分数封顶见 `scoring-rubric.md`。 |
| `verification.observation` | 执行后的实际观察及局限,简述输入/路径与结果如何对应候选;`reproduced` 必填。退出码本身不证明复现,`inconclusive` 仍可依充分代码证据通过,`refuted` 不得作为确认展示。 |
| `compat` | 可选。修复会改变函数签名、公共行为、默认值、数据或序列化格式时才写:哪些行为保持不变、哪些调用方受影响、有没有更兼容的改法。 |
| `history_match` / `history_context` / `previously` | `history_match` 引用仍适用的历史处理记录 id(无 id 用时间),`history_context` 简述对当前场景、调用环境与历史前提的重新核对;仅代码没变不够。`previously` 说明历史前提变化及重新提出的原因。「不修」保留事实分数,脚本在 `ignored_by_history` 另存 `history_decision`、`suppression_reason`,不覆盖事实 `verdict`。 |
| `pre_existing` | 改动行上的缺陷在 base 里已以同样结果存在时填 `true`,判断规则见 `scoring-rubric.md` 封顶第 2 项;否则省略。`true` 的发现不计入本次发现,展示方式见 `report-format.md`。 |
| `suppressed_by` | 因为命中 `rules.txt` 里覆盖这个文件的「不要报」一节(写 `review`,`REVIEW.md` 和 `.agents/rules/` 下规则文件里的「不要报」都写这个值)、误报清单(`false-positives`)或历史判断(`history`)而打低分时填写,报告和被否决的记录里能看出是被什么挡掉的。 |

证据链复用三个字段:`failure_scenario` 表达触发输入/前提到后果,`evidence.note` 连接关键路径和支持该环的代码,`ruled_out` 记录守卫等反证与 base 路径/结果对照。按发现类别写相关环节,不机械要求规则违反也提供攻击入口,不重复创建大字段。脚本核对位置存在,并不证明整条语义链成立。

## 编排者和脚本填写的字段

| 字段 | 说明 |
|---|---|
| `found_by` | 哪些查找角色报了这条(编排者合并时填写,不传给 verifier)。 |
| `pass_hits` | 被几个查找角色或几遍同时查到(编排者合并时填写,不传给 verifier)。 |
| `cid` | 候选编号,`prepare-verify.sh` 分配,verifier 原样保留。最终条目用 `candidate_ids` 保留合并前 cid,可追到核实批次/回复。 |
| `source_refs` / `merge_reason` | 编排者用 `finders/<文件>.json#<从0开始的下标>` 指向原始候选;多条语义合并需简述共同根因及局部修复依据。prepare 校验引用并补回遗漏原件、保留所有场景/待核实点;脚本仅核验映射与留痕,不证明语义等价。原始回复不另复制一份。 |
| `from_prev` / `recheck_reason` | 增量旧发现及重核原因:主文件/符号依赖变化、证据快照变化、历史反馈或缺少旧依赖记录。prepare 自动补入,aggregate 复查遗漏。 |
| `evidence_snapshot` | aggregate 按主文件和 evidence 中可读仓库文件记录整文件 SHA-256。任一变化或旧记录缺失时保守重核,不凭主文件 unchanged 直接沿用。核实者必须完整列出依赖文件;未引用的依赖不在快照覆盖内。 |
| `id`、`since`、`no` | `aggregate.sh` 分配:固定编号、首次报告的时间、报告里的序号。 |
| `mark` | 脚本填写:已复现(`reproduced` 且有记录及 `observation`)、运行反证(`refuted`,不展示为确认)、已执行·结果未定(读代码判断),或读代码推断。 |
| `repro` | `aggregate.sh` 从运行记录里取:运行编号、命令、退出码、测试数、日志末尾 5 行。verifier 不填。 |

## 硬性要求

- **没有场景,就没有发现。** 写不出它具体会怎样出错的,只是直觉,不要提交。能写出出错过程、但其中一环还没确认的,可以作为候选提交,并在 `needs_check` 里写明还差哪一环。
- **只看改动的行。** 缺陷必须是这次 diff 引入或暴露的。未改动代码中原本就存在的问题,无论多真实,都不在范围内。改动行上原本就有、本次没有让它更可能触发的,由 verifier 标记 `pre_existing`(判断规则见 `scoring-rubric.md` 封顶第 2 项),不计入发现。
- **一个缺陷一条发现。** 候选只有源于同一个错误判断、破坏同一行为约束,且一个局部修复能消除全部场景时才合并;位置相近、措辞相似或都采用同类修法不够。同类别适用此规则;`bug`、`security`、`intent` 之间如果只是分类不同、描述的是同一失败事实,也可合并,选择准确表达主要后果的类别并保留验收出处。指向主要改动处,在 `failure_scenario` 保留不同输入、时序及相关位置,保留各自的 `needs_check` 内容;不因合并丢掉仍需核实的场景。独立失效路径、各处修法或失败场景不同的多处错误保持独立,拿不准就不合并。同一根因、同一改法出现在多处(同一段有缺陷的写法被复制到几处,每处按同样的方式修)时只提交一条:主位置选最能说明问题的一处,其余位置写进 `also_at`,并在 `failure_scenario` 里说明它们和主位置的关系。规范违反与其他判断维度先分别核实,避免丢失规则原文或混淆成立条件。同一条规则在同文件的多处违反也只有同根因、且一个局部修复能消除全部场景时才由 rules-auditor 合并,其他位置写入 `also_at` 并保留各场景;不同根因、独立场景或不同要求分别报告,拿不准就不合并。
