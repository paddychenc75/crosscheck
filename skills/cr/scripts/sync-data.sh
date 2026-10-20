#!/bin/sh
# sync-data.sh — 把人的判断同步到团队的数据仓库(可选)。只在用户要求时运行,审查过程中从不调用。
#
# 用法: sync-data.sh status         有多少条本机判断还没推送
#       sync-data.sh pull           拉取团队的判断记录
#       sync-data.sh push [--yes]   先拉取,再把本机新增的判断推送上去;不带 --yes 时只列出要推送的条数
#
# 读 ~/.crosscheck/config 里的 data_repo=<地址> 和 data_dir=<本地克隆的位置>(默认 ~/.crosscheck/data)。
# 数据仓库里按项目、月份、人分文件,只追加不修改:
#   feedback/<项目>/<年-月>/<判断人的哈希>.jsonl
# 多人同时同步也不会冲突;撤回一条判断是追加一条新记录。判断里有代码片段和原因,
# 数据仓库的访问权限要和代码仓库一致。人名只存哈希,不存任何凭证。需要 python3。

set -eu

die() { printf 'sync-data: %s\n' "$*" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(sh "$here/config.sh" get data_repo)
dir=$(sh "$here/config.sh" get data_dir); dir=${dir:-${CROSSCHECK_HOME:-$HOME/.crosscheck}/data}
safe() { printf '%s\n' "$1" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##'; }
cmd=${1:-}; yes=no
[ "${2:-}" = "--yes" ] && yes=yes
case "$cmd" in
  status|pull|push) ;;
  *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
command -v python3 >/dev/null 2>&1 || die "需要 python3"
[ -n "$repo" ] || die "没有配置 data_repo。团队要共享判断时,在 $(sh "$here/config.sh" path) 里加一行 data_repo=<数据仓库的地址>;不共享也能正常使用"

pull() {
  if [ ! -d "$dir/.git" ]; then
    mkdir -p "$(dirname "$dir")"
    git clone -q "$repo" "$dir" >/dev/null 2>&1 || die "无法克隆 $(safe "$repo")(地址、权限或网络问题)"
  else
    git -C "$dir" pull -q --ff-only >/dev/null 2>&1 || die "无法从 $(safe "$repo") 拉取(网络、权限,或本地副本有未推送的改动)"
  fi
}

pending() { # 打印「项目<TAB>待推送条数」;--write 时把它们写进数据仓库的工作区
  CR_SCRIPTS="$here" CR_DATA="$dir" CR_WRITE="${1:-}" python3 - <<'PY'
import json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
reviews = os.path.join(crlib.home(), "reviews")
data, write = os.environ["CR_DATA"], os.environ["CR_WRITE"] == "--write"
total = 0
for name in sorted(os.listdir(reviews)) if os.path.isdir(reviews) else []:
    pdir = os.path.join(reviews, name)
    fb = os.path.join(pdir, "feedback.jsonl")
    if not os.path.isfile(fb):
        continue
    try:
        repo_id = open(os.path.join(pdir, ".repo"), encoding="utf-8").readline().strip()
    except OSError:
        continue
    lines = open(fb, encoding="utf-8").read().splitlines()
    try:
        done = int(open(os.path.join(pdir, ".feedback-synced")).read().strip() or 0)
    except (OSError, ValueError):
        done = 0
    new = [l for l in lines[done:] if l.strip()]
    if not new:
        continue
    print("%s\t%d" % (name, len(new)))
    total += len(new)
    if write:
        for l in new:
            try:
                e = json.loads(l)
            except ValueError:
                continue
            month = str(e.get("time", ""))[:7] or "unknown"
            who = e.get("author") or crlib.author_hash()
            out = os.path.join(data, "feedback", crlib.project_key(repo_id), month, who + ".jsonl")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        with open(os.path.join(pdir, ".feedback-synced.pending"), "w") as f:
            f.write(str(len(lines)))
print("total\t%d" % total)
PY
}

case "$cmd" in
  pull)
    pull
    echo "ok: 已拉取团队的判断记录($dir)" ;;
  status)
    pending ;;
  push)
    pull
    if [ "$yes" != yes ]; then
      pending
      echo "以上是要推送的条数。确认后运行 sync-data.sh push --yes"
      exit 0
    fi
    out=$(pending --write)
    n=$(printf '%s\n' "$out" | awk -F '\t' '$1 == "total" { print $2 }')
    if [ "${n:-0}" -eq 0 ]; then echo "没有需要推送的判断"; exit 0; fi
    git -C "$dir" add -A feedback
    git -C "$dir" commit -q -m "crosscheck: 同步判断记录 $n 条" || die "提交失败"
    if ! git -C "$dir" push -q >/dev/null 2>&1; then
      git -C "$dir" reset -q --hard HEAD~1 2>/dev/null || true
      find "${CROSSCHECK_HOME:-$HOME/.crosscheck}/reviews" -name .feedback-synced.pending -delete 2>/dev/null || true
      die "推送到 $(safe "$repo") 失败(权限或网络),这次没有同步,本机记录不受影响"
    fi
    for p in "${CROSSCHECK_HOME:-$HOME/.crosscheck}"/reviews/*/.feedback-synced.pending; do
      [ -f "$p" ] && mv "$p" "${p%.pending}"
    done
    echo "ok: 已推送 $n 条判断" ;;
esac
