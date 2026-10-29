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
NOTHING = "未发现接口、迁移、配置、签名变化"
VERIFY = "### 怎么验证(由代码推断,未执行)"
UNTESTED = "- 执行记录:未验证(无执行记录)"


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
        "entrypoints": entrypoints(triggers=[entry("config", "order.cursor.enabled", file="config/app.yml", line=3, value="true", on=True)]),
        "method": "按名字在本仓库文本匹配(git grep -w),只看签名变化", "limits": [],
    }


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
    def rich_trimmed_with_focus_and_criteria(c):
        rich(c)
        c.criteria = ["条件 %d" % i for i in range(1, 7)]
        c.analysis = {"summary": {"text": "x"}, "requirements": [{"criterion": "条件 1(来自 需求单)", "hunks": ["H1"]}]}
        c.author["focus"] = [{"where": "api/orders.ts:%d" % i, "ask": "问题%d" % i} for i in range(1, 4)]

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
                self.assertIn("### CR负责人:\n\n@xx\n\n", md)
                self.assertIn("<section><h2>CR负责人:</h2><p>@xx</p></section>", page)
                notes = "".join(x + "\n" for x in out.splitlines() if x.startswith("NOTE:"))
                self.compare(name, {k: normalize(v, self.version, c.meta["head_sha"]) for k, v in (("md", md), ("html", page), ("notes", notes))})

    def test_golden_files_are_exactly_the_scenarios(self):
        names = {"%s.%s" % (n, ext) for n in scenarios() for ext in ("md", "html", "notes")}
        self.assertEqual(set(os.listdir(GOLDEN)), names)

    def test_golden_scenarios_cover_the_branches(self):
        """场景覆盖了已合入、PR/MR 信息没读到、作者点名的重点、需求单各种状态、裁剪等分支。"""
        md = {name: Path(GOLDEN, "%s.md" % name).read_text(encoding="utf-8") for name in scenarios()}
        self.assertIn("状态:已合入 main,描述已无法影响合入", md["merged"])
        self.assertIn("PR/MR 信息:没有读到(tools.code_host:glab 未登录)", md["code_host_failed"])
        self.assertIn("### 请重点看\n1. ", md["pr_links_and_author_focus"])
        self.assertNotIn("重点看", md["basic"])
        self.assertIn("需求单:未提供(非交互运行,没有询问)", md["ticket_unasked_non_interactive"])
        self.assertIn("需求单:无需求单:hotfix", md["ticket_none_and_docs"])
        self.assertIn("需求文档:plan.md、api.md", md["ticket_none_and_docs"])
        self.assertIn("需求单:未提供(作者跳过)", md["sources_variants"])
        self.assertIn("内容未读取:没有权限", md["sources_variants"])
        self.assertNotIn("需求文档", md["doc_skipped_is_not_listed"])
        self.assertIn("**分支 feature 的改动**", md["no_summary_no_tested"])
        self.assertIn("- 怎么跑:`npm test`(项目命令,未确认已执行);新增用例「returns next cursor」", md["tests_and_command"])
        self.assertIn("- 预期:新增断言 `expect(res.next).toBeDefined()`(`tests/orders.test.ts:4`)", md["tests_and_command"])
        self.assertIn("### 需求对照\n- 接口支持 cursor <参数>(来自 需求单):`api/orders.ts`\n- 旧参数继续可用(来自 需求单):没有标出对应的改动\n",
                      md["criteria_mapped"])
        self.assertIn("- 预期:验收标准「接口支持 cursor <参数>(来自 需求单)」「旧参数继续可用(来自 需求单)」", md["criteria_mapped"])
        self.assertIn("**订单列表改为游标分页**\n规模:2 个文件 +5/−2 · 分支:feature → main · 需求单:%s 「订单游标分页」\n" % TICKET, md["basic"])
        self.assertIn("### 影响面\n配置 1 · 函数 1(签名变 1)\n", md["basic"])
        self.assertIn("- 触发:开启配置 `order.cursor.enabled`(`config/app.yml:3`);`listOrders` 的 diff 外调用方 `report/daily.ts:1`、`report/daily.ts:2`",
                      md["basic"])
        self.assertIn("- 执行记录:作者提供:本地跑了 jest", md["basic"])
        self.assertIn(UNTESTED, md["no_summary_no_tested"])
        for name, text in md.items():
            with self.subTest(scenario=name):
                self.assertTrue(text.startswith("<!-- crosscheck:begin -->\n") and text.endswith("<!-- crosscheck:end -->\n"))
                self.assertLessEqual(len(text.replace("### CR负责人:\n\n@xx\n\n", "").strip().splitlines()) - 2, 20)
                self.assertIn("### 影响面\n", text)
                self.assertIn(VERIFY, text)
                self.assertRegex(text, r"\n- 执行记录:")
                for word in ("已实现", "已覆盖", "无风险", "建议", "无影响", "预期通过", "不下结论", "阅读顺序", "可跳过", "需注意", "审查量",
                             "可能值得重点看", "可能相关的测试文件", "需求文档:未提供", "> 只按名字"):
                    self.assertNotIn(word, text)
                if "### 需求对照" not in text:
                    self.assertNotIn("未提供(需求材料", text)


