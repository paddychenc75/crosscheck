# 更新日志

本文件记录 crosscheck 每个版本对使用者可见的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

## [0.1.0] - 2026-10-09

首个公开版本。

### 新增

- **审查流程**:多个专项角色(`bug-hunter`、`security-reviewer`、`rules-auditor`、`simplifier`)并行查找候选,再由没看过推理过程的 `verifier` 独立核实并打分,低于 80 分的不展示。
- **审查目标**:当前分支(含未提交和未跟踪的改动)、分支名或其他 ref(不需要检出)、commit 范围、路径、GitHub PR 和 GitLab MR 的编号或 URL。
- **深度档位**:`--quick`、默认的 standard、`--deep`;`--only` 限定审查维度。
- **大改动分批**:改动超过约 1000 行时按路径顺序切成批次分别审查,用 `CR_BATCH_LINES` 调整每批大小。
- **项目规则**:读取各 agent 的约定文件(`CLAUDE.md`、`AGENTS.md`、Cursor rules、Copilot instructions 等),按作用域筛选并去重。
- **`REVIEW.md` 审查设置**:项目可以调整跳过的路径或类别、严重程度的定义、条数上限和证据要求。被当前 PR/MR 修改过的 `REVIEW.md` 不生效。
- **`.cr/ignore.md`**:记录不想再被提醒的模式。
- **审查记录**:每次审查的报告和发现保存到 `~/.crosscheck/reviews/`,用 `CROSSCHECK_HOME` 修改位置。
- **增量审查**:同一目标再次审查时,只看 diff 发生变化的文件;报告分为新发现、上次遗留、已解决三段。`--full` 或说“重新审”时全量重审。
- **`--fix`**:只在用户明确要求时,把通过核实的发现改到工作区;不暂存、不提交。目标不是当前分支且工作区有未提交改动时不切换、不修。
- **只读保障**:
  - 审查期间拦截改变仓库状态的 git / gh / glab 命令和对仓库内文件的编辑(Claude Code、Codex、Cursor 的 hook)。
  - 出报告前核对仓库状态,有变化时写在报告最前面。
- **多宿主**:同一套 skill 可在 Claude Code、Cursor、Codex 中运行;宿主不支持子代理时由主 agent 依次执行。
- **安装**:仓库自身可作为 Claude Code 的安装源。

### 已知限制

- 只在本地输出报告,不往 PR/MR 发评论。
- 只读拦截是用模拟的 hook 输入测试的,没有在真实宿主里端到端验证过。
- 通过 Cursor 和 Codex 各自的插件机制安装的方式没有验证过。
- 80 分阈值和每批 1000 行没有在标注数据集上验证过。

[未发布]: https://github.com/paddychenc75/crosscheck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/paddychenc75/crosscheck/releases/tag/v0.1.0
