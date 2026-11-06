"""产物底部的 crosscheck 版本:review 报告、improve 规则草稿视图、describe 的本地记录。只用隔离 HOME/TMPDIR。"""
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import fixture

sys.path.insert(0, fixture.SCRIPTS)
import crlib  # noqa: E402
import rules_draft as draft  # noqa: E402

VERSION = json.loads(Path(fixture.PLUGIN, ".claude-plugin", "plugin.json").read_text(encoding="utf-8"))["version"]
LINE = "生成:crosscheck " + VERSION


class PluginVersion(fixture.Base):
    def test_reads_current_manifest(self):
        self.assertTrue(VERSION)
        self.assertEqual(crlib.plugin_version(), VERSION)
        r = self.run_py("crlib.py", "version")
        self.assertEqual(r.stdout, VERSION + "\n")

    def test_falls_back_in_order_and_empty_when_all_missing(self):
        root = Path(self.tmp, "plugin")
        root.mkdir()
        self.assertEqual(crlib.plugin_version(str(root)), "")
        (root / ".cursor-plugin").mkdir()
        (root / ".cursor-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text('{"version": ""}')   # 空版本跳过,不当作读到
        self.assertEqual(crlib.plugin_version(str(root)), "1.2.3")
        (root / ".codex-plugin").mkdir()
        (root / ".codex-plugin" / "plugin.json").write_text('{"version": "2.0.0"}')
        self.assertEqual(crlib.plugin_version(str(root)), "2.0.0")


class ReportVersion(fixture.ProjectFixture):
    def save(self, rd):
        Path(rd, "report.md").write_text("# 报告\n\n订单列表分页\n", encoding="utf-8")
        Path(rd, "findings.json").write_text("[]", encoding="utf-8")
        return self.save_again(rd)

    def save_again(self, rd):
        out = self.run_script("save-review.sh", rd).stdout
        report = next(l[len("report="):] for l in out.splitlines() if l.startswith("report="))
        return out, Path(report).read_text(encoding="utf-8")

    def test_report_ends_with_version_and_stdout_has_it(self):
        rd, _ = self.collect("feature")
        out, text = self.save(rd)
        self.assertEqual(text.rstrip("\n").splitlines()[-1], LINE)
        self.assertEqual(text.count(LINE), 1)
        self.assertIn("\nversion=%s\n" % VERSION, out)
        self.assertLess(text.index("消耗:"), text.index(LINE))
        # 再存一次是新的记录目录,报告重新拷贝,仍只有一行
        _, text = self.save_again(rd)
        self.assertEqual(text.count(LINE), 1)

    def test_temporary_resave_does_not_repeat_version(self):
        rd, _ = self.collect("feature", "--no-store")
        _, text = self.save(rd)
        self.assertEqual(Path(rd, "report.md").read_text(encoding="utf-8"), text)
        _, text = self.save_again(rd)
        self.assertEqual(text.count(LINE), 1)
        self.assertEqual(text.rstrip("\n").splitlines()[-1], LINE)

    def test_no_version_line_when_manifest_unreadable(self):
        rd, _ = self.collect("feature")
        # 拷贝安装读不到 plugin.json:换一份没有清单的脚本副本来跑
        copy = Path(self.tmp, "copied", "skills", "review", "scripts")
        shutil.copytree(fixture.SCRIPTS, copy)
        shutil.copytree(Path(fixture.PLUGIN, "scripts"), Path(self.tmp, "copied", "scripts"))
        Path(rd, "report.md").write_text("# 报告\n", encoding="utf-8")
        Path(rd, "findings.json").write_text("[]", encoding="utf-8")
        out = self.run_script(str(copy / "save-review.sh"), rd).stdout
        self.assertNotIn("version=", out)
        report = next(l[len("report="):] for l in out.splitlines() if l.startswith("report="))
        self.assertNotIn("生成:crosscheck", Path(report).read_text(encoding="utf-8"))


class DraftVersion(fixture.Base):
    def setUp(self):
        super().setUp()
        self.root = self.make_repo("repo", {"REVIEW.md": "# 审查\n\n## 审查重点\n\n- 分页上限\n", "src/main.ts": "export {};\n"})
        self.work = Path(self.tmp, "work")
        self.work.mkdir()
        (self.work / "meta.txt").write_text("repo_root=%s\nstorage_mode=temporary\n" % self.root)

    def cli(self, *args):
        return self.run_py("rules-draft.py", str(self.work), *args)

    def test_view_ends_with_version_and_digest_ignores_it(self):
        self.cli("--capture", "--path", "REVIEW.md")
        text = Path(self.root, "REVIEW.md").read_text() + "\n## 不要报\n\n- 导出不要求分页\n"
        proposal = self.work / "proposal.json"
        proposal.write_text(json.dumps(dict(goal="维护审查配置", pending=[], changes=[dict(
            path="REVIEW.md", operation="write", content=text, reason="补充导出例外",
            source={"type": "user", "request": "补充导出例外"})]), ensure_ascii=False))
        self.cli("--propose", str(proposal))
        view = (self.work / draft.VIEW).read_text(encoding="utf-8")
        self.assertTrue(view.endswith("\n---\n\n%s\n" % LINE))
        self.assertIn("DIGEST: ", self.cli("--check").stdout)   # 同一版本下视图与机器清单一致
        data = json.loads((self.work / draft.STATE).read_text(encoding="utf-8"))
        self.assertNotIn(VERSION, json.dumps(data, ensure_ascii=False))
        before = draft.digest(data)
        with patch.object(crlib, "plugin_version", return_value="9.9.9"):
            other = draft.render(data)
        self.assertEqual(draft.digest(data), before)
        self.assertTrue(other.endswith("\n---\n\n生成:crosscheck 9.9.9\n"))
        self.assertEqual(other.replace("9.9.9", VERSION), view)
        with patch.object(crlib, "plugin_version", return_value=""):
            self.assertNotIn("生成:crosscheck", draft.render(data))


class DescribeVersion(fixture.ShopRepo):
    def test_author_record_has_version_but_markdown_does_not(self):
        r = self.run_describe("collect.sh", "--base", "main", "--non-interactive", cwd=self.repo)
        rd = self.review_dir(r.stdout)
        self.run_describe("describe.sh", rd, "--tested", "本地跑了订单列表的单测")
        out = Path(self.home, "describe", "shop", "branch-feature")
        author = self.load(out / "author.json")
        self.assertEqual(author["crosscheck_version"], VERSION)
        self.assertEqual(author["tested"], "本地跑了订单列表的单测")
        md = (out / "describe.md").read_text(encoding="utf-8")
        self.assertNotIn("crosscheck", md)
        self.assertNotIn(VERSION, md)
        # 旧记录里的版本字段不影响读取;再写时换成当前版本
        (out / "author.json").write_text(json.dumps(dict(author, crosscheck_version="0.0.1"), ensure_ascii=False))
        self.run_describe("describe.sh", rd)
        self.assertEqual((out / "describe.md").read_text(encoding="utf-8"), md)
        self.run_describe("describe.sh", rd, "--tested", "本地跑了订单列表的单测")
        self.assertEqual(self.load(out / "author.json")["crosscheck_version"], VERSION)
