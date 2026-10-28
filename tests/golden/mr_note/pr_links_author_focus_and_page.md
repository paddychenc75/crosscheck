<!-- crosscheck:begin -->
**订单列表改为游标分页**
需求单:https://tickets.acme.example.com/story/42 「订单游标分页」 · 需求文档:未提供(作者跳过) · feature → main · 2 个文件 +5/−2 · 审查量 1/5

### 请重点看
1. [`api/orders.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/orders.ts#L1) — 兼容期够不够
2. [`report/daily.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/report/daily.ts#L2) — 调用方要不要一起改

### 阅读顺序
1. 接口和类型:`api/orders.ts`
2. 配置:`config/app.yml`

### 影响面
> 只按名字在本仓库文本匹配、只看签名变化;签名不变的行为变化不在这里,由 CR 报告负责
- `listOrders` 签名变化:本次之外 2 处调用 [`report/daily.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/report/daily.ts#L1)、[`report/daily.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/report/daily.ts#L2)

### 需注意
- 配置键:新增配置文件 config/app.yml(1 个键)

### 测试
怎么验证的:本地跑了 jest

**CR 情况(作者本地运行)** · crosscheck <VERSION> · standard
已修复 0 条;未修复的严重和重要 1 条;轻微 1 条未展开:
- [重要] [`api/orders.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/orders.ts#L1) 旧参数没兼容 — 未表态
基于提交 <SHA> · [完整版](https://pages.example.com/p/1)
<!-- crosscheck:end -->
