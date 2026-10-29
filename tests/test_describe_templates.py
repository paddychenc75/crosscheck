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
from unittest import mock

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
VERIFY = "### 怎么验证(由代码推断,未执行)"
OWNER = "### CR负责人:\n\n@xx\n"


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
                      att("resource", "added", "App/Assets.xcassets", "App/Assets.xcassets", files=2),
                      att("resource", "modified", "App/zh-Hans.lproj", "App/zh-Hans.lproj", files=1)]
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
        c.analysis = {"requirements": [{"criterion": "接口支持 cursor <参数>(来自 需求单)", "hunks": ["H1", "H99"]}]}

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
    def local_docs_absolute_paths(c):
        c.sources = {"requirement": {"status": "given", "url": TICKET},
                     "doc": {"status": "given", "source": "本地文档", "local_files": ["/Users/someone/work/shop/docs/spec.md", "prd.md"]}}

    @case
    def rich_trimmed_with_focus_and_criteria(c):
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
        for fname, data in (("describe-facts.json", c.facts), ("analysis.json", c.analysis)):
            if data is not None:
                Path(rd, fname).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        items = [dict(v, kind=k) for k, v in c.sources.items()]
        Path(rd, "sources.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        Path(rd, "intent-arg.txt").write_text(c.intent, encoding="utf-8")
        text = "# 这次改动的意图\n\n"
        if c.criteria:
            text += "## 验收标准\n\n" + "".join("%d. %s(来自 需求单)\n" % (i, x) for i, x in enumerate(c.criteria, 1))
        Path(rd, "intent.md").write_text(text, encoding="utf-8")
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
                self.assertTrue(md.endswith(OWNER), md)
                self.assertIn("<section><h2>CR负责人:</h2><p>@xx</p></section>", page)
                notes = "".join(x + "\n" for x in out.splitlines() if x.startswith("NOTE:"))
                self.compare(name, {k: normalize(v, self.version, c.meta["head_sha"]) for k, v in (("md", md), ("html", page), ("notes", notes))})

    def test_golden_files_are_exactly_the_scenarios(self):
        names = {"%s.%s" % (n, ext) for n in scenarios() for ext in ("md", "html", "notes")}
        self.assertEqual(set(os.listdir(GOLDEN)), names)

    def test_golden_scenarios_cover_the_branches(self):
        """场景覆盖了已合入、PR/MR 信息没读到、作者点名的重点、需求单各种状态、改了什么、不认识的语言、裁剪等分支。"""
        md = {name: Path(GOLDEN, "%s.md" % name).read_text(encoding="utf-8") for name in scenarios()}
        self.assertIn("\n- 状态:已合入 main,描述已无法影响合入\n", md["merged"])
        self.assertIn("\n- PR/MR 信息:没有读到(tools.code_host:glab 未登录)\n", md["code_host_failed"])
        self.assertIn("### 请重点看\n1. ", md["pr_links_and_author_focus"])
        self.assertNotIn("重点看", md["basic"])
        self.assertIn("- 需求单:未提供(非交互运行,没有询问)\n", md["ticket_unasked_non_interactive"])
        self.assertIn("- 需求单:无需求单:hotfix\n", md["ticket_none_and_docs"])
        self.assertIn("- 需求文档:plan.md、api.md\n", md["ticket_none_and_docs"])
        self.assertIn("- 需求文档:spec.md、prd.md\n", md["local_docs_absolute_paths"])
        self.assertNotIn("/Users/", md["local_docs_absolute_paths"])
        self.assertIn("- 需求单:%s\n" % TICKET, md["local_docs_absolute_paths"])           # 没有标题时直接给链接
        self.assertIn("- 需求单:未提供(作者跳过)", md["sources_variants"])
        self.assertIn("- 需求文档:[PRD](https://docs.acme.example.com/d/abc)(内容未读取:没有权限)\n", md["sources_variants"])
        self.assertNotIn("需求文档", md["doc_skipped_is_not_listed"])
        self.assertIn("**分支 feature 的改动**", md["no_summary_no_tested"])
        self.assertIn("### 需求对照\n- 接口支持 cursor <参数>(来自 需求单):`api/orders.ts`\n- 旧参数继续可用(来自 需求单):没有标出对应的改动\n",
                      md["criteria_mapped"])
        self.assertIn("**订单列表改为游标分页**\n- 需求单:[订单游标分页](%s)\n- 规模:2 个文件 +5/−2 · `feature` → `main`\n\n" % TICKET,
                      md["basic"])
        self.assertIn("### 改了什么\n1. 改动:2 处(orders.ts、app.yml)\n", md["basic"])
        self.assertNotIn("签名变 1", md["basic"])      # 总览只在完整版
        self.assertIn(VERIFY + "\n- 开启配置 `order.cursor.enabled`(`config/app.yml:3`)\n- 执行记录:作者提供:本地跑了 jest\n", md["basic"])
        self.assertIn("### 改了什么\n1. `listOrders` 改收 `cursor` 参数(H1)\n2. 其他:2 处(app.yml、export.ts)\n", md["changes_and_verify"])
        self.assertIn("- 订单列表翻页 → 请求带 `cursor`\n", md["changes_and_verify"])
        # 不认识的语言:写明检查范围,不写「未发现」;资源和工程配置按路径认出来
        self.assertIn("- 接口、迁移、签名:Swift 文件不在自动识别范围,未检查\n", md["swift_unchecked"])
        self.assertIn("- 接口、迁移、签名:已检查 3 个文件,没有认出;另有 3 个 Swift、Objective-C 文件不在自动识别范围,未检查\n",
                      md["swift_partial"])
        self.assertIn("- 配置与依赖:修改工程配置 `App/Info.plist`;依赖变化 `Podfile`\n- 资源:修改资源 `App/zh-Hans.lproj`(1 个文件);"
                      "新增资源 `App/Assets.xcassets`(2 个文件)\n", md["swift_unchecked"])
        self.assertIn("1. `XViewController` 右上角加 `rightBarButtonItem`(H1、H2)\n2. 新增图标 `icon_history_v2`(H5)\n"
                      "3. 其他:2 处(YView.swift、Info.plist)\n", md["swift_unchecked"])     # 测试代码块不进「其他」
        self.assertIn(VERIFY + "\n- <功能A> 页 → 右上角出现历史按钮\n\n", md["swift_unchecked"])
        self.assertNotIn("怎么验证", md["swift_partial"])
        self.assertNotIn("怎么验证", md["no_verify_no_tested"])
        for name, text in md.items():
            with self.subTest(scenario=name):
                self.assertTrue(text.startswith("**") and text.endswith(OWNER), text)
                self.assertLessEqual(len(text.replace(OWNER, "").strip().splitlines()), 30)
                self.assertEqual("- 执行记录:" in text, bool(scenarios()[name].author.get("tested")))
                for word in ("已实现", "已覆盖", "无风险", "建议", "无影响", "预期通过", "不下结论", "阅读顺序", "可跳过", "需注意", "审查量",
                             "可能值得重点看", "可能相关的测试文件", "需求文档:未提供", "> 只按名字", "<!--", "crosscheck", "在本地生成",
                             "未发现", "未验证", "没有认出路由", "/work/shop", "回滚方式", "(第 "):
                    self.assertNotIn(word, text)
                if "### 需求对照" not in text:
                    self.assertNotIn("未提供(需求材料", text)
            with self.subTest(scenario=name, page="html"):
                page = Path(GOLDEN, "%s.html" % name).read_text(encoding="utf-8")
                for word in ("未发现", "未验证", "没有认出路由", "/Users/", "回滚方式"):
                    self.assertNotIn(word, page)


class Sections(Templates):
    """各段的规则:改了什么的分组和「其他」、影响面分层排序和检查范围,验证段每组一行。"""

    def test_layers_are_ordered_and_capped(self):
        c = Case()
        c.facts["symbols"] = [sym("addOne", "added", 1), sym("sigNoUse", "signature_changed", 2, inside=1),
                              sym("dropTwo", "removed", 3), sym("sigUsed", "signature_changed", 4, outside=2)]
        c.facts["attention"] = []
        c.facts["entrypoints"] = entrypoints(changes=[entry("route", "POST /a", "added"), entry("route", "GET /b", "removed", line=None)])
        md = self.md_of(c)
        self.assertNotIn("签名变 2", md)      # md 里没有总览行,网页版保留
        self.assertIn("删除 2 · 接口 2 · 函数 4(签名变 2)", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        self.assertIn("- 接口:删除路由 `GET /b`(`api/routes.ts`);新增路由 `POST /a`(`api/routes.ts:4`)\n", md)
        self.assertIn("- 内部函数:删除 `dropTwo`(`api/f3.ts`);签名变化 `sigUsed`(`api/f4.ts:3`,diff 外 2 处调用) 另 2 项\n", md)
        # 层序:接口 > 数据库与迁移 > 配置与依赖 > 资源 > 内部函数,空层不显示
        self.assertLess(md.index("- 接口:"), md.index("- 内部函数:"))
        self.assertNotIn("- 数据库与迁移", md)
        self.assertNotIn("- 配置与依赖", md)
        self.assertNotIn("- 资源", md)

    def test_layer_order_across_all_four(self):
        md = Path(GOLDEN, "rich_layers.md").read_text(encoding="utf-8")
        heads = [m.group(1) for m in re.finditer(r"^- (接口|数据库与迁移|配置与依赖|资源|内部函数):", md, re.M)]
        self.assertEqual(heads, ["接口", "数据库与迁移", "配置与依赖", "内部函数"])
        self.assertNotIn("签名变 2", md)
        self.assertIn("删除 4 · 迁移 2 · 接口 3 · 配置 4", Path(GOLDEN, "rich_layers.html").read_text(encoding="utf-8"))
        self.assertIn("- 数据库与迁移:删除迁移 `db/001.sql`;新增迁移 `db/002.sql`\n", md)

    def test_scope_says_what_was_checked_never_just_nothing_found(self):
        """没认出变化时写检查了什么、哪些没检查,不写「未发现」「无影响」;什么都没有的层整行不出现。"""
        c = Case()
        c.facts.update(symbols=[sym("total", "body_only", 1, tests=[])], attention=[], entrypoints=entrypoints())
        md = self.md_of(c)
        self.assertIn("### 影响面\n- 检查范围:2 个文件按接口、迁移、配置、签名检查过,没有认出这几类变化\n"
                      "- 未覆盖:另有 1 个函数只改了实现,行为变化不在此列\n", md)
        c.facts["coverage"] = {"checked": 0, "unchecked": [{"lang": "Swift", "files": 4}]}
        md = self.md_of(c, "swift")
        self.assertIn("- 接口、迁移、签名:Swift 文件不在自动识别范围,未检查\n", md)
        c.facts["coverage"] = {"checked": 2, "unchecked": [{"lang": "Swift", "files": 4}]}
        c.facts["symbols"] = [sym("addOne", "added", 1)]
        md = self.md_of(c, "partial")
        self.assertIn("- 接口、迁移、签名:已检查 2 个文件;另有 4 个 Swift 文件不在自动识别范围,未检查\n", md)   # 认出了东西就不写「没有认出」
        # 能识别的都认出了东西:不写检查范围
        self.assertNotIn("检查范围", Path(GOLDEN, "basic.md").read_text(encoding="utf-8"))
        # 没有事实可写:整段不出现
        c.facts.update(symbols=[], coverage={"checked": 0, "unchecked": []})
        md = self.md_of(c, "none")
        self.assertNotIn("### 影响面", md)
        for text in (md, Path(self.out, "describe.html").read_text(encoding="utf-8")):
            self.assertNotIn("无影响", text)
            self.assertNotIn("未发现", text)

    def test_resources_and_project_config_by_path(self):
        """按路径认的工程配置算在「配置与依赖」,资源另起一行和它并列;只有资源时只有资源这一行。"""
        c = Case()
        swift(c)
        c.facts["attention"] = [a for a in c.facts["attention"] if a["type"] == "resource"]
        md = self.md_of(c)
        self.assertIn("- 资源:修改资源 `App/zh-Hans.lproj`(1 个文件);新增资源 `App/Assets.xcassets`(2 个文件)\n", md)
        self.assertNotIn("配置与依赖", md)
        self.assertNotIn("资源 2", md)
        self.assertIn("资源 2", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        self.assertNotIn("配置键", md)

    def test_other_group_lists_every_unreferenced_non_test_hunk(self):
        c = Case()
        c.facts["hunks"] += [hunk(3, "src/a.ts"), hunk(4, "src/b.ts"), hunk(5, "src/c.ts"), hunk(6, "src/d.ts"), hunk(7, "t/a.test.ts", kind="test")]
        c.analysis = {"changes": [change("改了 `listOrders`", ["H1", "H7"], ["listOrders"])]}
        md = self.md_of(c)
        self.assertIn("1. 改了 `listOrders`(H1、H7)\n2. 其他:5 处(app.yml、a.ts、b.ts 另 2 个文件)\n", md)
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
        self.assertEqual(section.strip().splitlines(), ["- 开启配置 `order.cursor.enabled`(`config/app.yml:3`)", "- 执行记录:作者提供:本地跑了 jest"])

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

    def test_verify_one_line_per_group_and_at_most_five(self):
        """脚本认出的入口在前,AI 写的按组一行在后;超过 5 行时同类入口合成一行,还多就截掉。"""
        c = Case()
        c.facts["entrypoints"] = entrypoints(triggers=[entry("route", "GET /r%d" % i, line=i, handler="h%d" % i) for i in range(1, 4)] +
                                             [entry("route", "GET /dup", line=1)])      # 和第一条同一 文件:行,去掉
        c.analysis = {"changes": [change("改 `listOrders` %d" % i, ["H1"], ["listOrders"]) for i in range(1, 5)],
                      "verify": [verify(i, "入口 %d → 看到 `listOrders`" % i, ["H1"], ["listOrders"]) for i in range(1, 5)]}
        c.author = {}
        out, md, page = self.run_note(self.build(c, "many"))
        lines = md.split(VERIFY + "\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual(lines, ["- 请求 `GET /r1`、`GET /r2` 另 1 个(`api/routes.ts:1` 等)",
                                 "- 入口 1 → 看到 `listOrders`", "- 入口 2 → 看到 `listOrders`",
                                 "- 入口 3 → 看到 `listOrders`", "- 入口 4 → 看到 `listOrders`"])
        self.assertNotIn("GET /dup", md)
        self.assertIn("NOTE: 验证步骤由 AI 依据代码起草,贴之前请核对", out)
        self.assertIn("<td>请求 <code>GET /r3</code> → 进入 <code>h3</code></td>", page)   # 完整版逐条列出
        self.assertEqual(page.count("AI 起草,请核对"), 4)
        # 不多时逐条写,带处理函数
        c.analysis = {}
        md = self.md_of(c, "few")
        self.assertIn("- 请求 `GET /r1` → 进入 `h1`(`api/routes.ts:1`)\n- 请求 `GET /r2` → 进入 `h2`(`api/routes.ts:2`)\n", md)

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
        self.assertEqual(md.count("未覆盖"), 1)

    def test_requirements_absent_without_criteria(self):
        c = Case()
        c.analysis = {"requirements": [{"criterion": "编的一条", "hunks": ["H1"]}]}
        md = self.md_of(c)
        self.assertNotIn("需求对照", md)
        self.assertNotIn("需求对照", Path(self.out, "describe.html").read_text(encoding="utf-8"))

    def test_trim_order(self):
        """超过约 30 行时按 其他组 → 资源明细汇总 → 需求对照汇总 → 验证里多余的行 → 空行 的顺序多裁一项;改了什么的分组不裁。"""
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        self.assertEqual(describe.TRIM, ["other", "resources", "requirements", "verify", "blank"])
        self.assertEqual(describe.MAX_LINES, 30)
        c = scenarios()["rich_trimmed_with_focus_and_criteria"]
        c.facts["attention"].append(att("resource", "added", "App/Assets.xcassets", "App/Assets.xcassets", files=2))
        rd = self.build(c, "trim")
        with mock.patch.dict(os.environ, {"HOME": os.path.dirname(self.home)}):
            note = describe.Note(rd, self.out)
            f = note.facts_once()
            template = describe.read_template("describe.md")
            steps = []
            for i in range(len(describe.TRIM) + 1):
                text = describe.render(template, note.md_ctx(f, describe.TRIM[:i]))
                steps.append(describe.drop_blanks(text) if "blank" in describe.TRIM[:i] else text)
            final = note.markdown()
        gone = {"other": "9. 其他:", "resources": "- 资源:新增资源", "requirements": "- 条件 2"}
        for i, step in enumerate(describe.TRIM):
            if step in gone:
                self.assertIn(gone[step], steps[i], step)
                self.assertNotIn(gone[step], steps[i + 1], step)

        def verify_count(text):      # 验证段里的行数(不含作者给的执行记录)
            return len([x for x in text.split(VERIFY + "\n")[1].split("\n\n")[0].splitlines() if not x.startswith("- 执行记录")])
        self.assertGreater(verify_count(steps[3]), describe.MAX_VERIFY_CUT)
        self.assertEqual(verify_count(steps[4]), describe.MAX_VERIFY_CUT)
        self.assertIn("### 改了什么(另有 7 处未归组,见完整版)\n", steps[1])
        self.assertIn("- 资源:1 处(见完整版)\n", steps[2])
        self.assertIn("- 6 条验收标准,1 条标出了对应改动,逐条见完整版", steps[3])
        self.assertGreater(steps[4].count("\n\n"), steps[5].count("\n\n"))
        self.assertTrue(steps[5].endswith(OWNER))
        fit = next(s for s in steps if describe.body_lines(s) <= 30)
        self.assertEqual(final, fit)
        self.assertGreater(describe.body_lines(steps[0]), 30)
        # 改了什么的分组始终在
        for s in steps:
            self.assertIn("8. 改动 8 的 `listOrders`(H1)", s)
            self.assertNotIn("删除 4 · 迁移 2", s)

    def test_typical_description_is_short(self):
        md = Path(GOLDEN, "basic.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(md.replace(OWNER, "").strip().splitlines()), 20)

    def test_template_edit_changes_the_output(self):
        """团队改 templates/describe.md、describe.html 就能改描述的结构;脚本读的是模板,不是写死的。"""
        copy = str(self.install_plugin() / "skills" / "describe")
        for name, old, new in (("describe.md", "### 影响面", "### 改动范围"), ("describe.html", "<h2>影响面</h2>", "<h2>改动范围</h2>")):
            p = Path(copy, "templates", name)
            self.assertIn(old, p.read_text(encoding="utf-8"))
            p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        rd = self.build(Case())
        _, md, page = self.run_note(rd, os.path.join(copy, "scripts"))
        self.assertIn("### 改动范围", md)
        self.assertNotIn("### 影响面", md)
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
