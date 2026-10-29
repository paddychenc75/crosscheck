<!-- crosscheck:begin -->
**订单列表改为游标分页**
规模:2 个文件 +5/−2 · 分支:feature → main · 需求单:https://tickets.acme.example.com/story/42 「订单游标分页」
### 影响面
删除 4 · 迁移 2 · 接口 3 · 配置 4 · 函数 4(签名变 2)
- 接口:删除路由 `GET /orders/legacy`(`api/routes.ts`);新增路由 `POST /orders/export`(`api/routes.ts:4`) 另 1 项
- 数据库与迁移:删除迁移 `db/001.sql`;新增迁移 `db/002.sql`
- 配置、环境变量与依赖:删除配置键 `order.legacy`(`config/app.yml:5`);依赖变化 `package.json`(新增 axios@1.6.0、删除 left-pad 另 1 项) 另 2 项
- 未覆盖:另有 3 个函数只改了实现,行为变化不在此列
- 回滚方式:待补充
### 请重点看
1. `api/orders.ts:1` — 问题1
2. `api/orders.ts:2` — 问题2
3. `api/orders.ts:3` — 问题3
### 怎么验证(由代码推断,未执行)
- 触发:请求 `POST /orders/export`(`api/routes.ts:4`)、命令行带上 `--cursor`(`bin/cli.ts:8`)、定时任务 `0 3 * * *` 到点触发(`jobs/nightly.ts:2`) 另 1 个;`sigUsed` 的 diff 外调用方 `use/c4.ts:10`、`use/c4.ts:11`
- 预期:验收标准「条件 1(来自 需求单)」「条件 2(来自 需求单)」 另 4 条
- 执行记录:作者提供:本地跑了 jest
### 需求对照
- 6 条验收标准,1 条标出了对应改动,逐条见完整版
### CR负责人:

@xx

由 crosscheck <VERSION> 在本地生成 · 基于提交 <SHA>
<!-- crosscheck:end -->
