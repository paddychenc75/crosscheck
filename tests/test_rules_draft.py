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
        self.request = dict(goal="维护项目规则", changes=[], pending=["是否同时调整范围"], notes=["范围仍限于 src/**"])

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
        self.assertIn("PENDING: 是否同时调整范围", out)
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

    def next_round(self, name):
        self.work = Path(self.tmp, name)
        self.work.mkdir()
        (self.work / "meta.txt").write_text("repo_root=%s\n" % self.root)
        self.request = dict(goal="维护原生指令入口", changes=[], pending=[], notes=[])

    HINT = draft.AGENTS_HINT

    def link_at(self, rel):
        path = Path(self.root, rel)
        return os.readlink(path) if path.is_symlink() else None

    def test_rule_write_adds_agents_hint_and_claude_link_after_confirmation(self):
        self.capture(".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE))
        out, version = self.preview()
        self.assertIn("WRITE: AGENTS.md +3行", out)
        self.assertIn("+## 代码约束\n+\n+" + self.HINT + "\n", out)
        self.assertIn("LINK: .claude/rules/new.md -> ../../.agents/rules/new.md", out)
        self.assertNotIn("SUGGEST:", out)
        view = (self.work / draft.VIEW).read_text()
        self.assertIn("## 新增链接 .claude/rules/new.md", view)
        self.assertIn("Claude Code 按 .claude/rules 的 paths 原生加载规则", view)
        self.assertFalse(Path(self.root, "AGENTS.md").exists())                 # 预演不写
        self.assertFalse(os.path.lexists(Path(self.root, ".claude")))
        out = self.apply(version).stdout
        self.assertIn("LINK: .claude/rules/new.md -> ../../.agents/rules/new.md", out)
        self.assertNotIn("SUGGEST:", out)
        self.assertEqual("## 代码约束\n\n" + self.HINT + "\n", Path(self.root, "AGENTS.md").read_text())
        self.assertEqual("../../.agents/rules/new.md", self.link_at(".claude/rules/new.md"))
        self.assertEqual(RULE, Path(self.root, ".claude/rules/new.md").read_text())   # 链接可读到规则
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md"):
            self.assertFalse(os.path.lexists(Path(self.root, rel)))
        self.assertIn("APPLIED:", self.apply(version).stdout)
        self.assertIn("APPLIED:", self.cli("--check").stdout)
        # 链接后来被换掉:重复 apply 不能当成已应用
        os.unlink(Path(self.root, ".claude/rules/new.md"))
        os.symlink("../../.agents/rules/old.md", Path(self.root, ".claude/rules/new.md"))
        self.assertIn("后来又变化", self.apply(version, ok=False).stdout)

    def test_nested_rule_links_use_relative_targets(self):
        self.capture(".agents/rules/a/foo.md", ".agents/rules/b/foo.md")
        self.propose(self.change(".agents/rules/a/foo.md", RULE), self.change(".agents/rules/b/foo.md", RULE))
        self.apply(self.preview()[1])
        for sub in ("a", "b"):
            self.assertEqual("../../../.agents/rules/%s/foo.md" % sub, self.link_at(".claude/rules/%s/foo.md" % sub))

    def test_agents_hint_goes_into_section_and_is_idempotent(self):
        body = "# 用户说明\n\n## 代码约束\n\n手写说明必须保留\n\n## 测试\n\n用户命令\n"
        Path(self.root, "AGENTS.md").write_text(body)
        self.capture(".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", RULE + "\n补充\n"))
        self.apply(self.preview()[1])
        expected = body.replace("手写说明必须保留\n", "手写说明必须保留\n\n" + self.HINT + "\n")
        self.assertEqual(expected, Path(self.root, "AGENTS.md").read_text())
        self.assertEqual("../../.agents/rules/old.md", self.link_at(".claude/rules/old.md"))
        self.next_round("idempotent")
        self.capture(".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", RULE + "\n再补充\n"))
        self.assertEqual([".agents/rules/old.md"], [c["path"] for c in draft.load(str(self.work))["changes"]])
        self.apply(self.preview()[1])
        self.assertEqual(expected, Path(self.root, "AGENTS.md").read_text())

    def test_review_only_change_adds_no_hint_or_link(self):
        self.capture("REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 审查\n"))
        self.assertEqual(["REVIEW.md"], [c["path"] for c in draft.load(str(self.work))["changes"]])

    def test_legacy_index_migrates_to_hint_and_keeps_handwritten_lines(self):
        body = ("# 用户说明\n\n## 代码约束\n\n改动对应路径前先打开链接的规则文件读完并遵守。\n\n"
                "- [old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守\n"
                "- [other](.agents/rules/other.md):改 other/** 时先读这个文件并遵守\n"
                "- [自己的描述](.agents/rules/old.md) 手写要求\n\n## 测试\n\n用户命令\n")
        Path(self.root, "AGENTS.md").write_text(body)
        self.capture(".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", RULE + "\n补充\n"))
        out, version = self.preview()
        self.assertIn("-- [old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守", out)
        self.apply(version)
        self.assertEqual("# 用户说明\n\n## 代码约束\n\n" + self.HINT + "\n\n- [自己的描述](.agents/rules/old.md) 手写要求\n\n## 测试\n\n用户命令\n",
                         Path(self.root, "AGENTS.md").read_text())
        # 0.3.12 新建的 AGENTS.md:旧索引在文件末尾,迁移后不留多余空行
        legacy = "## 代码约束\n\n改动对应路径前先打开链接的规则文件读完并遵守。\n\n- [old](.agents/rules/old.md):改 src/** 时先读这个文件并遵守\n"
        self.assertEqual("## 代码约束\n\n" + self.HINT + "\n", draft.hint_content(legacy))
        self.assertEqual(draft.hint_content(legacy), draft.hint_content(draft.hint_content(legacy)))   # 迁移后幂等

    def test_agents_hint_respects_crlf_frontmatter_and_fence(self):
        original = "---\n## 代码约束\nname: example\n---\r\n# 说明\r\n\r\n```\r\n" + self.HINT + "\r\n```\r\n"
        Path(self.root, "AGENTS.md").write_bytes(original.encode())
        self.capture(".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE))
        self.apply(self.preview()[1])
        actual = Path(self.root, "AGENTS.md").read_bytes().decode()
        self.assertEqual(original + "\r\n## 代码约束\r\n\r\n" + self.HINT + "\r\n", actual)   # 头部和代码块里的都不算

    def test_multiple_code_sections_refuse(self):
        Path(self.root, "AGENTS.md").write_text("## 代码约束\n\n甲\n\n## 代码约束\n\n乙\n")
        self.capture(".agents/rules/new.md")
        self.assertIn("有多个代码约束章节", self.propose(self.change(".agents/rules/new.md", RULE), ok=False).stdout)

    def test_existing_link_conflicts_and_symlinked_parent_are_left_alone(self):
        Path(self.root, ".claude/rules").mkdir(parents=True)
        Path(self.root, ".claude/rules/old.md").symlink_to("../../.agents/rules/old.md")
        Path(self.root, ".claude/rules/conflict.md").write_text("用户自己的规则\n")
        self.capture(".agents/rules/old.md", ".agents/rules/conflict.md")
        self.propose(self.change(".agents/rules/old.md", RULE + "\n补充\n"), self.change(".agents/rules/conflict.md", RULE))
        out, version = self.preview()
        self.assertNotIn("LINK:", out)
        self.assertIn("PENDING: .claude/rules/conflict.md 已存在且不是指向 .agents/rules/conflict.md 的链接", out)
        self.apply(version)
        self.assertEqual("用户自己的规则\n", Path(self.root, ".claude/rules/conflict.md").read_text())
        # 共享规则目录:上级是符号链接时不替用户改
        shared = Path(self.tmp, "shared")
        shared.mkdir()
        self.next_round("shared-round")
        os.unlink(Path(self.root, ".claude/rules/old.md"))
        os.unlink(Path(self.root, ".claude/rules/conflict.md"))
        Path(self.root, ".claude/rules").rmdir()
        Path(self.root, ".claude/rules").symlink_to(shared, target_is_directory=True)
        self.capture(".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE))
        out, version = self.preview()
        self.assertNotIn("LINK:", out)
        self.assertIn("PENDING: .claude/rules/new.md 的上级 .claude/rules 是符号链接", out)
        self.apply(version)
        self.assertEqual([], os.listdir(shared))

    def test_rule_targets_get_links_for_existing_rules(self):
        self.capture(".agents/rules/old.md", "REVIEW.md")
        self.propose(self.change("REVIEW.md", "# 审查\n"), rule_targets=[".agents/rules/old.md"])
        out, version = self.preview()
        self.assertIn("LINK: .claude/rules/old.md", out)
        self.assertNotIn("AGENTS.md", out)                                       # 只确认已有规则,不改 AGENTS.md
        self.apply(version)
        self.assertEqual("../../.agents/rules/old.md", self.link_at(".claude/rules/old.md"))

    def test_delete_rule_unlinks_and_lists_stale_references(self):
        Path(self.root, ".claude/rules").mkdir(parents=True)
        Path(self.root, ".claude/rules/old.md").symlink_to("../../.agents/rules/old.md")
        Path(self.root, ".claude/rules/kept.md").write_text("同名之外的普通文件\n")
        agents = "# 说明\n\n- [旧规则](.agents/rules/old.md) 手写要求\n"
        claude = "# Claude\n\n@.agents/rules/old.md\n"
        nested = "# 嵌套\n\n[旧](../.agents/rules/old.md)\n"
        for rel, text in (("AGENTS.md", agents), ("CLAUDE.md", claude), (".claude/CLAUDE.md", nested)):
            Path(self.root, rel).write_text(text)
        self.capture(".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", None))
        out, version = self.preview()
        self.assertIn("UNLINK: .claude/rules/old.md", out)
        self.assertNotIn("WRITE: AGENTS.md", out)
        for rel in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"):
            self.assertIn("PENDING: %s 仍引用已删除的规则 .agents/rules/old.md" % rel, out)
        self.assertIn("## 删除链接 .claude/rules/old.md", (self.work / draft.VIEW).read_text())
        self.apply(version)
        self.assertFalse(os.path.lexists(Path(self.root, ".claude/rules/old.md")))
        self.assertEqual("同名之外的普通文件\n", Path(self.root, ".claude/rules/kept.md").read_text())
        for rel, text in (("AGENTS.md", agents), ("CLAUDE.md", claude), (".claude/CLAUDE.md", nested)):
            self.assertEqual(text, Path(self.root, rel).read_text())
        self.assertIn("APPLIED:", self.apply(version).stdout)

    def test_delete_rule_with_foreign_link_is_pending(self):
        Path(self.root, ".claude/rules").mkdir(parents=True)
        Path(self.root, ".claude/rules/old.md").symlink_to("../../docs/old.md")
        self.capture(".agents/rules/old.md")
        self.propose(self.change(".agents/rules/old.md", None))
        out, version = self.preview()
        self.assertNotIn("UNLINK:", out)
        self.assertIn("PENDING: .claude/rules/old.md 仍存在且不是指向已删除规则", out)
        self.apply(version)
        self.assertEqual("../../docs/old.md", self.link_at(".claude/rules/old.md"))

    def tamper(self, edit):
        self.propose(self.change(".agents/rules/new.md", RULE))   # 每次从干净提案开始,篡改互不叠加
        data = draft.load(str(self.work))
        edit(data)
        with patch.dict(os.environ, self.env):
            draft.save(str(self.work), data)
        return self.apply(draft.digest(data), ok=False).stdout

    def test_tampered_link_hint_or_baseline_is_rejected(self):
        self.capture(".agents/rules/new.md")
        def find(data, op):
            return next(c for c in data["changes"] if c["operation"] == op)
        self.assertIn("链接只能指向对应的", self.tamper(lambda d: find(d, "link").update(link="../../../outside.md")))
        self.assertIn("超出受限提示变化", self.tamper(lambda d: find(d, "hint").update(content="任意正文\n")))
        self.assertIn("链接变更只写 link 字段", self.tamper(lambda d: find(d, "link").update(content="正文\n")))
        def add_claude(d):
            d["base"]["CLAUDE.md"] = dict(content=None, sha256=None)
            d["changes"].append(dict(path="CLAUDE.md", operation="hint", content=self.HINT + "\n", reason="x", source=USER))
        self.assertNotEqual("", self.tamper(add_claude))
        self.assertFalse(os.path.lexists(Path(self.root, ".claude")))
        self.assertFalse(Path(self.root, "AGENTS.md").exists())
        self.assertFalse(Path(self.root, "CLAUDE.md").exists())
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())

    def test_link_location_change_after_preview_blocks_batch(self):
        self.capture(".agents/rules/new.md")
        self.propose(self.change(".agents/rules/new.md", RULE))
        _, version = self.preview()
        Path(self.root, ".claude/rules").mkdir(parents=True)
        Path(self.root, ".claude/rules/new.md").write_text("用户并发创建\n")
        self.assertIn("目标在起草后变化", self.apply(version, ok=False).stdout)
        self.assertEqual("用户并发创建\n", Path(self.root, ".claude/rules/new.md").read_text())
        self.assertFalse(Path(self.root, ".agents/rules/new.md").exists())
        self.assertFalse(Path(self.root, "AGENTS.md").exists())

    def test_retired_fields_and_instruction_bodies_are_rejected(self):
        self.capture(".agents/rules/old.md")
        for key, value in (("agents_links", [".agents/rules/old.md"]), ("instruction_files", ["AGENTS.md"]), ("agents_source", USER)):
            with self.subTest(key=key):
                result = self.propose(ok=False, **{key: value})
                self.assertIn("0.3.13 起", result.stdout)
                self.assertIn(key, result.stdout)
                self.request.pop(key)
        for rel in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", ".claude/rules/old.md"):
            with self.subTest(rel=rel):
                self.assertIn("不接受正文提案", self.propose(self.change(rel, "任意正文\n"), ok=False).stdout)
        for rel in ("CLAUDE.md", ".claude/rules/old.md", "src/CLAUDE.md"):
            with self.subTest(rel=rel):
                self.assertNotEqual(0, self.cli("--capture", "--path", rel, ok=False).returncode)

    def test_old_draft_format_is_rejected(self):
        self.capture("REVIEW.md")
        data = json.loads((self.work / draft.STATE).read_text())
        data["version"] = 1
        (self.work / draft.STATE).write_text(json.dumps(data))
        self.assertIn("草稿格式不受支持", self.cli("--check", ok=False).stdout)

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
