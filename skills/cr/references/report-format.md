# 报告的写法

报告的内容全部来自 `report-lines.sh` 生成的 `report.json` 和说明行（旧版本没有 report.json 时使用 final.json）,不要自己增删发现、改分数或改编号。

`report-lines.sh` 每行以一个标签开头,标签不写进报告:

| 标签 | 放在哪 |
|---|---|
| `GROUPED:` | 末尾说明同根因合并展示的组数 |
| `SCOPE:` | 报告第一行,紧跟标题 |
| `INTENT:` | 第二行 |
| `WEAKEN:` | 「测试和检查被削弱」一节,每行一条 |
| `MATERIALS:` | 末尾逐行说明外部来源、读取情况；不得把不完整称为检查完成 |
| `UNVERIFIED:` | 单列“本轮未能复核”，从 final.json.unverified 列出旧发现与原因，不计已解决 |
| `RISK:` | 末尾说明行 |
| `RULES:`、`VERIFY:`、`RULES_NOTE:` | 末尾说明行,`RULES_NOTE` 每条一行 |
| `TRIMMED:` | 末尾说明行 |
| `SKIPPED:`、`CHECKED:` | 末尾说明行 |
| `FEEDBACK:` | 报告最后,`记录:` 一行之前 |

严重程度显示为:`critical` → 严重,`major` → 重要,`minor` → 轻微。维度显示为:`bug`、`安全`、`需求核对`(intent)、`规范`(rules)、`可简化`(simplify)。

## 整体结构

``````
## 审查:<target 描述> — <N> 条发现

<SCOPE 的内容>
<INTENT 的内容>

| # | 严重程度 | 维度 | 位置 | 摘要 |
|---|---|---|---|---|
| 1 | 重要 | bug | `path/to/file.ts:42` | <摘要> |

### 1. [重要 · bug] <摘要>
<按下面「每条发现」的格式>

### 需求核对(<n>)
<每条按「每条发现」的格式,编号接着上面>

### 规范(<n>)
- **<编号>.** `path:line` — <规则编号>「<规则原文>」(出处 <文件>)→ <违反在哪、建议怎么改>
  <details><summary>依据(置信度 <分数>)</summary> … </details>

### 可简化(<n>)
- **<编号>.** `path:line` — <摘要> → <建议>

### 测试和检查被削弱(<n>)
<WEAKEN 的每一行>

<details><summary>已按历史判断忽略(<n>)</summary>

- `path:line` — <摘要>(<时间>判为<不成立/不修>:<原因>)
</details>

<RISK>
<RULES>
<VERIFY>
<RULES_NOTE…>
<TRIMMED>
<SKIPPED>
<CHECKED>
消耗:<save-review 的 cost>
<FEEDBACK>
记录:[<报告路径>](<file:// 链接>)
``````

`<N>` 是缺陷(bug、安全)和需求核对的条数之和。概览表只列缺陷和需求核对;两者都没有时不要概览表。某一节没有内容时省略该节。

**增量审查**(`final.json` 的 `scope` 为 `incremental`):标题写成 `— <N> 条发现(新增 <a> · 遗留 <b> · 已解决 <c>)`;概览表多一列「状态」(新增 / 遗留);逐条发现分成「新发现」和「上次遗留(仍然存在)」两段,遗留的标题末尾加 `· 首次报告于 <since>`;再加一节「已解决(<c>)」,列出 `final.json` 的 `resolved`:`- \`path:line\` — <摘要>(<resolved_reason>)`。

**没有任何发现通过核实时**,说明已检查部分没有确认发现；若外部资料不完整或有未能复核项，同时明确审查未完成，不能声称整体没有问题,后面跟上 `RISK`、`RULES`、`VERIFY`、`CHECKED` 这几行和测试削弱(如果有)。不要为了填充篇幅而添加观察、表扬或泛泛的建议。不要列出被否决的候选,除非用户问起(见 `after-report.md`)。

## 每条发现

``````
### 1. [重要 · bug] <摘要>

**位置**:`path/to/file.ts:42`

**问题代码**

```ts
   41 |   const total = rows.length;
>  42 |   const pageCount = Math.floor(total / pageSize);
   43 |   return rows.slice(0, pageCount * pageSize);
```

**问题**:<failure_scenario>

**修复建议**

```diff
-   const pageCount = Math.floor(total / pageSize);
+   const pageCount = Math.ceil(total / pageSize);
```

<details><summary>依据(置信度 96)</summary>

- 读过:paginate.ts:38-47(改动处);api/orders.ts:88(唯一调用方)
- 排除:base 版本用的是 Math.ceil,问题由本次改动引入
- 复现:<repro.cmd> → <repro.result>
- 查到:bug-hunter(两遍都查到)
</details>
``````

- **问题代码**必须是原文,不要凭记忆写。用 `sh <skill_dir>/scripts/snippet.sh <review_dir> <file> <line> [前面几行] [后面几行]` 摘,输出原样放进代码块,语言标记按文件类型写。默认前后各 3 行,一般不超过 12 行;问题涉及相隔较远的两处时,摘两段。命令报错说代码不在本地时,从 `diff.patch` 里摘对应的行。
- **修复建议**:`suggestion` 里有确切的替换代码时写成 `diff`,`-` 行与问题代码原文一致;只有文字时照文字写,不要自己编代码;没有 `suggestion` 时写「修复方式需要结合上下文判断」并说明要考虑什么。
- **兼容性**:有 `compat` 时,在修复建议下加一行 `**兼容性**:<compat>`。
- **之前的判断**:有 `previously` 时,在问题下加一行说明,例如 `之前(10-09)判为不成立:上游已校验;本次重新报告是因为 <原因>`。
- **依据**:默认折叠。`evidence` 每项写成「读过:文件:行(note)」,`ruled_out` 每项写成「排除:…」,有 `repro` 时写「复现:命令 → 结果」,`found_by` 写成「查到:…」(`pass_hits` 大于 1 时注明几遍都查到),`spot` 为真时写「来自抽查:rules-auditor 漏检」。
- **规范**发现在问题里逐字引用 `rule_quote` 和出处(`rule_origin` 存在时展示原始出处，`rule_file` 是本次保存的正文位置),`also_lines` 有值时写明共几处。
- **需求核对**发现在问题里写出是哪一条验收标准(来自 `intent.md`)。

## 同一问题的关联依据

`report.json` 的分组主条只展示一次失败事实和修复建议。保留其原 `no`，并标明 `display_members` 中的关联编号；无需为了连续编号重新编号。`related_findings` 中的规则原文、出处和核实依据作为主条下的“关联规范/验收依据”展示，不再在另一个分类重复列一条相同问题。不得丢掉规则原文、验收出处、独立核实证据或关联编号；不同失效条件不能借分组省略。

历史记录仍按原始发现编号保存。合并展示只减少重复叙述，不把关联规范变成未核实的附注，也不改变80分门槛。
