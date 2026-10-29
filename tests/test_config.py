"""共享配置(路径、环境变量优先级、安装布局)和审查上下文哈希的测试。"""
import os
import re
import subprocess
from pathlib import Path

import fixture


class ContextHash(fixture.ShopRepo):
    def test_multiple_docs_and_sources_do_not_change_context_hash(self):
        Path(self.repo, "plan.md").write_text("方案 A\n", encoding="utf-8")
        Path(self.repo, "api.md").write_text("接口文档\n", encoding="utf-8")
        _, rd = self.collect("--doc", "plan.md", "--doc", "api.md")
        intent = Path(rd, "intent.md").read_text(encoding="utf-8")
        self.assertIn("## 文档 plan.md", intent)
        self.assertIn("## 文档 api.md", intent)
        h1 = Path(rd, "context-hash.txt").read_text()
        _, rd2 = self.collect("--doc", "plan.md", "--doc", "api.md", "--source", "requirement=skip")
        self.assertEqual(Path(rd2, "context-hash.txt").read_text(), h1)
        Path(self.repo, "api.md").write_text("接口文档 v2\n", encoding="utf-8")
        _, rd3 = self.collect("--doc", "plan.md", "--doc", "api.md")
        self.assertNotEqual(Path(rd3, "context-hash.txt").read_text(), h1)

    def test_review_inputs_change_the_hash_and_rule_maintenance_does_not(self):
        installed = self.install_plugin()
        _, rd = self.collect()
        hash_file = Path(rd, "context-hash.txt")
        skill = installed / "skills" / "review"

        def rehash():
            subprocess.run(["python3", str(skill / "scripts" / "context_hash.py"), rd],
                           cwd=self.tmp, env=self.env, capture_output=True, text=True, check=True)
            return hash_file.read_text()
        base = rehash()
        # 规则沉淀只服务 improve,不影响 CR 报告
        for name in ("mine_mrs.py", "rules-write.py"):
            with (skill / "scripts" / name).open("a", encoding="utf-8") as stream:
                stream.write("\n# changed\n")
            self.assertEqual(rehash(), base, name)
        # 这两个脚本的输出(代码块编号、调用方、需求来源)进了审查,必须计入
        for name in ("analysis_facts.py", "sources.py"):
            with (skill / "scripts" / name).open("a", encoding="utf-8") as stream:
                stream.write("\n# changed\n")
            changed = rehash()
            self.assertNotEqual(changed, base, name)
            base = changed


