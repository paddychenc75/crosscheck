#!/bin/sh
# config.sh — 读取 crosscheck 的配置文件(默认 ~/.crosscheck/config)。
#
# 用法: config.sh get KEY    打印 KEY 的值;没有这个设置时什么都不打印
#       config.sh path       打印配置文件的路径
#
# 配置文件每行一个 key=value,# 开头是注释,行尾的 " # ..." 也是注释,~/ 开头的路径会展开。
# 常用的设置:
#   gitlab_hosts=code.company.com,company.com/gitlab   按 GitLab 处理的域名(可带子路径)
#   issue_cmd=<命令>    读取非 GitLab 需求单的命令,{key} 会替换成需求编号(可选)
#   model.finder=<模型>   查找角色用的模型(如更便宜的 sonnet / haiku);不写就用宿主当前的模型
#   model.verifier=<模型> 核实用的模型,最好和查找用的不同;调用时说「核实换成某某模型」会覆盖它
# 位置可以用环境变量 CROSSCHECK_CONFIG 覆盖;未设置时跟随 CROSSCHECK_HOME。

set -eu

file="${CROSSCHECK_CONFIG:-${CROSSCHECK_HOME:-$HOME/.crosscheck}/config}"
case "${1:-}" in
  path) printf '%s\n' "$file"; exit 0 ;;
  get) [ $# -ge 2 ] || { echo "config: 用法 config.sh get KEY" >&2; exit 2; } ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
[ -f "$file" ] || exit 0
awk -v key="$2" -v home="$HOME" '
  /^[ \t]*#/ || !/=/ { next }
  {
    k = $0; sub(/=.*/, "", k); gsub(/^[ \t]+|[ \t]+$/, "", k)
    if (k != key) next
    v = $0; sub(/^[^=]*=/, "", v); sub(/[ \t]+#.*$/, "", v); gsub(/^[ \t]+|[ \t]+$/, "", v)
    if (v ~ /^~\//) v = home substr(v, 2)
    val = v
  }
  END { if (val != "") print val }' "$file"
