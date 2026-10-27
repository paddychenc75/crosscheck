"""运行记录:run-check.sh、宿主见证、aggregate 对「已复现」的核对和报告标记。"""
import json
import os
import subprocess
from pathlib import Path

import test_crosscheck as fixture

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

    def test_records_run_and_prints_sentinel(self):
        code = "print('===== 2 passed, 1 failed in 0.10s ====='); raise SystemExit(1)"
        r = self.run_check("python3", "-c", code, kind="test")
        last = r.stdout.strip().splitlines()[-1]
        self.assertRegex(last, r"^CR-RUN id=r1 exit=1 sha=[0-9a-f]{16}$")
        run = self.load(os.path.join(self.rd, "runs", "r1.json"))
        self.assertEqual((run["exit"], run["tests_run"], run["tests_failed"], run["kind"]), (1, 3, 1, "test"))
        self.assertEqual(run["head_sha"], self.git(self.repo, "rev-parse", "HEAD"))
        self.assertFalse(run["worktree_changed"])
        self.assertEqual(self.run_check("python3", "-c", "pass").stdout.strip().splitlines()[-1][:12], "CR-RUN id=r2")


class RunVerification(fixture.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.git(self.repo, "switch", "-q", "feature")
        self.rd, _ = self.collect()

    def repro(self, code="raise SystemExit(1)", kind="repro"):
        out = self.run_script("run-check.sh", self.rd, "--kind", kind, "--", "python3", "-c", code).stdout
        rid = out.strip().splitlines()[-1].split()[1].split("=")[1]
        return rid, out

    # 宿主 hook:先让 guard 认为审查开始了(collect-diff.sh 执行完、输出 review_dir),再把 run-check.sh 的输出交给它。
    def hook(self, payload):
        payload.setdefault("session_id", "s1")
        payload.setdefault("cwd", self.repo)
        r = subprocess.run(["sh", os.path.join(fixture.SCRIPTS, "guard.sh")], input=json.dumps(payload),
                           text=True, capture_output=True, env=self.env, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)

    def start_guard(self, host="claude"):
        cmd = "sh /x/collect-diff.sh"
        if host == "cursor":
            self.hook({"hook_event_name": "beforeShellExecution", "command": cmd})
            self.hook({"hook_event_name": "afterShellExecution", "command": cmd, "output": "ok\n" + self.rd})
        else:
            self.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}})
            resp = {"stdout": "ok\n" + self.rd} if host == "claude" else "ok\n" + self.rd
            self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_response": resp})
        self.assertTrue(os.path.exists(os.path.join(self.rd, ".guard-active")))

    def witness(self, out, host="claude", event=None):
        cmd = "sh /x/run-check.sh %s --kind repro -- python3 r.py" % self.rd
        if host == "cursor":
            self.hook({"hook_event_name": "afterShellExecution", "command": cmd, "output": out})
        elif host == "codex":
            self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_response": out})
        else:
            self.hook({"hook_event_name": event or "PostToolUse", "tool_name": "Bash", "tool_input": {"command": cmd},
                       "tool_response": {"stdout": out}})

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

    def test_witnessed_repro_is_marked_and_keeps_full_score(self):
        for host in ("claude", "codex", "cursor"):
            with self.subTest(host=host):
                if host != "claude":
                    self.tearDown(); self.setUp()
                self.start_guard(host)
                rid, out = self.repro("print('boom'); raise SystemExit(1)")
                self.witness(out, host)
                final, shown, _ = self.pipeline(100, self.executed(rid))
                self.assertEqual((shown["confidence"], shown["mark"]), (100, "已复现"))
                self.assertEqual(shown["repro"]["exit"], 1)
                self.assertIn("boom", shown["repro"]["tail"])
                self.assertEqual((final["runs"]["total"], final["runs"]["witnessed"], final["runs"]["reproduced"]), (1, 1, 1))

    def test_claude_failure_event_is_witnessed_too(self):
        self.start_guard()
        rid, out = self.repro()
        self.witness(out, event="PostToolUseFailure")
        w = json.loads(Path(self.rd, "runs", "witness.jsonl").read_text().splitlines()[-1])
        self.assertEqual((w["id"], w["exit"], w["failed"]), (rid, 1, True))

    def test_no_hook_is_self_reported_and_capped(self):
        rid, _ = self.repro()
        _, shown, _ = self.pipeline(100, self.executed(rid))
        self.assertEqual((shown["confidence"], shown["mark"], shown["verification_status"]), (95, "已复现(自报)", "self_reported"))

    def assert_failed_claim(self, verification, extra=None, why=""):
        final, shown, rejected = self.pipeline(100, verification, extra)
        self.assertIsNone(shown)
        self.assertEqual((rejected["confidence"], rejected["verification"]["method"]), (60, "inferred"))
        self.assertIn(why, rejected["reject_reason"])
        self.assertEqual(final["runs"]["failed_claims"], 1)

    def test_guarded_run_without_witness_fails(self):
        self.start_guard()
        rid, _ = self.repro()
        self.assert_failed_claim(self.executed(rid), why="没有宿主的执行见证")

    def test_witness_exit_mismatch_fails(self):
        self.start_guard()
        rid, out = self.repro()
        self.witness(out.replace("exit=1", "exit=0"))
        self.assert_failed_claim(self.executed(rid), why="退出码")

    def test_forged_or_tampered_records_fail(self):
        rid, _ = self.repro()
        self.assert_failed_claim(self.executed("r9"), why="没有运行记录")
        Path(self.rd, "runs", rid + ".log").write_text("forged")
        self.assert_failed_claim(self.executed(rid), why="对不上")

    def test_run_before_review_started_fails(self):
        rid, _ = self.repro()
        p = Path(self.rd, "runs", rid + ".json")
        run = json.loads(p.read_text()); run["started"] = 1.0
        p.write_text(json.dumps(run))
        self.assert_failed_claim(self.executed(rid), why="早于本次审查开始")

    def test_test_run_with_zero_tests_fails(self):
        rid, _ = self.repro("print('collected 0 items')", kind="test")
        self.assert_failed_claim(self.executed(rid, "refuted"), why="没有跑到任何测试")

    def test_reproduced_claim_needs_a_failing_run(self):
        rid, _ = self.repro("pass")
        self.assert_failed_claim(self.executed(rid), why="没有一次运行按预测失败")

    def test_free_form_repro_is_not_trusted(self):
        self.assert_failed_claim(None, {"repro": {"cmd": "pytest", "result": "failed"}}, why="run-check.sh")

    def test_inferred_is_not_capped_and_report_lines_show_marks(self):
        _, shown, _ = self.pipeline(100)
        self.assertEqual((shown["confidence"], shown["mark"]), (100, "读代码推断"))
        Path(self.rd, "display-groups.json").write_text("[]")
        out = self.run_script("report-lines.sh", self.rd).stdout
        self.assertIn("MARK: 1\t读代码推断", out)
        self.assertNotIn("运行 ", next(l for l in out.splitlines() if l.startswith("CHECKED:")))

    def test_checked_line_counts_runs(self):
        self.start_guard()
        rid, out = self.repro()
        self.witness(out)
        self.repro("pass", kind="check")
        self.pipeline(100, self.executed(rid))
        Path(self.rd, "display-groups.json").write_text("[]")
        out = self.run_script("report-lines.sh", self.rd).stdout
        checked = next(l for l in out.splitlines() if l.startswith("CHECKED:"))
        self.assertIn("运行 2 次(见证 1) · 复现成功 1", checked)
        self.assertIn("MARK: 1\t已复现", out)
