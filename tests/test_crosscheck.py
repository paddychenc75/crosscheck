"""crosscheck 脚本的测试。

运行:python3 -m unittest discover -s tests -v   (在插件根目录)

每个测试在临时目录里建自己的仓库和记录目录;GitLab 用 tests/bin/glab 模拟,远程仓库用本地裸仓库代替。
不访问网络,不读写用户自己的 ~/.crosscheck。
"""

import json
from pathlib import Path
import os
import unittest

from fixture import Base, ProjectFixture, REVIEW_MD, SCRIPTS, api_key  # noqa: F401  其他测试模块也从这里取


class TestCollect(ProjectFixture):
    def test_range_review_reads_settings_and_rules_from_base(self):
        rd, out = self.collect("feature")
        settings = self.load(os.path.join(rd, "settings.json"))
        self.assertTrue(settings["source"].startswith("base:"))
        self.assertEqual(settings["limits"]["minor"], "2", "改动本身改的审查设置不应在审查它时生效")
        skipped = Path(os.path.join(rd, "skipped.txt")).read_text(encoding="utf-8")
        self.assertIn("src/gen/x.ts\treview-skip", skipped)
        self.assertIn("tests/page.test.ts\ttest", skipped)
        blocks = {b["file"]: b for b in self.load(os.path.join(rd, "rules-index.json"))}
        self.assertEqual(set(blocks), {"src/orders/REVIEW.md", "REVIEW.md", "CLAUDE.md"})
        self.assertEqual(blocks["src/orders/REVIEW.md"]["applies_files"], ["src/orders/paginate.ts"])
        self.assertEqual(blocks["src/orders/REVIEW.md"]["layer"], "L3")
        rules = Path(os.path.join(rd, "rules.txt")).read_text(encoding="utf-8")
        self.assertNotIn("分支里新加的规则", rules, "改动本身加的规则不应在审查它时生效")
        self.assertNotIn("skip:", rules, "frontmatter 不进规则正文")
        self.assertLess(rules.index("file=src/orders/REVIEW.md"), rules.index("file=REVIEW.md"), "越具体越先")
        meta = self.kv(os.path.join(rd, "rules-meta.txt"))
        self.assertEqual(meta["blocks"], "3")
        self.assertIn("order_client 统一重试", blocks["REVIEW.md"]["suppress"])
        self.assertNotIn("order_client", blocks["REVIEW.md"]["text"], "「不要报」不算要检查的规则")
        self.assertIn("order_client 统一重试", rules, "「不要报」仍交给角色看")
        self.assertTrue(meta["source"].startswith("base:"))

    def test_context_weakening_and_intent(self):
        rd, out = self.collect("feature")
        kinds = {line.split("\t")[0] for line in Path(os.path.join(rd, "weakening.txt")).read_text(encoding="utf-8").splitlines(keepends=True)}
        self.assertTrue({"assertions-removed", "skip-added", "ci-check-removed", "suppression-added"} <= kinds, kinds)
        self.assertNotIn("SUGGEST:", out)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertNotIn("risk_level", meta)
        self.assertIn("提交说明", meta["intent_sources"])
        intent = Path(os.path.join(rd, "intent.md")).read_text(encoding="utf-8")
        self.assertIn("feat: 修复分页", intent)

    def test_local_review_reads_worktree_rules(self):
        self.git(self.repo, "switch", "-q", "feature")
        rd, _ = self.collect("--intent", "修复分页丢数据")
        self.assertIn("分支里新加的规则", Path(os.path.join(rd, "rules.txt")).read_text(encoding="utf-8"))
        self.assertIn("你的补充", self.kv(os.path.join(rd, "meta.txt"))["intent_sources"])

    def test_no_rules_is_fine(self):
        repo = self.make_repo("plain", {"a.py": "x = 1\n"})
        self.commit(repo, {"a.py": "x = 2\n"}, "change")
        r = self.run_script("collect-diff.sh", "HEAD~1..HEAD", cwd=repo)
        rd = self.review_dir(r.stdout)
        r = self.run_script("collect-rules.sh", rd)
        self.assertIn("blocks=0", r.stdout)
        self.assertEqual(self.load(os.path.join(rd, "rules-index.json")), [])


