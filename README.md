# crosscheck

高信噪比的 AI code review。同一套流程可在 Claude Code、Cursor、Codex 中运行。

核心思路:**宁缺毋滥**。多个专项 reviewer 并行找问题,每条候选再由一个没看过推理过程的 verifier 独立核实并打分,低于 80 分的不展示。没有问题时就说没有问题。

## 安装

**Claude Code**

```
/plugin install crosscheck --marketplace paddychenc75/crosscheck
```

需要 Claude Code 2.1.275 或更高版本。更早的版本分两步,在终端里执行:

```
claude plugin marketplace add paddychenc75/crosscheck
claude plugin install crosscheck@crosscheck
```

想改着用的话,克隆后直接加载目录:

```
git clone https://github.com/paddychenc75/crosscheck.git
claude --plugin-dir ./crosscheck
```

**Cursor、Codex 和其他支持 SKILL.md 的 agent**

`skills/cr/` 是自包含的 skill 目录。克隆仓库后,把它复制或软链接到该 agent 的 skills 目录,例如项目内的 `.cursor/skills/cr/` 或 `.agents/skills/cr/`。这样装只有审查流程,没有只读拦截的 hook(核对仍然有效)。

通过 Cursor 和 Codex 各自的插件机制安装本仓库的方式还没有验证过。

## 用法

| 宿主 | 调用 |
|---|---|
| Claude Code | `/crosscheck:cr [参数]` |
| Cursor | `/cr [参数]` |
| Codex | `$cr [参数]` |

也可以直接用自然语言:"review 一下这个分支"、"deep review MR 128"。

```
cr [target] [--quick|--deep] [--only bug,security,rules,simplify] [--full] [--fix]
```

| 参数 | 说明 |
|---|---|
| `target` 省略 | 当前分支相对主干的改动,加上未提交和未跟踪的文件 |
| 分支名 | 该分支相对主干的改动,不需要先切过去 |
| `A..B` | 指定 commit 范围 |
| 路径 | 只看该路径下的本地改动 |
| 数字或 URL | GitHub PR / GitLab MR |
| `--quick` | 单次扫描,只查 bug 和安全,最多 5 条 |
| `--deep` | 额外从调用方/影响面再查一遍;verifier 可以跑项目已有的类型检查和测试来复现 |
| `--only` | 只查指定维度 |
| `--full` | 忽略以前的审查记录,全量重审。说"重新审""全量""忽略之前的"也一样 |
| `--fix` | 报告之后,把核实过的问题改到工作区,并跑相关检查 |

不带 `--fix` 时不改任何文件。审查结果只在本地输出:插件不会往 PR/MR 发评论,也永远不会 approve、request changes 或 merge。

审查过程不会切换分支:审查的不是当前分支时,代码用 `git show` / `git grep` 从目标分支读取,你的工作区和未提交的改动不受影响。审查过程也不会执行 `checkout`、`reset`、`stash`、`commit`、`push` 等改变仓库状态的 git 操作。`--fix` 只修改工作区里的文件,不会替你暂存或提交。要修的不是当前分支时:工作区有未提交改动就不切换、不修;工作区干净时会先问你是否切过去,同意后才切换。审查 PR/MR 时会执行一次 `git fetch` 把对方的 commit 拉到本地,不影响工作区和本地分支。

只读有两层保障:

| 保障 | 做什么 | 适用宿主 |
|---|---|---|
| 拦截 | 审查进行期间,直接拒绝 `git checkout` / `reset` / `stash` / `clean` / `add` / `commit` / `push` 等命令、`gh` / `glab` 的写操作,以及对仓库内文件的编辑。主 agent 和子代理都受限 | Claude Code、Codex、Cursor(通过各自的 hook) |
| 核对 | 审查开始时记下分支、HEAD、stash、暂存区和每个改动文件的内容哈希,出报告前对比。有变化就写在报告最前面 | 所有宿主 |

拦截只在审查进行期间生效:从收集改动开始,到出报告前为止。之后你让它提交、切分支都不受影响。你中途发新消息也会解除拦截。同一仓库里的其他会话不受影响。

