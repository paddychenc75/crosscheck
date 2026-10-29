# Changelog

## [0.3.0] - 2026-10-29

一次审查产出一份给开发者自己修的 CR 报告。依据是团队问卷:52% 的人卡在「改动太大、难以判断影响」,15% 缺需求背景,15% 缺验证手段(见 DESIGN.html)。

**PR/MR 描述拆成独立的 `/describe`**

- review 只做审查,产出 CR 报告,不再带描述;PR / MR 描述由独立技能 `describe` 生成(`/crosscheck:describe`,拷贝安装时 `/describe`)。原先随 review 的 MR 说明生成脚本、模板与 `analyst` 角色已经移除,在 `skills/describe/` 下改写为 `describe.py`、`describe.sh`、`describe.md`、`describe.html`、`analysis_check.py` 和 `roles/analyst.md`,对外统一叫「PR/MR 描述」。
- **新模板:影响面在前,怎么验证由代码推断**:`describe.md` 依次是一句话和基本信息、影响面、请重点看(仅作者用 `--focus` 点名才有)、怎么验证、需求对照(仅需求单里有验收标准才有),正文上限 20 行(不含首尾标记及负责人固定填写区)。影响面第一行是总览(删除、迁移、接口、配置、函数签名变化的数量),下面按接口、数据库与迁移、配置环境变量与依赖、内部函数分层,每层最多 2 项,有迁移、删除对外接口或配置键、依赖变化时写「回滚方式:待补充」,没发现就写「未发现」、不写「无影响」。怎么验证整段由脚本从代码推出,标注「由代码推断,未执行」:触发方式(路由、命令行、定时任务、事件、配置开关和 diff 外的调用方,带 file:line)、预期(只引用验收标准原文或新增测试的用例名和断言,没有出处不写)、怎么跑(`package.json` 的 `scripts.test` 或 Makefile 的 `test` 目标,标注未确认执行)、执行记录(作者给了 `--tested` 就原样引用,否则「未验证(无执行记录)」,不问「怎么验证的」)。入口识别在新增的 `skills/describe/scripts/entrypoints.py`。去掉阅读顺序、可跳过、需注意、审查量和自动列出的「可能值得重点看」,analyst 只保留一句话和需求对照,不再分组。
- **只在本地生成,不发布**:产出 `describe.md`(正文 20 行以内,不含首尾标记及负责人固定填写区)和 `describe.html`,写在 `~/.crosscheck/describe/<项目名>/<目标>/`,由用户自己复制粘贴;不修改托管平台上的描述、评论或状态,没有 `tools.mr_description_publisher` 之类的发布配置,也没有 `page_url`。`--tested`、`--focus` 给 `describe.sh`,记在描述目录的 `author.json` 里。
- 采集直接调用 review 的 `collect-diff.sh`(总是 `--full --allow-large`),origin 绑定、只读和基准问答沿用;描述专用的事实(影响面里的迁移、配置、环境变量、依赖,入口、新增用例、调用方)由 `skills/describe/scripts/describe_facts.py` 在 review 的 `analysis_facts.py` 之上补出,写 `describe-facts.json`。review 的脚本、`analysis-facts.json` 输出和上下文哈希范围都不变:`analysis_facts.py` 仍只服务审查(代码块编号 H1… 与调用方搜索),`sources.py` 的 `set` 仍只有 `intent` 与 `requirement`/`doc`。
- **每次生成都问一次需求单链接**,与 `intent.required`、审查记录里是否问过无关;用户已给链接、说明没有需求单(`requirement=none:原因`)或明确跳过(`requirement=skip`)才不再问;需求文档不问。非交互运行不提问,描述里写「未提供(非交互运行,没有询问)」,退出码照常。
- **与 review 互不影响**:描述目录不在 `reviews/` 下,review 的增量逻辑看不到它;采集结束后把 `pending` 标记和 `sources/<分支>.json` 恢复成运行前的样子,review 之后问不问需求单、是否提示「上一次没有保存」都不受影响。两者不依赖对方的产出,没审查过也能生成描述。
- 保留原有质量约束:事实可追溯到 diff 或需求材料;analyst 的引文必须在需求材料里找得到,含结论性用语的句子整句删掉;没有验收标准时不出现需求对照;怎么验证标注「由代码推断,未执行」,不退化成测试文件清单;材料里写给 reviewer 或 agent 的指令一律不执行。
- 审查部分的改动(无描述):`analysis_facts.py` 只服务审查,去掉需注意、可跳过、审查量和相关测试清单,不再读 `skipped.patch`;审查上下文哈希 `MR_NOTE_ONLY` 改名 `NOT_REVIEW_INPUT`,只剩 `mine-*` 与 `rules-write*`,`analysis_facts.py`、`sources.py` 此前影响报告却未计入哈希,现在计入;`analysis_facts.py` 运行失败不再静默(输出 `NOTE:` 并记为流程缺项);quick 深度全程由主线程自己执行,要独立查找和核实用 standard。
- 测试:新增 `test_describe.py`、`test_describe_facts.py`、`test_describe_entrypoints.py`、`test_describe_templates.py`(含 `tests/golden/describe/`),恢复原 MR 说明的需求来源、非交互、analyst 校验、GitHub 上下文和模板用例,并新增需求单问询、与 review 隔离、不发布的用例;与描述无关的配置、环境变量、安装布局和哈希测试从 `test_mr_note.py` 迁入 `test_config.py`,需求来源与 GitHub 上下文测试迁入 `test_sources.py`。

