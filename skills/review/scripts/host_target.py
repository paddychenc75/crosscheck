"""平台目标只取 git 实际解析的 origin；不读取凭证，不提供网络隔离。"""
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit, quote

import crlib


class TargetError(ValueError):
    pass


def address(raw):
    raw = raw.strip()
    if re.search(r"[\s\\]", raw):
        raise TargetError("远程地址包含不支持的字符")
    if "://" not in raw:
        m = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", raw)
        if not m:
            raise TargetError("origin 是本地路径或不支持的传输，不能读取托管平台")
        raw = "ssh://" + m[1] + "/" + m[2]
    u = urlsplit(raw)
    if u.scheme not in ("http", "https", "ssh", "git") or not u.hostname or u.query or u.fragment:
        raise TargetError("origin 不是支持的 HTTP/SSH/Git 托管地址")
    try:
        port = u.port
    except ValueError:
        raise TargetError("远程地址端口不合法")
    host = u.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    # SSH 的端口是 Git 传输端口，不是 Web/API 端口。
    scheme = u.scheme if u.scheme in ("http", "https") else "https"
    authority = host
    if u.scheme in ("http", "https") and port and port != (443 if scheme == "https" else 80):
        authority += ":" + str(port)
    path = u.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(p in ("", ".", "..") for p in path.split("/")) or "%" in path:
        raise TargetError("远程仓库路径不合法或编码有歧义")
    return dict(host=authority, scheme=scheme, path=path)


def resolve(root, platform="", link=""):
    r = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise TargetError("当前仓库没有 origin，平台读取停止；仍可进行本地审查")
    a = address(r.stdout.strip())  # get-url 应用 insteadOf，不能拿原始配置绕过重写。
    prefix = ""
    detected = "github" if a["host"] == "github.com" else "gitlab" if "gitlab" in a["host"].split(":")[0] else ""
    for entry in crlib.config().get("gitlab_hosts", "").split(","):
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        u = urlsplit(entry if "://" in entry else "https://" + entry)
        ep = u.path.strip("/")
        if u.netloc.lower() == a["host"] and (not ep or a["path"].startswith(ep + "/")):
            detected = "gitlab"
            if len(ep) > len(prefix):
                prefix = ep
    info = None
    if link:
        m = re.fullmatch(r"(https?://[^\s]+?)/(?:-/)?(merge_requests|merge|pulls?)/([0-9]+)(?:[/?#].*)?", link)
        if not m:
            raise TargetError("不认识的 MR/PR 链接")
        b = address(m[1])
        if (a["host"], a["path"], a["scheme"]) != (b["host"], b["path"], b["scheme"]):
            raise TargetError("MR/PR 链接主机或仓库与当前 origin 不一致，停止读取: " + b["host"] + "/" + b["path"])
        kind = "github" if m[2].startswith("pull") else "gitlab"
        if detected and detected != kind:
            raise TargetError("MR/PR 类型与 origin 平台不一致")
        detected = detected or kind
        info = dict(number=m[3], fallback_ref=("pull/" if kind == "github" else "merge-requests/") + m[3] + "/head")
    if platform and platform not in ("github", "gitlab"):
        raise TargetError("平台应为 github 或 gitlab")
    if platform and detected and platform != detected:
        raise TargetError("指定平台与 origin 平台不一致")
    platform = detected or platform
    if not platform:
        raise TargetError("无法识别 origin 平台；请配置 gitlab_hosts 或指定 --platform")
    if platform == "github" and a["scheme"] != "https":
        raise TargetError("gh 无法按此 HTTP origin 绑定 API，停止读取；请使用 HTTPS origin")
    repo = a["path"][len(prefix) + 1:] if prefix else a["path"]
    base = a["scheme"] + "://" + a["host"] + ("/" + prefix if prefix else "")
    return dict(a, platform=platform, repo=repo, api_project=quote(repo, safe=""), base=base,
                identity=a["host"] + "/" + a["path"], hostname=urlsplit(base).hostname, subfolder=prefix, **(info or {}))


def command(t, args):
    """仅构造已绑定主机和仓库的读调用；环境中的默认仓库不参与选择。"""
    cli = "gh" if t["platform"] == "github" else "glab"
    args = list(args)
    if args[:1] == ["api"]:
        if any(x.startswith("-") and x != "--paginate" for x in args[1:]):
            raise TargetError("平台 API 仅允许 GET，不允许覆盖方法或主机")
        if len([x for x in args[1:] if not x.startswith("-")]) != 1:
            raise TargetError("平台 API 只接受一个相对路径")
        path = next((x for x in args[1:] if not x.startswith("-")), "")
        if not path or "://" in path or path.startswith("/") or ".." in path:
            raise TargetError("API 只接受相对路径")
        path = path.replace("projects/:id/", "projects/" + t["api_project"] + "/").replace("repos/{owner}/{repo}/", "repos/" + t["repo"] + "/")
        prefix = "repos/" + t["repo"] + "/" if cli == "gh" else "projects/" + t["api_project"] + "/"
        if not path.startswith(prefix):
            raise TargetError("API 项目与 origin 不一致")
        args[args.index(next(x for x in args[1:] if not x.startswith("-")))] = path
        args += ["--method", "GET", "--hostname", t["host"] if cli == "gh" else t["hostname"]]
    elif args[:2] == ["auth", "status"]:
        args = ["auth", "status", "--hostname", t["host"] if cli == "gh" else t["hostname"]]
    elif len(args) >= 2 and args[0] in ("pr", "mr") and args[1] in ("view", "diff"):
        if "-R" in args:
            i = args.index("-R")
            del args[i:i + 2]
        args += ["-R", t["host"] + "/" + t["repo"] if cli == "gh" else t["base"] + "/" + t["repo"]]
    else:
        raise TargetError("不支持的平台命令，仅允许读取")
    return [cli] + args


def environment(t):
    env = dict(os.environ)
    for key in ("GH_REPO", "GLAB_REPO", "GITLAB_HOST", "GH_HOST", "GITLAB_API_HOST"):
        env.pop(key, None)
    env.update(GH_HOST=t["host"], GITLAB_HOST=t["hostname"], GITLAB_API_HOST=t["host"],
               GLAB_API_PROTOCOL=t["scheme"], GITLAB_SUBFOLDER=t["subfolder"] or "/",
               GLAB_CHECK_UPDATE="false", GLAB_SEND_TELEMETRY="false", GH_PROMPT_DISABLED="1", GLAB_NO_PROMPT="1")
    return env


def main():
    try:
        action, root, platform = sys.argv[1:4]
        t = resolve(root, platform, sys.argv[4] if action == "resolve" and len(sys.argv) > 4 else "")
        if action == "resolve":
            for k, v in t.items():
                print(k + "=" + v)
        elif action == "exec":
            if (os.environ.get("CR_HOST_BASE", t["base"]), os.environ.get("CR_HOST_REPO", t["repo"])) != (t["base"], t["repo"]):
                raise TargetError("origin 在收集过程中改变，停止平台读取")
            return subprocess.run(command(t, sys.argv[4:]), cwd=root, env=environment(t)).returncode
        else:
            raise TargetError("未知操作")
    except (TargetError, OSError) as e:
        print("平台目标: " + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