class PluginConfig(fixture.ShopRepo):
    def test_plugin_config_is_default_and_user_config_overrides(self):
        conf = os.path.join(self.tmp, "defaults.conf")
        Path(conf).write_text(("tools.requirement = 用 acme-kit:ticket-reader 读需求单\ntools.doc_reader = 用 acme-kit:doc-reader 读文档链接\n"
                                    "review.ask = requirement, doc\nmodel.finder = plugin model\n"), encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = conf
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单、需求文档 还没有提供", r.stdout)
        self.config("review.ask = requirement\n")
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求单 还没有提供", r.stdout)
        self.assertEqual(self.run_config("get", "model.finder").stdout.strip(), "plugin model")
        self.config("model.finder = mine\n")
        self.assertEqual(self.run_config("get", "model.finder").stdout.strip(), "mine")

    def test_config_paths_and_environment_precedence(self):
        self.assertEqual(self.run_config("plugin-path").stdout.strip(),
                         str(Path(fixture.PLUGIN, "config", "defaults.conf")))
        self.assertEqual(self.run_config("path").stdout.strip(), str(Path(self.home, "config")))
        personal = Path(self.tmp, "personal config")
        personal.write_text("model.finder = explicit\n", encoding="utf-8")
        self.config("model.finder = home\n")
        self.env["CROSSCHECK_CONFIG"] = str(personal)
        self.assertEqual(self.run_config("get", "model.finder").stdout.strip(), "explicit")
        self.env["CROSSCHECK_CONFIG"] = ""
        self.assertEqual(self.run_config("get", "model.finder").stdout.strip(), "home")
        self.env["CROSSCHECK_HOME"] = ""
        self.env["HOME"] = self.tmp
        self.assertEqual(self.run_config("path").stdout.strip(), str(Path(self.tmp, ".crosscheck", "config")))

    def test_installed_shared_config_and_symlinked_review(self):
        installed = self.install_plugin()
        (installed / "config" / "defaults.conf").write_text("model.finder = team model\n", encoding="utf-8")
        link = Path(self.tmp, "host skills", "review")
        link.parent.mkdir()
        link.symlink_to(installed / "skills" / "review", target_is_directory=True)
        result = subprocess.run(["sh", str(link / "scripts" / "collect-diff.sh"), "--base", "main"],
                                cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rd = self.review_dir(result.stdout)
        self.assertEqual(self.kv(os.path.join(rd, "meta.txt"))["finder_model"], "team model")
        self.assertEqual(subprocess.run(["python3", str(installed / "scripts" / "config.py"),
                                         "get", "model.finder"], cwd=self.tmp, env=self.env,
                                        capture_output=True, text=True, check=True).stdout.strip(), "team model")
        # 模板、共享解析器和实际覆盖文件都改变指纹,不靠模型参数变化间接验证。
        hash_file = Path(rd, "context-hash.txt")
        previous = hash_file.read_text()
        for path in (installed / "config" / "defaults.conf", installed / "scripts" / "config.py"):
            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n# changed fingerprint\n")
            subprocess.run(["python3", str(link / "scripts" / "context_hash.py"), rd],
                           cwd=self.tmp, env=self.env, capture_output=True, text=True, check=True)
            current = hash_file.read_text()
            self.assertNotEqual(previous, current)
            previous = current
        override = Path(self.tmp, "team override.conf")
        override.write_text("model.finder = team model\n", encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = str(override)
        override.write_text("model.finder = other model\n", encoding="utf-8")
        subprocess.run(["python3", str(link / "scripts" / "context_hash.py"), rd],
                       cwd=self.tmp, env=self.env, capture_output=True, text=True, check=True)
        self.assertNotEqual(previous, hash_file.read_text())

    SETTINGS = ("gitlab_hosts", "model.finder", "model.verifier",
                "tools.code_host", "tools.requirement",
                "tools.doc_reader", "review.ask", "describe.ask")

    def shipped_conf(self):
        return Path(fixture.PLUGIN, "config", "defaults.conf").read_text(encoding="utf-8")

    def test_shipped_plugin_config_sets_nothing(self):
        lines = [l for l in self.shipped_conf().splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, [])
        for key in self.SETTINGS:
            self.assertEqual(self.run_config("get", key).stdout, "", key)

    def test_shipped_plugin_config_lists_every_setting_commented_out(self):
        text = self.shipped_conf()
        for key in self.SETTINGS:
            self.assertRegex(text, r"(?m)^# %s =" % re.escape(key), key)

    def test_prompt_values_keep_hash_inside_urls_and_drop_trailing_comments(self):
        self.config("tools.requirement = 用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/browse/XX-1#comment-2 # 团队约定\n")
        out = self.run_config("get", "tools.requirement").stdout.strip()
        self.assertEqual(out, "用 acme-kit:ticket-reader 读需求单,链接形如 https://tickets.acme.example.com/browse/XX-1#comment-2")

    def test_empty_values_count_as_not_configured(self):
        conf = os.path.join(self.tmp, "defaults.conf")
        Path(conf).write_text("tools.requirement = 用 acme-kit:ticket-reader 读需求单\nreview.ask = requirement, doc\n", encoding="utf-8")
        self.env["CROSSCHECK_PLUGIN_CONFIG"] = conf
        self.config("tools.requirement =\nreview.ask = # 先不写\nmodel.finder =\n")
        self.assertEqual(self.run_config("get", "tools.requirement").stdout.strip(), "用 acme-kit:ticket-reader 读需求单")
        self.assertEqual(self.run_config("get", "review.ask").stdout.strip(), "requirement, doc")
        self.assertEqual(self.run_config("get", "model.finder").stdout, "")
        Path(conf).write_text("tools.doc_reader = 用 acme-kit:doc-reader 读文档链接\nreview.ask =\n", encoding="utf-8")
        self.config("")
        r, _ = self.collect()
        self.assertIn("SOURCES_MISSING: 需求文档 还没有提供(doc)", r.stdout)   # 空值等于没配置:review 用默认的 doc