class Sections(Templates):
    """各段的规则:影响面分层排序、空层文案、未覆盖、回滚,验证段只用脚本的事实。"""

    def test_layers_are_ordered_and_capped(self):
        c = Case()
        c.facts["symbols"] = [sym("addOne", "added", 1), sym("sigNoUse", "signature_changed", 2, inside=1),
                              sym("dropTwo", "removed", 3), sym("sigUsed", "signature_changed", 4, outside=2)]
        c.facts["attention"] = []
        c.facts["entrypoints"] = entrypoints(changes=[entry("route", "POST /a", "added"), entry("route", "GET /b", "removed", line=None)])
        md = self.md_of(c)
        self.assertIn("\n删除 2 · 接口 2 · 函数 4(签名变 2)\n", md)
        self.assertIn("- 接口:删除路由 `GET /b`(`api/routes.ts`);新增路由 `POST /a`(`api/routes.ts:4`)\n", md)
        self.assertIn("- 内部函数:删除 `dropTwo`(`api/f3.ts`);签名变化 `sigUsed`(`api/f4.ts:3`,diff 外 2 处调用) 另 2 项\n", md)
        # 层序:接口 > 数据库与迁移 > 配置 > 内部函数,空层不显示
        self.assertLess(md.index("- 接口:"), md.index("- 内部函数:"))
        self.assertNotIn("- 数据库与迁移", md)
        self.assertNotIn("- 配置、环境变量与依赖", md)

    def test_layer_order_across_all_four(self):
        md = Path(GOLDEN, "rich_layers.md").read_text(encoding="utf-8")
        heads = [m.group(1) for m in re.finditer(r"^- (接口|数据库与迁移|配置、环境变量与依赖|内部函数):", md, re.M)]
        self.assertEqual(heads, ["接口", "数据库与迁移", "配置、环境变量与依赖", "内部函数"])
        self.assertIn("\n删除 4 · 迁移 2 · 接口 3 · 配置 4 · 函数 4(签名变 2)\n", md)
        self.assertIn("- 数据库与迁移:删除迁移 `db/001.sql`;新增迁移 `db/002.sql`\n", md)

    def test_empty_layers_say_nothing_found_never_no_impact(self):
        c = Case()
        c.facts.update(symbols=[sym("total", "body_only", 1, tests=[])], attention=[], entrypoints=entrypoints())
        md = self.md_of(c)
        self.assertIn("### 影响面\n%s\n- 未覆盖:另有 1 个函数只改了实现,行为变化不在此列\n" % NOTHING, md)
        c.facts["symbols"] = []
        md = self.md_of(c, "none")
        self.assertIn("### 影响面\n%s\n\n" % NOTHING, md)
        self.assertNotIn("未覆盖", md)
        for text in (md, Path(self.out, "describe.html").read_text(encoding="utf-8")):
            self.assertNotIn("无影响", text)

    def test_rollback_line_only_for_migration_removal_or_dependency(self):
        yes = {"迁移": dict(attention=[att("migration", "added", "db/1.sql", "db/1.sql")]),
               "删除配置键": dict(attention=[att("config_key", "removed", "a.b", "config/app.yml", 2)]),
               "删除配置文件": dict(attention=[att("config_file", "removed", "config/x.yml", "config/x.yml")]),
               "依赖": dict(attention=[att("dependency", "modified", "go.mod", "go.mod", parts=[])]),
               "删除接口": dict(entrypoints=entrypoints(changes=[entry("route", "GET /x", "removed", line=None)])),
               "删除公开函数": dict(symbols=[sym("gone", "removed", 1)])}
        no = {"新增路由": dict(entrypoints=entrypoints(changes=[entry("route", "GET /x", "added")])),
              "新增配置键": dict(attention=[att("config_key", "added", "a.b", "config/app.yml", 2)]),
              "签名变化": {},
              "删除私有函数": dict(symbols=[sym("gone", "removed", 1, exported=False)])}
        for want, cases in ((True, yes), (False, no)):
            for label, over in cases.items():
                with self.subTest(case=label):
                    c = Case()
                    c.facts["attention"] = []
                    c.facts.update(over)
                    md = self.md_of(c, label)
                    self.assertEqual("- 回滚方式:待补充\n" in md, want, md)

    def test_verify_section_comes_only_from_the_script(self):
        """analyst 回复里多写的验证内容不会出现:验证段只用 describe-facts.json 的事实。"""
        c = Case()
        c.analysis = {"summary": {"text": "起草"}, "verify": "运行 make e2e,预期全部通过", "trigger": "手工点一下"}
        md = self.md_of(c)
        section = md.split(VERIFY)[1].split("\n\n")[0]
        self.assertNotIn("make e2e", md)
        self.assertNotIn("手工点一下", md)
        self.assertEqual([x.split(":")[0] for x in section.strip().splitlines()], ["- 触发", "- 执行记录"])

    def test_no_trigger_is_stated(self):
        c = Case()
        c.facts.update(symbols=[], entrypoints=entrypoints())
        md = self.md_of(c)
        self.assertIn("- 触发:没有认出路由、命令行、配置开关、定时任务或事件入口,也没有找到 diff 外的调用方\n", md)

    def test_expected_only_with_a_source(self):
        c = Case()
        md = self.md_of(c)
        self.assertNotIn("- 预期", md)
        c.facts["entrypoints"]["cases"] = [{"name": "returns cursor", "file": "tests/a.test.ts", "line": 2}]
        md = self.md_of(c, "cases")
        self.assertNotIn("- 预期", md)          # 只有用例名时名字写在「怎么跑」里
        c.facts["entrypoints"]["assertions"] = [{"text": "expect(a).toBe(1)", "file": "tests/a.test.ts", "line": 3}]
        self.assertIn("- 预期:新增断言 `expect(a).toBe(1)`(`tests/a.test.ts:3`)\n", self.md_of(c, "asserts"))
        c.criteria = ["返回 next_cursor"]
        md = self.md_of(c, "criteria")
        self.assertIn("- 预期:验收标准「返回 next_cursor(来自 需求单)」\n", md)
        self.assertNotIn("新增断言", md)

    def test_execution_record_quotes_tested_or_says_unverified(self):
        c = Case()
        c.author = {}
        md = self.md_of(c)
        self.assertIn(UNTESTED + "\n", md)
        self.assertNotIn("预期通过", md)
        c.author = {"tested": "pnpm test:退出码 0"}
        md = self.md_of(c, "tested")
        self.assertIn("- 执行记录:作者提供:pnpm test:退出码 0\n", md)
        self.assertNotIn("未验证", md)

    def test_test_line_names_changed_functions_missing_from_the_changed_tests(self):
        """验证段只在能指出具体事实时才有测试一行:新增或签名变化的公开函数在改动的测试里没有出现;影响面里的「未覆盖」只有一处。"""
        c = Case()
        changed = [{"file": "tests/c.test.ts", "changed": True}]
        c.facts["symbols"] = [sym("a", "added", 1, tests=[]), sym("b", "signature_changed", 2, tests=[{"file": "tests/b.test.ts", "changed": False}]),
                              sym("c", "signature_changed", 3, tests=changed), sym("d", "added", 4, tests=None),
                              sym("e", "added", 5, tests=[], exported=False), sym("f", "body_only", 6, tests=[])]
        md = self.md_of(c)
        self.assertIn("\n- 测试:2 个新增/签名变化的函数在改动的测试里没有出现:`a`、`b`\n", md)
        self.assertIn("\n- 未覆盖:另有 1 个函数只改了实现,行为变化不在此列\n", md)
        self.assertEqual(md.count("未覆盖"), 1)
        self.assertNotIn("没有对应测试用例", md)
        self.assertIn("测试:2 个新增/签名变化的函数在改动的测试里没有出现", Path(self.out, "describe.html").read_text(encoding="utf-8"))
        # 算不出(没搜过、都在改动的测试里出现、只有只改实现的)就省略
        c.facts["symbols"] = [sym("c", "signature_changed", 3, tests=changed), sym("d", "added", 4, tests=None), sym("f", "body_only", 6, tests=[])]
        md = self.md_of(c, "none")
        self.assertNotIn("- 测试:", md)
        self.assertNotIn("没有出现", Path(self.out, "describe.html").read_text(encoding="utf-8"))

    def test_callers_belong_to_the_function_that_changed(self):
        """diff 外调用方按函数归属:只给签名变化、新增的函数和入口处理函数;只改了实现的函数的调用方不挂在别的入口下面。"""
        c = Case()
        c.facts["symbols"] = [sym("sigFn", "signature_changed", 1, outside=1, tests=[]), sym("bodyFn", "body_only", 2, outside=1),
                              sym("bodyHandler", "body_only", 3, outside=1)]
        c.facts["entrypoints"] = entrypoints(triggers=[entry("route", "GET /a", file="api/routes.ts", line=4)])
        md = self.md_of(c)
        page = Path(self.out, "describe.html").read_text(encoding="utf-8")
        self.assertIn("- 触发:请求 `GET /a`(`api/routes.ts:4`);`sigFn` 的 diff 外调用方 `use/c1.ts:10`\n", md)
        for text in (md, page):
            self.assertNotIn("use/c2.ts", text)      # 只改了实现的 bodyFn
            self.assertNotIn("use/c3.ts", text)      # bodyHandler 不是任何入口的处理函数
        self.assertIn("<td><code>sigFn</code></td>", page)
        # 入口的处理函数本身,即使只改了实现,它的调用方也算
        c.facts["entrypoints"] = entrypoints(triggers=[entry("route", "GET /a", file="api/routes.ts", line=4, handler="bodyHandler")])
        md = self.md_of(c, "handler")
        self.assertIn(";`sigFn` 的 diff 外调用方 `use/c1.ts:10`;`bodyHandler` 的 diff 外调用方 `use/c3.ts:10`\n", md)
        self.assertNotIn("use/c2.ts", md)
        # 最多给两个函数,每个函数最多两处,多的写个数
        c.facts["symbols"] = [sym("f%d" % i, "added", i, outside=3) for i in range(1, 4)]
        c.facts["entrypoints"] = entrypoints()
        md = self.md_of(c, "capped")
        self.assertIn("`f1` 的 diff 外调用方 `use/c1.ts:10`、`use/c1.ts:11` 等 3 处;`f2` 的 diff 外调用方", md)
        self.assertNotIn("`f3` 的 diff 外调用方", md)
        # 同名歧义或多个同名符号时不能确认归属,省略调用方
        c.facts["symbols"] = [sym("same", "signature_changed", 1, outside=1, ambiguous=True),
                              sym("dup", "added", 2, outside=1), sym("dup", "added", 3, outside=1)]
        md = self.md_of(c, "ambiguous")
        self.assertNotIn("的 diff 外调用方", md)

    def test_cases_are_capped_at_five(self):
        c = Case()
        c.facts["entrypoints"]["cases"] = [{"name": "c%d" % i, "file": "tests/a.test.ts", "line": i} for i in range(1, 8)]
        self.assertIn("新增用例「c1」、「c2」、「c3」、「c4」、「c5」 另 2 个\n", self.md_of(c))

    def test_requirements_absent_without_criteria(self):
        c = Case()
        c.analysis = {"requirements": [{"criterion": "编的一条", "hunks": ["H1"]}]}
        md = self.md_of(c)
        self.assertNotIn("需求对照", md)
        self.assertNotIn("需求对照", Path(self.out, "describe.html").read_text(encoding="utf-8"))

    def test_trim_order_and_hard_limit(self):
        """超过 20 行时按 需求对照汇总 → 内部函数层 → 用例名 → 命令 → 空行 → 验证段测试一行 → 预期 的顺序多裁一项。"""
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe
        self.assertEqual(describe.TRIM, ["requirements", "func", "cases", "command", "blank", "untested", "expect"])
        c = scenarios()["rich_trimmed_with_focus_and_criteria"]
        rd = self.build(c, "trim")
        with mock.patch.dict(os.environ, {"CROSSCHECK_HOME": self.home}):
            note = describe.Note(rd, self.out)
            f = note.facts_once()
            template = describe.read_template("describe.md")
            steps = []
            for i in range(len(describe.TRIM) + 1):
                text = describe.render(template, note.md_ctx(f, describe.TRIM[:i]))
                steps.append(describe.drop_blanks(text) if "blank" in describe.TRIM[:i] else text)
            final = note.markdown()
        gone = {"requirements": "- 条件 2", "func": "- 内部函数:", "cases": "新增用例", "command": "`npm test`",
                "untested": "- 测试:", "expect": "- 预期:"}
        for i, step in enumerate(describe.TRIM):
            if step in gone:
                self.assertIn(gone[step], steps[i], step)
                self.assertNotIn(gone[step], steps[i + 1], step)
        self.assertIn("- 6 条验收标准,1 条标出了对应改动,逐条见完整版", steps[1])
        self.assertGreater(steps[4].count("\n\n"), steps[5].count("\n\n"))
        fit = next(s for s in steps if describe.body_lines(s) <= 20)
        self.assertEqual(final, fit)
        self.assertGreater(describe.body_lines(steps[0]), 20)
        # 总览行和执行记录始终在
        for s in steps:
            self.assertIn("删除 4 · 迁移 2", s)
            self.assertIn("- 执行记录:", s)

    def test_typical_description_is_short(self):
        md = Path(GOLDEN, "basic.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(md.replace("### CR负责人:\n\n@xx\n\n", "").strip().splitlines()) - 2, 15)

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
                               (good.replace("{{/expect}}\n", ""), "没有对应的 {{/expect}}"),
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
            listed = set(re.findall(r"[a-z_]+", re.search(r"可用的名字:([^。]*)。|占位符都是纯文本[^:]*:([^。]*)。", text).group(0).split(":", 1)[1]))
            used = {m.group(2) for m in describe.TAG.finditer(text) if m.group(1) != "!"}
            self.assertEqual(used, listed, name)
