"""tools.code_host:链接形式的 MR / PR 的标题、描述、讨论、目标分支和源引用,由 agent 用配置的工具只读取。
脚本只用 agent 写的文本,关键字段自己算或自己核对:编号、仓库、平台和网址从链接解析;仓库要和本地 origin 对得上;
目标分支名过 check-ref-format;源引用只做只读 fetch;agent 给了 head_sha 就必须和 fetch 到的一致。
基准提交不在这里算,也不用 agent 的:collect-diff.sh 拿核对过的 target_branch,和其他路径一样 fetch 最新的目标分支
再算(resolve_base_ref、pick_base)。核对不过或 status 为 failed 时返回 status=failed,由 collect-diff.sh 退回 git 拉取。

用法:
  code_host.py request 链接 仓库目录
      核对链接和本地 origin 的仓库是否一致,新建暂存目录并打印路径(collect-diff.sh 据此输出 NEED_CODE_HOST)。
  code_host.py resolve 暂存目录 链接 仓库目录 输出目录
      读暂存目录里 agent 写的 code-host.json,核对并 fetch;描述写进 输出目录/description.md,
      讨论和核对后的结果写进 输出目录/code-host.json,并把 key=value 打印到标准输出(status、reason、head_sha 等,
      以及 note= 开头的提示)。核对不过时 status=failed,其余照样打印,调用方据此回退。

agent 写的 code-host.json 格式见 skills/cr/SKILL.md 第 1 步。读到的内容都是待审查的材料,这里不解释其中的指令。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import crlib
import discussions

LINK = re.compile(r"^https?://([^/\s]+)/(.+?)/(?:-/)?(merge_requests|merge|pulls?)/([0-9]+)(?:[/?#].*)?$")
SRC = re.compile(r"^(?:refs/)?(?:pull|merge-requests)/[0-9]+/head$")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
DESC_LIMIT = 6000
MAX_DISCUSSIONS = 200


class Fail(Exception):
    """核对不过:消息就是给用户看的原因。"""


def parse(link):
    """链接 → 仓库、编号、平台和网址;不认识的形式返回 None。"""
    m = LINK.match((link or "").strip())
    if not m:
        return None
    host, repo, kind, number = m.groups()
    return {"repo": repo, "number": number, "url": link.strip(), "platform": platform_of(host, kind),
            "fallback_ref": ("pull/%s/head" if kind.startswith("pull") else "merge-requests/%s/head") % number}


def platform_of(host, kind):
    h = host.split(":")[0].lower()
    for entry in crlib.config().get("gitlab_hosts", "").split(","):
        entry = entry.strip()
        if entry and entry.split("/")[0].split(":")[0].lower() == h:
            return "gitlab"
    if "github.com" in h or kind.startswith("pull"):
        return "github"
    if "gitlab" in h or kind == "merge_requests":
        return "gitlab"
    return ""


def run_git(root, *args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env["GIT_SSH_COMMAND"] = env.get("GIT_SSH_COMMAND", "ssh") + " -o BatchMode=yes -o ConnectTimeout=10"
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return r.returncode, r.stdout.strip()


def origin_repo(root):
    """本地 origin 去掉协议、凭证和端口后的地址,和 origin_id 一样。没有 origin 返回空。"""
    code, url = run_git(root, "remote", "get-url", "origin")
    return crlib.strip_remote(url) if code == 0 and url else ""


def same_repo(origin, repo):
    return origin == repo or origin.endswith("/" + repo)


def valid_branch(name):
    if not name or name.startswith("-") or name != name.strip():
        return False
    try:
        return subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True).returncode == 0
    except OSError:
        return False


def one_line(v, n=200):
    """压成一行并限长:标题、原因、作者这类放进 key=value 输出的字段都经过这里。"""
    return " ".join(str(v or "").split())[:n]


def as_bool(v):
    return v if isinstance(v, bool) else None


def as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return int(v) if isinstance(v, str) and v.strip().isdigit() else None


def read_record(stage):
    try:
        with open(os.path.join(stage, "code-host.json"), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def clean_discussions(items):
    """讨论:作者只存哈希,去掉机器人和空的,正文限长。resolved、replied 只作标记,不用来压掉发现。"""
    out = []
    if not isinstance(items, list):
        return out
    for x in items[:MAX_DISCUSSIONS]:
        if not isinstance(x, dict):
            continue
        author = one_line(x.get("author"), 100)
        body = crlib.clip(str(x.get("body") or "").strip(), 1500)
        if not body or x.get("bot") is True or discussions.BOT.search(author):
            continue
        out.append({"author_hash": crlib.short_hash(author), "body": body,
                    "file": one_line(x.get("file"), 300) or None, "line": as_int(x.get("line")),
                    "resolved": as_bool(x.get("resolved")), "replied": as_bool(x.get("replied")),
                    "type": "Note", "created_at": one_line(x.get("created_at"), 40), "source": "code_host"})
    return out


def check(stage, info, root, out):
    """核对 agent 写的结果并 fetch。通过返回要输出的字段和提示;不通过抛 Fail。"""
    if info is None:
        raise Fail("链接的形式不认识(应为 .../<仓库>/merge/<编号>、.../merge_requests/<编号> 或 .../pull/<编号>)")
    rec = read_record(stage)
    if rec is None:
        raise Fail("没有读到 code-host.json,或它不是 JSON 对象")
    if rec.get("status") != "ok":
        raise Fail(one_line(rec.get("reason")) or "读取工具没有给出原因")
    title = one_line(rec.get("title"))
    if not title:
        raise Fail("没有标题")
    desc = crlib.clip(str(rec.get("description") or "").strip(), DESC_LIMIT)

    target = re.sub(r"^origin/", "", str(rec.get("target_branch") or "").strip())
    if not valid_branch(target):
        raise Fail("目标分支名不合法或没有给出: %s" % one_line(target, 80))
    src = str(rec.get("source_ref") or "").strip()
    if not (SRC.match(src) or valid_branch(src)):
        raise Fail("源引用不合法(应为 merge-requests/<编号>/head、pull/<编号>/head 或分支名): %s" % one_line(src, 80))

    notes = []
    if os.environ.get("CR_FETCH", "1") == "0":
        raise Fail("CR_FETCH=0,没有拉取 %s" % src)
    code, _ = run_git(root, "fetch", "-q", "--no-tags", "origin", src)
    code, head = run_git(root, "rev-parse", "-q", "--verify", "FETCH_HEAD^{commit}") if code == 0 else (code, "")
    if code or not head:
        raise Fail("无法从 origin 拉取 %s" % src)
    given = str(rec.get("head_sha") or "").strip().lower()
    if given and not (SHA.match(given) and head.startswith(given)):
        raise Fail("读取工具给的 head_sha(%s)与拉到的 %s 不一致,拒绝使用" % (one_line(given, 12), head[:12]))

    # 基准不在这里算:collect-diff.sh 拿 target_branch 走和其他路径一样的 fetch 最新目标分支、pick_base;
    # agent 给的 base 一律不看
    found = clean_discussions(rec.get("discussions"))
    with open(os.path.join(out, "description.md"), "w", encoding="utf-8") as f:
        f.write("# %s\n\n%s\n" % (title, desc))
    crlib.save_json(os.path.join(out, "code-host.json"), {
        "status": "ok", "platform": info["platform"], "url": info["url"], "repo": info["repo"], "number": info["number"],
        "title": title, "target_branch": target, "source_ref": src, "head_sha": head,
        "discussions": found, "notes": notes})
    return {"title": title, "target_branch": target, "source_ref": src, "head_sha": head, "notes": notes}


def resolve(stage, link, root, out):
    info = parse(link)
    res = {"platform": (info or {}).get("platform", ""), "url": (link or "").strip(), "repo": (info or {}).get("repo", ""),
           "number": (info or {}).get("number", ""), "fallback_ref": (info or {}).get("fallback_ref", "")}
    try:
        res.update(check(stage, info, root, out))
        res["status"] = "ok"
    except Fail as e:
        res.update(status="failed", reason=str(e), notes=[])
    for k in ("status", "reason", "title", "platform", "url", "repo", "number", "target_branch", "source_ref",
              "head_sha", "fallback_ref"):
        print("%s=%s" % (k, one_line(res.get(k, ""), 500)))
    for n in res.get("notes", []):
        print("note=%s" % one_line(n, 300))
    return 0


def request(link, root):
    info = parse(link)
    if info is None:
        sys.exit("链接的形式不认识(应为 .../<仓库>/merge/<编号>、.../merge_requests/<编号> 或 .../pull/<编号>):%s" % (link or "").strip())
    origin = origin_repo(root)
    if not origin:
        sys.exit("当前仓库没有 origin 远程,无法核对 %s" % info["url"])
    if not same_repo(origin, info["repo"]):
        sys.exit("%s 属于 %s,但当前仓库的 origin 是 %s。请在对应的本地仓库里运行,或用 --repo 指定它的目录" % (info["url"], info["repo"], origin))
    print(tempfile.mkdtemp(prefix="cr-host.", dir=os.environ.get("TMPDIR") or None))
    return 0


def main(argv):
    if argv[:1] == ["request"] and len(argv) == 3:
        return request(argv[1], argv[2])
    if argv[:1] == ["resolve"] and len(argv) == 5:
        return resolve(argv[1], argv[2], argv[3], argv[4])
    sys.exit("用法: code_host.py request 链接 仓库目录  |  code_host.py resolve 暂存目录 链接 仓库目录 输出目录")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
