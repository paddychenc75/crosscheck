#!/bin/sh
# mine-reviews.sh — 为按需发起的规则提炼收集材料(只读)。由 rule-miner 角色使用。
#
# 用法: mine-reviews.sh [--since 30d | --mr 128,140] [--repo DIR] [--out DIR]
#
#   --since Nd   最近 N 天合并的 MR(默认 30d);--mr 指定编号
#   需要 glab(公网 GitLab 或私有化 GitLab 都可以,私有化的域名写在配置的 gitlab_hosts 里)。
#
# 写入输出目录(默认 ~/.crosscheck/rule-work/<项目>/mined-<日期>/,规则维护的中间材料,可以随时删除):
#   comments.jsonl          人工行评论与一般讨论:去掉系统消息、机器人、「LGTM」「+1」这类没有信息量的;
#                           每条带 MR 编号、文件、行号、评论人(只存哈希)、是否已解决、之后那几行代码是否被改过
#                           (code_changed_after),以及 crosscheck 当时有没有报过这里(matched_finding:发现编号;
#                           suppressed:discussions = 看到了但因为人已评论而没报;rejected:<分数> = 没通过核实)
#                           和那条的摘要(matched_summary,位置相近不等于说的是同一件事,由 rule-miner 判断)
#   misses.json             crosscheck 审过这个 MR、人工指出了、但当时没报的——候选的漏报,确认后可以进评测集
#   feedback-summary.json   这个项目的判断记录汇总:反复出现的「不成立」原因、反复被判为成立的同类问题
#   closed-candidates.json  被关闭的 crosscheck-rule MR(标题、说明、最后一条评论),提炼时不要再提同样的规则
#   open-candidates.json   未合入规则提案，避免重复创建
#   collection-status.json 日期口径、完整 / 部分 / 失败与缺失原因
#   existing-rules.md       现有项目规则（含原生格式、范围及用途）和只读组织规则，用于去重
#   summary.txt             计数
# 只做只读的 API 请求和 git fetch,不改工作区。需要 python3。

set -eu
script_dir=$(cd "$(dirname "$0")" && pwd)

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
repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#^\([^/:]*\):[0-9][0-9]*/#\1/#' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')

CR_SCRIPTS=$script_dir CR_ROOT=$root CR_REPO_ID=$repo_id CR_SINCE=$since CR_MRS=$mrs CR_OUT=$out python3 - <<'PY'
import collections, datetime, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
import rule_materials
import project_rules

root, repo_id = os.environ["CR_ROOT"], os.environ["CR_REPO_ID"]
cfg = crlib.config()
host = None
for h in (cfg.get("gitlab_hosts", "") or "").split(","):
    h = re.sub(r"^([^/:]*):\d+", r"\1", re.sub(r"^[A-Za-z]+://", "", h.strip()).rstrip("/"))
    if h and (repo_id == h or repo_id.startswith(h + "/")):
        if host is None or len(h) > len(host):
            host = h
env = dict(os.environ)
prefix = host.split("/", 1)[1] if host and "/" in host else ""
proj_path = repo_id[len(host) + 1:] if host else (repo_id.split("/", 1)[1] if "/" in repo_id else repo_id)
if prefix:
    # 部署在子路径下:glab 需要完整的地址(协议、端口、子路径),从 origin 地址里取,不输出它
    raw = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.match(r"^(https?)://(?:[^@/]*@)?([^/]+)/", raw)
    if m:
        env["GITLAB_HOST"] = "%s://%s/%s" % (m.group(1), m.group(2), prefix)
proj = proj_path.replace("/", "%2F")

collection_errors = []

def api(path):
    try:
        r = subprocess.run(["glab", "api", path], capture_output=True, text=True, timeout=60, env=env, cwd=root)
    except (OSError, subprocess.TimeoutExpired):
        collection_errors.append("API 无法读取: " + path)
        return None
    if r.returncode != 0:
        collection_errors.append("API 失败: " + path)
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        collection_errors.append("API 返回非 JSON: " + path)
        return None

