**订单列表改为游标分页**
- 需求单:[订单游标分页](https://tickets.acme.example.com/story/42)
- 规模:2 个文件 +5/−2 · `feature` → `main`

### 改了什么
1. 改动:2 处(orders.ts、app.yml)

### 影响面
- 配置与依赖:新增配置文件 [`config/app.yml`](http://localhost:8080/eng/shop/-/blob/<SHA>/config/app.yml)(1 个键)
- 内部函数:签名变化 `listOrders`([`api/orders.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/orders.ts#L1),diff 外 2 处调用)

### 请重点看
1. [`api/orders.ts:1`](http://localhost:8080/eng/shop/-/blob/<SHA>/api/orders.ts#L1) — 兼容期够不够
2. [`report/daily.ts:2`](http://localhost:8080/eng/shop/-/blob/<SHA>/report/daily.ts#L2) — 调用方要不要一起改

### 怎么验证(由代码推断,未执行)
- 开启配置 `order.cursor.enabled`([`config/app.yml:3`](http://localhost:8080/eng/shop/-/blob/<SHA>/config/app.yml#L3))
- 执行记录:作者提供:本地跑了 jest

### CR负责人:

@xx