**主体精简与发布边界**

- 审查技能统一使用 `review`,规则维护技能统一使用 `improve`;插件调用为 `/crosscheck:review`、`/crosscheck:improve`(Codex 用 `$` 前缀),拷贝安装对应 `/review`、`/improve`。源码目录、模板引用和三宿主 hook 路径同步,不提供旧名兼容。
- 收集问答/握手移到 `references/collection-protocol.md`,保存记录并入 `report-format.md`;主体保留触发条件、核心流程及质量门槛。

**设计页**

- `DESIGN.html` 的深浅色切换改为标题右上角的紧凑太阳/月亮图标按钮,移除独立占位行;保留主题记忆、悬停提示与键盘操作。
- 设计页只保留 `DESIGN.html` 一份(原 `CR-DESIGN.html` 改名,`DESIGN.md` 删除并把没覆盖的内容并入,新增「已知限制」一节);README 只讲用法。审查角色改成一张表;「CR 规则」一节补了好规则的标准和样例,以及沉淀规则、整理 `REVIEW.md` 两套动画演示。
- 流程图的候选连线只从查找组进入 verifier。执行日志桌面填满卡片剩余高度,移除固定高度导致的底部空白;窄屏按内容展开,折叠仅保留标题。纠正缓存成本、厂商角色数量及 80 分阈值的依据,不新增 reviewer 或投票。

**托管目标与命令约束**

- 审查、历史评论沉淀、辅助 API 使用统一 origin 解析;应用 Git URL 重写,平台调用显式绑定主机与项目,链接主机/仓库不匹配在网络请求前停止。code_host 的 request/resolve 均校验,失败只允许同 origin 的 Git 回退。
- 保留 HTTP(S) 协议、端口及 GitLab 部署子路径,SSH 传输端口不误作 API 端口;无 origin 允许本地审查,停止平台读取。gitlab_hosts 仅识别 GitLab/子路径。
- review/improve frontmatter 仅给具体 Git 读取命令免批准授权,正文限定远端只读并保留明确授权的本地 fix/apply;不新增 hook 或全局权限,不宣称强制工具隔离或网络沙箱。

**审查结果可靠性**

