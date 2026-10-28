"""审查前先问对比基准:按项目记一次规则(不记提交),明确给了基准只对当次生效,非交互运行用推荐项并标「基准未确认」。
同一轮还会带上必填需求和可选需求资料。"""
import glob
import os
import sys
from pathlib import Path
from unittest import mock

import fixture

sys.path.insert(0, fixture.SCRIPTS)
import base_rule  # noqa: E402
import crlib  # noqa: E402


class Project(fixture.Base):
    """origin 上有 main、develop;feature 从 main 拉出,dev-feature 从 develop 拉出。"""

    def setUp(self):
        super().setUp()
        origin = self.bare("acme-kit")
        self.repo = self.make_repo("acme-kit", {"a.txt": "1\n"}, remote=origin)
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "checkout", "-qb", "develop")
        self.commit(self.repo, {"d.txt": "d\n"}, "dev")
        self.git(self.repo, "push", "-q", "origin", "develop")
        self.git(self.repo, "checkout", "-q", "main")
        self.git(self.repo, "checkout", "-qb", "feature")
        self.commit(self.repo, {"f.txt": "f\n"}, "feat Closes #1")

    def collect(self, *args, branch=None, skip_doc=True):
        if branch:
            self.git(self.repo, "checkout", "-q", branch)
        # 需求单用提交说明里的编号,需求文档默认跳过,让基准成为这些测试里唯一要问的事
        if skip_doc and "--source" not in args:
            args += ("--source", "doc=skip")
        r = self.run_script("collect-diff.sh", *args, "--full", cwd=self.repo)
        rd = self.review_dir(r.stdout)
        return r.stdout, rd, self.kv(os.path.join(rd, "meta.txt"))

    def record(self):
        files = glob.glob(os.path.join(self.home, "reviews", "*", "project.json"))
        return self.load(files[0]) if files else None

    def pending(self):
        return glob.glob(os.path.join(self.home, "reviews", "*", "*", "pending"))


