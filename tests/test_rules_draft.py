"""统一规则草稿的边界回归。只用隔离 HOME/TMPDIR 和临时 Git 仓库。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import fixture

sys.path.insert(0, fixture.SCRIPTS)
import rules_draft as draft

USER = {"type": "user", "request": "只维护公共查询的规则和审查配置"}
RULE = '---\npaths: "src/**"\nglobs: "src/**"\n---\n# 查询\n\n## 上限\n\n- **规则**:必须校验查询上限\n- **不要报**:离线导出除外\n'


class RulesDraft(fixture.Base):
    def setUp(self):
        super().setUp()
        self.root = self.make_repo("repo", {"REVIEW.md": "# 审查\n\n## 审查重点\n\n- 用户原有内容\n",
                                           ".agents/rules/old.md": RULE, "src/main.ts": "export {};\n"})
        self.work = Path(self.tmp, "work")
        self.work.mkdir()
        (self.work / "meta.txt").write_text("repo_root=%s\nstorage_mode=temporary\n" % self.root)
        self.request = dict(goal="维护项目规则", changes=[], pending=["是否同时接入索引"], notes=["范围仍限于 src/**"])

    def cli(self, *args, ok=True):
        return self.run_py("rules-draft.py", str(self.work), *args, check=ok)

    def capture(self, *paths):
        args = ["--capture"]
        for rel in paths:
            args += ["--path", rel]
        return self.cli(*args)

    def change(self, path, text, source=None):
        return dict(path=path, operation="delete" if text is None else "write", content=text,
                    reason="按明确目标整理,保留未涉及内容", source=source or USER)

    def propose(self, *changes, ok=True, **extra):
        self.request.update(changes=list(changes), **extra)
        path = self.work / "proposal.json"
        path.write_text(json.dumps(self.request, ensure_ascii=False))
        return self.cli("--propose", str(path), ok=ok)

    def preview(self):
        out = self.cli("--check").stdout
        return out, out.split("DIGEST: ")[-1].strip()

    def apply(self, expected, ok=True):
        return self.cli("--apply", "--expected-digest", expected, ok=ok)

    def test_user_request_needs_no_history_and_temporary_work_can_apply(self):
        rel = ".agents/rules/api/query.md"
        self.capture(rel, "REVIEW.md")
        original = Path(self.root, "REVIEW.md").read_text()
        self.propose(self.change(rel, RULE), self.change("REVIEW.md", original + "\n## 不要报\n\n- 离线导出不要求分页\n"))
        before = {p: p.read_bytes() for p in self.work.iterdir() if p.is_file()}
        out, version = self.preview()
        self.assertIn("PENDING: 是否同时接入索引", out)
        self.assertFalse(Path(self.root, rel).exists())
        self.assertEqual(original, Path(self.root, "REVIEW.md").read_text())
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse((self.work / "candidates.json").exists())
        self.apply(version)
        self.assertEqual(RULE, Path(self.root, rel).read_text())
        self.assertIn("用户原有内容", Path(self.root, "REVIEW.md").read_text())
        self.assertIn("用户要求", (self.work / draft.VIEW).read_text())

    def test_modify_merge_delete_and_unselected_file_is_untouched(self):
        untouched = Path(self.root, ".agents/rules/untouched.md")
        untouched.write_text("保留\n")
        self.capture(".agents/rules/old.md", ".agents/rules/merged.md", "REVIEW.md")
        self.propose(self.change(".agents/rules/old.md", None), self.change(".agents/rules/merged.md", RULE + "\n合并后的例外\n"))
        out, version = self.preview()
        self.assertIn("DELETE: .agents/rules/old.md", out)
        self.assertIn("+++ /dev/null", out)
        self.apply(version)
        self.assertFalse(Path(self.root, ".agents/rules/old.md").exists())
        self.assertEqual("保留\n", untouched.read_text())
        self.assertIn("用户原有内容", Path(self.root, "REVIEW.md").read_text())

    def test_subdirectory_review_can_be_created_modified_deleted(self):
        self.capture("src/REVIEW.md")
        self.propose(self.change("src/REVIEW.md", "# 审查\n\n## 不要报\n\n- 明确场景\n"))
        self.apply(self.preview()[1])
        # 新一轮维护用新基线,不能把上一轮确认当成这一轮授权。
        second = self.work.parent / "second"
        second.mkdir()
        (second / "meta.txt").write_text("repo_root=%s\n" % self.root)
        self.work = second
        self.capture("src/REVIEW.md")
        self.propose(self.change("src/REVIEW.md", None))
        self.apply(self.preview()[1])
        self.assertFalse(Path(self.root, "src/REVIEW.md").exists())

    def test_missing_wrong_digest_and_changed_proposal_reject(self):
        self.capture("REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 第一次\n"))
        _, old = self.preview()
        self.assertIn("缺少 --expected-digest", self.cli("--apply", ok=False).stdout)
        self.assertNotEqual(0, self.apply("wrong", ok=False).returncode)
        self.propose(self.change("REVIEW.md", "# 用户补充后\n"))
        self.assertIn("草稿版本/内容已变化", self.apply(old, ok=False).stdout)
        self.assertIn("用户原有内容", Path(self.root, "REVIEW.md").read_text())
        self.apply(self.preview()[1])

    def test_view_edit_or_machine_edit_invalidates_preview(self):
        self.capture("REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 新\n"))
        _, old = self.preview()
        p = self.work / draft.VIEW
        p.write_text(p.read_text() + "用户另有补充\n")
        self.assertIn("不一致", self.apply(old, ok=False).stdout)
        self.propose(self.change("REVIEW.md", "# 新\n"))
        _, old = self.preview()
        machine = json.loads((self.work / draft.STATE).read_text())
        machine["changes"][0]["content"] += "未预览内容\n"
        (self.work / draft.STATE).write_text(json.dumps(machine))
        self.assertIn("版本/内容已变化", self.apply(old, ok=False).stdout)

    def test_existing_and_absent_baseline_concurrency_refuse_entire_batch(self):
        self.capture("REVIEW.md", ".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE), self.change("REVIEW.md", "# 改\n"))
        _, old = self.preview()
        Path(self.root, "REVIEW.md").write_text("用户并发修改\n")
        result = self.apply(old, ok=False)
        self.assertIn("目标在起草后变化", result.stdout)
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())
        recapture = self.cli("--capture", "--path", "REVIEW.md", ok=False)
        self.assertIn("目标在起草后变化", recapture.stdout)
        self.assertEqual("用户并发修改\n", Path(self.root, "REVIEW.md").read_text())

    def test_creation_binds_absence(self):
        self.capture(".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE))
        _, old = self.preview()
        Path(self.root, ".agents/rules/new.md").write_text("别人已创建\n")
        self.assertIn("目标在起草后变化", self.apply(old, ok=False).stdout)
        self.assertEqual("别人已创建\n", Path(self.root, ".agents/rules/new.md").read_text())

    def test_capture_adds_paths_and_invalidates_old_digest(self):
        self.capture("REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 改\n"))
        _, old = self.preview()
        self.capture(".agents/rules/old.md")
        self.assertIn("版本/内容已变化", self.apply(old, ok=False).stdout)

    def test_all_targets_validated_before_apply(self):
        self.capture("REVIEW.md", ".agents/rules/new.md")
        self.propose(self.change("REVIEW.md", "# 改\n"), self.change(".agents/rules/new.md", RULE))
        data = json.loads((self.work / draft.STATE).read_text())
        data["changes"][-1]["path"] = ".git/REVIEW.md"
        (self.work / draft.STATE).write_text(json.dumps(data))
        (self.work / draft.VIEW).write_text("irrelevant")
        result = self.apply(draft.digest(data), ok=False)
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("WRITE:", result.stdout)
        self.assertIn("用户原有内容", Path(self.root, "REVIEW.md").read_text())

    def test_agents_index_is_generated_and_cannot_overwrite_body(self):
        original = "# 用户说明\n\n## 测试\n\n运行用户自己的命令\n"
        Path(self.root, "AGENTS.md").write_text(original)
        self.capture("AGENTS.md", ".agents/rules/new.md")
        rejected = self.propose(self.change("AGENTS.md", "全部替换\n"), ok=False)
        self.assertIn("不接受正文提案", rejected.stdout)
        self.propose(self.change(".agents/rules/new.md", RULE), agents_links=[".agents/rules/new.md"], agents_source=USER)
        out, version = self.preview()
        self.assertIn("WRITE: AGENTS.md", out)
        self.apply(version)
        text = Path(self.root, "AGENTS.md").read_text()
        self.assertTrue(text.startswith(original))
        self.assertIn("改 src/** 时先读这个文件并遵守", text)

    def test_agents_tampered_machine_content_is_rejected(self):
        self.capture("AGENTS.md", ".agents/rules/old.md")
        self.propose(agents_links=[".agents/rules/old.md"], agents_source=USER)
        data = json.loads((self.work / draft.STATE).read_text())
        data["changes"][-1]["content"] = "任意正文\n"
        (self.work / draft.STATE).write_text(json.dumps(data))
        (self.work / draft.VIEW).write_text(draft.render(data))
        self.assertIn("超出受限索引变化", self.apply(draft.digest(data), ok=False).stdout)
        self.assertFalse(Path(self.root, "AGENTS.md").exists())

    def test_path_and_symlink_escapes(self):
        outside = Path(self.tmp, "outside")
        outside.mkdir()
        Path(self.root, "alias").symlink_to(outside, target_is_directory=True)
        for rel in ("../REVIEW.md", ".git/REVIEW.md", "src/main.ts", "alias/REVIEW.md"):
            with self.subTest(rel=rel):
                self.assertNotEqual(0, self.cli("--capture", "--path", rel, ok=False).returncode)
        self.assertFalse((outside / "REVIEW.md").exists())

    def test_symlink_swapped_after_preview_is_rejected(self):
        self.capture("src/REVIEW.md")
        self.propose(self.change("src/REVIEW.md", "# 新\n"))
        _, version = self.preview()
        outside = Path(self.tmp, "outside.md")
        outside.write_text("外部\n")
        Path(self.root, "src/REVIEW.md").symlink_to(outside)
        self.assertIn("符号链接", self.apply(version, ok=False).stdout)
        self.assertEqual("外部\n", outside.read_text())

    def test_settings_and_rule_scope_are_validated(self):
        self.capture("REVIEW.md", ".agents/rules/new.md")
        for header in ("unknown: x", "depth: slow", "depth: [quick]", "limits: { minor: -1 }", "limits: nope", "skip: { x: y }"):
            with self.subTest(header=header):
                result = self.propose(self.change("REVIEW.md", "---\n" + header + "\n---\n# 审查\n"), ok=False)
                self.assertNotEqual(0, result.returncode, result.stdout)
        result = self.propose(self.change(".agents/rules/new.md", RULE.replace('globs: "src/**"', 'globs: "other/**"')), ok=False)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.propose(self.change("REVIEW.md", "---\nskip: [src/generated/**]\nlimits: {minor: 3}\ndepth: standard\n---\n# 审查\n"))
        self.apply(self.preview()[1])

    def test_partial_failure_reports_completed_and_remaining(self):
        self.capture("REVIEW.md", ".agents/rules/new.md")
        self.propose(self.change("REVIEW.md", "# 改\n"), self.change(".agents/rules/new.md", RULE))
        version = self.preview()[1]
        original = os.replace
        def fail_second(src, dst):
            if str(dst).endswith("new.md"):
                raise OSError("模拟写入失败")
            return original(src, dst)
        with patch.dict(os.environ, self.env), patch.object(draft.os, "replace", side_effect=fail_second), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "已完成:REVIEW.md;未完成:.agents/rules/new.md"):
                draft.apply(str(self.work), version)
        self.assertEqual("# 改\n", Path(self.root, "REVIEW.md").read_text())
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())

    def test_inferred_remains_inferred_and_materials_are_bound_at_apply(self):
        self.capture("REVIEW.md")
        inferred = dict(type="inferred", basis="既有规则冲突,提出待负责人决定的合并")
        self.propose(self.change("REVIEW.md", "# 建议\n", inferred))
        with patch.dict(os.environ, self.env):
            data = draft.load(str(self.work))
            (self.work / "candidates.json").write_text("[]\n")
            data["bindings"] = {"candidates.json": draft.content_hash("[]\n")}
            draft.save(str(self.work), data)
        _, version = self.preview()
        (self.work / "candidates.json").write_text("[{}]\n")
        self.assertIn("来源材料已变化", self.apply(version, ok=False).stdout)
        self.assertIn("不因确认而变成历史共识", (self.work / draft.VIEW).read_text())

    def test_agents_managed_scope_update_and_merge_removes_only_selected_link(self):
        old_link = "- [old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守\n"
        unrelated = "- [other](.agents/rules/other.md):改 other/** 时先读这个文件并遵守\n"
        body = "# 用户说明\n\n## 代码约束\n\n手写说明必须保留\n" + old_link + unrelated + "\n## 测试\n\n用户命令\n"
        Path(self.root, "AGENTS.md").write_text(body)
        self.capture("AGENTS.md", ".agents/rules/old.md")
        narrower = RULE.replace("src/**", "src/api/**")
        self.propose(self.change(".agents/rules/old.md", narrower), agents_links=[".agents/rules/old.md"], agents_source=USER)
        _, version = self.preview()
        data = draft.load(str(self.work))
        proposed = next(c["content"] for c in data["changes"] if c["path"] == "AGENTS.md")
        self.assertEqual(body.replace("改 src/**", "改 src/api/**"), proposed)
        self.apply(version)
        # 第二轮把旧规则合并到新文件,明确同步两端索引。
        self.work = Path(self.tmp, "merge-work")
        self.work.mkdir()
        (self.work / "meta.txt").write_text("repo_root=%s\n" % self.root)
        self.capture("AGENTS.md", ".agents/rules/old.md", ".agents/rules/merged.md")
        self.propose(self.change(".agents/rules/old.md", None), self.change(".agents/rules/merged.md", narrower),
                     agents_links=[".agents/rules/old.md", ".agents/rules/merged.md"], agents_source=USER)
        self.apply(self.preview()[1])
        actual = Path(self.root, "AGENTS.md").read_text()
        self.assertNotIn(".agents/rules/old.md", actual)
        self.assertIn(".agents/rules/merged.md", actual)
        self.assertIn(unrelated, actual)
        self.assertIn("手写说明必须保留", actual)
        self.assertIn("## 测试\n\n用户命令", actual)

    def test_agents_unmanaged_reference_is_preserved_and_pending(self):
        original = "# 说明\n\n## 代码约束\n\n- [自己的描述](.agents/rules/old.md) 后面还有手写要求\n"
        Path(self.root, "AGENTS.md").write_text(original)
        self.capture("AGENTS.md", ".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", None), agents_links=[".agents/rules/old.md"], agents_source=USER)
        out, version = self.preview()
        self.assertIn("不能安全同步的手写或重复引用", out)
        self.apply(version)
        self.assertEqual(original, Path(self.root, "AGENTS.md").read_text())

    def test_success_receipt_distinguishes_applied_from_pending(self):
        self.capture("REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 已维护\n"))
        version = self.preview()[1]
        self.apply(version)
        self.assertIn("已应用到工作区", (self.work / draft.VIEW).read_text())
        self.assertIn("APPLIED:", self.cli("--check").stdout)
        self.assertIn("APPLIED:", self.apply(version).stdout)
        Path(self.root, "REVIEW.md").write_text("后来用户又改了\n")
        self.assertIn("后来又变化", self.apply(version, ok=False).stdout)

    def test_host_hookup_suggestions_are_only_printed_for_live_rules(self):
        Path(self.root, ".claude").mkdir()
        Path(self.root, ".cursor/rules").mkdir(parents=True)
        Path(self.root, ".cursor/rules/old.mdc").symlink_to("../../.agents/rules/old.md")
        self.capture(".agents/rules/old.md", ".agents/rules/new.md")
        self.propose(self.change(".agents/rules/old.md", None), self.change(".agents/rules/new.md", RULE))
        preview, version = self.preview()
        self.assertNotIn("SUGGEST:", preview)
        out = self.apply(version).stdout
        self.assertIn("SUGGEST: mkdir -p .claude/rules", out)
        self.assertIn(".cursor/rules/new.mdc", out)
        self.assertNotIn("SUGGEST: mkdir -p .claude/rules && ln -s ../../.agents/rules/old.md", out)
        self.assertFalse(Path(self.root, ".claude/rules").exists())
        self.assertFalse(Path(self.root, ".cursor/rules/new.mdc").exists())

    def test_host_hookup_preserves_nested_rule_paths(self):
        Path(self.root, ".claude").mkdir()
        self.capture(".agents/rules/a/foo.md", ".agents/rules/b/foo.md")
        self.propose(self.change(".agents/rules/a/foo.md", RULE), self.change(".agents/rules/b/foo.md", RULE))
        out = self.apply(self.preview()[1]).stdout
        self.assertIn("mkdir -p .claude/rules/a && ln -s ../../../.agents/rules/a/foo.md .claude/rules/a/foo.md", out)
        self.assertIn("mkdir -p .claude/rules/b && ln -s ../../../.agents/rules/b/foo.md .claude/rules/b/foo.md", out)
        self.assertFalse(Path(self.root, ".claude/rules").exists())

    def test_agents_frontmatter_comment_is_not_an_index_section(self):
        original = "---\n## 代码约束\nname: example\n---\n# 用户说明\n\n## 测试\n\n命令\n"
        Path(self.root, "AGENTS.md").write_text(original)
        self.capture("AGENTS.md", ".agents/rules/old.md")
        self.propose(agents_links=[".agents/rules/old.md"], agents_source=USER)
        self.apply(self.preview()[1])
        actual = Path(self.root, "AGENTS.md").read_text()
        self.assertTrue(actual.startswith(original))
        self.assertEqual(2, actual.count("## 代码约束"))

    def next_round(self, name):
        self.work = Path(self.tmp, name)
        self.work.mkdir()
        (self.work / "meta.txt").write_text("repo_root=%s\n" % self.root)
        self.request = dict(goal="维护原生指令入口", changes=[], pending=[], notes=[])

    def test_joint_instruction_files_preserve_body_and_are_idempotent(self):
        agents = "# AGENTS 用户正文\n\n## 测试\n\n用户自己的命令\n"
        claude = "# Claude 用户正文\n\n## 代码约束\n\n手写说明必须保留\n\n## 测试\n\n用户命令\n"
        Path(self.root, "AGENTS.md").write_text(agents)
        Path(self.root, "CLAUDE.md").write_text(claude)
        self.capture("AGENTS.md", "CLAUDE.md", ".agents/rules/old.md")
        self.propose(instruction_files=["AGENTS.md", "CLAUDE.md"],
                     agents_links=[".agents/rules/old.md"], agents_source=USER)
        before = {p: Path(self.root, p).read_bytes() for p in ("AGENTS.md", "CLAUDE.md")}
        out, version = self.preview()
        self.assertIn("WRITE: AGENTS.md", out)
        self.assertIn("WRITE: CLAUDE.md", out)
        self.assertEqual(before, {p: Path(self.root, p).read_bytes() for p in before})
        self.apply(version)
        actual = {p: Path(self.root, p).read_text() for p in before}
        self.assertTrue(actual["AGENTS.md"].startswith(agents))
        self.assertIn("手写说明必须保留", actual["CLAUDE.md"])
        self.assertIn("## 测试\n\n用户命令", actual["CLAUDE.md"])
        for text in actual.values():
            self.assertIn("[old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守", text)
        self.assertIn("APPLIED:", self.apply(version).stdout)
        self.next_round("idempotent")
        self.capture("AGENTS.md", "CLAUDE.md", ".agents/rules/old.md")
        self.propose(instruction_files=["AGENTS.md", "CLAUDE.md"],
                     agents_links=[".agents/rules/old.md"], agents_source=USER)
        self.assertEqual([], draft.load(str(self.work))["changes"])
        self.apply(self.preview()[1])
        self.assertEqual(actual, {p: Path(self.root, p).read_text() for p in actual})

    def test_joint_index_scope_merge_and_removal_sync_existing_references(self):
        files = ["AGENTS.md", "CLAUDE.md"]
        body = "# 手写正文\n\n## 代码约束\n\n- [old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守\n"
        for rel in files:
            Path(self.root, rel).write_text(body)
        self.capture(*files, ".agents/rules/old.md")
        narrowed = RULE.replace("src/**", "src/api/**")
        # 只选择入口,也会同步本轮变更规则的既有引用。
        self.propose(self.change(".agents/rules/old.md", narrowed), instruction_files=files, agents_source=USER)
        self.apply(self.preview()[1])
        for rel in files:
            self.assertIn("改 src/api/**", Path(self.root, rel).read_text())
        self.next_round("joint-merge")
        self.capture(*files, ".agents/rules/old.md", ".agents/rules/merged.md")
        self.propose(self.change(".agents/rules/old.md", None), self.change(".agents/rules/merged.md", narrowed),
                     instruction_files=files, agents_links=[".agents/rules/merged.md"], agents_source=USER)
        self.apply(self.preview()[1])
        for rel in files:
            actual = Path(self.root, rel).read_text()
            self.assertNotIn(".agents/rules/old.md", actual)
            self.assertIn(".agents/rules/merged.md", actual)
            self.assertTrue(actual.startswith("# 手写正文\n"))
        self.next_round("joint-delete")
        self.capture(*files, ".agents/rules/merged.md")
        self.propose(self.change(".agents/rules/merged.md", None), instruction_files=files, agents_source=USER)
        self.apply(self.preview()[1])
        for rel in files:
            self.assertNotIn(".agents/rules/", Path(self.root, rel).read_text())

    def test_existing_nested_claude_entry_has_relative_links_and_preserves_body(self):
        Path(self.root, ".claude").mkdir()
        original = "# 已有 Claude 入口\n\n手写命令\n"
        Path(self.root, ".claude/CLAUDE.md").write_text(original)
        self.capture("CLAUDE.md", ".claude/CLAUDE.md", ".agents/rules/old.md")
        result = self.propose(instruction_files=["CLAUDE.md"], agents_links=[".agents/rules/old.md"],
                              agents_source=USER, ok=False)
        self.assertIn("显式选择现有入口", result.stdout)
        self.propose(instruction_files=[".claude/CLAUDE.md"], agents_links=[".agents/rules/old.md"], agents_source=USER)
        self.apply(self.preview()[1])
        actual = Path(self.root, ".claude/CLAUDE.md").read_text()
        self.assertTrue(actual.startswith(original))
        self.assertIn("[old](../.agents/rules/old.md)", actual)
        self.assertFalse(Path(self.root, "CLAUDE.md").exists())
        self.next_round("nested-update")
        self.capture(".claude/CLAUDE.md", ".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", RULE.replace("src/**", "src/api/**")),
                     instruction_files=[".claude/CLAUDE.md"], agents_source=USER)
        self.apply(self.preview()[1])
        self.assertIn("改 src/api/**", Path(self.root, ".claude/CLAUDE.md").read_text())

    def test_existing_root_claude_respects_explicit_selection_when_both_exist(self):
        Path(self.root, ".claude").mkdir()
        Path(self.root, "CLAUDE.md").write_text("# 根入口\n")
        nested = Path(self.root, ".claude/CLAUDE.md")
        nested.write_text("# 既有嵌套入口\n")
        self.capture("CLAUDE.md", ".agents/rules/old.md")
        self.propose(instruction_files=["CLAUDE.md"], agents_links=[".agents/rules/old.md"], agents_source=USER)
        self.apply(self.preview()[1])
        self.assertIn(".agents/rules/old.md", Path(self.root, "CLAUDE.md").read_text())
        self.assertEqual("# 既有嵌套入口\n", nested.read_text())

    def test_claude_handwritten_or_duplicate_references_are_pending_and_untouched(self):
        body = "# 用户正文\n\n## 代码约束\n\n- [自己的规则](.agents/rules/old.md) 手写要求\n"
        Path(self.root, "CLAUDE.md").write_text(body)
        self.capture("CLAUDE.md", ".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", None), instruction_files=["CLAUDE.md"], agents_source=USER)
        out, version = self.preview()
        self.assertIn("CLAUDE.md 对 .agents/rules/old.md 有不能安全同步的手写或重复引用", out)
        self.apply(version)
        self.assertEqual(body, Path(self.root, "CLAUDE.md").read_text())

    def test_instruction_selection_changes_digest_and_concurrent_entry_blocks_batch(self):
        self.capture("AGENTS.md", "CLAUDE.md", ".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE), instruction_files=["AGENTS.md"],
                     agents_links=[".agents/rules/new.md"], agents_source=USER)
        old = self.preview()[1]
        self.propose(self.change(".agents/rules/new.md", RULE), instruction_files=["AGENTS.md", "CLAUDE.md"],
                     agents_links=[".agents/rules/new.md"], agents_source=USER)
        self.assertIn("版本/内容已变化", self.apply(old, ok=False).stdout)
        version = self.preview()[1]
        Path(self.root, "CLAUDE.md").write_text("用户并发创建\n")
        self.assertIn("目标在起草后变化", self.apply(version, ok=False).stdout)
        self.assertFalse(Path(self.root, "AGENTS.md").exists())
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())

    def test_new_nested_entry_after_preview_rejects_new_root_entry(self):
        self.capture("CLAUDE.md", ".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE), instruction_files=["CLAUDE.md"],
                     agents_links=[".agents/rules/new.md"], agents_source=USER)
        version = self.preview()[1]
        Path(self.root, ".claude").mkdir()
        Path(self.root, ".claude/CLAUDE.md").write_text("并发出现的既有入口\n")
        self.assertIn("显式选择现有入口", self.apply(version, ok=False).stdout)
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())
        self.assertFalse(Path(self.root, "CLAUDE.md").exists())

    def test_illegal_instruction_files_and_body_proposals_are_rejected(self):
        self.capture("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", ".agents/rules/old.md")
        for value in ("CLAUDE.md", None, ["../CLAUDE.md"], ["src/CLAUDE.md"], [".git/CLAUDE.md"],
                      [".claude/settings.json"], ["AGENTS.md", "AGENTS.md"], [123],
                      ["CLAUDE.md", ".claude/CLAUDE.md"], [".claude/CLAUDE.md"], []):
            with self.subTest(value=value):
                result = self.propose(instruction_files=value, agents_links=[".agents/rules/old.md"], agents_source=USER, ok=False)
                self.assertNotEqual(0, result.returncode, result.stdout)
        for rel in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"):
            with self.subTest(rel=rel):
                self.assertIn("不接受正文提案", self.propose(self.change(rel, "任意正文\n"),
                                                            instruction_files=[], ok=False).stdout)
        self.assertNotEqual(0, self.cli("--capture", "--path", "src/CLAUDE.md", ok=False).returncode)

    def test_claude_requires_captured_baseline_and_rejects_tampered_machine_body(self):
        self.capture(".agents/rules/old.md")
        result = self.propose(instruction_files=["CLAUDE.md"], agents_links=[".agents/rules/old.md"], agents_source=USER, ok=False)
        self.assertIn("--capture --path CLAUDE.md", result.stdout)
        self.capture("CLAUDE.md")
        self.propose(instruction_files=["CLAUDE.md"], agents_links=[".agents/rules/old.md"], agents_source=USER)
        data = draft.load(str(self.work))
        data["changes"][-1]["content"] = "任意正文\n"
        with patch.dict(os.environ, self.env):
            draft.save(str(self.work), data)
        self.assertIn("CLAUDE.md 超出受限索引变化", self.apply(draft.digest(data), ok=False).stdout)
        self.assertFalse(Path(self.root, "CLAUDE.md").exists())

    def test_history_material_bindings_are_explicit_and_changes_reject_apply(self):
        for name in ("dropped.json", "snapshot.json", "rules-sources.json"):
            with self.subTest(name=name):
                self.next_round("binding-" + name)
                self.capture("REVIEW.md")
                material = self.work / name
                material.write_text("[]\n")
                with patch.dict(os.environ, self.env):
                    draft.propose(str(self.work), dict(goal="维护规则", changes=[self.change("REVIEW.md", "# 建议\n")]),
                                  bindings={name: draft.content_hash("[]\n")})
                version = self.preview()[1]
                material.write_text("[{}]\n")
                self.assertIn("来源材料已变化", self.apply(version, ok=False).stdout)
                self.assertIn("用户原有内容", Path(self.root, "REVIEW.md").read_text())
        self.next_round("invalid-binding")
        self.capture("REVIEW.md")
        with patch.dict(os.environ, self.env), self.assertRaisesRegex(ValueError, "不支持的材料绑定"):
            draft.propose(str(self.work), dict(goal="维护规则", changes=[]), bindings={"../secret": "irrelevant"})

    def test_no_old_apply_entry(self):
        result = self.run_py("rules-write.py", str(self.work), "--apply", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--prepare", result.stderr)
