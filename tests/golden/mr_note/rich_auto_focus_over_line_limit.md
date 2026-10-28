<!-- crosscheck:begin -->
**订单列表改为游标分页**
需求单:https://tickets.acme.example.com/story/42 「订单游标分页」 · 需求文档:未提供(作者跳过) · feature → main · 2 个文件 +5/−2 · 审查量 1/5

### 可能值得重点看(自动列出)
1. `api/f1.ts:3` — `fn1` 签名变化,另有 2 处调用
2. `api/f2.ts:3` — `fn2` 签名被删除,另有 2 处调用
3. `api/f5.ts:3` — `fn5` 签名变化,另有 2 处调用

### 阅读顺序
1. 接口和类型:`api/f1.ts`
2. 实现:`api/f2.ts`、`api/f3.ts`

### 影响面
> 只按名字在本仓库文本匹配、只看签名变化;签名不变的行为变化不在这里,由 CR 报告负责
- `fn1` 签名变化:本次之外 2 处调用 `use/c1.ts:11`、`use/d1.ts:2`
- `fn2` 被删除(同名歧义):本次之外 2 处调用 `use/c2.ts:12`、`use/d2.ts:2`
- `fn5` 签名变化:本次之外 2 处调用 `use/c5.ts:15`、`use/d5.ts:2`
- `listOrders` 签名变化:本次之外 2 处调用 `report/daily.ts:1`、`report/daily.ts:2`
- `fn0` 被删除:本次之外 1 处调用 `use/c0.ts:10`
- 另有 3 个,见完整版

**CR 情况(作者本地运行)** · crosscheck <VERSION> · standard
已修复 0 条;未修复的严重和重要 8 条:
- [重要] `api/orders.ts:1` 重要 1 — 未表态
- [重要] `api/orders.ts:2` 重要 2 — 未表态
- [重要] `api/orders.ts:3` 重要 3 — 未表态
- [重要] `api/orders.ts:4` 重要 4 — 未表态
- 另有 4 条,见完整版
基于提交 <SHA>
<!-- crosscheck:end -->
