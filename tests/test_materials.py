"""意图和需求材料的全文登记、验收标准提取、读取状态的报告措辞、依据引用材料、交付范围待明确,
以及收集问询时跳过重计算和分段计时的测试。"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import intent

TEAM = """
tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/story/42
tools.doc_reader = 用 acme-kit:doc-reader 读文档链接,支持 https://docs.acme.example.com/ 下的页面
review.ask = requirement, doc
"""


def acceptance_of(text):
    it = intent.Intent()
    mat = {"acceptance_lines": []}
    it.take_acceptance("文档", text, mat)
    return [a.replace("(来自 文档)", "") for a in it.acceptance], mat["acceptance_lines"]


def line_of(prefix, out):
    return next((l for l in out.splitlines() if l.startswith(prefix)), "")


class AcceptanceExtraction(fixture.Base):
    def test_numbered_and_annotated_headings(self):
        for head in ("## 3. 验收标准", "### 3.2 验收标准(AC)", "**四、验收条件**", "## Acceptance Criteria", "## AC", "### 验收",
                     "验收标准:"):
            with self.subTest(head=head):
                items, lines = acceptance_of("# 订单列表\n\n%s\n- 分页切换后保留筛选条件\n- 导出按当前排序\n" % head)
                self.assertEqual(items, ["分页切换后保留筛选条件", "导出按当前排序"])
                self.assertEqual(lines, [4, 5])

    def test_plain_text_mentioning_acceptance_does_not_start_a_section(self):
        items, _ = acceptance_of("# 订单列表\n\n本次改动需要满足验收标准里的要求,详见下文。\n- 这是普通列表,不是验收条目\n")
        self.assertEqual(items, [])
        items, _ = acceptance_of("## 背景\n- ACME 的旧分页\n## ACK 说明\n- 不是验收\n")
        self.assertEqual(items, [])

    def test_subheadings_continue_until_same_level(self):
        text = ("# 订单列表导出\n## 3. 验收标准\n### 3.1 分页\n- 翻页后序号连续\n### 3.2 导出\n- 导出文件包含订单号\n"
                "#### 细节\n1. 文件名带日期\n## 4. 上线计划\n- 灰度一周\n")
        items, lines = acceptance_of(text)
        self.assertEqual(items, ["翻页后序号连续", "导出文件包含订单号", "文件名带日期"])
        self.assertEqual(lines, [4, 6, 8])

    def test_bold_heading_ends_at_next_bold_heading(self):
        items, _ = acceptance_of("**验收标准**\n- 空列表显示 0 页\n**上线计划**\n- 灰度一周\n")
        self.assertEqual(items, ["空列表显示 0 页"])

    def test_table_rows(self):
        text = ("## 验收标准\n\n| 编号 | 场景 | 预期 |\n|---|:---|---:|\n| 1 | 切换分页 | 保留勾选 |\n| 2 | 导出 |  |\n\n## 其他\n| a | b |\n")
        items, lines = acceptance_of(text)
        self.assertEqual(items, ["1;切换分页;保留勾选", "2;导出"])
        self.assertEqual(lines, [5, 6])


class MaterialRegistration(fixture.ShopRepo):
    def materials(self, rd):
        return self.load(os.path.join(rd, "materials.json"), [])

    def test_long_document_is_clipped_with_full_text_and_outline(self):
        filler = "\n".join("- 第 %d 条背景:订单列表的分页说明。" % i for i in range(300))
        doc = "# 订单列表导出\n\n## 1. 背景\n\n%s\n\n## 5. 验收标准\n\n- 导出按当前筛选条件生成\n- 分页切换后保留勾选\n" % filler
        self.write(self.repo, {"docs/plan.md": doc})
        r, rd = self.collect("--doc", "docs/plan.md")
        mat = next(x for x in self.materials(rd) if x["kind"] == "doc")
        full = doc.strip()
        self.assertGreater(full.index("## 5. 验收标准"), 4000)
        text = Path(rd, mat["path"]).read_text(encoding="utf-8")
        self.assertEqual(text, full)
        self.assertEqual((mat["chars"], mat["lines"], mat["sha256"], mat["clipped"]),
                         (len(full), len(full.split("\n")), hashlib.sha256(full.encode("utf-8")).hexdigest(), True))
        self.assertEqual(mat["label"], "文档 docs/plan.md")
        head_line = full.split("\n").index("## 5. 验收标准") + 1
        self.assertIn({"line": head_line, "text": "## 5. 验收标准"}, mat["headings"])
        self.assertEqual(mat["acceptance_lines"], [head_line + 2, head_line + 3])
        md = Path(rd, "intent.md").read_text(encoding="utf-8")
        section = md.split("## 文档 docs/plan.md\n", 1)[1]
        self.assertIn("……(已截断:全文 %d 字符、%d 行,见 %s;需要时按下面的章节位置读取原文)" % (mat["chars"], mat["lines"], mat["path"]), section)
        self.assertIn("- 第 %d 行 ## 5. 验收标准" % head_line, section)
        self.assertIn("导出按当前筛选条件生成(来自 docs/plan.md)", md)
        commits = next(x for x in self.materials(rd) if x["kind"] == "commits")
        self.assertIn("## 提交说明\n\n(全文:%s)" % commits["path"], md)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual((meta["acceptance_state"], meta["intent_acceptance"], meta["materials_clipped"], meta["requirement_materials"]),
                         ("extracted", "2", "1", "1"))
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn(";验收标准 2 条(自动提取,可能不全);1 份材料较长,intent.md 只放摘录,全文在 materials/", line_of("INTENT:", out))
        self.assertIn("、需求核对", line_of("CHECKED:", out))
        self.assertNotIn("需求核对(", line_of("CHECKED:", out))

    def test_requirement_without_acceptance(self):
        self.write(self.repo, {"docs/plan.md": "# 订单列表导出\n\n订单列表支持导出当前页。\n"})
        _, rd = self.collect("--doc", "docs/plan.md")
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual(meta["acceptance_state"], "not_extracted")
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("需求核对(未自动提取到验收标准,按需求材料原文核对明确的要求)", line_of("CHECKED:", out))
        self.assertIn(";需求材料 1 份,未自动提取到验收标准", line_of("INTENT:", out))

    def test_commit_message_only_is_intent_without_requirement(self):
        _, rd = self.collect()
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual((meta["acceptance_state"], meta["intent_materials"], meta["requirement_materials"]), ("no_requirement", "1", "0"))
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("需求核对(没有需求材料,按改动说明核对其中点名的行为和对象)", line_of("CHECKED:", out))

    def test_all_local_documents_are_registered(self):
        names = ["docs/p%d.md" % i for i in range(1, 7)]
        self.write(self.repo, {n: "# 分页方案 %d\n\n订单列表分页说明。\n" % i for i, n in enumerate(names, 1)})
        args = [x for n in names for x in ("--doc", n)]
        _, rd = self.collect(*args)
        docs = [x for x in self.materials(rd) if x["kind"] == "doc"]
        self.assertEqual([x["label"] for x in docs], ["文档 " + n for n in names])
        md = Path(rd, "intent.md").read_text(encoding="utf-8")
        for n in names:
            self.assertIn("## 文档 %s\n" % n, md)
        doc = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
        self.assertEqual(doc["local_files"], names)

    def test_staged_partial_header(self):
        self.config(TEAM)
        stage = self.run_py("sources.py", "stage").stdout.strip()
        Path(stage, "doc.md").write_text("url: https://docs.acme.example.com/d/abc\ntitle: 订单导出 PRD\npartial: 只取到前两页\n---\n"
                                         "## 验收标准\n- 导出文件包含订单号\n", encoding="utf-8")
        r, rd = self.collect("--source", "doc=https://docs.acme.example.com/d/abc", "--source-dir", stage)
        mat = next(x for x in self.materials(rd) if x["kind"] == "staged")
        self.assertEqual((mat["partial"], mat["label"], mat["clipped"]), ("只取到前两页", "需求文档:订单导出 PRD", False))
        self.assertNotIn("partial", next(x for x in self.materials(rd) if x["kind"] == "commits"))
        md = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## 需求文档:订单导出 PRD\n\n(只读到部分内容:只取到前两页)\n(全文:%s)" % mat["path"], md)
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["materials_partial"], "1")
        self.assertIn(";1 份只读到部分内容", line_of("INTENT:", self.run_script("report-lines.sh", rd).stdout))

    def test_each_collection_replaces_previous_materials(self):
        self.write(self.repo, {"docs/plan.md": "# 分页方案\n"})
        _, rd = self.collect("--doc", "docs/plan.md")
        self.assertTrue(Path(rd, "materials", "m2.md").is_file())
        _, rd2 = self.collect("--doc", "", "--out", rd)
        self.assertEqual(rd2, rd)
        self.assertEqual([x["kind"] for x in self.materials(rd)], ["commits"])
        self.assertFalse(Path(rd, "materials", "m2.md").exists())


class NoMaterials(fixture.Base):
    def test_diff_only(self):
        self.env["CR_FETCH"] = "0"
        repo = self.make_repo("plain", {"list.ts": "export const pageSize = 10\n"})
        self.write(repo, {"list.ts": "export const pageSize = 20\n"})
        r = self.run_script("collect-diff.sh", "--base", "main", cwd=repo)
        rd = self.review_dir(r.stdout)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual((meta["files"], meta["mode"], meta["intent_sources"]), ("1", "local", "无"))
        self.assertEqual((meta["acceptance_state"], meta["intent_materials"], meta["materials_clipped"]), ("no_requirement", "0", "0"))
        self.assertEqual(self.load(os.path.join(rd, "materials.json")), [])
        out = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("需求核对(没有意图和需求材料,未判断需求符合性)", line_of("CHECKED:", out))
        self.assertEqual(line_of("INTENT:", out), "INTENT: 意图来源:无")


class IssueLimit(fixture.Base):
    def test_unread_issue_numbers_are_noted(self):
        self.use_mock_glab()
        self.config("gitlab_hosts=code.example.test:8929\ntools.requirement = 用 acme-kit:ticket-reader 读需求单\n")
        repo = self.make_repo("shop", {"a.ts": "old\n"}, remote="http://code.example.test:8929/eng/shop.git")
        base = self.git(repo, "rev-parse", "HEAD")
        head = self.commit(repo, {"a.ts": "new\n"}, "分页和导出\n\n" + "\n".join("Closes #%d" % i for i in range(1, 7)))
        for i in range(1, 7):
            self.mock_api("projects/:id/issues/%d" % i, dict(title="订单列表需求 %d" % i, description="## 验收标准\n- 第 %d 项" % i))
        r = self.run_script("collect-diff.sh", base + ".." + head, cwd=repo)
        rd = self.review_dir(r.stdout)
        self.assertIn("NOTE: 另有 1 个关联需求编号没有读取:#6;需要时用 --doc 或 --intent 补充", r.stdout)
        issues = [x for x in self.load(os.path.join(rd, "materials.json")) if x["kind"] == "issue"]
        self.assertEqual([x["source"] for x in issues], ["#1", "#2", "#3", "#4", "#5"])
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["intent_acceptance"], "5")


PRD = """\
# 订单列表导出
## 范围
- 导出当前页的订单
- 本期不含批量导出全部分页
## 验收标准
- 导出文件包含订单号
"""


class MaterialEvidenceAndScope(fixture.ProjectFixture):
    P = "src/orders/paginate.ts"

    def setUp(self):
        super().setUp()
        self.write(self.repo, {"docs/prd.md": PRD})
        self.rd, _ = self.collect("feature", "--doc", "docs/prd.md")
        self.doc = next(x for x in self.load(os.path.join(self.rd, "materials.json")) if x["kind"] == "doc")

    def cand(self, summary, line=2, category="intent"):
        return {"file": self.P, "line": line, "category": category, "severity": "major", "summary": summary, "failure_scenario": summary}

    def run_with(self, rows):
        """rows: (候选, 置信度, 依据, 核实结果里另加的字段)。"""
        cands = [c for c, _, _, _ in rows]
        Path(self.rd, "candidates.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
        self.run_script("prepare-verify.sh", self.rd)
        extra = {c["summary"]: (conf, ev, more) for c, conf, ev, more in rows}
        results = []
        for name in sorted(os.listdir(os.path.join(self.rd, "verify"))):
            for c in self.load(os.path.join(self.rd, "verify", name)):
                conf, ev, more = extra[c["summary"]]
                results.append(dict(c, confidence=conf, verdict="读过代码", evidence=ev, **more))
        Path(self.rd, "verify", "result-1.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        self.run_script("aggregate.sh", self.rd)
        return (self.load(os.path.join(self.rd, "final.json")), self.load(os.path.join(self.rd, "findings.json")),
                self.load(os.path.join(self.rd, "rejected.json")))

    def test_evidence_can_cite_registered_materials(self):
        code = {"file": self.P, "lines": "2"}
        rows = [(self.cand("引用材料行范围"), 90, [code, {"file": self.doc["path"], "lines": "3-4"}], {}),
                (self.cand("引用材料编号和文字"), 90, [code, {"file": self.doc["id"], "lines": "导出当前页的订单"}], {}),
                (self.cand("材料行号越界", 3), 90, [code, {"file": self.doc["path"], "lines": "99"}], {}),
                (self.cand("未登记的材料", 4), 90, [code, {"file": "materials/m99.md", "lines": "1"}], {})]
        final, _, rejected = self.run_with(rows)
        self.assertEqual(sorted(x["summary"] for x in final["groups"]["intent"]), ["引用材料编号和文字", "引用材料行范围"])
        why = {x["summary"]: x["reject_reason"] for x in rejected}
        self.assertIn("依据引用的位置不存在:%s:99" % self.doc["path"], why["材料行号越界"])
        self.assertIn("依据引用的位置不存在:materials/m99.md:1", why["未登记的材料"])

    def test_modified_material_file_is_not_accepted(self):
        Path(self.rd, self.doc["path"]).write_text("改过的内容\n" * 10, encoding="utf-8")
        _, _, rejected = self.run_with([(self.cand("材料被改过"), 90, [{"file": self.P, "lines": "2"}, {"file": self.doc["path"], "lines": "3"}], {})])
        self.assertIn("依据引用的位置不存在", rejected[0]["reject_reason"])

    def test_delivery_scope(self):
        path = self.doc["path"]
        req = {"file": path, "lines": "3", "quote": "导出当前页的订单"}
        lim = {"file": path, "lines": "4", "quote": "本期不含批量导出全部分页"}
        code = [{"file": self.P, "lines": "2"}]
        rows = [
            (self.cand("跨页导出待明确"), 90, code, {"delivery_scope": "unclear", "scope_basis": {"requirement": req, "limit": lim, "missing": "是否包含\n跨页勾选"}}),
            (self.cand("限定原文对不上", 3), 90, code, {"delivery_scope": "unclear", "scope_basis": {"requirement": req, "limit": dict(lim, quote="材料里没有这句")}}),
            (self.cand("批量导出不属于本期", 4), 90, code, {"delivery_scope": "out", "scope_basis": {"limit": lim}}),
            (self.cand("分页缺陷", 2, "bug"), 90, code, {"delivery_scope": "unclear", "scope_basis": {"requirement": req, "limit": lim}}),
            (self.cand("当前页导出缺订单号", 3), 90, code, {"scope_basis": {"requirement": req}}),
        ]
        final, findings, rejected = self.run_with(rows)
        notes = final["scope_notes"]
        self.assertEqual([x["summary"] for x in notes], ["跨页导出待明确"])
        self.assertEqual(set(notes[0]), {"summary", "file", "line", "failure_scenario", "verdict", "confidence", "scope_basis", "id"})
        self.assertEqual(final["counts"]["scope_notes"], 1)
        self.assertNotIn("跨页导出待明确", [x["summary"] for x in findings])
        self.assertEqual(sorted(x["summary"] for x in final["groups"]["intent"]), ["当前页导出缺订单号", "限定原文对不上"])
        self.assertEqual([x["summary"] for x in final["groups"]["defects"]], ["分页缺陷"])
        bad = next(x for x in findings if x["summary"] == "限定原文对不上")
        self.assertIn("限定引用的原文在 %s 里找不到" % path, bad["scope_basis_invalid"])
        out = next(x for x in rejected if x["summary"] == "批量导出不属于本期")
        self.assertTrue(out["reject_reason"].startswith("按需求材料不属于本次交付:本期不含批量导出全部分页"))
        self.assertEqual(final["counts"]["passed"], 3)   # 待明确的和不属于本期的都不计入通过的发现
        conflict = [c for c in final["scope_conflicts"] if c["requirement"] == "%s:3" % path]
        in_ids = [x["id"] for x in findings if x["summary"] in ("当前页导出缺订单号", "限定原文对不上")]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(conflict[0]["scopes"]["unclear"], [notes[0]["id"]])
        self.assertEqual(sorted(conflict[0]["scopes"]["in"]), sorted(in_ids))
        self.assertEqual(conflict[0]["scopes"]["out"], [])
        lines = self.run_script("report-lines.sh", self.rd).stdout
        self.assertIn("UNCLEAR: %s:2\t跨页导出待明确\t导出当前页的订单\t本期不含批量导出全部分页\t是否包含 跨页勾选" % self.P, lines)
        self.assertIn("SCOPE_CONFLICT: %s:3 上不同核实结论的交付范围不一致:in=" % path, lines)
        self.assertIn(";unclear=%s" % notes[0]["id"], lines)


class AskGateSkipsFacts(fixture.ShopRepo):
    def test_large_gate_skips_analysis_facts(self):
        self.env.update(CR_BATCH_LINES="1", CR_LARGE_BATCHES="1")
        r, rd = self.collect()
        self.assertIn("LARGE:", r.stdout)
        self.assertFalse(Path(rd, "analysis-facts.json").exists())
        self.assertFalse(Path(rd, "facts-failed.txt").exists())
        r, rd2 = self.collect("--allow-large", "--out", rd)
        self.assertEqual(rd2, rd)
        self.assertNotIn("LARGE:", r.stdout)
        self.assertTrue(Path(rd, "analysis-facts.json").is_file())

    def test_non_interactive_large_still_runs_facts(self):
        self.env.update(CR_BATCH_LINES="1", CR_LARGE_BATCHES="1", CR_NONINTERACTIVE="1")
        r, rd = self.collect()
        self.assertIn("LARGE:", r.stdout)
        self.assertTrue(Path(rd, "analysis-facts.json").is_file())


class StageTiming(fixture.ProjectFixture):
    def prepare(self, stamps):
        rd, _ = self.collect("feature")
        now = int(time.time())
        with open(os.path.join(rd, "meta.txt"), "a", encoding="utf-8") as f:
            f.write("started_epoch=%d\n" % (now - 300))
        Path(rd, "timing.txt").write_text("".join("%s=%d\n" % (k, now - v) for k, v in stamps), encoding="utf-8")
        Path(rd, "report.md").write_text("# 报告\n", encoding="utf-8")
        Path(rd, "findings.json").write_text("[]", encoding="utf-8")
        out = self.run_script("save-review.sh", rd).stdout
        return next(l for l in out.splitlines() if l.startswith("cost="))

    def test_cost_has_stage_breakdown(self):
        # collected 写过两次,以最后一次为准;查找从 collected 和 rules 中较晚的算起
        cost = self.prepare([("collected", 290), ("collected", 280), ("rules", 270), ("verify_start", 200), ("aggregated", 50)])
        self.assertRegex(cost, r"^cost=用时 5 分 \d+ 秒\(收集 20 秒 · 查找 1 分 10 秒 · 核实 2 分 30 秒 · 汇总与报告 5\d 秒\)")

    def test_missing_endpoints_drop_their_stages(self):
        cost = self.prepare([("collected", 280), ("aggregated", 50)])
        self.assertRegex(cost, r"^cost=用时 5 分 \d+ 秒\(收集 20 秒 · 汇总与报告 5\d 秒\)")
        cost = self.prepare([])
        self.assertRegex(cost, r"^cost=用时 5 分 \d+ 秒 · ")


class LegacyReport(fixture.Base):
    def report(self, meta):
        rd = Path(self.tmp, "legacy")
        rd.mkdir(exist_ok=True)
        (rd / "meta.txt").write_text("mode=local\ndepth=standard\nonly=all\nintent_sources=提交说明\n" + meta, encoding="utf-8")
        return self.run_script("report-lines.sh", str(rd)).stdout

    def test_records_without_acceptance_state_keep_old_wording(self):
        out = self.report("intent_acceptance=0\n")
        self.assertIn("需求核对(没有验收标准,只核对意图里点名的对象)", line_of("CHECKED:", out))
        self.assertEqual(line_of("INTENT:", out), "INTENT: 意图来源:提交说明")
        out = self.report("intent_acceptance=2\n")
        self.assertEqual(line_of("INTENT:", out), "INTENT: 意图来源:提交说明;验收标准 2 条")
        self.assertIn("已检查:bug、安全、需求核对", line_of("CHECKED:", out))