- 修复 GitHub 过期行内评论使用 `original_line` 时取错代码版本的问题:配套使用 `original_commit_id`,缺少对应提交时保留未知,避免误判评论后的代码改动。
- 明确 standard、全量和全部改动之间的参数转换,避免自行升级深度。
- 提交范围的 `--fix` 限制移到修复入口:即使范围的 head 就是当前 HEAD,也不修改工作区,只给修复建议。
- 运行记录与复现结论分开:只有记录、`reproduced` 和实际观察说明齐备才标已复现;`refuted` 否决该场景,`inconclusive` 保留日志且不冒称复现,可凭充分代码证据判断。80 分为操作性证据评分,不是正确概率。
- 历史不修决定与事实置信度分开保存,抑制前核对当前场景、调用环境与前提;本轮确认成立的发现不因过去判为不成立而直接消失,已抑制不算已解决。
- 原始候选复用 `finders/` 保存,增加来源映射与合并理由,补回未映射候选及原始场景/待核项;自动去重仅接受关键字段等价,不再靠附近位置和相似摘要判断同根因。保存候选、历史和运行记录以便追查。
- reviewer 回复同时声明覆盖情况;每批 15 条是查找预算,达到时通过现有 INCOMPLETE 标记说明覆盖可能受限,不编造未交条数,与展示上限分开。CR 报告及本地记录保留限制。

**模板**

- 每个 skill 自带自己用的模板:`skills/review/templates/` 放 `report.md`(CR 报告的骨架),`skills/improve/templates/` 放 `REVIEW.md`(从 review 挪过来)和 `rule.md`(一条规则的样例);`report-format.md` 只留填写规则(DESIGN.html)。

**需求来源**

- 配置按工具的用途写一句话说明(用什么工具、怎么用,原样交给 agent):`tools.requirement`(需求管理,读需求单)、`tools.doc_reader`(文档阅读,读 PRD、技术方案等),值写成一句给 agent 的提示词(链接识别取自其中网址的域名);`intent.required` 写每次都要收集的类别(`requirement`、`doc`)。缺了在审查开始前问一次(贴链接 / 没有需求单 / 跳过),同一分支记住,90 天过期;agent 用配置的工具读取内容并入意图。
- 新增 `--source`、`--source-dir`、`--doc`(可多次,`--plan` 同义)。
- GitHub PR 读取关联 issue(含验收标准)和 PR 评论、review,与 GitLab 对齐。

**代码托管工具与对比基准**

- 新增可选配置 `tools.code_host`:一句给 agent 的提示词,说明用哪个只读工具读 MR / PR。配置后链接形式的 MR / PR(GitHub、GitLab 的链接也一样)由它读取标题、描述、讨论、目标分支和源引用,写成固定格式的 `code-host.json`(两段式:`NEED_CODE_HOST` → `--code-host-dir`)。编号、仓库、平台、网址从链接解析,仓库要和 origin 对得上,分支名过 `git check-ref-format`,head 以只读 fetch 到的为准(和 agent 给的不一致就不用),基准由脚本算,不取 agent 的;讨论作者只存哈希、去掉机器人,标 `source=code_host`,其「已答复」「已解决」不让发现不报。核对不过或 `status=failed` 时退回 git 拉取,并在 NOTE 和报告里写明「MR 信息没有读到」。不写时照旧用 gh / glab(决策 26)。
- 对比基准默认一律用 origin 上最新的目标分支:所有路径(当前分支、其他分支、git 回退、`tools.code_host`)先拉取再算 merge-base,拉取失败才用本地已有的并输出 NOTE;报告的 SCOPE 行写明基准来自远端最新、本地缓存、平台给出还是用户指定(决策 21)。
- 基准能自动确定就直接审,不确定才问(DESIGN.html):当次明确选择优先,其次是明确保存的项目规则;PR/MR 使用已知目标分支,本地用分支创建 reflog 及必要的同一时刻 HEAD 切换记录识别来源,验证祖先关系后取远端最新版本。不用 upstream 或最近分支猜来源,记录缺失/歧义才 `BASE_UNSET`。默认仅本次,询问时提示是否记住;只有明确 `--base-rule target|branch:<分支>` 才保存,自动识别不保存。新增 `--base-target` 表示本次使用目标/默认分支;`--ask-base` 重新选择默认不改旧记录。无共同祖先、分支缺失或无法推断合入前版本时 `BASE_FAILED` 非零停止并给恢复指引;非交互无法确认来源时标「基准未确认」,计算失败仍停止。
- 基准、必填的需求来源、可选的需求资料(默认的需求文档:PRD、设计文档链接,新增提示行 `SOURCES_OPTIONAL:`)放在同一轮问完;可选的可以跳过、不算缺失,同一分支跳过后不再问;什么都不需要问时不打断用户。
- 新增 `--base-sha <提交>`(「对比基准是 abc1234」):短哈希也行,必须是 head 的祖先,优先于任何自动计算,对本地分支、提交范围、PR/MR 都生效;它变了算作全量重审。
- 已合入的 MR / PR 能正确审查:用 merge commit 合入时在目标分支的 first-parent 链上找到合入提交,用它的第一个父提交算基准(`crlib.pick_base`),squash / rebase 合入照常;找不到合入提交时输出 BASE_FAILED 并停止,不给出空 diff。报告开头写明「这个 MR 已合入」并提示描述已无法影响合入,`--fix` 不改代码。MR 的 diff 为空时一律输出 NOTE,不当成「0 条发现」。

