"""记录有效期、目标隔离和发现编号的边界。"""
import datetime
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import fixture
sys.path.insert(0, fixture.SCRIPTS)
import sources
import incremental


class RecordBoundaries(fixture.Base):
    def test_material_expires_after_ninety_days(self):
        path = Path(self.tmp, "sources.json")
        old = (datetime.datetime.now() - datetime.timedelta(days=91)).strftime("%Y-%m-%d %H:%M")
        fresh = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        path.write_text(json.dumps({"intent": {"status": "given", "at": old, "text": "old"},
                                    "local_docs": {"status": "given", "at": fresh, "files": ["plan.md"]}}))
        data = sources.load_store(str(path))
        self.assertNotIn("intent", data)
        self.assertIn("local_docs", data)

    def test_clearing_another_target_does_not_remove_material(self):
        rd = Path(self.tmp, "rd")
        rd.mkdir()
        meta = {"store_dir": str(Path(self.tmp, "project/pr-2")), "source_branch": "patch-1", "target_key": "pr-2"}
        Path(rd, "meta.txt").write_text("".join("%s=%s\n" % pair for pair in meta.items()))
        path = sources.store_path(meta)
        with patch.dict(os.environ, self.env, clear=True):
            sources.save_store(path, {"intent": sources.saved(text="first", target="pr-1"),
                                      "local_docs": sources.saved(files=["first.md"], target="pr-1")})
            for key in ("intent", "local_docs"):
                sources.main(["set", str(rd), key, ""])
            self.assertEqual(sources.load_store(path)["intent"]["text"], "first")
            self.assertEqual(sources.load_store(path)["local_docs"]["files"], ["first.md"])

    def test_completeness_requires_positive_result(self):
        rd = Path(self.tmp, "rd")
        rd.mkdir()
        for text, expected in (("", False), ("unexpected", False), ("OK\n", True),
                               ("OK\nINCOMPLETE: missing\n", False)):
            Path(rd, "completeness.txt").write_text(text)
            self.assertEqual(incremental.complete(str(rd)), expected)


class FindingIds(fixture.ProjectFixture):
    def test_two_reviews_in_same_minute_have_distinct_ids(self):
        ids = []
        for stamp in ("20261030123001", "20261030123002"):
            rd, _ = self.collect("feature")
            with open(Path(rd, "meta.txt"), "a") as f:
                f.write("started_id=%s\n" % stamp)
            p = "src/orders/paginate.ts"
            self.run_pipeline(rd, [{"file": p, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"}],
                              {"A": (90, [{"file": p, "lines": "2"}])})
            ids.append(self.load(Path(rd, "findings.json"))[0]["id"])
        self.assertEqual(ids, ["20261030123001-1", "20261030123002-1"])
