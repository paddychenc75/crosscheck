"""增量依赖搜索使用目标提交,搜索不完整时保守地重新审查。"""
import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import patch

import fixture

sys.path.insert(0, fixture.SCRIPTS)
spec = importlib.util.spec_from_file_location("incremental", os.path.join(fixture.SCRIPTS, "incremental.py"))
inc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inc)


class Dependencies(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("dep", {"definition.ts": "export function computeTotal() { return 1 }\n"})
        self.out = Path(self.tmp, "dependency-diff")
        (self.out / "split").mkdir(parents=True)
        (self.out / "split/index").write_text("1\tdefinition.ts\n")
        (self.out / "split/1.patch").write_text("+export function computeTotal() { return 2 }\n")
        self.status = [["changed", "definition.ts"], ["unchanged", "caller.ts"]]

    def test_reads_caller_from_target_commit(self):
        base = self.git(self.repo, "rev-parse", "HEAD")
        head = self.commit(self.repo, {"caller.ts": "computeTotal()\n"}, "caller")
        self.git(self.repo, "switch", "-q", "--detach", base)
        self.assertEqual(inc.dependents(str(self.out), self.repo, head, self.status), {"caller.ts"})

    def test_branch_collection_searches_reviewed_head(self):
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"definition.ts": "export function computeTotal() { return 2 }\n",
                                "caller.ts": "computeTotal()\n"}, "feature")
        self.git(self.repo, "switch", "-q", "main")
        first = self.review_dir(self.run_script("collect-diff.sh", "feature", cwd=self.repo).stdout)
        for name, text in (("report.md", "r\n"), ("findings.json", "[]"),
                           ("completeness.txt", "OK: 查找角色和核实结果都已落盘\n")):
            Path(first, name).write_text(text)
        self.run_script("save-review.sh", first)
        self.git(self.repo, "switch", "-q", "feature")
        self.commit(self.repo, {"definition.ts": "export function computeTotal() { return 3 }\n"}, "update")
        self.git(self.repo, "switch", "-q", "main")
        second = self.review_dir(self.run_script("collect-diff.sh", "feature", cwd=self.repo).stdout)
        self.assertIn("dependent\tcaller.ts", Path(second, "incremental.txt").read_text())

    def test_untracked_caller_is_searched(self):
        self.write(self.repo, {"caller.ts": "computeTotal()\n"})
        self.assertEqual(inc.dependents(str(self.out), self.repo, "", self.status), {"caller.ts"})

    def test_symbols_after_first_sixty_are_searched(self):
        (self.out / "split/1.patch").write_text("".join("+function symbol%03d() {}\n" % i for i in range(75)))
        self.write(self.repo, {"caller.ts": "symbol074()\n"})
        self.assertEqual(inc.dependents(str(self.out), self.repo, "", self.status), {"caller.ts"})

    def test_failed_search_rechecks_unchanged_files(self):
        self.assertEqual(inc.dependents(str(self.out), self.repo, "missing-ref", self.status), {"caller.ts"})

    def test_removed_definition_rechecks_unchanged_files(self):
        self.assertEqual(inc.dependents(str(self.out), self.repo, "", [["removed", "old.ts"], ["unchanged", "caller.ts"]]), {"caller.ts"})

    def test_unreadable_split_rechecks_unchanged_files(self):
        (self.out / "hashes.txt").write_text("new\tdefinition.ts\nsame\tcaller.ts\n")
        prev = Path(self.tmp, "previous")
        prev.mkdir()
        (prev / "hashes.txt").write_text("old\tdefinition.ts\nsame\tcaller.ts\n")
        with patch.object(inc, "dependents", side_effect=OSError("unreadable")):
            inc.main(str(self.out), str(prev), self.repo)
        self.assertIn("dependent\tcaller.ts", (self.out / "incremental.txt").read_text())