class TestPipeline(ProjectFixture):
    def test_aggregate_rules_evidence_and_limits(self):
        rd, _ = self.collect("feature")
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        ev = [{"file": P, "lines": "1-2"}]
        cands = [
            {"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "整页丢最后一页", "failure_scenario": "x", "found_by": ["reviewer"], "pass_hits": 2},
            {"file": L, "line": 1, "category": "security", "severity": "critical", "summary": "依据不存在", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_file": "REVIEW.md", "rule_quote": "日志里不能出现邮箱。", "summary": "日志有邮箱", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_quote": "Use pnpm.", "summary": "没写出处", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_file": "NOPE.md", "rule_quote": "编造", "summary": "编造的规则", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_file": "REVIEW.md", "rule_quote": "不是原文", "summary": "引用不对", "failure_scenario": "x"},
            {"file": L, "line": 1, "category": "rules", "rule_file": "src/orders/REVIEW.md", "rule_quote": "订单金额用 decimal", "summary": "范围不对", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_file": "REVIEW.md", "summary": "没有引文", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_file": "REVIEW.md", "rule_quote": "order_client 统一重试", "summary": "引了不要报", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "minor", "summary": "小问题1", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "bug", "severity": "minor", "summary": "小问题2", "failure_scenario": "x"},
            {"file": L, "line": 2, "category": "bug", "severity": "minor", "summary": "小问题3", "failure_scenario": "x"},
            {"file": P, "line": 9, "category": "bug", "severity": "major", "summary": "不在改动行", "failure_scenario": "x"},
            {"file": "REVIEW.md", "line": 1, "category": "rules", "rule_file": "REVIEW.md", "summary": "规则可能过时:日志", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "intent", "severity": "major", "summary": "没满足验收标准", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "major", "summary": "文字定位的依据", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "major", "summary": "文字定位不存在", "failure_scenario": "x"},
        ]
        scores = {"整页丢最后一页": (96, ev), "依据不存在": (90, [{"file": L, "lines": "50-60"}]), "日志有邮箱": (90, ev),
                  "没写出处": (90, ev), "编造的规则": (90, ev), "引用不对": (90, ev), "范围不对": (90, [{"file": L, "lines": "1"}]),
                  "没有引文": (90, ev), "引了不要报": (90, ev), "小问题1": (85, ev), "小问题2": (84, ev),
                  "小问题3": (83, [{"file": L, "lines": "1-2"}]), "不在改动行": (95, ev),
                  "没满足验收标准": (95, ev + [{"file": "intent.md", "lines": "验收标准 1"}]),
                  "文字定位的依据": (95, ev + [{"file": "REVIEW.md", "lines": "日志里不能出现邮箱"}]),
                  "文字定位不存在": (95, ev + [{"file": "REVIEW.md", "lines": "PRJ-999"}])}
        final, out = self.run_pipeline(rd, cands, scores)
        shown = {x["summary"]: x for g in final["groups"].values() for x in g}
        self.assertIn("整页丢最后一页", shown)
        self.assertIn("没满足验收标准", shown, "引用 review_dir 里的 intent.md 作依据是有效的")
        self.assertIn("文字定位的依据", shown, "REVIEW.md 里能找到这段文字,依据有效")
        self.assertEqual(shown["整页丢最后一页"]["found_by"], ["reviewer"])
        self.assertEqual(shown["日志有邮箱"]["severity"], "minor")
        self.assertEqual(shown["没写出处"]["rule_file"], "CLAUDE.md", "没写出处时按引文找到规则文件")
        rejected = {x["summary"]: x for x in self.load(os.path.join(rd, "rejected.json"))}
        self.assertEqual(rejected["依据不存在"]["confidence"], 50)
        self.assertEqual(rejected["文字定位不存在"]["confidence"], 50)
        for k, why in (("编造的规则", "规则文件不在"), ("引用不对", "对不上"), ("范围不对", "不覆盖"), ("没有引文", "没有引用"), ("引了不要报", "对不上")):
            self.assertEqual(rejected[k]["confidence"], 0, k)
            self.assertIn(why, rejected[k]["reject_reason"], k)
        self.assertEqual(rejected["不在改动行"]["confidence"], 0)
        self.assertEqual(final["trimmed"].get("defects"), 1, "minor 上限 2 条,第 3 条应被裁掉")
        self.assertEqual([n["summary"] for n in final["notes"]], ["规则可能过时:日志"])
        nos = sorted(x["no"] for g in final["groups"].values() for x in g)
        self.assertEqual(nos, list(range(1, len(nos) + 1)))
        standing = self.load(os.path.join(rd, "findings.json"))
        self.assertTrue(all(x.get("id") for x in standing))
        self.assertEqual(len(standing), sum(len(g) for g in final["groups"].values()) + 1)
        lines = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("SCOPE: 审查范围:分支 feature", lines)
        self.assertIn("`REVIEW.md`", lines)
        self.assertIn("违反 2 条", lines)
        self.assertNotIn("VERIFY:", lines)
        self.assertIn("WEAKEN:", lines)

    def test_same_rule_in_same_file_is_merged(self):
        rd, _ = self.collect("feature")
        P = "src/orders/paginate.ts"
        cands = [{"file": P, "line": n, "category": "rules", "rule_file": "REVIEW.md", "rule_quote": "日志里不能出现邮箱", "summary": "邮箱%d" % n, "failure_scenario": "x"} for n in (1, 2)]
        final, _ = self.run_pipeline(rd, cands, {"邮箱1": (90, [{"file": P, "lines": "1"}]), "邮箱2": (90, [{"file": P, "lines": "2"}])})
        self.assertEqual(len(final["groups"]["rules"]), 1)
        self.assertEqual(final["groups"]["rules"][0]["also_lines"], [2])

    def test_incremental_carries_and_resolves(self):
        rd, _ = self.collect("feature")
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "bug", "severity": "major", "summary": "B", "failure_scenario": "x"}]
        self.run_pipeline(rd, cands, {"A": (90, [{"file": P, "lines": "2"}]), "B": (90, [{"file": L, "lines": "1"}])})
        with open(os.path.join(rd, "report.md"), "w") as f:
            f.write("报告\n")
        r = self.run_script("save-review.sh", rd)
        self.assertIn("cost=", r.stdout)
        self.assertIn("hook 没有运行", r.stdout)
        saved = r.stdout.split("dir=")[1].splitlines()[0]
        self.assertTrue(os.path.exists(os.path.join(saved, "rejected.json")))
        self.git(self.repo, "switch", "-q", "feature")
        self.write(self.repo, {"src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.ceil(t / s)\n}\n"})
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "--amend", "--no-edit", "-q")
        self.git(self.repo, "switch", "-q", "main")
        rd2, out = self.collect("feature")
        self.assertEqual(self.kv(os.path.join(rd2, "meta.txt"))["review_scope"], "incremental")
        prev = self.load(os.path.join(rd2, "prev-findings.json"))
        a = next(p for p in prev if p["summary"] == "A")
        cand = dict(a, from_prev=True)
        cand.pop("confidence"); cand.pop("verdict")
        final, _ = self.run_pipeline(rd2, [cand], {"A": (10, [{"file": P, "lines": "2"}])})
        self.assertEqual([x["summary"] for x in final["resolved"]], ["A"])
        carried = [x for g in final["groups"].values() for x in g if x["status"] == "carried"]
        self.assertEqual([x["summary"] for x in carried], ["B"])


