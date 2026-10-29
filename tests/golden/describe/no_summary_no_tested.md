**分支 feature 的改动**
- 需求单:[订单游标分页](https://tickets.acme.example.com/story/42)
- 规模:2 个文件 +5/−2 · `feature` → `main`

### 改了什么
1. 改动:2 处(orders.ts、app.yml)

### 影响面
- 配置与依赖:新增配置文件 `config/app.yml`(1 个键)
- 内部函数:签名变化 `listOrders`(`api/orders.ts:1`,diff 外 2 处调用)

### 怎么验证(由代码推断,未执行)
- 开启配置 `order.cursor.enabled`(`config/app.yml:3`)

### CR负责人:

@xx
