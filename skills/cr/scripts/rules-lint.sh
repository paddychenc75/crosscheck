#!/bin/sh
# rules-lint.sh — 规则写入前的格式检查(只做确定性的部分,语义上的重复、能否对照代码检查由 agent 判断)。
#
# 用法: rules-lint.sh [--repo DIR]           检查项目里的 REVIEW.md(各级目录)和 .cr/ignore.md
#       rules-lint.sh --rules-repo DIR       检查组织规则仓库:packs/*/rules.md、正反例、CODEOWNERS、评测用例
#
# 项目规则:
#   ERROR  REVIEW.md 头部 disable 了规则却没写原因;同一个编号出现两次;severity 不是 critical/major/minor
#   WARN   规则没有编号;编号格式不对(项目规则 PRJ-NNN,抑制 IGN-NNN);scope 匹配不到任何文件;
#          单个文件超过 20 条;规则没写原因;extends 引用的规则包在本地找不到
# 组织规则仓库:
#   ERROR  规则包缺 pack / prefix / owner / applies_to;编号不以包的 prefix 开头;编号重复;
#          没有被标为 deprecated 的规则缺正例或反例(examples/<编号>.bad.* 和 .good.*);评测用例格式不对
#   WARN   CODEOWNERS 里没有这个包;单个包超过 20 条;规则没写原因
# 有 ERROR 时以 1 退出。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "rules-lint: 需要 python3" >&2; exit 2; }
mode=project; target=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) target=${2:-}; shift 2 ;;
    --rules-repo) mode=rules; target=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "rules-lint: 未知参数 $1" >&2; exit 2 ;;
  esac
