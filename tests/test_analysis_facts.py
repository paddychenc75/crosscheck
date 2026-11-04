"""analysis_facts.py:审查用的事实(代码块编号、符号、调用方)。"""
import json
import os

import fixture

SCRIPT = os.path.join(fixture.SCRIPTS, "analysis_facts.py")


class Facts(fixture.Base):
    def facts(self, repo, *args):
        r = self.run_script("collect-diff.sh", *args, cwd=repo)
        rd = self.review_dir(r.stdout)
        p = self.run_py("analysis_facts.py", rd, check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(rd, "analysis-facts.json"), encoding="utf-8") as f:
            return json.load(f), rd

    def feature(self, name, base, change, *args):
        repo = self.make_repo(name, base)
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, change, "改")
        self.git(repo, "switch", "-q", "main")
        return repo, self.facts(repo, "feature", *args)[0]

    def sym(self, data, name):
        return next(s for s in data["symbols"] if s["name"] == name)


class Languages(Facts):
    def test_ts_change_kinds_and_callers(self):
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
                             "export function freshOne(x: string) {\n  return x\n}\n"
                             "function helper(a: number, b: number) {\n  return a\n}\n",
        })
        s = self.sym(data, "listOrders")
        self.assertEqual((s["change"], s["kind"], s["exported"], s["lang"]), ("signature_changed", "function", True, "ts"))
        self.assertIn("page: number", s["old_sig"])
        self.assertIn("cursor", s["new_sig"])
        self.assertEqual([c["file"] for c in s["callers"]], ["src/report.ts", "src/report.ts"])
        self.assertEqual(s["callers_total"], 2)
        self.assertFalse(any(c["file"] == "src/orders.ts" and c["line"] == s["line"] for c in s["callers"]), "不含定义行本身")
        self.assertNotIn("tests", s)
        self.assertEqual(self.sym(data, "totalCount")["change"], "body_only")
        self.assertEqual(self.sym(data, "freshOne")["change"], "added")
        d = self.sym(data, "dropMe")
        self.assertEqual(d["change"], "removed")
        self.assertEqual([c["file"] for c in d["callers"]], ["src/report.ts", "src/report.ts"])
        h = self.sym(data, "helper")
        self.assertEqual((h["exported"], h["callers"]), (False, []))
        for gone in ("attention", "skippable", "effort"):
            self.assertNotIn(gone, data)
        hid = s["hunk"]
        self.assertIn("listOrders", next(x for x in data["hunks"] if x["id"] == hid)["symbols"])

    def test_go_python_java_kotlin(self):
        base = {
            "svc/order.go": "package svc\n\nfunc ListOrders(p int) int {\n\treturn p\n}\n\nfunc internalThing(p int) int {\n\treturn p\n}\n",
            "app/orders.py": "__all__ = ['list_orders']\n\ndef list_orders(page):\n    return page\n\ndef other_func(a):\n    return a\n",
            "src/OrderService.java": "public class OrderService {\n    public List<Order> listOrders(int page) {\n        return null;\n    }\n"
                                     "    private void secretStep(int a) {\n    }\n}\n",
            "src/Orders.kt": "class Orders {\n    fun listOrders(page: Int): Int {\n        return page\n    }\n}\n",
        }
        change = {
            "svc/order.go": "package svc\n\nfunc ListOrders(p int, size int) int {\n\treturn p\n}\n\nfunc internalThing(p string) int {\n\treturn 0\n}\n",
            "app/orders.py": "__all__ = ['list_orders']\n\ndef list_orders(page, size):\n    return page\n\ndef other_func(a, b):\n    return a\n",
            "src/OrderService.java": "public class OrderService {\n    public List<Order> listOrders(int page,\n                                  int size) {\n        return null;\n    }\n"
                                     "    private void secretStep(long a) {\n    }\n}\n",
            "src/Orders.kt": "class Orders {\n    fun listOrders(page: Int, size: Int): Int {\n        return page\n    }\n}\n",
        }
        _, data = self.feature("langs", base, change)
        got = {(s["file"], s["name"]): s for s in data["symbols"]}
        self.assertEqual(got[("svc/order.go", "ListOrders")]["change"], "signature_changed")
        self.assertTrue(got[("svc/order.go", "ListOrders")]["exported"])
        self.assertFalse(got[("svc/order.go", "internalThing")]["exported"])
        self.assertTrue(got[("app/orders.py", "list_orders")]["exported"])
        self.assertFalse(got[("app/orders.py", "other_func")]["exported"], "__all__ 里没有的不算公开")
        j = got[("src/OrderService.java", "listOrders")]
        self.assertEqual((j["change"], j["exported"]), ("signature_changed", True))
        self.assertIn("int size", j["new_sig"], "多行签名拼起来")
        self.assertFalse(got[("src/OrderService.java", "secretStep")]["exported"])
        self.assertEqual(got[("src/Orders.kt", "listOrders")]["change"], "signature_changed")
        self.assertTrue(got[("src/Orders.kt", "listOrders")]["exported"])

    def test_unknown_definition_falls_back_to_hunk_header(self):
        body = "".join("  line(%d)\n" % i for i in range(20))
        _, data = self.feature("hdr", {
            "src/a.ts": "export function longFunction(a: number) {\n" + body + "  return a\n}\n",
            "lib/a.rs": "fn a() {}\n",
        }, {
            "src/a.ts": "export function longFunction(a: number) {\n" + body + "  return a + 1\n}\n",
            "lib/a.rs": "fn a() { 1 }\n",
        })
        s = self.sym(data, "longFunction")
        self.assertEqual(s["change"], "body_only")
        self.assertTrue(any("lib/a.rs" in l for l in data["limits"]))


