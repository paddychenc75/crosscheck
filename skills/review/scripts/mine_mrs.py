"""mine-mrs.sh 的实现,用法和说明见 mine-mrs.sh。"""
import datetime, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
import crlib
import project_rules
import host_target

root, repo_id, platform = sys.argv[1], sys.argv[2], sys.argv[3]
days, limit, home = int(sys.argv[4]), int(sys.argv[5]), crlib.home()
storage_mode = crlib.storage_mode({"storage_mode": sys.argv[6] if len(sys.argv) > 6 else "persistent"})

target_error = False
try:
    target = host_target.resolve(root, platform)
except host_target.TargetError:
    target_error = True
    target = None
repo_url = ""        # 仓库网页地址,快照里的 MR 没有网页地址时 mine-check 用它拼
if target:
    platform = target["platform"]
    repo_id = target["identity"]
    repo_url = target["scheme"] + "://" + target["identity"]

CACHE_VERSION = 2  # 2:评论带 commit(评论所在的版本),旧缓存重拉
BATCH_CHAR_BUDGET = 48000  # JSON 序列化字符数，仅用于分配上下文，不估算 token、不截正文。

class CollectionError(Exception):
    def __init__(self, resource, kind):
        self.resource, self.kind = resource, kind


def api(path):
    try:
        if host_target.resolve(root, platform) != target:
            raise CollectionError(path, "origin_changed")
        cmd = host_target.command(target, ["api", path])
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=root,
                           env=host_target.environment(target), timeout=30)
    except FileNotFoundError:
        raise CollectionError(path, "cli_missing")
    except subprocess.TimeoutExpired:
        raise CollectionError(path, "timeout")
    except host_target.TargetError:
        raise CollectionError(path, "target_invalid")
    except OSError:
        raise CollectionError(path, "execution_failed")
    if r.returncode != 0:
        # CLI 不提供通用结构化错误码；不得根据 stderr 推断登录状态或打印原始错误。
        raise CollectionError(path, "api_failed")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise CollectionError(path, "invalid_json")


def paginate(path):
    out, n = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        resource = "%s%sper_page=100&page=%d" % (path, sep, n)
        data = api(resource)
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise CollectionError(resource, "invalid_shape")
        out.extend(data)
        if len(data) < 100:
            return out
        n += 1


def require(condition, resource):
    if not condition:
        raise CollectionError(resource, "invalid_shape")


def validate_comment(note, user_key, resource):
    require(isinstance(note, dict) and isinstance(note.get("id"), int)
            and not isinstance(note["id"], bool) and isinstance(note.get("body"), str)
            and isinstance(note.get(user_key), dict)
            and isinstance(note.get("created_at"), str), resource)
    login = "username" if user_key == "author" else "login"
    require(isinstance(note[user_key].get(login), str), resource)
    position = note.get("position")
    require(position is None or isinstance(position, dict), resource)
    position = position or {}
    for key in ("new_line", "old_line"):
        value = position.get(key)
        require(value is None or type(value) is int and value > 0, resource)
    for key in ("new_path", "old_path", "head_sha", "base_sha"):
        require(position.get(key) is None or isinstance(position[key], str), resource)
    for key in ("line", "original_line", "in_reply_to_id"):
        value = note.get(key)
        require(value is None or type(value) is int and value > 0, resource)
    for key in ("path", "commit_id", "original_commit_id"):
        require(note.get(key) is None or isinstance(note[key], str), resource)

# 项目记录目录:和 feedback 一样按 .repo 找;没有就用项目名。
pdir = crlib.project_dir(repo_id) or ""
key = os.path.basename(pdir) if pdir else re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(repo_id))
base = os.path.join(home, "mining", key)
cache = os.path.join(base, "mrs")
if storage_mode == "persistent":
    crlib.makedirs(cache)

now = datetime.datetime.utcnow().replace(microsecond=0)
since = now - datetime.timedelta(days=days)
since_s = since.strftime("%Y-%m-%dT%H:%M:%SZ")

def is_bot(user):
    return user.get("bot") is True or user.get("type") == "Bot"


def identity(user):
    return {k: user[k] for k in ("id", "username", "login", "name", "type", "bot") if k in user}

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

# 在任何平台请求前创建本次记录，失败也保留可复核诊断。
work = crlib.create_work("mining", key, storage_mode)
with open(os.path.join(work, "meta.txt"), "w", encoding="utf-8") as f:
    f.write("repo_root=%s\nstorage_mode=%s\n" % (root, storage_mode))
errors, records, mrs = [], [], []
head_result = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
head = head_result.stdout.strip() if head_result.returncode == 0 else ""
fetched = reused = failed = 0


