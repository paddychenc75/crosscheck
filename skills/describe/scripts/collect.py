"""为 PR/MR 描述收集材料:调用 review 的 collect-diff.sh,再整理描述用的事实。

用法: python3 collect.py [target] [collect-diff.sh 的选项]...
  选项原样交给 <plugin_dir>/skills/review/scripts/collect-diff.sh:origin 绑定、只读、基准的握手都沿用它。
  另外总是加 --full(不用审查记录做增量,描述要看完整改动)和 --allow-large(描述不分批,不问范围)。
  collect-diff.sh 的输出原样转出,最后一行仍是材料目录;没有要先问用户的提示时,再写 describe-facts.json。

需求来源的问法和 review 不同,由这里决定,与 intent.required、审查记录里问过没有都无关:
  需求单  每次都问一次:没有 --source requirement=<链接 | skip | none:原因> 就输出 SOURCES_MISSING。非交互运行不问,
          记为 requirement=unasked(描述里写「未提供(非交互运行,没有询问)」)
  需求文档 不问,用户给了(--source doc=…、--doc、--source-dir)才收下;没给就记为 doc=unasked
  collect-diff.sh 自己输出的 SOURCES_MISSING / SOURCES_OPTIONAL 丢掉,不转出。

不往审查记录里留东西:collect-diff.sh 开始审查时会写 reviews/<项目>/<目标>/pending(「进行中」标记),
--source、--intent 会写进 reviews/<项目>/sources/<分支>.json(review 之后要不要再问需求单看它)。
这里在它结束后把这两处恢复成运行前的样子(原来没有就删掉,目录是这次新建的也删掉):review 下次运行不会把这次描述
当成没保存的审查,也不会因为这里的回答少问或多问。审查记录(baseline、latest、各次记录目录)只由 save-review.sh 写,这里不调用它。
"""
import glob
import os
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
REVIEW_SCRIPTS = os.path.join(HERE, "..", "..", "review", "scripts")
sys.path.insert(0, REVIEW_SCRIPTS)
import crlib  # noqa: E402

ASK = ("BASE_UNSET:", "SOURCES_MISSING:", "SOURCES_OPTIONAL:", "NEED_CODE_HOST:", "EMPTY:")
REVIEW_SOURCES = ("SOURCES_MISSING:", "SOURCES_OPTIONAL:")   # 按 review 的规则问的,describe 另有问法
REVIEW_ONLY = "NOTE: 上一次对这个目标的审查"   # 说的是没保存的审查,和描述无关
ASK_REQUIREMENT = ("SOURCES_MISSING: 需求单 还没有提供(requirement)。每次生成描述都要问一次:请贴需求单链接;"
              "没有需求单就说一声(hotfix、重构等);也可以跳过。")


def has_source(argv, kind):
    return any(a == "--source" and i + 1 < len(argv) and argv[i + 1].startswith(kind + "=") for i, a in enumerate(argv))


def snapshot(reviews):
    """运行前的 pending 标记、来源记录和已有目录。"""
    files = {}
    for pattern in (("*", "*", "pending"), ("*", "sources", "*.json")):
        for p in glob.glob(os.path.join(glob.escape(reviews), *pattern)):
            try:
                with open(p, "rb") as f:
                    files[p] = f.read()
            except OSError:
                pass
    return files, set(glob.glob(os.path.join(glob.escape(reviews), "*"))) | set(glob.glob(os.path.join(glob.escape(reviews), "*", "*")))


def prune(path, dirs):
    while path not in dirs and os.path.isdir(path) and not os.listdir(path):
        os.rmdir(path)
        path = os.path.dirname(path)


def put(path, data, dirs):
    """恢复成运行前的内容;运行前没有就删掉,目录是这次新建的也删掉。"""
    if data is not None:
        with open(path, "wb") as f:
            f.write(data)
        return
    if os.path.exists(path):
        os.remove(path)
    prune(os.path.dirname(path), dirs)


def restore(reviews, before, out):
    files, dirs = before
    real = os.path.realpath(out) if out else None
    if not real:
        return
    for p in glob.glob(os.path.join(glob.escape(reviews), "*", "*", "pending")):
        written = crlib.read_kv(p).get("review_dir", "")
        if written and os.path.realpath(written) == real:
            put(p, files.get(p), dirs)
    store = crlib.load_json(os.path.join(out, "sources.json"), {}).get("store", "")
    if store and (os.path.isfile(store) or store in files):
        put(store, files.get(store), dirs)


def main(argv):
    reviews = os.path.join(crlib.home(), "reviews")
    before = snapshot(reviews)
    non_interactive = "--non-interactive" in argv or os.environ.get("CR_NONINTERACTIVE") == "1"
    ask_requirement = not has_source(argv, "requirement") and not non_interactive
    extra = ["--full", "--allow-large"]
    if not has_source(argv, "requirement") and non_interactive:
        extra += ["--source", "requirement=unasked"]
    if not has_source(argv, "doc") and not {"--doc", "--plan", "--source-dir"} & set(argv):
        extra += ["--source", "doc=unasked"]
    r = subprocess.run(["sh", os.path.join(REVIEW_SCRIPTS, "collect-diff.sh")] + argv + extra, stdout=subprocess.PIPE, text=True)
    lines = [x for x in r.stdout.splitlines() if not x.startswith((REVIEW_ONLY,) + REVIEW_SOURCES)]
    out = lines[-1].strip() if r.returncode == 0 and lines else ""
    if out and not os.path.isfile(os.path.join(out, "meta.txt")):
        out = ""
    if out and ask_requirement and not any(x.startswith("EMPTY:") for x in lines):
        lines.insert(-1, ASK_REQUIREMENT)
    try:
        restore(reviews, before, out)
    except OSError as e:
        lines.insert(-1 if out else len(lines), "NOTE: 没能恢复审查记录里的进行中标记或来源记录(%s)" % e)
    if out and not any(x.startswith(ASK) for x in lines):
        f = subprocess.run([sys.executable, os.path.join(HERE, "describe_facts.py"), out], stdout=subprocess.PIPE, text=True)
        lines.insert(-1, f.stdout.strip() if f.returncode == 0 else "NOTE: describe_facts.py 运行失败,描述里只有脚本能直接给出的部分")
    if lines:
        print("\n".join(lines))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