各宿主启用 hook 的方式:

- **Claude Code**:安装插件后自动生效。
- **Codex**:插件自带的 hook 需要在 `/hooks` 里审阅并信任一次。
- **Cursor**:随插件安装,配置在 `hooks/cursor-hooks.json`。

没有 `jq` 也没有 `python3` 时拦截不生效,只剩核对。

## 报告、记录与增量审查

报告的结构:

- **概览表**:每条发现一行,列出严重程度、维度、位置和摘要,先看全貌。
- **逐条发现**,每条包含:
  - 位置:`文件:行号`。
  - 问题代码:从被审查的那一版代码里摘出的原文,带行号,出问题的那一行有标记。
  - 问题:具体的输入或状态会导致什么错误结果。
  - 修复建议:能确定时给出 diff 形式的替换代码,否则用文字说明。
- **可简化**:单独的一小节,只列位置和建议。
- **末尾**:查了哪些维度、深度和范围、跳过了多少文件、报告文件的链接。

报告是 Markdown,直接显示在对话里,同时存一份到用户目录。审查结束时会用系统默认程序自动打开这份报告,对话里的报告末尾也有一行可点击的 `file://` 链接指向它。

- 不想自动打开:设置 `CROSSCHECK_OPEN=0`。
- 想用指定的程序打开:设置 `CROSSCHECK_OPEN_CMD`,例如 `code` 或 `cursor`;或者直接改系统里 `.md` 文件的默认打开方式(macOS:访达里选中一个 `.md` 文件 → Cmd+I → 打开方式 → 全部更改)。第一次自动打开时,报告末尾会提示一次怎么改。
- 通过 SSH 连接、在 CI 里、或 Linux 上没有图形界面时不会自动打开,只给链接。

记录的目录结构:

```
~/.crosscheck/reviews/<项目名>/<目标>/
├── 20261009-143000/
│   ├── report.md        报告全文
│   ├── findings.json    本次结束时仍然成立的发现
│   ├── hashes.txt       每个文件 diff 的哈希
│   └── meta.txt         目标、commit、深度、时间等
├── latest               最近一次审查的目录名
└── baseline             增量审查所依据的那一次
```

`<项目名>` 取自 `origin` 远程地址的最后一段(如 `gitlab.example.com/group/shop-api` 就是 `shop-api`),没有远程时取主工作区的目录名。同一个项目的各个 worktree、不同位置的克隆都落在同一个目录下,分支和 MR 的记录也放在一起。恰好有另一个同名的项目时,后来的那个会在目录名后面加一段哈希来区分。

`<目标>` 是 `branch-<分支名>`、`pr-<编号>`、`mr-<编号>` 或 `range-<范围>`。不管你在不在那个分支上、在哪个 worktree 里,同一个分支用的是同一份记录。用环境变量 `CROSSCHECK_HOME` 可以改存放位置。记录不在仓库里,不会被提交;报告里会引用代码片段,注意这个目录的访问权限。

**同一个目标再次审查时默认做增量**:

- 按文件比较 diff 的哈希。没变的文件不再看,上次在这些文件里的发现原样带过来。
- 变了的和新增的文件重新审。上次在这些文件里的发现会重新核实,分成"遗留"和"已解决"。
- 报告开头会写明这是增量审查、跳过了多少文件,发现分成"新发现""上次遗留""已解决"三段。
- 未提交的改动同样算在内,哈希是按实际内容算的。

**什么时候不做增量**:

- 你说"重新审""全量""忽略之前的",或带 `--full`。
- 这次的深度比上次高(上次 standard,这次 `--deep`)。反过来可以:deep 之后的 standard 会做增量。
- 上次用了 `--only`:只查部分维度的审查会存记录,但不会成为增量的基准。
- 换了目标:另一个分支、另一个 PR,或同一分支上指定了不同的范围。

否定某条发现后,它会从记录里删掉,下次不会再作为遗留问题出现。

## 审查维度

