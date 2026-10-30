| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### 改了什么
订单列表改为游标分页
1. 改动:2 处(orders.ts、app.yml)

### 影响面
- 接口:删除路由 `GET /orders/legacy`([`api/routes.ts`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts));新增路由 `POST /orders/export`([`api/routes.ts:4`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts#L4)) 另 1 项
- 数据库与迁移:删除迁移 [`db/001.sql`](http://localhost:8080/eng/shop/-/blob/<SHA>/db/001.sql);新增迁移 [`db/002.sql`](http://localhost:8080/eng/shop/-/blob/<SHA>/db/002.sql)
- 配置与依赖:删除配置键 `order.legacy`([`config/app.yml:5`](http://localhost:8080/eng/shop/-/blob/<SHA>/config/app.yml#L5));依赖变化 [`package.json`](http://localhost:8080/eng/shop/-/blob/<SHA>/package.json)(新增 axios@1.6.0、删除 left-pad 另 1 项) 另 2 项
- 内部函数:删除 `dropTwo`([`api/f2.ts`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/f2.ts),diff 外 1 处调用);签名变化 `sigUsed`([`api/f4.ts:3`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/f4.ts#L3),diff 外 2 处调用) 另 2 项
- 未覆盖:另有 3 个函数只改了实现,行为变化不在此列

### 怎么验证
- 请求 `POST /orders/export`([`api/routes.ts:4`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/routes.ts#L4))
- 命令行带上 `--cursor`([`bin/cli.ts:8`](http://localhost:8080/eng/shop/-/blob/<SHA>/bin/cli.ts#L8))
- 定时任务 `0 3 * * *` 到点触发([`jobs/nightly.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/jobs/nightly.ts#L2))
- 开启配置 `order.cursor.enabled`([`config/app.yml:3`](http://localhost:8080/eng/shop/-/blob/<SHA>/config/app.yml#L3))
- 执行记录:作者提供:本地跑了 jest

### CR负责人:

@xx
