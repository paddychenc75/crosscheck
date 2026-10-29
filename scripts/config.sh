#!/bin/sh
# config.sh — 读取 crosscheck 的配置:插件里的 config/defaults.conf(团队统一),再用 ~/.crosscheck/config(个人)逐项覆盖。
#
# 用法: config.sh get KEY    打印 KEY 的值;没有这个设置时什么都不打印
#       config.sh path       打印个人配置文件的路径
#       config.sh plugin-path 打印插件配置文件(config/defaults.conf)的路径
#
# 配置文件每行一个 key=value,# 开头是注释,行尾的 " # ..." 也是注释,~/ 开头的路径会展开。
# 常用的设置:
#   gitlab_hosts=code.company.com,company.com/gitlab   按 GitLab 处理的域名(可带子路径)
#   model.finder=<模型>   查找角色用的模型(如更便宜的 sonnet / haiku);不写就用宿主当前的模型
#   model.verifier=<模型> 核实用的模型,最好和查找用的不同;调用时说「核实换成某某模型」会覆盖它
#   tools.code_host、tools.requirement、tools.doc_reader   外部工具:写给 agent 的一句话,说明用什么、怎么用
#   intent.required   审查前必须问到(可跳过)的需求类别(ticket、doc)
#   所有设置、用途和默认行为见 config/defaults.conf
# 个人配置的位置可以用环境变量 CROSSCHECK_CONFIG 覆盖,未设置时跟随 CROSSCHECK_HOME;
# 插件配置的位置可以用 CROSSCHECK_PLUGIN_CONFIG 覆盖。解析在插件 scripts/config.py 里,和其他脚本共用一份。

case "${1:-}" in
  get|path|plugin-path) exec python3 "$(cd "$(dirname "$0")" && pwd)/config.py" "$@" ;;
  *) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