class AskOnce(Project):
    def test_unrecorded_project_is_asked_and_review_does_not_start(self):
        out, rd, meta = self.collect()
        self.assertIn("BASE_UNSET: 这个项目还没有记录对比基准", out)
        self.assertIn("这次是默认分支 main", out)
        self.assertEqual(meta["base_state"], "unset")
        self.assertEqual(self.pending(), [], "还在问用户,不算开始审查")
        self.assertIsNone(self.record())

    def test_answer_is_recorded_as_a_rule_and_not_asked_again(self):
        out, rd, meta = self.collect("--base-rule", "target")
        self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(self.record()["base"], {"rule": "target"})
        self.assertEqual(len(self.pending()), 1)
        out, rd, meta = self.collect()
        self.assertNotIn("BASE_UNSET", out)
        self.assertEqual((meta["base_state"], meta["base_rule"]), ("rule", "target"))

    def test_rule_is_resolved_again_each_time_not_remembered_as_a_commit(self):
        self.collect("--base-rule", "target")
        recorded = Path(glob.glob(os.path.join(self.home, "reviews", "*", "project.json"))[0]).read_text()
        self.assertNotRegex(recorded, r"[0-9a-f]{7,40}")
        # origin/main 前进了,feature 变基到它上面:基准跟着变
        self.git(self.repo, "checkout", "-q", "main")
        newer = self.commit(self.repo, {"m.txt": "m\n"}, "main moves")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "checkout", "-q", "feature")
        self.git(self.repo, "rebase", "-q", "main")
        out, rd, meta = self.collect()
        self.assertEqual(meta["base_sha"], newer)
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["f.txt"])

    def test_branch_rule_is_normalised_before_use(self):
        out, rd, meta = self.collect("--base-rule", " branch:origin/develop ")
        self.assertEqual(meta["target_branch"], "develop")
        self.assertEqual(self.record()["base"], {"rule": "branch", "branch": "develop"})

    def test_branch_rule_applies_and_replaces_closer_branch_hint(self):
        out, rd, meta = self.collect("--base-rule", "branch:develop", branch="feature")
        self.assertEqual(self.record()["base"], {"rule": "branch", "branch": "develop"})
        self.assertEqual(meta["target_branch"], "develop")
        self.assertIn("项目记录的基准分支", meta["base_desc"])
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["f.txt"])
        # 之后不带参数:仍按 develop,而且不再问、不再提示更近的分支
        out, rd, meta = self.collect()
        self.assertEqual(meta["target_branch"], "develop")
        self.assertNotRegex(out, "BASE_UNSET|BASE_CHOICE")

    def test_recorded_branch_that_no_longer_exists_stops_with_a_way_out(self):
        self.collect("--base-rule", "branch:develop")
        self.git(self.repo, "push", "-q", "origin", "--delete", "develop")
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/develop")
        self.git(self.repo, "branch", "-D", "develop")
        r = self.run_script("collect-diff.sh", cwd=self.repo, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("换个基准", r.stderr)

    def test_invalid_rules_are_refused(self):
        for bad in ("main", "branch:", "branch:a..b", "branch:-x", "commit:abc1234"):
            with self.subTest(rule=bad):
                r = self.run_script("collect-diff.sh", "--base-rule", bad, cwd=self.repo, check=False)
                self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(self.record())


class OneOffAndOverride(Project):
    def test_explicit_base_applies_once_and_does_not_touch_the_record(self):
        self.collect("--base-rule", "target")
        out, rd, meta = self.collect("--base", "develop", branch="feature")
        self.assertEqual((meta["base_state"], meta["target_branch"]), ("explicit", "develop"))
        self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(self.record()["base"], {"rule": "target"})

    def test_explicit_base_in_an_unrecorded_project_is_not_asked_or_recorded(self):
        out, rd, meta = self.collect("--base", "develop", branch="feature")
        self.assertNotIn("BASE_UNSET", out)
        self.assertIsNone(self.record())
        self.assertEqual(len(self.pending()), 1)

    def test_commit_is_one_off_and_never_recorded(self):
        first = self.git(self.repo, "rev-parse", "main")
        out, rd, meta = self.collect("--base-sha", first[:10])
        self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(meta["base_sha"], first)
        self.assertIsNone(self.record(), "提交哈希下次就不是对的基准了,不记")
        out, _, _ = self.collect()
        self.assertIn("BASE_UNSET", out)

    def test_deleted_recorded_branch_does_not_block_an_explicit_commit(self):
        self.collect("--base-rule", "branch:develop")
        self.git(self.repo, "push", "-q", "origin", "--delete", "develop")
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/develop")
        self.git(self.repo, "branch", "-D", "develop")
        base = self.git(self.repo, "rev-parse", "main")
        for target in ((), ("main..feature",)):
            with self.subTest(target=target):
                out, rd, meta = self.collect(*target, "--base-sha", base)
                self.assertEqual(meta["base_sha"], base)
                self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(self.record()["base"], {"rule": "branch", "branch": "develop"})

    def test_commit_range_is_an_explicit_base(self):
        head = self.git(self.repo, "rev-parse", "HEAD")
        out, rd, meta = self.collect("main.." + head)
        self.assertNotIn("BASE_UNSET", out)

    def test_ask_again_keeps_the_old_rule_until_answered(self):
        self.collect("--base-rule", "target")
        out, _, _ = self.collect("--ask-base")
        self.assertIn("BASE_UNSET: 用户要求重新选择这个项目的对比基准", out)
        self.assertEqual(self.record()["base"], {"rule": "target"})
        out, rd, meta = self.collect("--base-rule", "branch:develop", branch="feature")
        self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(self.record()["base"], {"rule": "branch", "branch": "develop"})
        self.assertEqual(meta["target_branch"], "develop")


class Context(Project):
    def test_closer_long_lived_branch_goes_into_the_same_question(self):
        out, rd, meta = self.collect(branch="feature")
        self.assertNotIn("BASE_CHOICE", out)
        self.assertEqual(meta["base_state"], "unset")
        self.git(self.repo, "checkout", "-qb", "dev-feature", "origin/develop")
        self.commit(self.repo, {"g.txt": "g\n"}, "g Closes #2")
        out, rd, meta = self.collect()
        self.assertEqual(len([x for x in out.splitlines() if x.startswith("BASE_UNSET:")]), 1)
        self.assertIn("分支 develop 比默认分支更近", out)
        self.assertNotIn("BASE_CHOICE", out)

    def test_merged_branch_mentions_pre_merge_version(self):
        self.git(self.repo, "checkout", "-q", "main")
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
        self.git(self.repo, "push", "-q", "origin", "main")
        out, rd, meta = self.collect("feature")
        self.assertEqual(meta["merged"], "yes")
        self.assertIn("它已合入,推荐项对应的是合入前的版本", out)
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["f.txt"])


