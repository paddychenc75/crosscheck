"""crosscheck 脚本的测试。

运行:python3 -m unittest discover -s tests -v   (在插件根目录)

每个测试在临时目录里建自己的仓库和记录目录;GitLab 用 tests/bin/glab 模拟,远程仓库用本地裸仓库代替。
不访问网络,不读写用户自己的 ~/.crosscheck。
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "cr", "scripts")
MOCK_BIN = os.path.join(HERE, "bin")


def api_key(path):
    return re.sub(r"[/?&=:%]", "_", path)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cr-test-")
        self.home = os.path.join(self.tmp, "home")
        self.mock = os.path.join(self.tmp, "mock")
        os.makedirs(os.path.join(self.mock, "api"))
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.tmp, "t"))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GITLAB", "GLAB", "GH_", "GITHUB", "CROSSCHECK", "CR_"))}
        env.update({
            "CROSSCHECK_HOME": self.home, "CROSSCHECK_OPEN": "0", "CR_USER_SHELL": "0", "TMPDIR": os.path.join(self.tmp, "t"),
            "GIT_TERMINAL_PROMPT": "0", "MOCK_GLAB": self.mock, "LC_ALL": "en_US.UTF-8",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        })
        # 默认 PATH 里没有 glab / gh,需要时用 use_mock_glab()
        env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if not (os.path.exists(os.path.join(p, "glab")) or os.path.exists(os.path.join(p, "gh"))))
        self.env = env

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_mock_glab(self):
        self.env["PATH"] = MOCK_BIN + ":" + self.env["PATH"]

    def config(self, text):
        with open(os.path.join(self.home, "config"), "w") as f:
            f.write(textwrap.dedent(text))

    def mock_api(self, path, data):
        with open(os.path.join(self.mock, "api", api_key(path) + ".json"), "w") as f:
            json.dump(data, f, ensure_ascii=False)

    def run_script(self, name, *args, cwd=None, check=True, env=None):
        r = subprocess.run(["sh", os.path.join(SCRIPTS, name)] + list(args), cwd=cwd or self.tmp, env=env or self.env,
                           capture_output=True, text=True)
        if check and r.returncode != 0:
            self.fail("%s 失败(%d):\n%s\n%s" % (name, r.returncode, r.stdout, r.stderr))
        return r

    def git(self, repo, *args, check=True):
        r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=self.env)
        if check and r.returncode != 0:
            self.fail("git %s 失败:%s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def write(self, root, files):
        for path, text in files.items():
            p = os.path.join(root, path)
            if text is None:
                if os.path.exists(p):
                    os.remove(p)
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(text).lstrip("\n"))

    def make_repo(self, name, files, remote=None):
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo, files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "init")
        if remote:
            self.git(repo, "remote", "add", "origin", remote)
        return repo

    def commit(self, repo, files, msg):
        self.write(repo, files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", msg)
        return self.git(repo, "rev-parse", "HEAD")

    def bare(self, name):
        p = os.path.join(self.tmp, "remotes", name + ".git")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", p)
        return p

    def review_dir(self, out):
        return out.strip().splitlines()[-1]

    def load(self, path, default=None):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def kv(self, path):
        d = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    d[k] = v
        return d


REVIEW_MD = """\
---
extends: [platform/rpc]
disable:
  - ORG-RPC-004: 本项目是网关,超时由上游设置
skip: ["src/gen/**"]
limits: { minor: 2 }
severity: { PRJ-002: critical }
risk_paths: ["src/payment/**"]
---
# Review 约定

## 每次都要检查

- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 dayjs。原因:时区统一处理。
  <!-- cr: id=PRJ-001 scope=src/** severity=minor src=manual added=2026-10 -->
- 日志里不能出现邮箱。原因:合规。
  <!-- cr: id=PRJ-002 src=manual added=2026-10 -->
- 没有编号的一条规则
"""

PACK = """\
---
pack: platform/rpc
prefix: ORG-RPC
owner: "@rpc-team"
applies_to: ["**/*.ts"]
---
- RPC 调用必须设置超时。原因:避免拖垮调用方。 <!-- cr: id=ORG-RPC-001 -->
- 网关透传 header。原因:链路追踪。
  <!-- cr: id=ORG-RPC-004 -->
