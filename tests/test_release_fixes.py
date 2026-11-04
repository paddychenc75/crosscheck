"""0.2.0 发布前调研发现的问题的回归测试。"""
import json
import os
import sys
import time
from pathlib import Path

import fixture
sys.path.insert(0, fixture.SCRIPTS)
import intent


def save_baseline(t, rd):
    for name, text in (("report.md", "r\n"), ("findings.json", "[]"), ("completeness.txt", "OK: 查找角色和核实结果都已落盘\n")):
        if not os.path.exists(os.path.join(rd, name)):
            Path(rd, name).write_text(text, encoding="utf-8")
    t.run_script("save-review.sh", rd)
    time.sleep(1.1)   # 记录目录精确到秒


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

    def test_unchanged_finding_rechecks_feedback_and_external_evidence(self):
        import crlib
        self.write(self.repo, {"caller.ts": "original caller\n"})
        old = {"id": "old-1", "file": "a.ts", "line": 1, "category": "bug",
               "summary": "old finding", "evidence": [{"file": "caller.ts"}]}
        old["evidence_snapshot"] = crlib.evidence_snapshot(old, crlib.Source(self.repo))
        first = self.collect()
        Path(first, "findings.json").write_text(json.dumps([old]))
        save_baseline(self, first)
        self.assertEqual(Path(self.collect(), "files.txt").read_text(), "")
        # 未通过符号依赖匹配的调用方变化也必须重核主文件。
        self.write(self.repo, {"caller.ts": "changed caller\n"})
        rd = self.collect()
        self.assertIn("a.ts", Path(rd, "files.txt").read_text().splitlines())
        self.write(self.repo, {"caller.ts": "original caller\n"})
        self.run_script("feedback.sh", "wontfix", "old-1", "暂时接受", "--repo", self.repo)
        rd = self.collect()
        self.assertIn("a.ts", Path(rd, "files.txt").read_text().splitlines())
        self.assertTrue(Path(rd, "batches.txt").read_text().strip())
        self.assertTrue(self.load(Path(rd, "history.json"))[0]["same_code"])
        Path(rd, "candidates.json").write_text("[]")
        self.run_script("prepare-verify.sh", rd)
        self.assertTrue(any(c.get("from_prev") for c in self.load(Path(rd, "candidates.json"))))

    def test_remote_requirement_body_changes_force_full(self):
        self.config("tools.doc_reader = 读取需求文档\nreview.ask = none\n")
        def staged(name, body, error=""):
            stage = Path(self.tmp, name)
            stage.mkdir()
            (stage / "doc.md").write_text("url: https://example.test/prd\ntitle: PRD\nerror: %s\n---\n%s" % (error, body))
            return str(stage)
        first = self.collect("--source", "doc=https://example.test/prd", "--source-dir", staged("one", "验收 A"))
        save_baseline(self, first)
        same = self.collect("--source-dir", staged("two", "验收 A"))
        self.assertEqual(self.scope(same), "incremental")
        changed = self.collect("--source-dir", staged("three", "验收 B"))
        self.assertEqual(self.scope(changed), "full")
        failed = self.collect("--source-dir", staged("failed", "", "无权限"))
        self.assertEqual(self.scope(failed), "full")
        self.assertIn("无权限", Path(failed, "context-notes.txt").read_text())

    def test_large_quick_continuation_keeps_initial_confirmation(self):
        self.env.update(CR_BATCH_LINES="1", CR_LARGE_BATCHES="1")
        initial = self.run_script("collect-diff.sh", "--depth", "quick", cwd=self.repo)
        self.assertIn("LARGE:", initial.stdout)
        continued = self.run_script("collect-diff.sh", "--depth", "quick", "--allow-large", cwd=self.repo)
        self.assertNotIn("LARGE:", continued.stdout)
        self.assertTrue(Path(self.review_dir(continued.stdout), "batches.txt").read_text().strip())

    def test_issue_body_fingerprint_uses_unclipped_content(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        api = SimpleNamespace(run=lambda args: "", why_unreadable=lambda: "无权限")
        rd = self.collect()
        meta = self.kv(Path(rd, "meta.txt"))
        self.config("tools.requirement = 读取需求单\nreview.ask = none\n")
        def fingerprint(body):
            issue = None if body is None else {"title": "需求", "description": body}
            with patch.object(intent, "issue_refs", return_value={"42"}), patch.object(intent, "read_issue", return_value=issue), patch.dict(os.environ, self.env):
                it, _ = intent.collect(api, meta, rd)
            self.run_py("context_hash.py", rd)
            return Path(rd, "context-hash.txt").read_text(), it
        original, _ = fingerprint("x" * 5000 + "A")
        same, _ = fingerprint("x" * 5000 + "A")
        changed, _ = fingerprint("x" * 5000 + "B")
        failed, it = fingerprint(None)
        self.assertEqual(original, same)
        self.assertNotEqual(original, changed)
        self.assertNotEqual(original, failed)
        self.assertTrue(any("没有读到" in n for n in it.notes))

    def test_with_tests_toggle_forces_full(self):
        save_baseline(self, self.collect("--with-tests"))
        self.assertEqual(self.scope(self.collect()), "full")

    def test_user_intent_change_forces_full(self):
        save_baseline(self, self.collect())
        self.assertEqual(self.scope(self.collect("--intent", "修复分页")), "full")

    def test_intent_and_docs_carry_over_until_cleared(self):
        Path(self.repo, "plan.md").write_text("方案\n", encoding="utf-8")
        save_baseline(self, self.collect("--intent", "修复分页", "--doc", "plan.md"))
        rd = self.collect()
        self.assertEqual(self.scope(rd), "incremental")
        sources = self.kv(os.path.join(rd, "meta.txt"))["intent_sources"]
        self.assertIn("的补充", sources)
        self.assertIn("的文档 plan.md", sources)
        self.assertIn("修复分页", Path(rd, "intent.md").read_text(encoding="utf-8"))
        rd = self.collect("--intent", "")
        self.assertEqual(self.scope(rd), "full")
        self.assertNotIn("修复分页", Path(rd, "intent.md").read_text(encoding="utf-8"))
        self.assertNotIn("修复分页", Path(self.collect(), "intent.md").read_text(encoding="utf-8"))

    def test_quick_in_between_keeps_the_standard_comparison_point(self):
        first = self.collect()
        save_baseline(self, first)
        quick = self.collect("--depth", "quick")
        self.assertEqual(self.scope(quick), "incremental")
        save_baseline(self, quick)
        rd = self.collect()
        self.assertEqual(self.scope(rd), "incremental")
        self.assertIn("标准审查", self.run_script("report-lines.sh", rd, check=False).stdout)
        store = self.kv(os.path.join(first, "meta.txt"))["store_dir"]
        standard = sorted(p for p in os.listdir(store) if p[0].isdigit())[0]
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["prev_review"], standard)

    def test_quick_stays_comparable_with_quick(self):
        save_baseline(self, self.collect("--depth", "quick"))
        self.assertEqual(self.scope(self.collect("--depth", "quick")), "incremental")
        self.assertEqual(self.scope(self.collect()), "full")

    def test_review_missing_a_finder_is_not_a_comparison_point(self):
        rd = self.collect()
        Path(rd, "completeness.txt").write_text("INCOMPLETE: 缺 finders/reviewer.json\n", encoding="utf-8")
        save_baseline(self, rd)
        self.assertEqual(self.scope(self.collect()), "full")

    def test_any_incomplete_review_is_not_a_comparison_point(self):
        for line in ("finders/reviewer.json 覆盖受限:没看完 a.ts", "finders/reviewer.json 达到候选预算,覆盖可能受限(未交候选数量未知)",
                     "finders/reviewer.json 未声明覆盖限制(落盘或读过不代表完整覆盖)", "缺 analysis-facts.json"):
            with self.subTest(line=line):
                rd = self.collect()
                Path(rd, "completeness.txt").write_text("INCOMPLETE: %s\n" % line, encoding="utf-8")
                save_baseline(self, rd)
                self.assertEqual(self.scope(self.collect()), "full")

    def test_review_without_completeness_is_not_a_comparison_point(self):
        rd = self.collect()
        save_baseline(self, rd)
        os.remove(os.path.join(Path(rd, "record.txt").read_text(encoding="utf-8").strip(), "completeness.txt"))
        self.assertEqual(self.scope(self.collect()), "full")

    def test_incomplete_review_falls_back_to_the_last_complete_one(self):
        good = self.collect()
        save_baseline(self, good)
        stamp = os.path.basename(Path(good, "record.txt").read_text(encoding="utf-8").strip())
        limited = self.collect()
        Path(limited, "completeness.txt").write_text("INCOMPLETE: finders/reviewer.json 覆盖受限:x\n", encoding="utf-8")
        save_baseline(self, limited)
        self.assertEqual(self.kv(os.path.join(self.collect(), "meta.txt"))["prev_review"], stamp)

    def test_saved_intent_and_docs_stay_with_their_target(self):
        """整个分支和只审一个路径是两个目标,但共用同一份分支记录,相当于不同 fork 的同名分支。"""
        Path(self.repo, "plan.md").write_text("路径的方案\n", encoding="utf-8")
        self.collect("a.ts", "--intent", "路径的意图", "--doc", "plan.md")
        rd = self.collect()
        self.assertNotIn("路径的", Path(rd, "intent.md").read_text(encoding="utf-8"))
        self.collect("--intent", "", "--doc", "")   # 别的目标清除自己的,不动这一条
        self.assertIn("路径的意图", Path(self.collect("a.ts"), "intent.md").read_text(encoding="utf-8"))

    def test_same_branch_name_in_two_prs_does_not_share_intent(self):
        store = os.path.join(self.tmp, "records", "pr-1")
        for name in ("one", "two"):
            os.makedirs(os.path.join(self.tmp, name))
        one = dict(store_dir=store, source_branch="patch-1", target_key="pr-1")
        Path(self.tmp, "one", "intent-arg.txt").write_text("第一个 PR 的意图", encoding="utf-8")
        Path(self.tmp, "one", "doc-files.txt").write_text("one.md\n", encoding="utf-8")
        intent.user_material(one, os.path.join(self.tmp, "one"))
        two = dict(one, target_key="pr-2")
        self.assertEqual(intent.user_material(two, os.path.join(self.tmp, "two")), ("", [], "", ""))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "two", "intent-arg.txt")))

    def test_docs_can_be_cleared(self):
        Path(self.repo, "plan.md").write_text("方案内容\n", encoding="utf-8")
        self.collect("--doc", "plan.md")
        self.assertIn("方案内容", Path(self.collect(), "intent.md").read_text(encoding="utf-8"))
        self.assertNotIn("方案内容", Path(self.collect("--doc", ""), "intent.md").read_text(encoding="utf-8"))
        self.assertNotIn("方案内容", Path(self.collect(), "intent.md").read_text(encoding="utf-8"))

    def test_unreadable_doc_does_not_count_as_provided(self):
        self.config("tools.doc_reader = 用 acme-kit:doc-reader 读文档\nreview.ask = doc\n")
        plan = Path(self.repo, "plan.md")
        plan.write_text("方案\n", encoding="utf-8")
        self.assertNotIn("SOURCES_MISSING", self.run_script("collect-diff.sh", "--doc", "plan.md", cwd=self.repo).stdout)
        self.assertNotIn("SOURCES_MISSING", self.run_script("collect-diff.sh", cwd=self.repo).stdout)
        plan.unlink()
        self.assertIn("SOURCES_MISSING: 需求文档", self.run_script("collect-diff.sh", cwd=self.repo).stdout)

    def test_help_lists_full(self):
        help_text = self.run_script("collect-diff.sh", "--help").stdout
        self.assertIn("--full 忽略历史", help_text)
        self.assertIn("--no-store", help_text)
        self.assertNotIn("set -eu", help_text)

    def test_only_rule_files_change_the_context(self):
        save_baseline(self, self.collect())
        self.commit(self.repo, {".claude/settings.json": "{}\n"}, "宿主设置")
        rd = self.collect()
        self.assertEqual(self.scope(rd), "incremental")
        self.commit(self.repo, {".claude/rules/a.md": "- 规则\n"}, "规则")
        self.assertEqual(self.scope(self.collect()), "full")


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

    def test_quick_report_does_not_claim_rules(self):
        rd, _ = self.collect("feature", "--depth", "quick")
        self.run_pipeline(rd, [], {})
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("quick 不对照项目规则", out)
        checked = next(l for l in out.splitlines() if l.startswith("CHECKED:"))
        self.assertNotIn("项目规则", checked)
        self.assertNotIn("GUARD:", out)


