<!-- CR 报告的骨架:章节的顺序,每节放什么。尖括号是要填的内容,填写规则(何时省略某节、标签含义、依据怎么写、增量审查和 0 条发现时的变化)见 references/report-format.md。某一节没有内容时整节省略。 -->

## 审查:<target 描述> — <N> 条发现

<!-- 有 INCOMPLETE 或保存返回 incomplete 时,标题改为「审查未完成:<target 描述> — 已检查部分确认 <N> 条发现」;首句以「审查未完成,已检查部分确认 <N> 条发现」开头,接已知覆盖限制。N 仍按本次展示条目计数,完整审查照常。 -->
<一句结论,只概括本次展示的发现与覆盖限制>

<MERGED 的内容,只在已合入时>
<SCOPE 的内容>
<INTENT 的内容>

| # | 严重程度 | 维度 | 位置 | 摘要 |
|---|---|---|---|---|
| 1 | 重要 | bug | `path/to/file.ts:42` | <摘要> |

<!-- 逐条发现:概览表之后,每条一个三级标题,写法见下面「每条发现」 -->
### 1. [重要 · bug · 已复现] <摘要>
<按下面「每条发现」的格式>

### 需求核对(<n>)
<每条按「每条发现」的格式,编号接着上面>

### 规范(<n>)
- **<编号>.** `path:line` — 「<规则原文>」(出处 <文件>)→ <违反在哪、建议怎么改>
  <details><summary>依据(置信度 <分数>)</summary> … </details>

### 测试与检查变更(<n>)

以下为检测信号,不代表已确认缺陷,不计入发现条数。

<WEAKEN 的每条信号原样写入:首行是位置 file:line(删除行带「旧版」)和说明,紧跟 diff 代码块(原始 +/- 片段,不是修复建议)>
<details><summary>已按历史判断忽略(<n>)</summary>

- `path:line` — <摘要>(历史处理:<history_decision>,原因:<suppression_reason>;当前事实:<verdict>,置信度 <confidence>;前提复核:<history_context>)
</details>

### 改动代码里的原有问题(<n>)
<PREEXIST 的每一行>

<RULES>
<RULES_NOTE…>
<TRIMMED>
<SKIPPED>
<CHECKED>
<FACTS_NOTE>
<INCOMPLETE>
消耗:<save-review 的 cost>
<FEEDBACK,仅脚本输出时展示,没有时整行省略>
记录:[<报告路径>](<file:// 链接>)

<!-- 每条发现。标题里的三项依次是严重程度、维度、MARK 给出的标记(已复现 / 已执行·结果未定(读代码判断) / 读代码推断)。 -->
### 1. [重要 · bug · 读代码推断] <摘要>

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
- 运行 <repro.run>:`<repro.cmd>` → 退出码 <repro.exit>,<测试数>
  ```
  <repro.tail 原样>
  ```
- 查到:reviewer(安全一遍)
</details>
