| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### 改了什么
订单列表改为游标分页
1. 改动:2 处(orders.ts、app.yml)

### 影响面
- 配置与依赖:新增配置文件 `config/app.yml`(1 个键)
- 内部函数:签名变化 `listOrders`(`api/orders.ts:1`,diff 外 2 处调用)

### 怎么验证
- 开启配置 `order.cursor.enabled`(`config/app.yml:3`)
- 执行记录:作者提供:本地跑了 jest

### CR负责人:

@xx
