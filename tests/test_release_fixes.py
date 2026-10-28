"""0.2.0 发布前调研发现的问题的回归测试。"""
import json
import os
from pathlib import Path

import fixture


def save_baseline(t, rd):
    for name, text in (("report.md", "r\n"), ("findings.json", "[]")):
        if not os.path.exists(os.path.join(rd, name)):
            Path(rd, name).write_text(text, encoding="utf-8")
    t.run_script("save-review.sh", rd)


class IncrementalScope(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("inc", {"a.ts": "a\n", "b.ts": "b\n", "a.test.ts": "t\n"})
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"a.ts": "a2\n", "b.ts": "b2\n", "a.test.ts": "t2\n"}, "第一次")

    def collect(self, *args):
        r = self.run_script("collect-diff.sh", *args, cwd=self.repo)
        return self.review_dir(r.stdout)

    def scope(self, rd):
        return self.kv(os.path.join(rd, "meta.txt"))["review_scope"]

    def test_new_commit_and_feedback_keep_incremental(self):
        save_baseline(self, self.collect())
        self.commit(self.repo, {"a.ts": "a3\n"}, "第二次:换了提交说明")
        rd = self.collect()
        self.assertEqual(self.scope(rd), "incremental")
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["a.ts"])

    def test_with_tests_toggle_forces_full(self):
        save_baseline(self, self.collect("--with-tests"))
        self.assertEqual(self.scope(self.collect()), "full")

    def test_user_intent_change_forces_full(self):
        save_baseline(self, self.collect())
        self.assertEqual(self.scope(self.collect("--intent", "修复分页")), "full")


