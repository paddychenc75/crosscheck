{{! PR/MR 描述(describe.md)的模板,由 scripts/describe.py 填充。影响面、怎么验证、链接、需求对照等事实都由脚本算好,模板只管结构和措辞;团队可以在自己的插件市场里改这个文件,适配自己的格式。}}
{{! 写法:名字外面套两层花括号是占位符,换成脚本算好的内容。井号开头的块(名字前加井号,结尾名字前加斜杠)有内容才保留,脱字符开头的块没有内容才保留,块里的占位符就是这个块自己的内容。感叹号开头的是注释,单独占一行时整行不输出。}}
{{! 占位符都是纯文本,已经带好链接和列表符号:summary(一句话)、basic(基本信息一行)、overview(影响面总览)、layers(影响面各层)、uncovered、rollback、focus(作者点名才有)、verify_note、trigger、expect、run、record、untested、requirements(有验收标准才有)、version、sha。}}
{{! 正文超过 20 行(不含首尾标记及负责人固定填写区)时,脚本按 需求对照改一行汇总、内部函数层只留数量、用例名、项目命令、空行、验证段的测试一行、预期 的顺序裁剪;总览和执行记录不裁。第一行和最后一行的标记要保留:贴进 PR/MR 描述后,靠它们分清哪一段是 crosscheck 生成的。}}
<!-- crosscheck:begin -->
**{{summary}}**
{{basic}}

### 影响面
{{overview}}
{{#layers}}
{{layers}}
{{/layers}}
{{#uncovered}}
- 未覆盖:{{uncovered}}
{{/uncovered}}
{{#rollback}}
- 回滚方式:{{rollback}}
{{/rollback}}

{{#focus}}
### 请重点看
{{focus}}

{{/focus}}
### 怎么验证({{verify_note}})
- 触发:{{trigger}}
{{#expect}}
- 预期:{{expect}}
{{/expect}}
{{#run}}
- 怎么跑:{{run}}
{{/run}}
- 执行记录:{{record}}
{{#untested}}
- 测试:{{untested}}
{{/untested}}

{{#requirements}}
### 需求对照
{{requirements}}

{{/requirements}}
### CR负责人:

@xx

由 crosscheck {{version}} 在本地生成 · 基于提交 {{sha}}
<!-- crosscheck:end -->
