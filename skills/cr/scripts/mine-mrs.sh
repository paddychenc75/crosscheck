#!/bin/sh
# mine-mrs.sh — 只读地拉取最近合并的 MR 和上面的人工评论,整理成供 cr-rules 归纳规则的素材。
#
# 用法: mine-mrs.sh [--days N] [--limit N] [--repo DIR] [--platform gitlab|github]
#
#   --days N     最近 N 天合并的 MR(默认 90,约一个季度)
#   --limit N    最多取多少个 MR(默认 200,取合并时间最近的)
#   --repo DIR   仓库所在的目录(默认:当前目录)
#   --platform   默认按 origin 的地址判断:含 github.com 用 gh,其余用 glab
#
# 只读:只调用 glab api / gh api 的 GET 请求和 git show,不改仓库,也不往平台写任何东西。
# 缓存在 ${CROSSCHECK_HOME:-~/.crosscheck}/mining/<项目名>/mrs/<编号>.json,MR 没有更新过就不再拉评论。
# 缓存只存 MR 标题、作者和人工评论(不存 MR 描述原文)。
#
# 每次运行新建工作目录 mining/<项目名>/run-<时间>/,写入:
#   batch-<n>.json   按目录分好的评论:mr、note、author、file、line、code(评论处上下几行代码)、body,
#                    resolved(讨论是否已解决,不知道为 null)、changed_after(评论处的代码在 MR 最终版本里是否改了)
#   feedback.json    crosscheck 自己记下的判断(成立 / 不成立 / 不修)及原因,也是候选的素材
#   existing-rules.txt  仓库里已有的规则,归纳时不重复
#   meta.txt         repo_root、days、mrs、notes、reviewers、batches 等
# 输出统计,另有一行 MR_NOTE: 统计 MR 描述里贴了 crosscheck「MR 说明」的比例和评审人的回复;最后一行是工作目录。
# 需要 python3,以及已登录的 glab(GitHub 用 gh)。

set -eu

die() { printf 'mine-mrs: %s\n' "$*" >&2; exit 1; }
days=90; limit=200; repo_arg=""; platform=""
while [ $# -gt 0 ]; do
  case "$1" in
    --days) [ $# -ge 2 ] || die "--days 需要一个值"; days=$2; shift 2 ;;
    --limit) [ $# -ge 2 ] || die "--limit 需要一个值"; limit=$2; shift 2 ;;
    --repo) [ $# -ge 2 ] || die "--repo 需要一个值"; repo_arg=$2; shift 2 ;;
    --platform) [ $# -ge 2 ] || die "--platform 需要一个值"; platform=$2; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不认识的参数: $1" ;;
  esac
done
case "$days" in ''|*[!0-9]*) die "--days 应为正整数" ;; esac
case "$limit" in ''|*[!0-9]*) die "--limit 应为正整数" ;; esac
command -v python3 >/dev/null 2>&1 || die "需要 python3(3.8 或更新)"
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库,请用 --repo 指定"
root=$(git rev-parse --show-toplevel)

# 项目的身份,算法与 collect-diff.sh 一致;原始的远程地址不会被输出。
u=$(git remote get-url origin 2>/dev/null || true)
if [ -n "$u" ]; then
  repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#^\([^/:]*\):[0-9][0-9]*/#\1/#' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')
else
  repo_id=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd)")
fi
if [ -z "$platform" ]; then
  case "$repo_id" in *github.com*) platform=github ;; *) platform=gitlab ;; esac
fi
case "$platform" in
  gitlab) command -v glab >/dev/null 2>&1 || die "需要 glab(并已登录,可运行 glab auth login)" ;;
  github) command -v gh >/dev/null 2>&1 || die "需要 gh(并已登录,可运行 gh auth login)" ;;
  *) die "--platform 应为 gitlab 或 github" ;;
esac

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_ROOT="$root" CR_REPO_ID="$repo_id" CR_PLATFORM="$platform" \
CR_DAYS="$days" CR_LIMIT="$limit" CR_HOME="${CROSSCHECK_HOME:-$HOME/.crosscheck}" python3 - <<'PY'
import datetime, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
import project_rules

root, repo_id, platform = os.environ["CR_ROOT"], os.environ["CR_REPO_ID"], os.environ["CR_PLATFORM"]
days, limit, home = int(os.environ["CR_DAYS"]), int(os.environ["CR_LIMIT"]), os.environ["CR_HOME"]

def fail(msg):
    print("mine-mrs: " + msg, file=sys.stderr); sys.exit(1)

def api(path):
    cmd = ["glab", "api", path] if platform == "gitlab" else ["gh", "api", path]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        return None

def pages(path, max_pages=20):
    out = []
    for n in range(1, max_pages + 1):
        sep = "&" if "?" in path else "?"
        data = api("%s%sper_page=100&page=%d" % (path, sep, n))
        if data is None:
            return None if n == 1 else out
        if not isinstance(data, list):
            break
        out.extend(data)
        if len(data) < 100:
            break
    return out