class RiskAndHead(fixture.Base):
    def test_range_with_dirty_worktree_reads_committed_code(self):
        repo = self.make_repo("dirty", {"a.ts": "old\n"})
        base = self.git(repo, "rev-parse", "HEAD")
        self.commit(repo, {"a.ts": "new\n"}, "改")
        self.write(repo, {"a.ts": "未提交\n"})
        rd = self.review_dir(self.run_script("collect-diff.sh", base + "..HEAD", cwd=repo).stdout)
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["head_checked_out"], "no")

    def test_empty_on_default_branch_gives_hint(self):
        repo = self.make_repo("clean", {"a.ts": "a\n"})
        out = self.run_script("collect-diff.sh", cwd=repo).stdout
        self.assertIn("EMPTY:", out)
        self.assertIn("HINT:", out)

    def test_large_change_asks_first(self):
        repo = self.make_repo("large", {"a.ts": "a\n", "b.ts": "b\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"a.ts": "a2\n", "b.ts": "b2\n"}, "改")
        env = dict(self.env, CR_LARGE_BATCHES="0", CR_BATCH_LINES="1")
        self.assertIn("LARGE:", self.run_script("collect-diff.sh", cwd=repo, env=env).stdout)
        self.assertNotIn("LARGE:", self.run_script("collect-diff.sh", "--allow-large", "--full", cwd=repo, env=env).stdout)


class PipelineFixes(fixture.ProjectFixture):

    def test_unreadable_code_stops_aggregate(self):
        rd, _ = self.collect("feature")
        meta = Path(rd, "meta.txt")
        meta.write_text(meta.read_text().replace("head_checked_out=no", "head_checked_out=no\ncode_ref=UNAVAILABLE"))
        Path(rd, "candidates.json").write_text("[]")
        self.run_script("prepare-verify.sh", rd)
        Path(rd, "verify", "result-1.json").write_text("[]")
        r = self.run_script("aggregate.sh", rd, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("读不到被审查的代码", r.stderr)

    def test_prev_candidate_is_reverified_from_scratch(self):
        rd, _ = self.collect("feature")
        c = {"file": "src/orders/paginate.ts", "line": 2, "category": "bug", "severity": "major", "summary": "A",
             "failure_scenario": "x", "from_prev": True, "id": "1-1", "since": "t", "confidence": 95, "verdict": "旧结论",
             "evidence": [{"file": "x", "lines": "1"}], "no": 3}
        Path(rd, "candidates.json").write_text(json.dumps([c]))
        self.run_script("prepare-verify.sh", rd)
        sent = self.load(os.path.join(rd, "verify", "batch-1.json"))[0]
        for k in ("confidence", "verdict", "evidence", "no"):
            self.assertNotIn(k, sent)
        self.assertEqual((sent["id"], sent["from_prev"]), ("1-1", True))

    def test_quick_report_does_not_claim_rules_and_notes_missing_guard(self):
        rd, _ = self.collect("feature", "--depth", "quick")
        self.run_pipeline(rd, [], {})
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("quick 不对照项目规则", out)
        checked = next(l for l in out.splitlines() if l.startswith("CHECKED:"))
        self.assertNotIn("项目规则", checked)
        self.assertIn("GUARD:", out)
        Path(rd, ".guard-active").write_text("")
        self.assertNotIn("GUARD:", self.run_script("report-lines.sh", rd).stdout)


class JudgedAfterReview(fixture.ProjectFixture):

    def test_judged_carried_finding_is_dropped_and_renumbered(self):
        rd, _ = self.collect("feature")
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "bug", "severity": "critical", "summary": "B", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "security", "severity": "major", "summary": "C", "failure_scenario": "x"}]
        self.run_pipeline(rd, cands, {k: (90, [{"file": f, "lines": l}]) for k, f, l in (("A", P, "2"), ("B", L, "1"), ("C", L, "1"))})
        Path(rd, "report.md").write_text("r\n")
        self.run_script("save-review.sh", rd)
        b = next(x for x in self.load(os.path.join(rd, "findings.json")) if x["summary"] == "B")
        self.run_script("feedback.sh", "invalid", b["id"], "上游已经校验", cwd=self.repo)
        self.git(self.repo, "switch", "-q", "feature")
        self.commit(self.repo, {P: "export function page(t: number, s: number) {\n  return Math.round(t / s)\n}\n"}, "再改")
        self.git(self.repo, "switch", "-q", "main")
        rd2, _ = self.collect("feature")
        self.assertEqual(self.kv(os.path.join(rd2, "meta.txt"))["review_scope"], "incremental")
        a = dict(next(x for x in self.load(os.path.join(rd2, "prev-findings.json")) if x["summary"] == "A"), from_prev=True)
        final, _ = self.run_pipeline(rd2, [a], {"A": (10, [{"file": P, "lines": "2"}])})
        shown = [x for g in final["groups"].values() for x in g]
        self.assertEqual([x["summary"] for x in shown], ["C"])
        self.assertEqual(shown[0]["no"], 1)
        self.assertEqual([x["summary"] for x in final["ignored_by_history"]], ["B"])


class RuleCompat(fixture.Base):
    def rules(self, files, changed):
        repo = self.make_repo("rules", files)
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, changed, "改")
        self.git(repo, "switch", "-q", "main")
        rd = self.review_dir(self.run_script("collect-diff.sh", "feature", cwd=repo).stdout)
        self.run_script("collect-rules.sh", rd)
        return self.load(os.path.join(rd, "rules-index.json")), Path(rd, "rules-meta.txt").read_text(encoding="utf-8")

    def test_english_do_not_report_and_no_includes(self):
        blocks, _ = self.rules({
            "REVIEW.md": "# Review\n\n@review-api.md\n\n## Do not report\n\n- generated files\n",
            "review-api.md": "- API 路由要有集成测试。\n",
            ".cursor/BUGBOT.md": "- 不再读取\n",
            "src/a.ts": "a\n"}, {"src/a.ts": "b\n"})
        root = next(b for b in blocks if b["file"] == "REVIEW.md")
        self.assertIn("@review-api.md", root["text"])
        self.assertNotIn("API 路由要有集成测试", root["text"])
        self.assertIn("generated files", root["suppress"])
        self.assertNotIn("generated files", root["text"])
        self.assertNotIn(".cursor/BUGBOT.md", {b["file"] for b in blocks})


class SeverityDefinitions(fixture.Base):
    """严重程度和降级规则只在文档里,没有可执行的行为;这里只防止关键定义被以后的改动悄悄删掉。"""
    def test_critical_types_and_no_downgrade_for_scope(self):
        cr = os.path.dirname(fixture.SCRIPTS)
        schema = Path(cr, "references", "finding-schema.md").read_text(encoding="utf-8")
        section = schema[schema.index("## 严重程度"):schema.index("## verifier 填写的字段")]
        for phrase in ("数据丢失", "fire-and-forget", "现有调用方已经会因此崩溃", "安全边界被破坏", "影响范围窄"):
            self.assertIn(phrase, section)
        verifier = Path(cr, "roles", "verifier.md").read_text(encoding="utf-8")
        self.assertIn("不因为影响范围窄", verifier)
        rubric = Path(cr, "references", "scoring-rubric.md").read_text(encoding="utf-8")
        self.assertIn("后果还没在现有调用方里出现", rubric)
