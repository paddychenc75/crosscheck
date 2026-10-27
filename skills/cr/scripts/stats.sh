#!/bin/sh
# stats.sh — 从保存的审查记录里统计发现的效果和审查的成本。
#
# 用法: stats.sh [--all] [--days N] [--repo DIR]
#
#   默认只统计当前仓库对应的项目;--all 统计所有项目
#   --days N   只看最近 N 天的审查
#   --repo DIR 仓库所在的目录(默认:当前目录)
#
# 几个维度分开统计,互不折算:
#   判断     人对这条发现的结论(feedback.sh 记录):成立 / 不修 / 不成立 / 有争议 / 未表态
#   去向     代码层面发生了什么:仍在 / 代码已改 / 代码没变但不再报 / 文件已不在改动里
#   校准     不同置信度区间里,有人判断过的发现有多少是成立的
#   差一点   没通过核实的候选的分数分布,尤其是 70–79 分那批(rejected.json),用来判断 80 分门槛是否合适
# "代码已改"不等于问题解决了:改动可能与这条发现无关。"成立率"只按有人明确判断过的发现计算,样本不足 10 条时只给数量。
# 另外给出每种深度的平均用时和 token。需要 python3。只读取记录,不修改任何东西。

set -eu

die() { printf 'stats: %s\n' "$*" >&2; exit 1; }

all=no; days=""; repo_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --all) all=yes; shift ;;
    --days) [ $# -ge 2 ] || die "--days 需要一个值"; days=$2; shift 2 ;;
    --repo) [ $# -ge 2 ] || die "--repo 需要一个值"; repo_arg=$2; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done
case "$days" in ""|*[!0-9]*) [ -z "$days" ] || die "--days 必须是正整数" ;; esac
command -v python3 >/dev/null 2>&1 || die "需要 python3"

# 当前仓库的身份,算法与 collect-diff.sh 一致;去掉了账号和凭证,原始地址不会被输出。
repo_id=""
if [ "$all" = no ]; then
  if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    u=$(git remote get-url origin 2>/dev/null || true)
    if [ -n "$u" ]; then
      repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#^\([^/:]*\):[0-9][0-9]*/#\1/#' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')
    else
      repo_id=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd)")
    fi
  else
    die "当前目录不是 git 仓库。加 --all 统计所有项目,或用 --repo 指定仓库"
  fi
fi

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_STATS_HOME="${CROSSCHECK_HOME:-$HOME/.crosscheck}" CR_STATS_REPO="$repo_id" CR_STATS_DAYS="$days" python3 - <<'PY'
import collections, datetime, json, os, re, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

home = os.environ["CR_STATS_HOME"]; want = os.environ.get("CR_STATS_REPO", ""); days = os.environ.get("CR_STATS_DAYS", "")
root = os.path.join(home, "reviews")
cutoff = (datetime.datetime.now() - datetime.timedelta(days=int(days))).strftime("%Y%m%d-%H%M%S") if days else None
SEV = {"critical": "严重", "major": "重要", "minor": "轻微"}

def load(path, default):
    try:
        x = json.load(open(path, encoding="utf-8")); return x if isinstance(x, type(default)) else default
    except Exception:
        return default

def meta_of(path):
    m = {}
    try:
        for line in open(path, encoding="utf-8"):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1); m[k] = v
    except Exception:
        pass
    return m

def key(f):
    return f.get("id") or "|".join(str(f.get(k, "")) for k in ("file", "category", "since", "summary"))

def human(n):
    return "%.1fM" % (n / 1e6) if n >= 1e6 else ("%.0fK" % (n / 1e3) if n >= 1e3 else "%d" % n)

projects = []
if os.path.isdir(root):
    for name in sorted(os.listdir(root)):
        pdir = os.path.join(root, name)
        if not os.path.isdir(pdir): continue
        try: rid = open(os.path.join(pdir, ".repo"), encoding="utf-8").readline().strip()
        except Exception: rid = ""
        if want and rid != want: continue
        projects.append((name, pdir))
if not projects:
    print("这个项目还没有审查记录。" if want else "还没有审查记录。"); sys.exit(0)

findings = {}        # key -> finding (最新的版本)
seen_n = {}          # key -> 出现在多少次审查里
verdict = {}         # key -> 最后一次判断
fate = {}            # key -> 去向
open_now = set()
costs = {}
n_reviews = n_full = n_incr = n_clean = 0
rejected_all = []