# 项目记录目录:和 feedback.sh 一样按 .repo 找;没有就用项目名。
reviews = os.path.join(home, "reviews")
pdir, key = "", re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(repo_id))
if os.path.isdir(reviews):
    for name in sorted(os.listdir(reviews)):
        try:
            if open(os.path.join(reviews, name, ".repo"), encoding="utf-8").readline().strip() == repo_id:
                pdir, key = os.path.join(reviews, name), name
                break
        except OSError:
            pass
base = os.path.join(home, "mining", key)
cache = os.path.join(base, "mrs")
os.makedirs(cache, exist_ok=True)

now = datetime.datetime.utcnow().replace(microsecond=0)
since = now - datetime.timedelta(days=days)
since_s = since.strftime("%Y-%m-%dT%H:%M:%SZ")

BOT = re.compile(r"(^|[_\-\[])bot([_\-\]\d]|$)|bot$", re.I)
def is_bot(user):
    user = user or {}
    return bool(user.get("bot")) or user.get("type") == "Bot" or bool(BOT.search(user.get("username") or user.get("login") or ""))

def parse_time(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None

code_cache = {}
def code_near(sha, path, line):
    if not (sha and path and line):
        return ""
    k = (sha, path)
    if k not in code_cache:
        r = subprocess.run(["git", "-C", root, "show", "%s:%s" % (sha, path)], capture_output=True, text=True)
        code_cache[k] = r.stdout.split("\n") if r.returncode == 0 else None
    ls = code_cache[k]
    if not ls:
        return ""
    n = int(line)
    return "\n".join(ls[max(0, n - 3):n + 2])

def changed_after(sha, final, path, line):
    """评论之后那几行代码在 MR 最终版本里变没变:True / False;不知道时 None。"""
    if not (sha and final and path and line):
        return None
    if sha == final:
        return False
    before = code_near(sha, path, line)
    if not before:
        return None
    if subprocess.run(["git", "-C", root, "cat-file", "-e", final + "^{commit}"], capture_output=True).returncode:
        return None
    return code_near(final, path, line) != before

# ---------- MR 列表 ----------
if platform == "gitlab":
    listed = pages("projects/:id/merge_requests?state=merged&updated_after=%s" % since_s)
    if listed is None:
        fail("glab 读不到这个项目的 MR 列表。请确认已登录(glab auth login)、当前仓库的 origin 指向这个 GitLab 项目;不要去找或打印凭证")
    mrs = [dict(iid=m.get("iid"), title=m.get("title", ""), author=(m.get("author") or {}).get("username", ""),
                merged_at=m.get("merged_at") or "", updated_at=m.get("updated_at") or "", web_url=m.get("web_url", ""),
                head_sha=m.get("sha") or "", has_note="<!-- crosscheck:begin -->" in (m.get("description") or "")) for m in listed]
else:
    listed = pages("repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc", max_pages=10)
    if listed is None:
        fail("gh 读不到这个仓库的 PR 列表。请确认已登录(gh auth login);不要去找或打印凭证")
    mrs = [dict(iid=m.get("number"), title=m.get("title", ""), author=(m.get("user") or {}).get("login", ""),
                merged_at=m.get("merged_at") or "", updated_at=m.get("updated_at") or "", web_url=m.get("html_url", ""),
                head_sha=(m.get("head") or {}).get("sha", ""), has_note="<!-- crosscheck:begin -->" in (m.get("body") or "")) for m in listed]
mrs = [m for m in mrs if m["iid"] and m["merged_at"] and (parse_time(m["merged_at"]) or now) >= since]
mrs.sort(key=lambda m: m["merged_at"], reverse=True)
mrs = mrs[:limit]

# ---------- 评论(按 MR 缓存,没更新过就不再拉) ----------
def fetch_notes(m):
    out = []
    if platform == "gitlab":
        ds = pages("projects/:id/merge_requests/%s/discussions" % m["iid"])
        if ds is None:
            return None
        for d in ds:
            for i, n in enumerate(d.get("notes") or []):
                if n.get("system") or is_bot(n.get("author")):
                    continue
                pos = n.get("position") or {}
                path = pos.get("new_path") or pos.get("old_path") or ""
                line = pos.get("new_line") or pos.get("old_line") or None
                out.append(dict(id=n.get("id"), discussion=d.get("id"), author=(n.get("author") or {}).get("username", ""),
                                body=(n.get("body") or "")[:2000], file=path, line=line, reply=i > 0,
                                resolved=n.get("resolved") if n.get("resolvable") else None,
                                changed_after=changed_after(pos.get("head_sha"), m.get("head_sha"), path, line),
                                code=code_near(pos.get("head_sha"), path, line), created_at=n.get("created_at", "")))
    else:
        for kind, path in (("review", "repos/{owner}/{repo}/pulls/%s/comments"), ("issue", "repos/{owner}/{repo}/issues/%s/comments")):
            cs = pages(path % m["iid"])
            if cs is None:
                return None
            for c in cs:
                if is_bot(c.get("user")):
                    continue
                line = c.get("line") or c.get("original_line")
                out.append(dict(id=c.get("id"), discussion=c.get("in_reply_to_id") or c.get("id"), author=(c.get("user") or {}).get("login", ""),
                                body=(c.get("body") or "")[:2000], file=c.get("path") or "", line=line, reply=bool(c.get("in_reply_to_id")),
                                resolved=None, changed_after=changed_after(c.get("commit_id"), m.get("head_sha"), c.get("path"), line),
                                code=code_near(c.get("commit_id"), c.get("path"), line), created_at=c.get("created_at", "")))
    return out

fetched = reused = failed = 0
records = []
for m in mrs:
    cp = os.path.join(cache, "%s.json" % m["iid"])
    old = crlib.load_json(cp, {})
    if old and old.get("updated_at") == m["updated_at"] and isinstance(old.get("notes"), list):
        records.append(dict(old, has_note=m["has_note"])); reused += 1
        continue
    notes = fetch_notes(m)
    if notes is None:
        failed += 1
        if old:
            records.append(old)
        continue
    rec = dict(m, notes=notes)
    crlib.save_json(cp, rec)
    records.append(rec); fetched += 1

# ---------- 工作目录 ----------
work = os.path.join(base, "run-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
n = 0
while os.path.exists(work + ("-%d" % n if n else "")):
    n += 1
work += "-%d" % n if n else ""
os.makedirs(work)

def top(path):
    return path.split("/", 1)[0] if "/" in path else "(根目录)"

notes, people = [], set()
for r in records:
    for x in r.get("notes", []):
        if x.get("author") and x["author"] != r.get("author"):
            people.add(x["author"])
        notes.append(dict(mr=r["iid"], mr_title=r.get("title", ""), mr_author=r.get("author", ""), note=x.get("id"),
                          author=x.get("author", ""), file=x.get("file", ""), line=x.get("line"), code=x.get("code", ""),
                          body=x.get("body", ""), reply=x.get("reply", False), resolved=x.get("resolved"),
                          changed_after=x.get("changed_after")))
# 按顶层目录分批,没有位置的评论单独成批;每批最多 120 条
groups = {}
for x in notes:
    groups.setdefault(top(x["file"]) if x["file"] else "(整体评论)", []).append(x)
batches = []
for g in sorted(groups):
    items = sorted(groups[g], key=lambda x: (x["file"], x["mr"], str(x["note"])))
    for i in range(0, len(items), 120):
        batches.append(items[i:i + 120])
for i, b in enumerate(batches, 1):
    crlib.save_json(os.path.join(work, "batch-%d.json" % i), b)

fb = []
if pdir:
    for e in crlib.feedback_entries(pdir).values():
        if e.get("verdict") in ("invalid", "wontfix", "valid"):
            fb.append({k: e.get(k, "") for k in ("key", "id", "verdict", "reason", "file", "category", "summary", "author", "time")})
crlib.save_json(os.path.join(work, "feedback.json"), fb)

existing = []
for rec in project_rules.discover(crlib.Source(root)):
    if rec["body"].strip():
        existing.append("=== %s ===\n%s\n" % (rec["file"], rec["body"].strip()))
with open(os.path.join(work, "existing-rules.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(existing))

# ---------- MR 说明的使用情况 ----------
REPLY = re.compile(r"^\s*(有误|有|无)(?=$|[\s,，。.!！:：;；、])")
noted = [r for r in records if r.get("has_note")]
replies = {"有": 0, "无": 0, "有误": 0}
for r in noted:
    for x in r.get("notes", []):
        if x.get("author") == r.get("author"):
            continue
        m = REPLY.match(x.get("body") or "")
        if m:
            replies[m.group(1)] += 1

with open(os.path.join(work, "meta.txt"), "w", encoding="utf-8") as f:
    f.write("repo_root=%s\nrepo_id=%s\nplatform=%s\ndays=%d\nsince=%s\nmrs=%d\nnotes=%d\nreviewers=%d\nbatches=%d\n"
            "feedback=%d\ncache=%s\nproject_dir=%s\nmr_note=%d\n"
            % (root, repo_id, platform, days, since_s, len(records), len(notes), len(people), len(batches), len(fb), cache, pdir, len(noted)))

print("mining: 最近 %d 天合并的 MR %d 个(新拉取 %d,用缓存 %d%s),人工评论 %d 条,评审人 %d 位,分成 %d 批;crosscheck 判断记录 %d 条"
      % (days, len(records), fetched, reused, ",读取失败 %d" % failed if failed else "", len(notes), len(people), len(batches), len(fb)))
if records:
    print("MR_NOTE: %d/%d 个 MR 的描述里贴了 crosscheck 的 MR 说明(%d%%);评审人回复:有 %d、无 %d、有误 %d"
          % (len(noted), len(records), round(100 * len(noted) / len(records)), replies["有"], replies["无"], replies["有误"]))
if not notes and not fb:
    print("EMPTY: 这段时间里没有可用的人工评论,也没有 crosscheck 的判断记录")
print(work)
PY