def list_mrs():
    if platform == "gitlab":
        resource = "projects/:id/merge_requests?state=merged&updated_after=%s" % since_s
        listed = paginate(resource)
        number, user_key, login, url = "iid", "author", "username", "web_url"
    else:
        resource = "repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc"
        listed = paginate(resource)
        number, user_key, login, url = "number", "user", "login", "html_url"
    out = []
    for m in listed:
        require(isinstance(m.get(number), int) and not isinstance(m[number], bool) and m[number] > 0
                and isinstance(m.get(user_key), dict) and isinstance(m.get("updated_at"), str)
                and parse_time(m["updated_at"]) is not None
                and isinstance(m[user_key].get(login), str)
                and isinstance(m.get("title", ""), str)
                and (m.get("head") is None or isinstance(m.get("head"), dict)), resource)
        merged = m.get("merged_at")
        require(merged is None or isinstance(merged, str) and parse_time(merged) is not None, resource)
        if not merged or parse_time(merged) < since:
            continue
        out.append(dict(iid=m[number], title=m.get("title", ""), author=m[user_key].get(login, ""),
                        author_identity=identity(m[user_key]), merged_at=merged, updated_at=m["updated_at"],
                        web_url=m.get(url, ""), head_sha=m.get("sha", "") if platform == "gitlab"
                        else (m.get("head") or {}).get("sha", "")))
    out.sort(key=lambda m: m["merged_at"], reverse=True)
    return out[:limit] if limit else out

# ---------- 评论(按 MR 缓存,没更新过就不再拉) ----------
def fetch_notes(m):
    out = []
    if platform == "gitlab":
        ds = paginate("projects/:id/merge_requests/%s/discussions" % m["iid"])
        for d in ds:
            resource = "mr:%s/discussions" % m["iid"]
            require(isinstance(d.get("id"), str) and isinstance(d.get("notes"), list), resource)
            for i, n in enumerate(d["notes"]):
                validate_comment(n, "author", resource)
                if n.get("system") or is_bot(n.get("author")):
                    continue
                pos = n.get("position") or {}
                path = pos.get("new_path") or pos.get("old_path") or ""
                line = pos.get("new_line") or pos.get("old_line") or None
                if pos.get("new_line"):
                    commit = pos.get("head_sha") or ""
                    after, code = changed_after(commit, m.get("head_sha"), path, line), code_near(commit, path, line)
                else:
                    # 只有 old_line:评论落在删掉的行上,行号属于 base 那一版;删掉的行本来就不在最终版本里,改没改判断不了
                    path = pos.get("old_path") or path
                    commit = pos.get("base_sha") or ""
                    after, code = None, code_near(commit, path, line)
                out.append(dict(id=n.get("id"), discussion=d.get("id"), author=(n.get("author") or {}).get("username", ""),
                                author_identity=identity(n["author"]), body=n["body"], file=path, line=line, reply=i > 0,
                                resolved=n.get("resolved") if n.get("resolvable") else None,
                                changed_after=after, code=code, commit=commit, created_at=n.get("created_at", "")))
    else:
        for kind, path in (("review", "repos/{owner}/{repo}/pulls/%s/comments"), ("issue", "repos/{owner}/{repo}/issues/%s/comments")):
            cs = paginate(path % m["iid"])
            for c in cs:
                validate_comment(c, "user", path % m["iid"])
                if is_bot(c.get("user")):
                    continue
                line = c.get("line") or c.get("original_line")
                sha = c.get("commit_id") if c.get("line") else c.get("original_commit_id")
                out.append(dict(id=c.get("id"), discussion="review:%s" % (c.get("in_reply_to_id") or c.get("id")) if kind == "review" else "issue",author=(c.get("user") or {}).get("login", ""),
                                author_identity=identity(c["user"]), body=c["body"], in_reply_to_id=c.get("in_reply_to_id"), kind=kind, file=c.get("path") or "", line=line, reply=bool(c.get("in_reply_to_id")),
                                resolved=None, changed_after=changed_after(sha, m.get("head_sha"), c.get("path"), line),
                                code=code_near(sha, c.get("path"), line), commit=sha or "",
                                created_at=c.get("created_at", "")))
    return out

try:
    if target_error:
        raise CollectionError("origin", "target_invalid")
    mrs = list_mrs()
except CollectionError as e:
    errors.append(dict(resource=e.resource, kind=e.kind))

def valid_cached_notes(notes):
    return isinstance(notes, list) and all(
        isinstance(n, dict) and isinstance(n.get("id"), int)
        and isinstance(n.get("discussion"), (str, int))
        and isinstance(n.get("author"), str) and isinstance(n.get("author_identity"), dict)
        and isinstance(n.get("body"), str) and isinstance(n.get("created_at"), str)
        and isinstance(n.get("reply"), bool)
        for n in notes)


