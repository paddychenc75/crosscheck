<!-- CR 报告的骨架:章节的顺序,每节放什么。尖括号是要填的内容,填写规则(何时省略某节、标签含义、依据怎么写、增量审查和 0 条发现时的变化)见 references/report-format.md。某一节没有内容时整节省略。 -->

## 审查:<target 描述> — <N> 条发现

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

### 测试和检查被削弱(<n>)
<WEAKEN 的每一行>

<details><summary>已按历史判断忽略(<n>)</summary>

- `path:line` — <摘要>(<时间>判为<不成立/不修>:<原因>)
</details>

### 改动代码里的原有问题(<n>)
<PREEXIST 的每一行>

<RULES>
<RULES_NOTE…>
<TRIMMED>
<SKIPPED>
<CHECKED>
<INCOMPLETE>
消耗:<save-review 的 cost>
<FEEDBACK>
记录:[<报告路径>](<file:// 链接>)

<!-- 每条发现。标题里的三项依次是严重程度、维度、MARK 给出的标记(已复现 / 读代码推断)。 -->
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
