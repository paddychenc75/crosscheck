#!/bin/sh
# rules-health.sh — 规则复核清单:给负责人定期复核规则用。只读,不下结论,由负责人决定。
#
# 用法: rules-health.sh [--repo DIR] [--months 6]
#
# 规则是人批准才生效的,去留也由负责人定期主动复核决定,不靠命中次数这类统计。
# 这份清单按规则文件(以及组织规则包的负责人)列出每条规则和它上次复核的时间,
# 超过 --months 个月(默认 6)没复核的排在前面,并附上几项只读仓库就能做的检查,帮负责人省事:
#   作用域失效    scope 已经匹配不到任何文件
#   引用失效      规则里用 `…` 提到的文件、函数、包在代码库里已经找不到
#                 (要求使用的东西没了,规则多半过时;禁止的东西没了,说明都遵守了,不用动)
#   重复          同层或跨层有几乎一样的规则
#   缺元数据      没有编号,或没有 added / reviewed 日期
#   太长          单个文件超过 20 条
# 负责人确认保留的规则,用 propose-rules.sh 的 confirm 更新 reviewed=<年-月>;要改写或删除的用 replace / remove。
# 不读审查记录,也不需要团队共享任何数据。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "rules-health: 需要 python3" >&2; exit 2; }
repo_arg=""; months=6
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo_arg=${2:-}; shift 2 ;;
    --months) months=${2:-6}; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "rules-health: 未知参数 $1" >&2; exit 2 ;;
  esac
done
if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || { echo "rules-health: 目录不存在" >&2; exit 2; }; fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "rules-health: 当前目录不是 git 仓库" >&2; exit 2; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_ROOT=$(git rev-parse --show-toplevel) CR_MONTHS=$months python3 - <<'PY'
import datetime, difflib, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

root, limit = os.environ["CR_ROOT"], int(os.environ["CR_MONTHS"])
src = crlib.Source(root)
files = src.files()
now = datetime.date.today()

RULE_FILES = re.compile(r"(^|/)(REVIEW\.md|CLAUDE\.md|AGENTS(\.override)?\.md|GEMINI\.md|CONVENTIONS\.md)$|^\.cr/|^\.cursor/|^\.claude/|^\.github/(copilot-instructions\.md|instructions/)")
code_files = [f for f in files if not RULE_FILES.search(f)]

rules = []
def load(path, layer, text, owner=""):
    fm, body = crlib.parse_frontmatter(text)
    off = len(text.split("\n")) - len(body.split("\n"))
    for r in crlib.split_rules(body):
        rules.append(dict(r, layer=layer, file=path, line=r["start"] + off, owner=owner))
    return fm
fm_root = {}
for f in sorted(x for x in files if x == "REVIEW.md" or x.endswith("/REVIEW.md")):
    fm = load(f, "L2" if f == "REVIEW.md" else "L3", src.read(f) or "")
    if f == "REVIEW.md":
        fm_root = fm
if src.exists(".cr/ignore.md"):
    load(".cr/ignore.md", "ignore", src.read(".cr/ignore.md") or "")
rd = crlib.rules_dir()
for pack in crlib.as_list(fm_root.get("extends")):
    p = os.path.join(rd, "packs", pack, "rules.md") if rd else ""
    if p and os.path.isfile(p):
        t = open(p, encoding="utf-8").read()
        load("packs/%s/rules.md" % pack, "L1", t, str(crlib.parse_frontmatter(t)[0].get("owner") or ""))

