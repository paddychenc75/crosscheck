"""tools.code_host 读 MR、已合入的 MR、用户指定的基准提交,以及「对比基准默认用远端最新的目标分支」。

远程仓库用本地裸仓库代替,MR 的 head 放在裸仓库的 refs/merge-requests/<编号>/head 下;不访问网络。
"""
import json
import os
import subprocess
from pathlib import Path
import unittest

import fixture

LINK = "https://code.example.com/acme/shop/merge/12"
PROMPT = "tools.code_host = 用 acme-kit:mr-reader 读 MR,链接形如 https://code.example.com/x/y/merge/12\n"


class Remote(fixture.Base):
    """main 上一个提交 A;feature 改了 src/p.ts(head H,只放在 MR 的 ref 上);远端 main 之后又多了一个提交。"""

    def setUp(self):
        super().setUp()
        self.origin = self.bare("acme/shop")
        self.repo = self.make_repo("shop", {"src/p.ts": "export const a = 1\n", "other.ts": "export const o = 1\n"}, remote=self.origin)
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "switch", "-qc", "feature")
        self.head = self.commit(self.repo, {"src/p.ts": "export const a = 2\n"}, "改 a")
        self.git(self.repo, "push", "-q", "origin", "%s:refs/merge-requests/12/head" % self.head)
        self.git(self.repo, "switch", "-q", "main")
        self.change = self.git(self.repo, "diff", self.base, self.head)

    def advance_remote_main(self, files, msg):
        """在远端 main 上加一个提交,本地的 main 和 origin/main 都不动(模拟本地过期)。"""
        clone = os.path.join(self.tmp, "clone-%d" % len(os.listdir(self.tmp)))
        self.git(self.tmp, "clone", "-q", self.origin, clone)
        sha = self.commit(clone, files, msg)
        self.git(clone, "push", "-q", "origin", "main")
        return sha, clone

    def collect(self, *args, check=True, env=None):
        r = self.run_script("collect-diff.sh", *args, cwd=self.repo, check=check, env=dict(self.env, **(env or {})))
        return r

    def meta(self, rd):
        return self.kv(os.path.join(rd, "meta.txt"))

    def patch(self, rd):
        return Path(rd, "diff.patch").read_text(encoding="utf-8")