**配置**

- 团队配置迁至插件级 `config/defaults.conf`,唯一解析器迁至 `scripts/config.py`,`scripts/config.sh` 为薄转发;review/improve 共用入口。保留 `CROSSCHECK_HOME`、`CROSSCHECK_CONFIG`、`CROSSCHECK_PLUGIN_CONFIG` 的覆盖语义;增量指纹包含共享实现、默认模板和实际配置文件。安装指南与复制测试夹具保留完整共享布局。
- 配置键改名:按外部工具的用途分为 `tools.*`(需求管理、文档阅读)和 `intent.required`,`--source` 的类别为 `requirement` 和 `doc`。
- `config/defaults.conf` 成为唯一的完整模板:所有设置都列出、默认全部注释掉,个人配置 `~/.crosscheck/config` 格式相同、可直接复制过去;`templates/config.example` 删除。值为空的行视为没配置。
- README 和常用说法改成自然语言的用法,脚本参数只留在 `SKILL.md` 和 README 末尾的「内部参数对照」。

**收敛**

- 去掉 `stats.sh` 汇总统计:判断记录照常保存。
- 去掉 simplifier 角色和 `--only simplify`,查找角色只剩 `reviewer` 与 `rules-auditor`。
- 去掉自动打开报告(`CROSSCHECK_OPEN*`)。
- 去掉借用登录 shell 重试 glab(`CR_USER_SHELL`),保留变量写在哪个文件的提示和 git 兜底。
- reviewer 改成在同一个上下文里分遍查:正确性 → 安全 → 需求核对,deep 加影响面(不再单独启动影响面 reviewer)。安全一遍默认不报只导致服务不可用的问题、缺少限流、开放重定向和只在测试代码里的问题。同一上下文可复用已读材料;不同子代理的相同前缀也可能命中缓存,实际成本取决于输入、缓存和输出,不按角色数直接推算;规则仍由 rules-auditor 单独查。
- verifier 每批最多 3 条,critical 单独一批。

**核实**

- 每条发现区分「已复现 / 已执行·结果未定(读代码判断) / 读代码推断」:verifier 的运行必须经 `run-check.sh` 留下记录(命令、退出码、测试数、日志);复现还需实际观察与场景对应,声称运行但没有记录的按读代码推断处理、最高 60 分,报告展示的输出取自运行日志。

**规则**

