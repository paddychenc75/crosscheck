# 评测

评测衡量的是 crosscheck 这个工具本身:改了角色说明、评分标准、换了模型、调了门槛之后,结果变好还是变坏。由插件维护者在改动合入前运行,普通开发者不需要做。

## 跑一次评测

用户说「跑一下评测」「和基线比一下」时:

1. 准备:
   ```
   sh <skill_dir>/scripts/eval.sh prepare [--cases <目录>]
   ```
   默认读组织规则仓库的 `evals/cases/*.json`,再加上规则包里每条规则的正反例。用例里的仓库名要在配置里映射到本机路径(`repo.<名字>=<路径>`),没映射的会被跳过并列出来。输出运行目录 `RUN` 和每个用例要做的事。
2. 对 `RUN/plan.json` 里的每个用例做一次审查,所有命令前先加载 `RUN/env.sh` 里的环境变量(隔离的记录目录,不读历史判断、不做增量):
   - `collect-diff.sh <target> --repo <repo_path> --full --out RUN/cases/<case>/review`,深度和模型与基线保持一致(基线的 `per_case` 里没有记录时用 standard);规则正反例(`kind: rule-example`)加 `--only rules`。
   - 照 SKILL.md 第 2–5 步查找、核实、汇总,跑到 `aggregate.sh` 为止,不写报告、不保存记录。
   - `sh <skill_dir>/scripts/eval.sh done RUN <case>`。
   用例之间互不影响,能用子代理时可以并行。
3. 对答案:
   ```
   sh <skill_dir>/scripts/eval.sh match RUN
   ```
   然后启动 `judge`(`mode=eval`,配置里有 `model.helper` 时用那个模型),任务里给出 `RUN` 的路径;它把结论写进 `RUN/judgments.json`。挑几条给用户人工抽查,确认判断方式本身可靠。
4. 计分并和基线比较:
   ```
   sh <skill_dir>/scripts/eval.sh score RUN [--baseline <文件>]
   ```
   把报告原样给用户看,包括结论和样本量的说明。差距只有一个用例时,建议重跑一次再下判断。
5. 用户决定合入这次改动后,更新基线(先预览,确认后 `--yes` 创建 MR):
   ```
   sh <skill_dir>/scripts/eval.sh baseline RUN
   ```

## 补充评测用例

用户说「找几个评测用例」时:

```
sh <skill_dir>/scripts/eval.sh find-cases --repo <目录> [--since 365d] [--limit 20]
sh <skill_dir>/scripts/eval.sh find-cases --repo <目录> --clean      # 干净的提交
```

逐个给熟悉代码的人确认:这确实是那次提交引入的问题吗?期望 crosscheck 报出什么?确认的用:

```
sh <skill_dir>/scripts/eval.sh add-case --repo-name <名字> --base <sha> --head <sha> --source fix-commit \
   --expect "src/a.ts:40-44:bug:major:整页时丢最后一页" --confirmed-by <确认人>
```

来自 MR 的用例加 `--intent-file <那次审查记录里的 intent.md>`,把当时的 MR 描述和验收标准一起存进用例;否则评测时只有提交范围,需求核对类的期望测不出来。

干净用例不带 `--expect`,`--source clean`。从漏报来的用 `--source missed`。攒够一批后 `eval.sh submit-cases`(预览)、`eval.sh submit-cases --yes`(创建 MR,提交到规则仓库的 `evals/cases/`)。

选用例时照顾比例:2–3 个仓库、单个仓库不超过一半;bug 约一半,干净用例约三成;改动大小小中大都有;最近 6–12 个月的提交。期望问题少于 50 个时,评测只能发现明显的倒退。