class RejectedFeedback(fixture.ProjectFixture):
    def test_rejected_candidate_can_be_confirmed(self):
        rd, _ = self.collect("feature")
        P = "src/orders/paginate.ts"
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"}]
        self.run_pipeline(rd, cands, {"A": (70, [{"file": P, "lines": "2"}])})
        Path(rd, "report.md").write_text("r\n")
        saved = self.kv_stdout(self.run_script("save-review.sh", rd).stdout)["dir"]
        row = self.load(os.path.join(saved, "rejected.json"))[0]
        self.assertRegex(row["id"], r"^\d{14}-r1$")
        self.assertTrue(row["context_hash"])
        self.run_script("feedback.sh", "valid", row["id"], cwd=self.repo)
        store = os.path.dirname(os.path.dirname(saved))
        entry = json.loads(Path(store, "feedback.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((entry["verdict"], entry["summary"]), ("valid", "A"))

    def kv_stdout(self, text):
        return dict(l.split("=", 1) for l in text.splitlines() if "=" in l)


class JudgedAfterReview(fixture.ProjectFixture):

    def test_judged_carried_finding_is_dropped_and_renumbered(self):
        rd, _ = self.collect("feature")
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "bug", "severity": "critical", "summary": "B", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "security", "severity": "major", "summary": "C", "failure_scenario": "x"}]
        self.run_pipeline(rd, cands, {k: (90, [{"file": f, "lines": l}]) for k, f, l in (("A", P, "2"), ("B", L, "1"), ("C", L, "1"))})
        os.makedirs(os.path.join(rd, "finders"))
        for name in ("reviewer.json", "rules-auditor.json"):
            Path(rd, "finders", name).write_text('{"candidates": [], "coverage": {"limited": false}}')
        self.run_script("aggregate.sh", rd)
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
        Path(rd2, "candidates.json").write_text(json.dumps([a]))
        self.run_script("prepare-verify.sh", rd2)
        rows = [x for path in Path(rd2, "verify").glob("batch-*.json") for x in self.load(path)]
        self.assertEqual({x["summary"] for x in rows}, {"A", "B", "C"}, "历史判断触发主文件重审,同文件发现一起重核")
        results = [dict(x, confidence=90 if x["summary"] == "C" else 0,
                        verdict="独立问题仍成立" if x["summary"] == "C" else "当前调用方仍有守卫", evidence=[{"file": x["file"], "lines": "1"}],
                        **({"history_match": b["id"], "history_context": "重新读调用方确认守卫仍覆盖该输入"} if x["summary"] == "B" else {})) for x in rows]
        Path(rd2, "verify", "result-1.json").write_text(json.dumps(results))
        self.run_script("aggregate.sh", rd2)
        final = self.load(Path(rd2, "final.json"))
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
