# 审查之外的功能

这些功能都在审查结束之后、由用户发起。会往 GitLab 写东西的(创建规则 MR、推送判断记录),一律先给用户看要做什么,用户明确确认后才执行。

## 提炼规则（负责人按需发起）

用户说「从最近一个月的 MR 评论里总结一下规则」「提炼规则」时:

1. 收集材料(只读,需要 glab 已登录):
   ```
   sh <skill_dir>/scripts/mine-reviews.sh [--since 30d | --mr 128,140] [--repo <目录>]
   ```
   默认按 `merged_at` 筛选时间范围，包含人工行评论和一般讨论；先检查 `collection-status.json`，再核对 `existing-rules.md`、`open-candidates.json` 与关闭提案。未读全仅对已取得材料提出候选。最后一行是输出目录。脚本报错说需要 glab 时,告诉用户安装并登录(`glab auth login`,私有化 GitLab 用 `glab auth login --hostname <域名>`),不要尝试别的办法取凭证。
2. 启动 `rule-miner`(配置里有 `model.helper` 时用那个模型;没有子代理就自己按 `roles/rule-miner.md` 做):
   > 阅读 `<skill_dir>/roles/rule-miner.md` 并严格照做。材料在 `<输出目录>`。`skill_dir` 是 `<path>`。只回复 JSON。
3. 把结果列给用户:每条候选的规则、原因、作用域、证据(几条评论、几个 MR、几位评论人)、操作（新增 / 改写 / 删除）、具体维护文件及命中 / 不命中路径;没收的也简要列出原因。`misses_for_eval` 告诉用户保存为评测候选，独立确认后再纳入后续评测集，不修改本轮答案。
4. 用户挑选后,把选中的写成改动 JSON(`[{"op": "add", "text": …, "reason": …, "scope": …, "severity": …, "src": "!301,!317"}]`),先预览:
   ```
   sh <skill_dir>/scripts/propose-rules.sh --ops <改动.json> --target project --mr --preview <仓库外/preview.json> [--repo <目录>]
   ```
   给用户看 diff、未合入提案和路径验收结果，核对语义重复。预览和创建均用默认分支；基准或提案列表改变会要求重新预览。只写工作区时使用 `--worktree --preview <文件>`，再用同一文件加 `--yes`；不带 preview 的旧工作区调用继续兼容。
   原生格式操作必须指定 `file`，改写 / 删除用 `old_text` 唯一定位正文，保留 frontmatter 与已有编号；新增文件可给完整 `frontmatter`。用 `paths: {"match": [...], "miss": [...]}` 验证范围，全局规则的 miss 可为空。既有文件范围不能在新增条目时暗中扩大，优先另建相应范围的小文件；不支持的语法明确报错。组织规范与配置中的同步副本只读。通用约束更新已有 rules，审查专属要求才写 REVIEW.md。
   给用户看 diff。用户确认后创建 MR(只创建、不合并,带 `crosscheck-rule` 标签,不碰用户的工作区):
   ```
   sh <skill_dir>/scripts/propose-rules.sh --ops <改动.json> --target project --mr --preview <同一preview.json> --yes --title "crosscheck: 新增规则 …"
   ```
   跨项目通用建议只输出候选、证据与来源，交外部负责人处理。Crosscheck 不创建组织规则 MR，不调用组织写入工具。`propose-rules.sh` 只接受 project / ignore。

## 规则复核(负责人定期做)

规则的去留由负责人定期主动复核决定,不靠命中次数这类统计:一条规则长期没被报过,可能是没用了,也可能是大家都遵守了。建议每季度一次;团队小、规则在十几条以内的,半年一次也够。审查报告里 rules-auditor 提示「规则可能过时」时,也可以提前复核那一条。

用户说「检查一下我们的规则」「复核规则」时:

```
sh <skill_dir>/scripts/rules-health.sh [--repo <目录>] [--months 6]
```

它列出项目及目录规则、所在的层、上次复核的时间;超过 6 个月没复核的排在最前。「检查提示」一列是只读仓库就能做的检查:作用域匹配不到文件、规则里提到的文件或函数在代码库里找不到、和别的规则几乎一样、原生格式不强制编号，缺日期只提示人工复核。这些只是线索:比如规则禁止的东西在代码库里找不到,说明大家都遵守了,不用动。

把清单给负责人,陪他逐条过:

- 还对的 → `{"op": "confirm", "id": "PRJ-003"}`,记下 `reviewed=<本月>`;
- 要改的 → `{"op": "replace", "id": …, "text": …, "reason": …}`;
- 不要的 → `{"op": "remove", "id": …}`;命名、格式这类规则,建议改配 lint 规则后再删。

用 `propose-rules.sh --mr --preview <文件>` 预览，负责人确认后用同一 preview 加 `--yes` 创建 MR。负责人想知道某条规则和现有代码还对不对得上,可以让你在它的作用域里挑几个文件读一读再判断。

写入前的格式检查可以单独运行:`sh <skill_dir>/scripts/rules-lint.sh [--repo <目录>]`；`--rules-repo` 仅作为旧 Git 格式的只读校验工具保留。

## 审查效果和采纳率

用户问「review 的效果怎么样」「采纳率」「看看统计」时:

1. 先更新采纳情况(只读拉取已合并 MR 的最终代码):
   ```
   sh <skill_dir>/scripts/adoption.sh scan [--repo <目录>]
   ```
   如果它说有需要确认的条目,启动 `judge`(`mode=adoption`),任务里给出 `adoption-pending.json` 的路径;它会逐条判断问题是否真的解决,并用 `adoption.sh record` 记下来。没有子代理就自己按 `roles/judge.md` 做。
2. 再出统计:
   ```
   sh <skill_dir>/scripts/stats.sh [--all] [--days <n>] [--repo <目录>]
   ```

把输出原样给用户看。不要把「代码已改」说成「被采纳」;样本不足的比例不要自己算。「差一点通过」里 70–79 分的候选,可以建议用户挑几条看看,判断 80 分门槛是不是太严。不要根据这些数字自行改变审查的行为;要调整就写进 `REVIEW.md`。

## 同步团队的判断记录(可选)

团队开启共享时(配置里有 `data_repo`),用户说「同步一下 review 数据」:

```
sh <skill_dir>/scripts/sync-data.sh push          # 先拉取团队的记录,再列出要推送的条数
sh <skill_dir>/scripts/sync-data.sh push --yes    # 用户确认后推送
```

只拉取用 `sync-data.sh pull`。没有配置 `data_repo` 时告诉用户:不共享也能正常使用,团队可以靠把常见误报写成 `.cr/ignore.md` 来共享。

## 同步组织规则

用户说「同步一下组织规则」,或报告提示规则副本过期、找不到规则包时:

```
sh <skill_dir>/scripts/sync-rules.sh          # 克隆或更新到配置的 rules_ref
sh <skill_dir>/scripts/sync-rules.sh --check  # 只看状态
```

没有配置 `rules_repo` 时,告诉用户在配置文件里加上 `rules_repo=<地址>` 和 `rules_ref=<tag>`(配置文件的位置用 `sh <skill_dir>/scripts/config.sh path` 查)。

## 组织来源只读

组织规则的审批、发布、废弃由外部平台负责。`sync-rules.sh` 只保留 Git 来源同步兼容，不要求其他平台使用 Git 格式。通过 MCP 或文件接入见 `materials.md`；组织规则有问题时提供证据，不修改上游。
