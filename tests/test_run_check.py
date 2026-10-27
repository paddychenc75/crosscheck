"""运行记录:run-check.sh、aggregate 对「已复现」的核对和报告标记。"""
import json
import os
from pathlib import Path

import fixture

P = "src/orders/paginate.ts"
EV = [{"file": P, "lines": "1-2"}]


class RunCheckScript(fixture.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.git(self.repo, "switch", "-q", "feature")
        self.rd, _ = self.collect()

    def run_check(self, *argv, kind="repro", check=True):
        return self.run_script("run-check.sh", self.rd, "--kind", kind, "--", *argv, check=check)

    def test_rejects_unsafe_or_ambiguous_commands(self):
        for argv in (["pytest tests/x.py"], ["git", "status"], ["npm", "install"], ["eslint", "--fix", "a.ts"],
                     ["sh", "-c", "pytest"], ["python3", "-m", "pip", "install", "x"]):
            with self.subTest(argv=argv):
                r = self.run_check(*argv, check=False)
                self.assertEqual(r.returncode, 2, r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.rd, "runs")) and os.listdir(os.path.join(self.rd, "runs")))

    def test_records_run(self):
        code = "print('===== 2 passed, 1 failed in 0.10s ====='); raise SystemExit(1)"
        r = self.run_check("python3", "-c", code, kind="test")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "CR-RUN id=r1 exit=1")
        run = self.load(os.path.join(self.rd, "runs", "r1.json"))
        self.assertEqual((run["exit"], run["tests_run"], run["tests_failed"], run["kind"]), (1, 3, 1, "test"))
        self.assertEqual(self.run_check("python3", "-c", "pass").stdout.strip().splitlines()[-1], "CR-RUN id=r2 exit=0")


class RunVerification(fixture.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.git(self.repo, "switch", "-q", "feature")
        self.rd, _ = self.collect()

    def repro(self, code="raise SystemExit(1)", kind="repro"):
        out = self.run_script("run-check.sh", self.rd, "--kind", kind, "--", "python3", "-c", code).stdout
        return out.strip().splitlines()[-1].split()[1].split("=")[1]

    def pipeline(self, conf, verification=None, extra=None):
        c = {"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"}
        Path(self.rd, "candidates.json").write_text(json.dumps([c]))
        self.run_script("prepare-verify.sh", self.rd)
        r = dict(self.load(os.path.join(self.rd, "verify", "batch-1.json"))[0], confidence=conf, verdict="v", evidence=EV)
        if verification is not None:
            r["verification"] = verification
        r.update(extra or {})
        Path(self.rd, "verify", "result-1.json").write_text(json.dumps([r]))
        self.run_script("aggregate.sh", self.rd)
        final = self.load(os.path.join(self.rd, "final.json"))
        shown = final["groups"]["defects"]
        rejected = self.load(os.path.join(self.rd, "rejected.json"))
        return final, (shown[0] if shown else None), (rejected[0] if rejected else None)

    def executed(self, rid, outcome="reproduced"):
        return {"method": "executed", "runs": [rid], "outcome": outcome}

    def test_recorded_repro_is_marked_with_real_log(self):
        rid = self.repro("print('boom'); raise SystemExit(1)")
        final, shown, _ = self.pipeline(100, self.executed(rid))
        self.assertEqual((shown["confidence"], shown["mark"]), (100, "已复现"))
        self.assertEqual(shown["repro"]["exit"], 1)
        self.assertIn("boom", shown["repro"]["tail"])
        self.assertEqual((final["runs"]["total"], final["runs"]["reproduced"]), (1, 1))

    def assert_failed_claim(self, verification, extra=None, why=""):
        final, shown, rejected = self.pipeline(100, verification, extra)
        self.assertIsNone(shown)
        self.assertEqual((rejected["confidence"], rejected["verification"]["method"]), (60, "inferred"))
        self.assertIn(why, rejected["reject_reason"])
        self.assertEqual(final["runs"]["failed_claims"], 1)

    def test_missing_record_fails(self):
        self.assert_failed_claim(self.executed("r9"), why="没有对应的运行记录")

    def test_free_form_repro_is_not_trusted(self):
        self.assert_failed_claim(None, {"repro": {"cmd": "pytest", "result": "failed"}}, why="run-check.sh")

    def test_inferred_is_not_capped_and_report_lines_show_marks(self):
        _, shown, _ = self.pipeline(100)
        self.assertEqual((shown["confidence"], shown["mark"]), (100, "读代码推断"))
        out = self.run_script("report-lines.sh", self.rd).stdout
        self.assertIn("MARK: 1\t读代码推断", out)
        self.assertNotIn("运行 ", next(l for l in out.splitlines() if l.startswith("CHECKED:")))

    def test_checked_line_counts_runs(self):
        rid = self.repro()
        self.repro("pass", kind="check")
        self.pipeline(100, self.executed(rid))
        out = self.run_script("report-lines.sh", self.rd).stdout
        checked = next(l for l in out.splitlines() if l.startswith("CHECKED:"))
        self.assertIn("运行 2 次 · 复现成功 1", checked)
        self.assertIn("MARK: 1\t已复现", out)

    def test_batches_hold_three_and_critical_alone(self):
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "m%d" % i, "failure_scenario": "x"} for i in range(4)]
        cands.append({"file": P, "line": 2, "category": "security", "severity": "critical", "summary": "c", "failure_scenario": "x", "pass": "security"})
        Path(self.rd, "candidates.json").write_text(json.dumps(cands))
        out = self.run_script("prepare-verify.sh", self.rd).stdout
        self.assertIn("batches=3", out)
        sizes = sorted(len(self.load(os.path.join(self.rd, "verify", "batch-%d.json" % i))) for i in (1, 2, 3))
        self.assertEqual(sizes, [1, 1, 3])
        crit = next(self.load(os.path.join(self.rd, "verify", "batch-%d.json" % i)) for i in (1, 2, 3)
                    if self.load(os.path.join(self.rd, "verify", "batch-%d.json" % i))[0]["summary"] == "c")
        self.assertNotIn("pass", crit[0])
