<!-- crosscheck:begin -->
**订单列表改为游标分页**
规模:2 个文件 +5/−2 · 分支:feature → main · 需求单:https://tickets.acme.example.com/story/42 「订单游标分页」 · 状态:已合入 main,描述已无法影响合入 · PR/MR 信息:没有读到(tools.code_host:glab 未登录)

### 影响面
配置 1 · 函数 1(签名变 1)
- 配置、环境变量与依赖:新增配置文件 `config/app.yml`(1 个键)
- 内部函数:签名变化 `listOrders`(`api/orders.ts:1`,diff 外 2 处调用)

### 怎么验证(由代码推断,未执行)
- 触发:开启配置 `order.cursor.enabled`(`config/app.yml:3`);`listOrders` 的 diff 外调用方 `report/daily.ts:1`、`report/daily.ts:2`
- 执行记录:作者提供:本地跑了 jest

### CR负责人:

@xx

由 crosscheck <VERSION> 在本地生成 · 基于提交 <SHA>
<!-- crosscheck:end -->
