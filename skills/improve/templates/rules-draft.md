# 规则维护草稿

<!-- rules-draft.py 按真实基线和受限清单生成的人读视图示例,内容虚构。
不得手改此文件伪造确认;agent 应修改 proposal.json 并重新 --propose/--check。
顺序固定:变更一览 → 待确认 → 规则与 REVIEW.md 的 diff → 接入 → 核对说明 → 参考材料(只在历史归纳时出现)。
写代码一侧的接入由脚本按规则变更自动生成,不接受提案:写入 .agents/rules 时 AGENTS.md 只加一句固定提示(给 Codex、Cursor),
并为规则建 .claude/rules/<相对路径>.md 相对符号链接(Claude Code 按 paths 原生加载);删除规则时删掉指向它的链接。
CLAUDE.md 不写;删除规则后入口文件里仍有的引用只列待确认。下例的 AGENTS.md 是把 0.3.12 的逐条索引迁移成这句提示。
历史归纳的案例 / 维护建议每个主题一行放在参考材料,观察编号、全部引文和仓库核对在本轮 rules-materials.md。
-->

- 目标:把公共查询约束收窄到接口目录,同时清理 REVIEW.md 中的重复代码约束
- 版本:2
- 指纹:`<本次 --check 输出的完整 DIGEST>`

确认这份具体 diff 后才写工作区,不提交。补充要求或内容变化后重新预演、确认。
此文件由脚本生成;对话修改由 agent 更新提案,不要直接改此视图。

## 本次变更一览

| 文件 | 操作 | 写什么 | 来源 |
| --- | --- | --- | --- |
| .agents/rules/api.md | 修改 | 将约束限制在公共查询范围,保留明确例外 | 用户要求 |
| .agents/rules/query.md | 删除 | 内容已并入 api.md,保留会让同一约束有两个范围 | 用户要求 |
| REVIEW.md | 修改 | 代码约束由 rules 统一承担,删除重复句 | 材料推断 |
| AGENTS.md | 修改 | 提示读 AGENTS.md 的宿主去 .agents/rules 按 paths 找适用规则 | 材料推断 |
| .claude/rules/ | 新增链接 1 个 | 让 Claude Code 按 paths 加载规则 | 材料推断 |
| .claude/rules/ | 删除链接 1 个 | 去掉指向已删除规则的链接 | 材料推断 |

4 个文件变更、新增 1 个链接、删除 1 个链接;待确认 2 项。

## 待确认

- 离线导出是否始终使用独立协议,尚未核实
- CLAUDE.md 仍引用已删除的规则 .agents/rules/query.md,请确认是否改掉这处引用

## 修改 .agents/rules/api.md

- 理由:将约束限制在公共查询范围,保留明确例外
- 来源:用户要求
- 要求:只约束公共查询,离线导出除外

```diff
--- .agents/rules/api.md
+++ .agents/rules/api.md
@@ -1 +1 @@
-- **规则**:所有查询必须校验分页上限
+- **规则**:公共查询接口必须校验分页上限,离线导出除外
```

## 删除 .agents/rules/query.md

- 理由:内容已并入 api.md,保留会让同一约束有两个范围
- 来源:用户要求
- 要求:只约束公共查询,离线导出除外

```diff
--- .agents/rules/query.md
+++ /dev/null
@@ -1 +0,0 @@
-- **规则**:所有查询必须校验分页上限
```

## 修改 REVIEW.md

- 理由:代码约束由 rules 统一承担,删除重复句
- 来源:材料推断(不因确认而变成历史共识)
- 依据:规则文件已经覆盖同一范围,REVIEW.md 应只保存审查行为配置

```diff
--- REVIEW.md
+++ REVIEW.md
@@ -1 +0,0 @@
-- 所有查询必须校验分页上限
```

## 接入

- 来源:材料推断(不因确认而变成历史共识)

修改 AGENTS.md:提示读 AGENTS.md 的宿主去 .agents/rules 按 paths 找适用规则

```diff
--- AGENTS.md
+++ AGENTS.md
@@ -5,3 +5 @@
-改动对应路径前先打开链接的规则文件读完并遵守。
-
-- [api](.agents/rules/api.md):改 src/** 时先读这个文件并遵守
+改代码前先查看 .agents/rules/ 下的规则文件:按文件头的 paths 判断是否适用于要改的文件,适用的读完并遵守。
```

Claude Code 按 .claude/rules 的 paths 原生加载规则,下面的链接都指向对应的 .agents/rules 文件:

```
LINK: .claude/rules/api.md -> ../../.agents/rules/api.md
UNLINK: .claude/rules/query.md
```

## 核对说明

- 现有规则与 REVIEW.md 重复;约束归 rules,审查配置保留在 REVIEW.md