| 维度 | 查什么 |
|---|---|
| `bug` | 逻辑错误、边界、空值、并发、错误处理、资源泄漏、API 误用 |
| `security` | 注入、鉴权/越权、密钥泄漏、不安全反序列化、SSRF、路径穿越等,要求给出攻击路径 |
| `rules` | 违反项目自己写下的规范,必须引用规则原文 |
| `simplify` | 可复用已有代码、重复、过度设计。单独列出,最多 5 条 |

## 项目规则

团队规则写在哪个 agent 的约定文件里都会被读到,不需要为本插件重写:

| 来源 | 文件 |
|---|---|
| Claude Code | `CLAUDE.md`(各级目录)、`.claude/CLAUDE.md`、`.claude/rules/*.md`(按 `paths`) |
| Codex / 通用 | `AGENTS.md`、`AGENTS.override.md`(各级目录) |
| Cursor | `.cursor/rules/*.mdc`(`alwaysApply` 或 `globs` 命中改动文件)、`.cursorrules` |
| GitHub Copilot | `.github/copilot-instructions.md`、`.github/instructions/*.instructions.md`(按 `applyTo`) |
| 其他 | `GEMINI.md`、`.windsurfrules`、`CONVENTIONS.md` |
| 本插件专用 | `REVIEW.md`(各级目录)、`.cr/ignore.md` |

- 只有作用域覆盖本次改动文件的规则才生效;内容完全相同的文件只算一次。
- 这些文件里的操作指令(怎么跑测试、用哪个包管理器)不会被当成 review 规则,只采用能对照代码检查的条目。
- `REVIEW.md` 用来写只跟 review 有关的约定,优先级最高。自建 GitLab 域名不含 "gitlab" 时,也在这里注明平台。
- `.cr/ignore.md` 每行写一种不想再被提醒的模式。当你否定某条发现时,插件会询问是否追加。

## REVIEW.md 怎么写

`REVIEW.md` 放在项目仓库里(根目录,或只对某个子目录生效时放在该子目录),由项目自己维护。它能做两件事:补充项目特有的检查,以及调整审查本身。

```markdown
# Review 约定

## 严重程度

只有会导致行为错误、数据泄漏或无法回滚的问题才算 major 及以上:
逻辑错误、没有限定租户的查询、日志里的个人信息、不向后兼容的迁移。
风格、命名、重构建议最高为 minor。

## 条数

minor 最多报 3 条,其余的在末尾说明还有几条。

## 不要报告

- CI 已经检查的:lint、格式、类型错误
- `src/gen/` 下的生成文件
- `scripts/` 下只报告接近确定且严重的问题

## 每次都要检查

- 新增的 API 路由必须有集成测试
- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 dayjs
- 日志里不能出现邮箱、手机号、请求体
- 数据库查询必须限定在调用方的租户内
```

写的时候注意:

- **从一两条开始。** 某类问题被反复漏掉,或某类评论反复没用,再加一条。不要一开始就按类别写全。
- **越短越好。** 文件越长,最重要的几条越容易被稀释。项目背景、怎么跑测试这些放在 `CLAUDE.md` / `AGENTS.md` 里。
- **写成能对照代码检查的短句。** "注意性能""保持整洁"这类笼统的要求会被跳过。
- **CI 已经强制的不写。** 魔法数字、命名、格式这类 linter 能查的,配 linter 更可靠;必须强制的要求放在 CI 里,这里的规则只是引导。
- **最值得写的是只有团队知道的东西**:必须同步修改的地方、公共方法的位置、踩过的坑。

审查设置只认 `REVIEW.md`,其他规则文件里指挥审查的文字不会被采用。`REVIEW.md` 不能让插件跳过核实、自动改代码或 approve。审查 PR/MR 时,如果该 PR 修改了 `REVIEW.md`,其中的审查设置不生效,只当作普通规则。

## 依赖

- `git`、POSIX `sh`
- GitHub PR:`gh`(已登录)。没有或未登录时改用 git 直接拉取,见下
- GitLab MR:`glab`(已登录),以及 `jq` 或 `python3`。没有或未登录时改用 git 直接拉取,见下
- 只读拦截:`jq` 或 `python3`