for pname, pdir in projects:
    fb = list(crlib.feedback_entries(pdir).values())
    for e in fb:
        if cutoff and e.get("time", "").replace("-", "").replace(":", "").replace(" ", "-") < cutoff[:13]: continue
        verdict[e.get("key") or e.get("id")] = e.get("verdict")
    for target in sorted(os.listdir(pdir)):
        tdir = os.path.join(pdir, target)
        if not os.path.isdir(tdir): continue
        stamps = sorted(d for d in os.listdir(tdir) if re.fullmatch(r"\d{8}-\d{6}", d))
        if cutoff: stamps = [x for x in stamps if x >= cutoff]
        for stamp in stamps:
            rdir = os.path.join(tdir, stamp)
            m = meta_of(os.path.join(rdir, "meta.txt"))
            fs = load(os.path.join(rdir, "findings.json"), [])
            ds = load(os.path.join(rdir, "dismissed.json"), [])
            n_reviews += 1
            if m.get("review_scope") == "incremental": n_incr += 1
            else: n_full += 1
            if not fs and not ds: n_clean += 1
            c = costs.setdefault(m.get("depth", "standard"), {"n": 0, "dur_n": 0, "dur": 0, "tok_n": 0, "tok": 0, "sub": 0})
            c["n"] += 1
            if m.get("duration_s", "").isdigit(): c["dur_n"] += 1; c["dur"] += int(m["duration_s"])
            if m.get("usage_total_tokens", "").isdigit():
                c["tok_n"] += 1; c["tok"] += int(m["usage_total_tokens"])
                if m.get("usage_subagents") == "included": c["sub"] += 1
            for f in fs + ds:
                k = key(f); findings[k] = f; seen_n[k] = seen_n.get(k, 0) + 1
            for d in ds:                 # 旧版本只写了 dismissed.json,分不清不成立和不修
                verdict.setdefault(key(d), d.get("verdict", "invalid_legacy"))
            for t in load(os.path.join(rdir, "transitions.json"), []):
                fate[key(t)] = t.get("transition")
            for r in load(os.path.join(rdir, "rejected.json"), []):
                rejected_all.append(r)
        if stamps:
            try: latest = open(os.path.join(tdir, "latest"), encoding="utf-8").readline().strip()
            except Exception: latest = stamps[-1]
            for f in load(os.path.join(tdir, latest, "findings.json"), []):
                open_now.add(key(f))

V = [("valid", "成立"), ("wontfix", "成立但不修"), ("invalid", "不成立"), ("invalid_legacy", "被否定(旧记录)"), ("disputed", "有争议"), (None, "未表态")]
F = [("open", "仍在"), ("code_changed", "代码已改"), ("not_reported_code_unchanged", "代码没变但不再报"), ("out_of_scope", "文件已不在改动里"), ("unknown", "去向不明")]

def fate_of(k):
    if k in open_now: return "open"
    return fate.get(k, "unknown")

by_cat = collections.OrderedDict()
for k, f in sorted(findings.items(), key=lambda kv: (kv[1].get("category", ""), kv[1].get("severity", ""))):
    by_cat.setdefault(f.get("category", "?"), []).append(k)

def rate(keys):
    good = sum(1 for k in keys if verdict.get(k) in ("valid", "wontfix"))
    bad = sum(1 for k in keys if verdict.get(k) in ("invalid", "invalid_legacy"))
    n = good + bad
    if not n: return "—"
    return ("%d%%(n=%d)" % (round(100.0 * good / n), n)) if n >= 10 else "%d/%d(样本不足,不算比例)" % (good, n)

title = "全部项目" if not want else projects[0][0]
print("## crosscheck 审查统计:%s%s" % (title, "(最近 %s 天)" % days if days else ""))
print()
print("审查 %d 次(全量 %d,增量 %d),其中 %d 次没有任何发现。共 %d 条不同的发现。" % (n_reviews, n_full, n_incr, n_clean, len(findings)))
print()
print("### 判断:人对发现的结论")
print()
print("| 维度 | 发现 | " + " | ".join(n for _, n in V) + " | 成立率 |")
print("|---|---|" + "---|" * len(V) + "---|")
allk = list(findings)
for cat, keys in list(by_cat.items()) + [("**合计**", allk)]:
    cells = [str(sum(1 for k in keys if verdict.get(k) == v)) for v, _ in V]
    print("| %s | %d | %s | %s |" % (cat, len(keys), " | ".join(cells), rate(keys)))
print()
print("成立率 =(成立 + 成立但不修)/(成立 + 成立但不修 + 不成立),只算有人明确判断过的发现;未表态和有争议的不计入。")
print("注意选择偏差:人更倾向于标记错的,很少专门标记对的。用 --fix 修掉、或明确说\"这条对\"的发现会自动记为成立;未表态的比例越高,成立率越不可靠。")
print()
print("### 去向:代码层面发生了什么")
print()
print("| 维度 | " + " | ".join(n for _, n in F) + " |")
print("|---|" + "---|" * len(F))
for cat, keys in list(by_cat.items()) + [("**合计**", allk)]:
    print("| %s | %s |" % (cat, " | ".join(str(sum(1 for k in keys if fate_of(k) == v)) for v, _ in F)))