- 旧规则。原因:历史。 <!-- cr: id=ORG-RPC-005 status=deprecated -->
"""


class ProjectFixture(Base):
    """一个带规则、组织规则包和 feature 分支的项目。"""

    def setUp(self):
        super().setUp()
        rules = os.path.join(self.home, "org-rules")
        self.write(rules, {"packs/platform/rpc/rules.md": PACK})
        open(os.path.join(rules, ".last-sync"), "w").close()
        self.repo = self.make_repo("shop", {
            "REVIEW.md": REVIEW_MD,
            "src/orders/REVIEW.md": "- 订单金额用 decimal。原因:精度。 <!-- cr: id=PRJ-003 -->\n",
            ".cr/ignore.md": "- order_client 统一重试,调用方不用处理 <!-- cr: id=IGN-001 -->\n",
            "CLAUDE.md": "# Project\nUse pnpm.\n",
            "src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.ceil(t / s)\n}\n",
            "src/auth/login.ts": "export function login(u: string) {\n  return u\n}\n",
            "tests/page.test.ts": 'test("a", () => { expect(page(4, 2)).toBe(2) })\n',
            ".gitlab-ci.yml": "stages: [test]\nunit:\n  script: npm test\nlint:\n  script: npm run lint\n",
        })
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "switch", "-qc", "feature")
        self.head = self.commit(self.repo, {
            "src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.floor(t / s)\n}\n// TODO\n",
            "src/gen/x.ts": "export const g = 1\n",
            "src/auth/login.ts": "export function login(u: string) { // @ts-ignore\n  return u\n}\n",
            "tests/page.test.ts": 'test.skip("a", () => {})\n',
            ".gitlab-ci.yml": "stages: [test]\nunit:\n  script: npm test\n",
            "db/migrations/001.sql": "ALTER TABLE x ADD y int;\n",
            "REVIEW.md": REVIEW_MD.replace("PRJ-002: critical", "PRJ-002: minor") + "- 分支里新加的规则。原因:测试。\n  <!-- cr: id=PRJ-009 -->\n",
        }, "feat: 修复分页 Closes #12, PROJ-231")
        self.git(self.repo, "switch", "-q", "main")

    def collect(self, *args):
        r = self.run_script("collect-diff.sh", *args, cwd=self.repo)
        rd = self.review_dir(r.stdout)
        self.run_script("collect-rules.sh", rd)
        return rd, r.stdout


class TestCollect(ProjectFixture):
    def test_range_review_reads_settings_and_rules_from_base(self):
        rd, out = self.collect("feature")
        settings = self.load(os.path.join(rd, "settings.json"))
        self.assertTrue(settings["source"].startswith("base:"))
        self.assertEqual(settings["severity"]["PRJ-002"], "critical")
        skipped = open(os.path.join(rd, "skipped.txt")).read()
        self.assertIn("src/gen/x.ts\treview-skip", skipped)
        self.assertIn("tests/page.test.ts\ttest", skipped)
        units = {u["unit"] for u in self.load(os.path.join(rd, "rules-index.json"))}
        self.assertTrue({"PRJ-001", "PRJ-002", "PRJ-003", "ORG-RPC-001", "FILE:REVIEW.md", "FILE:CLAUDE.md"} <= units)
        self.assertNotIn("PRJ-009", units, "改动本身加的规则不应在审查它时生效")
        self.assertNotIn("ORG-RPC-004", units)
        self.assertNotIn("ORG-RPC-005", units)
        meta = self.kv(os.path.join(rd, "rules-meta.txt"))
        self.assertIn("ORG-RPC-004", meta["disabled"])
        self.assertEqual(meta["deprecated"], "ORG-RPC-005")
        task = open(os.path.join(rd, "task-rules.md"), encoding="utf-8").read()
        self.assertIn("RULES-FP=" + meta["fingerprint"], task)
        nonce = open(os.path.join(rd, ".rules-nonce")).read().strip()
        self.assertTrue(task.rstrip().splitlines()[-1].startswith("核对码:" + nonce))

    def test_context_weakening_risk_and_intent(self):
        rd, out = self.collect("feature")
        kinds = {line.split("\t")[0] for line in open(os.path.join(rd, "weakening.txt"), encoding="utf-8")}
        self.assertTrue({"assertions-removed", "skip-added", "ci-check-removed", "suppression-added"} <= kinds, kinds)
        self.assertIn("SUGGEST:", out)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual(meta["risk_level"], "high")
        self.assertEqual(meta["depth_source"], "default")
        self.assertIn("提交说明", meta["intent_sources"])
        intent = open(os.path.join(rd, "intent.md"), encoding="utf-8").read()
        self.assertIn("feat: 修复分页", intent)

    def test_local_review_reads_worktree_rules(self):
        self.git(self.repo, "switch", "-q", "feature")
        rd, _ = self.collect("--intent", "修复分页丢数据")
        units = {u["unit"] for u in self.load(os.path.join(rd, "rules-index.json"))}
        self.assertIn("PRJ-009", units)
        self.assertIn("你的补充", self.kv(os.path.join(rd, "meta.txt"))["intent_sources"])

    def test_no_rules_is_fine(self):
        repo = self.make_repo("plain", {"a.py": "x = 1\n"})
        self.commit(repo, {"a.py": "x = 2\n"}, "change")
        r = self.run_script("collect-diff.sh", "HEAD~1..HEAD", cwd=repo)
        rd = self.review_dir(r.stdout)
        self.run_script("collect-rules.sh", rd)
        self.assertEqual(self.kv(os.path.join(rd, "rules-meta.txt"))["units"], "0")
        r = self.run_script("check-rules.sh", rd)
        self.assertIn("OK", r.stdout)


class TestPipeline(ProjectFixture):
    def auditor(self, rd, checked, nonce=None):
        nonce = nonce if nonce is not None else open(os.path.join(rd, ".rules-nonce")).read().strip()
        with open(os.path.join(rd, "rules-auditor-1.json"), "w") as f:
            json.dump({"nonce": nonce, "findings": [], "checked": checked}, f)

    def all_checked(self, rd):
        out = []
        for u in self.load(os.path.join(rd, "rules-index.json")):
            out.append({"rule_id": u["unit"], "files": u["applies_files"], "result": "ok", "lines": "1"})
        return out

    def test_check_rules_detects_missing_and_bad_nonce(self):
        rd, _ = self.collect("feature")
        checked = [c for c in self.all_checked(rd) if c["rule_id"] != "FILE:CLAUDE.md"]
        self.auditor(rd, checked)
        r = self.run_script("check-rules.sh", rd, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("MISSING: FILE:CLAUDE.md", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(rd, "rerun-rules.md")))
        self.auditor(rd, self.all_checked(rd), nonce="XXXX")
        r = self.run_script("check-rules.sh", rd, check=False)
        self.assertIn("NONCE", r.stdout)
        self.auditor(rd, self.all_checked(rd))
        r = self.run_script("check-rules.sh", rd)
        self.assertIn("OK", r.stdout)

    def test_check_rules_rerun_after_bad_nonce(self):
        rd, _ = self.collect("feature")
        good = open(os.path.join(rd, ".rules-nonce")).read().strip()
        for n, nonce in ((1, "XXXX"), (2, good)):
            with open(os.path.join(rd, "rules-auditor-%d.json" % n), "w") as f:
                json.dump({"nonce": nonce, "findings": [], "checked": self.all_checked(rd) if n == 1 else []}, f)
        r = self.run_script("check-rules.sh", rd, check=False)
        self.assertIn("MISSING", r.stdout, "核对码错的那次,检查清单不算覆盖")
        self.assertNotIn("NONCE", r.stdout, "以最后一次的核对码为准")
        with open(os.path.join(rd, "rules-auditor-2.json"), "w") as f:
            json.dump({"nonce": good, "findings": [], "checked": self.all_checked(rd)}, f)
        self.assertIn("OK", self.run_script("check-rules.sh", rd).stdout)
        self.assertEqual(self.load(os.path.join(rd, "rules-coverage.json"))["nonce_mismatch"], ["rules-auditor-1.json"])

    def run_pipeline(self, rd, candidates, scores, spot_score=0):
        with open(os.path.join(rd, "candidates.json"), "w") as f:
            json.dump(candidates, f, ensure_ascii=False)
        self.run_script("prepare-verify.sh", rd, "--spot", "1")
        results = []
        for name in sorted(os.listdir(os.path.join(rd, "verify"))):
            for c in self.load(os.path.join(rd, "verify", name)):
                r = dict(c)
                self.assertNotIn("found_by", r)
                if c.get("kind") == "spot":
                    text = next(u["text"] for u in self.load(os.path.join(rd, "rules-index.json")) if u["unit"] == c["rule_id"])
                    r.update(confidence=spot_score, verdict="抽查", evidence=[{"file": c["file"], "lines": "1"}], rule_quote=text[:20])
                else:
                    conf, ev = scores[c["summary"]]
                    r.update(confidence=conf, verdict="读过代码", evidence=ev)
                results.append(r)
        with open(os.path.join(rd, "verify", "result-1.json"), "w") as f:
            json.dump(results, f, ensure_ascii=False)
        r = self.run_script("aggregate.sh", rd)
        return self.load(os.path.join(rd, "final.json")), r.stdout

    def test_aggregate_rules_evidence_limits_and_severity(self):
        rd, _ = self.collect("feature")
        self.auditor(rd, self.all_checked(rd))
        self.run_script("check-rules.sh", rd)
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        ev = [{"file": P, "lines": "1-2"}]
        cands = [
            {"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "整页丢最后一页", "failure_scenario": "x", "found_by": ["bug-hunter"], "pass_hits": 2},
            {"file": L, "line": 1, "category": "security", "severity": "critical", "summary": "依据不存在", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_id": "PRJ-002", "rule_quote": "日志里不能出现邮箱。原因:合规。", "summary": "日志有邮箱", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_id": "PRJ-404", "summary": "编造的规则", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "rules", "rule_id": "PRJ-001", "rule_quote": "不是原文", "summary": "引用不对", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "minor", "summary": "小问题1", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "bug", "severity": "minor", "summary": "小问题2", "failure_scenario": "x"},
            {"file": L, "line": 2, "category": "bug", "severity": "minor", "summary": "小问题3", "failure_scenario": "x"},
            {"file": P, "line": 9, "category": "bug", "severity": "major", "summary": "不在改动行", "failure_scenario": "x"},
            {"file": "REVIEW.md", "line": 1, "category": "rules", "rule_id": "PRJ-002", "summary": "规则可能过时:日志", "failure_scenario": "x"},
            {"file": P, "line": 2, "category": "intent", "severity": "major", "summary": "没满足验收标准", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "major", "summary": "文字定位的依据", "failure_scenario": "x"},
            {"file": P, "line": 1, "category": "bug", "severity": "major", "summary": "文字定位不存在", "failure_scenario": "x"},
        ]
        scores = {"整页丢最后一页": (96, ev), "依据不存在": (90, [{"file": L, "lines": "50-60"}]), "日志有邮箱": (90, ev),
                  "编造的规则": (90, ev), "引用不对": (90, ev), "小问题1": (85, ev), "小问题2": (84, ev),
                  "小问题3": (83, [{"file": L, "lines": "1-2"}]), "不在改动行": (95, ev),
                  "没满足验收标准": (95, ev + [{"file": "intent.md", "lines": "验收标准 1"}]),
                  "文字定位的依据": (95, ev + [{"file": "REVIEW.md", "lines": "PRJ-002"}]),
                  "文字定位不存在": (95, ev + [{"file": "REVIEW.md", "lines": "PRJ-999"}])}
        final, out = self.run_pipeline(rd, cands, scores, spot_score=90)
        shown = {x["summary"]: x for g in final["groups"].values() for x in g}
        self.assertIn("整页丢最后一页", shown)
        self.assertIn("没满足验收标准", shown, "引用 review_dir 里的 intent.md 作依据是有效的")
        self.assertIn("文字定位的依据", shown, "REVIEW.md 里能找到 PRJ-002,依据有效")
        self.assertEqual(shown["整页丢最后一页"]["found_by"], ["bug-hunter"])
        self.assertEqual(shown["日志有邮箱"]["severity"], "critical", "REVIEW.md 的 severity 设置应生效")
        rejected = {x["summary"]: x for x in self.load(os.path.join(rd, "rejected.json"))}
        self.assertEqual(rejected["依据不存在"]["confidence"], 50)
        self.assertEqual(rejected["文字定位不存在"]["confidence"], 50)
        self.assertEqual(rejected["编造的规则"]["confidence"], 0)
        self.assertEqual(rejected["引用不对"]["confidence"], 0)
        self.assertEqual(rejected["不在改动行"]["confidence"], 0)
        self.assertEqual(final["trimmed"].get("defects"), 1, "minor 上限 2 条,第 3 条应被裁掉")
        self.assertEqual(final["spot"]["failed"], 1)
        self.assertEqual([n["summary"] for n in final["notes"]], ["规则可能过时:日志"])
        nos = sorted(x["no"] for g in final["groups"].values() for x in g)
        self.assertEqual(nos, list(range(1, len(nos) + 1)))
        standing = self.load(os.path.join(rd, "findings.json"))
        self.assertTrue(all(x.get("id") for x in standing))
        self.assertEqual(len(standing), sum(len(g) for g in final["groups"].values()) + 1)
        lines = self.run_script("report-lines.sh", rd).stdout
        self.assertIn("SCOPE: 审查范围:分支 feature", lines)
        self.assertIn("任务含规则 未验证(未安装 hook)", lines)
        self.assertIn("抽查 1 条发现 1 处漏检", lines)
        self.assertIn("WEAKEN:", lines)

    def test_incremental_carries_and_resolves(self):
        rd, _ = self.collect("feature")
        P, L = "src/orders/paginate.ts", "src/auth/login.ts"
        cands = [{"file": P, "line": 2, "category": "bug", "severity": "major", "summary": "A", "failure_scenario": "x"},
                 {"file": L, "line": 1, "category": "bug", "severity": "major", "summary": "B", "failure_scenario": "x"}]
        self.run_pipeline(rd, cands, {"A": (90, [{"file": P, "lines": "2"}]), "B": (90, [{"file": L, "lines": "1"}])})
        with open(os.path.join(rd, "report.md"), "w") as f:
            f.write("报告\n")
        r = self.run_script("save-review.sh", rd)
        self.assertIn("cost=", r.stdout)
        saved = r.stdout.split("dir=")[1].splitlines()[0]
        self.assertTrue(os.path.exists(os.path.join(saved, "rejected.json")))
        self.git(self.repo, "switch", "-q", "feature")
        self.commit(self.repo, {"src/orders/paginate.ts": "export function page(t: number, s: number) {\n  return Math.ceil(t / s)\n}\n"}, "fix")
        self.git(self.repo, "switch", "-q", "main")
        rd2, out = self.collect("feature")
        self.assertEqual(self.kv(os.path.join(rd2, "meta.txt"))["review_scope"], "incremental")
        prev = self.load(os.path.join(rd2, "prev-findings.json"))
        a = next(p for p in prev if p["summary"] == "A")
        cand = dict(a, from_prev=True)
        cand.pop("confidence"); cand.pop("verdict")
        final, _ = self.run_pipeline(rd2, [cand], {"A": (10, [{"file": P, "lines": "2"}])})
        self.assertEqual([x["summary"] for x in final["resolved"]], ["A"])
        carried = [x for g in final["groups"].values() for x in g if x["status"] == "carried"]
        self.assertEqual([x["summary"] for x in carried], ["B"])


class TestDependents(Base):
    def test_unchanged_file_using_changed_symbol_is_rechecked(self):
        repo = self.make_repo("dep", {"src/a.ts": "export function computeTotal(x) {\n  return x\n}\n",
                                      "src/b.ts": "import { computeTotal } from './a'\nexport const v = computeTotal(1)\n",
                                      "src/c.ts": "export const other = 1\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/a.ts": "export function computeTotal(x) {\n  return x + 1\n}\n",
                           "src/b.ts": "import { computeTotal } from './a'\nexport const v = computeTotal(2)\n",
                           "src/c.ts": "export const other = 2\n"}, "f1")
        rd = self.review_dir(self.run_script("collect-diff.sh", cwd=repo).stdout)
        for name, text in (("report.md", "r\n"), ("findings.json", "[]")):
            with open(os.path.join(rd, name), "w") as f:
                f.write(text)
        self.run_script("save-review.sh", rd)
        self.commit(repo, {"src/a.ts": "export function computeTotal(x) {\n  return x * 2\n}\n"}, "f2")
        rd2 = self.review_dir(self.run_script("collect-diff.sh", cwd=repo).stdout)
        st = dict(l.rstrip("\n").split("\t")[::-1] for l in open(os.path.join(rd2, "incremental.txt")))
        self.assertEqual((st["src/a.ts"], st["src/b.ts"], st["src/c.ts"]), ("changed", "dependent", "unchanged"))
        files = open(os.path.join(rd2, "files.txt")).read().split()
        self.assertEqual(sorted(files), ["src/a.ts", "src/b.ts"])


class TestGuard(ProjectFixture):
    def hook(self, payload):
        r = subprocess.run(["sh", os.path.join(SCRIPTS, "guard.sh")], input=json.dumps(payload), capture_output=True,
                           text=True, env=self.env, cwd=self.repo)
        return r.stdout

    def test_subagent_needs_rules_fingerprint(self):
        rd, _ = self.collect("feature")
        cmd = "sh %s/collect-diff.sh feature" % SCRIPTS
        self.hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.repo})
        self.hook({"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": cmd},
                   "tool_response": {"stdout": "files=4\n%s\n" % rd}})
        fp = self.kv(os.path.join(rd, "rules-meta.txt"))["fingerprint"]
        out = self.hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Agent", "tool_input": {"prompt": "阅读 roles/verifier.md"}})
        self.assertIn('"permissionDecision":"deny"', out)
        out = self.hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Agent", "tool_input": {"prompt": "阅读 roles/rules-auditor.md RULES-FP=%s" % fp}})
        self.assertEqual(out.strip(), "")
        self.assertTrue(os.path.exists(os.path.join(rd, ".hook-fp-ok")))
        out = self.hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": self.repo})
        self.assertIn("deny", out)
        self.assertIn("任务含规则 ✓", self.run_script("report-lines.sh", rd).stdout)


class TestGitLab(Base):
    def setUp(self):
        super().setUp()
        self.use_mock_glab()
        self.repo = self.make_repo("shop", {"src/p.ts": "export const a = 1\n"}, remote="https://code.example.com/eng/shop.git")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.head = self.commit(self.repo, {"src/p.ts": "export const a = 2\n"}, "change")
        self.git(self.repo, "checkout", "-q", "--detach", self.base)
        mr = {"iid": 12, "title": "修复分页", "description": "修复分页丢数据。\n\nCloses #7", "web_url": "https://code.example.com/eng/shop/-/merge_requests/12",
              "source_branch": "feature/pay", "target_branch": "main", "state": "merged",
              "diff_refs": {"base_sha": self.base, "start_sha": self.base, "head_sha": self.head}, "references": {"full": "eng/shop!12"}}
        self.mr = mr
        self.mock_api("projects/:id/merge_requests/12", mr)
        self.mock_api("projects/eng%2Fshop/merge_requests/12", mr)
        issue = {"iid": 7, "title": "分页丢数据", "description": "## 验收标准\n- total 是整数倍时返回全部数据\n- 空列表返回 0 页\n"}
        self.mock_api("projects/:id/issues/7", issue)
        notes = [{"notes": [
            {"body": "这里整页时会丢数据", "author": {"username": "alice"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1, "head_sha": self.head}, "resolved": True},
            {"body": "added 1 commit", "author": {"username": "alice"}, "system": True},
            {"body": "自动检查通过了", "author": {"username": "ci-bot", "bot": True}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1}},
        ]}]
        self.mock_api("projects/:id/merge_requests/12/discussions?per_page=100", notes)

    def test_self_hosted_host_from_config(self):
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gitlab_hosts", r.stderr)
        self.config("gitlab_hosts=code.example.com\n")
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo)
        rd = self.review_dir(r.stdout)
        meta = self.kv(os.path.join(rd, "meta.txt"))
        self.assertEqual((meta["platform"], meta["mode"], meta["target_branch"], meta["source_branch"]), ("gitlab", "pr", "main", "feature/pay"))
        intent = open(os.path.join(rd, "intent.md"), encoding="utf-8").read()
        self.assertIn("1. total 是整数倍时返回全部数据", intent)
        self.assertEqual(meta["intent_acceptance"], "2")
        self.assertEqual(len(self.load(os.path.join(rd, "discussions.json"))), 1)
        self.assertIn("#7", meta["intent_sources"])

    def test_subpath_mr_url(self):
        self.config("gitlab_hosts=company.com/gitlab\n")
        r = self.run_script("collect-diff.sh", "https://company.com/gitlab/eng/shop/-/merge_requests/12", cwd=self.repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["gitlab_host"], meta["number"]), ("eng/shop", "https://company.com/gitlab", "12"))

    def test_port_in_url_and_remote(self):
        self.git(self.repo, "remote", "set-url", "origin", "http://code.example.com:8929/eng/shop.git")
        self.config("gitlab_hosts=code.example.com:8929\n")
        r = self.run_script("collect-diff.sh", "http://code.example.com:8929/eng/shop/-/merge_requests/12", cwd=self.repo)
        meta = self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))
        self.assertEqual((meta["repo"], meta["gitlab_host"], meta["repo_id"]), ("eng/shop", "http://code.example.com:8929", "code.example.com/eng/shop"))
        r = self.run_script("collect-diff.sh", "12", cwd=self.repo)
        self.assertEqual(self.kv(os.path.join(self.review_dir(r.stdout), "meta.txt"))["platform"], "gitlab")

    def test_mine_reviews(self):
        self.config("gitlab_hosts=code.example.com\n")
        self.mock_api("projects/eng%2Fshop/merge_requests?state=merged&per_page=100&page=1", [self.mr])
        notes = [{"notes": [
            {"body": "整页时会丢最后一页的数据", "author": {"username": "alice"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1, "head_sha": self.head}, "resolved": True},
            {"body": "LGTM", "author": {"username": "bob"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 1, "head_sha": self.head}},
            {"body": "这个常量应该放进配置中心", "author": {"username": "bob"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 40, "head_sha": self.head}},
            {"body": "日志里打印了用户邮箱", "author": {"username": "bob"}, "type": "DiffNote", "position": {"new_path": "src/p.ts", "new_line": 3, "head_sha": self.head}},
        ]}]
        self.mock_api("projects/eng%2Fshop/merge_requests/12/discussions?per_page=100", notes)
        self.mock_api("projects/eng%2Fshop/merge_requests?state=closed&labels=crosscheck-rule&per_page=50", [])
        pdir = os.path.join(self.home, "reviews", "shop")
        os.makedirs(os.path.join(pdir, "mr-12", "20261020-100000"))
        with open(os.path.join(pdir, ".repo"), "w") as f:
            f.write("code.example.com/eng/shop\n")
        with open(os.path.join(pdir, "mr-12", "20261020-100000", "findings.json"), "w") as f:
            json.dump([{"id": "f1", "file": "src/p.ts", "line": 1, "summary": "丢数据"}], f)
        with open(os.path.join(pdir, "mr-12", "20261020-100000", "rejected.json"), "w") as f:
            json.dump([{"file": "src/p.ts", "line": 3, "summary": "日志有邮箱", "confidence": 0, "suppressed_by": "discussions"}], f, ensure_ascii=False)
        out = self.run_script("mine-reviews.sh", "--since", "30d", cwd=self.repo).stdout
        d = out.strip().splitlines()[-1]
        comments = [json.loads(x) for x in open(os.path.join(d, "comments.jsonl"), encoding="utf-8")]
        self.assertEqual(len(comments), 3)
        self.assertEqual(comments[0]["matched_finding"], "f1")
        self.assertEqual(comments[2]["matched_finding"], "suppressed:discussions", "取最近的一条;因人工已评论而压下的不算漏报")
        self.assertFalse(comments[0]["code_changed_after"])
        self.assertEqual([m["body"] for m in self.load(os.path.join(d, "misses.json"))], ["这个常量应该放进配置中心"])
        self.assertNotIn("alice", open(os.path.join(d, "comments.jsonl"), encoding="utf-8").read())


class TestSync(Base):
    def test_sync_rules_and_data(self):
        rules_remote = self.bare("rules")
        src = self.make_repo("rules-src", {"packs/platform/rpc/rules.md": PACK}, remote=rules_remote)
        self.git(src, "tag", "v1.0.0")
        self.git(src, "push", "-q", "origin", "main", "--tags")
        data_remote = self.bare("data")
        d = self.make_repo("data-src", {"README.md": "data\n"}, remote=data_remote)
        self.git(d, "push", "-q", "origin", "main")
        self.config("rules_repo=%s\nrules_ref=v1.0.0  # tag\ndata_repo=%s\n" % (rules_remote, data_remote))
        self.assertIn("v1.0.0", self.run_script("sync-rules.sh").stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.home, "org-rules", "packs", "platform", "rpc", "rules.md")))
        pdir = os.path.join(self.home, "reviews", "shop")
        os.makedirs(pdir)
        with open(os.path.join(pdir, ".repo"), "w") as f:
            f.write("code.example.com/eng/shop\n")
        with open(os.path.join(pdir, "feedback.jsonl"), "w") as f:
            f.write(json.dumps({"time": "2026-10-20 10:00", "verdict": "invalid", "reason": "上游已重试", "key": "k1", "id": "k1", "file": "a.go", "author": "abc"}, ensure_ascii=False) + "\n")
        self.assertIn("以上是要推送的条数", self.run_script("sync-data.sh", "push").stdout)
        self.assertIn("已推送 1 条判断", self.run_script("sync-data.sh", "push", "--yes").stdout)
        self.assertIn("total\t0", self.run_script("sync-data.sh", "status").stdout)
        # 另一个人的机器:没有本机记录,只靠团队数据
        rd = os.path.join(self.tmp, "rd")
        os.makedirs(rd)
        other = os.path.join(self.tmp, "home2")
        shutil.copytree(os.path.join(self.home, "data"), os.path.join(other, "data"))
        os.makedirs(os.path.join(other, "reviews", "shop"))
        with open(os.path.join(other, "reviews", "shop", ".repo"), "w") as f:
            f.write("code.example.com/eng/shop\n")
        with open(os.path.join(rd, "meta.txt"), "w") as f:
            f.write("store_dir=%s/reviews/shop/branch-x\nrepo_root=%s\nhead_checked_out=yes\n" % (other, self.tmp))
        with open(os.path.join(rd, "files.txt"), "w") as f:
            f.write("a.go\n")
        env = dict(self.env, CROSSCHECK_HOME=other)
        self.run_script("feedback.sh", "history", rd, os.path.join(rd, "history.json"), env=env)
        self.assertEqual(self.load(os.path.join(rd, "history.json"))[0]["reason"], "上游已重试")


class TestRules(Base):
    def test_lint_errors(self):
        repo = self.make_repo("p", {
            "REVIEW.md": "---\ndisable:\n  - ORG-X-001\n---\n- 规则甲。原因:a <!-- cr: id=PRJ-001 -->\n- 规则乙。原因:b <!-- cr: id=PRJ-001 severity=huge -->\n",
            "src/a.ts": "x\n"})
        r = self.run_script("rules-lint.sh", "--repo", repo, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有写原因", r.stdout)
        self.assertIn("编号 PRJ-001 重复", r.stdout)
        self.assertIn("severity=huge", r.stdout)

    def test_propose_worktree_and_mr(self):
        remote = self.bare("proj")
        repo = self.make_repo("p", {"REVIEW.md": "# Review 约定\n\n## 每次都要检查\n\n- 甲。原因:a。\n  <!-- cr: id=PRJ-001 -->\n", "src/a.go": "x\n"}, remote=remote)
        self.git(repo, "push", "-q", "origin", "main")
        self.git(repo, "remote", "set-head", "origin", "main")
        ops = os.path.join(self.tmp, "ops.json")
        with open(ops, "w") as f:
            json.dump([{"op": "add", "text": "金额用 decimal", "reason": "精度", "scope": "**/*.go", "src": "!301"}, {"op": "remove", "id": "PRJ-001"}], f, ensure_ascii=False)
        r = self.run_script("propose-rules.sh", "--ops", ops, "--repo", repo)
        self.assertIn("+  <!-- cr: id=PRJ-002", r.stdout)
        self.assertIn("确认后加 --yes", r.stdout)
        self.run_script("propose-rules.sh", "--ops", ops, "--repo", repo, "--worktree", "--yes")
        text = open(os.path.join(repo, "REVIEW.md"), encoding="utf-8").read()
        self.assertIn("id=PRJ-002", text)
        self.assertNotIn("id=PRJ-001", text)
        self.assertIn("PRJ-001", open(os.path.join(repo, ".cr", "retired-ids")).read())
        self.git(repo, "checkout", "-q", "--", "REVIEW.md")
        shutil.rmtree(os.path.join(repo, ".cr"))
        self.use_mock_glab()
        r = self.run_script("propose-rules.sh", "--ops", ops, "--repo", repo, "--mr", "--yes")
        self.assertIn("已创建 MR", r.stdout)
        self.assertIn("--label crosscheck-rule", open(os.path.join(self.mock, "calls.log")).read())
        branches = self.git(remote, "branch", "--list")
        self.assertIn("crosscheck/rules-", branches)
        self.assertEqual(self.git(repo, "status", "--short"), "", "不应碰用户的工作区")
        self.assertNotIn("crosscheck/rules-", self.git(repo, "branch", "--list"))

    def test_pack_rule_mr_needs_examples(self):
        remote = self.bare("rules")
        rules = os.path.join(self.home, "org-rules")
        shutil.move(self.make_repo("org-rules", {"packs/platform/rpc/rules.md": PACK}, remote=remote), rules)
        self.write(rules, {"packs/platform/rpc/examples/ORG-RPC-001.bad.go": "x\n", "packs/platform/rpc/examples/ORG-RPC-001.good.go": "x\n",
                           "packs/platform/rpc/examples/ORG-RPC-004.bad.go": "x\n", "packs/platform/rpc/examples/ORG-RPC-004.good.go": "x\n",
                           "CODEOWNERS": "packs/platform/rpc/ @rpc-team\n"})
        self.git(rules, "add", "-A")
        self.git(rules, "commit", "-q", "-m", "examples")
        self.git(rules, "push", "-q", "origin", "main")
        self.git(rules, "remote", "set-head", "origin", "main")
        self.use_mock_glab()
        ops = os.path.join(self.tmp, "ops.json")
        op = {"op": "add", "text": "RPC 重试要带幂等键", "reason": "避免重复扣款", "severity": "major"}
        with open(ops, "w") as f:
            json.dump([op], f, ensure_ascii=False)
        r = self.run_script("propose-rules.sh", "--ops", ops, "--target", "pack:platform/rpc", "--mr", "--yes", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ORG-RPC-006 缺少反例", r.stdout)
        self.assertNotIn("crosscheck/rules-", self.git(remote, "branch", "--list"), "lint 没过不应推送")
        with open(ops, "w") as f:
            json.dump([dict(op, ext="go", bad="retry(call)\n", good="retry(call, key)\n")], f, ensure_ascii=False)
        self.assertIn("已创建 MR", self.run_script("propose-rules.sh", "--ops", ops, "--target", "pack:platform/rpc", "--mr", "--yes").stdout)
        branch = next(b for b in self.git(remote, "branch", "--list").split() if b.startswith("crosscheck/rules-"))
        files = self.git(remote, "ls-tree", "-r", "--name-only", branch)
        self.assertIn("packs/platform/rpc/examples/ORG-RPC-006.bad.go", files)
        text = self.git(remote, "show", "%s:packs/platform/rpc/rules.md" % branch)
        self.assertIn("- RPC 重试要带幂等键。原因:避免重复扣款。", text)
        self.assertNotIn("\n\n- RPC 重试", text)

    def test_review_checklist_and_confirm(self):
        repo = self.make_repo("p", {
            "REVIEW.md": "".join([
                "- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 `dayjs`。原因:时区。\n  <!-- cr: id=PRJ-001 added=2020-01 -->\n",
                "- 日志不能有邮箱。原因:合规。\n  <!-- cr: id=PRJ-002 added=2026-10 reviewed=2026-10 -->\n",
                "- legacy 不能加依赖。原因:要下线。\n  <!-- cr: id=PRJ-003 scope=src/legacy/** added=2026-10 -->\n",
                "- 日志里不能有邮箱。原因:合规要求。\n  <!-- cr: id=PRJ-004 added=2026-10 -->\n",
                "- 调用 `loadConfig` 读配置。原因:统一缓存。\n  <!-- cr: id=PRJ-005 added=2026-10 -->\n"]),
            "src/a.ts": "export const x = loadConfig()\n"})
        out = self.run_script("rules-health.sh", "--repo", repo).stdout
        lines = [l for l in out.splitlines() if l.startswith("| ") and "PRJ-" in l]
        self.assertIn("PRJ-001", lines[0], "超过 6 个月没复核的排在最前")
        self.assertIn("⏰", lines[0])
        self.assertIn("`src/utils/date.ts`、`dayjs` 在代码库里找不到", lines[0])
        row = {re.search(r"PRJ-\d+", l).group(0): l for l in lines}
        self.assertIn("匹配不到任何文件", row["PRJ-003"])
        self.assertIn("几乎一样", row["PRJ-002"])
        self.assertNotIn("找不到", row["PRJ-005"])
        self.assertNotIn("命中", out)
        ops = os.path.join(self.tmp, "ops.json")
        with open(ops, "w") as f:
            json.dump([{"op": "confirm", "id": "PRJ-001"}], f)
        self.run_script("propose-rules.sh", "--ops", ops, "--repo", repo, "--worktree", "--yes")
        import datetime
        self.assertIn("id=PRJ-001 added=2020-01 reviewed=%s" % datetime.date.today().strftime("%Y-%m"),
                      open(os.path.join(repo, "REVIEW.md"), encoding="utf-8").read())


class TestEvalAdoptionStats(Base):
    def test_eval_flow(self):
        rules = os.path.join(self.home, "org-rules")
        self.write(rules, {"packs/platform/rpc/rules.md": PACK.replace("**/*.ts", "**/*.go"),
                           "packs/platform/rpc/examples/ORG-RPC-001.bad.go": "package x\n",
                           "packs/platform/rpc/examples/ORG-RPC-001.good.go": "package x\n"})
        repo = self.make_repo("shop", {"src/a.ts": "a\n"})
        head = self.commit(repo, {"src/a.ts": "b\n"}, "bug")
        base = self.git(repo, "rev-parse", "HEAD~1")
        cases = os.path.join(rules, "evals", "cases")
        os.makedirs(cases)
        for cid, exp, src in (("c1", [{"file": "src/a.ts", "lines": "1-1", "category": "bug", "severity": "major", "desc": "坏了"}], "fix-commit"), ("c2", [], "clean")):
            with open(os.path.join(cases, cid + ".json"), "w") as f:
                json.dump({"id": cid, "repo": "shop", "base": base, "head": head, "source": src, "expect": exp,
                           "intent": "## 验收标准\n\n1. 空列表返回 0 页\n" if cid == "c1" else ""}, f, ensure_ascii=False)
        self.config("repo.shop=%s\n" % repo)
        out = self.run_script("eval.sh", "prepare", "--out", os.path.join(self.tmp, "run")).stdout
        run = os.path.join(self.tmp, "run")
        plan = self.load(os.path.join(run, "plan.json"))
        self.assertEqual(sorted(p["case"] for p in plan), ["c1", "c2", "rule-ORG-RPC-001-bad", "rule-ORG-RPC-001-good"])
        c1 = next(p for p in plan if p["case"] == "c1")
        self.assertIn("空列表返回 0 页", open(c1["plan_file"], encoding="utf-8").read(), "用例的需求材料作为 --plan 传入")
        self.assertNotIn("plan_file", next(p for p in plan if p["case"] == "c2"))
        fake = {"c1": [{"id": "x1", "file": "src/a.ts", "line": 1, "summary": "坏了"}, {"id": "x4", "file": "src/a.ts", "line": 2, "summary": "另一件事"}],
                "c2": [{"id": "x2", "file": "src/a.ts", "line": 1, "summary": "误报"}],
                "rule-ORG-RPC-001-bad": [{"id": "x3", "file": "src/example.go", "line": 1, "category": "rules", "rule_id": "ORG-RPC-001"}],
                "rule-ORG-RPC-001-good": []}
        for cid, fs in fake.items():
            rv = os.path.join(run, "cases", cid, "review")
            os.makedirs(rv)
            for name, data in (("findings.json", fs), ("final.json", {})):
                with open(os.path.join(rv, name), "w") as f:
                    json.dump(data, f, ensure_ascii=False)
            with open(os.path.join(rv, "meta.txt"), "w") as f:
                f.write("started_epoch=1\ndepth=standard\n")
            self.run_script("eval.sh", "done", run, cid)
        self.assertIn("配对 2 组", self.run_script("eval.sh", "match", run).stdout)
        judged = {"pairs": [{"case": "c1", "expect_index": 0, "finding_id": "x1", "same": True},
                            {"case": "c1", "expect_index": 0, "finding_id": "x4", "same": False}],
                  "extras": [{"case": "c2", "finding_id": "x2", "valid": False}]}
        with open(os.path.join(run, "judgments.json"), "w") as f:
            json.dump(judged, f)
        self.run_script("eval.sh", "score", run)
        self.assertEqual(self.load(os.path.join(run, "score.json"))["unjudged"], 1, "配对都不成立的发现要判断站不站得住")
        judged["extras"].append({"case": "c1", "finding_id": "x4", "valid": True})
        with open(os.path.join(run, "judgments.json"), "w") as f:
            json.dump(judged, f)
        base_file = os.path.join(self.tmp, "baseline.json")
        with open(base_file, "w") as f:
            json.dump({"time": "old", "recall": {"hit": 1, "expected": 2}, "fp_per_clean": 1.0, "precision": {"valid": 1, "invalid": 1},
                       "per_case": {"c1": {"hit": 0, "invalid": 0}}}, f)
        out = self.run_script("eval.sh", "score", run, "--baseline", base_file).stdout
        self.assertIn("| 召回 | 1/2 | 2/2 |", out)
        self.assertIn("新抓到:c1", out)
        self.assertIn("可以合入", out)
        r = self.run_script("eval.sh", "add-case", "--repo-name", "shop", "--base", base, "--head", head, "--source", "随便写", check=False)
        self.assertEqual(r.returncode, 2)

    def test_adoption_and_stats(self):
        remote = self.bare("shop")
        repo = self.make_repo("shop", {"src/a.ts": "line1\nline2\nline3\nline4\nline5\n", "src/b.ts": "keep\n"}, remote=remote)
        self.git(repo, "push", "-q", "origin", "main")
        self.git(repo, "switch", "-qc", "mr")
        head = self.commit(repo, {"src/a.ts": "line1\nBAD\nline3\nline4\nline5\n", "src/b.ts": "keep\nBAD2\n"}, "mr")
        self.git(repo, "switch", "-q", "main")
        self.commit(repo, {"src/a.ts": "line1\nFIXED\nline3\nline4\nline5\n"}, "merge")
        self.git(repo, "merge", "-q", "--no-edit", "mr", check=False)
        self.git(repo, "checkout", "-q", "--theirs", "src/b.ts", check=False)
        self.write(repo, {"src/a.ts": "line1\nFIXED\nline3\nline4\nline5\n", "src/b.ts": "keep\nBAD2\n"})
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "merged", check=False)
        self.git(repo, "push", "-q", "origin", "main")
        pdir = os.path.join(self.home, "reviews", "shop")
        rec = os.path.join(pdir, "mr-5", "20261020-100000")
        os.makedirs(rec)
        with open(os.path.join(pdir, ".repo"), "w") as f:
            f.write(remote[:-len(".git")] + "\n")
        with open(os.path.join(pdir, "mr-5", "latest"), "w") as f:
            f.write("20261020-100000\n")
        import hashlib
        def ctx(ls, n):
            return hashlib.sha1("\n".join(" ".join(x.split()) for x in ls[max(0, n - 3):n + 2]).encode()).hexdigest()[:12]
        a = "line1\nBAD\nline3\nline4\nline5\n".split("\n")
        b = "keep\nBAD2\n".split("\n")
        with open(os.path.join(rec, "findings.json"), "w") as f:
            json.dump([{"id": "fa", "file": "src/a.ts", "line": 2, "code_line": "BAD", "context_hash": ctx(a, 2), "summary": "A", "confidence": 90},
                       {"id": "fb", "file": "src/b.ts", "line": 2, "code_line": "BAD2", "context_hash": ctx(b, 2), "summary": "B", "confidence": 85}], f)
        with open(os.path.join(rec, "meta.txt"), "w") as f:
            f.write("target_branch=main\nhead_sha=%s\n" % head)
        with open(os.path.join(rec, "rejected.json"), "w") as f:
            json.dump([{"file": "src/a.ts", "line": 2, "summary": "差一点", "confidence": 75, "reject_reason": "差一环"}], f, ensure_ascii=False)
        out = self.run_script("adoption.sh", "scan", "--repo", repo).stdout
        self.assertIn("自动判定 1 条", out)
        pending = self.load(os.path.join(pdir, "adoption-pending.json"))
        self.assertEqual([p["id"] for p in pending], ["fa"])
        self.assertIn("FIXED", pending[0]["final_excerpt"])
        self.run_script("adoption.sh", "record", "fa", "resolved", "已修复", "--repo", repo)
        out = self.run_script("stats.sh", "--repo", repo).stdout
        self.assertIn("### 采纳", out)
        self.assertIn("| 1 | 0 | 1 | 0 | 1/2(样本不足) |", out)
        self.assertIn("### 差一点通过", out)
        self.assertIn("75 分", out)


if __name__ == "__main__":
    unittest.main()
