| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |
### 改了什么(另有 7 处未归组,见完整版)
订单列表改为游标分页
1. 改动 1 的 `listOrders`
2. 改动 2 的 `listOrders`
3. 改动 3 的 `listOrders`
4. 改动 4 的 `listOrders`
5. 改动 5 的 `listOrders`
6. 改动 6 的 `listOrders`
7. 改动 7 的 `listOrders`
8. 改动 8 的 `listOrders`
### 影响面
- 接口:删除路由 `GET /orders/legacy`(`api/routes.ts`);新增路由 `POST /orders/export`(`api/routes.ts:4`) 另 1 项
- 数据库与迁移:删除迁移 `db/001.sql`;新增迁移 `db/002.sql`
- 配置与依赖:删除配置键 `order.legacy`(`config/app.yml:5`);依赖变化 `package.json`(新增 axios@1.6.0、删除 left-pad 另 1 项) 另 2 项
- 内部函数:删除 `dropTwo`(`api/f2.ts`,diff 外 1 处调用);签名变化 `sigUsed`(`api/f4.ts:3`,diff 外 2 处调用) 另 2 项
- 未覆盖:另有 3 个函数只改了实现,行为变化不在此列
### 请重点看
1. `api/orders.ts:1` — 问题1
2. `api/orders.ts:2` — 问题2
### 怎么验证
- 请求 `POST /orders/export`(`api/routes.ts:4`)
- 命令行带上 `--cursor`(`bin/cli.ts:8`)
- 定时任务 `0 3 * * *` 到点触发(`jobs/nightly.ts:2`)
- 执行记录:作者提供:本地跑了 jest
### CR负责人:

@xx
