"""不保存的跨技能合同:长期数据只读,运行材料、问询和反馈按明确目录延续。"""
import json
import os
from pathlib import Path
import subprocess
import unittest

import fixture
import test_code_host as code_host
import test_mining_collection as mining


def inside(path, root):
    return os.path.commonpath([str(path), str(root)]) == str(root)


def tree(root):
    """同时核对目录、内容和修改时间,能发现创建再删除的 probe/恢复写入。"""
    root = Path(root)
    if not root.exists():
        return None
    return {str(p.relative_to(root)): ("dir" if p.is_dir() else p.read_bytes(), p.stat().st_mtime_ns)
            for p in [root] + sorted(root.rglob("*"))}


class ReviewNoStore(fixture.ShopRepo):
    def ready(self, rd, fid="finding-current"):
        finding = {"id": fid, "no": 1, "file": "api/orders.ts", "line": 2,
                   "category": "bug", "severity": "major", "confidence": 90, "summary": "当前运行"}
        self.write(rd, {"report.md": "报告\n", "findings.json": json.dumps([finding]),
                        "completeness.txt": "OK\n"})
        return finding

    def test_temporary_collection_and_completion_never_touch_records(self):
        self.config("model.finder = configured-reader\ntools.doc_reader = 文档工具\n")
        before = tree(self.home)
        result, rd = self.collect("--no-store", "--source", "doc=skip", "--intent", "本轮意图")
        meta = self.kv(Path(rd, "meta.txt"))
        self.assertEqual(meta["storage_mode"], "temporary")
        self.assertEqual(meta["finder_model"], "configured-reader")
        self.assertTrue(inside(rd, self.env["TMPDIR"]))
        self.assertIn("本轮意图", Path(rd, "intent.md").read_text())
        self.ready(rd)
        saved = self.run_script("save-review.sh", rd)
        self.assertIn("storage_mode=temporary", saved.stdout)
        self.assertIn("completed=" + rd, saved.stdout)
        self.assertTrue(Path(rd, "completed.txt").is_file())
        self.assertFalse(Path(rd, "record.txt").exists())
        self.assertEqual(tree(self.home), before)
        self.assertTrue(Path(rd, "report.md").exists())
        denied = self.run_py("review_store.py", rd, meta["store_dir"], check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(tree(self.home), before)

    def test_absent_record_directory_is_not_created(self):
        Path(self.home).rmdir()
        result, rd = self.collect("--no-store")
        self.ready(rd)
        self.run_script("save-review.sh", rd)
        self.assertFalse(Path(self.home).exists())

    def test_sources_and_intent_overlay_survive_question_retry(self):
        self.config("tools.doc_reader = 文档工具\n")
        before = tree(self.home)
        first, rd = self.collect("--no-store")
        self.assertIn("SOURCES_MISSING:", first.stdout)
        self.run_py("sources.py", "set", rd, "doc", "skip")
        self.run_py("sources.py", "set", rd, "intent", "补充的本轮意图")
        # 仅凭已知目录继承 temporary;不依赖 agent 再传 --no-store。
        retry, again = self.collect("--out", rd)
        self.assertEqual(again, rd)
        self.assertNotIn("SOURCES_MISSING:", retry.stdout)
        self.assertEqual(self.kv(Path(rd, "meta.txt"))["storage_mode"], "temporary")
        self.assertIn("补充的本轮意图", Path(rd, "intent.md").read_text())
        self.assertEqual(tree(self.home), before)

    def test_history_remains_readable_without_refreshing_long_term_material(self):
        self.config("tools.doc_reader = 文档工具\n")
        _, saved_rd = self.collect("--source", "doc=skip", "--intent", "历史意图")
        self.ready(saved_rd)
        self.run_script("save-review.sh", saved_rd)
        before = tree(self.home)
        result, rd = self.collect("--no-store")
        self.assertIn("历史意图", Path(rd, "intent.md").read_text())
        self.assertEqual(self.kv(Path(rd, "meta.txt"))["review_scope"], "incremental")
        self.assertTrue(Path(rd, "prev-findings.json").exists())
        self.run_py("sources.py", "set", rd, "intent", "只改临时意图")
        self.assertEqual(tree(self.home), before)

    def test_feedback_binds_explicit_run_and_never_uses_latest(self):
        _, old = self.collect()
        self.ready(old, "old-id")
        self.run_script("save-review.sh", old)
        _, rd = self.collect("--no-store", "--full")
        self.ready(rd, "new-id")
        before = tree(self.home)
        self.run_script("feedback.sh", "valid", "new-id", "确认", "--review-dir", rd)
        entry = json.loads(Path(rd, "feedback.jsonl").read_text())
        self.assertEqual(entry["id"], "new-id")
        self.assertEqual(entry["review_dir"], rd)
        wrong = self.run_script("feedback.sh", "valid", "old-id", "--review-dir", rd, check=False)
        self.assertNotEqual(wrong.returncode, 0)
        missing = self.run_script("feedback.sh", "valid", "old-id", "--no-store", "--repo", self.repo, check=False)
        self.assertNotEqual(missing.returncode, 0)
        Path(rd, "meta.txt").unlink()
        gone = self.run_script("feedback.sh", "valid", "old-id", "--review-dir", rd, check=False)
        self.assertNotEqual(gone.returncode, 0)
        self.assertEqual(tree(self.home), before)

    def test_persistent_feedback_uses_context_frozen_at_save(self):
        _, rd = self.collect()
        self.ready(rd, "persisted-id")
        self.run_script("save-review.sh", rd)
        original = self.load(Path(rd, "findings.json"))[0]
        archive = Path(Path(rd, "record.txt").read_text().strip())
        archived = self.load(archive / "findings.json")[0]
        self.assertTrue(original["code_line"])
        self.assertTrue(original["context_hash"])
        self.assertEqual(original, archived)
        self.run_script("feedback.sh", "valid", "persisted-id", "--review-dir", rd)
        history = Path(rd, "feedback-check.json")
        self.run_script("feedback.sh", "history", rd, str(history))
        self.assertIs(self.load(history)[0]["same_code"], True)
        self.write(self.repo, {"api/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return {}\n}\n"})
        # 即使在改代码后才再次表态,判断也使用当时保存的锚点。
        self.run_script("feedback.sh", "disputed", "persisted-id", "修改后待核对", "--review-dir", rd)
        recorded = json.loads(Path(self.home, "reviews", "shop", "feedback.jsonl").read_text().splitlines()[-1])
        self.assertEqual(recorded["context_hash"], original["context_hash"])
        self.run_script("feedback.sh", "history", rd, str(history))
        self.assertIs(self.load(history)[0]["same_code"], False)

    def test_feedback_prompt_discloses_temporary_scope(self):
        _, rd = self.collect("--no-store")
        finding = self.ready(rd)
        self.write(rd, {"final.json": json.dumps({"groups": {"bug": [finding]}})})
        result = self.run_script("report-lines.sh", rd)
        feedback = next(line for line in result.stdout.splitlines() if line.startswith("FEEDBACK:"))
        self.assertIn("仅保留在本次临时目录", feedback)
        self.assertNotIn("供后续审查参考", feedback)

    def test_base_rule_and_long_term_out_are_rejected_before_probe(self):
        before = tree(self.home)
        for args in (("--no-store", "--base-rule", "target"),
                     ("--no-store", "--out", str(Path(self.home, "illegal")))):
            result = self.run_script("collect-diff.sh", *args, cwd=self.repo, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--no-store", result.stderr)
            self.assertEqual(tree(self.home), before)

    def test_relative_out_inherits_mode_from_requested_repo(self):
        _, rd = self.collect("--no-store")
        relative = os.path.relpath(rd, self.repo)
        before = tree(self.home)
        review = self.run_script("collect-diff.sh", "--repo", self.repo, "--base", "main", "--out", relative,
                                 cwd=self.tmp)
        self.assertEqual(self.review_dir(review.stdout), rd)
        describe = self.run_describe("collect.sh", "--repo", self.repo, "--base", "main", "--out", relative,
                                     "--non-interactive", cwd=self.tmp)
        self.assertEqual(self.review_dir(describe.stdout), rd)
        self.assertEqual(self.kv(Path(rd, "meta.txt"))["storage_mode"], "temporary")
        self.assertEqual(tree(self.home), before)

    def test_unreadable_mode_does_not_fall_back_to_persistent(self):
        if not hasattr(os, "geteuid") or os.geteuid() == 0:
            self.skipTest("root 用户下 chmod 不生效")
        _, rd = self.collect("--no-store")
        meta = Path(rd, "meta.txt")
        before = tree(self.home)
        meta.chmod(0)
        try:
            result, _ = self.collect("--out", rd, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("无法读取运行模式", result.stderr)
            self.assertEqual(tree(self.home), before)
        finally:
            meta.chmod(0o600)

    def test_unknown_mode_is_refused_by_downstream_commands(self):
        _, rd = self.collect("--no-store")
        self.ready(rd)
        with Path(rd, "meta.txt").open("a") as f:
            f.write("storage_mode=unexpected\n")
        before = tree(self.home)
        actions = [lambda: self.collect("--out", rd, check=False)[0],
                   lambda: self.run_script("save-review.sh", rd, check=False),
                   lambda: self.run_py("sources.py", "set", rd, "intent", "x", check=False),
                   lambda: self.run_script("feedback.sh", "valid", "finding-current", "--review-dir", rd, check=False),
                   lambda: self.run_describe("describe.sh", rd, check=False)]
        for action in actions:
            result = action()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("storage_mode", result.stderr)
            self.assertEqual(tree(self.home), before)

    def test_local_maintenance_initialization_obeys_same_mode(self):
        before = tree(self.home)
        work = Path(self.run_py("crlib.py", "init-work", self.repo, "--no-store").stdout.strip())
        meta = self.kv(work / "meta.txt")
        self.assertEqual(meta["storage_mode"], "temporary")
        self.assertEqual(meta["repo_root"], self.repo)
        self.assertTrue(inside(work, self.env["TMPDIR"]))
        self.assertEqual(tree(self.home), before)
        persistent = Path(self.run_py("crlib.py", "init-work", self.repo).stdout.strip())
        self.assertTrue(inside(persistent, Path(self.home, "maintenance")))
        self.assertEqual(self.kv(persistent / "meta.txt")["storage_mode"], "persistent")

    def test_hook_finishes_temporary_report_and_releases_session_state(self):
        import shlex
        # 仅复制到本测试夹具;安装路径与输出目录都包含空格。
        scripts = self.install_plugin() / 'skills/review/scripts'
        output = Path(self.env['TMPDIR'], 'review output'); output.mkdir()
        self.env['TMPDIR'] = str(output)
        guard = output / 'cr-guard'
        def hook(event, command, response=None):
            payload = dict(hook_event_name=event, session_id="no-store", tool_name="Bash",
                           tool_input={"command": command}, tool_response=response or {})
            result = subprocess.run(["sh", str(scripts / 'guard.sh')],
                                    input=json.dumps(payload), text=True, capture_output=True, env=self.env)
            self.assertEqual(result.returncode, 0, result.stderr)
        def execute(command):
            result = subprocess.run(['sh', '-c', command], cwd=self.repo, env=self.env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result
        collect = 'NOTE="two words" sh %s --no-store' % shlex.quote(str(scripts / 'collect-diff.sh'))
        hook("PreToolUse", collect)
        self.assertTrue((guard / 'no-store').exists())
        result = execute(collect)
        rd = self.review_dir(result.stdout)
        self.assertTrue(inside(rd, output))
        self.ready(rd)
        hook("PostToolUse", collect, {"stdout": result.stdout})
        self.assertEqual((guard / 'no-store.review').read_text().strip(), rd)
        finish = 'NOTE="two words" sh %s %s' % (shlex.quote(str(scripts / 'save-review.sh')), shlex.quote(rd))
        hook("PreToolUse", finish)
        self.assertEqual(Path(rd, 'usage.txt').read_text(), 'usage_status=no_start\n')
        result = execute(finish)
        self.assertIn('report=', result.stdout)
        hook("PostToolUse", finish, {"stdout": result.stdout})
        self.assertFalse((guard / "no-store").exists())
        self.assertFalse((guard / "no-store.review").exists())
        self.assertFalse((guard / "no-store.usage").exists())


class DescribeNoStore(fixture.ShopRepo):
    def test_render_and_author_updates_stay_in_run_and_default_remains_persistent(self):
        persistent = self.run_describe("collect.sh", "--base", "main", "--non-interactive", cwd=self.repo)
        previous = self.review_dir(persistent.stdout)
        self.run_describe("describe.sh", previous, "--tested", "旧的执行记录")
        before = tree(self.home)
        result = self.run_describe("collect.sh", "--base", "main", "--non-interactive", "--no-store", cwd=self.repo)
        rd = self.review_dir(result.stdout)
        rendered = self.run_describe("describe.sh", rd)
        self.assertIn("storage_mode=temporary", rendered.stdout)
        self.assertIn("旧的执行记录", Path(rd, "describe", "describe.md").read_text())
        self.run_describe("describe.sh", rd, "--tested", "本轮执行记录")
        self.assertEqual(self.load(Path(rd, "describe", "author.json"))["tested"], "本轮执行记录")
        self.assertTrue(Path(rd, "describe", "describe.html").exists())
        self.assertEqual(tree(self.home), before)

    def test_collection_failure_never_needs_to_restore_review_state(self):
        # 在 intent/sources 已处理后让 context_hash 失败,旧的事后恢复无法覆盖此分支。
        installed = self.install_plugin()
        scripts = installed / "skills/review/scripts"
        (scripts / "context_hash.py").write_text("raise SystemExit(9)\n")
        self.config("tools.doc_reader = 文档工具\n")
        _, persistent = self.collect("--source", "doc=skip")
        before = tree(self.home)
        before_reviews = tree(Path(self.home, "reviews"))
        for mode in ([], ["--no-store"]):
            result = self.run_describe("collect.sh", "--base", "main", "--source", "doc=https://example.com/new",
                                       "--non-interactive", *mode, cwd=self.repo, check=False,
                                       scripts=str(installed / "skills/describe/scripts"))
            self.assertNotEqual(result.returncode, 0)
            # 默认模式允许 probe,仅长期文件和 reviews 子树必须不变。
            if mode:
                self.assertEqual(tree(self.home), before)
            else:
                self.assertEqual(tree(Path(self.home, "reviews")), before_reviews)
                before = tree(self.home)


class MiningNoStore(unittest.TestCase):
    def setUp(self):
        self.f = mining.MiningCollection()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_temporary_cache_read_refresh_and_failure_do_not_write_records(self):
        f = self.f
        f.seed()
        _, first = f.mine()
        cache = Path(f.kv(first / "meta.txt")["cache"], "1.json")
        before = tree(f.home)
        f.data.pop(f.discussions())
        reused, work = f.mine("--no-store")
        self.assertIn("用缓存 1", reused.stdout)
        self.assertEqual(f.kv(work / "meta.txt")["storage_mode"], "temporary")
        self.assertTrue(inside(work, f.env["TMPDIR"]))
        f.data[f.listing()][0]["updated_at"] = mining.ago(0)
        failed, failed_work = f.mine("--no-store", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue((failed_work / "snapshot.json").exists())
        f.data[f.discussions()] = [{"id": "new", "notes": [f.note(12, body="新评论")]}]
        fetched, work = f.mine("--no-store")
        self.assertIn("新拉取 1", fetched.stdout)
        self.assertEqual(f.load(work / "snapshot.json")["records"][0]["notes"][0]["body"], "新评论")
        self.assertEqual(tree(f.home), before)
        self.assertTrue(cache.exists())

    def test_first_temporary_mining_does_not_create_cache_directory(self):
        f = self.f
        f.seed()
        before = tree(f.home)
        _, work = f.mine("--no-store")
        self.assertTrue((work / "batch-1.json").exists())
        self.assertEqual(tree(f.home), before)


class HandshakeNoStore(unittest.TestCase):
    def setUp(self):
        self.f = code_host.CodeHost()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_platform_handshake_retains_mode_without_repeating_flag(self):
        f = self.f
        before = tree(f.home)
        first = f.collect(code_host.LINK, "--no-store")
        stage = first.stdout.split("NEED_CODE_HOST: ", 1)[1].splitlines()[0].strip()
        stage_meta = f.kv(Path(stage, "meta.txt"))
        self.assertEqual(stage_meta["storage_mode"], "temporary")
        Path(stage, "code-host.json").write_text(json.dumps(f.record()))
        retry = f.collect(code_host.LINK, "--code-host-dir", stage)
        rd = f.review_dir(retry.stdout)
        self.assertEqual(rd, stage_meta["review_dir"])
        self.assertEqual(f.kv(Path(rd, "meta.txt"))["storage_mode"], "temporary")
        self.assertEqual(tree(f.home), before)
