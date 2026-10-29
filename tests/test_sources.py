"""需求来源(需求单、需求文档)的识别、记录、暂存文档,以及 GitHub 上下文的测试。"""
import json
import os
import sys
from pathlib import Path

import fixture

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = fixture.SCRIPTS
TEAM = """
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/story/42
tools.doc_reader = 用 acme-kit:doc-reader 读文档链接,支持 https://docs.acme.example.com/ 下的页面
intent.required = requirement, doc
"""


class Sources(fixture.ShopRepo):
    def settings_of(self, text):
        sys.path.insert(0, SCRIPTS)
        import sources
        cfg = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
        return sources.settings(cfg)["kinds"]

    def test_link_keywords_come_from_urls_in_the_prompt(self):
        kinds = self.settings_of("tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://Tickets.acme.example.com/browse/XX-123,"
                                 "也支持 http://pm.acme.example.com\n"
                                 "tools.doc_reader = 用 acme-kit:doc-reader 读文档\n")
        self.assertEqual(kinds["requirement"]["match"], ["tickets.acme.example.com", "pm.acme.example.com"])
        self.assertEqual(kinds["doc"]["match"], [])           # 提示词里没有网址:不自动识别
        self.assertEqual((kinds["requirement"]["label"], kinds["doc"]["label"]), ("需求单", "需求文档"))
        self.assertEqual(self.settings_of("")["requirement"]["tool"], "")

    def test_prompt_without_url_does_not_auto_detect_links(self):
        self.config("tools.requirement = 用 acme-kit:ticket-reader 读需求单\n")
        r, rd = self.collect()
        requirement = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "requirement")
        self.assertEqual(requirement["status"], "missing")
        self.assertIn("SOURCES_MISSING: 需求单", r.stdout)
        r, rd = self.collect("--source", "requirement=https://tickets.acme.example.com/story/42")
        self.assertNotIn("SOURCES_MISSING", r.stdout)

    def test_missing_required_sources_are_reported_once_per_branch(self):
        self.config(TEAM)
        r, rd = self.collect()
        self.assertIn("SOURCES_MISSING: 需求文档 还没有提供(doc)", r.stdout)
        src = self.load(os.path.join(rd, "sources.json"))
        requirement = next(x for x in src["items"] if x["kind"] == "requirement")
        self.assertEqual(requirement["source"], "自动识别")   # 提交说明里的需求单链接(按提示词里网址的域名识别)
        self.assertEqual(src["missing"], ["doc"])
        r, _ = self.collect("--source", "doc=skip")
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        r, rd = self.collect()                               # 记住了跳过,不再问
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        self.assertEqual(next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")["source"], "记录")

    def test_unasked_is_not_remembered_and_default_requires_ticket_only(self):
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供(requirement)", r.stdout)   # 没有配置时只要需求单
        self.commit(self.repo, {"b.ts": "b\n"}, "Closes #12")
        r, _ = self.collect()
        self.assertNotIn("SOURCES_MISSING", r.stdout)       # 提交说明里的需求编号也算
        self.config("intent.required = doc\n")
        r, _ = self.collect("--source", "doc=unasked")
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING", r.stdout)

    def test_staged_documents_join_intent_and_title(self):
        self.config(TEAM)
        stage = self.run_py("sources.py", "stage").stdout.strip()
        Path(stage, "doc.md").write_text("url: https://docs.acme.example.com/d/abc\ntitle: 订单游标分页 PRD\ntool: acme-kit:doc-reader\n---\n"
                                         "## 验收标准\n- 接口支持 cursor 参数\n", encoding="utf-8")
        Path(stage, "requirement.md").write_text("url: https://tickets.acme.example.com/story/42\nerror: 没有权限\n---\n", encoding="utf-8")
        r, rd = self.collect("--source", "doc=https://docs.acme.example.com/d/abc", "--source-dir", stage)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## 需求文档:订单游标分页 PRD", intent)
        self.assertIn("接口支持 cursor 参数(来自 需求文档)", intent)
        self.assertIn("需求单 https://tickets.acme.example.com/story/42 没有读到(没有权限)", r.stdout)
        doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
        self.assertEqual(doc["title"], "订单游标分页 PRD")

    def test_local_documents_count_as_the_doc_source(self):
        self.write(self.repo, {"plan.md": "# 方案\n", "api.md": "# 接口\n"})
        _, rd = self.collect("--doc", "plan.md", "--doc", "api.md")
        doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
        self.assertEqual(doc["local_files"], ["plan.md", "api.md"])

    def test_bad_source_argument(self):
        r, _ = self.collect("--source", "doc", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("KIND=VALUE", r.stderr)

    def test_set_only_accepts_intent_and_source_kinds(self):
        _, rd = self.collect("--source", "requirement=skip")
        self.run_py("sources.py", "set", rd, "intent", "订单列表改为游标分页")
        self.run_py("sources.py", "set", rd, "doc", "skip")
        for key in ("tested", "focus", "page_url"):
            with self.subTest(key=key):
                r = self.run_py("sources.py", "set", rd, key, "x", check=False)
                self.assertNotEqual(r.returncode, 0)

    def test_tested_is_no_longer_an_option(self):
        r, _ = self.collect("--tested", "跑了单测", check=False)
        self.assertNotEqual(r.returncode, 0)


class NonInteractive(fixture.ShopRepo):
    def test_missing_sources_become_unasked(self):
        self.config(TEAM)
        for args, env in (("--non-interactive", None), (None, "1")):
            with self.subTest(args=args):
                if env:
                    self.env["CR_NONINTERACTIVE"] = env
                r, rd = self.collect(*([args] if args else []), "--source", "requirement=skip")
                self.assertNotIn("SOURCES_MISSING", r.stdout)
                doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
                self.assertEqual(doc["status"], "unasked")
        # 没有记进记录:交互运行时仍然会问
        self.env.pop("CR_NONINTERACTIVE")
        r, _ = self.collect("--source", "requirement=skip")
        self.assertIn("SOURCES_MISSING: 需求文档", r.stdout)


class GitHubContext(fixture.Base):
    def setUp(self):
        super().setUp()
        self.ghmock = os.path.join(self.tmp, "ghmock")
        os.makedirs(os.path.join(self.ghmock, "api"))
        self.env["MOCK_GH"] = self.ghmock
        self.env["PATH"] = os.path.join(HERE, "bin-gh") + ":" + self.env["PATH"]
        self.env["CR_FETCH"] = "0"

    def api(self, path, data):
        key = fixture.api_key(path)
        Path(self.ghmock, "api", key + ".json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_issue_and_comments_are_read_and_bots_skipped(self):
        repo = self.make_repo("gh", {"a.ts": "a\n"})
        base = self.git(repo, "rev-parse", "HEAD")
        self.git(repo, "switch", "-qc", "f")
        head = self.commit(repo, {"a.ts": "b\n"}, "改")
        diff = self.git(repo, "diff", base, head) + "\n"
        Path(self.ghmock, "diff.patch").write_text(diff, encoding="utf-8")
        Path(self.ghmock, "pr_title_url_baseRefOid_headRefOid_headRefName_baseRefName.txt").write_text(
            "\t".join(["改 a", "https://github.com/acme/gh/pull/5", base, head, "f", "main"]) + "\n", encoding="utf-8")
        Path(self.ghmock, "pr_body.txt").write_text("Closes #7\n", encoding="utf-8")
        Path(self.ghmock, "pr_closingIssuesReferences.txt").write_text("7\n", encoding="utf-8")
        self.api("repos/acme/gh/issues/7", {"title": "a 要改成 b", "html_url": "https://github.com/acme/gh/issues/7", "body": "## 验收标准\n- a.ts 内容为 b\n"})
        self.api("repos/acme/gh/pulls/5/comments?per_page=100", [{"user": {"login": "alice", "type": "User"}, "body": "这里要判空", "path": "a.ts", "line": 1, "id": 1}])
        self.api("repos/acme/gh/issues/5/comments?per_page=100", [{"user": {"login": "ci[bot]", "type": "Bot"}, "body": "build ok", "id": 2}])
        self.api("repos/acme/gh/pulls/5/reviews?per_page=100", [{"user": {"login": "bob", "type": "User"}, "body": "整体可以", "id": 3}])
        self.git(repo, "remote", "add", "origin", "https://github.com/acme/gh.git")
        r = self.run_script("collect-diff.sh", "https://github.com/acme/gh/pull/5", "--platform", "github", cwd=repo)
        rd = self.review_dir(r.stdout)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("关联需求 #7:a 要改成 b", intent)
        self.assertIn("a.ts 内容为 b(来自 #7)", intent)
        requirement = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "requirement")
        self.assertEqual((requirement["url"], requirement["title"]), ("https://github.com/acme/gh/issues/7", "a 要改成 b"))
        disc = self.load(os.path.join(rd, "discussions.json"))
        self.assertEqual(sorted(x["body"] for x in disc), ["整体可以", "这里要判空"])
        self.assertTrue(all(x["platform"] == "github" for x in disc))

    def test_renamed_repo_keeps_the_url_the_user_gave(self):
        # 仓库改名后平台返回新地址;输入是网址时,meta 的 url 要和用户给的一致,链接才不会指向新名字
        repo = self.make_repo("gh", {"a.ts": "a\n"})
        base = self.git(repo, "rev-parse", "HEAD")
        self.git(repo, "switch", "-qc", "f")
        head = self.commit(repo, {"a.ts": "b\n"}, "改")
        Path(self.ghmock, "diff.patch").write_text(self.git(repo, "diff", base, head) + "\n", encoding="utf-8")
        Path(self.ghmock, "pr_title_url_baseRefOid_headRefOid_headRefName_baseRefName.txt").write_text(
            "\t".join(["改 a", "https://github.com/acme/new-name/pull/5", base, head, "f", "main"]) + "\n", encoding="utf-8")
        self.git(repo, "remote", "add", "origin", "https://github.com/acme/old-name.git")
        r = self.run_script("collect-diff.sh", "https://github.com/acme/old-name/pull/5", "--platform", "github", cwd=repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["url"]), ("acme/old-name", "https://github.com/acme/old-name/pull/5"))