- 新增规则维护命令 `improve`,管三件事:沉淀、检查现有规则、起草或整理根目录 `REVIEW.md`(原来 review 里的「检查一下我们的规则」并入这里)。沉淀从最近一个季度已合并 MR 的人工评论和 crosscheck 判断记录里沉淀规则。脚本核对证据(原文摘录、至少 2 个 MR、评论后确实改了代码或讨论已解决)、写法(必须/禁止、严重级别、不要报边界)和去重,按出现次数和评审人数排序列出候选和准入检查结果;选中的写进 `.agents/rules/<主题>.md`。只能用命令调用(`/crosscheck:improve`、`$improve`),不由自然语言触发;只有 review 可以自然语言触发。替换了原来靠 agent 手动总结规则的做法。
- 审查规则来源新增 `.agents/rules/`(frontmatter `paths` 或 `globs` 定范围,支持「不要报」一节)。
- `improve` 沉淀改成先生成 `plan.md` 计划:每条候选有「去向」(`.agents/rules/<主题>.md`,或某目录 `REVIEW.md` 的「每次都要检查」「不要报」)和「动作」(默认待定);你和 agent 对话修改,`rules-write.py --check` 预演,确定后 `--apply` 才写入,任一块有错整批不写;脚本对 `REVIEW.md` 只在指定章节追加一行并带来源注释。删除 `--pick`、`--edit`。`REVIEW.md` 模板按官方写法收紧,放哪按数量和范围分(核心短清单进 `REVIEW.md`,成批、按路径限定的进 `.agents/rules`)。
- `improve` 不带参数先列出三件事让你选,说「沉淀」或带时间范围才开始;只能用命令调用,不由自然语言触发。当场定的规则写进对应目录的 `REVIEW.md`(先查重、带来源注释、范围写窄),批量沉淀的写进 `.agents/rules/<主题>.md`;沉淀的准入清单改为候选自检必须填全、判断记录结论要与类别一致,写入时去重、只往带标记的文件追加。根目录 `REVIEW.md` 头部没写完整时会提示设置没有生效。
- 规则维护的「整理 REVIEW.md」只处理 `skip`、`limits` 两项头部设置。

**只读、流程完整性和模型**

- 只读不再靠 hook 事前拦截:hook 只统计 token,不拒绝任何命令或编辑。只读靠 `SKILL.md` 的「只读约束」和 `references/common.md` 的说明,出报告前 `check-state.sh` 核对仓库状态。去掉 `guard-paths.py`;报告里的 `GUARD` 行和 `.guard-active` 标记一并去掉。hook 注册的事件和工具名只留统计需要的。
- 流程完整性:查找角色的回复存成 `finders/<角色>[-<批次>].json`,`completeness.py` 检查该有的回复、以及每条候选的核实结果是否都在。缺了报告里写「流程不完整:…」,不阻止汇总,也不补写文件。`save-review.sh` 的角色数从文件数算,`--finders` / `--verifiers` 不再使用。
- 角色说明和 references 去重,每条规则只在一处写全:`common.md` 管只读和怎么读代码,`finding-schema.md` 管字段、严重程度和合并,`scoring-rubric.md` 管评分,`false-positives.md` 管误报。
- 推荐配置改为查找也用强模型:Claude Code 里 `model.finder=opus`、`model.verifier=opus`,成本比便宜的组合高;想省钱把查找换成 `sonnet`。Codex 用 sol 一类的强模型。评测里查找换成强模型后,查找阶段提出的高危问题明显增多。

**评测后的修复**

