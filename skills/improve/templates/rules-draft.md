# 规则维护草稿

<!-- rules-draft.py 按真实基线和受限清单生成的人读视图示例,内容虚构。
不得手改此文件伪造确认;agent 应修改 proposal.json 并重新 --propose/--check。
完整历史材料另由内部机器文件绑定,这里仅展示理由、目标、diff 和待确认。
原生入口由 proposal.json 的 instruction_files 明确选择,默认仍为 ["AGENTS.md"]。
联合接入示例:instruction_files=["AGENTS.md","CLAUDE.md"],配合现有 agents_links/agents_source。
入口和规则须先 --capture;只生成代码约束章节的受管提示及规则链接,不接受入口正文提案。
已有 .claude/CLAUDE.md 且根 CLAUDE.md 不存在时,捕获并选择既有入口;不自动回退或新建第二个入口。
-->

- 目标:把公共查询约束收窄到接口目录,同时清理 REVIEW.md 中的重复代码约束
- 版本:2
- 指纹:`<本次 --check 输出的完整 DIGEST>`

确认这份具体 diff 后才写工作区,不提交。补充要求或内容变化后重新预演、确认。

- 复核:现有规则与 REVIEW.md 重复;约束归 rules,审查配置保留在 REVIEW.md

## 待确认

- 离线导出是否始终使用独立协议,尚未核实
- 是否将本次规则同时接入 AGENTS.md 和选中的 CLAUDE.md 原生入口
- CLAUDE.md 对旧规则存在不能安全同步的手写或重复引用;已保留,请确认其范围/删除后的链接

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

## 修改 AGENTS.md

- 理由:接入本次规则索引
- 来源:用户要求
- 要求:在原生指令入口提示先读对应规则,保留用户正文

```diff
--- AGENTS.md
+++ AGENTS.md
@@ -2,0 +3,5 @@
+## 代码约束
+
+改动对应路径前先打开链接的规则文件读完并遵守。
+
+- [api](.agents/rules/api.md):改 src/api/** 时先读这个文件并遵守
```

## 修改 CLAUDE.md

- 理由:接入本次规则索引
- 来源:用户要求
- 要求:在原生指令入口提示先读对应规则,保留用户正文

```diff
--- CLAUDE.md
+++ CLAUDE.md
@@ -2,0 +3,5 @@
+## 代码约束
+
+改动对应路径前先打开链接的规则文件读完并遵守。
+
+- [api](.agents/rules/api.md):改 src/api/** 时先读这个文件并遵守
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
