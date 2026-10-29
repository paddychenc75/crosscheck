"""只读访问 GitLab(glab)和 GitHub(gh)的 API。读不到时返回 None,不抛异常。"""
import json
import os
import shutil
import subprocess
import host_target


class Platform:
    def __init__(self, meta):
        self.meta = meta
        self.root = meta.get("repo_root", "") or None
        self.target = None
        try:
            if self.root:
                self.target = host_target.resolve(self.root, meta.get("platform", ""))
                if meta.get("repo") and meta["repo"] != self.target["repo"]:
                    self.target = None
        except host_target.TargetError:
            pass
        self.env = host_target.environment(self.target) if self.target else dict(os.environ)
        via_api = meta.get("pr_source", "api") == "api" and self.target is not None
        self.project = self.target["api_project"] if self.target else ""
        self.has_glab = meta.get("platform") == "gitlab" and shutil.which("glab") is not None and via_api
        self.has_gh = meta.get("platform") == "github" and shutil.which("gh") is not None and via_api

    def run(self, cmd, timeout=30):
        try:
            if cmd[0] in ("gh", "glab"):
                if not self.target or self.meta.get("pr_source", "api") != "api":
                    return None
                current = host_target.resolve(self.root, self.target["platform"])
                if current != self.target or cmd[0] != ("gh" if current["platform"] == "github" else "glab"):
                    return None
                cmd = host_target.command(current, cmd[1:])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=self.env, cwd=self.root)
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, host_target.TargetError):
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
