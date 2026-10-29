"""测试共用的夹具:临时仓库、模拟的 glab、记录目录,以及运行脚本的辅助方法。

每个测试在临时目录里建自己的仓库和记录目录;GitLab 用 tests/bin/glab 模拟,远程仓库用本地裸仓库代替。
不访问网络,不读写用户自己的 ~/.crosscheck。
"""

import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SCRIPTS = os.path.join(PLUGIN, "skills", "review", "scripts")
SHARED_SCRIPTS = os.path.join(PLUGIN, "scripts")
MOCK_BIN = os.path.join(HERE, "bin")


def api_key(path):
    return re.sub(r"[/?&=:%]", "_", path)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cr-test-")
        self.home = os.path.join(self.tmp, "home")
        self.mock = os.path.join(self.tmp, "mock")
        os.makedirs(os.path.join(self.mock, "api"))
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "t"))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GITLAB", "GLAB", "GH_", "GITHUB", "CROSSCHECK", "CR_"))}
        env.update({
            "CROSSCHECK_HOME": self.home, "TMPDIR": os.path.join(self.tmp, "t"),
            "GIT_TERMINAL_PROMPT": "0", "MOCK_GLAB": self.mock, "LC_ALL": "en_US.UTF-8",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        })
        # 默认 PATH 里没有 glab / gh,需要时用 use_mock_glab()
        env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if not (os.path.exists(os.path.join(p, "glab")) or os.path.exists(os.path.join(p, "gh"))))
        # 测试用 git 包装器只允许本地传输；平台身份与本地裸仓库传输分开模拟。
        real_git = shutil.which("git", path=env["PATH"])
        bindir = Path(self.tmp, "git-bin")
        bindir.mkdir()
        wrapper = bindir / "git"
        wrapper.write_text('''#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
real = os.environ["TEST_REAL_GIT"]
command = next((x for x in args if x in ("fetch", "push", "ls-remote", "clone")), "")
if command:
    i = args.index(command)
    prefix = args[:i]
    if command == "clone":
        destinations = args[i+1:]
        if any("://" in x or "@" in x for x in destinations):
            sys.exit(1)
    else:
        u = subprocess.run([real] + prefix + ["remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        if "://" in u or "@" in u:
            local = subprocess.run([real] + prefix + ["config", "--get", "crosscheck.testLocalOrigin"], capture_output=True, text=True).stdout.strip()
            if not local:
                sys.exit(1)
            args = [local if x == "origin" else x for x in args]
os.execv(real, [real] + args)
''')
        wrapper.chmod(0o755)
        env["TEST_REAL_GIT"] = real_git
        env["PATH"] = str(bindir) + ":" + env["PATH"]
        self.env = env

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_mock_glab(self):
        self.env["PATH"] = MOCK_BIN + ":" + self.env["PATH"]

    def config(self, text):
        with open(os.path.join(self.home, "config"), "w") as f:
            f.write(textwrap.dedent(text))

    def mock_api(self, path, data):
        # 老样例使用 :id；实际请求必须使用显式项目路径。
        paths = [path]
        if "projects/:id/" in path:
            for repo in ("eng%2Fshop", "acme%2Facme-kit", "acme%2Fshop"):
                paths.append(path.replace("projects/:id/", "projects/" + repo + "/"))
        for item in paths:
            with open(os.path.join(self.mock, "api", api_key(item) + ".json"), "w") as f:
                json.dump(data, f, ensure_ascii=False)

    def install_plugin(self):
        """复制完整运行布局,安装路径含空格;不复制缓存或测试输出。"""
        target = Path(self.tmp, "installed crosscheck")
        for name in ("config", "scripts", "skills"):
            shutil.copytree(Path(PLUGIN, name), target / name, ignore=shutil.ignore_patterns("__pycache__"))
        return target

    def run_config(self, *args, check=True):
        r = subprocess.run(["sh", os.path.join(SHARED_SCRIPTS, "config.sh")] + list(args),
                           cwd=self.tmp, env=self.env, capture_output=True, text=True)
        if check and r.returncode != 0:
            self.fail("config.sh 失败:\n%s\n%s" % (r.stdout, r.stderr))
        return r

    def run_script(self, name, *args, cwd=None, check=True, env=None):
        r = subprocess.run(["sh", os.path.join(SCRIPTS, name)] + list(args), cwd=cwd or self.tmp, env=env or self.env,
                           capture_output=True, text=True)
        if check and r.returncode != 0:
            self.fail("%s 失败(%d):\n%s\n%s" % (name, r.returncode, r.stdout, r.stderr))
        return r

    def run_py(self, name, *args, check=True):
        r = subprocess.run(["python3", os.path.join(SCRIPTS, name)] + list(args), env=self.env, capture_output=True, text=True)
        if check and r.returncode != 0:
            self.fail("%s 失败:\n%s\n%s" % (name, r.stdout, r.stderr))
        return r

    def run_pipeline(self, rd, candidates, scores):
        with open(os.path.join(rd, "candidates.json"), "w") as f:
            json.dump(candidates, f, ensure_ascii=False)
        self.run_script("prepare-verify.sh", rd)
        results = []
        for name in sorted(os.listdir(os.path.join(rd, "verify"))):
            for c in self.load(os.path.join(rd, "verify", name)):
                r = dict(c)
                self.assertNotIn("found_by", r)
                conf, ev = scores[c["summary"]]
                r.update(confidence=conf, verdict="读过代码", evidence=ev)
                results.append(r)
        with open(os.path.join(rd, "verify", "result-1.json"), "w") as f:
            json.dump(results, f, ensure_ascii=False)
        r = self.run_script("aggregate.sh", rd)
        return self.load(os.path.join(rd, "final.json")), r.stdout

    def git(self, repo, *args, check=True):
        r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=self.env)
        if check and r.returncode != 0:
            self.fail("git %s 失败:%s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def write(self, root, files):
        for path, text in files.items():
            p = os.path.join(root, path)
            if text is None:
                if os.path.exists(p):
                    os.remove(p)
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(text).lstrip("\n"))

    def make_repo(self, name, files, remote=None):
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo, files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "init")
        if remote:
            self.git(repo, "remote", "add", "origin", remote)
        return repo

    def commit(self, repo, files, msg):
        self.write(repo, files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", msg)
        return self.git(repo, "rev-parse", "HEAD")

    def bare(self, name):
        p = os.path.join(self.tmp, "remotes", name + ".git")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", p)
        return p

    def review_dir(self, out):
        return out.strip().splitlines()[-1]

    def load(self, path, default=None):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def kv(self, path):
        d = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    d[k] = v
        return d


REVIEW_MD = """\
---
skip: ["src/gen/**"]
limits: { minor: 2 }
---
# Review 约定

## 每次都要检查

- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 dayjs。原因:时区统一处理。
- 日志里不能出现邮箱。原因:合规。

## 不要报

- order_client 统一重试,调用方不用处理
"""


class ProjectFixture(Base):
    """一个带规则和 feature 分支的项目。"""

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("shop", {
            "REVIEW.md": REVIEW_MD,
            "src/orders/REVIEW.md": "- 订单金额用 decimal。原因:精度。\n",
            "CLAUDE.md": "# Project\nUse pnpm.\n",
            "src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.ceil(t / s)\n}\n",
            "src/auth/login.ts": "export function login(u: string) {\n  return u\n}\n",
            "tests/page.test.ts": 'test("a", () => { expect(page(4, 2)).toBe(2) })\n',
            ".gitlab-ci.yml": "stages: [test]\nunit:\n  script: npm test\nlint:\n  script: npm run lint\n",
        })
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "switch", "-qc", "feature")
        self.head = self.commit(self.repo, {
            "src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.floor(t / s)\n}\n// TODO\n",
            "src/gen/x.ts": "export const g = 1\n",
            "src/auth/login.ts": "export function login(u: string) { // @ts-ignore\n  return u\n}\n",
            "tests/page.test.ts": 'test.skip("a", () => {})\n',
            ".gitlab-ci.yml": "stages: [test]\nunit:\n  script: npm test\n",
            "db/migrations/001.sql": "ALTER TABLE x ADD y int;\n",
            "src/log/logger.ts": "export const level = 1\n",
            "logs/today.txt": "started\n",
            "REVIEW.md": REVIEW_MD.replace("limits: { minor: 2 }", "limits: { minor: 9 }") + "- 分支里新加的规则。原因:测试。\n",
        }, "feat: 修复分页 Closes #12")
        self.git(self.repo, "switch", "-q", "main")

    def collect(self, *args):
        r = self.run_script("collect-diff.sh", *args, cwd=self.repo)
        rd = self.review_dir(r.stdout)
        self.run_script("collect-rules.sh", rd)
        return rd, r.stdout


class ShopRepo(Base):
    """main 上有 api/orders.ts 和调用方,feature 分支改了接口签名;提交说明里带需求单链接。"""

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("shop", {
            "api/orders.ts": "export function listOrders(page: number) {\n  return page\n}\n",
            "report/daily.ts": "import { listOrders } from '../api/orders'\nexport const x = listOrders(1)\n",
        })
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"api/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts\n}\n",
                                "config/app.yml": "order:\n  cursor:\n    enabled: true\n"},
                    "改为游标分页 https://tickets.acme.example.com/story/42")
        self.env["CR_FETCH"] = "0"

    def collect(self, *args, check=True):
        r = self.run_script("collect-diff.sh", "--base", "main", *args, cwd=self.repo, check=check)
        return r, (self.review_dir(r.stdout) if r.returncode == 0 else None)
