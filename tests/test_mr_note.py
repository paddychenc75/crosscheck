"""需求来源、MR 说明、analyst 校验和 GitHub 上下文的测试。"""
import json
import os
import subprocess
from pathlib import Path

import fixture

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = fixture.SCRIPTS
TEAM = """
source.ticket = acme-kit:ticket-reader
source.ticket.label = Ticket
source.prd = acme-kit:doc-reader
source.prd.label = PRD
source.required = ticket, prd
output.page = acme-kit:page-publisher
"""


class Repo(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo("shop", {
            "api/orders.ts": "export function listOrders(page: number) {\n  return page\n}\n",
            "report/daily.ts": "import { listOrders } from '../api/orders'\nexport const x = listOrders(1)\n",
        })
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"api/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts\n}\n",
                                "config/app.yml": "order:\n  cursor:\n    enabled: true\n"},
                    "改为游标分页 https://tickets.acme.example.com/story/42")
        self.env["CR_FETCH"] = "0"

    def collect(self, *args, check=True):
        r = self.run_script("collect-diff.sh", "--base", "main", *args, cwd=self.repo, check=check)
        return r, (self.review_dir(r.stdout) if r.returncode == 0 else None)



class Sources(Repo):
    def test_missing_required_sources_are_reported_once_per_branch(self):
        self.config(TEAM)
        r, rd = self.collect()
        self.assertIn("SOURCES_MISSING: PRD 还没有提供(prd)", r.stdout)
        src = self.load(os.path.join(rd, "sources.json"))
        ticket = next(x for x in src["items"] if x["kind"] == "ticket")
        self.assertEqual(ticket["source"], "自动识别")   # 提交说明里的需求单链接(按工具名里的 ticket 识别)
        self.assertEqual(src["missing"], ["prd"])
        r, _ = self.collect("--source", "prd=skip")
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        r, rd = self.collect()                               # 记住了跳过,不再问
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        self.assertEqual(next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "prd")["source"], "记录")

    def test_unasked_is_not_remembered_and_default_requires_ticket_only(self):
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供(ticket)", r.stdout)   # 没有配置时只要需求单
        self.commit(self.repo, {"b.ts": "b\n"}, "Closes #12")
        r, _ = self.collect()
        self.assertNotIn("SOURCES_MISSING", r.stdout)       # 提交说明里的需求编号也算
        self.config("source.required = prd\n")
        r, _ = self.collect("--source", "prd=unasked")
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING", r.stdout)

    def test_staged_documents_join_intent_and_title(self):
        self.config(TEAM)
        stage = self.run_py("sources.py", "stage").stdout.strip()
        Path(stage, "prd.md").write_text("url: https://docs.acme.example.com/d/abc\ntitle: 订单游标分页 PRD\ntool: acme-kit:doc-reader\n---\n"
                                         "## 验收标准\n- 接口支持 cursor 参数\n", encoding="utf-8")
        Path(stage, "ticket.md").write_text("url: https://tickets.acme.example.com/story/42\nerror: 没有权限\n---\n", encoding="utf-8")
        r, rd = self.collect("--source", "prd=https://docs.acme.example.com/d/abc", "--source-dir", stage)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## PRD:订单游标分页 PRD", intent)
        self.assertIn("接口支持 cursor 参数(来自 PRD)", intent)
        self.assertIn("Ticket https://tickets.acme.example.com/story/42 没有读到(没有权限)", r.stdout)
        prd = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "prd")
        self.assertEqual(prd["title"], "订单游标分页 PRD")

    def test_multiple_docs_and_sources_do_not_change_context_hash(self):
        Path(self.repo, "plan.md").write_text("方案 A\n", encoding="utf-8")
        Path(self.repo, "api.md").write_text("接口文档\n", encoding="utf-8")
        _, rd = self.collect("--doc", "plan.md", "--doc", "api.md")
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## 文档 plan.md", intent)
        self.assertIn("## 文档 api.md", intent)
        h1 = Path(rd, "context-hash.txt").read_text()
        _, rd2 = self.collect("--doc", "plan.md", "--doc", "api.md", "--source", "ticket=skip", "--tested", "跑了单测")
        self.assertEqual(Path(rd2, "context-hash.txt").read_text(), h1)
        Path(self.repo, "api.md").write_text("接口文档 v2\n", encoding="utf-8")
        _, rd3 = self.collect("--doc", "plan.md", "--doc", "api.md")
        self.assertNotEqual(Path(rd3, "context-hash.txt").read_text(), h1)

    def test_bad_source_argument(self):
        r, _ = self.collect("--source", "prd", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("KIND=VALUE", r.stderr)


class AnalystCheck(Repo):
    def setUp(self):
        super().setUp()
        self.config(TEAM)
        _, self.rd = self.collect("--source", "prd=skip", "--intent", "订单列表改为游标分页,解决翻页超时")
        self.hunks = [h["id"] for h in self.load(os.path.join(self.rd, "analysis-facts.json"))["hunks"]]

    def check(self, raw):
        Path(self.rd, "analyst.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        out = self.run_py("analysis_check.py", self.rd).stdout
        return out, self.load(os.path.join(self.rd, "analysis.json"))

    def test_quotes_must_come_from_material(self):
        out, a = self.check({"summary": {"text": "改成游标分页", "quote": "订单列表改为游标分页"},
                             "groups": [{"title": "接口", "purpose": "提升性能十倍", "quote": "性能提升十倍以上", "hunks": self.hunks[:1]}]})
        self.assertEqual(a["summary"]["text"], "改成游标分页")
        self.assertIsNone(a["groups"][0]["purpose"])
        self.assertIn("引文在需求材料里找不到", out)

    def test_banned_words_unknown_hunks_and_leftovers(self):
        out, a = self.check({"summary": {"text": "已实现游标分页,无风险", "quote": "订单列表改为游标分页"},
                             "groups": [{"title": "接口", "hunks": [self.hunks[0], "H99"]}]})
        self.assertIsNone(a["summary"])
        self.assertIn("含结论性用语", out)
        self.assertIn("没有这个代码块", out)
        self.assertEqual(a["groups"][-1]["title"], "其他")
        self.assertEqual(sorted(sum((g["hunks"] for g in a["groups"]), [])), sorted(self.hunks))

    def test_missing_reply_fails_softly(self):
        out = self.run_py("analysis_check.py", self.rd).stdout
        self.assertIn("STATUS: failed", out)


class MrNote(Repo):
    def setUp(self):
        super().setUp()
        self.config(TEAM)
        _, self.rd = self.collect("--source", "prd=skip", "--intent", "订单列表改为游标分页", "--tested", "本地跑了 jest")
        Path(self.rd, "findings.json").write_text(json.dumps([
            {"id": "f1", "no": 1, "file": "api/orders.ts", "line": 1, "category": "bug", "severity": "major", "summary": "旧参数没兼容"},
            {"id": "f2", "no": 2, "file": "api/orders.ts", "line": 2, "category": "bug", "severity": "minor", "summary": "命名"},
        ], ensure_ascii=False), encoding="utf-8")
        Path(self.rd, "final.json").write_text('{"groups": {}, "resolved": []}', encoding="utf-8")

    def note(self, *args):
        out = self.run_py("mr_note.py", self.rd, *args).stdout
        return out, Path(self.rd, "mr.md").read_text(encoding="utf-8")

    def test_markdown_has_facts_links_and_cr_status(self):
        out, md = self.note()
        self.assertTrue(md.startswith("<!-- crosscheck:begin -->"))
        self.assertIn("<!-- crosscheck:end -->", md)
        self.assertIn("**订单列表改为游标分页**", md)
        self.assertIn("Ticket:https://tickets.acme.example.com/story/42", md)
        self.assertIn("PRD:未提供(作者跳过)", md)
        self.assertIn("`listOrders` 签名变化", md)
        self.assertIn("report/daily.ts", md)
        self.assertIn("怎么验证的:本地跑了 jest", md)
        self.assertIn("[重要] `api/orders.ts:1` 旧参数没兼容 — 未表态", md)
        self.assertIn("轻微 1 条未展开", md)
        self.assertIn("可能值得重点看(自动列出)", md)
        self.assertLessEqual(len(md.strip().splitlines()), 32)
        for word in ("已实现", "已覆盖", "无风险"):
            self.assertNotIn(word, md)
        self.assertIn("page=acme-kit:page-publisher", out)
        self.assertIn("glab mr create --target-branch main", out)
        html = Path(self.rd, "mr.html").read_text(encoding="utf-8")
        self.assertIn("<title>", html)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn("轻微", html)

    def test_author_focus_page_url_and_unpushed_note(self):
        self.run_py("sources.py", "set", self.rd, "focus", json.dumps([{"where": "api/orders.ts:1", "ask": "兼容期够不够"}], ensure_ascii=False))
        self.run_py("sources.py", "set", self.rd, "page_url", "https://pages.example.com/p/1")
        bare = self.bare("shop")
        self.git(self.repo, "remote", "add", "origin", "https://github.com/acme/shop.git")
        self.git(self.repo, "config", "remote.origin.pushurl", bare)
        _, md = self.note()
        self.assertIn("### 请重点看", md)
        self.assertIn("兼容期够不够", md)
        self.assertIn("[完整版](https://pages.example.com/p/1)", md)

    def test_links_only_when_pushed(self):
        # repo_id 写进 meta 后,提交在远端存在才生成行链接
        meta = Path(self.rd, "meta.txt")
        meta.write_text(meta.read_text() + "repo_id=gitlab.example.com/eng/shop\n", encoding="utf-8")
        out, md = self.note()
        self.assertIn("还没有推送到远端", out)
        self.assertNotIn("](https://gitlab.example.com", md)
        bare = self.bare("shop")
        self.git(self.repo, "remote", "add", "origin", bare)
        self.git(self.repo, "push", "-q", "origin", "feature")
        out, md = self.note()
        self.assertIn("https://gitlab.example.com/eng/shop/-/blob/", md)

    def test_feedback_reasons_and_new_commits(self):
        hist = os.path.dirname(self.kv(os.path.join(self.rd, "meta.txt"))["store_dir"])
        os.makedirs(hist, exist_ok=True)
        with open(os.path.join(hist, "feedback.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"key": "f1", "id": "f1", "verdict": "wontfix", "reason": "下个版本统一加", "time": "2026-10-27 10:00"}, ensure_ascii=False) + "\n")
        self.commit(self.repo, {"api/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts || null\n}\n"}, "再改")
        out, md = self.note()
        self.assertIn("作者决定不修:下个版本统一加", md)
        self.assertIn("审查之后又有 1 个提交", md)


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
        self.api("repos/acme/gh/issues/7", {"title": "a 要改成 b", "body": "## 验收标准\n- a.ts 内容为 b\n"})
        self.api("repos/acme/gh/pulls/5/comments?per_page=100", [{"user": {"login": "alice", "type": "User"}, "body": "这里要判空", "path": "a.ts", "line": 1, "id": 1}])
        self.api("repos/acme/gh/issues/5/comments?per_page=100", [{"user": {"login": "ci[bot]", "type": "Bot"}, "body": "build ok", "id": 2}])
        self.api("repos/acme/gh/pulls/5/reviews?per_page=100", [{"user": {"login": "bob", "type": "User"}, "body": "整体可以", "id": 3}])
        r = self.run_script("collect-diff.sh", "https://github.com/acme/gh/pull/5", "--platform", "github", cwd=repo)
        rd = self.review_dir(r.stdout)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("关联需求 #7:a 要改成 b", intent)
        self.assertIn("a.ts 内容为 b(来自 #7)", intent)
        disc = self.load(os.path.join(rd, "discussions.json"))
        self.assertEqual(sorted(x["body"] for x in disc), ["整体可以", "这里要判空"])
        self.assertTrue(all(x["platform"] == "github" for x in disc))


class PluginConfig(Repo):
    def test_plugin_config_is_default_and_user_config_overrides(self):
        conf = os.path.join(self.tmp, "crosscheck.conf")
        Path(conf).write_text("source.required = ticket, prd\nsource.prd.label = 需求文档\noutput.page = acme-kit:page-publisher\n", encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = conf
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单、需求文档 还没有提供", r.stdout)
        self.config("source.required = ticket\n")
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供", r.stdout)
        out = self.run_script("config.sh", "get", "output.page").stdout.strip()
        self.assertEqual(out, "acme-kit:page-publisher")
        self.config("output.page = mine\n")
        self.assertEqual(self.run_script("config.sh", "get", "output.page").stdout.strip(), "mine")

    def test_shipped_plugin_config_sets_nothing(self):
        conf = os.path.join(SCRIPTS, "..", "crosscheck.conf")
        lines = [l for l in Path(conf).read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, [])
