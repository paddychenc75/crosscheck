"""run-check.sh 的实现,用法和说明见 run-check.sh。"""
import os, re, subprocess, sys
sys.dont_write_bytecode = True
import crlib

d = sys.argv[1]
args = sys.argv[2:]

def die(msg):
    print("run-check: " + msg, file=sys.stderr)
    sys.exit(2)

kind = None
if len(args) >= 2 and args[0] == "--kind":
    kind = args[1]
    args = args[2:]
elif args and args[0].startswith("--kind="):
    kind = args[0].split("=", 1)[1]
    args = args[1:]
if kind not in ("test", "check", "repro"):
    die("需要 --kind test|check|repro")
if not args or args[0] != "--":
    die("命令要写在 -- 之后,例如:run-check.sh <review_dir> --kind test -- pytest tests/test_a.py")
argv = args[1:]
if not argv:
    die("-- 之后没有命令")
if len(argv) == 1 and re.search(r"\s", argv[0]):
    die("请把命令拆成多个参数,不要把整条命令放进一个带引号的参数里")

base = os.path.basename(argv[0])
if base in ("git", "gh", "glab"):
    die("不运行 %s:审查期间不改变仓库或平台状态" % base)
if base in ("sh", "bash", "zsh", "dash", "ksh") and any(a.startswith("-") and "c" in a.lstrip("-") for a in argv[1:2]):
    die("不接受 %s -c,请直接写要运行的命令" % base)
if base in ("sudo", "brew", "apt", "apt-get", "yum", "dnf", "curl", "wget"):
    die("不运行 %s" % base)
INSTALL = {"pip": {"install", "uninstall"}, "pip3": {"install", "uninstall"}, "npm": {"install", "i", "ci", "add", "update", "uninstall"},
           "yarn": {"add", "install", "upgrade", "remove"}, "pnpm": {"add", "install", "i", "update", "remove"},
           "go": {"get", "install", "mod"}, "cargo": {"install", "add", "update"}, "gem": {"install"},
           "poetry": {"add", "install", "update", "lock"}, "uv": {"add", "sync", "pip", "lock"},
           "mvn": {"install", "deploy"}, "gradle": {"publish"}, "./gradlew": {"publish"}, "composer": {"install", "require", "update"},
           "bundle": {"install", "update"}}
if any(a in INSTALL.get(base, ()) or a in INSTALL.get(argv[0], ()) for a in argv[1:]):
    die("不运行安装或更新依赖的命令")
if base in ("python", "python3") and len(argv) > 2 and argv[1] == "-m" and argv[2] == "pip":
    die("不运行安装或更新依赖的命令")
REWRITE = {"--fix", "--write", "-u", "--update", "--updateSnapshot", "--update-snapshots", "--fix-dry-run=false", "-w"}
bad = [a for a in argv[1:] if a in REWRITE or a.startswith(("--fix=", "--write="))]
if bad:
    die("不运行会改写文件的参数:%s" % " ".join(bad))

meta = crlib.read_kv(os.path.join(d, "meta.txt"))
root = meta.get("repo_root") or os.getcwd()
runs = os.path.join(d, "runs")
os.makedirs(runs, exist_ok=True)

n = 0
while True:
    n += 1
    rid = "r%d" % n
    try:
        fd = os.open(os.path.join(runs, rid + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        break
    except FileExistsError:
        continue

env = dict(os.environ, CI="1", PYTHONDONTWRITEBYTECODE="1")
try:
    timeout = int(os.environ.get("CR_RUN_TIMEOUT", "120"))
except ValueError:
    timeout = 120
log_path = os.path.join(runs, rid + ".log")
timed_out = False
with open(log_path, "wb") as log:
    try:
        p = subprocess.run(argv, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        timed_out, code = True, 124
        log.write(("\n[run-check] 超时(%d 秒),已终止\n" % timeout).encode())
    except OSError as e:
        code = 127
        log.write(("[run-check] 无法运行:%s\n" % e).encode())
text = open(log_path, "rb").read().decode("utf-8", "replace")

def parse_tests(t):
    """尽力从常见测试框架的输出里读出运行和失败的测试数;读不到返回 (None, None)。"""
    m = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+)", t)          # maven surefire
    if m:
        a, f, e = map(int, m[-1]); return a, f + e
    m = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", t)              # cargo
    if m:
        return sum(int(a) + int(b) for a, b in m), sum(int(b) for a, b in m)
    m = re.search(r"^Tests:\s+(.*?)(\d+) total", t, re.M)                            # jest
    if m:
        f = re.search(r"(\d+) failed", m.group(1)); return int(m.group(2)), int(f.group(1)) if f else 0
    m = re.search(r"^\s*Tests\s+(.*?)\((\d+)\)", t, re.M)                            # vitest
    if m:
        f = re.search(r"(\d+) failed", m.group(1)); return int(m.group(2)), int(f.group(1)) if f else 0
    m = re.search(r"^Ran (\d+) tests? in", t, re.M)                                   # unittest
    if m:
        f = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?", t)
        return int(m.group(1)), (int(f.group(1) or 0) + int(f.group(2) or 0)) if f else 0
    lines = [l for l in t.splitlines() if re.match(r"^=+ .* in [\d.]+s", l)]           # pytest
    if lines:
        s = lines[-1]
        cnt = {k: sum(int(x) for x in re.findall(r"(\d+) %s" % k, s)) for k in ("passed", "failed", "error", "errors")}
        failed = cnt["failed"] + cnt["error"] + cnt["errors"]
        return cnt["passed"] + failed, failed
    if re.search(r"no tests ran|collected 0 items", t):
        return 0, 0
    passes, fails = len(re.findall(r"^\s*--- PASS:", t, re.M)), len(re.findall(r"^\s*--- FAIL:", t, re.M))  # go test -v
    if passes or fails:
        return passes + fails, fails
    if re.search(r"\[no test files\]|no tests to run", t):
        return 0, 0
    return None, None

tests_run, tests_failed = parse_tests(text)
record = {"id": rid, "kind": kind, "argv": argv, "exit": code, "timed_out": timed_out,
          "log": rid + ".log", "tests_run": tests_run, "tests_failed": tests_failed}
crlib.save_json(os.path.join(runs, rid + ".json"), record)
tail = text.rstrip("\n").split("\n")[-20:]
print("\n".join(tail))
print("CR-RUN id=%s exit=%d" % (rid, code))
