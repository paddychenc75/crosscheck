<!-- crosscheck:begin -->
**订单列表改为游标分页**
规模:2 个文件 +5/−2 · 分支:feature → main · 需求单:https://tickets.acme.example.com/story/42 「订单游标分页」

### 影响面
删除 4 · 迁移 2 · 接口 3 · 配置 4 · 函数 4(签名变 2)
- 接口:删除路由 `GET /orders/legacy`([`api/routes.ts`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts));新增路由 `POST /orders/export`([`api/routes.ts:4`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts#L4)) 另 1 项
- 数据库与迁移:删除迁移 [`db/001.sql`](http://localhost:8080/eng/shop/-/blob/<SHA>/db/001.sql);新增迁移 [`db/002.sql`](http://localhost:8080/eng/shop/-/blob/<SHA>/db/002.sql)
- 配置、环境变量与依赖:删除配置键 `order.legacy`([`config/app.yml:5`](http://localhost:8080/eng/shop/-/blob/<SHA>/config/app.yml#L5));依赖变化 [`package.json`](http://localhost:8080/eng/shop/-/blob/<SHA>/package.json)(新增 axios@1.6.0、删除 left-pad 另 1 项) 另 2 项
- 内部函数:删除 `dropTwo`([`api/f2.ts`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/f2.ts),diff 外 1 处调用);签名变化 `sigUsed`([`api/f4.ts:3`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/f4.ts#L3),diff 外 2 处调用) 另 2 项
- 未覆盖:另有 3 个函数只改了实现,行为变化不在此列
- 回滚方式:待补充

### 怎么验证(由代码推断,未执行)
- 触发:请求 `POST /orders/export`([`api/routes.ts:4`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts#L4))、命令行带上 `--cursor`([`bin/cli.ts:8`](http://localhost:8080/eng/shop/-/blob/<SHA>/bin/cli.ts#L8))、定时任务 `0 3 * * *` 到点触发([`jobs/nightly.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/jobs/nightly.ts#L2)) 另 1 个;`sigUsed` 的 diff 外调用方 [`use/c4.ts:10`](http://localhost:8080/eng/shop/-/blob/<SHA>/use/c4.ts#L10)、[`use/c4.ts:11`](http://localhost:8080/eng/shop/-/blob/<SHA>/use/c4.ts#L11)
- 预期:新增断言 `expect(x).toBe(1)`([`tests/orders.test.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/tests/orders.test.ts#L1))、`expect(x).toBe(2)`([`tests/orders.test.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/tests/orders.test.ts#L2)) 另 1 条
- 怎么跑:`npm test`(项目命令,未确认已执行);新增用例「case 1」、「case 2」、「case 3」、「case 4」、「case 5」 另 2 个
- 执行记录:作者提供:本地跑了 jest
- 测试:2 个新增/签名变化的函数在改动的测试里没有出现:`addOne`、`sigUsed`

### CR负责人:

@xx

由 crosscheck <VERSION> 在本地生成 · 基于提交 <SHA>
<!-- crosscheck:end -->
