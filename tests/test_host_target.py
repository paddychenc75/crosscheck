"""使用临时 git 配置与假 CLI 验证目标绑定；不访问真实平台。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fixture
sys.path.insert(0, fixture.SCRIPTS)
import host_target
import platform_api


class Target(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("repo", {"a": "a"})

    def remote(self, url):
        self.git(self.repo, "remote", "add", "origin", url)

    def resolve(self, **kw):
        with patch.dict(os.environ, self.env), patch.object(host_target.crlib, "config", return_value={"gitlab_hosts": kw.pop("hosts", "")}):
            return host_target.resolve(self.repo, **kw)

    def test_link_exact_host_and_repo(self):
        self.remote("git@github.com:org/repo.git")
        t = self.resolve(link="https://github.com/org/repo/pull/12")
        self.assertEqual(t["repo"], "org/repo")
        for url in ("https://evil.test/org/repo/pull/12", "https://github.com/x/org/repo/pull/12", "https://github.com/org/repo2/pull/12", "https://github.com:8443/org/repo/pull/12"):
            with self.assertRaises(host_target.TargetError):
                self.resolve(link=url)

    def test_effective_rewrite_is_identity(self):
        self.remote("https://github.com/org/repo.git")
        self.git(self.repo, "config", "url.https://gitlab.example/.insteadOf", "https://github.com/")
        self.assertEqual(self.resolve()["host"], "gitlab.example")
        with self.assertRaises(host_target.TargetError):
            self.resolve(link="https://github.com/org/repo/pull/1")

    def test_http_port_and_subpath(self):
        self.remote("http://git.example:8080/lab/group/repo.git")
        t = self.resolve(hosts="git.example:8080/lab", link="http://git.example:8080/lab/group/repo/-/merge_requests/3")
        self.assertEqual((t["base"], t["repo"]), ("http://git.example:8080/lab", "group/repo"))
        cmd = host_target.command(t, ["api", "projects/:id/merge_requests"])
        self.assertEqual(cmd, ["glab", "api", "projects/group%2Frepo/merge_requests", "--method", "GET", "--hostname", "git.example"])

        env = host_target.environment(t)
        self.assertEqual((env["GITLAB_API_HOST"], env["GLAB_API_PROTOCOL"], env["GITLAB_SUBFOLDER"]), ("git.example:8080", "http", "lab"))

    def test_ssh_port_not_api_port(self):
        self.remote("ssh://git@gitlab.example:2222/org/repo.git")
        self.assertEqual(self.resolve(link="https://gitlab.example/org/repo/-/merge_requests/1")["base"], "https://gitlab.example")

    def test_no_origin_and_local_origin_stop(self):
        with self.assertRaises(host_target.TargetError):
            self.resolve(platform="github")
        self.remote(self.tmp + "/local.git")
        with self.assertRaises(host_target.TargetError):
            self.resolve(platform="github")

    def test_no_platform_switch_and_no_default_repo(self):
        self.remote("https://github.com/org/repo.git")
        with self.assertRaises(host_target.TargetError):
            self.resolve(platform="gitlab")
        t = self.resolve()
        self.assertEqual(host_target.command(t, ["pr", "view", "1", "-R", "wrong/repo"])[-1], "github.com/org/repo")
        self.assertEqual(host_target.command(t, ["api", "repos/{owner}/{repo}/pulls"])[-2:], ["--hostname", "github.com"])
        for path in ("https://evil.test/a", "repos/other/repo/pulls", "../org/repo"):
            with self.assertRaises(host_target.TargetError):
                host_target.command(t, ["api", path])
        for args in (["-X", "POST"], ["--method=POST"], ["-XPOST"], ["--hostname=evil.test"], ["--repo", "x/y"]):
            with self.assertRaises(host_target.TargetError):
                host_target.command(t, ["api", "repos/org/repo/pulls"] + args)

    def test_mismatch_stops_before_cli_for_every_entry(self):
        self.remote("https://github.com/org/repo.git")
        bindir = Path(self.tmp, "fake-bin")
        bindir.mkdir()
        log = Path(self.tmp, "network-called")
        for cli in ("gh", "glab"):
            p = bindir / cli
            p.write_text('#!/bin/sh\ntouch "' + str(log) + '"\nexit 1\n')
            p.chmod(0o755)
        env = dict(self.env, PATH=str(bindir) + ":" + self.env["PATH"])
        r = self.run_script("collect-diff.sh", "https://evil.test/org/repo/pull/1", cwd=self.repo, env=env, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(log.exists())
        r = self.run_script("mine-mrs.sh", "--repo", self.repo, "--platform", "gitlab", env=env, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(log.exists())
        r = self.run_py("code_host.py", "resolve", self.tmp, "https://evil.test/org/repo/pull/1", self.repo, self.tmp, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(log.exists())

    def test_mining_numeric_and_auxiliary_bind_host(self):
        self.remote("https://github.com/org/repo.git")
        bindir = Path(self.tmp, "api-bin")
        bindir.mkdir()
        log = Path(self.tmp, "calls.jsonl")
        cli = bindir / "gh"
        cli.write_text('#!/usr/bin/env python3\nimport json,sys\nwith open(' + repr(str(log)) + ', "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\nprint("[]")\n')
        cli.chmod(0o755)
        env = dict(self.env, PATH=str(bindir) + ":" + self.env["PATH"], GH_HOST="evil.test", GH_REPO="wrong/repo", CR_FETCH="0")
        self.run_script("mine-mrs.sh", "--repo", self.repo, env=env)
        import json
        calls = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertIn("repos/org/repo/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1", calls[0])
        self.assertEqual(calls[0][-4:], ["--method", "GET", "--hostname", "github.com"])
        with patch.dict(os.environ, env):
            api = platform_api.Platform({"repo_root": self.repo, "platform": "github", "repo": "org/repo"})
            api.gh("repos/org/repo/issues/1")
        self.run_script("collect-diff.sh", "12", cwd=self.repo, env=env, check=False)
        calls = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertIn(["api", "repos/org/repo/issues/1", "--method", "GET", "--hostname", "github.com"], calls)
        views = [x for x in calls if x[:2] == ["pr", "view"]]
        self.assertTrue(views)
        self.assertTrue(all(x[-2:] == ["-R", "github.com/org/repo"] for x in views))

    def test_auxiliary_no_origin_does_not_auth(self):
        with patch.object(platform_api.shutil, "which", return_value="fake"), patch.object(platform_api.subprocess, "run", wraps=host_target.subprocess.run) as run:
            api = platform_api.Platform({"repo_root": self.repo, "platform": "github", "repo": "org/repo"})
            self.assertFalse(api.has_gh)
            self.assertIsNone(api.gh("repos/org/repo/issues/1"))
            self.assertTrue(all(c.args[0][0] == "git" for c in run.call_args_list))

    def test_local_review_without_origin(self):
        r = self.run_script("collect-diff.sh", "--base", "main", "--non-interactive", cwd=self.repo, env=dict(self.env, CR_FETCH="0"))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
