| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### CR负责人: @reviewer.username

### 改了什么
订单列表改为游标分页
1. 改动:2 处(orders.ts、app.yml)

### 主要影响
- 接口:删除路由 `GET /orders/legacy`(`api/routes.ts`)
- 接口:新增路由 `POST /orders/export`(`api/routes.ts:4`)
- 接口:新增命令行参数 `--cursor`(`bin/cli.ts:8`)
- 数据库与迁移:删除迁移 `db/001.sql`
- 数据库与迁移:新增迁移 `db/002.sql`
- 配置与依赖:删除配置键 `order.legacy`(`config/app.yml:5`)
- 配置与依赖:依赖变化 `package.json`(新增 axios@1.6.0、删除 left-pad、lodash 4.0.0 → 4.17.0)
- 配置与依赖:新增配置键 `order.cursor.enabled`(`config/app.yml:3`)
- 配置与依赖:新环境变量 `ORDER_API_URL`(`src/env.ts:1`)
- 函数签名:删除 `dropTwo`(`api/f2.ts`,diff 外 1 处调用)
- 函数签名:签名变化 `sigUsed`(`api/f4.ts:3`,diff 外 2 处调用)
- 函数签名:签名变化 `sigNoUse`(`api/f3.ts:3`)

### 怎么验证
- 执行记录:作者提供:本地跑了 jest

> 引用校验只确认出处,不代表业务已验证;验证建议未经执行确认。
> 缺少可用分析,仅展示确定事实和未归组代码块。
> 分析缺项:analyst.json — 缺少分析
