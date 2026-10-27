"""规则沉淀(cr-rules):拉取 MR 评论、按准入清单核对候选、写入 .agents/rules。GitLab 用 tests/bin/glab 模拟。"""
import datetime
import json
import os
from pathlib import Path
import subprocess

import test_crosscheck as fixture

LIST = "projects/:id/merge_requests?state=merged&per_page=100&page=1"
CHECKS = {k: True for k in ("project_specific", "not_tool_catchable", "not_subjective", "not_overturned", "no_conflict", "generalizable")}


def disc(iid):
    return "projects/:id/merge_requests/%d/discussions?per_page=100&page=1" % iid


NOW = datetime.datetime.utcnow().replace(microsecond=0)


def ago(days):
    return (NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def note(nid, who, body, path=None, line=None, sha=None, system=False, bot=False, resolved=None):
    n = {"id": nid, "body": body, "system": system, "author": {"username": who, "bot": bot}, "created_at": ago(3)}
    if path:
        n["position"] = {"new_path": path, "new_line": line, "head_sha": sha}
    if resolved is not None:
        n.update(resolvable=True, resolved=resolved)
    return n


def rule(**kw):
    c = {"kind": "rule", "topic": "api", "title": "分页参数要有上限", "rule": "在 src/api/** 的接口里,必须校验分页参数的上限",
         "why": "不校验会查全表(!12、!15)", "severity": "重要", "bad_example": "list(limit)", "good_example": "list(Math.min(limit, 100))",
         "exceptions": "内部管理后台接口不限", "paths": ["src/api/**"], "checks": dict(CHECKS),
         "evidence": [{"mr": 12, "note": 101, "quote": "分页参数没校验上限"}, {"mr": 15, "note": 201, "quote": "limit 要加上限"}]}
    c.update(kw)
    return c


class RulesMining(fixture.Base):
    def setUp(self):
        super().setUp()
        self.use_mock_glab()
        self.repo = self.make_repo("shop", {
            "src/api/orders.ts": "".join("line %d\n" % i for i in range(1, 11)),
            "src/api/users.ts": "export function users() {}\n",
            "REVIEW.md": "# 规则\n\n- 日志里不能出现邮箱。\n",
        }, remote="git@gitlab.example.com:eng/shop.git")
        self.sha0 = self.git(self.repo, "rev-parse", "HEAD")
        # MR 12 在评论后改了第 5 行
        self.sha1 = self.commit(self.repo, {"src/api/orders.ts": "".join(("line %d\n" % i) if i != 5 else "limit checked\n" for i in range(1, 11))}, "fix")

    def mr(self, iid, author="dev1", days=5, updated=None, desc="", sha=None):
        return {"iid": iid, "title": "MR %d" % iid, "author": {"username": author}, "merged_at": ago(days),
                "updated_at": updated or ago(days), "web_url": "https://gitlab.example.com/eng/shop/-/merge_requests/%d" % iid,
                "description": desc, "sha": sha or self.sha0}

    def standard_mrs(self):
        self.mock_api(LIST, [self.mr(12, sha=self.sha1), self.mr(15, author="dev2"), self.mr(16, author="dev3"),
                             self.mr(17, author="dev4"), self.mr(18, author="dev5"), self.mr(3, days=200)])
        self.mock_api(disc(12), [
            {"id": "d1", "notes": [
                note(101, "alice", "分页参数没校验上限,传个 100000 就查全表了", "src/api/orders.ts", 5, self.sha0),
                note(102, "dev1", "好的,我加上", "src/api/orders.ts", 5, self.sha0)]},
            {"id": "d2", "notes": [note(103, "gitlab-bot", "Pipeline passed"), note(104, "alice", "changed the description", system=True)]},
        ])
        self.mock_api(disc(15), [
            {"id": "d3", "notes": [note(201, "bob", "limit 要加上限,不然会查全表", "src/api/users.ts", 1, self.sha0, resolved=True)]},
            {"id": "d4", "notes": [note(202, "project_7_bot_ab12", "自动检查完成")]},
        ])
        # MR 16:评论了,但代码没改、讨论也没解决
        self.mock_api(disc(16), [{"id": "d5", "notes": [note(301, "carol", "这里应该用事务包起来", "src/api/users.ts", 1, self.sha0, resolved=False)]}])
        # MR 17:作者只回了「好的」,代码没改
        self.mock_api(disc(17), [{"id": "d6", "notes": [
            note(302, "dave", "users 这里也要用事务,limit 也要加上限", "src/api/users.ts", 1, self.sha0, resolved=False),
            note(303, "dev4", "好的", "src/api/users.ts", 1, self.sha0)]}])
        self.mock_api(disc(18), [{"id": "d7", "notes": [note(401, "alice", "金额要用分,别用浮点", "src/api/orders.ts", 2, self.sha0, resolved=True)]}])

    def mine(self, *args):
        r = self.run_script("mine-mrs.sh", *args, cwd=self.repo)
        return r.stdout, self.review_dir(r.stdout)

    def check(self, work, cands):
        Path(work, "result-1.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "result-1.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, self.load(os.path.join(work, "candidates.json")), self.load(os.path.join(work, "dropped.json"))

    def write_rules(self, work, *args):
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "rules-write.py"), work] + list(args),
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def batch_notes(self, work):
        return [n for f in sorted(os.listdir(work)) if f.startswith("batch-") for n in self.load(os.path.join(work, f))]

    def test_fetch_filters_bots_records_fix_state_and_reuses_cache(self):
        self.standard_mrs()
        out, work = self.mine()
        self.assertIn("MR 5 个", out)
        self.assertIn("新拉取 5", out)
        notes = {n["note"]: n for n in self.batch_notes(work)}
        self.assertEqual(sorted(notes), [101, 102, 201, 301, 302, 303, 401])         # 机器人和系统消息去掉,超出窗口的 MR 不取
        self.assertIn("line 5", notes[101]["code"])
        self.assertTrue(notes[101]["changed_after"])                   # 评论处的代码在最终版本里改了
        self.assertEqual((notes[201]["resolved"], notes[201]["changed_after"]), (True, False))
        self.assertEqual(self.kv(os.path.join(work, "meta.txt"))["reviewers"], "4")   # MR 作者自己的回复不算评审人
        # 没更新过的 MR 不再拉评论
        for iid in (12, 15, 16, 17, 18):
            os.remove(os.path.join(self.mock, "api", fixture.api_key(disc(iid)) + ".json"))
        out, _ = self.mine()
        self.assertIn("用缓存 5", out)
        # 更新过的重新拉
        self.mock_api(LIST, [self.mr(12, updated=ago(1), sha=self.sha1), self.mr(15, author="dev2"), self.mr(16, author="dev3"),
                             self.mr(17, author="dev4"), self.mr(18, author="dev5")])
        self.mock_api(disc(12), [{"id": "d9", "notes": [note(501, "carol", "新评论")]}])
        out, work = self.mine()
        self.assertIn("新拉取 1", out)
        self.assertIn("用缓存 4", out)
        self.assertIn(501, [n["note"] for n in self.batch_notes(work)])

    def test_check_applies_admission_list(self):
        self.standard_mrs()
        _, work = self.mine()
        good = rule()
        dup = rule(rule="在 src/api/** 的接口里必须校验分页参数的上限!",
                   evidence=[{"mr": 12, "note": 101, "quote": "传个 100000 就查全表了"}, {"mr": 15, "note": 201, "quote": "不然会查全表"}])
        forged = rule(rule="所有查询必须加索引", evidence=[{"mr": 12, "note": 101, "quote": "所有查询都要加索引"},
                                                     {"mr": 15, "note": 201, "quote": "limit 要加上限"}])
        thin = rule(rule="写操作必须记审计日志", evidence=[{"mr": 12, "note": 101, "quote": "分页参数没校验上限"}])
        unfixed = rule(rule="接口里必须用事务包起来", evidence=[{"mr": 16, "note": 301, "quote": "应该用事务包起来"},
                                                       {"mr": 16, "note": 301, "quote": "事务"}, {"mr": 13, "note": 9, "quote": "x"}])
        unfixed2 = rule(rule="users 接口必须用事务", evidence=[{"mr": 16, "note": 301, "quote": "应该用事务包起来"},
                                                       {"mr": 17, "note": 302, "quote": "users 这里也要用事务"}])
        no_must = rule(rule="分页参数的上限最好校验一下")
        no_exc = rule(rule="接口必须记录耗时", exceptions="")
        subjective = rule(rule="接口必须写得清楚", checks=dict(CHECKS, not_subjective=False))
        existing = rule(rule="日志里不能出现邮箱")
        bad_scope = rule(rule="别处必须这样", paths=["../x/**"])
        out, cands, dropped = self.check(work, [good, dup, forged, thin, unfixed, unfixed2, no_must, no_exc, subjective, existing, bad_scope])
        self.assertEqual([c["rule"] for c in cands], ["在 src/api/** 的接口里,必须校验分页参数的上限"])
        c = cands[0]
        self.assertEqual((c["no"], c["mrs"], c["people"], c["severity"]), (1, [12, 15], 2, "重要"))
        self.assertEqual(len(c["evidence"]), 2)                              # 重复的候选合并成一条,同一条评论不重复计
        reasons = {d["rule"]: d["reason"] for d in dropped}
        self.assertIn("证据不足", reasons["所有查询必须加索引"])
        self.assertIn("!12 note 101", reasons["所有查询必须加索引"])
        self.assertIn("证据不足", reasons["写操作必须记审计日志"])
        self.assertIn("证据不足", reasons["接口里必须用事务包起来"])           # 同一 MR 不算两次,编造的 MR 作废
        self.assertIn("代码没改、讨论也没解决", reasons["users 接口必须用事务"])  # 作者只回「好的」不算改了
        self.assertIn("必须 / 禁止", reasons["分页参数的上限最好校验一下"])
        self.assertIn("不要报", reasons["接口必须记录耗时"])
        self.assertIn("非主观风格", reasons["接口必须写得清楚"])
        self.assertIn("已有规则(REVIEW.md)", reasons["日志里不能出现邮箱"])
        self.assertIn("范围不对", reasons["别处必须这样"])
        self.assertIn("1. [api · 重要] 在 src/api/** 的接口里,必须校验分页参数的上限", out)
        self.assertIn("准入:✓2 个 MR ✓2 位评审人 ✓改过代码或已解决 ✓针对改动的代码 ✓有不要报的边界 ✓有正反例", out)
        self.assertIn("不要报:内部管理后台接口不限", out)

    def test_ranked_by_mrs_and_reviewers(self):
        self.standard_mrs()
        _, work = self.mine()
        three = rule(rule="在 src/api/** 里,limit 必须有上限且必须校验",
                     evidence=rule()["evidence"] + [{"mr": 17, "note": 302, "quote": "limit 也要加上限"}])
        single = rule(rule="在 src/api/** 里,金额字段必须用整数分表示",
                      evidence=[{"mr": 18, "note": 401, "quote": "金额要用分"}, {"mr": 12, "note": 101, "quote": "查全表"}])
        single_reviewer = rule(rule="金额相关的计算禁止使用浮点数",
                               evidence=[{"mr": 18, "note": 401, "quote": "别用浮点"}, {"mr": 12, "note": 101, "quote": "分页参数"}])
        out, cands, _ = self.check(work, [single_reviewer, single, three])
        self.assertEqual(cands[0]["rule"], three["rule"])
        self.assertEqual((cands[0]["mrs"], cands[0]["people"]), ([12, 15, 17], 3))
        self.assertIn("?1 位评审人", out)                                     # 只有 alice 一个人:标出来,不丢

    def test_feedback_records_as_suppress_evidence(self):
        self.standard_mrs()
        pdir = os.path.join(self.home, "reviews", "shop")
        os.makedirs(pdir)
        Path(pdir, ".repo").write_text("gitlab.example.com/eng/shop\n")
        rows = [{"key": "k1", "id": "f1", "verdict": "invalid", "reason": "生成的客户端代码不用管", "file": "src/api/gen/a.ts", "author": "h1", "time": "2026-10-01 10:00"},
                {"key": "k2", "id": "f2", "verdict": "invalid", "reason": "这是生成的客户端代码,不用管重复", "file": "src/api/gen/b.ts", "author": "h2", "time": "2026-10-02 10:00"}]
        Path(pdir, "feedback.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        _, work = self.mine()
        self.assertEqual(len(self.load(os.path.join(work, "feedback.json"))), 2)
        sup = {"kind": "suppress", "topic": "api", "rule": "生成的客户端代码里的重复不用提", "paths": ["src/api/gen/**"],
               "evidence": [{"feedback": "k1", "quote": "生成的客户端代码不用管"}, {"feedback": "f2", "quote": "不用管重复"}]}
        out, cands, _ = self.check(work, [sup])
        self.assertEqual([c["kind"] for c in cands], ["suppress"])
        self.assertIn("1. [不要报 · api] 生成的客户端代码里的重复不用提", out)

    def test_write_rules_and_collect_rules_uses_them(self):
        self.standard_mrs()
        _, work = self.mine()
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        sup = {"kind": "suppress", "topic": "api", "rule": "mock 数据里的硬编码金额不用提", "paths": ["src/api/**"],
               "evidence": rule()["evidence"]}
        _, cands, _ = self.check(work, [rule(), r2, sup])
        no = {c["rule"]: c["no"] for c in cands}
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[sup["rule"]]
        out = self.write_rules(work, "--pick", "%d,%d" % (n1, n3), "--edit", "%d=在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)" % n1)
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('---\npaths: ["src/api/**"]\n---\n# api\n'))
        block = ("## 分页参数要有上限\n\n- **规则**:在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)\n"
                 "- **原因**:不校验会查全表(!12、!15)\n- **严重级别**:重要\n- **反例**:`list(limit)`\n"
                 "- **正例**:`list(Math.min(limit, 100))`\n- **不要报**:内部管理后台接口不限\n<!-- 来源:!12 !15 -->")
        self.assertIn(block, text)
        self.assertLess(text.index("最多 100"), text.index("## 不要报"))
        self.assertIn("## 不要报\n\n- mock 数据里的硬编码金额不用提 <!-- 来源:!12 !15 -->", text)
        self.assertIn("WROTE: .agents/rules/api.md", out)
        self.assertIn("@.agents/rules/", out)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "?? .agents/")   # 只改工作区,没有 add
        # 同主题、范围不同的另起文件;没给正反例的不写那两行
        self.write_rules(work, "--pick", str(n2))
        t2 = Path(self.repo, ".agents/rules/api-2.md").read_text(encoding="utf-8")
        self.assertIn('paths: ["src/api/orders.ts"]', t2)
        self.assertNotIn("反例", t2)
        # 审查时 collect-rules 按 paths 读到它,「不要报」单独放
        self.write(self.repo, {".agents/rules/all.md": "- 全仓库规则\n", ".agents/rules/web.md": "---\nglobs: web/**\n---\n- 前端规则\n"})
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "rules")
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"src/api/orders.ts": "changed\n", "lib/x.ts": "x\n"}, "change")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=self.repo).stdout)
        self.run_script("collect-rules.sh", rd)
        blocks = {b["file"]: b for b in self.load(os.path.join(rd, "rules-index.json"))}
        api = blocks[".agents/rules/api.md"]
        self.assertEqual((api["source"], api["applies_files"]), ("agents-rules", ["src/api/orders.ts"]))
        self.assertIn("mock 数据", api["suppress"])
        self.assertNotIn("mock 数据", api["text"])
        self.assertIn("**不要报**:内部管理后台接口不限", api["text"])        # 每条规则自己的边界留在规则里
        self.assertEqual(sorted(blocks[".agents/rules/all.md"]["applies_files"]), ["lib/x.ts", "src/api/orders.ts"])
        self.assertNotIn(".agents/rules/web.md", blocks)

    def test_long_rule_file_warns(self):
        self.standard_mrs()
        _, work = self.mine()
        import hashlib
        many = [rule(title="规则 %d" % i, rule="必须 " + hashlib.sha1(str(i).encode()).hexdigest()) for i in range(16)]
        _, cands, _ = self.check(work, many)
        self.assertEqual(len(cands), 16)
        out = self.write_rules(work, "--pick", ",".join(str(c["no"]) for c in cands))
        self.assertIn("WARN: .agents/rules/api.md 已有 16 条规则", out)

    def test_needs_login(self):
        r = self.run_script("mine-mrs.sh", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("glab 读不到", r.stderr)
