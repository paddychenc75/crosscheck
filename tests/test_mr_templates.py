"""MR 说明的模板:mr.md / mr.html 的输出要和 tests/golden/mr_note/ 里存的逐字一致。

每个场景用固定的数据现场造一个审查目录(不依赖 git 提交的哈希和时间),跑 mr_note.py,
把生成的 mr.md、mr.html 和 NOTE 行跟存下来的文件比对。版本号、生成时间、提交哈希先换成占位符再比。
改模板或脚本后输出有意变了:UPDATE_GOLDEN=1 python3 -m unittest tests.test_mr_templates 重新生成,再看 git diff 确认改动是想要的。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fixture

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden", "mr_note")
PLUGIN = os.path.dirname(HERE)
SCRIPTS = fixture.SCRIPTS
SHA = "69b809735dd8fcb26d59f118188b4e6c1c861387"
CONFIG = """
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/story/42
tools.doc_reader = 用 acme-kit:doc-reader 读文档链接,支持 https://docs.acme.example.com/ 下的页面
intent.required = ticket, doc
tools.doc_publisher = 用 acme-kit:page-publisher 把 HTML 发布成网页,返回网页链接
"""
PR_META = {"mode": "pr", "url": "http://localhost:8080/eng/shop/-/merge_requests/7", "repo_id": "localhost/eng/shop",
           "number": "7", "source_branch": "feature", "target_branch": "main"}


def sig_symbol(name, i, **over):
    sym = {"name": name, "file": "api/f%d.ts" % i, "line": 3, "lang": "ts", "kind": "function", "change": "signature_changed",
           "old_sig": "%s(a)" % name, "new_sig": "%s(a, b)" % name, "exported": True, "hunk": "H1",
           "callers": [{"file": "use/c%d.ts" % i, "line": 10 + i, "in_diff": False}, {"file": "use/d%d.ts" % i, "line": 2, "in_diff": i % 3 == 0}],
           "callers_total": 2, "callers_truncated": False, "ambiguous": False, "skipped": None,
           "tests": [{"file": "tests/t%d.test.ts" % i, "changed": i % 2 == 0}]}
    sym.update(over)
    return sym


def base_facts():
    return {
        "version": 1, "scope": {"diff": "diff.patch", "files": 2, "added": 5, "deleted": 2},
        "hunks": [{"id": "H1", "file": "api/orders.ts", "start": 1, "end": 3, "add": 2, "del": 2, "func": "listOrders", "kind": "other", "symbols": ["listOrders"]},
                  {"id": "H2", "file": "config/app.yml", "start": 1, "end": 3, "add": 3, "del": 0, "func": "", "kind": "config", "symbols": []}],
        "symbols": [{"name": "listOrders", "file": "api/orders.ts", "line": 1, "lang": "ts", "kind": "function", "change": "signature_changed",
                     "old_sig": "export function listOrders(page: number)", "new_sig": "export function listOrders(opts: { cursor?: string })",
                     "exported": True, "hunk": "H1",
                     "callers": [{"file": "report/daily.ts", "line": 1, "in_diff": False}, {"file": "report/daily.ts", "line": 2, "in_diff": False}],
                     "callers_total": 2, "callers_truncated": False, "ambiguous": False, "skipped": None, "tests": []}],
        "attention": [{"type": "config_key", "file": "config/app.yml", "line": None, "detail": "新增配置文件 config/app.yml(1 个键)"},
                      {"type": "signature", "file": "api/orders.ts", "line": 1, "detail": "`listOrders` 签名变化"}],
        "skippable": [], "effort": {"score": 1, "basis": "2 文件 +5/−2,2 个目录,1 处签名变化或删除"},
        "method": "按名字在本仓库文本匹配(git grep -w),只看签名变化", "limits": [],
    }


def finding(i, severity="major", category="bug", **over):
    f = {"id": "f%d" % i, "no": i, "file": "api/orders.ts", "line": i, "category": category, "severity": severity, "summary": "问题 %d" % i}
    f.update(over)
    return f


class Case:
    """一个场景的数据,默认值是「基本的一次审查」,各场景只改自己关心的部分。"""

    def __init__(self):
        self.meta = {"mode": "local", "platform": "none", "repo_id": "/work/shop", "head_sha": SHA, "files": "2", "depth": "standard",
                     "source_branch": "feature", "target_branch": "main", "target_key": "branch-feature", "merged": "", "code_host_reason": ""}
        self.facts = base_facts()
        self.sources = {"ticket": {"status": "given", "url": "https://tickets.acme.example.com/story/42", "title": "订单游标分页"},
                        "doc": {"status": "skip"}}
        self.store = {"intent": "订单列表改为游标分页", "tested": "本地跑了 jest"}
        self.analysis = None
        self.findings = [finding(1, summary="旧参数没兼容"), finding(2, "minor", summary="命名")]
        self.final = None
        self.feedback = []
        self.earlier_finals = []
        self.repo = False   # 要不要建一个真的 git 仓库:审查之后分支上又有新提交时用


def scenarios():
    out = {}

    def case(fn):
        c = Case()
        fn(c)
        out[fn.__name__] = c
        return fn

    @case
    def basic(c):
        pass

    @case
    def merged(c):
        c.meta["merged"] = "yes"

    @case
    def code_host_failed(c):
        c.meta["code_host_reason"] = "glab 未登录"

    @case
    def merged_and_code_host_failed(c):
        c.meta.update(merged="yes", code_host_reason="glab 未登录")

    @case
    def pr_links_author_focus_and_page(c):
        c.meta.update(PR_META)
        c.store["focus"] = [{"where": "api/orders.ts:1", "ask": "兼容期够不够"}, {"where": "report/daily.ts:2", "ask": "调用方要不要一起改"}]
        c.store["page_url"] = "https://pages.example.com/p/1"

    @case
    def github_pr(c):
        c.meta.update(PR_META, platform="github", url="https://github.com/acme/shop/pull/7", repo_id="github.com/acme/shop")

    @case
    def summary_drafted_by_ai(c):
        c.store.pop("intent")
        c.analysis = {"summary": {"text": "起草的一句话"}}

    @case
    def title_only(c):
        c.store.pop("intent")
        c.meta["title"] = "订单游标分页 <b>"

    @case
    def no_summary_no_tested(c):
        c.store.clear()

    @case
    def discussed_verdicts_and_fixed(c):
        c.findings = [finding(1, summary="旧参数没兼容", raised_in_discussion=True), finding(2, summary="空指针"),
                      finding(3, "critical", "security", summary="拼接 SQL"), finding(4, "minor", summary="命名"), finding(5, summary="已经修了")]
        c.feedback = [{"key": "f2", "id": "f2", "verdict": "wontfix", "reason": "下个版本统一加", "time": "2026-10-27 10:00"},
                      {"key": "f3", "id": "f3", "verdict": "invalid", "reason": "上游已转义", "time": "2026-10-27 10:00"}]
        c.earlier_finals = [{"resolved": [{"id": "f5", "resolved_reason": "代码变了,这处已不存在"}]}]

    @case
    def commits_after_review(c):
        c.repo = True
        c.meta["repo_id"] = "gitlab.example.com/eng/shop"

    @case
    def no_findings(c):
        c.findings = []

    @case
    def many_findings_with_markup(c):
        c.findings = [finding(i, ["critical", "major", "minor"][i % 3], ["bug", "security", "intent", "rules"][i % 4], summary="问题 %d <x>&" % i)
                      for i in range(1, 12)]

    @case
    def sources_variants(c):
        c.sources = {"ticket": {"status": "none", "type": "hotfix"}, "doc": {"status": "given", "url": "https://docs.acme.example.com/d/abc",
                                                                           "title": "PRD", "read_error": "没有权限"}}

    @case
    def skippable_files(c):
        c.facts["skippable"] = [{"file": "dist/a.js", "reason": "generated"}, {"file": "dist/b.js", "reason": "generated"},
                                {"file": "yarn.lock", "reason": "lockfile"}, {"file": "b.ts", "reason": "weird"}]

    @case
    def facts_empty(c):
        c.facts = {}
        c.findings = []

    def rich(c):
        h = [{"id": "H%d" % i, "file": "api/f%d.ts" % i, "start": 3, "end": 9, "add": 1, "del": 1, "func": "fn%d" % i, "kind": "other", "symbols": []}
             for i in range(1, 4)]
        c.facts["hunks"] += h
        c.facts["skippable"] = [{"file": "dist/a.js", "reason": "generated"}, {"file": "yarn.lock", "reason": "lockfile"},
                                {"file": "a.ts", "reason": "whitespace_only"}, {"file": "b.ts", "reason": "weird"}]
        c.facts["attention"] = [
            {"type": "migration", "file": "db/001.sql", "line": 1, "detail": "db/001.sql 新增列 y"},
            {"type": "env_var", "file": "config/app.yml", "line": None, "detail": "config/app.yml 读取 ORDER_X"},
            {"type": "config_key", "file": "config/app.yml", "line": 2, "detail": "order.cursor.enabled"},
            {"type": "dependency", "file": "package.json", "line": None, "detail": "新增 lodash"},
            {"type": "signature", "file": "api/orders.ts", "line": 1, "detail": "sig"},
            {"type": "mystery", "file": "z.ts", "line": None, "detail": ""}]
        syms = []
        for i in range(7):
            syms.append(sig_symbol("fn%d" % i, i, change="signature_changed" if i % 2 else "removed", new_sig="fn%d(a, b)" % i if i % 2 else "",
                                   ambiguous=i == 2, hunk="H1"))
        syms[4].update(callers=[], callers_total=40, skipped="too_many_hits")
        syms[5].update(skipped="common_name")
        syms[6].update(callers_truncated=True)
        c.facts["symbols"] += syms
        c.facts["limits"] = ["不含反射", "不含依赖注入"]
        # 原有问题只在 CR 报告里单列,不进 MR 说明
        c.final = {"groups": {}, "resolved": [], "pre_existing": [{"file": "legacy/old.ts", "line": 9, "summary": "原有的竞态问题"}]}

    @case
    def rich_with_analysis_and_author_focus(c):
        rich(c)
        c.analysis = {"summary": {"text": "x"}, "groups": [
            {"title": "接口 <A>", "purpose": {"text": "游标"}, "files": ["api/orders.ts", "a.ts", "b.ts", "c.ts"], "hunks": ["H1"]},
            {"title": "配置", "purpose": None, "files": ["config/app.yml"], "hunks": ["H2", "H3"]}],
            "requirements": [{"criterion": "接口支持 cursor <参数>", "hunks": ["H1", "H99"]}]}
        c.store["focus"] = [{"where": "api/orders.ts:1", "ask": "x"}]

    @case
    def rich_pr_links(c):
        rich(c)
        c.meta.update(PR_META)

    @case
    def rich_auto_focus_over_line_limit(c):
        rich(c)
        c.findings = [finding(i, summary="重要 %d" % i) for i in range(1, 9)]

    @case
    def rich_only_attention(c):
        rich(c)
        c.facts["symbols"] = c.facts["symbols"][:1]
        c.store.pop("tested")

    return out


def normalize(text, version, sha=SHA):
    text = text.replace(sha, "<SHA>").replace(sha[:8], "<SHA>")
    text = re.sub(r"最后更新 \d{4}-\d\d-\d\d \d\d:\d\d", "最后更新 <NOW>", text)
    return text.replace("crosscheck %s" % version, "crosscheck <VERSION>") if version else text


class MrTemplates(fixture.Base):
    def setUp(self):
        super().setUp()
        self.config(CONFIG)
        meta = json.loads((Path(PLUGIN) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.version = meta.get("version", "")

    def build(self, c, name="basic"):
        rd = os.path.join(self.tmp, "review-" + name)
        os.makedirs(rd)
        store = os.path.join(self.home, "reviews", "shop", "branch-feature")
        os.makedirs(store)
        meta = dict(c.meta, repo_root="/nonexistent/shop", store_dir=store)
        if c.repo:
            repo = self.make_repo("shop", {"a.ts": "a\n"})
            self.git(repo, "switch", "-qc", "feature")
            meta.update(repo_root=repo, head_sha=self.commit(repo, {"a.ts": "b\n"}, "reviewed"))
            self.commit(repo, {"a.ts": "c\n"}, "after review")
        c.meta = meta
        Path(rd, "meta.txt").write_text("".join("%s=%s\n" % kv for kv in meta.items()), encoding="utf-8")
        for fname, data in (("analysis-facts.json", c.facts), ("analysis.json", c.analysis), ("findings.json", c.findings), ("final.json", c.final)):
            if data is not None:
                Path(rd, fname).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        items = [dict(v, kind=k) for k, v in c.sources.items()]
        Path(rd, "sources.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        sources = os.path.join(self.home, "reviews", "shop", "sources")
        os.makedirs(sources)
        Path(sources, "feature.json").write_text(json.dumps(c.store, ensure_ascii=False), encoding="utf-8")
        if c.feedback:
            Path(self.home, "reviews", "shop", "feedback.jsonl").write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in c.feedback), encoding="utf-8")
        for i, final in enumerate(c.earlier_finals):
            os.makedirs(os.path.join(store, "2026-10-2%d" % i))
            Path(store, "2026-10-2%d" % i, "final.json").write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        return rd

    def mr_note(self, rd, script_dir=SCRIPTS):
        return subprocess.run(["python3", os.path.join(script_dir, "mr_note.py"), rd], env=self.env, capture_output=True, text=True)

    def run_note(self, rd, script_dir=SCRIPTS):
        r = self.mr_note(rd, script_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (r.stdout, Path(rd, "mr.md").read_text(encoding="utf-8"), Path(rd, "mr.html").read_text(encoding="utf-8"))

    def compare(self, name, actual):
        for ext, text in actual.items():
            path = os.path.join(GOLDEN, "%s.%s" % (name, ext))
            if os.environ.get("UPDATE_GOLDEN"):
                os.makedirs(GOLDEN, exist_ok=True)
                Path(path).write_text(text, encoding="utf-8")
                continue
            self.assertTrue(os.path.isfile(path), "缺少 %s;用 UPDATE_GOLDEN=1 生成" % path)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), text, "%s.%s 和存下来的不一致" % (name, ext))

    def test_output_matches_golden_files(self):
        for name, c in scenarios().items():
            shutil.rmtree(self.home)       # 每个场景一个干净的记录目录(放在 with 之前:某个场景失败了也不影响后面的)
            os.makedirs(self.home)
            self.config(CONFIG)
            with self.subTest(scenario=name):
                rd = self.build(c, name)
                out, md, page = self.run_note(rd)
                notes = "".join(x + "\n" for x in out.splitlines() if x.startswith("NOTE:"))
                self.compare(name, {k: normalize(v, self.version, c.meta["head_sha"]) for k, v in (("md", md), ("html", page), ("notes", notes))})

    def test_golden_scenarios_cover_the_branches(self):
        """场景覆盖了已合入、MR 信息没读到、重点是自动列的还是作者点名的、被去掉的段落等分支。"""
        seen = {}
        for name in scenarios():
            for ext in ("md", "html"):
                seen[(name, ext)] = Path(GOLDEN, "%s.%s" % (name, ext)).read_text(encoding="utf-8")
        md = {n: t for (n, e), t in seen.items() if e == "md"}
        self.assertIn("状态:已合入 main,描述已无法影响合入", md["merged"])
        self.assertIn("MR 信息:没有读到(tools.code_host:glab 未登录)", md["code_host_failed"])
        self.assertIn("### 请重点看", md["pr_links_author_focus_and_page"])
        self.assertIn("[完整版](https://pages.example.com/p/1)", md["pr_links_author_focus_and_page"])
        self.assertIn("### 可能值得重点看(自动列出)", md["basic"])
        self.assertIn("> 审查之后又有 1 个提交,未经 crosscheck 审查", md["commits_after_review"])
        self.assertIn("已修复 1 条", md["discussed_verdicts_and_fixed"])
        self.assertIn("(讨论中已有人提出,代码仍未改)", md["discussed_verdicts_and_fixed"])
        self.assertIn("作者决定不修:下个版本统一加", md["discussed_verdicts_and_fixed"])
        self.assertIn("- 另有 ", md["many_findings_with_markup"])
        self.assertIn("没有未修复的发现", md["no_findings"])
        # 超过 30 行时去掉的段落
        self.assertIn("可跳过:生成代码 2 个、lock 文件 1 个、weird 1 个", md["skippable_files"])
        self.assertNotIn("可跳过:", md["rich_with_analysis_and_author_focus"])
        self.assertNotIn("可跳过:", md["rich_auto_focus_over_line_limit"])
        self.assertNotIn("### 需注意", md["rich_auto_focus_over_line_limit"])
        self.assertNotIn("### 测试", md["rich_auto_focus_over_line_limit"])
        for name, text in md.items():
            self.assertNotIn("原有的竞态问题", text)       # 原有问题不进 MR 说明
            self.assertTrue(text.startswith("<!-- crosscheck:begin -->\n") and text.endswith("<!-- crosscheck:end -->\n"), name)

    def test_template_edit_changes_the_output(self):
        """团队改 templates/mr.md、mr.html 就能改 MR 说明的结构;脚本读的是模板,不是写死的。"""
        copy = os.path.join(self.tmp, "cr")
        shutil.copytree(os.path.join(PLUGIN, "skills", "cr"), copy, ignore=shutil.ignore_patterns("__pycache__"))
        for name, old, new in (("mr.md", "### 阅读顺序", "### 读这里"), ("mr.html", "<h2>阅读顺序</h2>", "<h2>读这里</h2>")):
            p = Path(copy, "templates", name)
            self.assertIn(old, p.read_text(encoding="utf-8"))
            p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        rd = self.build(Case())
        _, md, page = self.run_note(rd, os.path.join(copy, "scripts"))
        self.assertIn("### 读这里", md)
        self.assertNotIn("### 阅读顺序", md)
        self.assertIn("<h2>读这里</h2>", page)

    def test_broken_template_fails_with_the_reason(self):
        copy = os.path.join(self.tmp, "cr")
        shutil.copytree(os.path.join(PLUGIN, "skills", "cr"), copy, ignore=shutil.ignore_patterns("__pycache__"))
        rd = self.build(Case())
        md = Path(copy, "templates", "mr.md")
        good = md.read_text(encoding="utf-8")
        for broken, reason in ((good + "{{no_such_name}}\n", "{{no_such_name}} 不是 MR 说明的内容"),
                               (good.replace("{{/skip}}\n", ""), "没有对应的 {{/skip}}"),
                               (good + "{{/focus}}\n", "{{/focus}} 前面没有对应的开头")):
            with self.subTest(reason=reason):
                md.write_text(broken, encoding="utf-8")
                r = self.mr_note(rd, os.path.join(copy, "scripts"))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(reason, r.stderr)
        md.unlink()
        r = self.mr_note(rd, os.path.join(copy, "scripts"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("读不到模板", r.stderr)


class RenderSyntax(fixture.Base):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, SCRIPTS)
        import mr_note
        self.render = mr_note.render
        self.error = mr_note.TemplateError

    def test_placeholders_blocks_comments_and_standalone_lines(self):
        values = {"a": "甲", "b": "", "list": "1. x\n2. y"}
        tpl = ("{{! 注释整行不输出}}\n"
               "A={{a}} B=[{{b}}]\n"
               "{{#b}}\n没有内容,不输出 {{b}}\n{{/b}}\n"
               "{{#a}}\n有内容:{{a}}\n{{/a}}\n"
               "{{^b}}\n反向块\n{{/b}}\n"
               "行内{{#a}}保留{{/a}}{{^a}}去掉{{/a}}{{#b}}去掉{{/b}}{{! 行内注释}}。\n"
               "{{list}}\n")
        self.assertEqual(self.render(tpl, values), "A=甲 B=[]\n有内容:甲\n反向块\n行内保留。\n1. x\n2. y\n")

    def test_values_are_not_parsed_again(self):
        self.assertEqual(self.render("{{a}}|{{#a}}{{a}}{{/a}}\n", {"a": "{{b}} {{#c}}"}), "{{b}} {{#c}}|{{b}} {{#c}}\n")

    def test_template_mistakes_are_errors(self):
        for tpl, reason in (("{{x}}\n", "{{x}} 不是 MR 说明的内容"), ("{{#a}}\n没闭合\n", "没有对应的 {{/a}}"), ("{{/a}}\n", "前面没有对应的开头"),
                            ("{{^a}}\n{{x}}\n{{/a}}\n", "{{x}} 不是 MR 说明的内容")):   # 不保留的块里写错了也要报
            with self.subTest(tpl=tpl):
                with self.assertRaises(self.error) as ctx:
                    self.render(tpl, {"a": "1"})
                self.assertIn(reason, str(ctx.exception))


class TemplateFiles(fixture.Base):
    """每个 skill 自带自己用的模板,放在各自的 templates/ 下。"""

    def test_each_skill_ships_its_own_templates(self):
        skills = os.path.join(PLUGIN, "skills")
        self.assertEqual(sorted(os.listdir(os.path.join(skills, "cr", "templates"))), ["mr.html", "mr.md", "report.md"])
        self.assertEqual(sorted(os.listdir(os.path.join(skills, "cr-rules", "templates"))), ["REVIEW.md", "rule.md"])

    def test_rule_template_is_what_rules_write_produces(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rules_write", os.path.join(SCRIPTS, "rules-write.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, SCRIPTS)
        spec.loader.exec_module(mod)
        block = mod.block_of({
            "title": "分页参数要有上限", "rule": "在 src/api/** 的接口里,必须校验分页参数 limit 的上限",
            "why": "不校验时传入很大的 limit 会一次查出全表(!123、!145)", "severity": "重要",
            "bad_example": "repo.list({ limit: req.query.limit })", "good_example": "repo.list({ limit: Math.min(req.query.limit, 100) })",
            "exceptions": "内部管理后台和离线导出接口不限", "mrs": [123, 145]})
        text = Path(PLUGIN, "skills", "cr-rules", "templates", "rule.md").read_text(encoding="utf-8")
        self.assertEqual(text.split("-->\n\n", 1)[1], "\n".join(block) + "\n")
