"""describe_facts.py:PR/MR 描述的事实部分(调用方和相关测试、影响面里的迁移配置依赖、入口),以及它不改 review 的 analysis-facts.json。"""
import json
import os

import fixture


class Facts(fixture.Base):
    def facts(self, repo, *args):
        r = self.run_describe("collect.sh", *args, "--non-interactive", cwd=repo)
        rd = self.review_dir(r.stdout)
        self.assertIn("describe_facts: ", r.stdout)
        with open(os.path.join(rd, "describe-facts.json"), encoding="utf-8") as f:
            return json.load(f), rd

    def feature(self, name, base, change, *args):
        repo = self.make_repo(name, base)
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, change, "改")
        self.git(repo, "switch", "-q", "main")
        return repo, self.facts(repo, "feature", *args)[0]

    def sym(self, data, name):
        return next(s for s in data["symbols"] if s["name"] == name)


class Symbols(Facts):
    def test_ts_change_kinds_callers_and_related_tests(self):
        repo, data = self.feature("ts", {
            "src/orders.ts": "export function listOrders(page: number) {\n  return page\n}\n"
                             "export function totalCount(a: number) {\n  return a\n}\n"
                             "export function dropMe(x: number) {\n  return x\n}\n"
                             "function helper(a: number) {\n  return a\n}\n",
            "src/report.ts": "import { listOrders, dropMe } from './orders'\nlistOrders(1)\ndropMe(2)\n",
            "src/orders.test.ts": "listOrders(1)\n",
        }, {
            "src/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts\n}\n"
                             "export function totalCount(a: number) {\n  return a + 1\n}\n"
                             "function helper(a: number) {\n  return a * 2\n}\n"
                             "export function freshOne() {\n  return 1\n}\n",
        })
        s = self.sym(data, "listOrders")
        self.assertEqual(s["change"], "signature_changed")
        self.assertEqual([(c["file"], c["line"]) for c in s["callers"]], [("src/report.ts", 2)])   # 第 1 行是 import,不是调用
        self.assertEqual(s["tests"], [{"file": "src/orders.test.ts", "changed": False}])
        self.assertEqual(self.sym(data, "totalCount")["change"], "body_only")
        self.assertEqual(self.sym(data, "freshOne")["change"], "added")
        d = self.sym(data, "dropMe")
        self.assertEqual(d["change"], "removed")
        h = self.sym(data, "helper")
        self.assertEqual((h["exported"], h["callers"]), (False, []))
        self.assertNotIn("skippable", data)
        self.assertNotIn("effort", data)

    def test_import_and_reexport_lines_are_not_callers(self):
        repo, data = self.feature("imports", {
            "src/a.ts": "export function total(a: number) {\n  return a\n}\n",
            "src/b.ts": "import {\n  total,\n} from './a'\nconst { total: t2 } = require('./a')\nexport { total } from './a'\n"
                        "export { total }\nconst n = total(1)\n",
            "src/c.py": "from a import total\n",
        }, {"src/a.ts": "export function total(a: number, b: number) {\n  return a\n}\n"})
        self.assertEqual([(c["file"], c["line"]) for c in self.sym(data, "total")["callers"]], [("src/b.ts", 7)])

    def test_changed_test_files_are_hunks_and_marked_changed(self):
        repo = self.make_repo("indiff", {
            "src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n",
            "src/b.ts": "listOrders(1)\n",
            "src/a.test.ts": "listOrders(1)\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function listOrders(p: number, q: number) {\n  return p\n}\n",
                           "src/a.test.ts": "listOrders(1, 2)\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, rd = self.facts(repo, "feature")
        kinds = {h["file"]: h["kind"] for h in data["hunks"]}
        self.assertEqual(kinds, {"src/a.ts": "other", "src/a.test.ts": "test"})
        self.assertEqual(self.sym(data, "listOrders")["tests"], [{"file": "src/a.test.ts", "changed": True}])
        self.assertEqual(data["scope"]["files"], 2)
        # review 自己的事实里测试文件不编代码块号,也没有 tests
        review = self.load(os.path.join(rd, "analysis-facts.json"))
        self.assertEqual([h["file"] for h in review["hunks"]], ["src/a.ts"])
        self.assertTrue(all("tests" not in s for s in review["symbols"]))

    def test_body_only_symbols_list_callers_and_tests(self):
        repo, data = self.feature("body", {
            "src/a.ts": "export function total(a: number) {\n  return a\n}\n",
            "src/b.ts": "total(1)\n",
            "src/a.test.ts": "total(1)\n",
        }, {"src/a.ts": "export function total(a: number) {\n  return a + 1\n}\n"})
        s = self.sym(data, "total")
        self.assertEqual(s["change"], "body_only")
        self.assertEqual([c["file"] for c in s["callers"]], ["src/b.ts"])   # 只改实现的也查调用方,验证段要用
        self.assertEqual(s["tests"], [{"file": "src/a.test.ts", "changed": False}])


class AttentionAndSkip(Facts):
    def test_attention_kinds(self):
        _, data = self.feature("attn", {
            "config/app.yml": "order:\n  page:\n    size: 20\n  legacy: true\n",
            "src/settings.properties": "a.b=1\n",
            "package.json": json.dumps({"dependencies": {"left-pad": "1.0.0", "lodash": "4.0.0"}}, indent=1) + "\n",
            "src/env.ts": "export const x = 1\n",
            "src/main.go": "package main\n\nfunc main() {\n}\n",
        }, {
            "config/app.yml": "order:\n  page:\n    size: 50\n  cursor:\n    enabled: true\n",
            "src/settings.properties": "a.b=1\nfeature.flag=on\n",
            "package.json": json.dumps({"dependencies": {"lodash": "4.17.0", "axios": "1.6.0"}}, indent=1) + "\n",
            "db/migrations/002_add_cursor.sql": "ALTER TABLE orders ADD cursor text;\n",
            "src/env.ts": "export const x = process.env.ORDER_API_URL\n",
            "src/main.go": "package main\n\nimport \"os\"\n\nfunc main() {\n\t_ = os.Getenv(\"CURSOR_SECRET\")\n}\n",
            "package-lock.json": "{}\n",
        })
        att = data["attention"]
        details = [a["detail"] for a in att]
        self.assertTrue({"migration", "config_key", "env_var", "dependency"} <= {a["type"] for a in att})
        self.assertIn("新增配置键 order.cursor.enabled", details)
        self.assertIn("删除配置键 order.legacy", details)
        self.assertIn("新增配置键 feature.flag", details)
        self.assertTrue(any("新环境变量 ORDER_API_URL" == d for d in details))
        self.assertTrue(any("新环境变量 CURSOR_SECRET" == d for d in details))
        dep = next(d for d in details if "package.json" in d)
        self.assertIn("新增 axios@1.6.0", dep)
        self.assertIn("删除 left-pad", dep)
        self.assertIn("lodash 4.0.0 → 4.17.0", dep)
        self.assertTrue(any(a["type"] == "dependency" and a["file"] == "package-lock.json" for a in att))
        change = {a["detail"]: a["change"] for a in att}
        self.assertEqual(change["新增配置键 order.cursor.enabled"], "added")
        self.assertEqual(change["删除配置键 order.legacy"], "removed")
        self.assertEqual(change["新增迁移 db/migrations/002_add_cursor.sql"], "added")
        self.assertEqual(change["新环境变量 ORDER_API_URL"], "added")
        self.assertTrue(all(a["type"] in ("migration", "config_key", "config_file", "env_var", "dependency") for a in att))
        self.assertTrue(any(a["type"] == "migration" and "002_add_cursor.sql" in a["detail"] for a in att))
        size = next(s for s in data["symbols"] if s["name"] == "order.page.size")
        self.assertEqual((size["change"], size["kind"]), ("body_only", "config_key"))
        self.assertEqual({h["file"]: h["kind"] for h in data["hunks"]}["config/app.yml"], "config")

    def test_scope_and_method(self):
        _, small = self.feature("small", {"src/a.ts": "export const a = 1\n"}, {"src/a.ts": "export const a = 2\n"})
        self.assertEqual(small["scope"], {"diff": "diff.patch", "files": 1, "added": 1, "deleted": 1})
        self.assertEqual(small["method"], "按名字在本仓库文本匹配(git grep -w),只看签名变化")

    def test_new_config_file_is_one_item_and_other_languages_fall_back(self):
        _, data = self.feature("newcfg", {"bin/run.sh": "#!/bin/sh\nstart_server() {\n  echo a\n}\n"}, {
            "config/new.yml": "a:\n  b: 1\n  c: 2\n",
            "bin/run.sh": "#!/bin/sh\nstart_server() {\n  echo b\n}\nstop_server() {\n  echo c\n}\n",
        })
        cfg = [a for a in data["attention"] if a["file"] == "config/new.yml"]
        self.assertEqual([a["detail"] for a in cfg], ["新增配置文件 config/new.yml(2 个键)"])
        self.assertFalse(any(s["file"] in ("config/new.yml", "bin/run.sh") for s in data["symbols"]))
        self.assertTrue(any("bin/run.sh" in l for l in data["limits"]))

    def test_path_rules_for_resources_project_config_and_dependencies(self):
        """.plist、Podfile、xcassets、.strings 按路径进「资源、配置与依赖」;xcassets 的 Contents.json 不当配置键。"""
        _, data = self.feature("paths", {
            "App/Info.plist": "<plist><dict><key>A</key><string>1</string></dict></plist>\n",
            "Podfile": "pod 'Alamofire'\n",
            "App/XView.swift": "class XView {\n}\n",
        }, {
            "App/Info.plist": "<plist><dict><key>A</key><string>2</string></dict></plist>\n",
            "Podfile": "pod 'Alamofire'\npod 'SnapKit'\n",
            "App/Assets.xcassets/icon_history_v2.imageset/Contents.json": '{\n  "info" : {\n    "version" : 1\n  }\n}\n',
            "App/zh-Hans.lproj/Localizable.strings": '"history" = "历史";\n',
            "App/XView.swift": "class XView {\n  let title = \"历史记录\"\n}\n",
        })
        types = {(a["type"], a["file"]) for a in data["attention"]}
        self.assertIn(("project_config", "App/Info.plist"), types)
        self.assertIn(("dependency", "Podfile"), types)
        res = {a["name"]: a for a in data["attention"] if a["type"] == "resource"}
        self.assertEqual(sorted(res), ["App/Assets.xcassets", "App/zh-Hans.lproj"])
        self.assertFalse(any(s["kind"] == "config_key" for s in data["symbols"]), data["symbols"])
        kinds = {h["file"]: h["kind"] for h in data["hunks"]}
        self.assertEqual(kinds["App/Assets.xcassets/icon_history_v2.imageset/Contents.json"], "resource")
        swift = next(h for h in data["hunks"] if h["file"] == "App/XView.swift")
        self.assertEqual(swift["strings_added"], ["历史记录"])
        self.assertEqual(swift["strings_removed"], [])
        # Swift 不在自动识别范围:记进 unchecked,不算「检查过」
        self.assertEqual(data["coverage"], {"checked": 0, "unchecked": [{"lang": "Swift", "files": 1}]})
        self.assertNotIn("batches", data)

    def test_resource_attention_lists_actual_files_per_container(self):
        """同一个容器里文案和其他资源混着改:attention 记下容器里实际改的文件,影响面据此分类。"""
        res = "app/src/main/res/values-zh"
        _, data = self.feature("mixed_res", {
            res + "/strings.xml": "<resources></resources>\n",
            res + "/colors.xml": "<resources></resources>\n",
        }, {
            res + "/strings.xml": '<resources><string name="a">A</string></resources>\n',
            res + "/colors.xml": '<resources><color name="c">#fff</color></resources>\n',
        })
        item = next(a for a in data["attention"] if a["type"] == "resource")
        self.assertEqual((item["name"], item["files"]), (res, 2))
        self.assertEqual(sorted((x["path"], x["change"]) for x in item["paths"]),
                         [(res + "/colors.xml", "modified"), (res + "/strings.xml", "modified")])

    def test_coverage_counts_checked_files(self):
        _, data = self.feature("cov", {"src/a.ts": "export const a = 1\n", "src/b.swift": "let b = 1\n"},
                               {"src/a.ts": "export const a = 2\n", "src/b.swift": "let b = 2\n"})
        self.assertEqual(data["coverage"], {"checked": 1, "unchecked": [{"lang": "Swift", "files": 1}]})

    def test_batches_split_by_directory_only_when_many_hunks(self):
        import sys
        sys.path.insert(0, fixture.DESCRIBE_SCRIPTS)
        import describe_facts
        few = [{"id": "H%d" % i, "file": "a/b.ts"} for i in range(1, 41)]
        self.assertEqual(describe_facts.batches(few), [])
        many = [{"id": "H%d" % i, "file": ("app/ui/v%d.swift" if i <= 30 else "lib/core/c%d.ts") % i} for i in range(1, 61)]
        b = describe_facts.batches(many)
        self.assertEqual([x["id"] for x in b], ["B1", "B2"])
        self.assertEqual(sum(len(x["hunks"]) for x in b), 60)
        self.assertTrue(all(len(x["hunks"]) <= describe_facts.BATCH_HUNKS for x in b))
        self.assertEqual(b[0]["dirs"], ["app/ui", "lib/core"])
        self.assertEqual(b[1]["dirs"], ["lib/core"])

    def test_bad_input_still_writes_file(self):
        rd = os.path.join(self.tmp, "rd")
        os.makedirs(rd)
        r = self.run_describe("describe_facts.py", rd, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.load(os.path.join(rd, "describe-facts.json"))
        self.assertEqual(data["hunks"], [])
        self.assertTrue(data["limits"])