- 用公开 PR 做了三轮评测(8 个有已知 bug 的 PR 加 4 个干净对照)。第二轮发现宿主模型常常跳过子代理、自己读一遍就出结论,也不保存记录;SKILL.md 改为「流程不能省」:standard 和 deep 只要宿主能启动子代理就必须启动,改动只有一两行也一样;保存记录不能跳过,除非用户明确说不保存。
- 子代理回复里说自己还有后台任务的,算没有返回;非交互运行能选时用前台方式启动子代理,避免会话提前结束、没有报告。
- 0 条发现的报告也用统一结构,不写没经过核实的判断。
- reviewer 只负责找、不负责判:不再读误报清单,拿不准是否触发、base 是否已有、别处是否有守卫的照常提交,交给 verifier。评测里查找阶段漏掉的比核实否决的多得多。
- reviewer 对每块有效改动过一遍检查点:去掉或缩小的保护、读-改-写并发、包装层绕回自己、违反写明的约定、新实现处理空输入不一致、搬移或重命名时丢掉的行为。
- 修复宿主把命令写成 `{ sh save-review.sh …; } 2>&1 | grep …` 时 hook 认不出来、token 没统计。
- 修复 `log/`、`logs/` 目录里的源代码被当成日志跳过(如 Go 的 `pkg/plugins/log/`),现在只跳过其中不是源代码的文件。
- 评分标准新增例外:本次改动破坏了接口写明的约定或去掉了原有保护(锁、事务、过滤条件、校验、await)的,不按「现在没人这样调用」否决,严重程度最高「重要」;reviewer 也要提交这类倒退。
- 讨论里已经有人提出、代码仍没改的发现保留下来,报告里标「讨论中已有人提出」(`raised_in_discussion`)。
- 新增 `--non-interactive`(或环境变量 `CR_NONINTERACTIVE=1`):不问需求来源,也不记「没问过」为跳过。
- 恢复 Codex 的 token 统计(含子代理);修复核对仓库状态时删掉审查标记、导致 token 没统计的问题。
- 上次的发现重新核实时以这次的结果为准,不成立的记为已解决。
- 修复仓库改名或转移后链接指向新地址:输入是网址时,`meta.txt` 的 `url` 以用户给的地址为准(GitHub 和 GitLab),不再指向平台返回的新名字。
- 同一根因、同一改法出现在多处时只报一条,其余位置写进新字段 `also_at`,报告的「其他位置」列出;verifier 逐处核对,不在改动行上的位置由 `aggregate.py` 去掉。
- 严重程度按后果类型定义(数据丢失或错误写入、崩溃或未处理的异常、约定违反导致现有调用方崩溃、安全边界被破坏属于严重),verifier 不再因影响范围窄或后果尚未出现而降级;「破坏约定」的上限只适用于现有调用方还没出错的情况。

**流程可靠性**

- 进行中标记:`collect-diff.sh` 真正开始审查时在记录目录写 `pending`,`save-review.sh` 保存成功后删除;下次审查同一目标时发现标记还在,输出 `NOTE`,提示上一次没有保存、很可能提前结束,这次照常重新审查。问需求来源、对比基准、改动过大的那几次不写标记。
- `SKILL.md` 要求非交互运行(`claude -p`、`codex exec`、CI)前台启动子代理,等每个子代理的最终 JSON 都回来再进入下一步;子代理全部返回之前不写报告、不保存记录。
- 实际模型:`usage.py` 从会话记录里读实际出现的模型名(Claude Code 读回复里的 `model`,Codex 读 `turn_context`;只取名字),写成 `usage_models`;消耗行新增「实际模型」。配置的 `model.finder` / `model.verifier` 没有出现在记录里时(不区分大小写,按子串对),报告末尾加一行「模型与配置不一致」,`save-review.sh` 同时输出 `model_note=`。

**核实口径**

- `aggregate.py` 合并同一根因的重复发现:位置有交集,或同一文件里相距 5 行以内,且摘要相似度不低于 0.85 才合并,留下分高的一条,另一条的位置并进 `also_at`。宁可漏合并也不误合并;规则发现不走这里。
- 原有问题(`pre_existing`):verifier 核实缺陷真实存在、但 base 里已以同样结果存在的,标 `pre_existing: true`,不再打 0 分;过了 80 分门槛后单列在报告「改动代码里的原有问题」一节(`report-lines.sh` 的 `PREEXIST:` 行),不计入发现数、不进概览表。被本次改动变得更容易触发或范围变大的,仍算本次引入。借鉴 Anthropic 内部代码审查实践里的 Pre-existing 标签。
- 严重程度统一按后果类型定级:正常输入稳定触发的 500 或未处理异常是严重;`finding-schema.md` 加了几条虚构的对照例子;措辞轻重、个人觉得不严重也不是降级的理由,verifier 只在后果类型判断错时调整级别。
- `REVIEW.md` 模板新增「出问题长什么样」一节,由代码负责人写这块代码失败时是什么样(文档写错事实、配置接错路由或开关、迁移不可回滚),只用来说明后果类型,不用于挑风格、命名上的小毛病。`improve` 起草或整理 `REVIEW.md` 时会提醒补这一节。借鉴 Anthropic 文章里 REVIEW.md 写「what failure looks like」的做法。

