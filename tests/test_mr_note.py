"""需求来源、MR 说明、analyst 校验和 GitHub 上下文的测试。"""
import json
import os
import re
import subprocess
from pathlib import Path

import fixture

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = fixture.SCRIPTS
TEAM = """
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/story/42
tools.doc_reader = 用 acme-kit:doc-reader 读文档链接,支持 https://docs.acme.example.com/ 下的页面
intent.required = ticket, doc
tools.doc_publisher = 用 acme-kit:page-publisher 把 HTML 发布成网页,返回网页链接
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
    def settings_of(self, text):
        import sys
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
        self.assertEqual(kinds["ticket"]["match"], ["tickets.acme.example.com", "pm.acme.example.com"])
        self.assertEqual(kinds["doc"]["match"], [])           # 提示词里没有网址:不自动识别
        self.assertEqual((kinds["ticket"]["label"], kinds["doc"]["label"]), ("需求单", "需求文档"))
        self.assertEqual(self.settings_of("")["ticket"]["tool"], "")

    def test_prompt_without_url_does_not_auto_detect_links(self):
        self.config("tools.requirement = 用 acme-kit:ticket-reader 读需求单\n")
        r, rd = self.collect()
        ticket = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "ticket")
        self.assertEqual(ticket["status"], "missing")
        self.assertIn("SOURCES_MISSING: 需求单", r.stdout)
        r, rd = self.collect("--source", "ticket=https://tickets.acme.example.com/story/42")
        self.assertNotIn("SOURCES_MISSING", r.stdout)

    def test_missing_required_sources_are_reported_once_per_branch(self):
        self.config(TEAM)
        r, rd = self.collect()
        self.assertIn("SOURCES_MISSING: 需求文档 还没有提供(doc)", r.stdout)
        src = self.load(os.path.join(rd, "sources.json"))
        ticket = next(x for x in src["items"] if x["kind"] == "ticket")
        self.assertEqual(ticket["source"], "自动识别")   # 提交说明里的需求单链接(按提示词里网址的域名识别)
        self.assertEqual(src["missing"], ["doc"])
        r, _ = self.collect("--source", "doc=skip")
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        r, rd = self.collect()                               # 记住了跳过,不再问
        self.assertNotIn("SOURCES_MISSING", r.stdout)
        self.assertEqual(next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")["source"], "记录")

    def test_unasked_is_not_remembered_and_default_requires_ticket_only(self):
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供(ticket)", r.stdout)   # 没有配置时只要需求单
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
        Path(stage, "ticket.md").write_text("url: https://tickets.acme.example.com/story/42\nerror: 没有权限\n---\n", encoding="utf-8")
        r, rd = self.collect("--source", "doc=https://docs.acme.example.com/d/abc", "--source-dir", stage)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## 需求文档:订单游标分页 PRD", intent)
        self.assertIn("接口支持 cursor 参数(来自 需求文档)", intent)
        self.assertIn("需求单 https://tickets.acme.example.com/story/42 没有读到(没有权限)", r.stdout)
        doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
        self.assertEqual(doc["title"], "订单游标分页 PRD")

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
        r, _ = self.collect("--source", "doc", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("KIND=VALUE", r.stderr)


class NonInteractive(Repo):
    def test_missing_sources_become_unasked(self):
        self.config(TEAM)
        for args, env in (("--non-interactive", None), (None, "1")):
            with self.subTest(args=args):
                if env:
                    self.env["CR_NONINTERACTIVE"] = env
                r, rd = self.collect(*([args] if args else []), "--source", "ticket=skip")
                self.assertNotIn("SOURCES_MISSING", r.stdout)
                doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
                self.assertEqual(doc["status"], "unasked")
        self.run_py("mr_note.py", rd)
        md = Path(rd, "mr.md").read_text(encoding="utf-8")
        self.assertIn("需求文档:未提供(非交互运行,没有询问)", md)
        # 没有记进记录:交互运行时仍然会问
        self.env.pop("CR_NONINTERACTIVE")
        r, _ = self.collect("--source", "ticket=skip")
        self.assertIn("SOURCES_MISSING: 需求文档", r.stdout)


class AnalystCheck(Repo):
    def setUp(self):
        super().setUp()
        self.config(TEAM)
        _, self.rd = self.collect("--source", "doc=skip", "--intent", "订单列表改为游标分页,解决翻页超时")
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
        _, self.rd = self.collect("--source", "doc=skip", "--intent", "订单列表改为游标分页", "--tested", "本地跑了 jest")
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
        self.assertIn("需求单:https://tickets.acme.example.com/story/42", md)
        self.assertIn("需求文档:未提供(作者跳过)", md)
        self.assertIn("`listOrders` 签名变化", md)
        self.assertIn("report/daily.ts", md)
        self.assertIn("怎么验证的:本地跑了 jest", md)
        self.assertIn("[重要] `api/orders.ts:1` 旧参数没兼容 — 未表态", md)
        self.assertIn("轻微 1 条未展开", md)
        self.assertIn("可能值得重点看(自动列出)", md)
        self.assertLessEqual(len(md.strip().splitlines()), 32)
        for word in ("已实现", "已覆盖", "无风险"):
            self.assertNotIn(word, md)
        self.assertIn("page=用 acme-kit:page-publisher 把 HTML 发布成网页,返回网页链接", out)   # 提示词原样交给 agent
        self.assertNotIn("page-publisher", md)                                          # MR 说明里不显示提示词
        self.assertIn("md_link=file://", out)
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

    def meta_add(self, text):
        meta = Path(self.rd, "meta.txt")
        meta.write_text(meta.read_text() + text, encoding="utf-8")

    def test_pr_mode_links_use_platform_head_and_self_hosted_url(self):
        # PR/MR:head 在平台上,不要求分支已推送;自建 GitLab 带端口、主机名没有点
        self.meta_add("mode=pr\nurl=http://localhost:8080/eng/shop/-/merge_requests/7\nrepo_id=localhost/eng/shop\n")
        out, md = self.note()
        self.assertNotIn("还没有推送", out)
        self.assertIn("](http://localhost:8080/eng/shop/-/blob/", md)
        self.meta_add("url=\ngitlab_host=http://git.corp:9000\nrepo=eng/shop\n")
        _, md = self.note()
        self.assertIn("](http://git.corp:9000/eng/shop/-/blob/", md)
        self.meta_add("gitlab_host=\nrepo_id=intranet/eng/shop\n")
        _, md = self.note()
        self.assertIn("](https://intranet/eng/shop/-/blob/", md)

    def test_note_when_links_cannot_be_built(self):
        out, md = self.note()   # 没有远程:repo_id 是本地路径
        self.assertIn("NOTE: 没有找到远程仓库的网页地址", out)
        self.assertNotIn("](http", md)

    def test_no_newer_warning_when_nothing_newer(self):
        self.meta_add("source_branch=feature\n")
        _, md = self.note()
        self.assertNotIn("审查之后又有", md)
        # PR/MR 模式:本地分支多出来的提交不是"审查之后的新提交"
        self.commit(self.repo, {"api/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts || 1\n}\n"}, "本地新提交")
        self.meta_add("mode=pr\n")
        out, md = self.note()
        self.assertNotIn("审查之后又有", md + out)

    def test_auto_focus_skips_findings_and_notes_once(self):
        Path(self.rd, "analysis.json").write_text(json.dumps({"summary": {"text": "起草的一句话"}}, ensure_ascii=False), encoding="utf-8")
        self.run_py("sources.py", "set", self.rd, "intent", "")
        out, md = self.note()
        self.assertEqual(out.count("AI 按需求材料起草"), 1)
        focus = md.split("### 可能值得重点看(自动列出)")[1].split("###")[0]
        self.assertNotIn("旧参数没兼容", focus)
        self.assertEqual(focus.strip().count("\n"), 0)   # 只有一条:签名变化,一行
        self.assertLessEqual(md.count("旧参数没兼容"), 1)

    def test_discussed_marker_in_note_and_report_lines(self):
        Path(self.rd, "findings.json").write_text(json.dumps([
            {"id": "f1", "no": 1, "file": "api/orders.ts", "line": 1, "category": "bug", "severity": "major",
             "summary": "旧参数没兼容", "raised_in_discussion": True}], ensure_ascii=False), encoding="utf-8")
        _, md = self.note()
        self.assertIn("讨论中已有人提出,代码仍未改", md)
        Path(self.rd, "final.json").write_text(json.dumps({"groups": {"defects": [
            {"no": 1, "summary": "x", "raised_in_discussion": True}]}, "resolved": []}), encoding="utf-8")
        out = self.run_script("report-lines.sh", self.rd).stdout
        self.assertIn("DISCUSSED: 1\t讨论中已有人提出,代码仍未改", out)

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
        self.api("repos/acme/gh/issues/7", {"title": "a 要改成 b", "html_url": "https://github.com/acme/gh/issues/7", "body": "## 验收标准\n- a.ts 内容为 b\n"})
        self.api("repos/acme/gh/pulls/5/comments?per_page=100", [{"user": {"login": "alice", "type": "User"}, "body": "这里要判空", "path": "a.ts", "line": 1, "id": 1}])
        self.api("repos/acme/gh/issues/5/comments?per_page=100", [{"user": {"login": "ci[bot]", "type": "Bot"}, "body": "build ok", "id": 2}])
        self.api("repos/acme/gh/pulls/5/reviews?per_page=100", [{"user": {"login": "bob", "type": "User"}, "body": "整体可以", "id": 3}])
        r = self.run_script("collect-diff.sh", "https://github.com/acme/gh/pull/5", "--platform", "github", cwd=repo)
        rd = self.review_dir(r.stdout)
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("关联需求 #7:a 要改成 b", intent)
        self.assertIn("a.ts 内容为 b(来自 #7)", intent)
        ticket = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "ticket")
        self.assertEqual((ticket["url"], ticket["title"]), ("https://github.com/acme/gh/issues/7", "a 要改成 b"))
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
        r = self.run_script("collect-diff.sh", "https://github.com/acme/old-name/pull/5", "--platform", "github", cwd=repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["url"]), ("acme/old-name", "https://github.com/acme/old-name/pull/5"))


class PluginConfig(Repo):
    def test_plugin_config_is_default_and_user_config_overrides(self):
        conf = os.path.join(self.tmp, "crosscheck.conf")
        Path(conf).write_text("intent.required = ticket, doc\ntools.doc_publisher = 用 acme-kit:page-publisher 发布网页\n", encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = conf
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单、需求文档 还没有提供", r.stdout)
        self.config("intent.required = ticket\n")
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供", r.stdout)
        out = self.run_script("config.sh", "get", "tools.doc_publisher").stdout.strip()
        self.assertEqual(out, "用 acme-kit:page-publisher 发布网页")
        self.config("tools.doc_publisher = mine\n")
        self.assertEqual(self.run_script("config.sh", "get", "tools.doc_publisher").stdout.strip(), "mine")

    SETTINGS = ("gitlab_hosts", "model.finder", "model.verifier",
                "tools.code_host", "tools.requirement",
                "tools.doc_reader",
                "tools.doc_publisher", "intent.required")

    def shipped_conf(self):
        return Path(SCRIPTS, "..", "crosscheck.conf").read_text(encoding="utf-8")

    def test_shipped_plugin_config_sets_nothing(self):
        lines = [l for l in self.shipped_conf().splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, [])
        for key in self.SETTINGS:
            self.assertEqual(self.run_script("config.sh", "get", key).stdout, "", key)

    def test_shipped_plugin_config_lists_every_setting_commented_out(self):
        text = self.shipped_conf()
        for key in self.SETTINGS:
            self.assertRegex(text, r"(?m)^# %s =" % re.escape(key), key)

    def test_prompt_values_keep_hash_inside_urls_and_drop_trailing_comments(self):
        self.config("tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/browse/XX-1#comment-2 # 团队约定\n")
        out = self.run_script("config.sh", "get", "tools.requirement").stdout.strip()
        self.assertEqual(out, "用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/browse/XX-1#comment-2")

    def test_empty_values_count_as_not_configured(self):
        conf = os.path.join(self.tmp, "crosscheck.conf")
        Path(conf).write_text("tools.requirement = 用 acme-kit:ticket-reader 读需求单\nintent.required = ticket, doc\n", encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = conf
        self.config("tools.requirement =\nintent.required = # 先不写\nmodel.finder =\n")
        self.assertEqual(self.run_script("config.sh", "get", "tools.requirement").stdout.strip(), "用 acme-kit:ticket-reader 读需求单")
        self.assertEqual(self.run_script("config.sh", "get", "intent.required").stdout.strip(), "ticket, doc")
        self.assertEqual(self.run_script("config.sh", "get", "model.finder").stdout, "")
        Path(conf).write_text("intent.required =\n", encoding="utf-8")
        self.config("")
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供(ticket)", r.stdout)   # 空值等于没配置:只要需求单