def pages(path, per=100):
    result, seen = [], set()
    for page in range(1, 10001):
        rows = api(path + ("&" if "?" in path else "?") + "per_page=%d&page=%d" % (per, page))
        if not isinstance(rows, list):
            collection_errors.append("列表未读全: " + path)
            break
        signature = json.dumps(rows, sort_keys=True)
        if rows and signature in seen:
            collection_errors.append("分页重复，读取中止: " + path)
            break
        seen.add(signature)
        result.extend(rows)
        if len(rows) < per:
            break
    else:
        collection_errors.append("分页上限，读取未完成: " + path)
    return result

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
out = os.environ["CR_OUT"] or os.path.join(crlib.home(), "rule-work", pname, "mined-" + datetime.date.today().strftime("%Y%m%d"))
os.makedirs(out, exist_ok=True)

# 要看的 MR
mr_list = []
if os.environ["CR_MRS"]:
    if not re.fullmatch(r"\d+(,\d+)*", os.environ["CR_MRS"]):
        sys.exit("mine-reviews: --mr 应为逗号分隔的 MR 编号")
    for n in os.environ["CR_MRS"].split(","):
        m = api("projects/%s/merge_requests/%s" % (proj, n))
        if isinstance(m, dict) and m.get("iid"):
            mr_list.append(m)
else:
    if not re.fullmatch(r"[1-9]\d*d", os.environ["CR_SINCE"]):
        sys.exit("mine-reviews: --since 应为正整数天数，例如 30d")
    days = int(os.environ["CR_SINCE"][:-1])
    start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    after = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    ms = pages("projects/%s/merge_requests?state=merged&updated_after=%s" % (proj, after))
    for m in ms:
        merged = m.get("merged_at")
        if not merged:
            collection_errors.append("MR !%s 缺少 merged_at，未纳入日期范围" % m.get("iid"))
        else:
            try:
                stamp = datetime.datetime.fromisoformat(merged.replace('Z', '+00:00'))
                if stamp >= start: mr_list.append(m)
            except (ValueError, TypeError):
                collection_errors.append("MR !%s 合并时间格式无法确认" % m.get('iid'))

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
    fetched = git("fetch", "-q", "origin", "merge-requests/%s/head" % iid)
    if fetched.returncode and (not final_head or git("cat-file", "-e", final_head + "^{commit}").returncode):
        collection_errors.append("MR !%s 代码无法读取，不能判断评论后的修改" % iid)
    known = record_findings(iid)
    reviewed = bool(known) or (pdir and os.path.isdir(os.path.join(pdir, "mr-%s" % iid)))
    discs = pages("projects/%s/merge_requests/%s/discussions" % (proj, iid))
    for disc in discs if isinstance(discs, list) else []:
        for n in disc.get("notes") or []:
            author = n.get("author") or {}
            uname = str(author.get("username", ""))
            body = (n.get("body") or "").strip()
            pos = n.get("position") or {}
            if n.get("system") or author.get("bot") or BOT.search(uname) :
                continue
            if len(body) < 6 or TRIVIAL.match(body):
                continue
            path = (pos.get("new_path") if pos.get("new_line") else pos.get("old_path")) or pos.get("new_path")
            line = pos.get("new_line") or pos.get("old_line")
            comment_ref = pos.get("head_sha") if pos.get("new_line") else pos.get("base_sha")
            changed_after = None
            if path and line and final_head:
                old = lines_at(comment_ref, path) if comment_ref else None
                new = lines_at(final_head, pos.get("new_path") or path)
                if old and 1 <= int(line) <= len(old):
                    if new is None:
                        if git("cat-file", "-e", final_head + "^{commit}").returncode == 0:
                            changed_after = True
                        else:
                            collection_errors.append("MR !%s 最终代码不可读: %s" % (iid, path))
                    else:
                        h = crlib.ctx_hash(old, int(line))
                        changed_after = not any(crlib.ctx_hash(new, i + 1) == h for i in range(len(new)))
                else:
                    collection_errors.append("MR !%s 评论所在版本不可读: %s" % (iid, path))
            # 同一文件 ±3 行内取最近的一条;距离相同时优先通过的发现。
            # 因为人已经评论过而被压下的候选(suppressed_by)记为 suppressed:…,crosscheck 其实看到了,不算漏报。
            match, match_summary, best = None, "", None
            for kind, f in known:
                try:
                    dist = abs(int(f.get("line", 0)) - int(line or 0))
                except (TypeError, ValueError):
                    continue
                if f.get("file") != path or dist > 3:
                    continue
                rank = (dist, kind != "finding")
                if best is not None and rank >= best:
                    continue
                best = rank
                if kind == "finding":
                    match = f.get("id")
                elif f.get("suppressed_by"):
                    match = "suppressed:%s" % f.get("suppressed_by")
                else:
                    match = "rejected:%s" % f.get("confidence")
                match_summary = str(f.get("summary", ""))[:200]
            c = {"mr": iid, "file": path, "line": line, "body": body, "reviewer_hash": crlib.short_hash(uname),
                 "resolved": bool(n.get("resolved")), "code_changed_after": changed_after, "matched_finding": match, "matched_summary": match_summary,
                 "reviewed_by_crosscheck": bool(reviewed), "created_at": n.get("created_at", ""),
                 "note_id": n.get("id"), "discussion_id": disc.get("id"),
                 "discussion": [{"id": x.get("id"), "body": x.get("body", ""), "resolved": bool(x.get("resolved"))} for x in disc.get("notes", []) if not x.get("system")],
                 "url": mr.get("web_url", "") + ("#note_%s" % n["id"] if n.get("id") else "")}
            comments.append(c)
            if reviewed and path and line and (match is None or str(match).startswith("rejected:")):
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
for m in pages("projects/%s/merge_requests?state=closed&labels=crosscheck-rule" % proj):
    notes = pages("projects/%s/merge_requests/%s/notes?sort=desc" % (proj, m.get("iid")))
    last = next((x.get("body", "") for x in notes if not x.get("system")), "")
    closed.append({"iid": m.get("iid"), "title": m.get("title"), "description": (m.get("description") or ""),
                   "last_comment": last, "url": m.get("web_url")})