def missing_refs(text):
    out = []
    for tok in re.findall(r"`([^`\n]{2,80})`", text):
        t = tok.strip()
        if re.search(r"[*?{]", t) or " " in t:
            continue
        if "/" in t or (re.search(r"\.[A-Za-z]{1,5}$", t) and not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", t)):
            p = t.lstrip("./")
            if not any(f == p or f.startswith(p.rstrip("/") + "/") for f in files):
                out.append(t)
            continue
        name = re.sub(r"\(.*$", "", t).split(".")[-1]
        if len(name) < 3 or not re.fullmatch(r"[A-Za-z_][\w-]*", name):
            continue
        r = subprocess.run(["git", "-C", root, "grep", "-q", "-w", "-F", "-e", name, "--"] + code_files[:5000], capture_output=True)
        if r.returncode == 1:
            out.append(t)
    return out

def months_since(ym):
    if not re.fullmatch(r"\d{4}-\d{2}", ym or ""):
        return None
    y, m = map(int, ym.split("-"))
    return (now.year - y) * 12 + now.month - m

norm = lambda s: re.sub(r"\s+", "", s.lower())
rows = []
for i, r in enumerate(rules):
    if r["meta"].get("status") == "deprecated":
        continue
    notes = []
    ym = r["meta"].get("reviewed") or r["meta"].get("added")
    age = months_since(ym)
    if not r["id"]:
        notes.append("没有编号")
    if age is None:
        notes.append("没有 added / reviewed 日期")
    scope = r["meta"].get("scope")
    if scope and not any(crlib.any_match(scope.split(","), f) for f in files):
        notes.append("作用域 %s 匹配不到任何文件" % scope)
    gone = missing_refs(r["text"])
    if gone:
        notes.append("提到的 %s 在代码库里找不到" % "、".join("`%s`" % g for g in gone))
    for k, o in enumerate(rules):
        if k != i and difflib.SequenceMatcher(None, norm(r["text"]), norm(o["text"])).ratio() >= 0.85:
            notes.append("和 %s(%s:%d)几乎一样" % (o["id"] or "无编号", o["file"], o["line"]))
    due = age is None or age >= limit
    rows.append({"id": r["id"] or "", "file": r["file"], "line": r["line"], "layer": r["layer"], "owner": r["owner"],
                 "text": r["text"], "last": ym or "", "months": age, "due": due, "notes": notes})

per_file = {}
for r in rules:
    per_file[r["file"]] = per_file.get(r["file"], 0) + 1
crowded = ["%s 有 %d 条规则,超过 20 条" % (f, n) for f, n in sorted(per_file.items()) if n > 20]

rows.sort(key=lambda x: (not x["due"], -(x["months"] if x["months"] is not None else 999), x["file"], x["line"]))
pname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(root))
kdir = os.path.join(crlib.home(), "rule-work", pname)
os.makedirs(kdir, exist_ok=True)
out = os.path.join(kdir, "review-checklist-%s.json" % now.strftime("%Y%m%d"))
crlib.save_json(out, {"months": limit, "rules": rows, "crowded": crowded})

LAYER = {"L1": "组织", "L2": "项目", "L3": "目录", "ignore": "抑制"}
due = [x for x in rows if x["due"]]
print("## 规则复核清单:%s" % pname)
print()
print("共 %d 条规则,其中 %d 条超过 %d 个月没复核(或没有日期),排在最前。" % (len(rows), len(due), limit))
for c in crowded:
    print("- " + c + ",考虑合并、挪到目录级,或删掉不重要的")
print()
print("| # | 规则 | 层级 | 位置 | 上次复核 | 规则内容 | 检查提示 |")
print("|---|---|---|---|---|---|---|")
for i, x in enumerate(rows, 1):
    last = "%s(%d 个月前)" % (x["last"], x["months"]) if x["months"] is not None else "—"
    print("| %d%s | %s | %s%s | `%s:%d` | %s | %s | %s |" % (i, " ⏰" if x["due"] else "", x["id"] or "—", LAYER.get(x["layer"], x["layer"]),
          "(%s)" % x["owner"] if x["owner"] else "", x["file"], x["line"], last, x["text"][:60], ";".join(x["notes"]) or "—"))
print()
print("负责人逐条看:还对的,用 propose-rules.sh 的 confirm 记下复核日期;要改的用 replace,不要的用 remove。检查提示只是线索,不替你下结论。")
print(out)
PY
