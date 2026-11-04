"""PR/MR 描述的模板:describe.md / describe.html 的输出要和 tests/golden/describe/ 里存的逐字一致,以及各段的规则。

每个场景用固定的数据现场造一个材料目录(不依赖 git 提交的哈希和时间),跑 describe.py,
把生成的 describe.md、describe.html 和 NOTE 行跟存下来的文件比对。版本号、生成时间、提交哈希先换成占位符再比。
改模板或脚本后输出有意变了:UPDATE_GOLDEN=1 python3 -m unittest test_describe_templates 重新生成,再看 git diff 确认改动是想要的。
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

import fixture

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden", "describe")
PLUGIN = os.path.dirname(HERE)
SHA = "69b809735dd8fcb26d59f118188b4e6c1c861387"
TICKET = "https://tickets.acme.example.com/story/42"
CONFIG = """
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/story/42
tools.doc_reader = 用 acme-kit:doc-reader 读文档链接,支持 https://docs.acme.example.com/ 下的页面
review.ask = requirement, doc
describe.ask = requirement
"""
PR_META = {"mode": "pr", "url": "http://localhost:8080/eng/shop/-/merge_requests/7", "repo_id": "localhost/eng/shop",
           "number": "7", "source_branch": "feature", "target_branch": "main"}
VERIFY = "### 怎么验证"
OWNER = "### CR负责人: @reviewer.username\n"


def sym(name, change, i=0, outside=0, inside=0, tests=None, **over):
    s = {"name": name, "file": "api/f%d.ts" % i, "line": 3, "lang": "ts", "kind": "function", "change": change,
         "old_sig": "%s(a)" % name if change != "added" else None, "new_sig": "%s(a, b)" % name if change != "removed" else None,
         "exported": True, "hunk": "H1",
         "callers": [{"file": "use/c%d.ts" % i, "line": 10 + k, "in_diff": False} for k in range(outside)] +
                    [{"file": "api/f%d.ts" % i, "line": 20 + k, "in_diff": True} for k in range(inside)],
         "callers_total": outside + inside, "callers_truncated": False, "ambiguous": False, "skipped": None, "tests": tests}
    s.update(over)
    return s


def entry(kind, label, change=None, file="api/routes.ts", line=4, **over):
    e = {"kind": kind, "label": label, "file": file, "line": line, "handler": None}
    if change:
        e["change"] = change
    e.update(over)
    return e


def att(type_, change, name, file, line=None, **over):
    a = {"type": type_, "change": change, "name": name, "file": file, "line": line, "detail": "%s %s" % (change, name)}
    a.update(over)
    return a


def res_att(change, container, *files):
    """影响面的一条资源:容器(xcassets、lproj、res/values)和里面实际改的文件。"""
    return att("resource", change, container, container, files=len(files), paths=[{"path": p, "change": change} for p in files])


def entrypoints(**over):
    e = {"changes": [], "triggers": [], "cases": [], "assertions": [], "command": ""}
    e.update(over)
    return e


def base_facts():
    return {
        "version": 1, "scope": {"diff": "diff.patch", "files": 2, "added": 5, "deleted": 2},
        "hunks": [{"id": "H1", "file": "api/orders.ts", "start": 1, "end": 3, "add": 2, "del": 2, "func": "listOrders", "kind": "other", "symbols": ["listOrders"]},
                  {"id": "H2", "file": "config/app.yml", "start": 1, "end": 3, "add": 3, "del": 0, "func": "", "kind": "config", "symbols": []}],
        "symbols": [{"name": "listOrders", "file": "api/orders.ts", "line": 1, "lang": "ts", "kind": "function", "change": "signature_changed",
                     "old_sig": "export function listOrders(page: number)", "new_sig": "export function listOrders(opts: { cursor?: string })",
                     "exported": True, "hunk": "H1",
                     "callers": [{"file": "report/daily.ts", "line": 1, "in_diff": False}, {"file": "report/daily.ts", "line": 2, "in_diff": False}],
                     "callers_total": 2, "callers_truncated": False, "ambiguous": False, "skipped": None,
                     "tests": [{"file": "tests/orders.test.ts", "changed": True}]}],
        "attention": [att("config_file", "added", "config/app.yml", "config/app.yml", keys=1)],
        "coverage": {"checked": 2, "unchecked": []},
        "entrypoints": entrypoints(triggers=[entry("config", "order.cursor.enabled", file="config/app.yml", line=3, value="true", on=True)]),
        "method": "按名字在本仓库文本匹配(git grep -w),只看签名变化", "limits": [],
    }


def hunk(i, file, start=1, kind="other"):
    return {"id": "H%d" % i, "file": file, "start": start, "end": start + 2, "add": 2, "del": 1, "func": "", "kind": kind, "symbols": [],
            "strings_added": [], "strings_removed": []}


def change(text, hunks, anchors, title="组"):
    return {"title": title, "text": text, "hunks": hunks, "anchors": anchors}


def verify(n, text, hunks, anchors):
    return {"change": n, "text": text, "hunks": hunks, "anchors": anchors}


def swift(c):
    """不在语言识别范围的工程:<功能A> 的界面和资源,外加工程配置和依赖文件。"""
    f = c.facts
    f["hunks"] = [hunk(1, "App/XViewController.swift", 12), hunk(2, "App/XViewController.swift", 40), hunk(3, "App/YView.swift", 3),
                  hunk(4, "App/Info.plist", 5, "other"), hunk(5, "App/Assets.xcassets/icon_history_v2.imageset/Contents.json", 1, "resource"),
                  hunk(6, "Tests/XTests.swift", 1, "test")]
    f["symbols"] = []
    f["scope"] = {"diff": "diff.patch", "files": 6, "added": 40, "deleted": 12}
    f["attention"] = [att("project_config", "modified", "App/Info.plist", "App/Info.plist"),
                      att("dependency", "modified", "Podfile", "Podfile", parts=[]),
                      res_att("added", "App/Assets.xcassets", "App/Assets.xcassets/icon_history_v2.imageset/Contents.json", "App/Assets.xcassets/icon_history_v2.imageset/icon.png"),
                      res_att("modified", "App/zh-Hans.lproj", "App/zh-Hans.lproj/Localizable.strings")]
    f["coverage"] = {"checked": 0, "unchecked": [{"lang": "Swift", "files": 2}]}
    f["entrypoints"] = entrypoints()


class Case:
    """一个场景的数据,默认值是「基本的一次描述」,各场景只改自己关心的部分。"""

    def __init__(self):
        self.meta = {"mode": "local", "platform": "none", "repo_id": "/work/shop", "head_sha": SHA, "files": "2", "depth": "standard",
                     "source_branch": "feature", "target_branch": "main", "target_key": "branch-feature", "merged": "", "code_host_reason": ""}
        self.facts = base_facts()
        self.sources = {"requirement": {"status": "given", "url": TICKET, "title": "订单游标分页"}}
        self.intent = "订单列表改为游标分页"
        self.criteria = []
        self.analysis = None
        self.author = {"tested": "本地跑了 jest"}


def rich(c):
    """每层都超过 2 条、有删除和迁移、有依赖变化、触发和用例都很多。"""
    f = c.facts
    f["symbols"] = [sym("addOne", "added", 1, tests=[]), sym("dropTwo", "removed", 2, outside=1), sym("sigNoUse", "signature_changed", 3, inside=1),
                    sym("sigUsed", "signature_changed", 4, outside=2, tests=[]), sym("bodyA", "body_only", 5, tests=[]),
                    sym("bodyB", "body_only", 6, tests=[{"file": "tests/b.test.ts", "changed": False}]), sym("bodyC", "body_only", 7, outside=1)]
    f["attention"] = [att("migration", "added", "db/002.sql", "db/002.sql"), att("migration", "removed", "db/001.sql", "db/001.sql"),
                      att("config_key", "added", "order.cursor.enabled", "config/app.yml", 3),
                      att("config_key", "removed", "order.legacy", "config/app.yml", 5),
                      att("env_var", "added", "ORDER_API_URL", "src/env.ts", 1),
                      att("dependency", "modified", "package.json", "package.json", parts=["新增 axios@1.6.0", "删除 left-pad", "lodash 4.0.0 → 4.17.0"])]
    f["entrypoints"] = entrypoints(
        changes=[entry("route", "POST /orders/export", "added"), entry("route", "GET /orders/legacy", "removed", line=None),
                 entry("cli_flag", "--cursor", "added", file="bin/cli.ts", line=8)],
        triggers=[entry("config", "order.cursor.enabled", file="config/app.yml", line=3, value="true", on=True),
                  entry("route", "POST /orders/export"), entry("cli_flag", "--cursor", file="bin/cli.ts", line=8),
                  entry("cron", "0 3 * * *", file="jobs/nightly.ts", line=2)],
        cases=[{"name": "case %d" % i, "file": "tests/orders.test.ts", "line": i} for i in range(1, 8)],
        assertions=[{"text": "expect(x).toBe(%d)" % i, "file": "tests/orders.test.ts", "line": i} for i in range(1, 4)],
        command="npm test")


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
    def pr_links_and_author_focus(c):
        c.meta.update(PR_META)
        c.author["focus"] = [{"where": "api/orders.ts:1", "ask": "兼容期够不够"}, {"where": "report/daily.ts:2", "ask": "调用方要不要一起改"}]

    @case
    def github_pr(c):
        c.meta.update(PR_META, platform="github", url="https://github.com/acme/shop/pull/7", repo_id="github.com/acme/shop")

    @case
    def summary_drafted_by_ai(c):
        c.intent = ""
        c.analysis = {"summary": {"text": "起草的一句话"}}

    @case
    def title_only(c):
        c.intent = ""
        c.meta["title"] = "订单游标分页 <b>"

    @case
    def no_summary_no_tested(c):
        c.intent = ""
        c.author = {}

    @case
    def ticket_unasked_non_interactive(c):
        c.sources = {"requirement": {"status": "unasked", "source": "非交互运行"}}

    @case
    def ticket_none_and_docs(c):
        c.sources = {"requirement": {"status": "none", "type": "hotfix"},
                     "doc": {"status": "given", "source": "本地文档", "local_files": ["plan.md", "api.md"]}}

    @case
    def sources_variants(c):
        c.sources = {"requirement": {"status": "skip"}, "doc": {"status": "given", "url": "https://docs.acme.example.com/d/abc",
                                                            "title": "PRD", "read_error": "没有权限"}}

    @case
    def doc_skipped_is_not_listed(c):
        c.sources["doc"] = {"status": "skip"}

    @case
    def facts_empty(c):
        c.facts = {}

    @case
    def nothing_found_but_bodies_changed(c):
        c.facts["symbols"] = [sym("total", "body_only", 1, tests=[])]
        c.facts["attention"] = []
        c.facts["entrypoints"] = entrypoints()

    @case
    def tests_and_command(c):
        c.facts["entrypoints"]["cases"] = [{"name": "returns next cursor", "file": "tests/orders.test.ts", "line": 3}]
        c.facts["entrypoints"]["assertions"] = [{"text": "expect(res.next).toBeDefined()", "file": "tests/orders.test.ts", "line": 4}]
        c.facts["entrypoints"]["command"] = "npm test"

    @case
    def criteria_mapped(c):
        c.criteria = ["接口支持 cursor <参数>", "旧参数继续可用"]
        c.analysis = {"requirements": [{"criterion": "接口支持 cursor <参数>(来自 需求单)", "hunks": ["H1"]}]}

    @case
    def rich_layers(c):
        rich(c)

    @case
    def rich_pr_links(c):
        rich(c)
        c.meta.update(PR_META)

    @case
    def changes_and_verify(c):
        c.facts["hunks"].append(hunk(3, "api/export.ts", 8))
        c.analysis = {"changes": [change("`listOrders` 改收 `cursor` 参数", ["H1"], ["listOrders", "cursor"], "游标分页")],
                      "verify": [verify(1, "订单列表翻页 → 请求带 `cursor`", ["H1"], ["cursor"])]}

    @case
    def swift_unchecked(c):
        swift(c)
        c.analysis = {"changes": [change("`XViewController` 右上角加 `rightBarButtonItem`", ["H1", "H2"],
                                         ["XViewController", "rightBarButtonItem"], "历史入口"),
                                  change("新增图标 `icon_history_v2`", ["H5"], ["icon_history_v2"], "图标")],
                      "verify": [verify(1, "<功能A> 页 → 右上角出现历史按钮", ["H1"], ["rightBarButtonItem"])]}
        c.author = {}

    @case
    def swift_partial(c):
        swift(c)
        c.facts["coverage"] = {"checked": 3, "unchecked": [{"lang": "Swift", "files": 2}, {"lang": "Objective-C", "files": 1}]}
        c.author = {}

    @case
    def no_verify_no_tested(c):
        c.facts["entrypoints"] = entrypoints()
        c.author = {}

    @case
    def i18n_and_other_by_dir(c):
        """多语言文案汇总成一条(带语言);其他文件多时按目录汇总;影响面的资源按类型汇总文件数。"""
        f = c.facts
        langs = ("en", "zh-Hans", "zh-Hant", "ja", "ko")
        f["hunks"] = ([hunk(1, "App/Feature/VoucherView.swift", 5)] + [hunk(2 + i, "App/Models/M%d.swift" % i) for i in range(4)] +
                      [hunk(6 + i, "App/Services/S%d.swift" % i) for i in range(3)] +
                      [hunk(9 + i, "res/ios/%s.lproj/Localizable.strings" % lang, kind="resource") for i, lang in enumerate(langs)])
        f["symbols"] = []
        f["scope"] = {"diff": "diff.patch", "files": 13, "added": 60, "deleted": 8}
        f["attention"] = ([res_att("modified", "res/ios/%s.lproj" % lang, "res/ios/%s.lproj/Localizable.strings" % lang) for lang in langs] +
                          [res_att("removed", "App/Assets.xcassets", "App/Assets.xcassets/icon_history_v2.imageset/Contents.json", "App/Assets.xcassets/icon_history_v2.imageset/icon.png")])
        f["coverage"] = {"checked": 0, "unchecked": [{"lang": "Swift", "files": 8}]}
        f["entrypoints"] = entrypoints()
        c.analysis = {"changes": [change("展示券的不可用原因", ["H1"], ["VoucherView"])]}

    @case
    def links_found_in_ticket(c):
        """作者没给需求文档:用 analyst 从需求单里找到的需求文档和设计稿链接。"""
        c.sources = {"requirement": {"status": "given", "url": TICKET}}
        c.analysis = {"links": [{"kind": "doc", "url": "https://docs.acme.example.com/d/abc", "label": "订单 PRD"},
                                {"kind": "design", "url": "https://www.figma.com/file/xyz", "label": "列表页设计稿"}]}

    @case
    def local_docs_absolute_paths(c):
        c.sources = {"requirement": {"status": "given", "url": TICKET},
                     "doc": {"status": "given", "source": "本地文档", "local_files": ["/Users/someone/work/shop/docs/spec.md", "prd.md"]}}

    @case
    def rich_with_focus_and_criteria(c):
        rich(c)
        c.criteria = ["条件 %d" % i for i in range(1, 7)]
        c.analysis = {"summary": {"text": "x"}, "requirements": [{"criterion": "条件 1(来自 需求单)", "hunks": ["H1"]}]}
        c.author["focus"] = [{"where": "api/orders.ts:%d" % i, "ask": "问题%d" % i} for i in range(1, 3)]
        c.facts["hunks"] += [hunk(i, "src/m%d.ts" % i) for i in range(3, 9)]
        c.analysis["changes"] = [change("改动 %d 的 `listOrders`" % i, ["H1"], ["listOrders"]) for i in range(1, 9)]
        c.analysis["verify"] = [verify(i, "入口 %d → 看到 `listOrders`" % i, ["H1"], ["listOrders"]) for i in range(1, 5)]

    return out


def normalize(text, version, sha=SHA):
    text = text.replace(sha, "<SHA>").replace(sha[:8], "<SHA>")
    text = re.sub(r"最后更新 \d{4}-\d\d-\d\d \d\d:\d\d", "最后更新 <NOW>", text)
    return text.replace("crosscheck %s" % version, "crosscheck <VERSION>") if version else text


class Templates(fixture.Base):
    def setUp(self):
        super().setUp()
        self.config(CONFIG)
        meta = json.loads((Path(PLUGIN) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.version = meta.get("version", "")
        self.out = os.path.join(self.home, "describe", "shop", "branch-feature")

    def build(self, c, name="basic"):
        rd = os.path.join(self.tmp, "material-" + name)
        os.makedirs(rd)
        store = os.path.join(self.home, "reviews", "shop", "branch-feature")
        meta = dict(c.meta, repo_root="/nonexistent/shop", store_dir=store)
        c.meta = meta
        Path(rd, "meta.txt").write_text("".join("%s=%s\n" % kv for kv in meta.items()), encoding="utf-8")
        for fname, data in (("describe-facts.json", c.facts), ("analyst.json", c.analysis)):
            if data is not None:
                Path(rd, fname).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        items = [dict(v, kind=k) for k, v in c.sources.items()]
        Path(rd, "sources.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        Path(rd, "intent-arg.txt").write_text(c.intent, encoding="utf-8")
        text = "# 这次改动的意图\n\n"
        if c.criteria:
            text += "## 验收标准\n\n" + "".join("%d. %s(来自 需求单)\n" % (i, x) for i, x in enumerate(c.criteria, 1))
        # 模板场景也经过真实 analysis_check -> describe 链路;原文行是固定夹具。
        raw = dict(c.analysis or {})
        if isinstance(raw.get("summary"), dict):
            raw["summary"] = dict(raw["summary"], quote="模板场景需求原文")
        if c.analysis is not None:
            Path(rd, "analyst.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        text += "\n模板场景需求原文\n"
        text += "\n".join(x["url"] for x in raw.get("links", []) if isinstance(x, dict))
        Path(rd, "intent.md").write_text(text, encoding="utf-8")
        patches = []
        for h in c.facts.get("hunks", []):
            anchors = [a for field in ("changes", "impact", "verify")
                       for item in (raw.get(field) if isinstance(raw.get(field), list) else [])
                       if isinstance(item, dict) and h["id"] in item.get("hunks", [])
                       for a in item.get("anchors", [])]
            patches.append("diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+{1}\n".format(
                h["file"], " ".join(anchors) or "fixture"))
        Path(rd, "diff.patch").write_text("".join(patches), encoding="utf-8")
        os.makedirs(self.out, exist_ok=True)
        Path(self.out, "author.json").write_text(json.dumps(c.author, ensure_ascii=False), encoding="utf-8")
        return rd

    def describe(self, rd, scripts=fixture.DESCRIBE_SCRIPTS):
        return self.run_describe("describe.py", rd, scripts=scripts, check=False)

    def run_note(self, rd, scripts=fixture.DESCRIBE_SCRIPTS):
        r = self.describe(rd, scripts)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, Path(self.out, "describe.md").read_text(encoding="utf-8"), Path(self.out, "describe.html").read_text(encoding="utf-8")

    def md_of(self, c, name="case"):
        shutil.rmtree(self.out, ignore_errors=True)
        return self.run_note(self.build(c, name))[1]

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
                self.assertEqual(md.count(OWNER), 1)
                self.assertLess(md.index("| 设计稿 |"), md.index(OWNER))
                self.assertLess(md.index(OWNER), md.index("### 改了什么"))
                self.assertIn("<h3>CR负责人: @reviewer.username</h3>", page)
                self.assertLess(page.index("<h2>基本信息"), page.index("CR负责人:"))
                if "<h2>改了什么" in page:
                    self.assertLess(page.index("CR负责人:"), page.index("<h2>改了什么"))
                notes = "".join(x + "\n" for x in out.splitlines() if x.startswith("NOTE:"))
                self.compare(name, {k: normalize(v, self.version, c.meta["head_sha"]) for k, v in (("md", md), ("html", page), ("notes", notes))})

    def test_changes_section_is_plain_text(self):
        """md 的「改了什么」只写文字:不带文件链接,也不带代码块编号(出处只在网页版)。"""
        for f in sorted(os.listdir(GOLDEN)):
            if not f.endswith(".md"):
                continue
            text = Path(GOLDEN, f).read_text(encoding="utf-8")
            m = re.search(r"### 改了什么.*?\n(.*?)\n\n", text, re.S)
            if m:
                self.assertNotRegex(m.group(1), r"\]\(|\(H\d", f)

    def test_golden_files_are_exactly_the_scenarios(self):
        names = {"%s.%s" % (n, ext) for n in scenarios() for ext in ("md", "html", "notes")}
        self.assertEqual(set(os.listdir(GOLDEN)), names)

    def test_golden_scenarios_cover_the_branches(self):
        """场景覆盖了已合入、PR/MR 信息没读到、作者点名的重点、需求单各种状态、改了什么、不认识的语言等分支。"""
        md = {name: Path(GOLDEN, "%s.md" % name).read_text(encoding="utf-8") for name in scenarios()}
        for name in ("merged", "code_host_failed"):      # 工具状态不贴进描述,只在终端 NOTE
            self.assertNotIn("状态", md[name])
            self.assertNotIn("PR/MR 信息", md[name])
        self.assertIn("### 请重点看\n1. ", md["pr_links_and_author_focus"])
        self.assertNotIn("重点看", md["basic"])
        self.assertIn("| 需求单 |  |\n| 需求文档 |  |\n| 设计稿 |  |\n", md["ticket_unasked_non_interactive"])
        self.assertIn("| 需求单 |  |\n| 需求文档 |  |\n", md["ticket_none_and_docs"])   # 本地文档不写
        self.assertIn("| 需求文档 |  |", md["local_docs_absolute_paths"])
        self.assertNotIn("/Users/", md["local_docs_absolute_paths"])
        self.assertIn("| 需求单 | %s |\n" % TICKET, md["local_docs_absolute_paths"])           # 没有标题时直接给链接
        self.assertIn("| 需求单 |  |", md["sources_variants"])
        self.assertIn("| 需求文档 | [PRD](https://docs.acme.example.com/d/abc)(内容未读取:没有权限) |\n", md["sources_variants"])
        self.assertIn("| 需求文档 |  |", md["doc_skipped_is_not_listed"])   # 跳过:行还在,值留空
        self.assertIn("### 改了什么\n分支 feature 的改动\n", md["no_summary_no_tested"])   # 一句话是这一段的开头
        self.assertIn("\n1. 展示券的不可用原因\n2. 多语言文案 5 个文件(en、zh-Hans、zh-Hant 另 2 种语言)\n3. 其他:7 处(Models 4、Services 3)\n",
                      md["i18n_and_other_by_dir"])
        self.assertNotIn("- 资源:", md["i18n_and_other_by_dir"])
        self.assertNotIn("Localizable.strings", md["i18n_and_other_by_dir"])      # 逐个路径只在完整版
        self.assertIn("Localizable.strings", Path(GOLDEN, "i18n_and_other_by_dir.html").read_text(encoding="utf-8"))
        self.assertIn("| 需求文档 | [订单 PRD](https://docs.acme.example.com/d/abc) |\n| 设计稿 | [列表页设计稿](https://www.figma.com/file/xyz) |\n", md["links_found_in_ticket"])
        self.assertNotIn("需求对照", md["criteria_mapped"])      # 需求对照只在网页版
        self.assertIn("旧参数继续可用(来自 需求单)", Path(GOLDEN, "criteria_mapped.html").read_text(encoding="utf-8"))
        self.assertIn("| 资料 | 链接 |\n|---|---|\n| 需求单 | [订单游标分页](%s) |\n| 需求文档 |  |\n| 设计稿 |  |\n\n" % TICKET,
                      md["basic"])
        self.assertIn("### 改了什么\n订单列表改为游标分页\n1. 改动:2 处(orders.ts、app.yml)\n", md["basic"])
        self.assertNotIn("签名变 1", md["basic"])      # 总览只在完整版
        self.assertIn(VERIFY + "\n- 执行记录:作者提供:本地跑了 jest\n", md["basic"])
        self.assertIn("\n1. `listOrders` 改收 `cursor` 参数\n2. 其他:2 处(app.yml、export.ts)\n", md["changes_and_verify"])
        self.assertIn("- 订单列表翻页 → 请求带 `cursor`\n", md["changes_and_verify"])
        self.assertNotIn("自动识别范围", md["swift_unchecked"])
        self.assertNotIn("自动识别范围", md["swift_partial"])
        self.assertIn("- 配置与依赖:修改工程配置 `App/Info.plist`\n- 配置与依赖:依赖变化 `Podfile`\n", md["swift_unchecked"])
        self.assertIn("1. `XViewController` 右上角加 `rightBarButtonItem`\n2. 新增图标 `icon_history_v2`\n"
                      "3. 其他:2 处(YView.swift、Info.plist)\n", md["swift_unchecked"])     # 测试代码块不进「其他」
        self.assertIn(VERIFY + "\n- <功能A> 页 → 右上角出现历史按钮\n", md["swift_unchecked"])
        self.assertNotIn("怎么验证", md["swift_partial"])
        self.assertNotIn("怎么验证", md["no_verify_no_tested"])
        for name, text in md.items():
            with self.subTest(scenario=name):
                self.assertTrue(text.startswith("| 资料 | 链接 |\n|---|---|\n| 需求单 |") and text.count(OWNER) == 1, text)
                self.assertNotIn("自动识别范围", text)
                self.assertEqual("- 执行记录:" in text, bool(scenarios()[name].author.get("tested")))
                for word in ("已实现", "已覆盖", "无风险", "无影响", "预期通过", "不下结论", "阅读顺序", "可跳过", "需注意", "审查量",
                             "可能值得重点看", "可能相关的测试文件", "需求文档:未提供", "> 只按名字", "<!--", "crosscheck", "在本地生成",
                             "未发现", "没有认出路由", "/work/shop", "回滚方式", "(第 "):
                    self.assertNotIn(word, text)
                if "### 需求对照" not in text:
                    self.assertNotIn("未提供(需求材料", text)
            with self.subTest(scenario=name, page="html"):
                page = Path(GOLDEN, "%s.html" % name).read_text(encoding="utf-8")
                for word in ("未发现", "没有认出路由", "/Users/", "回滚方式"):
                    self.assertNotIn(word, page)


class Sections(Templates):
    """各段的规则:改了什么的分组和「其他」、影响面分层排序和检查范围,验证段每组一行。"""

    def test_resource_paths_and_language_counts(self):
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        import describe_facts
        for path, lang in (("App/en.lproj/Localizable.strings", "en"),
                           ("app/src/main/res/values-zh-rCN/strings.xml", "zh-rCN"),
                           ("App/Localizable.xcstrings", None)):
            with self.subTest(path=path):
                self.assertEqual(describe_facts.path_rule(path), "resource")
                self.assertTrue(describe_facts.is_i18n(path))
                self.assertEqual(describe_facts.lang_of(path), lang)
        self.assertFalse(describe_facts.is_i18n("App/icon.png"))
        # 同目录里不是文案的文件不算多语言,values-night、values-v21、Base.lproj 也不是语言
        for path in ("app/src/main/res/values/colors.xml", "app/src/main/res/values-night/styles.xml",
                     "App/Base.lproj/Main.storyboard", "App/zh-Hans.lproj/Main.xib"):
            with self.subTest(path=path):
                self.assertFalse(describe_facts.is_i18n(path))
        for path in ("app/src/main/res/values-night/colors.xml", "app/src/main/res/values-v21/styles.xml",
                     "app/src/main/res/values-sw600dp/dimens.xml", "App/Base.lproj/Main.storyboard"):
            with self.subTest(path=path):
                self.assertIsNone(describe_facts.lang_of(path))
        self.assertIsNone(describe_facts.lang_of("App/Base.lproj/Localizable.strings"))
        self.assertEqual(describe.Note.resource_text(["App/en.lproj/a.strings", "App/en.lproj/b.strings",
                                                     "App/zh.lproj/a.strings", "App/icon.png"]),
                         "多语言文案 3 个文件(en、zh);图片等资源 1 个文件")

    def test_impact_resources_are_classified_by_file_not_container(self):
        """同一个容器里的文案和其他资源分开计数;Android 的 values 容器不带斜杠也能认出文案;限定符目录不算语言,非文案文件不带语言。"""
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        items = [res_att("modified", "app/src/main/res/values-zh-rCN", "app/src/main/res/values-zh-rCN/strings.xml",
                         "app/src/main/res/values-zh-rCN/colors.xml"),
                 res_att("modified", "app/src/main/res/values-night", "app/src/main/res/values-night/colors.xml"),
                 res_att("removed", "App/en.lproj", "App/en.lproj/Localizable.strings"),
                 res_att("removed", "App/Base.lproj", "App/Base.lproj/Main.storyboard")]
        self.assertEqual(describe.Note.res_summary(None, items), "多语言文案 2 个文件(zh-rCN、en,删除 1 个);图片等资源 3 个文件(删除 1 个)")

    def test_resource_summary_does_not_make_ungrouped_code_other(self):
        c = Case()
        c.facts["hunks"].append(hunk(3, "App/Localizable.xcstrings", kind="resource"))
        md = self.md_of(c)
        self.assertIn("1. 多语言文案 1 个文件\n2. 改动:2 处(orders.ts、app.yml)", md)
        page = Path(self.out, "describe.html").read_text(encoding="utf-8")
        self.assertIn("改动:2 处", page)
        self.assertNotIn("其他:2 处", page)

    def test_layers_are_ordered_one_fact_per_line(self):
        c = Case()
        c.facts["symbols"] = [sym("addOne", "added", 1), sym("sigNoUse", "signature_changed", 2, inside=1),
                              sym("dropTwo", "removed", 3), sym("sigUsed", "signature_changed", 4, outside=2)]
        c.facts["attention"] = []
        c.facts["entrypoints"] = entrypoints(changes=[entry("route", "POST /a", "added"), entry("route", "GET /b", "removed", line=None)])
        md = self.md_of(c)
        self.assertNotIn("签名变 2", md)      # md 里没有总览行,网页版保留
        self.assertIn("删除 2 · 接口 2 · 函数 4(签名变 2)", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        self.assertIn("- 接口:删除路由 `GET /b`(`api/routes.ts`)\n- 接口:新增路由 `POST /a`(`api/routes.ts:4`)\n", md)
        self.assertIn("- 函数签名:删除 `dropTwo`(`api/f3.ts`)\n- 函数签名:签名变化 `sigUsed`(`api/f4.ts:3`,diff 外 2 处调用)\n"
                      "- 函数签名:签名变化 `sigNoUse`(`api/f2.ts:3`)\n", md)
        self.assertNotIn("内部函数", md)
        self.assertNotIn("addOne", md)      # 这一层只列删除和签名变化的函数,新增的只在完整版
        self.assertIn("addOne", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        # 层序:接口 > 数据库与迁移 > 配置与依赖 > 资源 > 函数签名,空层不显示
        self.assertLess(md.index("- 接口:"), md.index("- 函数签名:"))
        self.assertNotIn("- 数据库与迁移", md)
        self.assertNotIn("- 配置与依赖", md)
        self.assertNotIn("- 资源", md)

    def test_layer_order_across_all_four(self):
        md = Path(GOLDEN, "rich_layers.md").read_text(encoding="utf-8")
        heads = [m.group(1) for m in re.finditer(r"^- (接口|数据库与迁移|配置与依赖|资源|函数签名):", md, re.M)]
        self.assertEqual(heads, ["接口"] * 3 + ["数据库与迁移"] * 2 + ["配置与依赖"] * 4 + ["函数签名"] * 3)      # 一条事实一行
        self.assertNotIn("签名变 2", md)
        self.assertIn("删除 4 · 迁移 2 · 接口 3 · 配置 4", Path(GOLDEN, "rich_layers.html").read_text(encoding="utf-8"))
        self.assertIn("- 数据库与迁移:删除迁移 `db/001.sql`\n- 数据库与迁移:新增迁移 `db/002.sql`\n", md)

    def test_body_only_business_impact_and_scope_in_full_report(self):
        c = Case()
        c.facts.update(symbols=[sym("total", "body_only", 1)], attention=[], entrypoints=entrypoints())
        md = self.md_of(c)
        self.assertNotIn("### 主要影响", md)
        page = Path(self.out, "describe.html").read_text()
        self.assertIn("只改了实现,不列入签名与接口变化表", page)
        self.assertNotIn("未覆盖", page)
        c.analysis = {"impact": [{"text": "空列表展示空态", "hunks": ["H1"], "anchors": ["empty"]}]}
        c.facts["coverage"] = {"checked": 0, "unchecked": [{"lang": "Swift", "files": 4}]}
        md = self.md_of(c, "business")
        self.assertIn("### 主要影响\n- 空列表展示空态", md)
        self.assertNotIn("Swift", md)
        page = Path(self.out, "describe.html").read_text()
        for text in ("空列表展示空态", "<code>empty</code>", "H1 <code>", "Swift", "业务说明依据 diff 起草", "不证明业务语义"):
            self.assertIn(text, page)

    def test_valid_business_and_technical_impact_is_never_cut(self):
        c = scenarios()["rich_with_focus_and_criteria"]
        c.analysis["impact"] = [change("场景%d的用户可以继续翻页" % i, ["H1"], ["cursor"]) for i in range(8)]
        md = self.md_of(c)
        page = Path(self.out, "describe.html").read_text()
        for i in range(8):
            self.assertIn("场景%d的用户可以继续翻页" % i, md)
            self.assertIn("场景%d的用户可以继续翻页" % i, page)
        for text in ("--cursor", "sigNoUse", "order.cursor.enabled", "ORDER_API_URL", "lodash 4.0.0 → 4.17.0"):
            self.assertIn(text, md)
            self.assertIn(text.replace("→", "→"), page)

    def test_business_impact_first_then_one_technical_fact_per_line(self):
        c = Case()
        rich(c)
        c.analysis = {"impact": [change("场景%d的用户可以继续翻页" % i, ["H1"], ["cursor"]) for i in range(2)]}
        section = self.md_of(c).split("### 主要影响\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual(section[:2], ["- 场景0的用户可以继续翻页", "- 场景1的用户可以继续翻页"])      # 业务影响在前
        self.assertEqual([x for x in section if x.startswith("- 配置与依赖:")], [
            "- 配置与依赖:删除配置键 `order.legacy`(`config/app.yml:5`)",
            "- 配置与依赖:依赖变化 `package.json`(新增 axios@1.6.0、删除 left-pad、lodash 4.0.0 → 4.17.0)",
            "- 配置与依赖:新增配置键 `order.cursor.enabled`(`config/app.yml:3`)",
            "- 配置与依赖:新环境变量 `ORDER_API_URL`(`src/env.ts:1`)"])
        self.assertEqual(len(section), 2 + 3 + 2 + 4 + 3)      # 业务 2 + 接口 3 + 迁移 2 + 配置 4 + 函数签名 3
        self.assertFalse([x for x in section if ";" in x])      # 不再用分号把几条事实连成一行

    def test_resources_and_project_config_by_path(self):
        """按路径认的工程配置算在「配置与依赖」,资源另起一行和它并列;只有资源时只有资源这一行。"""
        c = Case()
        swift(c)
        c.facts["attention"] = [a for a in c.facts["attention"] if a["type"] == "resource"]
        md = self.md_of(c)
        self.assertNotIn("### 主要影响", md)
        self.assertNotIn("配置与依赖", md)
        self.assertNotIn("· 资源 2", md)
        self.assertIn("资源 2", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        self.assertIn("App/Assets.xcassets/icon_history_v2.imageset/icon.png", Path(self.out, "describe.html").read_text())
        self.assertNotIn("配置键", md)

    def test_other_group_lists_every_unreferenced_non_test_hunk(self):
        c = Case()
        c.facts["hunks"] += [hunk(3, "src/a.ts"), hunk(4, "src/b.ts"), hunk(5, "src/c.ts"), hunk(6, "src/d.ts"), hunk(7, "t/a.test.ts", kind="test")]
        c.analysis = {"changes": [change("改了 `listOrders`", ["H1", "H7"], ["listOrders"])]}
        md = self.md_of(c)
        self.assertIn("1. 改了 `listOrders`\n2. 其他:5 处(src 4、config 1)\n", md)
        page = Path(self.out, "describe.html").read_text(encoding="utf-8")
        for h in ("H2", "H3", "H4", "H5", "H6"):
            self.assertIn("%s <code>" % h, page)
        # 每个代码块都归了组:没有「其他」
        c.analysis = {"changes": [change("改了 `listOrders`", ["H%d" % i for i in range(1, 7)], ["listOrders"])]}
        self.assertNotIn("其他", self.md_of(c, "all"))

    def test_verify_comes_only_from_checked_analysis_and_script_entries(self):
        """analyst 回复里格式不对的验证内容不会出现;验证段只有脚本认出的入口和校验过的 verify。"""
        c = Case()
        c.analysis = {"summary": {"text": "起草"}, "verify": "运行 make e2e,预期全部通过", "trigger": "手工点一下"}
        md = self.md_of(c)
        section = md.split(VERIFY)[1].split("\n\n")[0]
        self.assertNotIn("make e2e", md)
        self.assertNotIn("手工点一下", md)
        self.assertEqual(section.strip().splitlines(), ["- 执行记录:作者提供:本地跑了 jest"])

    def test_no_verify_section_when_nothing_to_write(self):
        c = Case()
        c.facts.update(symbols=[], entrypoints=entrypoints())
        c.author = {}
        md = self.md_of(c)
        self.assertNotIn("怎么验证", md)
        self.assertNotIn("没有认出", md)
        self.assertNotIn("怎么验证", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        c.author = {"tested": "手工点过"}
        md = self.md_of(c, "tested")
        self.assertIn(VERIFY + "\n- 执行记录:作者提供:手工点过\n", md)

    def test_execution_record_only_when_tested(self):
        c = Case()
        c.author = {}
        md = self.md_of(c)
        self.assertNotIn("执行记录", md)
        self.assertNotIn("未验证", md)
        self.assertNotIn("执行记录", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        c.author = {"tested": "pnpm test:退出码 0"}
        md = self.md_of(c, "tested")
        self.assertIn("- 执行记录:作者提供:pnpm test:退出码 0\n", md)
        self.assertIn("<h3>执行记录</h3><p>作者提供:pnpm test:退出码 0</p>", Path(self.out, "describe.html").read_text(encoding="utf-8"))

    def test_verify_keeps_every_checked_analysis_entry(self):
        """analyst 校验过的验证条目全部保留,没有条数上限;有它们时脚本认出的入口不再重复写进 md,完整版两者都列。"""
        c = Case()
        c.facts["entrypoints"] = entrypoints(triggers=[entry("route", "GET /r%d" % i, line=i, handler="h%d" % i) for i in range(1, 4)])
        c.analysis = {"changes": [change("改 `listOrders` %d" % i, ["H1"], ["listOrders"]) for i in range(1, 8)],
                      "verify": [verify(i, "入口 %d → 看到 `listOrders`" % i, ["H1"], ["listOrders"]) for i in range(1, 8)]}
        out, md, page = self.run_note(self.build(c, "many"))
        lines = md.split(VERIFY + "\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual(lines, ["- 入口 %d → 看到 `listOrders`" % i for i in range(1, 8)] + ["- 执行记录:作者提供:本地跑了 jest"])
        self.assertNotIn("GET /r", md)
        self.assertIn("NOTE: 验证步骤由 AI 依据代码起草,贴之前请核对", out)
        for i in range(1, 4):      # 完整版逐条列出,不丢入口
            self.assertIn("<td><code>GET /r%d</code> / <code>h%d</code></td>" % (i, i), page)
        self.assertEqual(page.count("脚本入口线索,未核对调用链"), 3)
        self.assertEqual(page.count("AI 起草,请核对"), 7)

    def test_script_entries_never_fill_in_model_verify(self):
        """没有模型建议时 md 不补入口,HTML 保留明确标注的技术线索。"""
        c = Case()
        c.facts["entrypoints"] = entrypoints(triggers=[entry("route", "GET /r%d" % i, line=i, handler="h%d" % i) for i in range(1, 8)] +
                                             [entry("route", "GET /dup", line=1)])      # 和第一条同一 文件:行,去掉
        c.author = {}
        md = self.md_of(c, "fill")
        self.assertNotIn(VERIFY, md)
        self.assertNotIn("GET /r", md)
        page = Path(self.out, "describe.html").read_text()
        self.assertIn("脚本入口线索,未核对调用链", page)
        self.assertNotIn("→ 进入", page)
        # 脚本的入口对不上某一组,所以按整体判断:只有部分组写了验证时,也不再补入口
        c.analysis = {"changes": [change("改 `listOrders` %d" % i, ["H1"], ["listOrders"]) for i in range(1, 3)],
                      "verify": [verify(1, "入口 1 → 看到 `listOrders`", ["H1"], ["listOrders"])]}
        md = self.md_of(c, "partial")
        self.assertEqual(md.split(VERIFY + "\n")[1].split("\n\n")[0].splitlines(), ["- 入口 1 → 看到 `listOrders`"])
        self.assertNotIn("GET /r", md)

    def test_tests_and_untested_are_only_in_the_full_page(self):
        """新增用例、断言、项目命令和没在改动测试里出现的函数只放在完整版的「测试线索」里,不进贴到 PR/MR 的那份。"""
        c = Case()
        changed = [{"file": "tests/c.test.ts", "changed": True}]
        c.facts["symbols"] = [sym("a", "added", 1, tests=[]), sym("b", "signature_changed", 2, tests=[{"file": "tests/b.test.ts", "changed": False}]),
                              sym("c", "signature_changed", 3, tests=changed), sym("d", "added", 4, tests=None),
                              sym("e", "added", 5, tests=[], exported=False), sym("f", "body_only", 6, tests=[])]
        c.facts["entrypoints"].update(cases=[{"name": "returns next cursor", "file": "tests/orders.test.ts", "line": 3}],
                                      assertions=[{"text": "expect(res.next).toBeDefined()", "file": "tests/orders.test.ts", "line": 4}],
                                      command="npm test")
        md = self.md_of(c)
        page = Path(self.out, "describe.html").read_text(encoding="utf-8")
        for text in ("returns next cursor", "toBeDefined", "npm test", "没有出现"):
            self.assertNotIn(text, md)
            self.assertIn(text, page)
        self.assertIn("2 个新增/签名变化的函数在改动的测试里没有出现:a、b", page)
        self.assertNotIn("未覆盖", md)

    def test_requirements_absent_without_criteria(self):
        c = Case()
        c.analysis = {"requirements": [{"criterion": "编的一条", "hunks": ["H1"]}]}
        md = self.md_of(c)
        self.assertNotIn("### 需求对照", md)
        self.assertNotIn("<h2>需求对照", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        self.assertIn("不是 intent.md 里列出的验收标准", md)

    def test_missing_analysis_disclosure_does_not_invent_verification(self):
        md = Path(GOLDEN, "basic.md").read_text(encoding="utf-8")
        self.assertIn("缺少可用分析", md)
        self.assertEqual(md.count("引用校验只确认出处"), 1)
        self.assertNotIn("开启配置", md)
        self.assertIn("- 执行记录:作者提供:本地跑了 jest", md)

    def test_template_edit_changes_the_output(self):
        """团队改 templates/describe.md、describe.html 就能改描述的结构;脚本读的是模板,不是写死的。"""
        copy = str(self.install_plugin() / "skills" / "describe")
        for name, old, new in (("describe.md", "### 主要影响", "### 改动范围"), ("describe.html", "<h2>技术变化与自动识别范围</h2>", "<h2>改动范围</h2>")):
            p = Path(copy, "templates", name)
            self.assertIn(old, p.read_text(encoding="utf-8"))
            p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        rd = self.build(Case())
        _, md, page = self.run_note(rd, os.path.join(copy, "scripts"))
        self.assertIn("### 改动范围", md)
        self.assertNotIn("### 主要影响", md)
        self.assertIn("<h2>改动范围</h2>", page)

    def test_broken_template_fails_with_the_reason(self):
        copy = str(self.install_plugin() / "skills" / "describe")
        rd = self.build(Case())
        md = Path(copy, "templates", "describe.md")
        good = md.read_text(encoding="utf-8")
        for broken, reason in ((good + "{{no_such_name}}\n", "{{no_such_name}} 不是 PR/MR 描述的内容"),
                               (good.replace("{{/verify}}\n", ""), "没有对应的 {{/verify}}"),
                               (good + "{{/focus}}\n", "{{/focus}} 前面没有对应的开头")):
            with self.subTest(reason=reason):
                md.write_text(broken, encoding="utf-8")
                r = self.describe(rd, os.path.join(copy, "scripts"))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(reason, r.stderr)
        md.unlink()
        r = self.describe(rd, os.path.join(copy, "scripts"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("读不到模板", r.stderr)


class RenderSyntax(fixture.Base):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        self.render = describe.render
        self.error = describe.TemplateError

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
        for tpl, reason in (("{{x}}\n", "{{x}} 不是 PR/MR 描述的内容"), ("{{#a}}\n没闭合\n", "没有对应的 {{/a}}"), ("{{/a}}\n", "前面没有对应的开头"),
                            ("{{^a}}\n{{x}}\n{{/a}}\n", "{{x}} 不是 PR/MR 描述的内容")):   # 不保留的块里写错了也要报
            with self.subTest(tpl=tpl):
                with self.assertRaises(self.error) as ctx:
                    self.render(tpl, {"a": "1"})
                self.assertIn(reason, str(ctx.exception))


class TemplateFiles(fixture.Base):
    """每个 skill 自带自己用的模板,放在各自的 templates/ 下。"""

    def test_each_skill_ships_its_own_templates(self):
        skills = os.path.join(PLUGIN, "skills")
        self.assertEqual(sorted(os.listdir(os.path.join(skills, "review", "templates"))), ["report.md"])
        self.assertEqual(sorted(os.listdir(os.path.join(skills, "describe", "templates"))), ["describe.html", "describe.md"])
        self.assertEqual(sorted(os.listdir(os.path.join(skills, "improve", "templates"))), ["REVIEW.md", "plan.md", "rule.md"])

    def test_template_placeholders_are_all_provided_by_the_script(self):
        """模板注释里列出的可用名字就是脚本提供的名字,没有多也没有少。"""
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        for name in ("describe.md", "describe.html"):
            text = Path(PLUGIN, "skills", "describe", "templates", name).read_text(encoding="utf-8")
            listed = set(re.findall(r"^\{\{! (\w+) — ", text, re.M))      # 每个占位符一行注释:名字 — 作用
            used = {m.group(2) for m in describe.TAG.finditer(text)}
            self.assertEqual(used, listed, name)
            head = text[:text.index("\n", text.rindex("{{!"))].splitlines()      # 顶部说明:每行都是独立的一条注释,说明里没有花括号
            for line in head:
                self.assertRegex(line, r"^\{\{![^{}]*\}\}$", name)
