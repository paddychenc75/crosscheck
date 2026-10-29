"""流程完整性:查找角色的回复和核实结果是否落盘(completeness.py),以及 save-review 从文件数记录。不调用模型。"""
import json
import os
import subprocess
from pathlib import Path

import fixture

P = "src/orders/paginate.ts"
CAND = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "分页向下取整", "failure_scenario": "x"}]
SCORE = {"分页向下取整": (90, [{"file": P, "lines": "2"}])}


class FlowCompleteness(fixture.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.rd, _ = self.collect("feature")

    def collect(self, *args):
        # 基准和需求资料都回答过了,才算真正开始审查(没回答时在问用户,不留进行中标记)
        return super().collect(*args, "--base-rule", "target", "--source", "doc=skip")

    def finders(self, *names):
        os.makedirs(os.path.join(self.rd, "finders"), exist_ok=True)
        for n in names:
            with open(os.path.join(self.rd, "finders", n), "w", encoding="utf-8") as f:
                json.dump({"candidates": [], "coverage": {"budget_reached": False, "limited": False}} if n.startswith("reviewer") else [], f)

    def completeness(self):
        with open(os.path.join(self.rd, "completeness.txt"), encoding="utf-8") as f:
            return f.read().splitlines()

    def save(self, *args, check=True):
        with open(os.path.join(self.rd, "report.md"), "w", encoding="utf-8") as f:
            f.write("报告\n")
        return self.run_script("save-review.sh", self.rd, *args, check=check)

    def test_complete_flow_has_no_incomplete_line(self):
        self.finders("reviewer.json", "rules-auditor.json")
        self.run_pipeline(self.rd, CAND, SCORE)
        self.assertEqual(self.completeness(), ["OK: 查找角色和核实结果都已落盘"])
        self.assertNotIn("INCOMPLETE:", self.run_script("report-lines.sh", self.rd).stdout)
        out = self.save().stdout
        self.assertNotIn("incomplete=", out)

    def test_missing_reviewer_is_reported_but_report_still_builds(self):
        self.finders("rules-auditor.json")
        final, _ = self.run_pipeline(self.rd, CAND, SCORE)
        self.assertEqual(self.completeness(), ["INCOMPLETE: 缺 finders/reviewer.json"])
        self.assertEqual(len(final["groups"]["defects"]), 1, "流程不完整不阻止出报告")
        self.assertIn("INCOMPLETE: 流程不完整:缺 finders/reviewer.json",
                      self.run_script("report-lines.sh", self.rd).stdout)
        r = self.save("--finders", "2", "--verifiers", "9")
        saved = r.stdout.split("dir=")[1].splitlines()[0]
        meta = self.kv(os.path.join(saved, "meta.txt"))
        self.assertEqual((meta["run_finders"], meta["run_verifiers"]), ("1", "1"), "角色数和核实数从文件数出来,不用自报")
        self.assertIn("incomplete=缺 finders/reviewer.json", r.stdout)
        with open(os.path.join(saved, "report.md"), encoding="utf-8") as f:
            self.assertIn("流程不完整:缺 finders/reviewer.json", f.read())

    def test_standard_without_seen_subagent_usage_is_flagged(self):
        self.finders("rules-auditor.json")
        self.run_pipeline(self.rd, CAND, SCORE)
        with open(os.path.join(self.rd, "usage.txt"), "w", encoding="utf-8") as f:
            f.write("usage_total_tokens=100\nusage_subagents=none-seen\n")
        r = self.save()
        self.assertIn("incomplete=缺 finders/reviewer.json；没有看到子代理用量", r.stdout)

    def test_facts_failure_is_noted_and_recorded_as_missing_step(self):
        installed = self.install_plugin()
        Path(installed, "skills", "review", "scripts", "analysis_facts.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        r = subprocess.run(["sh", str(installed / "skills" / "review" / "scripts" / "collect-diff.sh"), "feature",
                            "--base-rule", "target", "--source", "doc=skip"],
                           cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOTE: analysis_facts.py 运行失败", r.stdout)
        self.assertNotIn("MR 说明", r.stdout)
        self.rd = self.review_dir(r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.rd, "analysis-facts.json")))
        self.finders("reviewer.json", "rules-auditor.json")
        self.run_pipeline(self.rd, CAND, SCORE)
        self.assertEqual(self.completeness(), ["INCOMPLETE: 缺 analysis-facts.json(代码块编号和调用方搜索结果没有生成)"])
        self.assertIn("incomplete=缺 analysis-facts.json", self.save().stdout)

    def test_unsaved_review_leaves_marker_and_next_run_says_so(self):
        store = self.kv(os.path.join(self.rd, "meta.txt"))["store_dir"]
        self.assertTrue(os.path.isfile(os.path.join(store, "pending")), "开始真正审查时留下进行中标记")
        # 没有保存就结束(标记还在),下一次对同一目标审查时要说出来
        _, out = self.collect("feature")
        self.assertIn("上一次对这个目标的审查(开始于", out)
        self.assertIn("没有保存", out)
        self.finders("reviewer.json", "rules-auditor.json")
        self.run_pipeline(self.rd, CAND, SCORE)
        self.save()
        self.assertFalse(os.path.exists(os.path.join(store, "pending")), "保存后标记清除")

    def test_configured_model_missing_from_session_is_noted(self):
        self.finders("reviewer.json", "rules-auditor.json")
        self.run_pipeline(self.rd, CAND, SCORE)
        with open(os.path.join(self.rd, "meta.txt"), "a", encoding="utf-8") as f:
            f.write("finder_model=claude-sonnet-5-5\n")
        with open(os.path.join(self.rd, "usage.txt"), "w", encoding="utf-8") as f:
            f.write("usage_total_tokens=100\nusage_subagents=included\nusage_models=claude-haiku-5-5\n")
        r = self.save()
        self.assertIn("model_note=配置的finder模型 claude-sonnet-5-5 没有出现在会话记录里", r.stdout)
        dest = r.stdout.split("dir=")[1].splitlines()[0]
        with open(os.path.join(dest, "report.md"), encoding="utf-8") as f:
            report = f.read()
        self.assertIn("实际模型:claude-haiku-5-5", report)
        self.assertIn("模型与配置不一致:配置的finder模型 claude-sonnet-5-5", report)

    def test_budget_limit_propagates_to_report_and_saved_record(self):
        self.finders("reviewer.json", "rules-auditor.json")
        with open(os.path.join(self.rd, "finders/reviewer.json"), "w") as f:
            json.dump({"candidates": CAND, "coverage": {"budget_reached": True, "limited": True,
                      "reason": "remaining paths not inspected"}}, f)
        final, _ = self.run_pipeline(self.rd, CAND, SCORE)
        self.assertEqual(final['trimmed'], {}, '查找预算与展示裁剪分别记录')
        lines = self.run_script('report-lines.sh', self.rd).stdout
        self.assertIn('达到候选预算,覆盖可能受限', lines)
        dest = self.save().stdout.split('dir=')[1].splitlines()[0]
        for name in ('report.md', 'completeness.txt'):
            with open(os.path.join(dest, name)) as f:
                self.assertIn('达到候选预算,覆盖可能受限', f.read(), name)
        self.assertTrue(os.path.isfile(os.path.join(dest, 'candidates.json')))
        self.assertTrue(os.path.isfile(os.path.join(dest, 'finders/reviewer.json')))

    def test_legacy_array_does_not_claim_complete_coverage(self):
        self.finders('reviewer.json', 'rules-auditor.json')
        with open(os.path.join(self.rd, 'finders/reviewer.json'), 'w') as f:
            json.dump([], f)
        self.run_pipeline(self.rd, [], {})
        self.assertIn('未声明覆盖限制', '\n'.join(self.completeness()))

    def test_candidates_without_verify_batch_are_reported(self):
        self.finders("reviewer.json", "rules-auditor.json")
        with open(os.path.join(self.rd, "candidates.json"), "w", encoding="utf-8") as f:
            json.dump(CAND, f, ensure_ascii=False)
        r = self.run_script("aggregate.sh", self.rd, check=False)
        self.assertEqual(r.returncode, 2, "候选没有核实结果时汇总照旧拒绝")
        self.assertEqual(self.completeness(), ["INCOMPLETE: 缺 verify/batch-*.json(核实批次没有生成)"], "汇总拒绝时缺项也要写出")

    def test_verify_result_missing_for_one_batch_is_reported(self):
        self.finders("reviewer.json", "rules-auditor.json")
        # 同一个文件的 4 条候选分成两批(每批最多 3 条)
        cands = [dict(CAND[0], summary="分页向下取整 %d" % i) for i in range(4)]
        with open(os.path.join(self.rd, "candidates.json"), "w", encoding="utf-8") as f:
            json.dump(cands, f, ensure_ascii=False)
        self.run_script("prepare-verify.sh", self.rd)
        batch1 = self.load(os.path.join(self.rd, "verify", "batch-1.json"))
        rows = [dict(c, confidence=90, verdict="读过代码", evidence=[{"file": P, "lines": "2"}]) for c in batch1]
        with open(os.path.join(self.rd, "verify", "result-1.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        r = self.run_script("aggregate.sh", self.rd, check=False)
        self.assertEqual(r.returncode, 2, "缺核实结果时汇总仍然拒绝")
        self.assertEqual(self.completeness(), ["INCOMPLETE: 缺 verify/result-2.json(核实批次 2 有 1 条没有核实结果)"])
