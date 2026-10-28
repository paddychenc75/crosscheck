"""流程完整性:查找角色的回复和核实结果是否落盘(completeness.py),以及 save-review 从文件数记录。不调用模型。"""
import json
import os

import fixture

P = "src/orders/paginate.ts"
CAND = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "分页向下取整", "failure_scenario": "x"}]
SCORE = {"分页向下取整": (90, [{"file": P, "lines": "2"}])}


class FlowCompleteness(fixture.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.rd, _ = self.collect("feature")

    def finders(self, *names):
        os.makedirs(os.path.join(self.rd, "finders"), exist_ok=True)
        for n in names:
            with open(os.path.join(self.rd, "finders", n), "w", encoding="utf-8") as f:
                f.write("[]")

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