## [0.1.6] - 2026-10-17

根据第一批使用反馈调整。

- 人对发现的判断改为项目级记录(`feedback.sh`):不成立、成立但不修、成立、有争议。对项目的所有分支、MR 和范围都有效;只需要发现的 id,不需要知道保存目录。判为不成立或不修时必须写原因;记录失败会明确告知,不再退回手动删除。
- 历史判断交给 verifier 参考,不在核实前删除候选:同一个问题且代码原样未变的不再报,代码变了的重新评估并注明之前的判断。因此没报的发现在报告里折叠列出,可以核对。
- agent 自己撤回的发现也要记录;用 `--fix` 修好的自动记为成立。
- 统计拆成三个维度:人的判断、代码层面的去向、置信度校准,互不折算;"代码已改"不再算作"已解决",成立率只按明确判断过的发现计算并标出样本数。
- 规范违规单独成节、单独计上限(默认 5 条),同一规则同一文件只报一条;现有代码大量反着写的规则提示"可能过时";有针对同一规则的抑制注释的地方不报。严重程度默认轻微,`REVIEW.md` 指定的优先。
- 修复会改变函数签名、公共行为、默认值或数据格式时,修复建议附带兼容性说明;优先选择不改变接口的修法。
- 没有 hook 时 token 显示为"未统计",不会去推断或读取其他会话的记录。
- `dismiss.sh` 保留,等同于 `feedback.sh invalid`。

## [0.1.5] - 2026-10-10

- 只审有效代码:默认跳过测试文件、测试数据、日志和各类产物(覆盖率报告、备份文件、IDE 配置等),`--with-tests` 可以把测试纳入审查。注释和排版、原样移动的代码、被注释掉的代码、没有被用上的代码不再按缺陷报告。
- 提高召回:查找角色改为放开查找,可以提交还差一环没确认的候选,由 verifier 核对;核实的标准不变。
- `--deep` 把改动按不同顺序多查几遍,verifier 会尝试写最小的脚本复现 bug。
- 本地分支审查时,把这段改动的提交说明作为意图提供给审查,对照"说了要做什么"和"实际做了什么"。
- 新增审查效果统计(`stats.sh`):每个维度报告了多少、被解决了多少、被否定了多少、还有多少没处理,以及长期没人处理的发现。
- 被否定的发现改为记录下来(`dismiss.sh`),不再直接删除;每条发现有固定的 `id`。
- 记录每次审查的成本:用时、启动的角色数、候选数和通过数;以插件方式安装时,由 hook 从宿主的会话记录里统计 token 用量(Claude Code 含子代理,Codex 和 Cursor 尽力而为)。报告末尾新增"消耗"一行,统计里新增按深度的平均用时和平均 token。

## [0.1.4] - 2026-10-10

- 只读拦截收紧生效范围,减少误伤:
  - 只在真正执行收集改动的脚本时才开始拦截,查看、搜索、编辑该脚本不再触发。
  - 收集改动失败、没有可审查的内容或改动没有变化时立刻解除,不再等到下一条消息或超时。
  - 凭证相关的拦截只针对主目录下的文件和 gh/glab 的配置;仓库里同名的文件(如 `.zshrc`、`.env.example`、`hosts.yml`)可以正常读取。
  - `gh api graphql` 的只读查询不再被当作写请求。

## [0.1.3] - 2026-10-09

- 修改README

## [0.1.2] - 2026-10-09

