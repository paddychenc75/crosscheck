"""记录目录(~/.crosscheck)写不了时:各入口开始前就停下,给出路径和申请写入权限的提示,以固定退出码结束,不打印异常栈。"""
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import fixture

WRITE_DENIED = 77
ENTRY_SCRIPTS = {   # 各技能的入口:一开始就要试写记录目录
    "skills/review/scripts/collect-diff.sh": 'sh "$config_sh" probe',
    "skills/review/scripts/save-review.sh": 'sh "$config_sh" probe',
    "skills/review/scripts/mine-mrs.sh": "scripts/config.sh\" probe",
    "skills/describe/scripts/collect.py": "crlib.probe()",
}
WRITE = re.compile(r"\bos\.makedirs\(|\bos\.mkdir\(|\.mkdir\(|(^|[;&|(]|\bthen|\bdo)\s*mkdir\b")   # shell 里命令位置的 mkdir


def not_root():
    return hasattr(os, "geteuid") and os.geteuid() != 0


class Denied(fixture.ShopRepo):
    def setUp(self):
        super().setUp()
        self.good_env = dict(self.env)
        self.read_only = []

    def tearDown(self):
        for record in self.read_only:
            record.chmod(stat.S_IRWXU)   # 先恢复权限,临时目录才删得掉
        super().tearDown()

    def home_under_file(self):
        """HOME 指到一个普通文件下面:root 用户也写不了。"""
        blocker = Path(self.tmp, "blocker")
        blocker.write_text("x\n")
        self.env["HOME"] = str(blocker / "home")
        return str(blocker / "home" / ".crosscheck")

    def home_read_only(self):
        """~/.crosscheck 已经存在但不可写:宿主沙箱的常见情况,目录在、写不进去。"""
        home = Path(tempfile.mkdtemp(prefix="ro-home-", dir=self.tmp))
        record = home / ".crosscheck"
        record.mkdir()
        record.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.read_only.append(record)
        self.env["HOME"] = str(home)
        return str(record)

    def assertDenied(self, r, path):
        self.assertEqual(r.returncode, WRITE_DENIED, r.stdout + r.stderr)
        self.assertIn(path, r.stderr)
        self.assertIn("宿主不允许写入 ~/.crosscheck", r.stderr)
        self.assertIn("申请一次写入权限", r.stderr)
        self.assertIn("重新运行同一条命令", r.stderr)
        self.assertIn("放行 ~/.crosscheck 的写入", r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def saved_ready_review(self):
        """用可写的 HOME 收集好一次审查,只差保存。"""
        self.env = dict(self.good_env)
        _, rd = self.collect()
        Path(rd, "report.md").write_text("报告\n", encoding="utf-8")
        Path(rd, "findings.json").write_text("[]\n", encoding="utf-8")
        return rd

    def check_entries(self, make_home):
        cases = {
            "collect-diff.sh": lambda: self.run_script("collect-diff.sh", "--base", "main", cwd=self.repo, check=False),
            "mine-mrs.sh": lambda: self.run_script("mine-mrs.sh", cwd=self.repo, check=False),
            "describe collect.sh": lambda: self.run_describe("collect.sh", "--base", "main", "--non-interactive",
                                                            cwd=self.repo, check=False),
        }
        for name, run in cases.items():
            with self.subTest(name):
                self.env = dict(self.good_env)
                path = make_home()
                self.assertDenied(run(), path)
        with self.subTest("save-review.sh"):
            rd = self.saved_ready_review()
            store = self.kv(os.path.join(rd, "meta.txt"))["store_dir"]
            path = make_home()
            self.assertDenied(self.run_script("save-review.sh", rd, check=False), path)
            self.assertFalse(os.path.exists(os.path.join(store, "latest")))

    def test_entries_stop_when_home_is_under_a_file(self):
        self.check_entries(self.home_under_file)

    @unittest.skipUnless(not_root(), "root 用户下 chmod 不生效")
    def test_entries_stop_when_record_dir_is_read_only(self):
        self.check_entries(self.home_read_only)

    @unittest.skipUnless(not_root(), "root 用户下 chmod 不生效")
    def test_python_writes_without_helper_get_the_same_message(self):
        """兜底:脚本里没经过 record_write 的写入被拒时,也是同样的提示和退出码。"""
        record = self.home_read_only()
        code = "import os, crlib; crlib.save_json(os.path.join(crlib.home(), 'x.json'), {})"
        r = subprocess.run(["python3", "-c", code], cwd=fixture.SCRIPTS, env=self.env, capture_output=True, text=True)
        self.assertDenied(r, os.path.join(record, "x.json"))

    def test_record_writes_in_python_scripts(self):
        path = self.home_under_file()
        pdir = os.path.join(path, "reviews", "shop")
        r = self.run_py("base_rule.py", "set", pdir, "shop", "target", check=False)
        self.assertDenied(r, pdir)

    def test_writable_home_passes_probe_and_leaves_nothing(self):
        r = self.run_config("probe")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(os.listdir(self.home), [])


class WritePoints(unittest.TestCase):
    """以后新增脚本也不能漏:写目录一律走 config.py 的 makedirs / record_write,例外要在同一行注释说明。"""

    def sources(self):
        for top in ("skills", "scripts"):
            for d, _, names in os.walk(os.path.join(fixture.PLUGIN, top)):
                for name in names:
                    if name.endswith((".py", ".sh")):
                        yield os.path.join(d, name)

    def test_no_bare_directory_creation(self):
        helpers = {os.path.join(fixture.PLUGIN, "scripts", name) for name in ("config.py", "config.sh")}
        found = 0
        for path in self.sources():
            if path in helpers:
                continue
            lines = Path(path).read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                if not WRITE.search(line) or line.lstrip().startswith("#"):
                    continue
                found += 1
                rel = "%s:%d" % (os.path.relpath(path, fixture.PLUGIN), i + 1)
                with self.subTest(rel):
                    allowed = "写记录目录的例外" in line or (i and "record_write" in lines[i - 1])
                    self.assertTrue(allowed, "%s 直接建目录:写 ~/.crosscheck 请用 crlib.makedirs / config.sh mkdir;"
                                             "不在记录目录的,在同一行注释「写记录目录的例外:原因」\n%s" % (rel, line))
        self.assertGreater(found, 0)

    def test_entries_probe_first(self):
        for rel, probe in ENTRY_SCRIPTS.items():
            with self.subTest(rel):
                self.assertIn(probe, Path(fixture.PLUGIN, rel).read_text(encoding="utf-8"))

    def test_improve_probes_before_menu(self):
        text = Path(fixture.PLUGIN, "skills", "improve", "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('config.sh" probe', text)
        self.assertLess(text.index('config.sh" probe'), text.index("菜单:"))

    def test_skills_explain_how_to_handle_denied_writes(self):
        for skill in ("review", "improve", "describe"):
            with self.subTest(skill):
                text = Path(fixture.PLUGIN, "skills", skill, "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("宿主不允许写入 ~/.crosscheck", text)
                self.assertIn("重新运行同一条命令", text)
                self.assertIn("不要换目录", text)

    def test_record_dir_has_no_environment_override(self):
        for path in self.sources():
            text = Path(path).read_text(encoding="utf-8")
            with self.subTest(os.path.relpath(path, fixture.PLUGIN)):
                self.assertNotRegex(text, r"CROSSCHECK_(HOME|CONFIG)\b")


if __name__ == "__main__":
    unittest.main()