class Callers(Facts):
    def test_common_name_too_many_hits_and_ambiguous(self):
        many = "".join("bigCaller(%d)\n" % i for i in range(60))
        _, data = self.feature("callers", {
            "src/a.ts": "export function run(a: number) {\n  return a\n}\nexport function bigCaller(a: number) {\n  return a\n}\n"
                        "export function dupName(a: number) {\n  return a\n}\n",
            "src/use.ts": many + "dupName(1)\n",
            "src/other.ts": "export function dupName(x: string) {\n  return x\n}\n",
        }, {
            "src/a.ts": "export function run(a: string) {\n  return a\n}\nexport function bigCaller(a: string) {\n  return a\n}\n"
                        "export function dupName(a: string) {\n  return a\n}\n",
        })
        self.assertEqual(self.sym(data, "run")["skipped"], "common_name")
        big = self.sym(data, "bigCaller")
        self.assertEqual((big["skipped"], big["callers"], big["callers_total"]), ("too_many_hits", [], 60))
        dup = self.sym(data, "dupName")
        self.assertTrue(dup["ambiguous"])
        self.assertEqual([c["file"] for c in dup["callers"]], ["src/use.ts"])

    def test_in_diff_marks_callers_in_the_changed_files(self):
        repo = self.make_repo("indiff", {
            "src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n",
            "src/b.ts": "listOrders(1)\n",
            "src/c.ts": "listOrders(2)\n",
            "src/a.test.ts": "listOrders(3)\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function listOrders(p: string) {\n  return p\n}\n",
                           "src/b.ts": "listOrders('1')\n", "src/a.test.ts": "listOrders('3')\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, _ = self.facts(repo, "feature")
        s = self.sym(data, "listOrders")
        self.assertEqual({(c["file"], c["in_diff"]) for c in s["callers"]}, {("src/b.ts", True), ("src/c.ts", False)})
        self.assertFalse(any(c["file"] == "src/a.test.ts" for c in s["callers"]), "测试文件不算调用方")

    def test_reads_code_from_git_not_worktree(self):
        repo = self.make_repo("ref", {
            "src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n",
            "src/b.ts": "listOrders(1)\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function listOrders(p: string) {\n  return p\n}\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        self.write(repo, {"src/b.ts": "nothing here\n", "src/c.ts": "listOrders(9)\n"})  # 工作区的改动不该影响结果
        data, rd = self.facts(repo, "feature")
        self.assertNotEqual(self.kv(os.path.join(rd, "meta.txt"))["code_ref"], "WORKTREE")
        self.assertEqual([c["file"] for c in self.sym(data, "listOrders")["callers"]], ["src/b.ts"])

    def test_worktree_review_reads_working_files(self):
        repo = self.make_repo("wt", {
            "src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n",
            "src/b.ts": "listOrders(1)\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.write(repo, {"src/a.ts": "export function listOrders(p: string) {\n  return p\n}\n", "src/new.ts": "listOrders('x')\n"})
        data, rd = self.facts(repo)
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["code_ref"], "WORKTREE")
        self.assertEqual(sorted(c["file"] for c in self.sym(data, "listOrders")["callers"]), ["src/b.ts", "src/new.ts"])


class ConfigAndScope(Facts):
    def test_config_keys_and_hunk_kinds(self):
        _, data = self.feature("cfg", {
            "config/app.yml": "order:\n  page:\n    size: 20\n  legacy: true\n",
            "src/settings.properties": "a.b=1\n",
            "src/gen.ts": "// @generated\nexport const c = 1\n",
        }, {
            "config/app.yml": "order:\n  page:\n    size: 50\n  cursor:\n    enabled: true\n",
            "src/settings.properties": "a.b=1\nfeature.flag=on\n",
            "src/gen.ts": "// @generated\nexport const c = 2\n",
        })
        changes = {s["name"]: (s["change"], s["kind"]) for s in data["symbols"] if s["kind"] == "config_key"}
        self.assertEqual(changes, {"order.page.size": ("body_only", "config_key"),
                                   "order.cursor.enabled": ("added", "config_key"),
                                   "order.legacy": ("removed", "config_key"),
                                   "feature.flag": ("added", "config_key")})
        hunk_kinds = {h["file"]: h["kind"] for h in data["hunks"]}
        self.assertEqual((hunk_kinds["config/app.yml"], hunk_kinds["src/gen.ts"]), ("config", "generated"))

    def test_scope_and_method(self):
        _, data = self.feature("small", {"src/a.ts": "export const a = 1\n"}, {"src/a.ts": "export const a = 2\n"})
        self.assertEqual(data["scope"], {"diff": "diff.patch", "files": 1, "added": 1, "deleted": 1})
        self.assertEqual(data["method"], "按名字在本仓库文本匹配(git grep -w),只看签名变化")

    def test_hunks_cover_only_the_reviewed_diff_and_tests_with_the_flag(self):
        repo = self.make_repo("withtests", {"src/a.ts": "export const a = 1\n", "src/a.test.ts": "a\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export const a = 2\n", "src/a.test.ts": "b\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, _ = self.facts(repo, "feature")   # 测试文件默认不审查,不编代码块号
        self.assertEqual({h["file"]: h["id"] for h in data["hunks"]}, {"src/a.ts": "H1"})
        data, _ = self.facts(repo, "feature", "--with-tests")
        self.assertEqual({h["file"]: (h["id"], h["kind"]) for h in data["hunks"]},
                         {"src/a.test.ts": ("H1", "test"), "src/a.ts": ("H2", "other")})

    def test_bad_input_still_writes_file(self):
        rd = os.path.join(self.tmp, "rd")
        os.makedirs(rd)
        with open(os.path.join(rd, "diff.patch"), "w") as f:
            f.write("diff --git a/x.ts b/x.ts\n@@ -1 +1 @@\n-a\n+export function zzz(\n")
        with open(os.path.join(rd, "meta.txt"), "w") as f:
            f.write("repo_root=%s\ncode_ref=UNAVAILABLE\n" % self.tmp)
        p = self.run_py("analysis_facts.py", rd, check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = self.load(os.path.join(rd, "analysis-facts.json"))
        self.assertTrue(any("读不到被审查的代码" in l for l in data["limits"]))
        self.assertEqual(data["symbols"][0]["name"], "zzz")

    def test_new_config_file_and_other_languages_have_no_symbols(self):
        _, data = self.feature("newcfg", {"bin/run.sh": "#!/bin/sh\nstart_server() {\n  echo a\n}\n"}, {
            "config/new.yml": "a:\n  b: 1\n  c: 2\n",
            "bin/run.sh": "#!/bin/sh\nstart_server() {\n  echo b\n}\nstop_server() {\n  echo c\n}\n",
        })
        self.assertFalse(any(s["file"] in ("config/new.yml", "bin/run.sh") for s in data["symbols"]))
        self.assertTrue(any("bin/run.sh" in l for l in data["limits"]))


class CallersToCheck(Facts):
    def test_only_signature_changes_and_removals_with_callers_outside_the_diff(self):
        repo = self.make_repo("todo", {
            "src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n"
                        "export function onlyInDiff(p: number) {\n  return p\n}\n"
                        "export function dropMe(x: number) {\n  return x\n}\n"
                        "export function bodyOnly(x: number) {\n  return x\n}\n"
                        "function privateOne(x: number) {\n  return x\n}\n",
            "src/b.ts": "onlyInDiff(1)\nbodyOnly(1)\nprivateOne(1)\n",
            "src/c.ts": "dropMe(2)\n",
            "src/f.ts": "// 用法\nlistOrders(1)\n",
            "src/d.ts": "export function dupName(x: string) {\n  return x\n}\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {
            "src/a.ts": "export function listOrders(p: string) {\n  return p\n}\n"
                        "export function onlyInDiff(p: string) {\n  return p\n}\n"
                        "export function bodyOnly(x: number) {\n  return x + 1\n}\n"
                        "function privateOne(x: string) {\n  return x\n}\n"
                        "export function dupName(x: number) {\n  return x\n}\n",
            "src/b.ts": "onlyInDiff('1')\nbodyOnly(1)\nprivateOne(1)\n",
        }, "改")
        self.git(repo, "switch", "-q", "main")
        data, rd = self.facts(repo, "feature")
        todo = {c["name"]: c for c in data["callers_to_check"]}
        self.assertEqual(sorted(todo), ["dropMe", "listOrders"], "只改实现、私有、调用方都在改动行里的都不列")
        lo = todo["listOrders"]
        self.assertEqual((lo["file"], lo["line"], lo["change"], lo["callers"]), ("src/a.ts", 1, "signature_changed", [{"file": "src/f.ts", "line": 2}]))
        self.assertEqual((lo["ambiguous"], lo["truncated"]), (False, False))
        drop = todo["dropMe"]
        self.assertEqual((drop["change"], drop["callers"]), ("removed", [{"file": "src/c.ts", "line": 1}]))
        with open(os.path.join(rd, "ranges.txt"), encoding="utf-8") as f:
            ranges = [l.split("\t") for l in f.read().splitlines()]
        self.assertTrue(any(p == "src/a.ts" and int(a) <= drop["line"] <= int(b) for p, a, b in ranges), "删除的定义也定位到改动行上")

    def test_ambiguous_and_truncated_are_marked(self):
        many = "".join("bigCaller(%d)\n" % i for i in range(60))
        _, data = self.feature("todo2", {
            "src/a.ts": "export function bigCaller(a: number) {\n  return a\n}\nexport function dupName(a: number) {\n  return a\n}\n",
            "src/use.ts": many + "dupName(1)\n",
            "src/other.ts": "export function dupName(x: string) {\n  return x\n}\n",
        }, {"src/a.ts": "export function bigCaller(a: string) {\n  return a\n}\nexport function dupName(a: string) {\n  return a\n}\n"})
        todo = {c["name"]: c for c in data["callers_to_check"]}
        self.assertTrue(todo["dupName"]["ambiguous"])
        self.assertEqual((todo["bigCaller"]["truncated"], todo["bigCaller"]["callers"], todo["bigCaller"]["callers_total"]), (True, [], 60))


class FixClues(Facts):
    def test_lines_introduced_by_a_fix_commit_are_listed(self):
        repo = self.make_repo("fix", {"src/page.py": "def page(n):\n    return n\n\n\ndef size():\n    return 10\n"})
        self.commit(repo, {"src/page.py": "def page(n):\n    if n < 0:\n        return 0\n    return n\n\n\ndef size():\n    return 10\n"},
                    "Fix crash on negative page")
        self.commit(repo, {"src/page.py": "def page(n):\n    if n < 0:\n        return 0\n    return n\n\n\ndef size():\n    return 20  # prefix\n"},
                    "add prefix option")
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/page.py": "def page(n):\n    return n\n\n\ndef size():\n    return 30\n"}, "简化")
        self.git(repo, "switch", "-q", "main")
        data, _ = self.facts(repo, "feature")
        fix_sha = self.git(repo, "log", "-1", "--format=%H", "--grep=Fix crash", "main")
        self.assertEqual(data["fix_clues"], [{"file": "src/page.py", "hunk": "H1", "commit": fix_sha[:10],
                                              "title": "Fix crash on negative page", "base_lines": "2-3"}],
                         "prefix 不算 fix;只列修复提交引入的那几行")

    def test_chinese_keyword_and_no_clue_without_fix(self):
        repo = self.make_repo("fix2", {"a.go": "package a\n\nfunc A() int {\n\treturn 1\n}\n"})
        self.commit(repo, {"a.go": "package a\n\nfunc A() int {\n\treturn 2\n}\n"}, "修复返回值")
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"a.go": "package a\n\nfunc A() int {\n\treturn 3\n}\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, _ = self.facts(repo, "feature")
        self.assertEqual([c["title"] for c in data["fix_clues"]], ["修复返回值"])
        _, data = self.feature("nofix", {"b.go": "package b\n\nfunc B() int {\n\treturn 1\n}\n"},
                               {"b.go": "package b\n\nfunc B() int {\n\treturn 2\n}\n"})
        self.assertEqual(data["fix_clues"], [])


class Cochange(Facts):
    def test_stats_weight_batch_commits_by_size(self):
        import sys
        sys.path.insert(0, fixture.SCRIPTS)
        import analysis_facts as af
        batch = {"a.ts", "x.ts"} | {"f%d.ts" % i for i in range(30)}
        commits = [{"a.ts", "b.ts"}, {"a.ts", "b.ts", "c.ts"}, batch, batch, {"a.ts"}]
        got = {(a, b): (n, total, ratio) for a, b, n, total, ratio in af.cochange_stats(commits, {"a.ts"})}
        self.assertEqual(got, {("a.ts", "b.ts"): (2, 5, 0.58)}, "两次批量提交凑不出强关联,c.ts 只一起改过一次")
        # 只出现在批量提交里的文件,比例再高也不算
        self.assertEqual(af.cochange_stats([batch, batch], {"a.ts"}), [])
        # 每次都一起改的一组文件(不止两个)仍是强关联
        trio = [{"m.py", "n.py", "o.py"}] * 2
        self.assertEqual({b for _, b, *_ in af.cochange_stats(trio, {"m.py"})}, {"n.py", "o.py"})

    def test_files_often_changed_together_but_not_this_time(self):
        files = {"src/model.ts": "export const m = 1\n", "src/api.ts": "export const a = 1\n", "src/model.test.ts": "m\n",
                 "yarn.lock": "x\n", "src/other.ts": "export const o = 1\n"}
        files.update({"pkg/f%d.ts" % i: "export const f = 1\n" for i in range(25)})
        repo = self.make_repo("co", files)
        for i in (2, 3):
            self.commit(repo, {"src/model.ts": "export const m = %d\n" % i, "src/api.ts": "export const a = %d\n" % i,
                               "src/model.test.ts": "m%d\n" % i, "yarn.lock": "x%d\n" % i}, "改 model %d" % i)
        for i in (4, 5):   # 格式化这类批量提交:model 和 other 都在里面
            self.commit(repo, dict({"src/model.ts": "export const m = %d\n" % i, "src/other.ts": "export const o = %d\n" % i},
                                   **{"pkg/f%d.ts" % k: "export const f = %d\n" % i for k in range(25)}), "format %d" % i)
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/model.ts": "export const m = 9\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, _ = self.facts(repo, "feature")
        got = {c["file"]: c for c in data["cochange"]}
        self.assertEqual(sorted(got), ["src/api.ts"], "测试、lock、只在批量提交里一起改的都不列")
        self.assertEqual((got["src/api.ts"]["with"], got["src/api.ts"]["together"]), ("src/model.ts", 3))
        self.assertGreater(got["src/api.ts"]["ratio"], 0.5)


class FactsNote(Facts):
    def report_note(self, rd):
        return [l for l in self.run_script("report-lines.sh", rd).stdout.splitlines() if l.startswith("FACTS_NOTE:")]

    def test_disclosed_only_when_something_was_not_checked(self):
        repo = self.make_repo("note", {"src/a.ts": "export function listOrders(p: number) {\n  return p\n}\n", "src/b.ts": "listOrders(1)\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function listOrders(p: string) {\n  return p\n}\n"}, "改")
        self.git(repo, "switch", "-q", "main")
        data, rd = self.facts(repo, "feature")
        self.assertEqual(data["limits"], [])
        self.assertEqual(self.report_note(rd), [])
        data["limits"].append("查调用方超过 10 秒,已停止;调用方没有列出")
        data["callers_to_check"][0]["ambiguous"] = True
        with open(os.path.join(rd, "analysis-facts.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        note = self.report_note(rd)
        self.assertEqual(len(note), 1)
        self.assertIn("查调用方超过 10 秒", note[0])
        self.assertIn("同名定义 1 个、调用方没列全 0 个", note[0])
        self.assertIn("可以用 deep", note[0])
        with open(os.path.join(rd, "meta.txt"), "a", encoding="utf-8") as f:
            f.write("depth=deep\n")
        self.assertIn("deep 的影响面一遍已从使用方往回查", self.report_note(rd)[0])
