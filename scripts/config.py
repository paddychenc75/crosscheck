"""插件级配置入口,供所有技能共用;仅使用标准库,兼容 Python 3.8+。"""
import os
import re
import sys


def config_path():
    return os.environ.get("CROSSCHECK_CONFIG") or os.path.join(
        os.environ.get("CROSSCHECK_HOME") or os.path.join(os.path.expanduser("~"), ".crosscheck"), "config")


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


def home():
    return os.environ.get("CROSSCHECK_HOME") or os.path.join(os.path.expanduser("~"), ".crosscheck")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args == ["path"]:
        print(config_path())
    elif args == ["plugin-path"]:
        print(plugin_config_path())
    elif len(args) == 2 and args[0] == "get":
        value = config().get(args[1], "")
        if value:
            print(value)
    else:
        sys.exit("用法: config.py get KEY | path | plugin-path")


if __name__ == "__main__":
    main()
