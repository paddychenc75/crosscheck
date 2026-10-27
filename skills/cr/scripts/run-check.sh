#!/bin/sh
# run-check.sh — verifier 在 deep 模式下运行检查、测试或复现时必须经过它,留下可核对的运行记录。
#
# 用法: run-check.sh DIR --kind test|check|repro -- 命令 参数...
#
#   DIR        collect-diff.sh 的输出目录(review_dir)
#   --kind     test:项目已有的测试;check:类型检查、linter 等;repro:仓库外写的最小复现
#   命令       必须拆成多个参数写在 -- 之后,不接受整条命令放进一个带引号的参数,也不接受 sh -c
#
# 在仓库根目录(meta.txt 的 repo_root)运行,超时 CR_RUN_TIMEOUT 秒(默认 120),设置 CI=1、
# PYTHONDONTWRITEBYTECODE=1。拒绝 git / gh / glab、安装依赖的命令,以及 --fix、--write、-u 这类会改写文件的参数。
# 写出 DIR/runs/<id>.log(完整输出)和 DIR/runs/<id>.json(命令、退出码、解析到的测试数、日志文件名)。
# 打印日志末尾,最后一行是 `CR-RUN id=<id> exit=<退出码>`,verifier 在结论里引用这个 id。
# 命令本身失败时本脚本仍以 0 退出(退出码在记录里);参数不合法时以 2 退出,不运行任何东西。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "run-check: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "run-check: 第一个参数应是 collect-diff.sh 的输出目录" >&2; exit 2; }
CR_DIR=$(cd "$1" && pwd)
shift
exec python3 "$(cd "$(dirname "$0")" && pwd)/run_check.py" "$CR_DIR" "$@"