class CodeHost(Remote):
    def setUp(self):
        super().setUp()
        self.config(PROMPT)
        self.advance_remote_main({"other.ts": "export const o = 2\n"}, "别人的改动")

    def record(self, **kw):
        rec = {"status": "ok", "title": "修复分页", "description": "修复分页丢数据。\n\n$(touch pwned) `touch pwned2`",
               "target_branch": "main", "source_ref": "merge-requests/12/head", "head_sha": self.head[:10],
               "base_sha": "0" * 40, "discussions": [
                   {"author": "alice", "body": "这里整页会丢数据", "file": "src/p.ts", "line": 1, "resolved": True, "replied": True},
                   {"author": "ci-bot", "body": "build ok"}]}
        rec.update(kw)
        return rec

    def review(self, rec, *args, check=True, env=None):
        r = self.collect(LINK, *args, env=env)
        self.assertIn("NEED_CODE_HOST: ", r.stdout)
        stage = r.stdout.split("NEED_CODE_HOST: ", 1)[1].splitlines()[0].strip()
        self.assertTrue(os.path.isdir(stage))
        Path(stage, "code-host.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        r = self.collect(LINK, "--code-host-dir", stage, *args, check=check, env=env)
        return r, (self.review_dir(r.stdout) if r.returncode == 0 else None)

    def test_first_run_only_asks_for_the_record(self):
        r = self.collect(LINK)
        self.assertEqual(r.stdout.strip().splitlines()[-1].split(": ")[0], "NEED_CODE_HOST")

    def test_other_repo_link_is_refused(self):
        r = self.collect("https://code.example.com/acme/other/merge/12", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acme/other", r.stderr)

    def test_normal_read(self):
        r, rd = self.review(self.record())
        m = self.meta(rd)
        self.assertEqual((m["mode"], m["number"], m["repo"], m["url"], m["pr_source"]), ("pr", "12", "acme/shop", LINK, "code_host"))
        self.assertEqual((m["head_sha"], m["base_sha"], m["title"]), (self.head, self.base, "修复分页"))
        self.assertEqual(m["target_key"], "mr-12")
        self.assertIn("远端最新 origin/main", m["base_desc"])
        self.assertEqual(self.patch(rd).strip(), self.change.strip())
        desc = Path(rd, "description.md").read_text(encoding="utf-8")
        self.assertIn("$(touch pwned) `touch pwned2`", desc)
        self.assertFalse(any(os.path.exists(os.path.join(d, "pwned")) for d in (self.repo, self.tmp, rd)))
        self.assertIn("## MR 描述", Path(rd, "intent.md").read_text(encoding="utf-8"))
        disc = self.load(os.path.join(rd, "discussions.json"))
        self.assertEqual([x["body"] for x in disc], ["这里整页会丢数据"])
        self.assertEqual((disc[0]["source"], disc[0]["resolved"], disc[0]["replied"]), ("code_host", True, True))
        self.assertNotIn("alice", json.dumps(disc, ensure_ascii=False))
        self.assertNotIn("MR 信息没有读到", r.stdout)

    def test_head_mismatch_is_rejected_and_falls_back_to_git(self):
        r, rd = self.review(self.record(head_sha="0123456789"))
        m = self.meta(rd)
        self.assertTrue(m["pr_source"].startswith("git"))
        self.assertIn("head_sha", m["code_host_reason"])
        self.assertIn("NOTE: MR 信息没有读到", r.stdout)
        self.assertEqual(m["title"], "")
        self.assertTrue(Path(rd, "description.md").read_text(encoding="utf-8").startswith("(MR 信息没有读到"))
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_failed_status_falls_back_with_note(self):
        r, rd = self.review({"status": "failed", "reason": "工具没有这个仓库的读取权限"})
        self.assertIn("NOTE: MR 信息没有读到(tools.code_host:工具没有这个仓库的读取权限)", r.stdout)
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("MR 信息没有读到", out.split("CHECKED: ", 1)[1].splitlines()[0])
        self.assertNotIn("## MR 描述", Path(rd, "intent.md").read_text(encoding="utf-8"))

    def test_invalid_branch_name_is_rejected(self):
        for bad in ("-main", "main..x", "ma in"):
            r, rd = self.review(self.record(target_branch=bad))
            self.assertIn("目标分支名不合法", self.meta(rd)["code_host_reason"], bad)
        r, rd = self.review(self.record(source_ref="--upload-pack=x"))
        self.assertIn("源引用不合法", self.meta(rd)["code_host_reason"])

    def test_base_comes_from_merge_base_not_from_the_agent(self):
        latest = self.git(self.origin, "rev-parse", "main")
        r, rd = self.review(self.record(base_sha=latest))
        m = self.meta(rd)
        self.assertEqual(m["base_sha"], self.base)
        self.assertNotIn("other.ts", Path(rd, "files.txt").read_text())

    def test_replied_discussion_does_not_suppress_a_finding(self):
        r, rd = self.review(self.record())
        self.run_script("collect-rules.sh", rd)
        cand = [{"file": "src/p.ts", "line": 1, "category": "bug", "severity": "major", "summary": "整页丢数据",
                 "failure_scenario": "x", "raised_in_discussion": True}]
        final, _ = self.run_pipeline(rd, cand, {"整页丢数据": (90, [{"file": "src/p.ts", "lines": "1"}])})
        kept = [f for g in final["groups"].values() for f in g]
        self.assertEqual([f["summary"] for f in kept], ["整页丢数据"])
        self.assertTrue(kept[0]["raised_in_discussion"])
        self.assertIn("DISCUSSED: ", self.run_script("report-lines.sh", rd).stdout)

    def test_merge_commit_merged_mr_keeps_the_original_change(self):
        clone = os.path.join(self.tmp, "merger")
        self.git(self.tmp, "clone", "-q", self.origin, clone)
        self.git(clone, "fetch", "-q", "origin", "refs/merge-requests/12/head")
        self.git(clone, "merge", "-q", "--no-ff", "-m", "Merge MR 12", "FETCH_HEAD")
        self.git(clone, "push", "-q", "origin", "main")
        r, rd = self.review(self.record())
        m = self.meta(rd)
        self.assertEqual((m["merged"], m["base_sha"]), ("yes", self.base))
        self.assertEqual(self.patch(rd).strip(), self.change.strip())
        self.assertIn("已合入", r.stdout)
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("MERGED: 这个 MR 已合入 main", out)
        note = self.run_py("mr_note.py", rd).stdout
        self.assertIn("这个 MR 已合入", note)
        self.assertIn("状态:已合入 main", Path(rd, "mr.md").read_text(encoding="utf-8"))

    def test_squash_merged_mr_keeps_the_original_change(self):
        self.advance_remote_main({"src/p.ts": "export const a = 2\n"}, "修复分页 (squash)")
        r, rd = self.review(self.record())
        m = self.meta(rd)
        self.assertEqual(m["base_sha"], self.base)
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_given_base_sha_wins(self):
        r, rd = self.review(self.record(), "--base-sha", self.base[:7])
        m = self.meta(rd)
        self.assertEqual((m["base_sha"], m["base_sha_arg"]), (self.base, self.base[:7]))
        self.assertIn("对比基准:用户指定 %s" % self.git(self.repo, "rev-parse", "--short", self.base),
                      self.run_script("report-lines.sh", rd).stdout)


class MergedAndGivenBase(Remote):
    """分支路径、git 回退路径:已合入、快进合入、用户给基准提交。"""

    def merge_into_remote_main(self, squash=False):
        clone = os.path.join(self.tmp, "merger")
        self.git(self.tmp, "clone", "-q", self.origin, clone)
        self.commit(clone, {"other.ts": "export const o = 3\n"}, "合入前别人的改动")
        self.git(clone, "fetch", "-q", "origin", "refs/merge-requests/12/head")
        if squash:
            self.git(clone, "merge", "-q", "--squash", "FETCH_HEAD")
            self.git(clone, "commit", "-qm", "改 a (squash)")
        else:
            self.git(clone, "merge", "-q", "--no-ff", "-m", "Merge MR 12", "FETCH_HEAD")
        self.git(clone, "push", "-q", "origin", "main")

    def test_merged_branch_is_reviewed_against_the_merge_parent(self):
        self.merge_into_remote_main()
        rd = self.review_dir(self.collect("feature").stdout)
        m = self.meta(rd)
        self.assertEqual((m["base_sha"], m["merged"]), (self.base, "yes"))
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_squash_merged_branch_keeps_the_change(self):
        self.merge_into_remote_main(squash=True)
        rd = self.review_dir(self.collect("feature").stdout)
        self.assertEqual(self.meta(rd)["base_sha"], self.base)
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_merged_mr_via_git_fallback(self):
        # 没有 gh / glab:按编号走 git 回退,origin 是本地路径时要指定平台
        self.merge_into_remote_main()
        r = self.collect("12", "--platform", "gitlab")
        rd = self.review_dir(r.stdout)
        self.assertEqual((self.meta(rd)["base_sha"], self.meta(rd)["merged"]), (self.base, "yes"))
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_fast_forward_merge_asks_for_a_base_instead_of_an_empty_diff(self):
        self.git(self.repo, "push", "-q", "origin", "%s:refs/heads/main" % self.head)
        r = self.collect("12", "--platform", "gitlab", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NOTE: ", r.stdout)
        self.assertIn("--base-sha", r.stdout)
        rd = self.review_dir(self.collect("12", "--platform", "gitlab", "--base-sha", self.base).stdout)
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_given_short_base_sha_wins_over_automatic_base(self):
        self.git(self.repo, "switch", "-q", "feature")
        mid = self.commit(self.repo, {"src/q.ts": "export const q = 1\n"}, "再加一个文件")
        self.git(self.repo, "switch", "-q", "main")
        rd = self.review_dir(self.collect("feature", "--base-sha", self.head[:6]).stdout)
        m = self.meta(rd)
        self.assertEqual((m["base_sha"], m["head_sha"]), (self.head, mid))
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["src/q.ts"])
        self.assertIn("对比基准:用户指定", self.run_script("report-lines.sh", rd).stdout)
        # 不给时按远端 main 自动算,两个文件都在
        rd = self.review_dir(self.collect("feature").stdout)
        self.assertEqual(sorted(Path(rd, "files.txt").read_text().split()), ["src/p.ts", "src/q.ts"])

    def test_base_sha_must_be_an_ancestor_of_head(self):
        other, _ = self.advance_remote_main({"other.ts": "export const o = 9\n"}, "x")
        self.git(self.repo, "fetch", "-q", "origin")
        r = self.collect("feature", "--base-sha", other[:8], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不是被审查的 head", r.stderr)
        r = self.collect("feature", "--base-sha", "main", check=False)
        self.assertIn("提交哈希", r.stderr)
        r = self.collect("feature", "--base-sha", "abcdef0", check=False)
        self.assertIn("找不到 --base-sha 指定的提交", r.stderr)

    def test_given_base_sha_on_merged_mr_gives_the_change(self):
        self.merge_into_remote_main()
        rd = self.review_dir(self.collect("12", "--platform", "gitlab", "--base-sha", self.base[:8]).stdout)
        self.assertEqual(self.patch(rd).strip(), self.change.strip())

    def test_empty_mr_diff_is_not_silent(self):
        rd_out = self.collect("12", "--platform", "gitlab", "--base-sha", self.head).stdout
        self.assertIn("EMPTY:", rd_out)
        self.assertIn("NOTE: 按指定的对比基准", rd_out)

    def test_changed_base_sha_forces_full_review(self):
        rd = self.review_dir(self.collect("feature", "--base-sha", self.base).stdout)
        h1 = Path(rd, "context-hash.txt").read_text()
        rd = self.review_dir(self.collect("feature").stdout)
        self.assertNotEqual(h1, Path(rd, "context-hash.txt").read_text())


class LatestTarget(Remote):
    """不指定基准时以远端最新的目标分支为准,本地 main、origin/main 过期也一样。"""

    def setUp(self):
        super().setUp()
        self.newer, _ = self.advance_remote_main({"other.ts": "export const o = 2\n"}, "远端新提交")
        # feature 基于远端最新的 main 再改一处:按过期的本地 main 比会把别人的提交也算进来
        self.git(self.repo, "fetch", "-q", "origin", "main")
        self.git(self.repo, "switch", "-qc", "feat2", "FETCH_HEAD")
        self.commit(self.repo, {"src/r.ts": "export const r = 1\n"}, "r")
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)

    def test_default_base_uses_the_latest_remote_branch(self):
        rd = self.review_dir(self.collect("feat2").stdout)
        m = self.meta(rd)
        self.assertEqual(m["base_sha"], self.newer)
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["src/r.ts"])
        self.assertIn("远端最新 origin/main", m["base_desc"])

    def test_given_base_is_not_replaced(self):
        rd = self.review_dir(self.collect("feat2", "--base-sha", self.base).stdout)
        self.assertEqual(self.meta(rd)["base_sha"], self.base)
        self.assertIn("other.ts", Path(rd, "files.txt").read_text())

    def test_fetch_failure_is_noted(self):
        r = self.collect("feat2", env={"CR_FETCH": "0"})
        self.assertIn("目标分支可能不是最新", r.stdout)
        self.assertIn("本地缓存 origin/main", self.meta(self.review_dir(r.stdout))["base_desc"])

    def test_code_host_path_uses_the_latest_remote_branch(self):
        self.config(PROMPT)
        r = self.collect(LINK)
        stage = r.stdout.split("NEED_CODE_HOST: ", 1)[1].splitlines()[0].strip()
        Path(stage, "code-host.json").write_text(json.dumps({
            "status": "ok", "title": "t", "target_branch": "main", "source_ref": "merge-requests/12/head"}), encoding="utf-8")
        rd = self.review_dir(self.collect(LINK, "--code-host-dir", stage).stdout)
        m = self.meta(rd)
        self.assertEqual((m["base_sha"], m["pr_source"]), (self.base, "code_host"))
        self.assertEqual(self.git(self.repo, "rev-parse", "origin/main"), self.newer)


class PlatformMerged(Remote):
    """gh / glab 路径:已合入的 PR / MR 用平台冻结的基准,源分支删掉后从 pull/N/head、merge-requests/N/head 取 head。"""

    def setUp(self):
        super().setUp()
        clone = os.path.join(self.tmp, "merger")
        self.git(self.tmp, "clone", "-q", self.origin, clone)
        self.git(clone, "fetch", "-q", "origin", "refs/merge-requests/12/head")
        self.git(clone, "merge", "-q", "--no-ff", "-m", "Merge", "FETCH_HEAD")
        self.git(clone, "push", "-q", "origin", "main", "%s:refs/pull/5/head" % self.head)
        # 本地删掉源分支,head 只在远端的 MR / PR ref 上
        self.git(self.repo, "branch", "-qD", "feature")
        self.git(self.repo, "reflog", "expire", "--expire=now", "--all")
        self.git(self.repo, "gc", "-q", "--prune=now")
        self.assertNotEqual(subprocess.run(["git", "-C", self.repo, "cat-file", "-e", self.head], capture_output=True).returncode, 0)

    def test_gitlab_merged_mr_uses_frozen_diff_refs(self):
        self.use_mock_glab()
        mr = {"iid": 12, "title": "改 a", "description": "", "web_url": "https://code.example.com/acme/shop/-/merge_requests/12",
              "source_branch": "feature", "target_branch": "main", "state": "merged",
              "diff_refs": {"base_sha": self.base, "start_sha": self.base, "head_sha": self.head}, "references": {"full": "acme/shop!12"}}
        self.mock_api("projects/:id/merge_requests/12", mr)
        r = self.collect("12", "--platform", "gitlab")
        rd = self.review_dir(r.stdout)
        m = self.meta(rd)
        self.assertEqual((m["base_sha"], m["head_sha"], m["merged"]), (self.base, self.head, "yes"))
        self.assertEqual(self.patch(rd).strip(), self.change.strip())
        self.assertIn("这个 MR 已合入", r.stdout)

    def test_github_merged_pr(self):
        ghmock = os.path.join(self.tmp, "ghmock")
        os.makedirs(os.path.join(ghmock, "api"))
        self.env.update(MOCK_GH=ghmock, PATH=os.path.join(fixture.HERE, "bin-gh") + ":" + self.env["PATH"])
        Path(ghmock, "diff.patch").write_text(self.change + "\n", encoding="utf-8")
        # 合入后 baseRefOid 停在合入时的目标分支提交(这里就是 A);即使给的是合入之后的,也要能算出 A
        merged_main = self.git(self.origin, "rev-parse", "main")
        for base_ref_oid in (self.base, merged_main):
            Path(ghmock, "pr_title_url_baseRefOid_headRefOid_headRefName_baseRefName.txt").write_text(
                "\t".join(["改 a", "https://github.com/acme/shop/pull/5", base_ref_oid, self.head, "feature", "main"]) + "\n", encoding="utf-8")
            Path(ghmock, "pr_state.txt").write_text("MERGED\n", encoding="utf-8")
            r = self.collect("https://github.com/acme/shop/pull/5", "--platform", "github")
            rd = self.review_dir(r.stdout)
            m = self.meta(rd)
            self.assertEqual((m["base_sha"], m["merged"]), (self.base, "yes"), base_ref_oid)
            self.assertEqual(self.patch(rd).strip(), self.change.strip())


if __name__ == "__main__":
    unittest.main()