- 报告结构调整:开头增加概览表;每条发现包含位置、带行号的问题代码原文、问题说明和修复建议,修复建议在能确定时以 diff 形式给出。
- 新增 `snippet.sh`,从被审查的那一版代码里摘取问题代码,保证与原文一致。
- verifier 会一并检查修复建议里的代码。
- 没有 `gh` / `glab` 或未登录时,改用 git 直接拉取 PR/MR 的代码,使用仓库自己配置的凭证;新增 `--base` 指定这种情况下的目标分支。
- 新增 `--repo`,可以从仓库目录之外运行;不在仓库里时的报错会说明该怎么做。
- 凭证保护:脚本显示和保存的远程地址一律去掉账号和凭证;要求 agent 不读取、不输出凭证;审查期间拦截会显示凭证的操作(打印环境变量、查看或 `source` shell 配置文件、读取凭证文件、`git remote -v`、`gh auth token` 等)。
- 识别"登录配置写在 `~/.zshrc`、但当前终端没有加载"的情况并给出提示,不再误报为没有登录;识别到时通过用户的登录 shell 重试一次 `glab`,输出全部丢弃。`CR_USER_SHELL=0` 可关闭。
- 拉取失败时说明可能的原因(编号、权限、网络或沙箱限制),不再输出 git 的原始报错。
- 报告的位置以保存脚本给出的链接为准,不再另行复制。

## [0.1.1] - 2026-10-09

- 审查记录的目录改为直接用项目名(`~/.crosscheck/reviews/<项目名>/<目标>/`),不再带路径哈希。同一个项目的各个 worktree、不同位置的克隆共用一份记录,分支和 MR 的记录放在一起。
- 项目名取自 `origin` 远程地址,没有远程时取主工作区的目录名;不同项目重名时后来者加哈希区分。
- 旧目录(`<目录名>-<哈希>`)会在下次审查时自动挪到新位置。
- 审查结束后自动用系统默认程序打开保存的报告,报告末尾给出可点击的 `file://` 链接。`CROSSCHECK_OPEN=0` 关闭自动打开,`CROSSCHECK_OPEN_CMD` 指定打开用的程序。第一次自动打开时会提示怎么更换默认的打开程序。
- 改动没有变化时,给出上次报告的链接。

## [0.1.0] - 2026-10-09

首个公开版本。

- 审查流程:`bug-hunter`、`security-reviewer`、`rules-auditor`、`simplifier` 并行查找候选,再由没看过推理过程的 `verifier` 独立核实并打分,低于 80 分的不展示。
- 审查目标:当前分支(含未提交和未跟踪的改动)、分支名或其他 ref(不需要检出)、commit 范围、路径、GitHub PR 和 GitLab MR 的编号或 URL。
- 深度档位:`--quick`、默认的 standard、`--deep`;`--only` 限定审查维度。
- 大改动分批:改动超过约 1000 行时按路径顺序切成批次分别审查,用 `CR_BATCH_LINES` 调整每批大小。
- 项目规则:读取各 agent 的约定文件(`CLAUDE.md`、`AGENTS.md`、Cursor rules、Copilot instructions 等),按作用域筛选并去重。
- `REVIEW.md` 审查设置:项目可以调整跳过的路径或类别、严重程度的定义、条数上限和证据要求。
- `.cr/ignore.md`:记录不想再被提醒的模式。
- 审查记录:每次审查的报告和发现保存到 `~/.crosscheck/reviews/`,用 `CROSSCHECK_HOME` 修改位置。
- 增量审查:同一目标再次审查时只看 diff 发生变化的文件,报告分为新发现、上次遗留、已解决三段;`--full` 全量重审。
- `--fix`:只在用户明确要求时把通过核实的发现改到工作区,不暂存、不提交。
- 只读保障:审查期间拦截改变仓库状态的 git / gh / glab 命令和对仓库内文件的编辑;出报告前核对仓库状态。
- 多宿主:同一套 skill 可在 Claude Code、Cursor、Codex 中运行。
- 安装:仓库自身可作为 Claude Code 的安装源。
