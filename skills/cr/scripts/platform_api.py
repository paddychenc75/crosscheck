"""只读访问 GitLab(glab)和 GitHub(gh)的 API。读不到时返回 None,不抛异常。"""
import json
import os
import shutil
import subprocess


class Platform:
    def __init__(self, meta):
        self.meta = meta
        self.root = meta.get("repo_root", "") or None
        self.env = dict(os.environ)
        host = meta.get("gitlab_host", "")
        if host:
            self.env["GITLAB_HOST"] = host if "://" in host else (("https://" + host) if "/" in host else host)
        via_api = meta.get("pr_source", "api") == "api"
        self.project = meta.get("api_project") or ":id"
        self.has_glab = meta.get("platform") == "gitlab" and shutil.which("glab") is not None and via_api
        self.has_gh = (meta.get("platform") == "github" and shutil.which("gh") is not None and via_api
                       and bool(meta.get("repo")) and self.run(["gh", "auth", "status"]) is not None)

    def run(self, cmd, timeout=30):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=self.env, cwd=self.root)
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def glab(self, path):
        out = self.run(["glab", "api", path])
        try:
            return json.loads(out) if out else None
        except ValueError:
            return None

    def gh(self, path, paginate=False):
        out = self.run(["gh", "api"] + (["--paginate"] if paginate else []) + [path])
        if not out:
            return None
        try:
            return json.loads(out)
        except ValueError:
            # --paginate 时多页数组首尾相接
            try:
                return json.loads("[" + out.strip().replace("][", ",").strip("[]") + "]")
            except ValueError:
                return None

    def why_unreadable(self):
        """关联需求读不到时的原因说明。"""
        meta = self.meta
        if meta.get("pr_source") == "code_host":
            return "MR 信息经 tools.code_host 读取,没有 API 访问;可用 --intent 或 --doc 补充"
        if meta.get("platform") == "github":
            return "未找到 gh 或没有登录" if not self.has_gh else "API 请求失败,请检查访问权限与编号"
        if meta.get("platform") != "gitlab":
            return "未识别为 GitLab,可用 --platform gitlab"
        if shutil.which("glab") is None:
            return "未找到 glab"
        if meta.get("pr_source", "api") != "api":
            return "本次使用 git 回退,未请求 API"
        return "API 请求失败或返回内容不完整;请检查服务连接、访问权限与需求编号"