class TestDependents(Base):
    def test_unchanged_file_using_changed_symbol_is_rechecked(self):
        repo = self.make_repo("dep", {"src/a.ts": "export function computeTotal(x) {\n  return x\n}\n",
                                      "src/b.ts": "import { computeTotal } from './a'\nexport const v = computeTotal(1)\n",
                                      "src/c.ts": "export const other = 1\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function computeTotal(x) {\n  return x + 1\n}\n",
                           "src/b.ts": "import { computeTotal } from './a'\nexport const v = computeTotal(2)\n",
                           "src/c.ts": "export const other = 2\n"}, "f1")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=repo).stdout)
        for name, text in (("report.md", "r\n"), ("findings.json", "[]")):
            with open(os.path.join(rd, name), "w") as f:
                f.write(text)
        self.run_script("save-review.sh", rd)
        self.write(repo, {"src/a.ts": "export function computeTotal(x) {\n  return x * 2\n}\n"})
        rd2 = self.review_dir(self.run_script("collect-diff.sh", cwd=repo).stdout)
        st = dict(l.rstrip("\n").split("\t")[::-1] for l in Path(os.path.join(rd2, "incremental.txt")).read_text(encoding="utf-8").splitlines(keepends=True))
        self.assertEqual((st["src/a.ts"], st["src/b.ts"], st["src/c.ts"]), ("changed", "dependent", "unchanged"))
        files = Path(os.path.join(rd2, "files.txt")).read_text(encoding="utf-8").split()
        self.assertEqual(sorted(files), ["src/a.ts", "src/b.ts"])