class PlatformRules(Project):
    def setUp(self):
        super().setUp()
        self.use_mock_glab()
        self.config("gitlab_hosts=code.example.com\n")
        self.git(self.repo, "checkout", "-qb", "dev-feature", "develop")
        self.head = self.commit(self.repo, {"g.txt": "g\n"}, "feat Closes #1")
        self.git(self.repo, "push", "-q", "origin", "HEAD:refs/merge-requests/12/head")
        base = self.git(self.repo, "rev-parse", "main")
        self.mock_api("projects/:id/merge_requests/12", {
            "title": "change", "web_url": "https://code.example.com/acme/acme-kit/-/merge_requests/12",
            "target_branch": "main", "source_branch": "dev-feature", "description": "Closes #1",
            "diff_refs": {"base_sha": base, "head_sha": self.head}, "references": {"full": "acme/acme-kit!12"}})

    def test_gitlab_uses_selected_branch_and_reuses_the_rule(self):
        for args in (("--base-rule", "branch:develop"), ()):
            out, rd, meta = self.collect("12", "--platform", "gitlab", *args)
            self.assertEqual(Path(rd, "files.txt").read_text().split(), ["g.txt"])
            self.assertIn("项目记录的基准分支", meta["base_desc"])
            self.assertNotIn("BASE_UNSET", out)
        self.assertEqual(self.record()["base"], {"rule": "branch", "branch": "develop"})

    def test_explicit_branch_changes_platform_diff_for_this_run_only(self):
        out, rd, meta = self.collect("12", "--platform", "gitlab", "--base", "develop")
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["g.txt"])
        self.assertIn("用户指定分支", meta["base_desc"])
        self.assertIsNone(self.record())

    def test_github_uses_selected_branch(self):
        ghmock = os.path.join(self.tmp, "ghmock")
        os.makedirs(ghmock)
        self.env.update(MOCK_GH=ghmock, PATH=os.path.join(fixture.HERE, "bin-gh") + ":" + self.env["PATH"])
        self.git(self.repo, "push", "-q", "origin", "HEAD:refs/pull/12/head")
        base = self.git(self.repo, "rev-parse", "main")
        Path(ghmock, "pr_title_url_baseRefOid_headRefOid_headRefName_baseRefName.txt").write_text(
            "change\thttps://example.com/acme/acme-kit/pull/12\t%s\t%s\tdev-feature\tmain\n" % (base, self.head))
        Path(ghmock, "diff.patch").write_text(self.git(self.repo, "diff", base, self.head) + "\n")
        out, rd, meta = self.collect("12", "--platform", "github", "--base-rule", "branch:develop")
        self.assertEqual(Path(rd, "files.txt").read_text().split(), ["g.txt"])
        self.assertIn("项目记录的基准分支", meta["base_desc"])


class NonInteractive(Project):
    def test_uses_recommended_option_and_marks_it_unconfirmed(self):
        out, rd, meta = self.collect("--non-interactive")
        self.assertNotIn("BASE_UNSET", out)
        self.assertIn("没法询问,用了推荐项「远端最新的目标分支」", out)
        self.assertIn("基准未确认", meta["base_desc"])
        self.assertIsNone(self.record(), "没确认过的不记成项目规则")
        self.assertEqual(len(self.pending()), 1, "不问了,照常开始审查")
        scope = [x for x in self.run_script("report-lines.sh", rd).stdout.splitlines() if x.startswith("SCOPE:")][0]
        self.assertIn("基准未确认", scope)

    def test_env_variable_works_too_and_a_recorded_rule_is_confirmed(self):
        self.env["CR_NONINTERACTIVE"] = "1"
        out, rd, meta = self.collect()
        self.assertIn("基准未确认", meta["base_desc"])
        self.collect("--base-rule", "target")
        out, rd, meta = self.collect()
        self.assertNotIn("基准未确认", meta["base_desc"])
        self.assertNotIn("没法询问", out)

    def test_closer_branch_is_offered_as_a_note(self):
        self.git(self.repo, "checkout", "-qb", "dev-feature", "origin/develop")
        self.commit(self.repo, {"g.txt": "g\n"}, "g Closes #2")
        out, rd, meta = self.collect("--non-interactive")
        self.assertIn("要对比它请加 --base develop", out)


class OneRound(Project):
    """基准、必填需求、可选需求资料在同一次运行里一起报出来。"""

    def test_everything_missing_is_reported_together(self):
        self.git(self.repo, "checkout", "-q", "feature")
        self.git(self.repo, "commit", "-q", "--amend", "-m", "no refs here")
        out, rd, meta = self.collect(skip_doc=False)
        lines = [x.split(":")[0] for x in out.splitlines() if x.startswith(("BASE_UNSET:", "SOURCES_MISSING:", "SOURCES_OPTIONAL:"))]
        self.assertEqual(sorted(lines), ["BASE_UNSET", "SOURCES_MISSING", "SOURCES_OPTIONAL"])
        self.assertIn("SOURCES_MISSING: 需求单", out)
        self.assertIn("SOURCES_OPTIONAL: 可选的需求文档", out)
        self.assertEqual(self.pending(), [])

    def test_nothing_to_ask_does_not_interrupt(self):
        self.collect("--base-rule", "target", "--source", "doc=skip")
        out, rd, meta = self.collect()
        self.assertNotRegex(out, "BASE_UNSET|SOURCES_MISSING|SOURCES_OPTIONAL")
        self.assertEqual(len(self.pending()), 1)