crlib.save_json(os.path.join(out, "closed-candidates.json"), closed)

opened = pages("projects/%s/merge_requests?state=opened&labels=crosscheck-rule" % proj)
crlib.save_json(os.path.join(out, "open-candidates.json"), [
    {"iid": m.get("iid"), "title": m.get("title"), "description": m.get("description"), "url": m.get("web_url")}
    for m in opened])

# 现有规则
src = crlib.Source(root)
parts = []
for r in project_rules.discover(src):
    parts.append("=== %s scope=%s%s ===\n%s" % (r["file"], ",".join(r["patterns"]),
                 " excludeAgent=code-review（仅核对已有编码约束）" if r["excluded"] else "", r["text"]))
    collection_errors.extend(r["file"] + ": " + x for x in r["issues"])
t = src.read("REVIEW.md")
packs, errors = rule_materials.legacy_packs({"extends": crlib.as_list(crlib.parse_frontmatter(t)[0].get("extends")) if t else []})
for pack in packs:
    parts.append("=== 组织规则(只读) %s ===\n%s" % (pack["pack"], pack["text"]))
materials_file = crlib.config().get("materials_file", "")
if materials_file and not os.path.isabs(materials_file):
    materials_file = os.path.join(root, materials_file)
materials, material_errors = rule_materials.read_import(materials_file)
for item in materials:
    if item["purpose"] == "rules" and item["text"]:
        parts.append("=== 外部规范(只读) %s ===\n%s" % (item["ref"], item["text"]))
collection_errors.extend(errors + material_errors)
for error in errors + material_errors:
    parts.append("资料未取得:" + error)
with open(os.path.join(out, "existing-rules.md"), "w", encoding="utf-8") as f:
    f.write("\n\n".join(parts) + "\n")

summary = "MR %d 个 · 人工评论 %d 条(含一般讨论、过滤后) · 其中 crosscheck 审过的 MR 上的漏报 %d 条 · 判断记录 %d 条 · 被关闭的规则 MR %d 个" % (
    len(mr_list), len(comments), len(misses), len(fb), len(closed))
status = "complete" if not collection_errors else ("partial" if mr_list else "failed")
crlib.save_json(os.path.join(out, "collection-status.json"), {
    "status": status, "date_basis": "explicit MR" if os.environ["CR_MRS"] else "merged_at",
    "since": os.environ["CR_SINCE"], "mrs": len(mr_list), "errors": collection_errors,
    "open_proposals": len(opened), "comment_scope": "human diff notes and general discussions"})
summary += " · 采集 " + status + " · 未合入规则 MR %d 个" % len(opened)
for error in collection_errors:
    print("mine-reviews: " + error, file=sys.stderr)
with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as f:
    f.write(summary + "\n")
print(summary)
print(out)
PY
