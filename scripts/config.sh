#!/bin/sh
# config.sh — 读取 crosscheck 的配置:插件里的 config/defaults.conf(团队统一),再用 ~/.crosscheck/config(个人)逐项覆盖。
#
# 用法: config.sh get KEY    打印 KEY 的值;没有这个设置时什么都不打印
#       config.sh path       打印个人配置文件的路径
#       config.sh plugin-path 打印插件配置文件(config/defaults.conf)的路径
#       config.sh home       打印记录目录(~/.crosscheck)
#       config.sh probe      在 ~/.crosscheck 下试写并删除一个探测文件;写不了时说明怎么申请写入权限,以 77 退出
#       config.sh mkdir DIR  建记录目录;失败时同 probe
#
# 配置文件每行一个 key=value,# 开头是注释,行尾的 " # ..." 也是注释,~/ 开头的路径会展开。
# 常用的设置:
#   gitlab_hosts=code.company.com,company.com/gitlab   按 GitLab 处理的域名(可带子路径)
#   model.finder=<模型>   查找角色用的模型(如更便宜的 sonnet / haiku);不写就用宿主当前的模型
#   model.verifier=<模型> 核实用的模型,最好和查找用的不同;调用时说「核实换成某某模型」会覆盖它
#   tools.code_host、tools.requirement、tools.doc_reader   外部工具:写给 agent 的一句话,说明用什么、怎么用
#   review.ask、describe.ask   各技能开始前要问的资料类别(requirement、doc);没配对应读取工具的类别不问
#   所有设置、用途和默认行为见 config/defaults.conf
# 记录目录固定是 ~/.crosscheck,个人配置固定是 ~/.crosscheck/config;
# 插件配置的位置可以用 CROSSCHECK_PLUGIN_CONFIG 覆盖。解析在插件 scripts/config.py 里,和其他脚本共用一份。

case "${1:-}" in
  get|path|plugin-path|home|probe|mkdir) exec python3 "$(cd "$(dirname "$0")" && pwd)/config.py" "$@" ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
