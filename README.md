# crosscheck

在本地仓库里运行的 AI 代码审查插件,支持 Claude Code、Codex、Cursor,可审本地分支、提交范围、GitHub PR 和 GitLab MR。

和普通的「让 AI 看一遍 diff」不同:**先查找候选,再独立核实,只展示证据评分达到 80 分的发现**。每条附代码位置、失败场景和修复建议;没有确认的发现就如实说没有,不凑数。

[是什么](#是什么) · [三个技能](#三个技能) · [使用](#使用) · [项目规则](#项目规则) · [配置](#配置) · [记录存在哪](#记录存在哪) · [常见问题](#常见问题) · [已知限制](#已知限制)

原理、流程演示和设计依据见 [DESIGN.html](DESIGN.html),在本地浏览器打开。

## 是什么

| 特点 | 说明 |
|---|---|
| 精确率优先 | 查找和核实分开做;评分衡量证据是否充分,不是正确概率。低于 80 分的不展示,但会存档 |
| 有依据 | 依据里的代码位置由脚本核对,不存在或没给依据的最高 50 分;违反规则的必须逐字引用规则原文 |
| 查找方式按深度不同 | quick 由主线程顺序查找和核实;standard / deep 在宿主支持时用独立子代理 |
| 托管平台只读 | 不创建或修改 PR/MR、评论和状态;describe 生成的描述由你自己复制粘贴 |
| 默认不改代码 | 只有你明确说「修掉」才改本地工作区 |
| 本地运行 | 「本地」指运行方式,仍会通过 Git、`gh` / `glab` 或配置的工具读取远端资料;不提供平台机器人或 CI 服务部署 |

## 三个技能

| 技能 | 做什么 | 产出 | 什么时候用 | 能否用自然语言触发 |
|---|---|---|---|---|
| `review` | 检查改动里的 bug、安全问题、需求遗漏和违反规则之处 | CR 报告(对话里显示一份,存档一份);可记录你对发现的判断 | 提测或合入前自查,审别人的 PR/MR | 可以,说「review 一下」 |
| `describe` | 按改动和需求材料写给评审人看的改动说明,只列事实,不判断对错 | `describe.md`(复制进 PR/MR 描述)和含出处的完整版 `describe.html` | 准备提 PR/MR 时;不依赖审查结果,没审过也能用 | 可以,说「写一下 PR 描述」 |
| `improve` | 联合维护代码规则和 `REVIEW.md`,整理用户要求、重复或冲突的规则,按需从历史评审归纳 | 维护草稿 `rules-draft.md` 和具体 diff;确认当前版本后才改工作区 | 补充或调整约束、整理已有规则、从历史评审归纳约定 | 不可以,只能用命令 |

## 使用

需要 `git`、`sh`、`python3`(3.8 以上),必须有本地克隆;读 PR/MR 信息要用已登录的 `gh` 或 `glab`,或者配置只读的 [`tools.code_host`](#配置)。

### 入口

| 技能 | Claude Code | Codex | Cursor |
|---|---|---|---|
| review | `/crosscheck:review`(拷贝安装时 `/review`) | `$crosscheck:review`(拷贝安装时 `$review`) | `/review` |
| describe | `/crosscheck:describe`(拷贝安装时 `/describe`) | `$crosscheck:describe`(拷贝安装时 `$describe`) | `/describe` |
| improve | `/crosscheck:improve`(拷贝安装时 `/improve`) | `$crosscheck:improve`(拷贝安装时 `$improve`) | `/improve` |

下文示例用 Claude Code 写法,其他宿主换成对应前缀。说「review 帮助」会列出常用说法。

### review:审查

```text
/crosscheck:review                                   审当前分支的全部改动(含未提交的)
/crosscheck:review 帮我看下 MR 128,这次是修复分页丢数据   审 MR,并告诉它改动目的
/crosscheck:review 上线前深度审一下 feature/pay 分支      指定分支和深度
```

报告开头写明实际范围、基准、深度、全量还是增量;范围不对时直接说。

<details>
<summary>更多说法</summary>

| 想做什么 | 可以这样说 |
|---|---|
| 审另一个分支(不用切过去) | 「看看 feature/pay 分支」 |
| 审一个 PR / MR | 「review MR 128」,或直接贴链接 |
| 审某几个提交 | 「看看最近 3 个提交」 |
| 只看某个目录 | 「只看 src/orders 下面的改动」 |
| 指定对比基准(只对这一次生效) | 「对比 develop」「要合到 release/1.2」「对比基准是 abc1234」 |
| 换个对比基准 | 「换个基准」(重新选择,默认仅本次) |
| 记住基准规则 | 「以后跟随每个 MR 的目标分支,记住」「以后固定对比 develop,记住」 |
| 快速过一遍 / 上线前仔细审 | 「快速看一下」/「仔细看看」 |
| 只关心某一方面 | 「只看安全问题」「只对照团队规范」 |
| 给方案、接口文档 | 「方案在 docs/plan.md」 |
| 给需求链接 | 「需求单是 <链接>」「这是 hotfix,没有需求单」 |
| 说明怎么验证的 | 「本地跑过 jest,手工测了翻页」 |
| 测试也一起看 | 「测试也一起看」 |
| 换一个核实用的模型 | 「核实换成某某模型」 |
| 从头完整审一遍 | 「重新审」「全量」 |
| 审完顺手修掉 | 「审完把问题修掉」 |
| 用自己的历史 bug 检验 | 「拿我们修过的 bug 测一下能不能抓到」 |

</details>

**审查之后**,直接回复,用报告里的编号指代发现:

| 想做什么 | 可以这样说 |
|---|---|
| 表态(会被记住) | 「1 对,2 误报:上游已经重试」「3 先不改:下个版本」 |
| 质疑某条 | 「第 2 条不对吧」(重新读代码核对,站不住就撤回并记录) |
| 修复 | 「把第 1、3 条修掉」 |
| 看没报的 | 「还有别的吗」「70 多分的那些是什么」 |
| 指出漏报 / 单独深挖 | 「你漏了 cache.ts 的并发问题」「cache.ts 用 deep 再看一遍」 |
| 当场定一条规则 | 「以后都要检查 X」「以后这种别报了」(先给你看,确认后只改工作区) |
| 改完再看 | 「再看一下」(同一目标自动做增量) |

**几个默认行为**

| 项目 | 默认 |
|---|---|
| 深度 | 当次指定 > 根目录 `REVIEW.md` 头部的 `depth` > standard。quick 最多报 5 条、不启动子代理;deep 多查一遍影响面(从调用方往回查),工作区就是被审版本时按需运行项目已有的检查和测试来复现 |
| 对比基准 | 当次指定 > 你明确保存的项目规则 > PR/MR 的目标分支 > 本地分支的切出来源(取 origin 上最新版)。确定不了才问,不猜;回答默认只对这一次生效,明确说「记住」才保存 |
| 基准不可用 | 没有共同祖先、基准分支缺失时停下说明原因,不当成「没有问题」;非交互运行用推荐基准并标「基准未确认」 |
| 已合入的 MR | 照样能审,审合入前的改动;要修请另开分支,`--fix` 不改代码 |
| 改动很大 | 按约 1000 行一批;超过 4 批先问你全审、只审某目录,还是用 quick |
| 改文件 | 没说「修掉」就不改任何文件;「这个该怎么修」不算修改请求 |

**报告怎么读**

- 开头:标题和首句概括发现与覆盖限制;没审完会明确写「审查未完成」。
- 概览表 + 逐条发现:每条写明严重程度、维度(`bug` / `security` / `intent` / `rules`)和确认方式(**已复现**、**读代码推断**等),附位置、问题代码、失败后果、修复建议和折叠的依据。
- 「需求核对」「规范」「测试与检查变更」「改动代码里的原有问题」「已按历史判断忽略」按需出现;后三类不计入发现数。
- 末尾:用了哪些规则文件、跳过多少文件、查了哪些维度、消耗(用时、角色数、token)和报告文件链接;覆盖受限时标「流程不完整」「覆盖可能受限」。
- 不报风格、命名、可读性、日志措辞和影响很小的性能优化;测试、生成代码、lock 文件、vendored 代码和 `REVIEW.md` 的 `skip` 路径整个跳过(测试被削弱另外扫描)。

各节细节见 [DESIGN.html 的「输出报告」](DESIGN.html#review-report),核实与评分见 [「结论核实」](DESIGN.html#verify)。

**需求和意图**:审查会把 MR 描述(本地分支用提交说明)、关联需求单、MR 上已有的人工讨论、你说的改动目的和给的文档汇总成「意图」,用来判断行为变化是不是有意的。有明确验收标准时报告多一节「需求核对」。意图不能让安全问题不报。你补充的意图和本地文档在同一审查目标内沿用 90 天,说「清除之前的意图」「不再使用之前的文档」即可清除。开始前问哪些资料由 `review.ask` 决定,基准和资料放在同一轮问完。详见 [「需求来源」](DESIGN.html#sources)。

### describe:写 PR/MR 描述

```text
/crosscheck:describe 帮我写一下这个分支的 PR 描述
/crosscheck:describe MR 128
/crosscheck:describe 需求单是 <链接>,本地跑过 jest,请评审人重点看 api/orders.ts:42 的兼容期
```

| 文件 | 内容 |
|---|---|
| `describe.md` | 复制进 PR/MR 描述:资料表(需求单、需求文档、设计稿)、人工填写的 CR 负责人、改了什么、主要影响、请重点看(你点名才有,最多 3 条)、怎么验证 |
| `describe.html` | 完整版:补充规模、分支、需求对照、代码块编号与出处、技术明细 |

- 只列事实和链接,不判断对错、风险或修复建议;「怎么验证」是模型起草的建议,不表示已执行。你主动说了执行情况才有「执行记录」。
- 开始前问哪些资料由 `describe.ask` 决定(不写时问需求单),每次生成都问一次;非交互运行不提问。
- 模板在 `skills/describe/templates/`,团队可以调整格式。生成后自己核对 `describe.md` 再粘贴。

各部分怎么生成见 [DESIGN.html 的「描述内容」](DESIGN.html#describe-output)。

### improve:规则维护

只能用命令调用,直接说明要维护什么即可:

| 命令 | 做什么 |
|---|---|
| `/crosscheck:improve` | 先问一次:本地维护,还是从历史 MR 归纳(默认最近 90 天)。没有 origin,或已知是非交互运行(说明无人在场或 `CR_NONINTERACTIVE=1`)时直接本地维护 |
| `/crosscheck:improve 整理现有规则` | 联合检查本地 rules 与 `REVIEW.md`,整理成维护草稿,不问、不拉取历史 MR |
| `/crosscheck:improve 补充一条:查询必须限制在调用方租户内` | 结合现有规则和代码整理要求、范围及例外,无需凑历史 MR 证据 |
| `/crosscheck:improve 这条规则和 REVIEW.md 冲突,一起整理` | 展示冲突和建议取舍,在同一份草稿中新增、修改、合并或删除相关内容 |
| `/crosscheck:improve 从最近 30 天的评审沉淀规则` | 只读采集已合并 MR,分批观察和全局归并,把候选编入同一维护草稿 |

统一流程是 **理解要求 → 联查规则 → 维护草稿 → 预演 diff → 用户确认 → 写入工作区**。

- rules 放代码约束和该规则的例外;`REVIEW.md` 放审查设置、严重程度、重点及通用误报边界。一次维护可以同时改两边,你不必先决定文件落点。
- `rules-draft.md` 是生成的预览,说明改什么、为什么、具体差异和待确认项。直接对话补充要求,agent 更新提案后重新生成草稿并预演,不要只改预览文件。不能把局部要求擅自扩大成全仓库规范。
- 确认绑定预演的版本。草稿变了或目标文件被修改后,旧确认不能继续写入,必须重新核对 diff。确认后只改工作区,不 `git add`、不提交。
- 用户要求与历史推断分别记录来源。历史评论按约束的条件、行为、范围和例外归并,不要求措辞相同,也不以两个 MR 作为唯一门槛。单 MR 已落实或有可核对的仓库惯例,也可以进入待确认;用户确认采用不等于历史已经证明团队共识。
- 采纳以该 MR 最终代码是否落实评论要求为主要依据,不要求作者回复。仓库惯例单独核对固定版本、独立实现和相反实现,不增加 MR 采纳数。代码不可读就保留未知,不推断未落实。
- 归纳同时核对工程收益、范围和例外。已有规范冲突进入维护建议,证据不足的有价值经验进入待观察,在统一草稿末尾的参考材料里每个主题一行,不当作待确认项;完整来源在本轮材料 `rules-materials.md`。复用本轮材料,不建永久观察库、不按次数自动晋升;不承诺召回所有有价值的建议。
- 历史候选证据保持简短:统计加至多 3 个典型 MR 的链接、原话与结论。完整材料保留供本机追问;对外说明不要求读者持有本机文件。详见 [「沉淀流程」](DESIGN.html#improve-flow) 和 [「维护草稿」](DESIGN.html#plan-md)。
- 写代码的工具怎么读到这些规则,见常见问题里的 [Codex](#codex-写代码时不读-agentsrules) 和 [Claude Code / Cursor](#claude-code-和-cursor-怎么接上-agentsrules)。

## 项目规则

规则就是仓库里的 Markdown,不需要编号或专门格式。**`REVIEW.md` 是可选的**,一条规则都没有也照常审查,只是没有「规范」一节。

| 来源 | 文件 |
|---|---|
| 审查约定(只调整审查行为) | `REVIEW.md`(根目录对全部改动生效,子目录只对该目录生效) |
| 代码规则(审查和写代码共用) | `.agents/rules/**/*.md`(按 frontmatter 的 `paths` / `globs` 生效;不写表示全仓库) |
| Claude Code | `CLAUDE.md`、`.claude/rules/*.md` |
| Codex / 通用 | `AGENTS.md`、`AGENTS.override.md` |
| Cursor | `.cursor/rules/*.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md`、`.github/instructions/*.instructions.md` |

- 只有范围覆盖本次改动文件的规则才生效;审 PR/MR、其他分支或提交范围时规则从 base 那一版读,PR 改不了审查用的规则;审自己当前分支时读工作区。
- 同一件事几层说法不同时,目录里的优先于根目录的,再优先于 `.agents/rules/` 和团队已有约定;同一层两条矛盾时都不报,改发一条「规则冲突」说明。
- 同一份规则经软链接或 `AGENTS.md` 链接出现多次时只读一次,优先按 `.agents/rules/` 的路径。

**一句话放哪,依次问三个问题**

1. 脚本能执行吗(跳过哪些路径、报几条、默认深度)?能 → 根目录 `REVIEW.md` 的头部。
2. 写代码的 agent 也要遵守吗(比如「数据库查询必须限定在调用方的租户内」)?要 → `.agents/rules/<主题>.md`,用 `paths` / `globs` 限定范围;规则自己的例外写进规则块的「不要报」字段。
3. 都不是 → `REVIEW.md` 正文的三节。

```markdown
---
skip: ["src/gen/**"]                 # 不审查的路径
limits: { minor: 3 }                 # 条数上限
depth: standard                      # 没说深度时的默认深度(quick / standard / deep)
---
## 严重程度

只有会导致行为错误、数据泄漏或无法回滚的问题才算重要及以上。

## 审查重点

- 这是支付回调:失败是重复入账或漏入账,不是日志不好看。

## 不要报

- 生成的 protobuf 代码里的命名问题。
```

- 「严重程度」管定级,「审查重点」管查找(写失败会变成什么样),「不要报」只压住审查器的通用直觉(「应该加重试」「应该补测试」这类),要写窄。
- 头部设置只认根目录 `REVIEW.md`;子目录的只有正文三节。任何规则都不能让插件跳过核实、降低 80 分门槛、自动改代码或 approve。
- 不该写的:linter 和 CI 能查的、泛泛的规范、大段背景、写给写代码 agent 的操作说明(放 `CLAUDE.md` / `AGENTS.md`)。从一两条开始,单个文件约 15 条以内。
- 模板见 `skills/improve/templates/REVIEW.md`,也可以用 `improve` 起草。好规则的标准和样例见 [DESIGN.html 的「好的规则」](DESIGN.html#good-rule)、[「REVIEW.md」](DESIGN.html#review-md)。

## 配置

两层,每行一个 `key = value`,`#` 开头是注释,值为空等于没配置:

- **插件里的 `config/defaults.conf`**:随插件分发,也是完整的配置模板,所有设置都在里面并默认注释掉。用自己的插件市场分发时,团队改一次,成员装上就生效。
- **个人的 `~/.crosscheck/config`**:逐项覆盖插件里的,可以把 `config/defaults.conf` 复制过去再改。

常用设置:

| 设置 | 作用 | 不写会怎样 |
|---|---|---|
| `tools.code_host` | 读 MR / PR 标题、描述、讨论、目标分支的**只读**工具。值是一句话提示词,写明用哪个 skill 或命令,附一个链接示例 | GitHub 用 `gh`,GitLab 用 `glab`;都认不出的平台只能审本地分支 |
| `tools.requirement` | 读需求单(标题、描述、验收标准)的工具,写法同上 | 不问、不读需求单,你主动给的链接只记下 |
| `tools.doc_reader` | 读 PRD、技术方案等文档链接的工具,写法同上 | 不问、不读文档 |
| `gitlab_hosts` | 按 GitLab 处理的域名,逗号分隔,可带子路径(如 `company.com/gitlab`) | 只认域名里带 gitlab 的;私有化 GitLab 域名不含 gitlab 时必填 |
| `model.finder` / `model.verifier` | 查找 / 核实用的模型,写当前宿主认识的模型名 | 用宿主当前的模型 |
| `review.ask` / `describe.ask` | 开始前要问的资料类别:`requirement`(需求单)、`doc`(需求文档),逗号分隔;要配了对应读取工具才会问 | review 问 `doc`,describe 问 `requirement` |

示例(取自 `config/defaults.conf`):

```text
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.example.com/browse/XX-123
gitlab_hosts = code.company.com,company.com/gitlab
review.ask = requirement, doc
```

- 值要写在一行里,不要出现「空格加 #」(行尾 ` # ...` 会被当成注释截掉)。
- 读取命令:`python3 "<插件目录>/scripts/config.py" get model.finder`;`path` 返回个人配置路径,`plugin-path` 返回团队配置路径;环境变量 `CROSSCHECK_PLUGIN_CONFIG` 可覆盖团队文件位置。
- `gitlab_hosts` 只用来识别平台和子路径,不是访问白名单,读哪个仓库只由当前仓库的 origin 决定。

## 记录存在哪

默认保存在**你本机**的 `~/.crosscheck/`,不在仓库里,也不共享给别人。明确选择不保存时,本轮材料改放临时目录:

```text
~/.crosscheck/
├── config                         个人配置
├── describe/<项目名>/<目标>/        describe.md、describe.html
├── mining/<项目名>/               规则沉淀的 MR 评论缓存和采集材料
├── maintenance/<项目名>/run-<随机值>/  本地维护的 rules-draft.md、rules-draft.json
└── reviews/<项目名>/
    ├── feedback.jsonl             你对发现的判断
    ├── project.json               这个项目的对比基准规则
    ├── sources/<分支>.json        需求链接、你补充的意图和文档(90 天)
    └── <对象>/<时间>/             report.md、findings.json、rejected.json、meta.txt
        latest                     最近一次
```

- `<项目名>` 取自 origin 地址的最后一段,同一项目的 worktree 和克隆共用记录;`<对象>` 是 `branch-<分支>`、`mr-<编号>`、`pr-<编号>` 或 `range-<范围>`。
- **增量**:同一目标再审时默认只看变了的文件,结果分成「新发现」「上次遗留」「已解决」。说「重新审」,或规则、审查设置、意图、模型、插件变了,或这次深度更高时,会全量重审。
- **判断记录**:记着那段代码的样子(问题行加上下各两行),代码没变就不再报,变了会重新评估并注明之前的判断。想让全队都不再报,写进规则的「不要报」字段或 `REVIEW.md` 的「不要报」。

存放细节见 [DESIGN.html 的「文件存放」](DESIGN.html#storage)。

## 常见问题

### 测试时可以不保存吗?

可以。**review、improve、describe 默认都保存**,只有明确说「本次不保存」「不留记录」才启用单次 `--no-store`。只说「测试一下」仍按默认保存。

```text
/crosscheck:review MR 128,本次不保存记录
/crosscheck:improve 整理现有规则,本次不留记录,只看草稿
/crosscheck:describe 本次不要保存记录
```

- 不更新插件的长期审查记录、需求来源、反馈、MR 缓存和描述作者信息;必要的分析文件、报告和草稿留在本轮临时目录,便于继续讨论。给出的文件链接是临时链接。
- 已有配置、历史和缓存仍可以只读使用。「不保存」不等于从零评测;review 要全量审查可以另外说「全量」。
- 保存分析记录与修改代码库分别决定。improve 可以在不保存分析记录时,按你对具体草稿的确认写入规则;「只预演」不会改仓库。
- 临时目录不可用后需要重新生成,不会把反馈套到旧的 `latest` 记录。此选项不控制宿主的对话存档、Git fetch 或项目测试自身产生的文件。

### Codex 下报告里没有 token 统计

报告末尾「消耗」写的是「token 未统计(hook 没有运行……)」,说明插件的 hook 没有执行。token 统计完全靠插件 hook(`hooks/codex-hooks.json` → `skills/review/scripts/guard.sh`)在审查开始和保存时读取会话记录;hook 不运行,审查本身照常,只是没有用量数字。

按报告里括号中的原因排查:

| 报告里的原因 | 含义 | 怎么办 |
|---|---|---|
| hook 没有运行:没有以插件方式安装,或在 Codex 里还没有用 /hooks 信任 | hook 一次都没执行 | 见下面的开启步骤 |
| hook 在运行,但审查开始时没拿到会话记录 | 保存时 hook 执行了,但审查开始时没有记下起点:宿主没给出会话记录路径,或审查进行到一半才信任 hook | 中途信任的,下一次审查即正常;审查结果不受影响 |
| hook 在运行,但会话记录里读不到用量 | 会话记录格式和插件读取的字段对不上 | 这些用量字段不在宿主正式文档里,宿主升级后可能失效 |

**开启步骤**

1. 确认是从 person-marketplace **以插件方式安装**的。手动拷贝的技能目录不会注册 hook。
2. 在 Codex CLI 里运行 `/hooks`,找到 crosscheck 的 hook,审核并信任。桌面端的入口(Review hooks 或 Settings > Hooks)以 Codex 官方文档为准。
3. **升级插件后要重新信任**。按 Codex 官方文档,信任绑定的是 hook 的哈希,脚本变了之前的信任就失效。
4. 仍然不行时检查 Codex 配置(以 Codex 官方 Hooks 文档为准):hooks 默认开启,配置里有 `[features] hooks = false`(`codex_hooks` 是已弃用的旧名)会整体关掉;管理员设置了 `allow_managed_hooks_only = true` 时,插件自带的 hook 会被整体跳过,需要找管理员。

Cursor 的会话记录里没有用量,不统计 token,这是预期行为。

### Codex 写代码时不读 `.agents/rules`

Codex 通过 `AGENTS.md` 获取项目指引,不会因为生成了 `.agents/rules/` 就自动读取其中内容。improve 写入任意 `.agents/rules` 规则时,会在维护草稿里自动带上 `AGENTS.md` 的一句固定提示,放在 `## 代码约束` 一节(没有就在末尾新建,`AGENTS.md` 不存在就新建):

```markdown
改代码前先查看 .agents/rules/ 下的规则文件:按文件头的 paths 判断是否适用于要改的文件,适用的读完并遵守。
```

只有这一句,不再逐条列规则;已有就不重复写。0.3.12 写入的逐条索引行和引导句会换成这一句,手写引用保留。预演里和其他文件一样显示 diff,确认后才写。这一句**只起指引作用**:Codex(以及读 `AGENTS.md` 的 Cursor)是否真的打开规则文件由模型决定,不是强制加载。审查时同一条规则不会因此读两遍,范围仍按规则文件自己的头部。

### Claude Code 和 Cursor 怎么接上 `.agents/rules`

Claude Code 靠 `.claude/rules/` 原生加载:improve 写入规则时,在草稿里为每个规则建 `.claude/rules/<相对路径>.md -> ../../.agents/rules/<相对路径>.md` 的相对符号链接(`.claude/` 不存在也创建),预演显示为 `LINK:`,你确认后与规则文件一起写入;删除规则时一并删掉指向它的链接(`UNLINK:`)。Claude Code 按规则头部的 `paths` 决定何时加载。已有同一指向的链接不变;位置上已有别的文件或链接,或 `.claude/rules` 本身是符号链接(例如共享规则目录)时不动,列为待确认。`CLAUDE.md` 不写任何东西;删除规则后 `AGENTS.md`、`CLAUDE.md` 里仍引用它的行只列待确认,不自动改。

Cursor 读 `AGENTS.md`,靠上面那句提示找到规则;不再建议 `.cursor/rules/*.mdc` 软链接。新建的规则文件头部 `paths` 和 `globs` 写同一串 glob。详见 [DESIGN.html 的「各工具怎么接上」](DESIGN.html#tool-hookup)。

### 报告为空,是不是没查?

不是。插件**精确率优先**:两条真实发现好过十条里错三条,报告为空是正当结果。查了什么看报告末尾(规则文件、跳过的文件、查过的维度、候选和通过的条数)。

被挡掉的候选存在本次记录目录的 `rejected.json`(`~/.crosscheck/reviews/<项目名>/<对象>/<时间>/`)。不用自己翻文件,直接说「还有别的吗」「70 多分的那些是什么」,会列出 70 分以上的候选、分数和没通过的理由;你确认是真问题的会被记为成立。

### 会不会改我的 PR/MR?

不会。review、describe、improve 对托管平台始终只读:不 push、不发评论、不改描述或标题、不 approve、不合入。只有 PR/MR 会 `git fetch` 一次对方的提交,fetch 只更新本地对象和 origin 跟踪引用。审查不切分支,出报告前会核对分支、HEAD、暂存区和改动文件,有变化写在报告最前面。本地文件只在你明确要求修复或确认规则写入时才改。

<details>
<summary>只读的边界</summary>

- 只读依靠流程说明和事后核对,不是强制的工具隔离或网络沙箱;hook 只统计 token,不拦截命令。
- 所有平台请求绑定当前仓库的 `origin`(含 Git `insteadOf` 重写);你给的 MR/PR 链接在请求前核对主机、端口和完整仓库路径,对不上就停止,不转向其他平台。没有 origin 仍可本地审查,但不读平台、不能沉淀。
- 支持 HTTPS、GitLab HTTP、SSH 和 SCP 风格地址(GitHub HTTP origin 不读平台)。SSH 别名与 Web 域名对不上、SSH 地址缺少 Web 子路径等无法唯一推导的情况会拒绝读平台。
- SKILL frontmatter 的 `allowed-tools` 只对少数 Git 读取命令免批准,不是禁止其他命令的白名单;Codex/Cursor 是否采用由宿主决定。
- `tools.code_host` 由 agent 执行,插件只能核对仓库、分支和提交,只读靠配置里的约定。

更多见 [DESIGN.html 的「安全边界」](DESIGN.html#security)。

</details>

### 明明登录了 gh / glab,却提示没登录

多半是登录靠 `GITLAB_TOKEN`、`GH_TOKEN` 这类环境变量,而宿主没把它传给命令:例如 Codex 的 `shell_environment_policy` 设为 `inherit = "core"` 会过滤掉 token,从 Dock 启动的宿主也读不到只写在 `~/.zshrc` 里的变量。agent 会先说明原因,再让你选:让 `gh` / `glab` 自己保存登录(推荐)、只放行这一个变量,或这次先不读平台。它不会打印 token,也不改宿主的全局配置。做法见 [code-host-auth.md](skills/review/references/code-host-auth.md)。在那之前会改用 git 直接拉代码,但拿不到标题、描述和关联需求。

### 连不上内网 GitLab / 私有化 GitLab 认不出来

连不上常见于沙箱环境,放开网络或换个环境再试,或改审本地分支。域名里不含 gitlab 的私有化部署,在配置里写 `gitlab_hosts`,部署在子路径下时写全(如 `company.com/gitlab`),并事先用 `glab` 登录对应主机。

### 想调阈值、角色关注点或报告格式

直接改对应文件(路径相对 `skills/review/`):评分和阈值在 `references/scoring-rubric.md`,不该报的在 `references/false-positives.md`,各角色关注点在 `roles/`,报告章节顺序在 `templates/report.md`(填写规则在 `references/report-format.md`)。improve 的模板在 `skills/improve/templates/`。跑测试:`python3 -m unittest discover -s tests`。对照表见 [DESIGN.html 的「想调什么,改哪里」](DESIGN.html#tuning)。

## 已知限制

- **会漏报**:80 分门槛会挡掉一部分真实但没能完整追踪到的问题;规则检查防得了编造,防不了漏看。
- **增量按文件判断**:没变的文件依赖本次改动的符号时会一起重看,但只按名字识别,配置、反射等间接影响可能漏掉;担心时说「重新审」。
- **影响面靠名字匹配**:依赖注入、反射、MyBatis XML、跨服务调用、再导出搜不到,同名方法会混在一起(标「同名歧义」)。
- **需求来源靠你配置的工具**:能否读到取决于工具和权限。
- **运行记录只防幻觉**:声称跑过的命令要有记录,但记录本身不防刻意伪造。
- **规则沉淀仍需人工复核**:不保证召回完整,引文校验不能证明语义正确;平台的 `resolved` 不等于采纳。
- **同时审查同一个目标**:默认保存的两次审查共用「进行中」标记,可能误报「上一次没有保存」;describe 采集不再写入这类审查状态。
- **真实环境验证不足**:私有化 GitLab 子路径部署、GitHub issue 和评论读取主要用模拟的 `glab` / `gh` 测过;`tools.code_host` 只用本地仓库模拟过。

完整列表和应对办法见 [DESIGN.html 的「已知限制」](DESIGN.html#limits)。

## 内部参数对照

给想直接调用脚本的人。日常使用不需要,上面的说法 agent 会自己转成这些参数(见 `skills/review/SKILL.md`)。

<details>
<summary>展开</summary>

| 说法 | 参数 |
|---|---|
| 「本次不保存」「不留记录」 | `--no-store`(三技能均支持;默认保存) |
| 「review 一下」 | 不带 target |
| 「看看 feature/pay 分支」 | `feature/pay` |
| 「review MR 128」,或直接贴链接 | `128`、`<URL>` |
| 「看看最近 3 个提交」 | `A..B` |
| 「只看 src/orders 下面的改动」 | `src/orders` |
| 「对比 develop」「要合到 release/1.2」 | `--base <分支>` |
| 「对比基准是 abc1234」「从 abc1234 开始比」 | `--base-sha <提交>` |
| 回答「对比基准」的问题(记成项目规则) | `--base-rule target`、`--base-rule branch:<分支>` |
| 「换个基准」 | `--ask-base` |
| 「快速看一下」 | `--depth quick` |
| 「仔细看看」「上线前深度审一下」 | `--depth deep` |
| 「只看安全问题」「只对照团队规范」 | `--only bug,security,rules` |
| 「这次是为了修复分页丢数据」(同一审查目标之后不说就沿用) | `--intent "…"`,`--intent ""` 清除 |
| 「方案在 docs/plan.md」「不再使用之前的文档」 | `--doc <文件>`(可多次),`--doc ""` 清除 |
| 「需求单是 <链接>」「这是 hotfix,没有需求单」 | `--source requirement=<链接>`、`requirement=none:hotfix` |
| 「测试也一起看」 | `--with-tests` |
| 「核实换成某某模型」 | `--verifier-model <模型>` |
| 「重新审」「全量」 | `--full` |
| 「审完把问题修掉」 | `--fix` |
| 指定平台 | `--platform github\|gitlab` |
| 非交互运行(不询问基准和缺的需求来源) | `--non-interactive` |
| 给需求文档链接 | `--source doc=<链接>`、`doc=skip` |

`describe` 的采集脚本是 `skills/describe/scripts/collect.sh`,参数同上的基准、`--intent`、`--doc`、`--source`、`--non-interactive`(总是全量,没有 `--depth`、`--only`);生成用 `describe.sh <目录> [--tested "…"] [--focus '<JSON>']`。`--platform` / `CR_PLATFORM` 只能补充未识别的平台类型,不能覆盖已识别的 origin 平台。

</details>