class TestGitLab(Base):
    def setUp(self):
        super().setUp()
        self.use_mock_glab()
        self.repo = self.make_repo("shop", {"src/p.ts": "export const a = 1\n"}, remote="https://code.example.com/eng/shop.git")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.head = self.commit(self.repo, {"src/p.ts": "export const a = 2\n"}, "change")
        self.git(self.repo, "checkout", "-q", "--detach", self.base)
        mr = {"iid": 12, "title": "修复分页", "description": "修复分页丢数据。\n\nCloses #7", "web_url": "https://code.example.com/eng/shop/-/merge_requests/12",
              "source_branch": "feature/pay", "target_branch": "main", "state": "merged",
              "diff_refs": {"base_sha": self.base, "start_sha": self.base, "head_sha": self.head}, "references": {"full": "eng/shop!12"}}
        self.mr = mr
        self.mock_api("projects/:id/merge_requests/12", mr)
        self.mock_api("projects/eng%2Fshop/merge_requests/12", mr)
        issue = {"iid": 7, "title": "分页丢数据", "description": "## 验收标准\n- total 是整数倍时返回全部数据\n- 空列表返回 0 页\n"}
        self.mock_api("projects/:id/issues/7", issue)
        notes = [{"notes": [
            {"body": "这里整页时会丢数据", "author": {"username": "alice"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1, "head_sha": self.head}, "resolved": True},
            {"body": "added 1 commit", "author": {"username": "alice"}, "system": True},
            {"body": "自动检查通过了", "author": {"username": "ci-bot", "bot": True}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1}},
        ]}]
        self.mock_api("projects/:id/merge_requests/12/discussions?per_page=100", notes)

    def test_self_hosted_host_from_config(self):
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gitlab_hosts", r.stderr)
        self.config("gitlab_hosts=code.example.com\n")
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo)
        rd = self.review_dir(r.stdout)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual((meta["platform"], meta["mode"], meta["target_branch"], meta["source_branch"]), ("gitlab", "pr", "main", "feature/pay"))
        intent = Path(os.path.join(rd, "intent.md")).read_text(encoding="utf-8")
        self.assertIn("1. total 是整数倍时返回全部数据", intent)
        self.assertEqual(meta["intent_acceptance"], "2")
        self.assertEqual(len(self.load(os.path.join(rd, "discussions.json"))), 1)
        self.assertIn("#7", meta["intent_sources"])

    def test_subpath_mr_url(self):
        self.config("gitlab_hosts=company.com/gitlab\n")
        r = self.run_script("collect-diff.sh", "https://company.com/gitlab/eng/shop/-/merge_requests/12", cwd=self.repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["gitlab_host"], meta["number"]), ("eng/shop", "https://company.com/gitlab", "12"))

    def test_port_in_url_and_remote(self):
        self.git(self.repo, "remote", "set-url", "origin", "http://code.example.com:8929/eng/shop.git")
        self.config("gitlab_hosts=code.example.com:8929\n")
        r = self.run_script("collect-diff.sh", "http://code.example.com:8929/eng/shop/-/merge_requests/12", cwd=self.repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["gitlab_host"], meta["repo_id"]), ("eng/shop", "http://code.example.com:8929", "code.example.com/eng/shop"))
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo)
        self.assertEqual(self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))["platform"], "gitlab")


if __name__ == "__main__":
    unittest.main()