done
if [ -z "$target" ]; then target=$(git rev-parse --show-toplevel 2>/dev/null || pwd); fi
[ -d "$target" ] || { echo "rules-lint: 目录不存在: $target" >&2; exit 2; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_MODE=$mode CR_TARGET=$(cd "$target" && pwd) python3 - <<'PY'
import glob, json, os, re, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

mode, root = os.environ["CR_MODE"], os.environ["CR_TARGET"]
errors, warns = [], []
def E(f, line, msg): errors.append("ERROR %s%s %s" % (f, ":%d" % line if line else "", msg))
def W(f, line, msg): warns.append("WARN  %s%s %s" % (f, ":%d" % line if line else "", msg))
REASON = re.compile(r"(原因|因为|以免|否则|避免|because|since|so that)", re.I)
SEV = ("critical", "major", "minor")

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

def check_rules(rel, text, idpat, idname, all_files, seen_ids):
    fm, body = crlib.parse_frontmatter(text)
    off = len(text.split("\n")) - len(body.split("\n"))
    rules = crlib.split_rules(body)
    if len(rules) > 20:
        W(rel, 0, "有 %d 条规则,超过 20 条,最重要的几条容易被稀释;考虑合并、挪到目录级,或删掉命中最少的" % len(rules))
    for r in rules:
        ln = r["start"] + off
        if not r["id"]:
            W(rel, ln, "规则没有编号,只能按文件核对:%s" % r["text"][:40])
            continue
        if not re.fullmatch(idpat, r["id"]):
            W(rel, ln, "编号 %s 的格式不对,应为 %s" % (r["id"], idname))
        if r["id"] in seen_ids:
            E(rel, ln, "编号 %s 重复(另一处在 %s)" % (r["id"], seen_ids[r["id"]]))
        seen_ids.setdefault(r["id"], "%s:%d" % (rel, ln))
        sev = r["meta"].get("severity")
        if sev and sev not in SEV:
            E(rel, ln, "%s 的 severity=%s 不对,应为 critical / major / minor" % (r["id"], sev))
        scope = r["meta"].get("scope")
        if scope and all_files is not None and not any(crlib.any_match(scope.split(","), f) for f in all_files):
            W(rel, ln, "%s 的作用域 %s 匹配不到任何文件" % (r["id"], scope))
        if not REASON.search(r["text"]) and idname != "IGN-NNN":
            W(rel, ln, "%s 没有写原因(写一句原因,verifier 能据此判断边界,后来的人也知道能不能删)" % r["id"])
    return fm, rules

if mode == "project":
    try:
        files = crlib.Source(root).files()
    except Exception:
        files = []
    seen = {}
    for rel in sorted(f for f in files if f == "REVIEW.md" or f.endswith("/REVIEW.md")):
        fm, _ = check_rules(rel, read(os.path.join(root, rel)), r"PRJ-\d{3,}", "PRJ-NNN", files, seen)
        if rel == "REVIEW.md":
            for item in crlib.as_list(fm.get("disable")):
                k, _, why = item.partition(":")
                if not why.strip():
                    E(rel, 0, "disable 了 %s 但没有写原因,这条 disable 不会生效" % k.strip())
            for k, v in crlib.as_map(fm.get("severity")).items():
                if v not in SEV:
                    E(rel, 0, "severity 设置里 %s: %s 不对,应为 critical / major / minor" % (k, v))
            rd = crlib.rules_dir()
            for pack in crlib.as_list(fm.get("extends")):
                if not rd or not os.path.isfile(os.path.join(rd, "packs", pack, "rules.md")):
                    W(rel, 0, "extends 引用的规则包 %s 在本地找不到(先运行 sync-rules.sh,或检查包名)" % pack)
            for g in crlib.as_list(fm.get("skip")) + crlib.as_list(fm.get("risk_paths")):
                if not any(crlib.glob_match(g, f) for f in files):
                    W(rel, 0, "路径 %s 匹配不到任何文件" % g)
    if os.path.isfile(os.path.join(root, ".cr", "ignore.md")):
        check_rules(".cr/ignore.md", read(os.path.join(root, ".cr", "ignore.md")), r"IGN-\d{3,}", "IGN-NNN", files, {})
else:
    seen = {}
    owners = read(os.path.join(root, "CODEOWNERS")) if os.path.isfile(os.path.join(root, "CODEOWNERS")) else ""
    for p in sorted(glob.glob(os.path.join(root, "packs", "**", "rules.md"), recursive=True)):
        rel = os.path.relpath(p, root)
        pack = os.path.dirname(os.path.relpath(p, os.path.join(root, "packs")))
        text = read(p)
        fm, _ = crlib.parse_frontmatter(text)
        for k in ("pack", "prefix", "owner", "applies_to"):
            if not fm.get(k):
                E(rel, 0, "头部缺少 %s" % k)
        if fm.get("pack") and fm["pack"] != pack:
            E(rel, 0, "头部的 pack=%s 和目录 %s 不一致" % (fm["pack"], pack))
        prefix = str(fm.get("prefix") or "")
        _, rules = check_rules(rel, text, re.escape(prefix) + r"-\d{3,}" if prefix else r"[A-Z][A-Z0-9-]*-\d{3,}", (prefix or "ORG-XXX") + "-NNN", None, seen)
        if owners and ("packs/%s" % pack) not in owners:
            W("CODEOWNERS", 0, "没有为 packs/%s/ 指定负责人" % pack)
        if not owners:
            W("CODEOWNERS", 0, "规则仓库没有 CODEOWNERS 文件")
        off = len(text.split("\n")) - len(crlib.parse_frontmatter(text)[1].split("\n"))
        ex = os.path.join(os.path.dirname(p), "examples")
        names = os.listdir(ex) if os.path.isdir(ex) else []
        for r in rules:
            if not r["id"] or r["meta"].get("status") == "deprecated":
                continue
            for kind in ("bad", "good"):
                if not any(n.startswith("%s.%s." % (r["id"], kind)) for n in names):
                    E(rel, r["start"] + off, "%s 缺少%s例 examples/%s.%s.<扩展名>" % (r["id"], "反" if kind == "bad" else "正", r["id"], kind))
    for p in sorted(glob.glob(os.path.join(root, "evals", "cases", "*.json"))):
        rel = os.path.relpath(p, root)
        try:
            c = json.load(open(p, encoding="utf-8"))
        except ValueError as e:
            E(rel, 0, "不是合法的 JSON:%s" % e); continue
        for k in ("id", "repo", "base", "head", "expect", "source"):
            if k not in c:
                E(rel, 0, "缺少字段 %s" % k)
        if str(c.get("source", "")).split(":")[0] not in ("fix-commit", "clean", "missed"):
            E(rel, 0, "source 应为 fix-commit、clean 或 missed(可以加 :说明)")
        for i, x in enumerate(c.get("expect") or []):
            for k in ("file", "lines", "category", "desc"):
                if k not in x:
                    E(rel, 0, "expect[%d] 缺少 %s" % (i, k))
    b = os.path.join(root, "evals", "baseline.json")
    if os.path.isfile(b):
        try:
            json.load(open(b, encoding="utf-8"))
        except ValueError as e:
            E("evals/baseline.json", 0, "不是合法的 JSON:%s" % e)

for line in errors + warns:
    print(line)
print("rules-lint: %d 个错误,%d 个提醒" % (len(errors), len(warns)))
sys.exit(1 if errors else 0)
PY
