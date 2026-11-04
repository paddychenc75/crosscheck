"""规则沉淀(improve):拉取 MR 评论、按准入清单核对候选、写入 .agents/rules。GitLab 用 tests/bin/glab 模拟。"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile

import fixture

LIST = "projects/:id/merge_requests?state=merged&per_page=100&page=1"
MR_URL = "https://gitlab.example.com/eng/shop/-/merge_requests/%d"
# 规则来源注释:典型 MR 的网页地址(已采纳优先、编号大的在前)加总数
SRC = "<!-- 来源:%s %s(共 2 个 MR) -->" % (MR_URL % 15, MR_URL % 12)


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
         "why": "不校验会查全表", "severity": "重要", "bad_example": "list(limit)", "good_example": "list(Math.min(limit, 100))",
         "exceptions": "内部管理后台接口不限", "paths": ["src/api/**"],
         "evidence": [{"mr": 12, "note": 101, "quote": "分页参数没校验上限"}, {"mr": 15, "note": 201, "quote": "limit 要加上限"}]}
    c.update(kw)
    return c


def synthesis(work, cands):
    """构造已完成观察和模型判断的测试输入;只测试结构与写入,不冒充模型语义评测。"""
    import copy
    cands = copy.deepcopy(cands)
    obs_ids = []
    for batch in sorted(Path(work).glob("batch-*.json")):
        rows = json.loads(batch.read_text())
        threads = {}
        for row in rows:
            threads.setdefault(row["thread"], row)
        observations = []
        for row in threads.values():
            observations.append({"pattern": "测试约束", "evidence": [{"mr": row["mr"], "note": row["note"], "quote": row["body"]}]})
            obs_ids.append(batch.stem + ":" + str(len(observations)))
        result = {"processed": list(threads), "unprocessed": [], "observations": observations, "excluded": []}
        batch.with_name(batch.name.replace("batch-", "result-")).write_text(json.dumps(result, ensure_ascii=False))
    sources_path = Path(work, "rules-sources.json")
    frozen_sources = json.loads(sources_path.read_text()) if sources_path.exists() else []
    sources = [{"file": row["file"], "quote": row["text"]}
               for row in frozen_sources if not row.get("issues") and row.get("text", "").strip()]
    coverage = {"status": "uncovered" if sources else "unknown",
                "reason": "已核对采集时冻结的规范全文" if sources else "采集没有提供可核对的规范全文",
                "sources": sources}
    for c in cands:
        c.setdefault("observations", obs_ids)
        c.setdefault("review", "测试中的模型判断:证据指向同一约束,边界已说明")
        c.setdefault("coverage", copy.deepcopy(coverage))
        for e in c.get("evidence", []):
            if not e.get("feedback"):
                state = "rejected" if e.get("mr") in (16, 17) else "accepted"
                e.setdefault("assessment", {"state": state, "reason": "测试夹具预置的语义判断", "basis": [{k: e[k] for k in ("mr", "note", "quote")}]})
    feedback = json.loads(Path(work, "snapshot.json").read_text()).get("feedback", [])
    used = {e["feedback"] for c in cands for e in c.get("evidence", []) if e.get("feedback")}
    unused = [f.get("key") or f["id"] for f in feedback if f.get("key") not in used and f.get("id") not in used]
    excluded = [{"observations": [], "feedback": unused, "reason": "本测试未采纳其它判断记录"}] if unused else []
    if not cands and obs_ids:
        excluded.append({"observations": obs_ids, "feedback": [], "reason": "本测试未提候选"})
    Path(work, "synthesis.json").write_text(json.dumps({"candidates": cands, "excluded": excluded}, ensure_ascii=False))


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
            comment.update(user={"login": "reviewer"}, body="必须校验分页上限", path="src/api.py", created_at=ago(3))
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
                "updated_at": updated or ago(days), "web_url": MR_URL % iid,
                "description": desc, "sha": sha or self.sha0}

    def standard_mrs(self):
        self.mock_api(LIST, [self.mr(12, sha=self.sha1), self.mr(15, author="dev2"), self.mr(16, author="dev3"),
                             self.mr(17, author="dev4"), self.mr(18, author="dev5"), self.mr(3, days=200)])
        self.mock_api(disc(12), [
            {"id": "d1", "notes": [
                note(101, "alice", "分页参数没校验上限,传个 100000 就查全表了", "src/api/orders.ts", 5, self.sha0),
                note(102, "dev1", "好的,我加上", "src/api/orders.ts", 5, self.sha0)]},
            {"id": "d2", "notes": [note(103, "gitlab-bot", "Pipeline passed", bot=True), note(104, "alice", "changed the description", system=True)]},
        ])
        self.mock_api(disc(15), [
            {"id": "d3", "notes": [note(201, "bob", "limit 要加上限,不然会查全表", "src/api/users.ts", 1, self.sha0, resolved=True)]},
            {"id": "d4", "notes": [note(202, "project_7_bot_ab12", "自动检查完成", bot=True)]},
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
        synthesis(work, cands)
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "synthesis.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, self.load(os.path.join(work, "candidates.json")), self.load(os.path.join(work, "dropped.json"))

    def run_write(self, work, *args):
        """内容/分流测试每次模拟一轮新的维护,apply 分支显式模拟确认刚预览的指纹。
        草稿跨命令的版本、确认失效和并发边界由 test_rules_draft 直接测 CLI。
        """
        if len(args) != 1 or args[0] not in ("--check", "--apply"):
            return self.run_py("rules-write.py", work, *args, check=False)
        draft_work = tempfile.mkdtemp(prefix="draft-", dir=self.tmp)
        for name in ("meta.txt", "candidates.json", "mined-candidates.md"):
            if Path(work, name).exists():
                shutil.copyfile(Path(work, name), Path(draft_work, name))
        result = self.run_py("rules-write.py", draft_work, "--prepare", check=False)
        if result.returncode:
            return result
        preview = self.run_py("rules-draft.py", draft_work, "--check", check=False)
        if args[0] == "--check" or preview.returncode:
            return preview
        expected = preview.stdout.split("DIGEST: ")[-1].strip()
        return self.run_py("rules-draft.py", draft_work, "--apply", "--expected-digest", expected, check=False)

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
        subjective = rule(rule="接口必须写得清楚", coverage={"status": "covered", "reason": "已有规范覆盖,不应新增", "sources": [{"file": "REVIEW.md", "quote": "日志里不能出现邮箱"}]})
        existing = rule(rule="日志里不能出现邮箱")
        bad_scope = rule(rule="别处必须这样", paths=["../x/**"])
        out, cands, dropped = self.check(work, [good, dup, forged, thin, unfixed, unfixed2, no_must, no_exc, subjective, existing, bad_scope])
        self.assertEqual({c["rule"] for c in cands}, {good["rule"], dup["rule"], existing["rule"]})
        c = next(c for c in cands if c["rule"] == good["rule"])
        self.assertEqual((c["no"], c["mrs"], c["people"], c["severity"]), (1, [12, 15], 2, "重要"))
        self.assertEqual(len(c["evidence"]), 2)                              # 重复的候选合并成一条,同一条评论不重复计
        reasons = {d["rule"]: d["reason"] for d in dropped}
        self.assertIn("证据不足", reasons["所有查询必须加索引"])
        self.assertIn("!12 note 101", reasons["所有查询必须加索引"])
        self.assertIn("证据不足", reasons["写操作必须记审计日志"])
        self.assertIn("证据不足", reasons["接口里必须用事务包起来"])           # 同一 MR 不算两次,编造的 MR 作废
        self.assertIn("明确未采纳", reasons["users 接口必须用事务"])
        self.assertIn("必须 / 禁止", reasons["分页参数的上限最好校验一下"])
        self.assertIn("不要报", reasons["接口必须记录耗时"])
        self.assertIn("已有约束覆盖", reasons["接口必须写得清楚"])
        self.assertNotIn("可能重复或冲突", out)  # 不再用字符串相似度替模型判断
        self.assertIn("范围须为仓库根目录相对 glob", reasons["别处必须这样"])
        # 每条候选一行:编号、规则句、建议去向;准入全满足时不列;自检不输出也不存
        self.assertIn("1. [api · 重要] 在 src/api/** 的接口里,必须校验分页参数的上限 → .agents/rules/api.md\n", out)
        lines = [x for x in out.split("\n") if x[:1].isdigit() and ". [" in x]
        self.assertEqual(len(lines), 3)
        for word in ("准入", "自检", "标题:", "证据:", "案例:"):
            self.assertFalse([x for x in out.split("\n") if x.startswith("   ") and word in x], word)
            self.assertFalse([x for x in lines if word in x], word)
        for key in ("self_checks", "admission"):
            self.assertNotIn(key, c)
        self.assertEqual(c["unmet"], [])

    def test_review_requires_reason_and_checkable_sources(self):
        self.standard_mrs()
        _, work = self.mine()
        _, cands, dropped = self.check(work, [rule(rule="接口必须带 trace id", review=""),
                                              rule(rule="接口必须记录请求方", coverage={"status": "uncovered", "reason": "没查", "sources": []}),
                                              rule(rule="接口必须限流", coverage=None)])
        self.assertEqual(cands, [])
        reasons = {d["rule"]: d["reason"] for d in dropped}
        self.assertIn("缺少语义复核说明", reasons["接口必须带 trace id"])
        self.assertIn("只能标记 unknown", reasons["接口必须记录请求方"])
        self.assertIn("缺少已有约束复核状态", reasons["接口必须限流"])

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
        self.assertEqual((cands[0]["support"], cands[0]["mentions"]), (2, 3))
        self.assertIn("待确认:评审人仅 1 位", out)                             # 只有 alice 一个人:标出来,不丢

    def test_feedback_records_as_suppress_evidence(self):
        self.standard_mrs()
        self.feedback([{"key": "k1", "id": "f1", "verdict": "invalid", "reason": "生成的客户端代码不用管", "file": "src/api/gen/a.ts", "author": "h1", "target": "mr-128", "time": "2026-10-01 10:00"},
                       {"key": "k2", "id": "f2", "verdict": "invalid", "reason": "这是生成的客户端代码,不用管重复", "file": "src/api/gen/b.ts", "author": "h2", "target": "mr-130", "time": "2026-10-02 10:00"}])
        _, work = self.mine()
        self.assertEqual(len(self.load(os.path.join(work, "feedback.json"))), 2)
        sup = {"kind": "suppress", "topic": "api", "rule": "生成的客户端代码里的重复不用提", "paths": ["src/api/gen/**"], "exception_of": None,
               "evidence": [{"feedback": "k1", "quote": "生成的客户端代码不用管"}, {"feedback": "f2", "quote": "不用管重复"}]}
        out, cands, _ = self.check(work, [sup])
        self.assertEqual([c["kind"] for c in cands], ["suppress"])
        self.assertIn("1. [不要报 · api] 生成的客户端代码里的重复不用提 → REVIEW.md#不要报\n", out)     # 目录不在仓库里,去根目录
        # 证据区写判断记录的整段原因(不是 miner 摘的片段),来源写成「审查 <对象> 发现 <id>」,不加链接;准入全满足,不列
        md = self.md_text(work)
        self.assertIn("> 判断记录 2 条\n> - 审查 mr-128 发现 k1「生成的客户端代码不用管」[判为不成立]\n"
                      "> - 审查 mr-130 发现 k2「这是生成的客户端代码,不用管重复」[判为不成立]\n", md)
        self.assertIn("- **候选**:1(判断记录 2 条;评审人 2 位)", md)
        # 来源写成「审查 <对象> 发现 <id>」,与审查时当场定规则的写法一致
        self.edit_block(work, 1, "动作", "写入")
        self.write_rules(work, "--apply")
        self.assertEqual([e["feedback"] for e in cands[0]["evidence"]], ["k1", "k2"])
        # 别名 f2 输入归一到稳定 key k2，候选规则文件和写入来源均使用 canonical key。
        self.assertIn("- 在 src/api/gen/** 范围内,生成的客户端代码里的重复不用提 <!-- 来源:审查 mr-128 发现 k1 审查 mr-130 发现 k2 -->",
                      Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"))           # 范围写进句子

    def test_feedback_verdict_must_match_kind(self):
        self.standard_mrs()
        self.feedback([{"key": "k1", "verdict": "invalid", "reason": "测试桩里的重复不用管", "file": "a", "author": "h1"},
                       {"key": "k2", "verdict": "valid", "reason": "测试桩里的重复确实该提", "file": "b", "author": "h2"},
                       {"key": "k3", "verdict": "valid", "reason": "接口必须校验签名", "file": "c", "author": "h3"}])
        _, work = self.mine()
        sup = {"kind": "suppress", "topic": "api", "rule": "测试桩里的重复不用提", "paths": ["src/api/**"], "exception_of": None,
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

    # ---------- mined-candidates.md ----------

    def sup(self, **kw):
        c = {"kind": "suppress", "topic": "api", "rule": "mock 数据里的硬编码金额不用提", "paths": ["src/api/**"],
             "why": "mock 只在测试里用", "exception_of": None, "evidence": rule()["evidence"]}
        c.update(kw)
        return c

    def prepare(self, *cands):
        """拉取、核对,返回 work_dir 和 {规则句: 候选编号}。"""
        self.standard_mrs()
        _, work = self.mine()
        _, got, _ = self.check(work, list(cands))
        return work, {c["rule"]: c["no"] for c in got}

    def md_text(self, work):
        return Path(work, "mined-candidates.md").read_text(encoding="utf-8")

    def edit_block(self, work, n, field, value):
        """改 mined-candidates.md 第 n 块的某个字段;value 为 None 时删掉这一行,没有这个字段时加上。"""
        lines = self.md_text(work).split("\n")
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
        Path(work, "mined-candidates.md").write_text("\n".join(lines), encoding="utf-8")

    def write_all(self, work, nos, dest=None):
        for n in nos:
            self.edit_block(work, n, "动作", "写入")

    def set_hookup(self, work, value):
        """改 mined-candidates.md 头部字段「接入 AGENTS.md」。"""
        text = self.md_text(work)
        lines = text.split("\n")
        i = next(i for i, x in enumerate(lines) if x.startswith("- **接入 AGENTS.md**:"))
        lines[i] = "- **接入 AGENTS.md**:" + value
        Path(work, "mined-candidates.md").write_text("\n".join(lines), encoding="utf-8")

    def hookup_value(self, work):
        return next(x for x in self.md_text(work).split("\n") if x.startswith("- **接入 AGENTS.md**:")).split(":", 1)[1]

    def repo_state(self):
        return (self.git(self.repo, "status", "--porcelain"), Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"))

    def test_candidates_is_generated_with_default_pending_and_suggested_dest(self):
        s_dir = self.sup()
        s_glob = self.sup(rule="mock 数据里的随机数不用提", paths=["src/api/**/*.ts"])
        s_gone = self.sup(rule="旧目录里的重复不用提", paths=["src/removed/**"])
        s_rule = self.sup(rule="离线导出脚本里的分页上限不用提", exception_of={"file": ".agents/rules/api.md", "title": "分页参数要有上限"})
        self.standard_mrs()
        _, work = self.mine()
        synthesis(work, [rule(), s_dir, s_glob, s_gone, s_rule])
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "synthesis.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n").split("\n")[-1], "RULES_DRAFT: " + os.path.join(work, "rules-draft.md"))
        self.assertIn("1. [", r.stdout)                                    # 原有的列表输出还在
        cands = {c["rule"]: c for c in self.load(os.path.join(work, "candidates.json"))}
        md = self.md_text(work)
        self.assertTrue(md.startswith("# 候选规则\n\n- 项目:shop\n- 扫描:最近 90 天,5 个已合并 MR(这是扫描量;每条候选的支持数见各块「候选」行)\n- 生成:"))
        self.assertEqual(md.split("\n")[5], "- **接入 AGENTS.md**:待定")          # 有规则类候选:头部问要不要接入 AGENTS.md
        blocks = {b["cand"]: b for b in self.parse_md(md)}
        self.assertEqual(len(blocks), 5)
        self.assertEqual({b["fields"]["动作"] for b in blocks.values()}, {"待定"})
        dest = lambda c: blocks[cands[c["rule"]]["no"]]["fields"]["去向"]
        self.assertEqual(dest(rule()), ".agents/rules/api.md")
        self.assertEqual(dest(s_dir), "src/api/REVIEW.md#不要报")          # 通用直觉,范围恰好是一个目录
        self.assertEqual(dest(s_glob), "REVIEW.md#不要报")                 # 带别的通配片段:根目录
        self.assertEqual(dest(s_gone), "REVIEW.md#不要报")                 # 目录不在仓库里:根目录
        self.assertEqual(dest(s_rule), ".agents/rules/api.md#分页参数要有上限")    # 已有规则的例外
        self.assertEqual(cands[s_rule["rule"]]["exception_of"], {"file": ".agents/rules/api.md", "title": "分页参数要有上限"})
        self.assertIn("> 不要报的类别(模型判断):已有规则的例外,.agents/rules/api.md「分页参数要有上限」", md)
        self.assertIn("> 不要报的类别(模型判断):通用直觉", md)
        f = blocks[cands[rule()["rule"]]["no"]]["fields"]
        self.assertEqual((f["规则"], f["严重级别"], f["范围"], f["不要报"]), (rule()["rule"], "重要", "src/api/**", "内部管理后台接口不限"))
        self.assertEqual((f["反例"], f["正例"]), ("list(limit)", "list(Math.min(limit, 100))"))
        self.assertEqual(f["候选"], "%d(2 个 MR;评审人 2 位)" % cands[rule()["rule"]]["no"])      # 准入全满足,不列
        sf = blocks[cands[s_dir["rule"]]["no"]]["fields"]
        self.assertNotIn("严重级别", sf)                                    # 不要报类没有这些字段
        self.assertNotIn("不要报", sf)
        self.assertNotIn("准入", md)
        # 再运行 mine-check 不覆盖 mined-candidates.md
        Path(work, "mined-candidates.md").write_text(md + "\n手改的\n", encoding="utf-8")
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "synthesis.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertIn("mined-candidates.md 已存在,没有覆盖", r.stdout)
        self.assertTrue(self.md_text(work).endswith("手改的\n"))

    def test_suppress_must_say_whether_it_is_a_rule_exception(self):
        self.standard_mrs()
        _, work = self.mine()
        missing = self.sup(rule="缺类别的不用提")
        del missing["exception_of"]
        cases = [(missing, "suppress 须写 exception_of"),
                 (self.sup(rule="类别写错的不用提", exception_of="api"), "exception_of 应为 {file, title} 或 null"),
                 (self.sup(rule="没有标题的不用提", exception_of={"file": ".agents/rules/api.md", "title": " "}), "exception_of 应为 {file, title} 或 null"),
                 (self.sup(rule="指向 REVIEW 的不用提", exception_of={"file": "REVIEW.md", "title": "x"}), "REVIEW.md 后要带章节"),
                 (self.sup(rule="指向别处的不用提", exception_of={"file": "docs/a.md", "title": "x"}), "应为 .agents/rules/<主题>.md")]
        _, got, dropped = self.check(work, [c for c, _ in cases])
        self.assertEqual(got, [])
        reasons = {d["rule"]: d["reason"] for d in dropped}
        for c, expect in cases:
            self.assertIn(expect, reasons[c["rule"]])

    def test_candidates_shows_evidence_and_parse_ignores_it(self):
        self.standard_mrs()
        # MR 19 的评论里带 HTML 注释记号:写进 mined-candidates.md 后不能被当成注释,把后面的行吞掉
        self.mock_api(LIST, [self.mr(12, sha=self.sha1), self.mr(15, author="dev2"), self.mr(16, author="dev3"),
                             self.mr(17, author="dev4"), self.mr(18, author="dev5"), self.mr(19, author="dev6")])
        self.mock_api(disc(19), [{"id": "d8", "notes": [
            note(501, "erin", "limit 必须校验 <!-- 这里不能吞行 --> 再往下", "src/api/users.ts", 1, self.sha0, resolved=True)]}])
        many = rule(rule="在 src/api/** 里,limit 必须有上限且必须校验",
                    evidence=[rule()["evidence"][0], {"mr": 15, "note": 201, "quote": "limit 要加上限"},
                              {"mr": 17, "note": 302, "quote": "limit 也要加上限"},
                              {"mr": 19, "note": 501, "quote": "limit 必须校验 <!-- 这里不能吞行 --> 再往下"}])
        lone = rule(rule="金额相关的计算禁止使用浮点数", bad_example="",
                    evidence=[{"mr": 18, "note": 401, "quote": "别用浮点"}, {"mr": 12, "note": 101, "quote": "分页参数"}])
        _, work = self.mine()
        self.check(work, [many, lone])
        md = self.md_text(work)
        # 统计照旧;典型 MR 已采纳优先、编号大的在前,各一行带链接;未采纳的 !17 排到「另有」里;判断依据不进 md
        self.assertIn("> 提及 4 个 MR:模型判断已采纳 3,未知/结论不一致 0,未采纳 1\n"
                      "> - [!19](%s)「limit 必须校验 <!—— 这里不能吞行 ——> 再往下」[模型判断已采纳]\n"
                      "> - [!15](%s)「limit 要加上限」[模型判断已采纳]\n"
                      "> - [!12](%s)「分页参数没校验上限」[模型判断已采纳]\n"
                      "> 另有 1 个 MR:!17\n" % (MR_URL % 19, MR_URL % 15, MR_URL % 12), md)
        self.assertNotIn("判断依据", md)
        self.assertNotIn("测试夹具预置的语义判断", md)
        self.assertIn("> 提及 2 个 MR:模型判断已采纳 2,未知/结论不一致 0,未采纳 0", md)
        self.assertIn("> 待确认:评审人仅 1 位;待确认:缺反例或正例\n\n- **候选**:", md)
        self.assertEqual(md.count("待确认"), 2)
        # 引用块不影响解析:字段、标题、候选编号和去掉引用块后完全一样,也没有吞掉后面的行
        blocks = self.parse_md(md)
        plain = self.parse_md("\n".join(x for x in md.split("\n") if not x.startswith(">")))
        shape = lambda bs: [(b["n"], b["title"], b["cand"], b["fields"]) for b in bs]
        self.assertEqual(len(blocks), 2)
        self.assertEqual(shape(blocks), shape(plain))
        # 用户改了引用块里的字也不影响;改动作后照常 apply,证据不写进规则文件
        changed_md = md.replace("[模型判断已采纳]", "[随便改]")
        self.assertNotEqual(changed_md, md)
        self.assertIn("[随便改]", changed_md)
        Path(work, "mined-candidates.md").write_text(changed_md, encoding="utf-8")
        self.edit_block(work, 1, "动作", "写入")
        self.edit_block(work, 2, "动作", "跳过")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md", out)
        written = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertIn("limit 必须有上限且必须校验", written)
        self.assertNotIn("再往下", written)
        self.assertNotIn("金额相关", written)
        after = self.md_text(work)
        self.assertEqual(after.count("已写入"), 0)
        self.assertEqual([x for x in after.split("\n") if x.startswith(">")],
                         [x for x in changed_md.split("\n") if x.startswith(">")])

    def test_review_md_suppress_is_read_in_review(self):
        other = self.sup(rule="测试桩里的重复不用提", why="")
        work, no = self.prepare(self.sup(), other)
        for n in no.values():
            self.edit_block(work, n, "动作", "写入")
        preview = self.write_rules(work, "--check")
        self.assertIn("+- mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC, preview)
        self.assertIn("+- 测试桩里的重复不用提 " + SRC, preview)
        self.write_rules(work, "--apply")
        # 审查时 collect-rules 把这一节单独放进 suppress,不当成要检查的规则
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "rules")
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"src/api/orders.ts": "changed\n"}, "change")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=self.repo).stdout)
        self.run_script("collect-rules.sh", rd)
        block = {b["file"]: b for b in self.load(os.path.join(rd, "rules-index.json"))}["src/api/REVIEW.md"]
        self.assertIn("mock 数据里的硬编码金额不用提", block["suppress"])
        self.assertIn("测试桩里的重复不用提", block["suppress"])
        self.assertNotIn("测试桩", block["text"])

    def parse_md(self, text):
        import sys
        sys.path.insert(0, fixture.SCRIPTS)
        import rule_candidates
        blocks, errors = rule_candidates.parse(text)
        self.assertEqual(errors, [])
        return blocks

    def test_candidates_template_uses_the_same_fields(self):
        import sys
        sys.path.insert(0, fixture.SCRIPTS)
        import rule_candidates
        text = Path(os.path.dirname(fixture.SCRIPTS), "..", "improve", "templates", "mined-candidates.md").read_text(encoding="utf-8")
        blocks = self.parse_md(text)
        self.assertEqual([b["cand"] for b in blocks], [1, 2, 3])
        used = {name for b in blocks for name in b["fields"]}
        self.assertEqual(used, set(rule_candidates.FIELDS))                          # 样例用到全部字段,没有多余的
        self.assertEqual({b["fields"]["动作"] for b in blocks}, {"待定", "跳过", "写入"})
        for b in blocks:
            rule_candidates.parse_dest(b["fields"]["去向"])
        fields, errors = rule_candidates.head_fields(text)                             # 头部字段也是同一写法
        self.assertEqual((errors, list(fields)), ([], list(rule_candidates.HEAD_FIELDS)))
        self.assertIn(fields[rule_candidates.HOOKUP][0], rule_candidates.ACTIONS)
        self.assertIn("「## %s」" % rule_candidates.AGENTS_SECTION, text)

    def test_candidates_rejects_unknown_candidate_and_new_blocks(self):
        work, no = self.prepare(rule())
        n = no[rule()["rule"]]
        extra = ("\n## 9. 自己加的\n\n- **候选**:9(!1)\n- **去向**:.agents/rules/x.md\n- **动作**:写入\n- **规则**:必须这样\n"
                 "- **严重级别**:重要\n- **范围**:src/**\n- **不要报**:无\n"
                 "\n## 10. 没有候选编号\n\n- **去向**:.agents/rules/x.md\n- **动作**:写入\n- **规则**:必须这样\n"
                 "\n## 新增的块\n\n- **动作**:写入\n")
        Path(work, "mined-candidates.md").write_text(self.md_text(work) + extra, encoding="utf-8")
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
        before = (self.md_text(work), self.repo_state())
        r = self.run_write(work, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.rstrip("\n").split("\n")
        self.assertEqual(lines[0].split(" ")[0], "WRITE:")                        # --check 前面不加别的前缀
        self.assertIn("WRITE: .agents/rules/api.md +", r.stdout)
        self.assertIn("WRITE: src/api/REVIEW.md +5行", r.stdout)                   # 新建:标题、空行、章节、空行、这一条
        self.assertIn("尚未选入拟写内容", r.stdout)
        self.assertNotIn("NOTE: 只改了工作区", r.stdout)
        self.assertEqual((self.md_text(work), self.repo_state()), before)        # 什么都没写,mined-candidates.md 也没动
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
            ("格式:动作不是四个值之一", "动作", "也许", "块 %d 动作: 应为 待定、写入、跳过 之一" % bad),
            ("格式:缺必填字段", "范围", None, "块 %d 范围: 缺少这个字段" % bad),
            ("规则句不合格", "规则", "mock 数据" + "金额" * 120, "块 %d 规则: 规则应为一句话" % bad),
            ("去向:不是允许的位置", "去向", "docs/guide.md", "块 %d 去向: 应为 .agents/rules/<主题>.md" % bad),
            ("去向:绝对路径", "去向", "/etc/REVIEW.md#不要报", "块 %d 去向: 应为仓库内的相对路径" % bad),
            ("去向:含 ..", "去向", "../other/REVIEW.md#不要报", "块 %d 去向: 应为仓库内的相对路径" % bad),
            ("去向:目录不存在", "去向", "docs/REVIEW.md#不要报", "块 %d 去向: 目录 docs 不在仓库里" % bad),
            ("去向:没写章节", "去向", "src/api/REVIEW.md", "块 %d 去向: REVIEW.md 后要带章节" % bad),
            ("去向:章节不认识", "去向", "src/api/REVIEW.md#随便", "块 %d 去向: REVIEW.md 后要带章节" % bad),
            ("去向:旧的必查章节", "去向", "src/api/REVIEW.md#每次都要检查", "块 %d 去向: REVIEW.md 后要带章节 #不要报" % bad),
            ("去向:规则文件不指明规则", "去向", ".agents/rules/api.md", "块 %d 去向: 不要报类写进 .agents/rules 时要指明是哪条规则的例外" % bad),
            ("去向:规则文件不存在", "去向", ".agents/rules/api.md#分页参数要有上限", "块 %d 去向: .agents/rules/api.md 不存在" % bad),
            ("范围不合法", "范围", "../x/**", "块 %d 范围:" % bad),
            ("范围不在目录里", "范围", "lib/**", "块 %d 范围: lib/** 不在 src/api 里" % bad),
        ]
        for name, field, value, expect in cases:
            with self.subTest(name):
                self.write_all(work, [good, bad])
                self.edit_block(work, bad, field, value)
                for mode in ("--check", "--apply"):
                    r = self.run_write(work, mode)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("ERROR: " + expect, r.stdout)
                    self.assertNotIn("WRITE:", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents")))      # 好的那块也没写
                self.assertFalse(os.path.exists(os.path.join(self.repo, "src/api/REVIEW.md")))
                self.assertNotIn("已写入", self.md_text(work))
                self.edit_block(work, bad, "动作", "待定")
                self.edit_block(work, good, "动作", "待定")
                self.prepare_reset(work)

    def prepare_reset(self, work):
        """把 mined-candidates.md 还原成 mine-check 刚生成的样子(删掉再生成)。"""
        os.remove(os.path.join(work, "mined-candidates.md"))
        r = subprocess.run(["python3", os.path.join(fixture.SCRIPTS, "mine-check.py"), work, os.path.join(work, "synthesis.json")],
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
            ("去向", "src/api/REVIEW.md#不要报", "块 %d 去向: 规则类是代码约束,写进 .agents/rules/<主题>.md" % n),
            ("去向", "src/api/REVIEW.md#每次都要检查", "块 %d 去向: REVIEW.md 后要带章节 #不要报" % n),
            ("去向", ".agents/rules/api.md#不要报", "块 %d 去向: 规则类写成新的规则块,去向不带 #" % n),
        ]
        for field, value, expect in cases:
            with self.subTest(field=field, value=value):
                self.edit_block(work, n, "动作", "写入")
                self.edit_block(work, n, field, value)
                r = self.run_write(work, "--apply")
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("ERROR: " + expect, r.stdout)
                self.prepare_reset(work)
        # 不要报类带上规则类的字段
        work, no = self.prepare(self.sup())
        n = no[self.sup()["rule"]]
        self.edit_block(work, n, "动作", "写入")
        self.edit_block(work, n, "严重级别", "重要")
        r = self.run_write(work, "--check")
        self.assertIn("ERROR: 块 %d 严重级别: 不要报类不用这个字段" % n, r.stdout)

    def test_apply_writes_selected_content_and_keeps_frozen_material(self):
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        work, no = self.prepare(rule(), r2, self.sup())
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[self.sup()["rule"]]
        self.edit_block(work, n1, "动作", "写入")
        self.edit_block(work, n2, "动作", "跳过")
        self.edit_block(work, n1, "规则", "在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)")     # 对话里改过的措辞
        before = self.md_text(work)
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md +", out)
        self.assertIn("尚未选入拟写内容", out)
        self.assertNotIn("SUGGEST:", out)                                   # 仓库里没有 .claude/、.cursor/、AGENTS.md
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, ".agents/rules"))), ["api.md"])        # 跳过的、待定的都没写
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('---\npaths: "src/api/**"\nglobs: "src/api/**"\n---\n# api\n'))   # Claude 读 paths,Cursor 读 globs
        self.assertIn("## 分页参数要有上限\n\n- **规则**:在 src/api/** 的接口里,必须校验分页参数的上限(最多 100)\n"
                      "- **原因**:不校验会查全表\n- **严重级别**:重要\n- **反例**:\n  ```ts\n  list(limit)\n  ```\n"
                      "- **正例**:\n  ```ts\n  list(Math.min(limit, 100))\n  ```\n- **不要报**:内部管理后台接口不限\n" + SRC, text)
        # 内部历史材料保持冻结;完成状态写在统一草稿
        after = self.md_text(work)
        self.assertEqual(after, before)        # 历史材料不再承担完成状态
        self.assertEqual(after.count("已写入"), 0)
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only"), "")                       # 没有 git add
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "?? .agents/")
        # 另起一轮维护:完整内容相同的规则不重复写
        out = self.write_rules(work, "--apply")
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), text)
        self.assertEqual(self.md_text(work), after)

    def test_same_rule_already_in_file_is_skipped(self):
        work, no = self.prepare(rule(), self.sup())
        n1, n2 = no[rule()["rule"]], no[self.sup()["rule"]]
        self.write_all(work, [n1])
        self.write_rules(work, "--apply")
        self.edit_block(work, n2, "去向", ".agents/rules/api.md#分页参数要有上限")
        self.write_all(work, [n2])
        self.write_rules(work, "--apply")
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertNotIn("\n## 不要报\n", text)
        self.assertIn("- **不要报**:内部管理后台接口不限；mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC + "\n", text)
        # 把它们重新标成写入:规则块并进过例外也算已在文件里,跳过;例外已在字段里,也跳过
        self.write_all(work, [n1, n2])
        for n in (n1, n2):
            self.edit_block(work, n, "动作", "写入")
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
        self.assertIn("## 不要报\n\n- **规则**:在 src/api/** 里,禁止吞掉数据库异常\n", text)   # 规则文件没有「不要报」一节,标题原样写
        # 之后沉淀的同范围规则追加到带标记的 api-2.md,不是再起一个文件
        self.write_all(work, [n3])
        self.write_rules(work, "--apply")
        text2 = Path(self.repo, ".agents/rules/api-2.md").read_text(encoding="utf-8")
        self.assertTrue(text2.startswith(text.rstrip("\n")))
        self.assertIn("在 src/api/** 里,必须给外部调用设置超时", text2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents/rules/api-3.md")))

    def review_work(self, review, dest, scope="**"):
        """repo 根目录放 review(None 表示删掉),不要报类候选 dest 指向某个 REVIEW.md#不要报;返回 work_dir 和块号。"""
        if review is None:
            os.remove(os.path.join(self.repo, "REVIEW.md"))
        else:
            self.write(self.repo, {"REVIEW.md": review})
        work, no = self.prepare(self.sup())
        n = no[self.sup()["rule"]]
        self.edit_block(work, n, "动作", "写入")
        self.edit_block(work, n, "去向", dest)
        self.edit_block(work, n, "范围", scope)
        return work, n

    def test_review_md_appends_to_existing_section(self):
        review = ("---\nskip: [\"gen/**\"]\n---\n# Review 约定\n\n## 严重程度\n\n只有出错才算重要。\n\n## 审查重点\n\n"
                  "- 分页出错表现为漏掉最后一页。\n\n## 不要报\n\n<!-- 说明 -->\n- 生成代码\n- 测试桩。\n    补充一句\n\n## 别的\n\n- x\n")
        work, n = self.review_work(review, "REVIEW.md#不要报")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: REVIEW.md +1行", out)
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        line = "- mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC
        self.assertEqual(text, review.replace("    补充一句\n", "    补充一句\n" + line + "\n"))      # 只多一行,头部和其他内容不变
        self.assertTrue(text.startswith("---\nskip: [\"gen/**\"]\n---\n"))
        self.assertNotIn("WARN:", out)
        self.assertNotIn("SUGGEST:", out)                                    # 只写 REVIEW.md 不给接入建议
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_review_md_suppress_goes_after_last_item_of_suppress_section(self):
        review = "# 规则\n\n## 不要报(生成代码)\n\n- 旧的一条\n- 另一条\n\n## 别的\n\n- x\n"
        work, n = self.review_work(review, "REVIEW.md#不要报")
        self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"),
                         "# 规则\n\n## 不要报(生成代码)\n\n- 旧的一条\n- 另一条\n"
                         "- mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC + "\n\n## 别的\n\n- x\n")

    def test_review_md_creates_missing_section_at_end_with_scope_in_sentence(self):
        review = "---\nskip: []\n---\n# 规则\n\n## 审查重点\n\n- 金额\n"
        work, n = self.review_work(review, "REVIEW.md#不要报", scope="src/api/**")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: REVIEW.md +4行", out)
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), review +
                         "\n## 不要报\n\n- 在 src/api/** 范围内,mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC + "\n")

    def test_review_md_empty_section_gets_item_under_heading(self):
        review = "# 规则\n\n## 不要报\n## 别的\n\n- x\n"
        work, n = self.review_work(review, "REVIEW.md#不要报")
        self.write_rules(work, "--apply")
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 规则\n\n## 不要报\n\n- mock 数据里的硬编码金额不用提"))
        self.assertIn(SRC + "\n\n## 别的\n\n- x\n", text)

    def test_review_md_created_in_subdirectory(self):
        work, n = self.review_work("# 规则\n", "src/api/REVIEW.md#不要报", scope="src/api/**")
        self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, "src/api/REVIEW.md").read_text(encoding="utf-8"),
                         "# Review 约定\n\n## 不要报\n\n- mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC + "\n")   # 范围就是这个目录,不写进句子
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), "# 规则\n")           # 根目录的没动
        self.assertNotIn("WARN", self.write_rules(work, "--check"))

    def test_review_md_subdirectory_narrower_scope_goes_into_sentence(self):
        work, n = self.review_work("# 规则\n", "src/api/REVIEW.md#不要报", scope="src/api/mock/**")
        self.write_rules(work, "--apply")
        self.assertIn("- 在 src/api/mock/** 范围内,mock 数据里的硬编码金额不用提",
                      Path(self.repo, "src/api/REVIEW.md").read_text(encoding="utf-8"))

    def test_review_md_subdirectory_wider_scope_refused(self):
        # 范围超出所在目录(包括只是前缀相同的兄弟目录)时整批不写,要改去根目录
        for scope in ("src/web/**", "src/api-old/**", "src/api/**, src/web/**"):
            work, n = self.review_work("# 规则\n", "src/api/REVIEW.md#不要报", scope=scope)
            for mode in ("--check", "--apply"):
                r = self.run_write(work, mode)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("ERROR: 块 %d 范围:" % n, r.stdout)
                self.assertIn("不在 src/api 里", r.stdout)
            self.assertFalse(Path(self.repo, "src/api/REVIEW.md").exists())
            self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), "# 规则\n")

    def test_review_md_unclosed_frontmatter_refused(self):
        review = "---\nskip: [\"gen/**\"]\n\n## 不要报\n\n- 旧\n"
        work, n = self.review_work(review, "REVIEW.md#不要报")
        for mode in ("--check", "--apply"):
            r = self.run_write(work, mode)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("ERROR: 块 %d 去向: REVIEW.md 的头部(---)没有结束,先修好再写" % n, r.stdout)
        self.assertEqual(Path(self.repo, "REVIEW.md").read_text(encoding="utf-8"), review)

    def test_source_note_comes_from_candidates_not_md(self):
        work, n = self.review_work("# 规则\n", "REVIEW.md#不要报")
        self.edit_block(work, n, "候选", "%d(!999 !888;评审人 9 位;准入 ✓)" % n)
        self.edit_block(work, n, "原因", "改过的原因 <!-- 来源:!777 -->")
        self.write_rules(work, "--apply")
        text = Path(self.repo, "REVIEW.md").read_text(encoding="utf-8")
        self.assertIn("(改过的原因) " + SRC, text)
        self.assertNotIn("999", text)
        self.assertNotIn("777", text)

    def test_mr_links_fall_back_to_repo_url_then_number(self):
        """快照里的 MR 没有网页地址:按 origin 的仓库地址拼 GitLab 的 /-/merge_requests/N;仓库地址也没有时只写 !N,不编造。"""
        self.standard_mrs()
        bare =[dict(self.mr(12, sha=self.sha1), web_url=""), dict(self.mr(15, author="dev2"), web_url="javascript:alert(1)")]
        self.mock_api(LIST, bare)
        _, work = self.mine()
        self.assertEqual(self.kv(os.path.join(work, "meta.txt"))["repo_url"], "https://gitlab.example.com/eng/shop")
        _, cands, _ = self.check(work, [rule()])
        self.assertEqual({e["mr"]: e["url"] for e in cands[0]["evidence"]}, {12: MR_URL % 12, 15: MR_URL % 15})
        md = self.md_text(work)
        self.assertIn("> - [!15](%s)「limit 要加上限」[模型判断已采纳]\n> - [!12](%s)" % (MR_URL % 15, MR_URL % 12), md)
        # meta.txt 里没有仓库地址:退回 !N,来源注释同样只写编号
        meta = Path(work, "meta.txt")
        meta.write_text("".join(x for x in meta.read_text().splitlines(True) if not x.startswith("repo_url=")))
        for name in ("mined-candidates.md", "candidates.json", "dropped.json"):
            os.remove(os.path.join(work, name))
        _, cands, _ = self.check(work, [rule()])
        self.assertEqual({e["url"] for e in cands[0]["evidence"]}, {""})
        md = self.md_text(work)
        self.assertIn("> - !15「limit 要加上限」[模型判断已采纳]\n> - !12「分页参数没校验上限」[模型判断已采纳]\n", md)
        self.assertNotIn("](", md.split("\n## ", 1)[1].split("> 语义复核")[0])
        self.edit_block(work, 1, "动作", "写入")
        self.write_rules(work, "--apply")
        self.assertIn("<!-- 来源:!15 !12(共 2 个 MR) -->", Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"))

    def rule_with_exceptions(self, rules_text):
        """先放好一个规则文件,再准备一条指向其中「分页参数要有上限」的例外候选;返回 work_dir 和块号。"""
        self.write(self.repo, {".agents/rules/api.md": rules_text})
        work, no = self.prepare(self.sup())
        n = no[self.sup()["rule"]]
        self.edit_block(work, n, "动作", "写入")
        self.edit_block(work, n, "去向", ".agents/rules/api.md#分页参数要有上限")
        return work, n

    def test_rule_exception_merges_into_its_field(self):
        head = '---\npaths: "src/api/**"\nglobs: "src/api/**"\n---\n# api\n\n'
        block = "## 分页参数要有上限\n\n- **规则**:必须校验分页上限\n- **不要报**:内部管理后台接口不限。\n<!-- 来源:!1 -->\n"
        other = "\n## 别的规则\n\n- **规则**:必须记日志\n- **不要报**:无\n"
        work, n = self.rule_with_exceptions(head + block + other)
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md +0行", out)
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        merged = block.replace("内部管理后台接口不限。", "内部管理后台接口不限；mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC)
        self.assertEqual(text, head + merged + other)                       # 范围等于文件的 paths,不写进句子;别的块不动
        # 字段缺失时在块的最后一个字段后补一行
        work, n = self.rule_with_exceptions(head + "## 分页参数要有上限\n\n- **规则**:必须校验分页上限\n<!-- 来源:!1 -->\n")
        self.edit_block(work, n, "范围", "src/api/mock/**")
        self.write_rules(work, "--apply")
        self.assertIn("- **规则**:必须校验分页上限\n- **不要报**:在 src/api/mock/** 范围内,mock 数据里的硬编码金额不用提",
                      Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"))

    def test_rule_exception_title_missing_or_duplicated_writes_nothing(self):
        cases = [
            ("## 别的规则\n\n- **不要报**:无\n", "去向: .agents/rules/api.md 里没有标题为「分页参数要有上限」的规则"),
            ("## 分页参数要有上限\n\n- **不要报**:无\n\n## 分页参数要有上限\n\n- **不要报**:无\n",
             "去向: .agents/rules/api.md 里标题为「分页参数要有上限」的规则有 2 条,无法确定是哪条的例外"),
            ("```\n## 分页参数要有上限\n```\n", "里没有标题为「分页参数要有上限」的规则"),        # 代码块里的不算标题
        ]
        for text, expect in cases:
            with self.subTest(expect):
                work, n = self.rule_with_exceptions("# api\n\n" + text)
                for mode in ("--check", "--apply"):
                    r = self.run_write(work, mode)
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn("ERROR: 块 %d " % n, r.stdout)
                    self.assertIn(expect, r.stdout)
                    self.assertNotIn("WRITE:", r.stdout)
                self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), "# api\n\n" + text)
                self.assertNotIn("已写入", self.md_text(work))

    def test_apply_prints_hookup_suggestions_without_running_them(self):
        self.write(self.repo, {".claude/settings.json": "{}\n", ".cursor/x": "\n", "AGENTS.md": "# Agents\n"})
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        self.assertNotIn("SUGGEST:", self.write_rules(work, "--check"))     # 预演不给建议
        out = self.write_rules(work, "--apply")
        tips = [x for x in out.split("\n") if x.startswith("SUGGEST: ")]
        self.assertEqual(tips, [                                              # AGENTS.md 不再给建议,由头部字段负责
            "SUGGEST: mkdir -p .claude/rules && ln -s ../../.agents/rules/api.md .claude/rules/api.md",
            "SUGGEST: mkdir -p .cursor/rules && ln -s ../../.agents/rules/api.md .cursor/rules/api.mdc  # Cursor 官方未写明软链接和多余字段的处理,请自行确认",
        ])
        self.assertIn("仅为建议,没有执行", out)
        self.assertIn("请在仓库根目录按需运行", out)
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".claude/rules")))      # 没执行
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".cursor/rules")))
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")    # 接入字段是待定,不写
        # 已经接上的不再提示:按建议建好软链接,再写一条同文件的规则
        for link in (".claude/rules/api.md", ".cursor/rules/api.mdc"):
            os.makedirs(os.path.dirname(os.path.join(self.repo, link)), exist_ok=True)
            os.symlink("../../.agents/rules/api.md", os.path.join(self.repo, link))
        work, no = self.prepare(rule(rule="在 src/api/** 里,必须给外部调用设置超时"))
        self.write_all(work, no.values())
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md", out)
        self.assertNotIn("SUGGEST:", out)

    def test_written_rules_reach_review_once_after_hookup(self):
        # 整条链路:沉淀规则并接入 AGENTS.md → 按建议建软链接 → 沉淀例外和通用不要报 → 提交后 review 采集
        self.write(self.repo, {".claude/settings.json": "{}\n", ".cursor/x": "\n",
                               "REVIEW.md": "# Review 约定\n\n## 严重程度\n\n只有出错才算重要。\n"})
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        self.set_hookup(work, "写入")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: AGENTS.md +5行", out)
        for tip in [x[len("SUGGEST: "):] for x in out.split("\n") if x.startswith("SUGGEST: ")]:
            subprocess.run(["sh", "-c", tip], cwd=self.repo, check=True)
        exception = self.sup(rule="测试夹具里的分页参数不用校验上限",
                             exception_of={"file": ".agents/rules/api.md", "title": "分页参数要有上限"})
        generic = self.sup()
        work, no = self.prepare(exception, generic)
        md = self.md_text(work)
        self.assertIn("- **去向**:.agents/rules/api.md#分页参数要有上限", md)       # 默认去向按 exception_of 给
        self.assertIn("- **去向**:src/api/REVIEW.md#不要报", md)                 # 通用直觉,范围正好是一个目录
        self.write_all(work, no.values())
        self.write_rules(work, "--apply")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "rules")
        tracked = self.git(self.repo, "ls-files", "-s", ".claude/rules", ".cursor/rules").split("\n")
        self.assertEqual(sorted(x.split()[0] + " " + x.split("\t")[1] for x in tracked),
                         ["120000 .claude/rules/api.md", "120000 .cursor/rules/api.mdc"])          # 两个软链接已提交
        self.commit(self.repo, {"src/api/orders.ts": "changed\n"}, "change")
        rd = self.review_dir(self.run_script("collect-diff.sh", "HEAD~1..HEAD", cwd=self.repo).stdout)
        self.run_script("collect-rules.sh", rd)
        units = self.load(os.path.join(rd, "rules-index.json"))
        files = [u["file"] for u in units]
        self.assertIn(".agents/rules/api.md", files)
        self.assertFalse([f for f in files if f.startswith((".claude/rules/", ".cursor/rules/"))], files)   # 软链接那两份不重复读
        api = next(u for u in units if u["file"] == ".agents/rules/api.md")
        self.assertIn("内部管理后台接口不限；测试夹具里的分页参数不用校验上限", api["text"])   # 例外随规则一起交给角色
        sub = next(u for u in units if u["file"] == "src/api/REVIEW.md")
        self.assertIn("mock 数据里的硬编码金额不用提", "\n".join(sub["suppress"]) if isinstance(sub["suppress"], list) else sub["suppress"])
        self.assertNotIn("mock 数据里的硬编码金额不用提", sub["text"])            # 不要报不当作要检查的规则
        # AGENTS.md 里的链接不让同一条规则读两次、也不改变它的范围
        self.assertEqual(files.count(".agents/rules/api.md"), 1, files)
        self.assertEqual(api["scope"], "src/api/**")
        self.assertEqual(sum("分页参数要有上限" in u["text"] for u in units), 1)
        agents = next(u for u in units if u["file"] == "AGENTS.md")
        self.assertEqual(agents["text"].count("](.agents/rules/api.md)"), 1)
        self.assertNotIn("- **规则**:", agents["text"])                         # 只有链接行,没有把规则正文并进来
        sys.path.insert(0, fixture.SCRIPTS)
        import project_rules
        mined = [r for r in project_rules.mining_sources(self.repo) if os.path.realpath(r["source_path"]) == os.path.realpath(os.path.join(self.repo, ".agents/rules/api.md"))]
        self.assertEqual([(r["file"], r["patterns"], r.get("referenced_by")) for r in mined], [(".agents/rules/api.md", ["src/api/**"], None)])

    AGENTS_INTRO = "改动对应路径前先打开链接的规则文件读完并遵守。"

    def test_agents_md_hookup_creates_file_and_is_idempotent(self):
        work, no = self.prepare(rule())
        md = self.md_text(work)
        head = md.split("\n## ")[0]
        self.assertIn("\n- 生成:", head)
        self.assertIn("\n- **接入 AGENTS.md**:待定\n", head)                     # 头部字段在头部,不进候选块
        self.assertNotIn("接入 AGENTS.md", md.split("\n## ", 1)[1].split("\n>")[0])
        self.write_all(work, no.values())
        self.set_hookup(work, "写入")
        before = (self.md_text(work), self.repo_state())
        r = self.run_write(work, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WRITE: AGENTS.md +5行\n", r.stdout)
        self.assertIn("+++ AGENTS.md\n", r.stdout)
        self.assertIn("+## 代码约束\n+\n+" + self.AGENTS_INTRO + "\n+\n+- [api](.agents/rules/api.md):改 src/api/** 时先读这个文件并遵守\n", r.stdout)
        self.assertNotIn("PENDING:", r.stdout)
        self.assertEqual((self.md_text(work), self.repo_state()), before)          # 预演不写
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        out = self.write_rules(work, "--apply")
        self.assertEqual([x for x in out.split("\n") if x.startswith("WRITE:")][-1], "WRITE: AGENTS.md +5行")
        agents = Path(self.repo, "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents, "## 代码约束\n\n" + self.AGENTS_INTRO + "\n\n- [api](.agents/rules/api.md):改 src/api/** 时先读这个文件并遵守\n")
        self.assertEqual(self.hookup_value(work), "写入")
        self.assertEqual(self.md_text(work), before[0])     # 不修改历史来源材料
        self.assertNotIn("SUGGEST:", out)
        # 新一轮维护识别相同内容,不重复写
        out = self.write_rules(work, "--apply")
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), agents)
        # 再选同样的内容:规则与索引都不重复写
        self.write_all(work, no.values())
        self.set_hookup(work, "写入")
        out = self.write_rules(work, "--apply")
        self.assertNotIn("WRITE: AGENTS.md", out)
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), agents)
        self.assertEqual(self.hookup_value(work), "写入")

    def test_agents_md_hookup_only_appends_to_its_section(self):
        original = ("# Agents\n\n用 pnpm 跑测试。\n\n## 代码约束\n\n团队已有的说明。\n\n"
                    "- [api](./.agents/rules/api.md):手写的接入行\n\n## 其他\n\n结尾 [文档](docs/a.md)\n")
        self.write(self.repo, {"AGENTS.md": original})
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        exc = self.sup(rule="测试夹具里的分页参数不用校验上限")
        work, no = self.prepare(rule(), r2, exc)
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[exc["rule"]]
        self.write_all(work, [n1, n2])
        self.set_hookup(work, "写入")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md", out)
        self.assertIn("WRITE: .agents/rules/api-2.md", out)                     # 同主题范围不同,另起文件
        self.assertIn("不能安全同步的手写或重复引用", out)  # ./ 写法也算同一个文件
        self.assertIn("WRITE: AGENTS.md +1行", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), original.replace(
            "\n## 其他", "\n- [api-2](.agents/rules/api-2.md):改 src/api/orders.ts 时先读这个文件并遵守\n## 其他"))
        self.assertEqual(self.hookup_value(work), "写入")
        # 不要报类并进已有规则:那个文件也算本次确认的规则文件,已有链接,不重复写
        self.edit_block(work, n3, "去向", ".agents/rules/api.md#分页参数要有上限")
        self.write_all(work, [n3])
        self.set_hookup(work, "写入")
        after = Path(self.repo, "AGENTS.md").read_text(encoding="utf-8")
        out = self.write_rules(work, "--apply")
        self.assertIn("不能安全同步的手写或重复引用", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), after)

    def test_agents_md_hookup_section_created_at_end_of_existing_file(self):
        self.write(self.repo, {"AGENTS.md": "# Agents\n\n用 pnpm 跑测试。\n\n\n"})
        work, no = self.prepare(rule(paths=["**"], rule="在所有接口里,必须校验分页参数的上限"))
        self.write_all(work, no.values())
        self.set_hookup(work, "写入")
        self.write_rules(work, "--apply")
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"),
                         "# Agents\n\n用 pnpm 跑测试。\n\n\n\n## 代码约束\n\n" + self.AGENTS_INTRO
                         + "\n\n- [api](.agents/rules/api.md):改 所有文件 时先读这个文件并遵守\n")

    def test_agents_md_hookup_pending_or_skipped_writes_nothing(self):
        self.write(self.repo, {"AGENTS.md": "# Agents\n"})
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        out = self.write_rules(work, "--check")                                  # 块都标了写入,只剩这个字段待定
        self.assertIn("PENDING: 接入 AGENTS.md 尚未决定\n", out)
        self.assertNotIn("WRITE: AGENTS.md", out)
        out = self.write_rules(work, "--apply")
        self.assertIn("PENDING: 接入 AGENTS.md 尚未决定", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")
        self.assertEqual(self.hookup_value(work), "待定")
        # 块待定、字段也待定:两样都算上
        work, no = self.prepare(rule(rule="在 src/api/** 里,必须给外部调用设置超时"))
        self.assertIn("PENDING: 接入 AGENTS.md 尚未决定\n", self.write_rules(work, "--check"))
        # 跳过:不写,也不再提示
        self.write_all(work, no.values())
        self.set_hookup(work, "跳过")
        out = self.write_rules(work, "--apply")
        self.assertNotIn("PENDING:", out)
        self.assertNotIn("WRITE: AGENTS.md", out)
        self.assertEqual(Path(self.repo, "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")
        self.assertEqual(self.hookup_value(work), "跳过")

    def test_agents_md_hookup_invalid_value_or_block_error_writes_nothing(self):
        work, no = self.prepare(rule(), self.sup())
        n1, n2 = no[rule()["rule"]], no[self.sup()["rule"]]
        self.write_all(work, [n1, n2])
        for value, expect in (("也许", "ERROR: 头部 接入 AGENTS.md: 应为 待定、写入、跳过 之一,现在是「也许」"),
                              ("", "ERROR: 头部 接入 AGENTS.md: 应为 待定、写入、跳过 之一,现在是「」")):
            with self.subTest(value=value):
                self.set_hookup(work, value)
                for mode in ("--check", "--apply"):
                    r = self.run_write(work, mode)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn(expect, r.stdout)
                    self.assertNotIn("WRITE:", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents")))     # 整批不写
                self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
                self.assertFalse(os.path.exists(os.path.join(self.repo, "src/api/REVIEW.md")))
                self.assertNotIn("已写入", self.md_text(work))
        # 头部写了不认识的字段、或字段写了两遍
        self.set_hookup(work, "写入")
        Path(work, "mined-candidates.md").write_text(self.md_text(work).replace("- **接入 AGENTS.md**:写入", "- **接入 AGENTS.md**:写入\n- **接入 CLAUDE.md**:写入\n- **接入 AGENTS.md**:跳过"), encoding="utf-8")
        r = self.run_write(work, "--check")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("ERROR: 头部 接入 CLAUDE.md: 未知的头部字段", r.stdout)
        self.assertIn("ERROR: 头部 接入 AGENTS.md: 字段重复", r.stdout)
        # 字段合法,但块有 ERROR:AGENTS.md 也不写
        self.prepare_reset(work)
        self.write_all(work, [n1])
        self.set_hookup(work, "写入")
        self.edit_block(work, n1, "严重级别", "很严重")
        for mode in ("--check", "--apply"):
            r = self.run_write(work, mode)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertNotIn("WRITE:", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        self.assertEqual(self.hookup_value(work), "写入")

    def test_candidates_without_rule_candidates_has_no_hookup_field(self):
        work, no = self.prepare(self.sup())
        self.assertNotIn("接入 AGENTS.md", self.md_text(work))
        self.write_all(work, no.values())
        out = self.write_rules(work, "--apply")
        self.assertNotIn("PENDING:", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))

    def test_agents_md_hookup_marked_but_no_rule_file_this_run(self):
        work, no = self.prepare(rule(), self.sup())
        self.write_all(work, [no[self.sup()["rule"]]])           # 只写去 REVIEW.md 的不要报
        self.set_hookup(work, "写入")
        out = self.write_rules(work, "--apply")
        self.assertIn("PENDING: 接入 AGENTS.md 已选择,但还没有选入规则文件", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        self.assertEqual(self.hookup_value(work), "写入")                       # 没接上,不改成已写入

    def test_long_rule_file_warns(self):
        import hashlib
        # 示例代码里的 ## 不算规则块
        many = [rule(title="规则 %d" % i, rule="必须 " + hashlib.sha1(str(i).encode()).hexdigest(), bad_example="## 不是标题\nx()") for i in range(16)]
        work, no = self.prepare(*many)
        self.assertEqual(len(no), 16)
        self.write_all(work, no.values())
        out = self.write_rules(work, "--check")
        self.assertIn("WARN: .agents/rules/api.md 已有 16 条规则", out)

    def test_examples_are_code_blocks_in_candidates_and_rule_file(self):
        bad = "const s = `tpl`\n## 不要报\n<!-- 代码里的注释 -->\n\n  indented()"
        good = "```\nfenced inside\n```"
        r = rule(bad_example=bad, good_example=good)
        work, no = self.prepare(r, self.sup())
        n, ns = no[r["rule"]], no[self.sup()["rule"]]
        md = self.md_text(work)
        # 候选规则文件里是围栏代码块:语言来自证据文件后缀,围栏比内容里的反引号串长,代码原样
        self.assertIn("- **反例**:\n  ```ts\n  const s = `tpl`\n  ## 不要报\n  <!-- 代码里的注释 -->\n\n    indented()\n  ```\n", md)
        self.assertIn("- **正例**:\n  ````ts\n  ```\n  fenced inside\n  ```\n  ````\n", md)
        block = {b["cand"]: b for b in self.parse_md(md)}[n]
        self.assertEqual((block["fields"]["反例"], block["fields"]["正例"]), (bad, good))
        self.assertEqual(block["lang"], {"反例": "ts", "正例": "ts"})
        self.assertEqual(block["fields"]["不要报"], "内部管理后台接口不限")          # 代码块后面的字段照常解析
        # 用户把语言标记改成别的,写入时跟着变;例外并进规则块的「不要报」字段,不碰代码里的 ## 不要报
        Path(work, "mined-candidates.md").write_text(md.replace("  ````ts\n", "  ````js\n"), encoding="utf-8")
        self.write_all(work, [n])
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE: .agents/rules/api.md", out)
        self.edit_block(work, ns, "去向", ".agents/rules/api.md#分页参数要有上限")
        self.write_all(work, [ns])
        self.write_rules(work, "--apply")
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertIn("- **反例**:\n  ```ts\n  const s = `tpl`\n  ## 不要报\n  <!-- 代码里的注释 -->\n\n    indented()\n  ```\n"
                      "- **正例**:\n  ````js\n  ```\n  fenced inside\n  ```\n  ````\n"
                      "- **不要报**:内部管理后台接口不限；mock 数据里的硬编码金额不用提(mock 只在测试里用) " + SRC + "\n" + SRC, text)
        self.assertEqual((text.count("\n  ## 不要报\n"), text.count("\n## 不要报\n")), (1, 0))   # 只有代码里那一个,没有单独的章节
        # 再写一次:规则块和例外都已在文件里,跳过
        self.write_all(work, [n, ns])
        out = self.write_rules(work, "--apply")
        self.assertNotIn("WRITE:", out)
        self.assertEqual(Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"), text)
        # 审查时 collect-rules 读这个文件:规则文件没有「不要报」一节,整份都是规则正文,例外留在规则自己的字段里
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "rules")
        self.git(self.repo, "switch", "-qc", "feature")
        self.commit(self.repo, {"src/api/orders.ts": "changed\n"}, "change")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=self.repo).stdout)
        self.run_script("collect-rules.sh", rd)
        api = {b["file"]: b for b in self.load(os.path.join(rd, "rules-index.json"))}[".agents/rules/api.md"]
        self.assertIn("  ## 不要报\n", api["text"])
        self.assertIn("fenced inside", api["text"])
        self.assertIn("**不要报**:内部管理后台接口不限；mock 数据里的硬编码金额", api["text"])
        self.assertEqual(api["suppress"], "")

    def test_single_line_example_and_broken_fence(self):
        r = rule(bad_example="", good_example="")
        work, no = self.prepare(r)
        n = no[r["rule"]]
        self.assertNotIn("反例", self.md_text(work).split("\n> ")[0].split("## 1.")[1])
        # 用户在对话里补一行示例:写在同一行也认,写进文件时仍是代码块
        self.edit_block(work, n, "反例", "`list(limit)`")
        self.write_all(work, [n])
        out = self.write_rules(work, "--check")
        self.assertIn("+- **反例**:\n+  ```\n+  list(limit)\n+  ```\n", out)
        # 围栏没有结束:报 ERROR,什么都不写
        md = self.md_text(work).replace("- **反例**:`list(limit)`", "- **反例**:\n  ```ts\n  list(limit)")
        Path(work, "mined-candidates.md").write_text(md, encoding="utf-8")
        res = self.run_write(work, "--check")
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("ERROR: 块 %d 反例: 代码块没有结束的围栏" % n, res.stdout)
        # 代码块不跟在示例字段后面也是 ERROR
        md = self.md_text(work).replace("- **动作**:写入\n", "- **动作**:写入\n  ```\n  x\n  ```\n").replace("  list(limit)", "  list(limit)\n  ```")
        Path(work, "mined-candidates.md").write_text(md, encoding="utf-8")
        res = self.run_write(work, "--check")
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("代码块只能紧跟在「反例」或「正例」字段行后面", res.stdout)

    def test_unmet_items_warn_when_marked_write(self):
        lone = rule(rule="金额相关的计算禁止使用浮点数", bad_example="",
                    evidence=[{"mr": 18, "note": 401, "quote": "别用浮点"}, {"mr": 12, "note": 101, "quote": "分页参数"}])
        work, no = self.prepare(rule(), lone)
        n1, n2 = no[rule()["rule"]], no[lone["rule"]]
        self.write_all(work, [n1, n2])
        out = self.write_rules(work, "--check")
        self.assertIn("PENDING: 候选 %d:待确认:评审人仅 1 位\n" % n2, out)
        self.assertNotIn("PENDING: 候选 %d:待确认" % n1, out)
        self.assertIn("WRITE: .agents/rules/api.md", out)                          # 只提醒,不拦
        out = self.write_rules(work, "--apply")
        self.assertIn("PENDING: 候选 %d:待确认" % n2, out)
        self.assertIn("金额相关的计算禁止使用浮点数", Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8"))

    def test_reason_must_not_carry_mr_numbers(self):
        r = rule(why="评审明确要求统一校验(!12、!15)")
        work, no = self.prepare(r)
        n = no[r["rule"]]
        self.assertIn("待确认:原因里写了 MR 号", self.md_text(work))
        self.write_all(work, [n])
        res = self.run_write(work, "--check")
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("ERROR: 块 %d 原因: 不要写 MR 号(!12)" % n, res.stdout)
        self.edit_block(work, n, "原因", "评审明确要求统一校验,见 PR #15")
        res = self.run_write(work, "--check")
        self.assertIn("ERROR: 块 %d 原因: 不要写 MR 号(#15)" % n, res.stdout)
        self.edit_block(work, n, "原因", "评审明确要求统一校验;颜色 #fff 和 a/b#1 不算")
        out = self.write_rules(work, "--check")
        self.assertIn("WRITE: .agents/rules/api.md", out)
        self.assertIn("原因**:评审明确要求统一校验;颜色 #fff 和 a/b#1 不算", out)

    def test_rule_punctuation_header_and_severity_reason(self):
        r = rule(rule="在 src/api/** 的接口里,必须校验分页参数的上限。", severity_reason="不限会一次查出全表,是实际缺陷")
        work, no = self.prepare(r)
        self.assertEqual(list(no), ["在 src/api/** 的接口里,必须校验分页参数的上限"])      # 末尾标点去掉
        md = self.md_text(work)
        self.assertIn("- **规则**:在 src/api/** 的接口里,必须校验分页参数的上限\n", md)
        self.assertIn("> 严重级别依据(模型判断):不限会一次查出全表,是实际缺陷\n", md)
        n = no["在 src/api/** 的接口里,必须校验分页参数的上限"]
        self.edit_block(work, n, "规则", "在 src/api/** 的接口里,必须校验分页参数的上限;")     # 用户改措辞又带了标点
        self.write_all(work, [n])
        self.write_rules(work, "--apply")
        text = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertIn("- **规则**:在 src/api/** 的接口里,必须校验分页参数的上限\n", text)
        self.assertNotIn("严重级别依据", text)                                      # 依据只在候选规则文件里给人看

    def test_written_rules_are_read_in_review(self):
        r2 = rule(title="金额用分", rule="在订单接口里,返回的金额必须统一用分", paths=["src/api/orders.ts"], bad_example="", good_example="")
        work, no = self.prepare(rule(), r2, self.sup())
        n1, n2, n3 = no[rule()["rule"]], no[r2["rule"]], no[self.sup()["rule"]]
        self.write_all(work, [n1])
        self.write_rules(work, "--apply")
        self.edit_block(work, n3, "去向", ".agents/rules/api.md#分页参数要有上限")
        self.write_all(work, [n3])
        self.write_rules(work, "--apply")
        # 同主题、范围不同的另起文件;没给正反例的不写那两行
        self.write_all(work, [n2])
        self.write_rules(work, "--apply")
        t = Path(self.repo, ".agents/rules/api.md").read_text(encoding="utf-8")
        self.assertNotIn("## 不要报", t)
        t2 = Path(self.repo, ".agents/rules/api-2.md").read_text(encoding="utf-8")
        self.assertIn('paths: "src/api/orders.ts"\nglobs: "src/api/orders.ts"\n', t2)
        self.assertNotIn("反例", t2)
        # 审查时 collect-rules 按 paths 读到它,例外留在规则自己的「不要报」字段
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
        self.assertEqual(api["suppress"], "")
        self.assertIn("**不要报**:内部管理后台接口不限；mock 数据", api["text"])        # 每条规则自己的边界留在规则里
        self.assertEqual(sorted(blocks[".agents/rules/all.md"]["applies_files"]), ["lib/x.ts", "src/api/orders.ts"])
        self.assertNotIn(".agents/rules/web.md", blocks)

    def test_unaccepted_mr_cannot_complete_two_cases(self):
        self.standard_mrs()
        _, work = self.mine()
        candidate = rule(evidence=[rule()["evidence"][0], {"mr": 17, "note": 302, "quote": "limit 也要加上限"}])
        _, got, dropped = self.check(work, [candidate])
        self.assertEqual(got, [])
        self.assertIn("接受案例不足", dropped[0]["reason"])

    def test_unknown_acceptance_is_not_counted_as_true_or_false(self):
        self.standard_mrs()
        _, work = self.mine()
        evidence = [rule()["evidence"][0], {"mr": 17, "note": 302, "quote": "limit 也要加上限",
                    "assessment": {"state": "unknown", "reason": "缺少落实对应关系", "basis": []}}]
        _, got, _ = self.check(work, [rule(evidence=evidence)])
        self.assertEqual((got[0]["support"], got[0]["mentions"], got[0]["unknown"]), (1, 2, 1))
        self.assertEqual({e["mr"]: e["state"] for e in got[0]["evidence"]}, {12: "模型判断已采纳", 17: "未知"})
        self.assertEqual(got[0]["unmet"], ["待确认:模型判断已采纳的只有 1 个"])

    def test_wontfix_and_feedback_context(self):
        self.standard_mrs()
        self.feedback([dict(key=k, verdict="wontfix", reason="当前暂不修", root_cause="转换前提",
                            failure_scenario="条件变化", target="branch-a", context_hash="private", time="private")
                       for k in ("k1", "k2")])
        _, work = self.mine()
        records = self.load(Path(work, "feedback.json"))
        self.assertEqual(records[0]["root_cause"], "转换前提")
        self.assertEqual(records[0]["failure_scenario"], "条件变化")
        self.assertEqual(records[0]["target"], "branch-a")
        self.assertNotIn("context_hash", records[0])
        self.assertNotIn("time", records[0])
        _, got, dropped = self.check(work, [self.sup(evidence=[{"feedback": k, "quote": "暂不修"} for k in ("k1", "k2")])])
        self.assertEqual(got, [])
        self.assertIn("结论与类别不符", dropped[0]["reason"])

    def test_similar_rules_keep_scopes_exceptions_and_negation(self):
        candidates = [rule(), rule(paths=["src/jobs/**"]), rule(exceptions="仅测试桩例外"),
                      rule(rule=rule()["rule"].replace("必须", "禁止"))]
        work, _ = self.prepare(*candidates)
        self.assertEqual(len(self.load(Path(work, "candidates.json"))), 4)
        self.assertIn('"patterns"', Path(work, "existing-rules.txt").read_text())

    def test_candidates_freezes_candidates_and_rejects_tampering(self):
        first, second = rule(), rule(paths=["src/jobs/**"])
        work, _ = self.prepare(first, second)
        original = {name: Path(work, name).read_bytes() for name in ("mined-candidates.md", "candidates.json", "dropped.json")}
        self.check(work, [second, first])
        self.assertEqual({name: Path(work, name).read_bytes() for name in original}, original)
        synthesis(work, [second])
        r = self.run_py("mine-check.py", work, str(Path(work, "synthesis.json")), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual({name: Path(work, name).read_bytes() for name in original}, original)
        Path(work, "candidates.json").write_text(json.dumps(list(reversed(self.load(Path(work, "candidates.json"))))))
        self.assertEqual(self.run_write(work, "--apply").returncode, 2)

    def test_rule_kind_never_goes_into_review_md(self):
        work, no = self.prepare(rule())
        n = no[rule()["rule"]]
        self.write_all(work, [n])
        for dest in ("REVIEW.md#不要报", "src/api/REVIEW.md#不要报"):
            self.edit_block(work, n, "去向", dest)
            r = self.run_write(work, "--check")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("REVIEW.md 只收不要报类", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "src/api/REVIEW.md")))

    def test_symlink_targets_outside_and_git_metadata_are_refused(self):
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        dest = Path(self.repo, ".agents/rules")
        dest.parent.mkdir()
        outside = Path(self.tmp, "outside")
        outside.mkdir()
        for target in (outside, Path(self.repo, ".git")):
            dest.symlink_to(target, target_is_directory=True)
            for mode in ("--check", "--apply"):
                r = self.run_write(work, mode)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            dest.unlink()
        work, no = self.prepare(self.sup())
        self.write_all(work, no.values())
        target = outside / "review"
        target.write_text("unchanged")
        Path(self.repo, "src/api/REVIEW.md").symlink_to(target)
        self.assertEqual(self.run_write(work, "--apply").returncode, 2)
        self.assertEqual(target.read_text(), "unchanged")

    def test_history_source_and_target_binding_survive_final_apply(self):
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        self.run_py("rules-write.py", work, "--prepare")
        preview = self.run_py("rules-draft.py", work, "--check").stdout
        expected = preview.split("DIGEST: ")[-1].strip()
        sources = Path(work, "candidates.json")
        original = sources.read_bytes()
        sources.write_text("[]")
        failed = self.run_py("rules-draft.py", work, "--apply", "--expected-digest", expected, check=False)
        self.assertIn("来源材料已变化", failed.stdout)
        self.assertFalse(Path(self.repo, ".agents/rules/api.md").exists())
        sources.write_bytes(original)
        self.run_py("rules-draft.py", work, "--apply", "--expected-digest", expected)
        content = Path(self.repo, ".agents/rules/api.md").read_text()
        self.assertIn(SRC, content)
        self.assertIn("APPLIED:", self.run_py("rules-draft.py", work, "--check").stdout)

    def test_same_sentence_different_exception_is_not_recovery_match(self):
        work, no = self.prepare(rule())
        self.write_all(work, no.values())
        self.write_rules(work, "--apply")
        n = next(iter(no.values()))
        self.edit_block(work, n, "动作", "写入")
        self.edit_block(work, n, "不要报", "仅本地测试例外")
        out = self.write_rules(work, "--apply")
        self.assertIn("WRITE:", out)
        self.assertIn("仅本地测试例外", Path(self.repo, ".agents/rules/api.md").read_text())

    def test_old_flags_are_gone(self):
        work, no = self.prepare(rule())
        for args in (["--pick", "1"], ["--edit", "1=x"], []):
            self.assertNotEqual(self.run_write(work, *args).returncode, 0)

    def test_api_failure_does_not_guess_login(self):
        r = self.run_script("mine-mrs.sh", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kind=api_failed", r.stderr)
        self.assertNotIn("auth login", r.stderr)
        work = Path(self.review_dir(r.stdout))
        self.assertIs(self.load(work / "snapshot.json")["complete"], False)
        self.assertEqual(self.kv(work / "meta.txt")["complete"], "false")
        self.assertEqual(list(work.glob("batch-*.json")), [])

    def test_improve_is_command_only(self):
        skill = Path(os.path.dirname(fixture.SCRIPTS), "..", "improve")
        head = skill.joinpath("SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("disable-model-invocation: true", head.splitlines())
        self.assertIn("allow_implicit_invocation: false", skill.joinpath("agents", "openai.yaml").read_text(encoding="utf-8"))


class EvidenceDigest(fixture.unittest.TestCase):
    """证据区和来源注释只放典型 MR:挑法、链接、截断都由 rule_candidates 决定,纯函数,不碰记录目录。"""

    def setUp(self):
        sys.path.insert(0, fixture.SCRIPTS)
        import rule_candidates
        self.rc = rule_candidates

    def ev(self, mr, state, quote=None, url=None):
        return {"mr": mr, "note": mr * 10 + len(quote or ""), "quote": quote or "第 %d 号的意见" % mr,
                "url": MR_URL % mr if url is None else url, "state": state,
                "assessment": {"state": state, "reason": "判断依据长文:commit 0123456789abcdef0123456789abcdef01234567 改了 list(limit)",
                               "basis": [{"mr": mr, "note": 1, "quote": "同讨论的回复原文"}]}}

    def cand(self, evidence, feedback=()):
        mrs = sorted({e["mr"] for e in evidence})
        states = {}
        for e in evidence:
            states.setdefault(e["mr"], set()).add(e["assessment"]["state"])
        acc = sum(1 for s in states.values() if s == {"accepted"})
        unk = sum(1 for s in states.values() if "unknown" in s or len(s) > 1)
        ev = list(evidence) + [{"feedback": k, "target": "mr-%d" % (i + 1), "quote": "判断记录的原因 %s" % k, "state": "判为成立"}
                               for i, k in enumerate(feedback)]
        return {"no": 1, "kind": "rule", "rule": "必须 x", "mrs": mrs, "evidence": ev, "mentions": len(mrs) + len(feedback),
                "support": acc + len(feedback), "unknown": unk, "people": 2, "unmet": [],
                "review": "两个 MR 指向同一约束,边界是离线导出不适用。第二句很长的细节:" + "细" * 200, "coverage": {}}

    def evidence(self):
        return [self.ev(5, "rejected"), self.ev(9, "accepted", "第一条"), self.ev(9, "accepted", "同一 MR 的第二条"),
                self.ev(3, "accepted"), self.ev(7, "unknown"), self.ev(7, "accepted"), self.ev(8, "unknown"), self.ev(2, "accepted")]

    def test_typical_prefers_accepted_one_per_mr_and_is_deterministic(self):
        picked, rest = self.rc.typical(self.cand(self.evidence()))
        self.assertEqual([(r["mr"], r["state"]) for r in picked], [(9, "accepted"), (3, "accepted"), (2, "accepted")])
        self.assertEqual(picked[0]["quote"], "第一条")                          # 每个 MR 一条:取它的第一条证据
        self.assertEqual(rest, [5, 7, 8])                                      # 其余按编号从小到大
        # MR 之间的顺序打乱,结果不变
        ev = self.evidence()
        by = {}
        for e in ev:
            by.setdefault(e["mr"], []).append(e)
        for order in ([2, 8, 7, 3, 9, 5], [5, 9, 3, 7, 8, 2]):
            again = self.rc.typical(self.cand([e for mr in order for e in by[mr]]))
            self.assertEqual(([r["mr"] for r in again[0]], again[1]), ([9, 3, 2], [5, 7, 8]))
        # 已采纳不够时依次补未知 / 结论不一致,最后才是未采纳;同档编号大的在前
        picked, rest = self.rc.typical(self.cand([e for e in self.evidence() if e["mr"] in (5, 7, 8, 2)]))
        self.assertEqual([(r["mr"], r["state"]) for r in picked], [(2, "accepted"), (8, "unknown"), (7, "mixed")])
        self.assertEqual(rest, [5])

    def test_evidence_lines_show_typical_links_and_count_the_rest(self):
        lines = self.rc.evidence_lines(self.cand(self.evidence()))
        self.assertEqual(lines[1:6], [
            "> 提及 6 个 MR:模型判断已采纳 3,未知/结论不一致 2,未采纳 1",
            "> - [!9](%s)「第一条」[模型判断已采纳]" % (MR_URL % 9),
            "> - [!3](%s)「第 3 号的意见」[模型判断已采纳]" % (MR_URL % 3),
            "> - [!2](%s)「第 2 号的意见」[模型判断已采纳]" % (MR_URL % 2),
            "> 另有 3 个 MR:!5 !7 !8"])
        text = "\n".join(lines)
        for word in ("判断依据", "0123456789abcdef", "同讨论的回复原文", "同一 MR 的第二条", "candidates.json", "第二句"):
            self.assertNotIn(word, text)                                       # 判断依据长文和多余原文不进 md
        self.assertIn("> 语义复核(模型判断):两个 MR 指向同一约束,边界是离线导出不适用。\n", text + "\n")
        # 不超过 3 个时不写「另有」;结论不一致的 MR 标出来
        lines = self.rc.evidence_lines(self.cand([self.ev(7, "unknown"), self.ev(7, "accepted"), self.ev(4, "accepted")]))
        self.assertEqual(lines[2:4], ["> - [!4](%s)「第 4 号的意见」[模型判断已采纳]" % (MR_URL % 4),
                                      "> - [!7](%s)「第 7 号的意见」[结论不一致]" % (MR_URL % 7)])
        self.assertFalse([x for x in lines if "另有" in x])

    def test_long_quote_is_clipped_and_feedback_is_capped(self):
        long = "这条评论很长" * 30
        c = self.cand([self.ev(4, "accepted", long)], feedback=["k1", "k2", "k3", "k4"])
        lines = self.rc.evidence_lines(c)
        row = next(x for x in lines if x.startswith("> - [!4]"))
        quote = row.split("「", 1)[1].split("」", 1)[0]
        self.assertTrue(quote.endswith("…") and len(quote) == self.rc.QUOTE_CHARS + 1, quote)
        self.assertIn("> - 审查 mr-1 发现 k1「判断记录的原因 k1」[判为成立]", lines)
        self.assertNotIn("k4", "\n".join(x for x in lines if x.startswith("> - ")))
        self.assertIn("> 另有 1 条判断记录", lines)

    def test_links_come_from_snapshot_or_repo_url_never_invented(self):
        url = self.rc.mr_web_url
        self.assertEqual(url("https://h.example/g/r/-/merge_requests/4", "https://other.example/x", "gitlab", 4),
                         "https://h.example/g/r/-/merge_requests/4")                  # 快照里平台给的地址优先
        self.assertEqual(url("", "https://h.example/g/r", "github", 4), "https://h.example/g/r/pull/4")
        self.assertEqual(url(None, "http://h.example:8080/a/b/c/", "gitlab", 4), "http://h.example:8080/a/b/c/-/merge_requests/4")
        for args in (("", "", "gitlab", 4), ("", "https://h.example/r", "", 4), ("", "h.example/r", "github", 4),
                     ("javascript:alert(1)", "", "github", 4), ("https://h.example/a b", "", "github", 4),
                     ("https://h.example/a-->x", "", "github", 4)):
            self.assertEqual(url(*args), "", args)
        c = self.cand([self.ev(4, "accepted", url=""), self.ev(6, "accepted", url="not a url")])
        self.assertEqual(self.rc.evidence_lines(c)[2:4], ["> - !6「第 6 号的意见」[模型判断已采纳]", "> - !4「第 4 号的意见」[模型判断已采纳]"])

    def test_source_note_lists_typical_links_and_total(self):
        self.assertEqual(self.rc.source_refs(self.cand(self.evidence())),
                         "%s %s %s 等 6 个 MR" % (MR_URL % 9, MR_URL % 3, MR_URL % 2))
        self.assertEqual(self.rc.source_refs(self.cand([self.ev(4, "accepted"), self.ev(6, "rejected", url="")])),
                         "%s !6(共 2 个 MR)" % (MR_URL % 4))
        self.assertEqual(self.rc.source_refs(self.cand([self.ev(4, "accepted")], feedback=["k1", "k2", "k3", "k4"])),
                         "%s(共 1 个 MR);审查 mr-1 发现 k1 审查 mr-2 发现 k2 审查 mr-3 发现 k3 等 4 条判断记录" % (MR_URL % 4))
        note = self.rc.source_refs(self.cand(self.evidence()))
        self.assertNotIn("-->", note)
        self.assertNotIn("!5", note)                                          # 未采纳的没进典型,只算在总数里
