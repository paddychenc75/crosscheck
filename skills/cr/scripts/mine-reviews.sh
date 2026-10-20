#!/bin/sh
# mine-reviews.sh — 为每月的规则提炼收集材料(只读)。由 rule-miner 角色使用。
#
# 用法: mine-reviews.sh [--since 30d | --mr 128,140] [--repo DIR] [--out DIR]
#
#   --since Nd   最近 N 天合并的 MR(默认 30d);--mr 指定编号
#   需要 glab(公网 GitLab 或私有化 GitLab 都可以,私有化的域名写在配置的 gitlab_hosts 里)。
#
# 写入输出目录(默认 ~/.crosscheck/knowledge/<项目>/mined-<日期>/):
#   comments.jsonl          挂在 diff 行上的人工评论:去掉系统消息、机器人、「LGTM」「+1」这类没有信息量的;
#                           每条带 MR 编号、文件、行号、评论人(只存哈希)、是否已解决、之后那几行代码是否被改过
#                           (code_changed_after),以及 crosscheck 当时有没有报过这里(matched_finding)
#   misses.json             crosscheck 审过这个 MR、人工指出了、但当时没报的——候选的漏报,确认后可以进评测集
#   feedback-summary.json   这个项目的判断记录汇总:反复出现的「不成立」原因、反复被判为成立的同类问题
#   closed-candidates.json  被关闭的 crosscheck-rule MR(标题、说明、最后一条评论),提炼时不要再提同样的规则
#   existing-rules.md       现有的规则(REVIEW.md、.cr/ignore.md、引用的组织规则包),用于去重
#   summary.txt             计数
# 只做只读的 API 请求和 git fetch,不改工作区。需要 python3。

set -eu

die() { printf 'mine-reviews: %s\n' "$*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "需要 python3"
command -v glab >/dev/null 2>&1 || die "需要 glab(并且已经登录)。安装后运行 glab auth login;私有化 GitLab 用 glab auth login --hostname <域名>"
since=30d; mrs=""; repo_arg=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) since=${2:-30d}; shift 2 ;;
    --mr) mrs=${2:-}; shift 2 ;;
    --repo) repo_arg=${2:-}; shift 2 ;;
    --out) out=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库,请用 --repo 指定"
root=$(git rev-parse --show-toplevel)
u=$(git remote get-url origin 2>/dev/null || true)
[ -n "$u" ] || die "仓库没有 origin 远程"
repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_ROOT=$root CR_REPO_ID=$repo_id CR_SINCE=$since CR_MRS=$mrs CR_OUT=$out python3 - <<'PY'
import collections, datetime, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

root, repo_id = os.environ["CR_ROOT"], os.environ["CR_REPO_ID"]
cfg = crlib.config()
host = None
for h in (cfg.get("gitlab_hosts", "") or "").split(","):
    h = re.sub(r"^[A-Za-z]+://", "", h.strip()).rstrip("/")
    if h and (repo_id == h or repo_id.startswith(h + "/")):
        if host is None or len(h) > len(host):
            host = h
env = dict(os.environ)
if host:
    env["GITLAB_HOST"] = ("https://" + host) if "/" in host else host
    proj_path = repo_id[len(host) + 1:]
else:
    proj_path = repo_id.split("/", 1)[1] if "/" in repo_id else repo_id
proj = proj_path.replace("/", "%2F")

def api(path):
    try:
        r = subprocess.run(["glab", "api", path], capture_output=True, text=True, timeout=60, env=env, cwd=root)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None

def git(*args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)

# 项目的记录目录
reviews = os.path.join(crlib.home(), "reviews")
pdir, pname = None, re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(repo_id))
for name in os.listdir(reviews) if os.path.isdir(reviews) else []:
    try:
        if open(os.path.join(reviews, name, ".repo"), encoding="utf-8").readline().strip() == repo_id:
            pdir, pname = os.path.join(reviews, name), name; break
    except OSError:
        pass
out = os.environ["CR_OUT"] or os.path.join(crlib.home(), "knowledge", pname, "mined-" + datetime.date.today().strftime("%Y%m%d"))
os.makedirs(out, exist_ok=True)

# 要看的 MR
mr_list = []
if os.environ["CR_MRS"]:
    for n in re.findall(r"\d+", os.environ["CR_MRS"]):
        m = api("projects/%s/merge_requests/%s" % (proj, n))
        if isinstance(m, dict) and m.get("iid"):
            mr_list.append(m)
else:
    days = int(re.sub(r"\D", "", os.environ["CR_SINCE"]) or 30)
    after = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for page in range(1, 4):
        ms = api("projects/%s/merge_requests?state=merged&updated_after=%s&per_page=100&page=%d" % (proj, after, page))
        if not isinstance(ms, list) or not ms:
            break
        mr_list.extend(ms)
if not mr_list:
    print("没有读到 MR。确认 glab 已登录、项目路径正确(%s),以及这段时间内有已合并的 MR。" % proj_path)
    sys.exit(1)

TRIVIAL = re.compile(r"^(lgtm|\+1|👍|ok|okay|done|好的|可以|收到|已改|已修改|fixed|thanks?|谢谢|nit\.?)[!！。.\s]*$", re.I)
BOT = re.compile(r"(^|[-_])bot$|^ghost$|^project_\d+_bot", re.I)

def lines_at(ref, path):
    r = git("show", "%s:%s" % (ref, path))
    return r.stdout.split("\n") if r.returncode == 0 else None

def record_findings(iid):
    tdir = os.path.join(pdir, "mr-%s" % iid) if pdir else ""
    found = []
    if tdir and os.path.isdir(tdir):
        for stamp in os.listdir(tdir):
            rdir = os.path.join(tdir, stamp)
            for f in crlib.load_json(os.path.join(rdir, "findings.json"), []):
                found.append(("finding", f))
            for f in crlib.load_json(os.path.join(rdir, "rejected.json"), []):
                found.append(("rejected", f))
    return found

