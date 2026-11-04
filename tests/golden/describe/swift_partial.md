| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### CR负责人: @reviewer.username

### 改了什么
订单列表改为游标分页
1. 图片等资源 1 个文件
2. 改动:4 处(XViewController.swift、YView.swift、Info.plist)

### 主要影响
- 配置与依赖:修改工程配置 `App/Info.plist`
- 配置与依赖:依赖变化 `Podfile`

> 引用校验只确认出处,不代表业务已验证;验证建议未经执行确认。
> 缺少可用分析,仅展示确定事实和未归组代码块。
> 分析缺项:analyst.json — 缺少分析
