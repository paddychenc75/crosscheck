# Changelog

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
