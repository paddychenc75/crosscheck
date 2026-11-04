"""采集完整性回归：仅 mock CLI 与本地仓库，不读取平台凭证。"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import fixture


def ago(days):
    return (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class MiningCollection(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("shop", {"a.py": "one\ntwo\n"},
                                   remote="https://gitlab.example.com/eng/shop.git")
        self.sha = self.git(self.repo, "rev-parse", "HEAD")
        self.data = {}
        self.api_file = Path(self.tmp, "responses.json")
        self.calls = Path(self.tmp, "calls.jsonl")
        bindir = Path(self.tmp, "cli")
        bindir.mkdir()
        cli = '''#!%s
import json, os, subprocess, sys
with open(os.environ["MINING_CALLS"], "a") as f:
    f.write(json.dumps({"args": sys.argv[1:], "env": {k: os.environ.get(k) for k in ("GH_REPO", "GLAB_REPO", "GH_HOST", "GITLAB_HOST")}}) + "\\n")
data = json.load(open(os.environ["MINING_RESPONSES"]))
path = sys.argv[2]
if path not in data:
    print("synthetic secret token; not logged in", file=sys.stderr)
    sys.exit(1)
value = data[path]
if isinstance(value, dict) and "$origin" in value:
    subprocess.run(["git", "remote", "set-url", "origin", value["$origin"]], check=True)
    value = value["value"]
if isinstance(value, dict) and "$raw" in value:
    print(value["$raw"])
else:
    print(json.dumps(value))
''' % sys.executable
        for name in ("gh", "glab"):
            path = bindir / name
            path.write_text(cli)
            path.chmod(0o755)
        self.env.update(PATH=str(bindir) + ":" + self.env["PATH"], MINING_RESPONSES=str(self.api_file),
                        MINING_CALLS=str(self.calls), GH_REPO="wrong/target", GLAB_REPO="wrong/target",
                        GH_HOST="wrong.example", GITLAB_HOST="wrong.example")

    def listing(self, page=1, github=False):
        if github:
            return "repos/example/shop/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=%d" % page
        # updated_after 每次运行生成，用 run() 将占位符换成本次时间窗口。
        return "projects/eng%%2Fshop/merge_requests?state=merged&updated_after=SINCE&per_page=100&page=%d" % page

    def discussions(self, iid=1, page=1):
        return "projects/eng%%2Fshop/merge_requests/%d/discussions?per_page=100&page=%d" % (iid, page)

    def mr(self, iid=1, days=1, github=False):
        record = dict(title="采集", merged_at=ago(days), updated_at=ago(days))
        if github:
            record.update(number=iid, user={"id": 5, "login": "author"}, head={"sha": self.sha})
        else:
            record.update(iid=iid, author={"id": 5, "username": "author"}, sha=self.sha)
        return record

    def note(self, nid, who="reviewer", body="需要校验", **extra):
        record = dict(id=nid, body=body, author={"id": 7, "username": who}, created_at=ago(1))
        record.update(extra)
        return record

    def seed(self, notes=None):
        self.data[self.listing()] = [self.mr()]
        self.data[self.discussions()] = [{"id": "d1", "notes": notes or [self.note(11)]}]

    def mine(self, *args, check=True):
        # mock 按同一秒内的 since 值写多个映射，跨秒运行也不影响测试。
        data = {}
        for key, value in self.data.items():
            for offset in range(-5, 6):
                since = (datetime.datetime.utcnow() - datetime.timedelta(days=90) +
                         datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
                data[key.replace("SINCE", since)] = value
        self.api_file.write_text(json.dumps(data))
        result = self.run_script("mine-mrs.sh", *args, cwd=self.repo, check=check)
        work = Path(self.review_dir(result.stdout)) if result.stdout.strip() else None
        return result, work

    def batches(self, work):
        return [self.load(path) for path in sorted(work.glob("batch-*.json"))]

    def assert_incomplete(self, result, work, kind):
        self.assertNotEqual(result.returncode, 0)
        snapshot = self.load(work / "snapshot.json")
        self.assertEqual(snapshot["version"], 1)
        self.assertIs(snapshot["complete"], False)
        self.assertIn(kind, [e["kind"] for e in snapshot["errors"]])
        self.assertTrue(all(set(e) == {"resource", "kind"} for e in snapshot["errors"]))
        self.assertEqual(self.kv(work / "meta.txt")["complete"], "false")
        self.assertEqual(self.kv(work / "meta.txt")["batches"], "0")
        self.assertEqual(self.batches(work), [])
        self.assertNotIn("synthetic secret", result.stdout + result.stderr)
        self.assertNotIn("not logged in", result.stdout + result.stderr)

    def test_second_list_page_failure_is_not_success_or_login_guess(self):
        self.data[self.listing()] = [self.mr(i) for i in range(1, 101)]
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "api_failed")
        self.assertIn("page=2", self.load(work / "snapshot.json")["errors"][0]["resource"])
        self.assertEqual(self.load(work / "snapshot.json")["records"], [])

    def test_second_discussion_page_failure_does_not_cache_partial_notes(self):
        self.seed()
        self.data[self.discussions()] = [{"id": "d%d" % i, "notes": [self.note(i)]} for i in range(100)]
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "api_failed")
        self.assertFalse((Path(self.kv(work / "meta.txt")["cache"]) / "1.json").exists())

    def test_bad_json_and_shapes_are_incomplete(self):
        for response, kind in [({"$raw": "{bad token"}, "invalid_json"),
                               ({"message": "not a list"}, "invalid_shape"),
                               (["bad item"], "invalid_shape"), ([{}], "invalid_shape")]:
            with self.subTest(response=response):
                self.data = {self.listing(): response}
                result, work = self.mine(check=False)
                self.assert_incomplete(result, work, kind)
        self.seed()
        self.data[self.discussions()] = [{"id": "missing-notes"}]
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "invalid_shape")

    def test_nested_comment_shape_failures_are_recorded(self):
        malformed = [dict(body=[]), dict(author=[]), dict(position=[]),
                     dict(position={"new_line": "oops"}), dict(position={"new_path": []}),
                     dict(created_at=None)]
        for fields in malformed:
            with self.subTest(fields=fields):
                self.seed()
                self.data[self.discussions()][0]["notes"][0].update(fields)
                result, work = self.mine(check=False)
                self.assert_incomplete(result, work, "invalid_shape")

    def test_ninety_day_window_and_invalid_origin_still_record_run(self):
        self.seed()
        self.data[self.listing()].append(self.mr(2, days=91))
        self.data[self.listing()].append(self.mr(3, days=89))
        self.data[self.discussions(3)] = []
        _, work = self.mine()
        self.assertEqual({r["iid"] for r in self.load(work / "snapshot.json")["records"]}, {1, 3})
        self.assertEqual(self.kv(work / "meta.txt")["days"], "90")
        # 平台与 origin 不匹配，在任何 CLI 读取前失败，仍留下运行记录。
        calls = self.calls.read_bytes()
        result, work = self.mine("--platform", "github", check=False)
        self.assert_incomplete(result, work, "target_invalid")
        self.assertEqual(self.calls.read_bytes(), calls)

    def test_cli_missing_is_classified_without_credentials(self):
        self.seed()
        Path(self.tmp, "cli", "glab").unlink()
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "cli_missing")

    def test_malformed_complete_cache_is_refetched(self):
        self.seed()
        _, work = self.mine()
        cache = Path(self.kv(work / "meta.txt")["cache"]) / "1.json"
        cached = self.load(cache)
        del cached["notes"][0]["discussion"]
        cache.write_text(json.dumps(cached))
        result, work = self.mine()
        self.assertIn("新拉取 1", result.stdout)
        self.assertEqual(self.load(work / "snapshot.json")["records"][0]["notes"][0]["discussion"], "d1")

    def test_old_incomplete_wrong_version_and_foreign_cache_refetched(self):
        self.seed()
        _, work = self.mine()
        cache = Path(self.kv(work / "meta.txt")["cache"]) / "1.json"
        original = self.load(cache)
        for changes in ({"cache_version": None}, {"cache_version": 999}, {"complete": False},
                        {"repo_id": "other/repo"}, {"platform": "github"}):
            with self.subTest(changes=changes):
                cached = dict(original, **changes)
                cached["notes"][0]["body"] = "旧内容"
                cache.write_text(json.dumps(cached))
                result, work = self.mine()
                self.assertIn("新拉取 1", result.stdout)
                self.assertEqual(self.load(work / "snapshot.json")["records"][0]["notes"][0]["body"], "需要校验")
        self.data.pop(self.discussions())
        result, _ = self.mine()
        self.assertIn("用缓存 1", result.stdout)

    def test_fetch_failure_never_falls_back_to_old_cache(self):
        self.seed()
        _, first = self.mine()
        cache = Path(self.kv(first / "meta.txt")["cache"]) / "1.json"
        old = cache.read_bytes()
        self.data[self.listing()][0]["updated_at"] = ago(0)
        self.data.pop(self.discussions())
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "api_failed")
        self.assertEqual(self.load(work / "snapshot.json")["records"], [])
        self.assertEqual(cache.read_bytes(), old)
        self.assertIs(self.load(first / "snapshot.json")["complete"], True)

    def test_long_cross_file_thread_reply_identity_and_platform_bot_only(self):
        long_body = "长正文\n" * 15000
        notes = [self.note(1, "human-bot", long_body), self.note(2, "author", "作者解释"),
                 self.note(3, "robot", author_identity="ignored"),
                 self.note(4, "service", author={"username": "service", "bot": True}),
                 self.note(5, "system", system=True)]
        # 同线程跨文件，长线程超过预算仍不可拆分。
        for note, path in zip(notes, ("src/a.py", "lib/b.py")):
            note["position"] = {"new_path": path, "new_line": 1, "head_sha": self.sha}
        notes[2]["author"]["type"] = "Bot"
        self.seed(notes)
        self.data[self.discussions()].append({"id": "d2", "notes": [self.note(6)]})
        result, work = self.mine()
        snapshot = self.load(work / "snapshot.json")
        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["errors"], [])
        self.assertEqual([n["id"] for n in snapshot["records"][0]["notes"]], [1, 2, 6])
        batches = self.batches(work)
        self.assertEqual([[n["note"] for n in batch] for batch in batches], [[1, 2], [6]])
        self.assertEqual(batches[0][0]["body"], long_body)
        self.assertEqual({n["thread"] for n in batches[0]}, {"1:d1"})
        self.assertTrue(batches[0][1]["reply"])
        self.assertEqual(batches[0][1]["author"], "author")
        self.assertEqual(batches[0][0]["author_identity"]["id"], 7)
        self.assertEqual(batches[0][0]["created_at"], notes[0]["created_at"])
        cache = self.load(Path(self.kv(work / "meta.txt")["cache"]) / "1.json")
        self.assertEqual(cache["notes"], snapshot["records"][0]["notes"])
        self.assertNotIn("人工", result.stdout)

    def test_gitlab_discussions_beyond_twenty_pages(self):
        self.seed()
        for page in range(1, 22):
            self.data[self.discussions(page=page)] = [
                {"id": "d%d-%d" % (page, i), "notes": [self.note(page * 100 + i)]} for i in range(100)]
        self.data[self.discussions(page=22)] = []
        _, work = self.mine()
        self.assertEqual(len(self.load(work / "snapshot.json")["records"][0]["notes"]), 2100)

    def test_no_default_mr_limit_and_explicit_positive_limit(self):
        rows = [self.mr(i) for i in range(1, 202)]
        for page in range(1, 4):
            self.data[self.listing(page)] = rows[(page - 1) * 100:page * 100]
        for row in rows:
            self.data[self.discussions(row["iid"])] = []
        _, work = self.mine()
        self.assertEqual(len(self.load(work / "snapshot.json")["records"]), 201)
        _, work = self.mine("--limit", "2")
        self.assertEqual(len(self.load(work / "snapshot.json")["records"]), 2)
        for bad in ("0", "00", "-1", "x"):
            result, _ = self.mine("--limit", bad, check=False)
            self.assertNotEqual(result.returncode, 0)

    def test_github_pages_reply_threads_and_origin_binding(self):
        self.git(self.repo, "remote", "set-url", "origin", "https://github.com/example/shop.git")
        # PR 列表第 11 页仍可能在范围内，不能静默截断。
        for page in range(1, 12):
            self.data[self.listing(page, github=True)] = [self.mr(page * 100 + i, days=200, github=True) for i in range(100)]
        self.data[self.listing(12, github=True)] = [self.mr(github=True)]
        path = "repos/example/shop/pulls/1/comments?per_page=100&page=%d"
        def comment(nid, reply=None):
            return dict(id=nid, user={"id": 9, "login": "not-a-bot", "type": "User"}, body="完整正文" * 1000,
                        created_at=ago(1), in_reply_to_id=reply)
        self.data[path % 1] = [comment(10)] + [comment(100 + i, 10) for i in range(99)]
        self.data[path % 2] = [comment(999, 10)]
        self.data["repos/example/shop/issues/1/comments?per_page=100&page=1"] = [comment(2001), comment(2002)]
        _, work = self.mine()
        batches = self.batches(work)
        snapshot = self.load(work / "snapshot.json")
        expected_threads = {n["id"]: "%s:%s" % (r["iid"], n["discussion"])
                            for r in snapshot["records"] for n in r["notes"]}
        for batch in batches:
            for note in batch:
                self.assertEqual(note["thread"], expected_threads[note["note"]])
        thread = [n for batch in batches for n in batch if n["thread"] == "1:review:10"]
        self.assertEqual(len(thread), 101)
        self.assertEqual(sum(any(n["thread"] == "1:review:10" for n in batch) for batch in batches), 1)
        self.assertEqual(thread[-1]["note"], 999)
        self.assertEqual(thread[-1]["in_reply_to_id"], 10)
        self.assertEqual(thread[-1]["author_identity"]["type"], "User")
        self.assertEqual({n["thread"] for batch in batches for n in batch if n["note"] in (2001, 2002)}, {"1:issue"})
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        for call in calls:
            self.assertIn("--method", call["args"])
            self.assertEqual(call["args"][-3:], ["GET", "--hostname", "github.com"])
            self.assertTrue(call["args"][1].startswith("repos/example/shop/"))
            self.assertIsNone(call["env"]["GH_REPO"])
            self.assertIsNone(call["env"]["GLAB_REPO"])

    def test_snapshot_freezes_feedback_and_records_collection_head(self):
        pdir = Path(self.home, "reviews", "shop")
        pdir.mkdir(parents=True)
        (pdir / ".repo").write_text("gitlab.example.com/eng/shop\n")
        feedback = dict(key="f1", id="finding", verdict="invalid", reason="完整原因",
                        root_cause="根因", failure_scenario="场景", target="mr-1", author="reviewer")
        (pdir / "feedback.jsonl").write_text(json.dumps(feedback) + "\n")
        self.seed()
        _, work = self.mine()
        snapshot = self.load(work / "snapshot.json")
        self.assertEqual(snapshot["feedback"], self.load(work / "feedback.json"))
        self.assertEqual(snapshot["feedback"][0]["reason"], "完整原因")
        self.assertEqual(self.kv(work / "meta.txt")["head"], self.sha)
        before = (work / "snapshot.json").read_bytes()
        (pdir / "feedback.jsonl").write_text("")
        self.assertEqual((work / "snapshot.json").read_bytes(), before)

    def test_origin_change_during_final_response_is_incomplete(self):
        self.seed()
        self.data[self.discussions()] = {"$origin": "https://gitlab.example.com/other/repo.git",
                                         "value": [{"id": "d1", "notes": [self.note(11)]}]}
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "origin_changed")

    def test_gitlab_origin_bound_and_change_stops_next_page(self):
        self.seed()
        _, _ = self.mine()
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        for call in calls:
            self.assertEqual(call["args"][-3:], ["GET", "--hostname", "gitlab.example.com"])
            self.assertTrue(call["args"][1].startswith("projects/eng%2Fshop/"))
        self.calls.unlink()
        self.data[self.listing()] = {"$origin": "https://gitlab.example.com/other/repo.git",
                                    "value": [self.mr(i) for i in range(1, 101)]}
        result, work = self.mine(check=False)
        self.assert_incomplete(result, work, "origin_changed")
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
