"""MR / PR 上已有的人工评论(只读;作者只存哈希,去掉机器人和系统消息)。结果写进 discussions.json。"""
import os
import re

import crlib

BOT = re.compile(r"(^|[-_])bot$|\[bot\]$|^ghost$", re.I)


def gitlab(api, number):
    out = []
    data = api.glab("projects/%s/merge_requests/%s/discussions?per_page=100" % (api.project, number))
    for disc in data if isinstance(data, list) else []:
        for n in disc.get("notes") or []:
            author = n.get("author") or {}
            uname = str(author.get("username", ""))
            body = (n.get("body") or "").strip()
            if n.get("system") or author.get("bot") or re.search(r"(^|[-_])bot$|^ghost$", uname, re.I) or not body:
                continue
            pos = n.get("position") or {}
            out.append({
                "author_hash": crlib.short_hash(uname), "body": crlib.clip(body, 1500),
                "file": pos.get("new_path") or pos.get("old_path"), "line": pos.get("new_line") or pos.get("old_line"),
                "resolved": bool(n.get("resolved")), "type": n.get("type") or "Note", "created_at": n.get("created_at", ""),
            })
    return out


def github(api, repo, number):
    base = "repos/%s" % repo
    items = [(c, c.get("path"), c.get("line") or c.get("original_line"), "DiffNote")
             for c in api.gh("%s/pulls/%s/comments?per_page=100" % (base, number), paginate=True) or [] if isinstance(c, dict)]
    items += [(c, None, None, "Note") for c in api.gh("%s/issues/%s/comments?per_page=100" % (base, number), paginate=True) or [] if isinstance(c, dict)]
    items += [(c, None, None, "Review") for c in api.gh("%s/pulls/%s/reviews?per_page=100" % (base, number), paginate=True) or [] if isinstance(c, dict)]
    out = []
    for c, path, line, kind in items:
        user = c.get("user") or {}
        uname = str(user.get("login", ""))
        body = (c.get("body") or "").strip()
        if user.get("type") == "Bot" or BOT.search(uname) or not body:
            continue
        out.append({
            "author_hash": crlib.short_hash(uname), "body": crlib.clip(body, 1500), "file": path, "line": line,
            "resolved": None, "type": kind, "created_at": c.get("created_at") or c.get("submitted_at") or "",
            "platform": "github", "thread_id": c.get("in_reply_to_id") or c.get("id"),
        })
    return out


def fetch(api, meta, review_dir):
    """返回 (讨论列表, 平台名称)。有讨论时写 discussions.json。"""
    number = meta.get("number")
    if meta.get("pr_source") == "code_host":
        # 经 tools.code_host 读到的讨论已由 code_host.py 核对,写在 code-host.json 里,带 source=code_host
        found = (crlib.load_json(os.path.join(review_dir, "code-host.json"), {}) or {}).get("discussions") or []
        name = "PR" if meta.get("platform") == "github" else "MR"
    elif api.has_glab and number:
        found, name = gitlab(api, number), "MR"
    elif api.has_gh and meta.get("mode") == "pr" and number:
        found, name = github(api, meta["repo"], number), "PR"
    else:
        return [], ""
    if found:
        crlib.save_json(os.path.join(review_dir, "discussions.json"), found)
    return found, name


def summary(found):
    """给 intent.md 的摘要:前 8 条。"""
    lines = []
    for x in found[:8]:
        where = "%s:%s " % (x["file"], x["line"]) if x.get("file") else ""
        state = "(已解决)" if x.get("resolved") else ("(已答复)" if x.get("replied") else "")
        lines.append("- %s%s%s" % (where, state, crlib.clip(x["body"].replace("\n", " "), 300)))
    more = "\n……还有 %d 条,见 discussions.json" % (len(found) - 8) if len(found) > 8 else ""
    return "\n".join(lines) + more