class OptionalSources(Project):
    def test_optional_material_is_asked_once_per_branch_and_skip_is_remembered(self):
        out, rd, meta = self.collect("--base-rule", "target", skip_doc=False)
        self.assertNotIn("SOURCES_MISSING", out)
        self.assertIn("SOURCES_OPTIONAL: 可选的需求文档还没有提供(doc);可以跳过,不算缺", out)
        self.assertEqual(meta["sources_optional"], "doc")
        sources = self.load(os.path.join(rd, "sources.json"))
        self.assertEqual((sources["missing"], sources["optional"]), ([], ["doc"]))
        out, rd, meta = self.collect("--source", "doc=skip")
        self.assertNotIn("SOURCES_OPTIONAL", out)
        out, rd, meta = self.collect(skip_doc=False)
        self.assertNotIn("SOURCES_OPTIONAL", out)
        self.git(self.repo, "checkout", "-qb", "feature-2", "feature")      # 另一个分支再问一次
        out, rd, meta = self.collect(skip_doc=False)
        self.assertIn("SOURCES_OPTIONAL", out)

    def test_a_link_given_is_remembered_too(self):
        self.collect("--base-rule", "target", "--source", "doc=https://docs.example.com/d/1")
        out, rd, meta = self.collect(skip_doc=False)
        self.assertNotIn("SOURCES_OPTIONAL", out)

    def test_local_doc_file_counts_as_given(self):
        Path(self.repo, "plan.md").write_text("方案\n", encoding="utf-8")
        out, rd, meta = self.collect("--doc", "plan.md", skip_doc=False)
        self.assertNotIn("SOURCES_OPTIONAL", out)

    def test_local_doc_also_satisfies_required_documents(self):
        self.config("intent.required = ticket, doc\n")
        Path(self.repo, "plan.md").write_text("方案\n", encoding="utf-8")
        out, rd, meta = self.collect("--doc", "plan.md", skip_doc=False)
        self.assertNotRegex(out, "SOURCES_MISSING|SOURCES_OPTIONAL")
        item = next(x for x in self.load(os.path.join(rd, "sources.json"))["items"] if x["kind"] == "doc")
        self.assertEqual(item["status"], "given")

    def test_not_asked_without_a_person(self):
        out, rd, meta = self.collect("--non-interactive", skip_doc=False)
        self.assertNotIn("SOURCES_OPTIONAL", out)
        self.assertEqual(meta["sources_optional"], "")

    def test_required_kind_is_not_also_optional(self):
        self.config("intent.required = ticket, doc\n")
        out, rd, meta = self.collect("--base-rule", "target", skip_doc=False)
        self.assertIn("SOURCES_MISSING: 需求文档", out)
        self.assertNotIn("SOURCES_OPTIONAL", out)


class RuleStore(fixture.Base):
    def test_parse_describe_and_roundtrip(self):
        self.assertEqual(base_rule.parse("target"), {"rule": "target"})
        self.assertEqual(base_rule.parse("branch:origin/release/1.2"), {"rule": "branch", "branch": "release/1.2"})
        self.assertEqual(base_rule.describe({"rule": "target"}), "远端最新的目标分支")
        self.assertEqual(base_rule.describe({"rule": "branch", "branch": "develop"}), "分支 develop")
        for bad in ("", "main", "branch:", "branch:a b", "branch:a..b", "branch:@{-1}", "sha:abc1234"):
            with self.assertRaises(ValueError, msg=bad):
                base_rule.parse(bad)
        d = os.path.join(self.tmp, "reviews", "p")
        self.assertIsNone(base_rule.load(d))
        base_rule.save(d, "example.com/acme/p", {"rule": "branch", "branch": "develop"})
        self.assertEqual(base_rule.load(d), {"rule": "branch", "branch": "develop"})
        self.assertEqual(Path(d, ".repo").read_text(), "example.com/acme/p\n")

    def test_unrecognised_record_is_ignored(self):
        d = os.path.join(self.tmp, "reviews", "p")
        os.makedirs(d)
        for text in ('{"base": {"rule": "commit", "sha": "abc1234", "branch": "develop"}}', '[]', 'null'):
            Path(d, "project.json").write_text(text, encoding="utf-8")
            self.assertIsNone(base_rule.load(d))

    def test_record_lives_next_to_feedback_in_the_project_directory(self):
        origin = self.bare("acme-kit")
        repo = self.make_repo("acme-kit", {"a.txt": "1\n"}, remote=origin)
        self.run_script("collect-diff.sh", "--base-rule", "target", cwd=repo)
        with mock.patch.dict(os.environ, {"CROSSCHECK_HOME": self.home}):
            pdir = crlib.project_dir(crlib.repo_identity(repo))
        self.assertTrue(pdir and os.path.isfile(os.path.join(pdir, "project.json")))
