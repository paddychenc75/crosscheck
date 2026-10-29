**订单列表改为游标分页**
- 需求单:[订单游标分页](https://tickets.acme.example.com/story/42)
- 规模:2 个文件 +5/−2 · `feature` → `main`

### 改了什么
1. `listOrders` 改收 `cursor` 参数(H1)
2. 其他:2 处(app.yml、export.ts)

### 影响面
- 配置与依赖:新增配置文件 `config/app.yml`(1 个键)
- 内部函数:签名变化 `listOrders`(`api/orders.ts:1`,diff 外 2 处调用)

### 怎么验证(由代码推断,未执行)
- 开启配置 `order.cursor.enabled`(`config/app.yml:3`)
- 订单列表翻页 → 请求带 `cursor`
- 执行记录:作者提供:本地跑了 jest

### CR负责人:

@xx
