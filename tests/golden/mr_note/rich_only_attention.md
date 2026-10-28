<!-- crosscheck:begin -->
**订单列表改为游标分页**
需求单:https://tickets.acme.example.com/story/42 「订单游标分页」 · 需求文档:未提供(作者跳过) · feature → main · 2 个文件 +5/−2 · 审查量 1/5

### 可能值得重点看(自动列出)
1. `api/orders.ts:1` — `listOrders` 签名变化,另有 2 处调用
2. `db/001.sql:1` — 新增列 y
3. `config/app.yml` — 读取 ORDER_X

### 阅读顺序
1. 接口和类型:`api/f1.ts`
2. 实现:`api/f2.ts`、`api/f3.ts`
可跳过:生成代码 1 个、lock 文件 1 个、纯格式化 1 个、weird 1 个

### 影响面
> 只按名字在本仓库文本匹配、只看签名变化;签名不变的行为变化不在这里,由 CR 报告负责
- `listOrders` 签名变化:本次之外 2 处调用 `report/daily.ts:1`、`report/daily.ts:2`

### 需注意
- 数据库迁移:db/001.sql 新增列 y
- 环境变量:config/app.yml 读取 ORDER_X
- 配置键:order.cursor.enabled
- 依赖变化:新增 lodash
- 另有 1 处,见完整版

**CR 情况(作者本地运行)** · crosscheck <VERSION> · standard
已修复 0 条;未修复的严重和重要 1 条;轻微 1 条未展开:
- [重要] `api/orders.ts:1` 旧参数没兼容 — 未表态
基于提交 <SHA>
<!-- crosscheck:end -->