comments, misses = [], []
for mr in mr_list:
    iid = mr.get("iid")
    final_head = (mr.get("diff_refs") or {}).get("head_sha") or mr.get("sha")
    git("fetch", "-q", "origin", "merge-requests/%s/head" % iid)
    known = record_findings(iid)
    reviewed = bool(known) or (pdir and os.path.isdir(os.path.join(pdir, "mr-%s" % iid)))
    discs = api("projects/%s/merge_requests/%s/discussions?per_page=100" % (proj, iid)) or []
    for disc in discs if isinstance(discs, list) else []:
        for n in disc.get("notes") or []:
            author = n.get("author") or {}
            uname = str(author.get("username", ""))
            body = (n.get("body") or "").strip()
            pos = n.get("position") or {}
            if n.get("system") or author.get("bot") or BOT.search(uname) or n.get("type") != "DiffNote" or not pos:
                continue
            if len(body) < 6 or TRIVIAL.match(body):
                continue
            path = pos.get("new_path") or pos.get("old_path")
            line = pos.get("new_line") or pos.get("old_line")
            changed_after = None
            if path and line and final_head:
                old = lines_at(pos.get("head_sha") or "", path) if pos.get("head_sha") else None
                new = lines_at(final_head, path)
                if old and 1 <= int(line) <= len(old):
                    if new is None:
                        changed_after = True
                    else:
                        h = crlib.ctx_hash(old, int(line))
                        changed_after = not any(crlib.ctx_hash(new, i + 1) == h for i in range(len(new)))
            match = None
            for kind, f in known:
                try:
                    if f.get("file") == path and abs(int(f.get("line", 0)) - int(line or 0)) <= 3:
                        match = f.get("id") if kind == "finding" else "rejected:%s" % f.get("confidence")
                        if kind == "finding":
                            break
                except (TypeError, ValueError):
                    pass
            c = {"mr": iid, "file": path, "line": line, "body": body[:1500], "reviewer_hash": crlib.short_hash(uname),
                 "resolved": bool(n.get("resolved")), "code_changed_after": changed_after, "matched_finding": match,
                 "reviewed_by_crosscheck": bool(reviewed), "created_at": n.get("created_at", ""), "url": mr.get("web_url", "")}
            comments.append(c)
            if reviewed and (match is None or str(match).startswith("rejected:")):
                misses.append(c)

with open(os.path.join(out, "comments.jsonl"), "w", encoding="utf-8") as f:
    for c in comments:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
crlib.save_json(os.path.join(out, "misses.json"), misses)

# 判断记录汇总
fb = list(crlib.feedback_entries(pdir, repo_id).values()) if pdir else []
neg = collections.defaultdict(list)
pos_ = collections.defaultdict(list)
for e in fb:
    k = (e.get("category", "?"), crlib.norm(e.get("reason", ""))[:80])
    if e.get("verdict") in ("invalid", "wontfix"):
        neg[k].append({"file": e.get("file"), "summary": e.get("summary"), "verdict": e.get("verdict"), "time": e.get("time")})
    elif e.get("verdict") == "valid":
        pos_[e.get("category", "?")].append({"file": e.get("file"), "summary": e.get("summary"), "rule_id": e.get("rule_id", "")})
crlib.save_json(os.path.join(out, "feedback-summary.json"), {
    "not_valid": [{"category": k[0], "reason": k[1], "count": len(v), "examples": v[:5]} for k, v in sorted(neg.items(), key=lambda kv: -len(kv[1]))],
    "valid": [{"category": k, "count": len(v), "examples": v[:10]} for k, v in pos_.items()],
})

# 被关闭的规则 MR
closed = []
for m in api("projects/%s/merge_requests?state=closed&labels=crosscheck-rule&per_page=50" % proj) or []:
    notes = api("projects/%s/merge_requests/%s/notes?sort=desc&per_page=20" % (proj, m.get("iid"))) or []
    last = next((x.get("body", "") for x in notes if not x.get("system")), "")
    closed.append({"iid": m.get("iid"), "title": m.get("title"), "description": (m.get("description") or "")[:800],
                   "last_comment": last[:800], "url": m.get("web_url")})
crlib.save_json(os.path.join(out, "closed-candidates.json"), closed)

# 现有规则
src = crlib.Source(root)
parts = []
for f in sorted(x for x in src.files() if x == "REVIEW.md" or x.endswith("/REVIEW.md")) + [".cr/ignore.md"]:
    t = src.read(f)
    if t:
        parts.append("=== %s ===\n%s" % (f, t))
t = src.read("REVIEW.md")
rd = crlib.rules_dir()
for pack in crlib.as_list(crlib.parse_frontmatter(t)[0].get("extends")) if t else []:
    p = os.path.join(rd, "packs", pack, "rules.md") if rd else ""
    if p and os.path.isfile(p):
        parts.append("=== 组织规则包 %s ===\n%s" % (pack, open(p, encoding="utf-8").read()))
with open(os.path.join(out, "existing-rules.md"), "w", encoding="utf-8") as f:
    f.write("\n\n".join(parts) + "\n")

summary = "MR %d 个 · 人工评论 %d 条(挂在改动行上、过滤后) · 其中 crosscheck 审过的 MR 上的漏报 %d 条 · 判断记录 %d 条 · 被关闭的规则 MR %d 个" % (
    len(mr_list), len(comments), len(misses), len(fb), len(closed))
with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as f:
    f.write(summary + "\n")
print(summary)
print(out)
PY
