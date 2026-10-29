"""规则沉淀(improve):拉取 MR 评论、按准入清单核对候选、写入 .agents/rules。GitLab 用 tests/bin/glab 模拟。"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import fixture

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


class GitHubCommentVersions(fixture.Base):
    def test_original_line_uses_original_commit_and_missing_sha_stays_unknown(self):
        repo = self.make_repo("versions", {"src/api.py": "old one\nold two\nold three\n"},
                              remote="https://github.com/example/versions.git")
        old = self.git(repo, "rev-parse", "HEAD")
        new = self.commit(repo, {"src/api.py": "fixed one\nfixed two\nfixed three\n"}, "fix")
        comments = [
            {"id": 1, "line": None, "original_line": 2, "commit_id": new, "original_commit_id": old},
            {"id": 2, "line": 2, "original_line": 2, "commit_id": new, "original_commit_id": old},
            {"id": 3, "line": None, "original_line": 2, "commit_id": new},
        ]
        for comment in comments:
            comment.update(user={"login": "reviewer"}, body="必须校验分页上限", path="src/api.py")
        data = {
            "repos/example/versions/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [
                {"number": 1, "user": {"login": "author"}, "merged_at": ago(1), "updated_at": ago(1), "head": {"sha": new}}],
            "repos/example/versions/pulls/1/comments?per_page=100&page=1": comments,
            "repos/example/versions/issues/1/comments?per_page=100&page=1": [],
        }
        api_file = Path(self.tmp, "github.json")
        api_file.write_text(json.dumps(data))
        bin_dir = Path(self.tmp, "bin")
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!%s\nimport json, os, sys\ndata = json.load(open(os.environ['MOCK_GITHUB']))\n"
                      "path = sys.argv[2]\nif path not in data: sys.exit(1)\nprint(json.dumps(data[path]))\n" % sys.executable)
        gh.chmod(0o755)
        self.env.update(PATH=str(bin_dir) + ":" + self.env["PATH"], MOCK_GITHUB=str(api_file))
        result = self.run_script("mine-mrs.sh", "--platform", "github", cwd=repo)
        work = Path(self.review_dir(result.stdout))
        notes = {n["note"]: n for batch in work.glob("batch-*.json") for n in self.load(batch)}
        self.assertIn("old two", notes[1]["code"])
        self.assertTrue(notes[1]["changed_after"])
        self.assertIn("fixed two", notes[2]["code"])
        self.assertFalse(notes[2]["changed_after"])
        self.assertEqual(notes[3]["code"], "")
        self.assertIsNone(notes[3]["changed_after"])


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

    def run_write(self, work, *args):
        return subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "rules-write.py"), work] + list(args),
                              capture_output=True, text=True, env=self.env)

    def write_rules(self, work, *args):
        r = self.run_write(work, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def feedback(self, rows):
        pdir = os.path.join(self.home, "reviews", "shop")
        os.makedirs(pdir, exist_ok=True)
        Path(pdir, ".repo").write_text("gitlab.example.com/eng/shop\n")
        Path(pdir, "feedback.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

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
        self.assertIn("   标题:分页参数要有上限", out)

    def test_rule_self_checks_must_be_complete(self):
        self.standard_mrs()
        _, work = self.mine()
        partial = {k: v for k, v in CHECKS.items() if k != "generalizable"}
        _, cands, dropped = self.check(work, [rule(rule="接口必须带 trace id", checks=partial),
                                              rule(rule="接口必须记录请求方", checks=dict(CHECKS, no_conflict="yes")),
                                              rule(rule="接口必须限流", checks=None)])
        self.assertEqual(cands, [])
        reasons = {d["rule"]: d["reason"] for d in dropped}
        self.assertIn("自检缺项:能泛化", reasons["接口必须带 trace id"])
        self.assertIn("自检不通过:不冲突", reasons["接口必须记录请求方"])
        self.assertIn("自检缺项", reasons["接口必须限流"])

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
        self.feedback([{"key": "k1", "id": "f1", "verdict": "invalid", "reason": "生成的客户端代码不用管", "file": "src/api/gen/a.ts", "author": "h1", "time": "2026-10-01 10:00"},
                       {"key": "k2", "id": "f2", "verdict": "invalid", "reason": "这是生成的客户端代码,不用管重复", "file": "src/api/gen/b.ts", "author": "h2", "time": "2026-10-02 10:00"}])
        _, work = self.mine()
        self.assertEqual(len(self.load(os.path.join(work, "feedback.json"))), 2)
        sup = {"kind": "suppress", "topic": "api", "rule": "生成的客户端代码里的重复不用提", "paths": ["src/api/gen/**"],
               "evidence": [{"feedback": "k1", "quote": "生成的客户端代码不用管"}, {"feedback": "f2", "quote": "不用管重复"}]}
        out, cands, _ = self.check(work, [sup])
        self.assertEqual([c["kind"] for c in cands], ["suppress"])
        self.assertIn("1. [不要报 · api] 生成的客户端代码里的重复不用提", out)

    def test_feedback_verdict_must_match_kind(self):
        self.standard_mrs()
        self.feedback([{"key": "k1", "verdict": "invalid", "reason": "测试桩里的重复不用管", "file": "a", "author": "h1"},
                       {"key": "k2", "verdict": "valid", "reason": "测试桩里的重复确实该提", "file": "b", "author": "h2"},
                       {"key": "k3", "verdict": "valid", "reason": "接口必须校验签名", "file": "c", "author": "h3"}])
        _, work = self.mine()
        sup = {"kind": "suppress", "topic": "api", "rule": "测试桩里的重复不用提", "paths": ["src/api/**"],
               "evidence": [{"feedback": "k1", "quote": "测试桩里的重复"}, {"feedback": "k2", "quote": "测试桩里的重复"}]}
        bad = rule(rule="接口必须校验签名", evidence=[{"feedback": "k1", "quote": "测试桩"}, {"feedback": "k3", "quote": "接口必须校验签名"}])
        _, cands, dropped = self.check(work, [sup, bad])
        self.assertEqual(cands, [])
        reasons = {d["rule"]: d["reason"] for d in dropped}
        self.assertIn("判断记录 k2(结论与类别不符)", reasons["测试桩里的重复不用提"])
        self.assertIn("判断记录 k1(结论与类别不符)", reasons["接口必须校验签名"])

    def test_suppress_heading_must_be_whole_title(self):
        self.write(self.repo, {"REVIEW.md": "# 规则\n\n## 不要报错后吞异常\n\n- 捕获异常后必须记日志\n\n"
                                            "## 不要报(生成代码)\n\n- src/gen 下的重复\n\n## Do not report:\n\n- vendor 目录\n"})
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "rules")
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"src/api/orders.ts": "changed\n"}, "change")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=self.repo).stdout)
        self.run_script("collect-rules.sh", rd)
        b = {x["file"]: x for x in self.load(os.path.join(rd, "rules-index.json"))}["REVIEW.md"]
        self.assertIn("捕获异常后必须记日志", b["text"])
        self.assertNotIn("捕获异常后必须记日志", b["suppress"])
        self.assertIn("src/gen 下的重复", b["suppress"])
        self.assertIn("vendor 目录", b["suppress"])

    def test_changed_after_ignores_lines_inserted_above(self):
        sha2 = self.commit(self.repo, {"src/api/orders.ts": "import a\nimport b\n\nimport c\n" + "".join(
            ("line %d\n" % i) if i != 5 else "limit checked\n" for i in range(1, 11))}, "insert")
        self.mock_api(LIST, [self.mr(19, sha=sha2)])
        old = note(602, "bob", "这行删掉前要确认调用方")
        old["position"] = {"old_path": "src/api/orders.ts", "old_line": 7, "base_sha": self.sha0, "head_sha": sha2}
        self.mock_api(disc(19), [{"id": "d1", "notes": [note(601, "alice", "这里的写法要统一", "src/api/orders.ts", 2, self.sha0), old]}])
        _, work = self.mine()
        notes = {n["note"]: n for n in self.batch_notes(work)}
        self.assertIs(notes[601]["changed_after"], False)                  # 上方插入几行,原来那几行原样还在
        self.assertIn("line 7", notes[602]["code"])                        # 只有 old_line 时按 base 那一版取代码
        self.assertIsNone(notes[602]["changed_after"])

    # ---------- plan.md ----------

    def sup(self, **kw):
        c = {"kind": "suppress", "topic": "api", "rule": "mock 数据里的硬编码金额不用提", "paths": ["src/api/**"],
             "why": "mock 只在测试里用", "evidence": rule()["evidence"]}
        c.update(kw)
        return c

    def prepare(self, *cands):
        """拉取、核对,返回 work_dir 和 {规则句: 候选编号}。"""
        self.standard_mrs()
        _, work = self.mine()
        _, got, _ = self.check(work, list(cands))
        return work, {c["rule"]: c["no"] for c in got}

    def plan_text(self, work):
        return Path(work, "plan.md").read_text(encoding="utf-8")

    def edit_plan(self, work, n, field, value):
        """改 plan.md 第 n 块的某个字段;value 为 None 时删掉这一行,没有这个字段时加上。"""
        lines = self.plan_text(work).split("\n")
        start = next(i for i, x in enumerate(lines) if x.startswith("## %d. " % n))
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        for i in range(start, end):
            if lines[i].startswith("- **%s**:" % field):
                if value is None:
                    del lines[i]
                else:
                    lines[i] = "- **%s**:%s" % (field, value)
                break
        else:
            if value is not None:
                last = max(i for i in range(start, end) if lines[i].strip())
                lines.insert(last + 1, "- **%s**:%s" % (field, value))
        Path(work, "plan.md").write_text("\n".join(lines), encoding="utf-8")

    def write_all(self, work, nos, dest=None):
        for n in nos:
            self.edit_plan(work, n, "动作", "写入")

    def repo_state(self):
        return (self.git(self.repo, "status", "--porcelain"), Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"))

    def test_plan_is_generated_with_default_pending_and_suggested_dest(self):
        s_dir = self.sup()
        s_glob = self.sup(rule="mock 数据里的随机数不用提", paths=["src/api/**/*.ts"])
        s_gone = self.sup(rule="旧目录里的重复不用提", paths=["src/removed/**"])
        self.standard_mrs()
        _, work = self.mine()
        Path(work, "result-1.json").write_text(json.dumps([rule(), s_dir, s_glob, s_gone], ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "result-1.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n").split("\n")[-1], "PLAN: " + os.path.join(work, "plan.md"))
        self.assertIn("1. [", r.stdout)                                    # 原有的列表输出还在
        cands = {c["rule"]: c for c in self.load(os.path.join(work, "candidates.json"))}
        plan = self.plan_text(work)
        self.assertTrue(plan.startswith("# 规则沉淀计划\n\n- 项目:shop\n- 范围:最近 90 天,5 个 MR\n- 生成:"))
        blocks = {b["cand"]: b for b in self.parse_plan(plan)}
        self.assertEqual(len(blocks), 4)
        self.assertEqual({b["fields"]["动作"] for b in blocks.values()}, {"待定"})
        dest = lambda c: blocks[cands[c["rule"]]["no"]]["fields"]["去向"]
        self.assertEqual(dest(rule()), ".agents/rules/api.md")
        self.assertEqual(dest(s_dir), "src/api/REVIEW.md#不要报")          # 范围恰好是一个目录
        self.assertEqual(dest(s_glob), ".agents/rules/api.md")             # 带别的通配片段
        self.assertEqual(dest(s_gone), ".agents/rules/api.md")             # 目录不在仓库里
        f = blocks[cands[rule()["rule"]]["no"]]["fields"]
        self.assertEqual((f["规则"], f["严重级别"], f["范围"], f["不要报"]), (rule()["rule"], "重要", "src/api/**", "内部管理后台接口不限"))
        self.assertEqual((f["反例"], f["正例"]), ("list(limit)", "list(Math.min(limit, 100))"))
        self.assertTrue(f["候选"].startswith("%d(!12 !15;评审人 2 位;准入 ✓✓✓✓✓✓)" % cands[rule()["rule"]]["no"]), f["候选"])
        sf = blocks[cands[s_dir["rule"]]["no"]]["fields"]
        self.assertNotIn("严重级别", sf)                                    # 不要报类没有这些字段
        self.assertNotIn("不要报", sf)
        # 证据不进 plan.md
        self.assertNotIn("分页参数没校验上限", plan)
        # 再运行 mine-check 不覆盖 plan.md
        Path(work, "plan.md").write_text(plan + "\n手改的\n", encoding="utf-8")
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "result-1.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertIn("plan.md 已存在,没有覆盖", r.stdout)
        self.assertTrue(self.plan_text(work).endswith("手改的\n"))

    def parse_plan(self, text):
        import sys
        sys.path.insert(0, fixture.SCRIPTS)
        import rules_plan
        blocks, errors = rules_plan.parse(text)
        self.assertEqual(errors, [])
        return blocks

    def test_plan_template_uses_the_same_fields(self):
        import sys
        sys.path.insert(0, fixture.SCRIPTS)
        import rules_plan
        text = Path(os.path.dirname(fixture.SCRIPTS), "..", "improve", "templates", "plan.md").read_text(encoding="utf-8")
        blocks = self.parse_plan(text)
        self.assertEqual([b["cand"] for b in blocks], [1, 2, 3])
        used = {name for b in blocks for name in b["fields"]}
        self.assertEqual(used, set(rules_plan.FIELDS))                          # 样例用到全部字段,没有多余的
        self.assertEqual({b["fields"]["动作"] for b in blocks}, {"待定", "跳过", "写入"})
        for b in blocks:
            rules_plan.parse_dest(b["fields"]["去向"])

    def test_plan_rejects_unknown_candidate_and_new_blocks(self):
        work, no = self.prepare(rule())
        n = no[rule()["rule"]]
        extra = ("\n## 9. 自己加的\n\n- **候选**:9(!1)\n- **去向**:.agents/rules/x.md\n- **动作**:写入\n- **规则**:必须这样\n"
                 "- **严重级别**:重要\n- **范围**:src/**\n- **不要报**:无\n"
                 "\n## 10. 没有候选编号\n\n- **去向**:.agents/rules/x.md\n- **动作**:写入\n- **规则**:必须这样\n"
                 "\n## 新增的块\n\n- **动作**:写入\n")
        Path(work, "plan.md").write_text(self.plan_text(work) + extra, encoding="utf-8")
        r = self.run_write(work, "--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR: 块 9 候选: 编号 9 不在 candidates.json 里", r.stdout)
        self.assertIn("ERROR: 块 10 候选: 缺少「候选」字段", r.stdout)
        self.assertIn("ERROR: 块 新增的块 格式: 标题应为「## 序号. 标题」", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents")))

    def test_check_prints_what_would_be_written_and_writes_nothing(self):
        work, no = self.prepare(rule(), self.sup(), rule(rule="在 src/api/** 里,必须记录请求耗时"))
        n1, n2, n3 = no[rule()["rule"]], no[self.sup()["rule"]], no["在 src/api/** 里,必须记录请求耗时"]
        self.write_all(work, [n1, n2])
        before = (self.plan_text(work), self.repo_state())
        r = self.run_write(work, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.rstrip("\n").split("\n")
        self.assertEqual(lines[0].split(" ")[0], "WRITE:")                        # --check 前面不加别的前缀
        self.assertIn("WRITE: .agents/rules/api.md +", r.stdout)
        self.assertIn("WRITE: src/api/REVIEW.md +5行", r.stdout)                   # 新建:标题、空行、章节、空行、这一条
        self.assertIn("PENDING: 1 块还是待定", r.stdout)
        self.assertNotIn("NOTE: 只改了工作区", r.stdout)
        self.assertEqual((self.plan_text(work), self.repo_state()), before)        # 什么都没写,plan.md 也没动
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "src/api/REVIEW.md")))
        # --check 和 --apply 打印的 WRITE 一致
        applied = self.write_rules(work, "--apply")
        self.assertEqual([x for x in lines if x.startswith("WRITE:")], [x for x in applied.split("\n") if x.startswith("WRITE:")])

    def test_any_error_stops_the_whole_batch(self):
        work, no = self.prepare(rule(), self.sup())
        good, bad = no[rule()["rule"]], no[self.sup()["rule"]]
        cases = [
            ("格式:未知字段", "优先级", "高", "块 %d 优先级: 未知字段" % bad),
            ("格式:动作不是四个值之一", "动作", "也许", "块 %d 动作: 应为 待定、写入、跳过、已写入 之一" % bad),
            ("格式:缺必填字段", "范围", None, "块 %d 范围: 缺少这个字段" % bad),
            ("规则句不合格", "规则", "mock 数据" + "金额" * 120, "块 %d 规则: 规则应为一句话" % bad),
            ("去向:不是允许的位置", "去向", "docs/guide.md", "块 %d 去向: 应为 .agents/rules/<主题>.md" % bad),
            ("去向:绝对路径", "去向", "/etc/REVIEW.md#不要报", "块 %d 去向: 应为仓库内的相对路径" % bad),
            ("去向:含 ..", "去向", "../other/REVIEW.md#不要报", "块 %d 去向: 应为仓库内的相对路径" % bad),
            ("去向:目录不存在", "去向", "docs/REVIEW.md#不要报", "块 %d 去向: 目录 docs 不在仓库里" % bad),
            ("去向:没写章节", "去向", "src/api/REVIEW.md", "块 %d 去向: REVIEW.md 后要带章节" % bad),
            ("去向:章节不认识", "去向", "src/api/REVIEW.md#随便", "块 %d 去向: REVIEW.md 后要带章节" % bad),
            ("去向:不要报类去必查", "去向", "src/api/REVIEW.md#每次都要检查", "块 %d 去向: 不要报类只能写进「不要报」" % bad),
            ("范围不合法", "范围", "../x/**", "块 %d 范围:" % bad),
        ]
        for name, field, value, expect in cases:
            with self.subTest(name):
                self.write_all(work, [good, bad])
                self.edit_plan(work, bad, field, value)
                for mode in ("--check", "--apply"):
                    r = self.run_write(work, mode)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("ERROR: " + expect, r.stdout)
                    self.assertNotIn("WRITE:", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents")))      # 好的那块也没写
                self.assertFalse(os.path.exists(os.path.join(self.repo, "src/api/REVIEW.md")))
                self.assertNotIn("已写入", self.plan_text(work))
                self.edit_plan(work, bad, "动作", "待定")
                self.edit_plan(work, good, "动作", "待定")
                self.prepare_reset(work)

    def prepare_reset(self, work):
        """把 plan.md 还原成 mine-check 刚生成的样子(删掉再生成)。"""
        os.remove(os.path.join(work, "plan.md"))
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "result-1.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rule_candidate_errors(self):
        work, no = self.prepare(rule())
        n = no[rule()["rule"]]
        cases = [
            ("规则", "分页参数的上限最好校验一下", "块 %d 规则: 没有写成「必须 / 禁止」的单句" % n),
            ("严重级别", "很严重", "块 %d 严重级别: 严重级别应为重要或轻微" % n),
            ("不要报", "", "块 %d 不要报: 不能为空" % n),
            ("不要报", None, "块 %d 不要报: 缺少这个字段" % n),
            ("去向", "src/api/REVIEW.md#不要报", "块 %d 去向: 规则类只能写进「每次都要检查」" % n),
            ("去向", ".agents/rules/api.md#不要报", "块 %d 去向: 规则类不能写进「不要报」" % n),
        ]
        for field, value, expect in cases:
            with self.subTest(field=field, value=value):
                self.edit_plan(work, n, "动作", "写入")
                self.edit_plan(work, n, field, value)
                r = self.run_write(work, "--apply")
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("ERROR: " + expect, r.stdout)
                self.prepare_reset(work)
        # 不要报类带上规则类的字段
        work, no = self.prepare(self.sup())
        n = no[self.sup()["rule"]]
        self.edit_plan(work, n, "动作", "写入")
        self.edit_plan(work, n, "严重级别", "重要")
        r = self.run_write(work, "--check")
        self.assertIn("ERROR: 块 %d 严重级别: 不要报类不用这个字段" % n, r.stdout)

    def test_apply_writes_only_marked_blocks_and_marks_them_written(self):
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        work, no = self.prepare(rule(), r2, self.sup())
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[self.sup()["rule"]]
        self.edit_plan(work, n1, "动作", "写入")
        self.edit_plan(work, n2, "动作", "跳过")
        self.edit_plan(work, n1, "规则", "在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)")     # 对话里改过的措辞
        before = self.plan_text(work)
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md +", out)
        self.assertIn("PENDING: 1 块还是待定", out)
        self.assertIn("@.agents/rules/", out)
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, ".agents/rules"))), ["api.md"])        # 跳过的、待定的都没写
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('---\npaths: ["src/api/**"]\n---\n# api\n'))
        self.assertIn("## 分页参数要有上限\n\n- **规则**:在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)\n"
                      "- **原因**:不校验会查全表(!12、!15)\n- **严重级别**:重要\n- **反例**:`list(limit)`\n"
                      "- **正例**:`list(Math.min(limit, 100))`\n- **不要报**:内部管理后台接口不限\n<!-- 来源:!12 !15 -->", text)
        # plan.md 只有写过的那块的动作变了
        after = self.plan_text(work)
        self.assertEqual(after, before.replace("- **动作**:写入", "- **动作**:已写入"))        # 只有这一处不同
        self.assertEqual(after.count("已写入"), 1)
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only"), "")                       # 没有 git add
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "?? .agents/")
        # 再执行一次:已写入的忽略,不重复写
        out = self.write_rules(work, "--apply")
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), text)
        self.assertEqual(self.plan_text(work), after)

    def test_same_rule_already_in_file_is_skipped(self):
        work, no = self.prepare(rule(), self.sup())
        n1, n2 = no[rule()["rule"]], no[self.sup()["rule"]]
        self.edit_plan(work, n2, "去向", ".agents/rules/api.md")
        self.write_all(work, [n1, n2])
        self.write_rules(work, "--apply")
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("\n## 不要报\n"), 1)
        # 把它们重新标成写入:同一句规则已在文件里,跳过;同一批里重复的块也只写一次
        self.write_all(work, [n1, n2])
        for n in (n1, n2):
            self.edit_plan(work, n, "动作", "写入")
        out = self.write_rules(work, "--apply")
        self.assertIn("SKIP: 块 %d 已在 .agents/rules/api.md 里" % n1, out)
        self.assertIn("SKIP: 块 %d 已在 .agents/rules/api.md 里" % n2, out)
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), text)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents/rules/api-2.md")))

    def test_rules_file_marker_and_hand_written_files(self):
        odd = rule(title="不要报", rule="在 src/api/** 里,禁止吞掉数据库异常")
        other = rule(rule="在 src/api/** 里,必须给外部调用设置超时")
        work, no = self.prepare(rule(), odd, other)
        n1, n2, n3 = no[rule()["rule"]], no[odd["rule"]], no[other["rule"]]
        # 团队手写的同名文件(没有沉淀标记)不追加,另起 api-2.md
        hand = '---\npaths: ["src/api/**"]\n---\n# API\n\n- 手写规则\n'
        self.write(self.repo, {".agents/rules/api.md": hand})
        self.write_all(work, [n1, n2])
        out = self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), hand)
        self.assertIn("WRITE: .agents/rules/api-2.md", out)
        text = Path(self.repo, ".agents/rules/api-2.md").read_text(encoding="utf-8")
        self.assertIn("## 在 src/api/** 里,禁止吞掉数据库异常\n", text)       # 标题会被当成「不要报」时改用规则句
        # 之后沉淀的同范围规则追加到带标记的 api-2.md,不是再起一个文件
        self.write_all(work, [n3])
        self.write_rules(work, "--apply")
        text2 = Path(self.repo, ".agents/rules/api-2.md").read_text(encoding="utf-8")
        self.assertTrue(text2.startswith(text.rstrip("\n")))
        self.assertIn("在 src/api/** 里,必须给外部调用设置超时", text2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents/rules/api-3.md")))

    def review_work(self, review, dest, kind="rule"):
        """repo 根目录放 review(None 表示删掉),候选 dest 指向 REVIEW.md 的某一节;返回 work_dir 和块号。"""
        if review is None:
            os.remove(os.path.join(self.repo, "REVIEW.md"))
        else:
            self.write(self.repo, {"REVIEW.md": review})
        c = rule() if kind == "rule" else self.sup()
        work, no = self.prepare(c)
        n = no[c["rule"]]
        self.edit_plan(work, n, "动作", "写入")
        self.edit_plan(work, n, "去向", dest)
        return work, n

    def test_review_md_appends_to_existing_section(self):
        review = ("---\nskip: [\"gen/**\"]\n---\n# Review 约定\n\n## 严重程度\n\n只有出错才算重要。\n\n## 每次都要检查\n\n"
                  "<!-- 说明 -->\n- 日期统一用 date.ts。原因:时区。\n- 金额用分。\n    补充一句\n\n## 不要报\n\n- 生成代码\n")
        work, n = self.review_work(review, "REVIEW.md#每次都要检查")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: REVIEW.md +1行", out)
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        line = "- 在 src/api/** 的接口里,必须校验分页参数的上限。原因:不校验会查全表(!12、!15) <!-- 来源:!12 !15 -->"
        self.assertEqual(text, review.replace("    补充一句\n", "    补充一句\n" + line + "\n"))      # 只多一行,头部和其他内容不变
        self.assertTrue(text.startswith("---\nskip: [\"gen/**\"]\n---\n"))
        self.assertIn("WARN: 块 %d 范围 src/api/** 写进 REVIEW.md 后,会对 整个仓库 下的所有文件生效" % n, out)
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_review_md_suppress_goes_after_last_item_of_suppress_section(self):
        review = "# 规则\n\n## 不要报(生成代码)\n\n- 旧的一条\n- 另一条\n\n## 别的\n\n- x\n"
        work, n = self.review_work(review, "REVIEW.md#不要报", kind="suppress")
        self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"),
                         "# 规则\n\n## 不要报(生成代码)\n\n- 旧的一条\n- 另一条\n"
                         "- mock 数据里的硬编码金额不用提(mock 只在测试里用) <!-- 来源:!12 !15 -->\n\n## 别的\n\n- x\n")

    def test_review_md_creates_missing_section_at_end(self):
        review = "---\nskip: []\n---\n# 规则\n\n## 不要报\n\n- 生成代码\n"
        work, n = self.review_work(review, "REVIEW.md#每次都要检查")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: REVIEW.md +4行", out)
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), review +
                         "\n## 每次都要检查\n\n- 在 src/api/** 的接口里,必须校验分页参数的上限。原因:不校验会查全表(!12、!15) <!-- 来源:!12 !15 -->\n")

    def test_review_md_empty_section_gets_item_under_heading(self):
        review = "# 规则\n\n## 每次都要检查\n## 不要报\n\n- x\n"
        work, n = self.review_work(review, "REVIEW.md#每次都要检查")
        self.write_rules(work, "--apply")
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 规则\n\n## 每次都要检查\n\n- 在 src/api/** 的接口里,必须校验分页参数的上限。"))
        self.assertIn("<!-- 来源:!12 !15 -->\n\n## 不要报\n\n- x\n", text)

    def test_review_md_created_in_subdirectory(self):
        work, n = self.review_work("# 规则\n", "src/api/REVIEW.md#每次都要检查")
        self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, "src/api/REVIEW.md").read_text(encoding="utf-8"),
                         "# Review 约定\n\n## 每次都要检查\n\n- 在 src/api/** 的接口里,必须校验分页参数的上限。原因:不校验会查全表(!12、!15) <!-- 来源:!12 !15 -->\n")
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), "# 规则\n")           # 根目录的没动
        # 范围在该目录里,不提醒
        self.assertNotIn("WARN", self.write_rules(work, "--check"))

    def test_review_md_unclosed_frontmatter_refused(self):
        review = "---\nskip: [\"gen/**\"]\n\n## 每次都要检查\n\n- 旧\n"
        work, n = self.review_work(review, "REVIEW.md#每次都要检查")
        for mode in ("--check", "--apply"):
            r = self.run_write(work, mode)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("ERROR: 块 %d 去向: REVIEW.md 的头部(---)没有结束,先修好再写" % n, r.stdout)
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), review)

    def test_source_note_comes_from_candidates_not_plan(self):
        work, no = self.prepare(rule())
        n = no[rule()["rule"]]
        self.edit_plan(work, n, "动作", "写入")
        self.edit_plan(work, n, "候选", "%d(!999 !888;评审人 9 位;准入 ✓)" % n)
        self.edit_plan(work, n, "原因", "改过的原因 <!-- 来源:!777 -->")
        self.edit_plan(work, n, "去向", "REVIEW.md#每次都要检查")
        self.write_rules(work, "--apply")
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        self.assertIn("原因:改过的原因 <!-- 来源:!12 !15 -->", text)
        self.assertNotIn("999", text)
        self.assertNotIn("777", text)

    def test_long_rule_file_warns(self):
        import hashlib
        many = [rule(title="规则 %d" % i, rule="必须 " + hashlib.sha1(str(i).encode()).hexdigest()) for i in range(16)]
        work, no = self.prepare(*many)
        self.assertEqual(len(no), 16)
        self.write_all(work, no.values())
        out = self.write_rules(work, "--check")
        self.assertIn("WARN: .agents/rules/api.md 已有 16 条规则", out)

    def test_written_rules_are_read_in_review(self):
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        work, no = self.prepare(rule(), r2, self.sup())
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[self.sup()["rule"]]
        self.edit_plan(work, n3, "去向", ".agents/rules/api.md")
        self.write_all(work, [n1, n3])
        self.write_rules(work, "--apply")
        # 同主题、范围不同的另起文件;没给正反例的不写那两行
        self.write_all(work, [n2])
        self.write_rules(work, "--apply")
        t = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertLess(t.index("分页参数要有上限"), t.index("## 不要报"))
        self.assertIn("## 不要报\n\n- mock 数据里的硬编码金额不用提(mock 只在测试里用) <!-- 来源:!12 !15 -->", t)
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

    def test_old_flags_are_gone(self):
        work, no = self.prepare(rule())
        for args in (["--pick", "1"], ["--edit", "1=x"], []):
            self.assertNotEqual(self.run_write(work, *args).returncode, 0)

    def test_needs_login(self):
        r = self.run_script("mine-mrs.sh", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("glab 读不到", r.stderr)

    def test_improve_is_command_only(self):
        skill = Path(os.path.dirname(fixture.SCRIPTS), "..", "improve")
        head = skill.joinpath("SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("disable-model-invocation: true", head.splitlines())
        self.assertIn("allow_implicit_invocation: false", skill.joinpath("agents", "openai.yaml").read_text(encoding="utf-8"))
