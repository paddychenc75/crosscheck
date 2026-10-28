"""项目的对比基准规则:按项目记一次,每次审查都重新解析成当时的最新值,不记某个提交。

规则只有两种:
  target        远端最新的目标分支(MR / PR 的目标分支,没有 MR 时是默认分支)
  branch:<名>   远端最新的某个分支,如 branch:develop
提交哈希(--base-sha)只对当次有效,不记成规则:下次审查时那个提交早就不是对的基准了。

记录:<项目记录目录>/project.json,和 feedback.jsonl 放在一起(项目记录目录是 reviews/ 下 .repo 对得上的那个):
  {"base": {"rule": "target"} 或 {"rule": "branch", "branch": "develop"}, "updated_at": "..."}

用法(collect-diff.sh 调用):
  python3 base_rule.py check 规则                      校验并打印规范化的规则,不合法时报错退出
  python3 base_rule.py get 项目记录目录                打印规则(target 或 branch:<名>);没有记录什么都不打印
  python3 base_rule.py set 项目记录目录 repo_id 规则   记下规则(目录不存在时新建并写 .repo)
"""
import datetime
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import crlib

FILE = "project.json"
LABEL = {"target": "远端最新的目标分支"}


def parse(text):
    """'target' 或 'branch:<名>' → {"rule": ...};其他写法报 ValueError。"""
    t = (text or "").strip()
    if t == "target":
        return {"rule": "target"}
    m = re.fullmatch(r"branch:(.+)", t)
    if not m:
        raise ValueError("基准规则要写成 target(远端最新的目标分支)或 branch:<分支名>: %s" % t)
    name = re.sub(r"^origin/", "", m.group(1).strip())
    if name.startswith("-") or subprocess.run(["git", "check-ref-format", "refs/heads/" + name], capture_output=True).returncode != 0:
        raise ValueError("不是合法的分支名: %s" % name)
    return {"rule": "branch", "branch": name}


def describe(rule):
    return "分支 %s" % rule["branch"] if rule.get("rule") == "branch" else LABEL["target"]


def load(project_dir):
    """项目记录的基准规则;没有、或记录的内容不认识时返回 None。"""
    data = crlib.load_json(os.path.join(project_dir, FILE), {}) if project_dir else {}
    base = data.get("base") if isinstance(data, dict) else None
    if not isinstance(base, dict) or base.get("rule") not in ("target", "branch"):
        return None
    try:
        return parse("target" if base["rule"] == "target" else "branch:%s" % base.get("branch", ""))
    except ValueError:
        return None


def save(project_dir, repo_id, rule):
    os.makedirs(project_dir, exist_ok=True)
    marker = os.path.join(project_dir, ".repo")
    if not os.path.isfile(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write(repo_id + "\n")
    path = os.path.join(project_dir, FILE)
    data = crlib.load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data["base"] = rule
    data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    crlib.save_json(path, data)


def main(argv):
    if len(argv) == 2 and argv[0] == "check":
        try:
            rule = parse(argv[1])
            print("branch:" + rule["branch"] if rule["rule"] == "branch" else "target")
        except ValueError as e:
            sys.exit(str(e))
    elif len(argv) == 2 and argv[0] == "get":
        rule = load(argv[1])
        if rule:
            print("branch:" + rule["branch"] if rule["rule"] == "branch" else "target")
    elif len(argv) == 4 and argv[0] == "set":
        try:
            save(argv[1], argv[2], parse(argv[3]))
        except ValueError as e:
            sys.exit(str(e))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
