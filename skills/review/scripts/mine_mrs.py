"""mine-mrs.sh 的实现,用法和说明见 mine-mrs.sh。"""
import datetime, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
import crlib
import project_rules
import host_target

root, repo_id, platform = sys.argv[1], sys.argv[2], sys.argv[3]
days, limit, home = int(sys.argv[4]), int(sys.argv[5]), crlib.home()

def fail(msg):
    print("mine-mrs: " + msg, file=sys.stderr); sys.exit(1)

try:
    target = host_target.resolve(root, platform)
except host_target.TargetError as e:
    fail(str(e))
platform = target["platform"]
repo_id = target["identity"]

def api(path):
    try:
        if host_target.resolve(root, platform) != target:
            fail("origin 已改变，停止平台读取")
        cmd = host_target.command(target, ["api", path])
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=root, env=host_target.environment(target), timeout=30)
    except (OSError, subprocess.TimeoutExpired, host_target.TargetError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        return None

def paginate(path, max_rounds=20):
    out = []
    for n in range(1, max_rounds + 1):
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

# 项目记录目录:和 feedback 一样按 .repo 找;没有就用项目名。
pdir = crlib.project_dir(repo_id) or ""
key = os.path.basename(pdir) if pdir else re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(repo_id))
base = os.path.join(home, "mining", key)
cache = os.path.join(base, "mrs")
crlib.makedirs(cache)

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
def code_lines(sha, path):
    k = (sha, path)
    if k not in code_cache:
        r = subprocess.run(["git", "-C", root, "show", "%s:%s" % (sha, path)], capture_output=True, text=True)
        code_cache[k] = r.stdout.split("\n") if r.returncode == 0 else None
    return code_cache[k]

def code_near(sha, path, line):
    if not (sha and path and line):
        return ""
    ls = code_lines(sha, path)
    if not ls:
        return ""
    n = int(line)
    return "\n".join(ls[max(0, n - 3):n + 2])

def changed_after(sha, final, path, line):
    """评论时那几行代码(去掉空白行)是否原样连续出现在 MR 最终版本的文件里:没出现 True,出现 False;不知道时 None。
    不按行号比,上方插入或删掉几行不算改了。"""
    if not (sha and final and path and line):
        return None
    if sha == final:
        return False
    before = [x.strip() for x in code_near(sha, path, line).split("\n") if x.strip()]
    if not before:
        return None
    if subprocess.run(["git", "-C", root, "cat-file", "-e", final + "^{commit}"], capture_output=True).returncode:
        return None
    after = code_lines(final, path)
    if after is None:
        return None
    after = [x.strip() for x in after if x.strip()]
    n = len(before)
    return not any(after[i:i + n] == before for i in range(len(after) - n + 1))

# ---------- MR 列表 ----------
if platform == "gitlab":
    listed = paginate("projects/:id/merge_requests?state=merged&updated_after=%s" % since_s)
    if listed is None:
        fail("glab 读不到这个项目的 MR 列表。请确认已登录(glab auth login)、当前仓库的 origin 指向这个 GitLab 项目;不要去找或打印凭证")
    mrs = [dict(iid=m.get("iid"), title=m.get("title", ""), author=(m.get("author") or {}).get("username", ""),
                merged_at=m.get("merged_at") or "", updated_at=m.get("updated_at") or "", web_url=m.get("web_url", ""),
                head_sha=m.get("sha") or "") for m in listed]
else:
    listed = paginate("repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc", max_rounds=10)
    if listed is None:
        fail("gh 读不到这个仓库的 PR 列表。请确认已登录(gh auth login);不要去找或打印凭证")
    mrs = [dict(iid=m.get("number"), title=m.get("title", ""), author=(m.get("user") or {}).get("login", ""),
                merged_at=m.get("merged_at") or "", updated_at=m.get("updated_at") or "", web_url=m.get("html_url", ""),
                head_sha=(m.get("head") or {}).get("sha", "")) for m in listed]
