"""插件级配置入口,供所有技能共用;仅使用标准库,兼容 Python 3.8+。
记录目录固定是 ~/.crosscheck(个人配置在 ~/.crosscheck/config),家目录只在 home() 里取。
写记录目录失败时统一走 write_denied:说明路径和怎么申请写入权限,以 WRITE_DENIED 退出,不打印异常栈。"""
import contextlib
import errno
import os
import re
import sys
import tempfile

WRITE_DENIED = 77   # 写不了 ~/.crosscheck 时的退出码,各脚本一致


def home():
    return os.path.join(os.path.expanduser("~"), ".crosscheck")


def config_path():
    return os.path.join(home(), "config")


def _denied_message(path, err):
    reason = "(%s)" % err.strerror if isinstance(err, OSError) and err.strerror else ""
    sys.stderr.write(
        "crosscheck: 无法写入 %s%s\n"
        "宿主不允许写入 ~/.crosscheck。请按宿主的方式申请一次写入权限(只针对这条命令、只写 ~/.crosscheck 这个目录),"
        "获批后重新运行同一条命令。\n"
        "用户拒绝或宿主不支持申请时请停下,请用户在宿主里放行 ~/.crosscheck 的写入后重试。\n" % (path, reason))


def write_denied(path, err=None):
    _denied_message(path, err)
    sys.exit(WRITE_DENIED)


@contextlib.contextmanager
def record_write(path):
    """在记录目录里创建目录、写文件:失败时走 write_denied。"""
    try:
        yield
    except OSError as e:
        write_denied(path, e)


def makedirs(path):
    with record_write(path):
        os.makedirs(path, exist_ok=True)


def probe():
    """开始前试写一次:在 ~/.crosscheck 下建再删一个探测文件,写不了就直接报错退出,不等流程跑到一半。"""
    d = home()
    with record_write(d):
        os.makedirs(d, exist_ok=True)
        fd, p = tempfile.mkstemp(prefix=".probe-", dir=d)
        os.close(fd)
        os.remove(p)


def _under_home(path):
    try:
        return os.path.commonpath([os.path.abspath(path), home()]) == home()
    except (TypeError, ValueError):
        return False


_previous_hook = sys.excepthook


def _excepthook(kind, value, tb):
    # 兜底:没有经过 record_write 的写入在记录目录里被拒时,也给同样的提示,不打印异常栈。
    if isinstance(value, OSError) and value.errno in (errno.EACCES, errno.EPERM, errno.EROFS) \
            and value.filename and _under_home(value.filename):
        _denied_message(value.filename, value)
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        os._exit(WRITE_DENIED)
    _previous_hook(kind, value, tb)


sys.excepthook = _excepthook


def plugin_config_path():
    """随插件分发的配置(config/defaults.conf),团队在自己的插件市场里改它;个人配置逐项覆盖它。"""
    return os.environ.get("CROSSCHECK_PLUGIN_CONFIG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "defaults.conf")


def config():
    """读插件配置和个人配置(个人的逐项覆盖插件的)。注释行和值为空的行都当作没配置。
    行尾注释只认「空白加 #」,所以值里的 URL#锚点 不会被截断,但写给 agent 的一句话里不要出现「空格加 #」。"""
    d = {}
    for path in (plugin_config_path(), config_path()):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    v = re.sub(r"\s+#.*$", "", v).strip()
                    if not v:
                        continue
                    if v.startswith("~/"):
                        v = os.path.join(os.path.expanduser("~"), v[2:])
                    d[k.strip()] = v
        except OSError:
            pass
    return d


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args == ["path"]:
        print(config_path())
    elif args == ["plugin-path"]:
        print(plugin_config_path())
    elif args == ["home"]:
        print(home())
    elif args == ["probe"]:
        probe()
    elif len(args) == 2 and args[0] == "mkdir":
        makedirs(args[1])
    elif len(args) == 2 and args[0] == "get":
        value = config().get(args[1], "")
        if value:
            print(value)
    else:
        sys.exit("用法: config.py get KEY | path | plugin-path | home | probe | mkdir DIR")


if __name__ == "__main__":
    main()