for m in mrs:
    cp = os.path.join(cache, "%s.json" % m["iid"])
    old = crlib.load_json(cp, {})
    if (isinstance(old, dict) and old.get("cache_version") == CACHE_VERSION
            and old.get("complete") is True and old.get("repo_id") == repo_id
            and old.get("platform") == platform and old.get("iid") == m["iid"]
            and old.get("updated_at") == m["updated_at"] and valid_cached_notes(old.get("notes"))):
        records.append(old); reused += 1
        continue
    try:
        rec = dict(m, notes=fetch_notes(m), cache_version=CACHE_VERSION,
                   complete=True, repo_id=repo_id, platform=platform)
    except CollectionError as e:
        failed += 1
        errors.append(dict(resource=e.resource, kind=e.kind))
        continue
    if storage_mode == "persistent":
        crlib.save_json(cp, rec)
    records.append(rec); fetched += 1

if not target_error:
    try:
        if host_target.resolve(root, platform) != target:
            errors.append(dict(resource="origin", kind="origin_changed"))
    except (host_target.TargetError, OSError):
        errors.append(dict(resource="origin", kind="target_invalid"))
complete = not errors

notes, people = [], set()
for r in records:
    for x in r.get("notes", []):
        if x.get("author") and x["author"] != r.get("author"):
            people.add(x["author"])
        notes.append(dict(mr=r["iid"], mr_title=r.get("title", ""), mr_author=r.get("author", ""), note=x.get("id"),
                          thread="%s:%s" % (r["iid"], x["discussion"]), discussion=x["discussion"],
                          author_identity=x.get("author_identity", {}), created_at=x.get("created_at", ""),
                          in_reply_to_id=x.get("in_reply_to_id"), author=x.get("author", ""), file=x.get("file", ""), line=x.get("line"), code=x.get("code", ""),
                          body=x.get("body", ""), reply=x.get("reply", False), resolved=x.get("resolved"),
                          changed_after=x.get("changed_after")))
# 以整线程分配序列化字符预算；同线程跨文件仍一起，超预算线程独占一批。
groups = {}
for x in notes:
    groups.setdefault(x["thread"], []).append(x)
batches, pending = [], []
if complete:
    for items in groups.values():
        if pending and len(json.dumps(pending + items, ensure_ascii=False)) > BATCH_CHAR_BUDGET:
            batches.append(pending)
            pending = []
        pending.extend(items)
        if len(json.dumps(pending, ensure_ascii=False)) >= BATCH_CHAR_BUDGET:
            batches.append(pending)
            pending = []
    if pending:
        batches.append(pending)
for i, b in enumerate(batches, 1):
    crlib.save_json(os.path.join(work, "batch-%d.json" % i), b)

fb = []
if pdir:
    for e in crlib.feedback_entries(pdir).values():
        if e.get("verdict") in ("invalid", "wontfix", "valid"):
            fb.append({k: e.get(k, "") for k in ("key", "id", "verdict", "reason", "file", "category", "summary", "author", "root_cause", "failure_scenario", "target")})
crlib.save_json(os.path.join(work, "feedback.json"), fb)
crlib.save_json(os.path.join(work, "snapshot.json"),
                dict(version=1, complete=complete, records=records, feedback=fb, errors=errors))

existing = []
rule_sources = project_rules.mining_sources(root)
crlib.save_json(os.path.join(work, "rules-sources.json"), rule_sources)
for rec in rule_sources:
    if rec["body"].strip() or rec["issues"]:
        existing.append("=== %s ===\n%s\n%s\n" % (rec["file"], json.dumps(
            {k: rec[k] for k in ("source", "source_path", "patterns", "native", "excluded", "issues")}, ensure_ascii=False), rec["text"].strip()))
with open(os.path.join(work, "existing-rules.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(existing))


with open(os.path.join(work, "meta.txt"), "w", encoding="utf-8") as f:
    f.write("storage_mode=%s\n" % storage_mode)
    f.write("repo_root=%s\nrepo_id=%s\nrepo_url=%s\nplatform=%s\ndays=%d\nsince=%s\nmrs=%d\nnotes=%d\nreviewers=%d\nbatches=%d\n"
            "feedback=%d\ncache=%s\nproject_dir=%s\ncomplete=%s\nbatch_char_budget=%d\nhead=%s\n"
            % (root, repo_id, repo_url, platform, days, since_s, len(records), len(notes), len(people), len(batches), len(fb), cache, pdir, str(complete).lower(), BATCH_CHAR_BUDGET, head))

print("mining: 最近 %d 天合并的 MR %d 个(新拉取 %d,用缓存 %d%s),评论 %d 条,非作者评论账号 %d 位,分成 %d 批;crosscheck 判断记录 %d 条"
      % (days, len(records), fetched, reused, ",读取失败 %d" % failed if failed else "", len(notes), len(people), len(batches), len(fb)))
if complete and not notes and not fb:
    print("EMPTY: 这段时间里没有可用的评论,也没有 crosscheck 的判断记录")
if errors:
    for e in errors:
        print("mine-mrs: 采集不完整 resource=%s kind=%s" % (e["resource"], e["kind"]), file=sys.stderr)
if storage_mode == "temporary":
    print("NOTE: 本次不保存长期记录或缓存;临时材料保留在 %s" % work)
print(work)
if not complete:
    sys.exit(1)