print()
print("\"代码已改\"只说明那一行后来变了,不等于是因为这条发现改的;\"代码没变但不再报\"多半说明这条发现不稳定。")
buckets = [("100", lambda c: c == 100), ("90-99", lambda c: 90 <= c < 100), ("80-89", lambda c: 80 <= c < 90), ("无记录", lambda c: c is None)]
judged = [k for k in allk if verdict.get(k) in ("valid", "wontfix", "invalid", "invalid_legacy")]
if judged:
    print()
    print("### 校准:置信度和实际成立的比例")
    print()
    print("| 置信度 | 有人判断过 | 成立 | 不成立 | 成立率 |")
    print("|---|---|---|---|---|")
    for name, test in buckets:
        def conf(k):
            c = findings[k].get("confidence")
            return int(c) if isinstance(c, (int, float)) or (isinstance(c, str) and c.isdigit()) else None
        ks = [k for k in judged if test(conf(k))]
        if not ks: continue
        good = sum(1 for k in ks if verdict.get(k) in ("valid", "wontfix")); bad = len(ks) - good
        pct = "%d%%" % round(100.0 * good / len(ks)) if len(ks) >= 10 else "样本不足"
        print("| %s | %d | %d | %d | %s |" % (name, len(ks), good, bad, pct))
    print()
    print("每个区间至少 10 条有人判断过的发现才给比例。高置信度区间的成立率如果并不比低区间高,说明打分没有区分度。")
if any(c["dur_n"] or c["tok_n"] for c in costs.values()):
    print()
    print("### 成本")
    print()
    print("| 深度 | 审查次数 | 平均用时 | 平均 token | 有 token 记录的次数 |")
    print("|---|---|---|---|---|")
    for depth in ("quick", "standard", "deep"):
        c = costs.get(depth)
        if not c: continue
        dur = "—"
        if c["dur_n"]:
            a = c["dur"] // c["dur_n"]; dur = "%d 分 %d 秒" % (a // 60, a % 60) if a >= 60 else "%d 秒" % a
        tok = human(c["tok"] / c["tok_n"]) if c["tok_n"] else "—"
        print("| %s | %d | %s | %s | %d%s |" % (depth, c["n"], dur, tok, c["tok_n"], "(其中 %d 次含子代理)" % c["sub"] if c["tok_n"] else ""))
    print()
    print("token 用量由 hook 从宿主的会话记录里读取,只算审查开始到保存之间;没有 hook(拷贝安装、Codex 未信任)或 Cursor 时不统计。")
if rejected_all:
    def conf_of(r):
        try: return int(float(r.get("confidence", 0)))
        except (TypeError, ValueError): return 0
    b = collections.Counter()
    for r in rejected_all:
        c = conf_of(r)
        b["70-79" if 70 <= c < 80 else ("50-69" if 50 <= c < 70 else "0-49")] += 1
    print()
    print("### 差一点通过:没通过核实的候选(%d 条)" % len(rejected_all))
    print()
    print("| 分数 | 条数 |")
    print("|---|---|")
    for k in ("70-79", "50-69", "0-49"):
        print("| %s | %d |" % (k, b[k]))
    near = sorted((r for r in rejected_all if 70 <= conf_of(r) < 80), key=conf_of, reverse=True)
    if near:
        print()
        print("70–79 分的候选(最多列 8 条,值得抽几条人工看看,判断 80 分门槛是不是太严):")
        for r in near[:8]:
            print("- %d 分 `%s:%s` %s —— %s" % (conf_of(r), r.get("file", "?"), r.get("line", "?"), r.get("summary", ""), r.get("reject_reason") or r.get("verdict", "")))
stale = [k for k in open_now if seen_n.get(k, 0) >= 3 and verdict.get(k) not in ("invalid", "wontfix")]
if stale:
    print()
    print("### 连续 3 次以上审查都还在的发现(%d 条)" % len(stale))
    for k in stale[:10]:
        f = findings[k]
        print("- `%s:%s` — %s(%s,%s)" % (f.get("file", "?"), f.get("line", "?"), f.get("summary", ""), f.get("category", "?"), verdict.get(k) and dict(V).get(verdict.get(k)) or "未表态"))
    if len(stale) > 10: print("- ……还有 %d 条" % (len(stale) - 10))
    print()
    print("一直没人处理也没人表态的发现,值得问一下:是不成立、不打算修,还是被忽略了。")
PY
