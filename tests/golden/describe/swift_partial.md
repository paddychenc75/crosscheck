| 资料 | 链接 |
|---|---|
| 需求单 | [订单游标分页](https://tickets.acme.example.com/story/42) |
| 需求文档 |  |
| 设计稿 |  |

### 改了什么
订单列表改为游标分页
1. 图片等资源 1 个文件
2. 改动:4 处(XViewController.swift、YView.swift、Info.plist)

### 影响面
- 配置与依赖:修改工程配置 `App/Info.plist`;依赖变化 `Podfile`
- 资源:多语言文案 1 个文件(zh-Hans);图片等资源 2 个文件
- 接口、迁移、签名:已检查 3 个文件,没有认出;另有 3 个 Swift、Objective-C 文件不在自动识别范围,未检查

### CR负责人:

@xx