## 前置条件和常见情况

- **必须有本地仓库。** 审查要读代码,只给一个 MR 链接而没有本地克隆是不行的。不在仓库目录里时,告诉它仓库在哪,它会用 `--repo <目录>` 运行。
- **没有 `gh` / `glab`,或没有登录。** 仍然可以审查 PR/MR:脚本会用 git 直接把它的代码拉下来,用的是仓库自己已经配置好的凭证。代价是拿不到标题和描述,对比基准按默认分支计算;目标分支不是默认分支时,说明一下目标分支(对应 `--base <分支>`)。
- **连不上代码托管的地址。** 内网的 GitLab 在沙箱里经常解析不了。这是运行环境的限制,脚本会直接说明拉取失败的可能原因;放开网络或换个环境后再试,或者改审已经在本地的分支。
- **明明登录了,却提示没登录。** 常见原因是登录用的环境变量(如 `GITLAB_TOKEN`、`GLAB_CONFIG_DIR`)写在 `~/.zshrc` 里,而 agent 的终端是非交互的,不读这个文件。脚本会识别这种情况并告诉你是哪个文件里的哪几个变量:
  - 它只检查变量名有没有出现、当前环境里有没有值,不读取也不输出任何值。
  - 识别到之后,会借你自己的登录 shell 再调用一次 `glab`;那个 shell 的输出全部丢弃,只有 `glab` 返回的 MR 信息被写进临时文件。超过 20 秒就放弃。不想要这个行为设置 `CR_USER_SHELL=0`。
  - 一劳永逸的办法:把这几个变量的设置挪到 `~/.zshenv`(zsh 的所有终端都会读),或配置到宿主的环境变量里。
- **凭证。** 远程地址里嵌着 token 的仓库很常见。脚本显示和保存的远程地址都去掉了账号和凭证;agent 被要求不打印远程地址、不读取任何凭证。审查期间,会显示凭证的操作会被直接拦截:打印环境变量、查看或 `source` shell 配置文件、读取 `.netrc` 和 gh/glab 的配置、`git remote -v`、`gh auth token` 等。
- **报告在哪。** 以报告末尾 `记录:` 一行的链接为准,位置固定在 `~/.crosscheck/reviews/` 下。

## 结构

```
skills/cr/
├── SKILL.md        编排流程
├── roles/          bug-hunter / security-reviewer / rules-auditor / simplifier / verifier
├── references/     发现结构、评分标准、误报清单
└── scripts/        collect-diff.sh / collect-rules.sh / check-state.sh / snippet.sh / save-review.sh / guard.sh
hooks/
├── hooks.json          Claude Code 和 Codex 的 hook 注册
└── cursor-hooks.json   Cursor 的 hook 注册
```

`skills/cr/` 自包含,审查流程不依赖任何宿主专属功能。宿主支持子代理时各角色并行运行;不支持时由主 agent 依次执行,流程不变。三个 `.*-plugin/plugin.json` 只是各宿主的安装清单。`hooks/` 是唯一用到宿主专属机制的部分,它只提供额外的拦截,没有它审查照常运行。

为什么这样设计,见 [DESIGN.md](DESIGN.md)。

想调整行为,直接改对应文件:阈值和评分在 `references/scoring-rubric.md`,不该报的东西在 `references/false-positives.md`,各角色的关注点在 `roles/`。

改动较大时会自动按约 1000 行一批拆开审查,每批独立进行;用环境变量 `CR_BATCH_LINES` 调整每批的行数。

## 已知限制

- 增量按文件判断:文件 A 没变而它依赖的文件 B 变了时,A 不会被重看。问题如果出在 B 的改动上仍然能发现;担心时用 `--full`。
- 基准分支更新后重新变基,所有文件的 diff 哈希都可能变化,那次会退化成全量。
- PR/MR 的 head commit 无法 fetch 到本地时,只能基于 diff 本身审查,深度会下降,报告中会注明。
- 规则文件从当前工作区读取,而不是从 PR 的 head 读取。