mrs = [m for m in mrs if m["iid"] and m["merged_at"] and (parse_time(m["merged_at"]) or now) >= since]
mrs.sort(key=lambda m: m["merged_at"], reverse=True)
mrs = mrs[:limit]

# ---------- 评论(按 MR 缓存,没更新过就不再拉) ----------
def fetch_notes(m):
    out = []
    if platform == "gitlab":
        ds = paginate("projects/:id/merge_requests/%s/discussions" % m["iid"])
        if ds is None:
            return None
        for d in ds:
            for i, n in enumerate(d.get("notes") or []):
                if n.get("system") or is_bot(n.get("author")):
                    continue
                pos = n.get("position") or {}
                path = pos.get("new_path") or pos.get("old_path") or ""
                line = pos.get("new_line") or pos.get("old_line") or None
                if pos.get("new_line"):
                    after, code = changed_after(pos.get("head_sha"), m.get("head_sha"), path, line), code_near(pos.get("head_sha"), path, line)
                else:
                    # 只有 old_line:评论落在删掉的行上,行号属于 base 那一版;删掉的行本来就不在最终版本里,改没改判断不了
                    path = pos.get("old_path") or path
                    after, code = None, code_near(pos.get("base_sha"), path, line)
                out.append(dict(id=n.get("id"), discussion=d.get("id"), author=(n.get("author") or {}).get("username", ""),
                                body=(n.get("body") or "")[:2000], file=path, line=line, reply=i > 0,
                                resolved=n.get("resolved") if n.get("resolvable") else None,
                                changed_after=after, code=code, created_at=n.get("created_at", "")))
    else:
        for kind, path in (("review", "repos/{owner}/{repo}/pulls/%s/comments"), ("issue", "repos/{owner}/{repo}/issues/%s/comments")):
            cs = paginate(path % m["iid"])
            if cs is None:
                return None
            for c in cs:
                if is_bot(c.get("user")):
                    continue
                line = c.get("line") or c.get("original_line")
                sha = c.get("commit_id") if c.get("line") else c.get("original_commit_id")
                out.append(dict(id=c.get("id"), discussion=c.get("in_reply_to_id") or c.get("id"), author=(c.get("user") or {}).get("login", ""),
                                body=(c.get("body") or "")[:2000], file=c.get("path") or "", line=line, reply=bool(c.get("in_reply_to_id")),
                                resolved=None, changed_after=changed_after(sha, m.get("head_sha"), c.get("path"), line),
                                code=code_near(sha, c.get("path"), line), created_at=c.get("created_at", "")))
    return out

fetched = reused = failed = 0
records = []
for m in mrs:
    cp = os.path.join(cache, "%s.json" % m["iid"])
    old = crlib.load_json(cp, {})
    if old and old.get("updated_at") == m["updated_at"] and isinstance(old.get("notes"), list):
        records.append(old); reused += 1
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
with crlib.record_write(work):
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


with open(os.path.join(work, "meta.txt"), "w", encoding="utf-8") as f:
    f.write("repo_root=%s\nrepo_id=%s\nplatform=%s\ndays=%d\nsince=%s\nmrs=%d\nnotes=%d\nreviewers=%d\nbatches=%d\n"
            "feedback=%d\ncache=%s\nproject_dir=%s\n"
            % (root, repo_id, platform, days, since_s, len(records), len(notes), len(people), len(batches), len(fb), cache, pdir))

print("mining: 最近 %d 天合并的 MR %d 个(新拉取 %d,用缓存 %d%s),人工评论 %d 条,评审人 %d 位,分成 %d 批;crosscheck 判断记录 %d 条"
      % (days, len(records), fetched, reused, ",读取失败 %d" % failed if failed else "", len(notes), len(people), len(batches), len(fb)))
if not notes and not fb:
    print("EMPTY: 这段时间里没有可用的人工评论,也没有 crosscheck 的判断记录")
print(work)
