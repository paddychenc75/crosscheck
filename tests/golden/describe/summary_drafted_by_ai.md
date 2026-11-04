| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### CR负责人: @reviewer.username

### 改了什么
起草的一句话
1. 改动:2 处(orders.ts、app.yml)

### 主要影响
- 配置与依赖:新增配置文件 `config/app.yml`(1 个键)
- 函数签名:签名变化 `listOrders`(`api/orders.ts:1`,diff 外 2 处调用)

### 怎么验证
- 执行记录:作者提供:本地跑了 jest

> 引用校验只确认出处,不代表业务已验证;验证建议未经执行确认。
