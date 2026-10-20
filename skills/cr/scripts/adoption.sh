#!/bin/sh
# adoption.sh — 统计 MR 合并后,报过的问题在最终代码里是否真的解决了。
#
# 用法: adoption.sh scan [--repo DIR]
#         找出这个项目里已经合并的 PR/MR,只读拉取合并后的代码,对照每条报过的发现:
#           那段代码原样还在          → 记为「代码没改」(没有采纳)
#           文件已经不在了            → 记为「文件已删除」(不计入采纳率)
#           那段代码改了              → 列进 adoption-pending.json,交给 verifier 判断问题是否真的解决
#         判断合并:有 glab / gh 时读平台的状态;没有时看 MR 的最后一个提交是否已经进了目标分支。
#       adoption.sh record ID resolved|not_resolved [说明] [--repo DIR]
#         记下 verifier 的判断。
# 结果记在 ~/.crosscheck/reviews/<项目>/adoption.jsonl,stats.sh 据此算采纳率。需要 python3。
# 只做只读的 git fetch,不改工作区和本地分支。

set -eu

die() { printf 'adoption: %s\n' "$*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "需要 python3"
cmd=${1:-}; [ $# -gt 0 ] && shift
repo_arg=""; args=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo_arg=${2:-}; shift 2 ;;
    *) args="${args}$1
"; shift ;;
  esac
done
case "$cmd" in scan|record) ;; *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库,请用 --repo 指定"
u=$(git remote get-url origin 2>/dev/null || true)
if [ -n "$u" ]; then
  repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')
else
  repo_id=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd)")
fi

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_CMD=$cmd CR_ARGS=$args CR_REPO_ID=$repo_id CR_ROOT=$(git rev-parse --show-toplevel) python3 - <<'PY'
import datetime, json, os, re, shutil, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

cmd, repo_id, root = os.environ["CR_CMD"], os.environ["CR_REPO_ID"], os.environ["CR_ROOT"]
args = [a for a in os.environ["CR_ARGS"].split("\n") if a]
reviews = os.path.join(crlib.home(), "reviews")
pdir = None
for name in os.listdir(reviews) if os.path.isdir(reviews) else []:
    try:
        if open(os.path.join(reviews, name, ".repo"), encoding="utf-8").readline().strip() == repo_id:
            pdir = os.path.join(reviews, name); break
    except OSError:
        pass
if not pdir:
    print("这个项目还没有审查记录。"); sys.exit(0)
log = os.path.join(pdir, "adoption.jsonl")
done = {e.get("id"): e for e in crlib._read_jsonl(log)}
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def append(e):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

if cmd == "record":
    if len(args) < 2 or args[1] not in ("resolved", "not_resolved"):
        print("adoption: 用法 adoption.sh record ID resolved|not_resolved [说明]", file=sys.stderr); sys.exit(2)
    append({"id": args[0], "result": args[1], "note": args[2] if len(args) > 2 else "", "time": now, "by": "verifier"})
    print("已记录:%s → %s" % (args[0], "已解决" if args[1] == "resolved" else "改了但没解决"))
    sys.exit(0)

def run(cmdline, timeout=60):
    try:
        r = subprocess.run(cmdline, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

pending, decided, skipped = [], 0, []
for tname in sorted(os.listdir(pdir)):
    m = re.fullmatch(r"(mr|pr)-(\d+)", tname)
    tdir = os.path.join(pdir, tname)
    if not m or not os.path.isdir(tdir):
        continue
    try:
        latest = open(os.path.join(tdir, "latest"), encoding="utf-8").readline().strip()
    except OSError:
        continue
    rdir = os.path.join(tdir, latest)
    meta = crlib.read_kv(os.path.join(rdir, "meta.txt"))
    fs = [f for f in crlib.load_json(os.path.join(rdir, "findings.json"), []) if f.get("id") and f.get("id") not in done]
    if not fs:
        continue
    num, tb, head = m.group(2), meta.get("target_branch", ""), meta.get("head_sha", "")
    final = None
    if m.group(1) == "mr" and shutil.which("glab"):
        env_host = meta.get("gitlab_host", "")
        if env_host:
            os.environ["GITLAB_HOST"] = ("https://" + env_host) if "/" in env_host else env_host
        out = run(["glab", "api", "projects/%s/merge_requests/%s" % (meta.get("api_project") or ":id", num)])
        try:
            mr = json.loads(out) if out else {}
        except ValueError:
            mr = {}
        if mr.get("state") == "merged":
            final = mr.get("merge_commit_sha") or mr.get("squash_commit_sha") or (mr.get("diff_refs") or {}).get("head_sha")
            tb = mr.get("target_branch") or tb
        elif mr.get("state"):
            skipped.append("%s(%s)" % (tname, mr.get("state"))); continue
    elif m.group(1) == "pr" and shutil.which("gh"):
        out = run(["gh", "pr", "view", num, "--json", "state,mergeCommit,baseRefName"])
        try:
            pr = json.loads(out) if out else {}
        except ValueError:
            pr = {}
        if pr.get("state") == "MERGED":
            final = (pr.get("mergeCommit") or {}).get("oid"); tb = pr.get("baseRefName") or tb
        elif pr.get("state"):
            skipped.append("%s(%s)" % (tname, pr.get("state"))); continue
    if tb:
        run(["git", "-C", root, "fetch", "-q", "origin", tb])
    if not final and tb and head:
        if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", head, "origin/" + tb], capture_output=True).returncode == 0:
            final = "origin/" + tb
    if not final:
        skipped.append("%s(未合并或无法判断)" % tname); continue
    if subprocess.run(["git", "-C", root, "cat-file", "-e", final + "^{commit}"], capture_output=True).returncode != 0:
        run(["git", "-C", root, "fetch", "-q", "origin", final])
    src = crlib.Source(root, final)
    for f in fs:
        text = src.read(f.get("file", ""))
        if text is None:
            append({"id": f["id"], "result": "file_removed", "mr": tname, "time": now, "by": "script"}); decided += 1; continue
        ls = text.split("\n")
        same = f.get("context_hash") and any(crlib.ctx_hash(ls, i + 1) == f["context_hash"] for i, x in enumerate(ls) if crlib.norm(x) == crlib.norm(f.get("code_line", "")))
        if same:
            append({"id": f["id"], "result": "unchanged", "mr": tname, "time": now, "by": "script"}); decided += 1; continue
        n = int(f.get("line") or 1)
        lo, hi = max(1, n - 12), min(len(ls), n + 12)
        pending.append({"id": f["id"], "mr": tname, "file": f.get("file"), "line": f.get("line"), "category": f.get("category"),
                        "summary": f.get("summary"), "failure_scenario": f.get("failure_scenario"), "final_ref": final,
                        "final_excerpt": "\n".join("%5d | %s" % (i, ls[i - 1]) for i in range(lo, hi + 1))})
crlib.save_json(os.path.join(pdir, "adoption-pending.json"), pending)
print("自动判定 %d 条(代码没改或文件已删除);需要 verifier 确认的 %d 条:%s" % (decided, len(pending), os.path.join(pdir, "adoption-pending.json")))
if skipped:
    print("跳过:" + "、".join(skipped))
PY
