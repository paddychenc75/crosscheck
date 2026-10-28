{{! MR 说明(mr.md)的模板,由 scripts/mr_note.py 填充。影响面、阅读顺序、链接、CR 情况等事实都由脚本算好,模板只管结构和措辞;团队可以在自己的插件市场里改这个文件,适配自己的 MR 格式。}}
{{! 写法:名字外面套两层花括号是占位符,换成脚本算好的内容。井号开头的块(名字前加井号,结尾名字前加斜杠)有内容才保留,脱字符开头的块没有内容才保留,块里的占位符就是这个块自己的内容。感叹号开头的是注释,单独占一行时整行不输出。}}
{{! 占位符都是纯文本,已经带好链接和列表符号:summary(一句话)、basic(基本信息一行)、focus_chosen(重点是否作者点名)、focus、read、skip、impact、attention、testing、tested、related_tests、version、depth、cr_summary、cr_findings、newer(审查后又有几个提交)、sha、page_url(网页版链接)。}}
{{! 整份超过 30 行时,脚本按 skip、testing、attention 的顺序整块去掉。第一行和最后一行的标记要保留:贴进 MR 描述后,靠它们分清哪一段是 crosscheck 生成的。}}
<!-- crosscheck:begin -->
{{#summary}}
**{{summary}}**
{{/summary}}
{{basic}}

{{#focus}}
### {{#focus_chosen}}请重点看{{/focus_chosen}}{{^focus_chosen}}可能值得重点看(自动列出){{/focus_chosen}}
{{focus}}

{{/focus}}
{{#read}}
### 阅读顺序
{{read}}
{{#skip}}
可跳过:{{skip}}
{{/skip}}

{{/read}}
{{#impact}}
### 影响面
> 只按名字在本仓库文本匹配、只看签名变化;签名不变的行为变化不在这里,由 CR 报告负责
{{impact}}

{{/impact}}
{{#attention}}
### 需注意
{{attention}}

{{/attention}}
{{#testing}}
### 测试
{{#tested}}
怎么验证的:{{tested}}
{{/tested}}
{{#related_tests}}
可能相关的测试文件(未执行):{{related_tests}}
{{/related_tests}}

{{/testing}}
**CR 情况(作者本地运行)** · crosscheck {{version}} · {{depth}}
{{cr_summary}}
{{#cr_findings}}
{{cr_findings}}
{{/cr_findings}}
{{#newer}}
> 审查之后又有 {{newer}} 个提交,未经 crosscheck 审查
{{/newer}}
基于提交 {{sha}}{{#page_url}} · [完整版]({{page_url}}){{/page_url}}
<!-- crosscheck:end -->
