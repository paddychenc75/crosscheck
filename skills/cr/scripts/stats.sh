#!/bin/sh
# stats.sh — 从保存的审查记录里统计发现的去向:多少被解决、多少被否定、多少一直没处理。
#
# 用法: stats.sh [--all] [--days N] [--repo DIR]
#
#   默认只统计当前仓库对应的项目;--all 统计所有项目
#   --days N   只看最近 N 天的审查
#   --repo DIR 仓库所在的目录(默认:当前目录)
#
# 判定方式:
#   已解决    某次审查里还在、下一次增量审查里不在了(代码改了,或文件不再属于改动)
#   被否定    用 dismiss.sh 标记过
#   未处理    最近一次审查里仍然存在
#   不计      下一次是全量重审(--full),前后对不上,不统计去向
# 采纳率 = 已解决 /(已解决 + 被否定)。它是近似值:代码改了不等于是因为这条发现改的。
# 需要 python3。只读取记录,不修改任何东西。

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
      repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')
    else
      repo_id=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd)")
    fi
  else
    die "当前目录不是 git 仓库。加 --all 统计所有项目,或用 --repo 指定仓库"
  fi
fi

CR_STATS_HOME="${CROSSCHECK_HOME:-$HOME/.crosscheck}" CR_STATS_REPO="$repo_id" CR_STATS_DAYS="$days" python3 - <<'PY'
import datetime, json, os, re, sys

home = os.environ["CR_STATS_HOME"]; want = os.environ.get("CR_STATS_REPO", ""); days = os.environ.get("CR_STATS_DAYS", "")
reviews_root = os.path.join(home, "reviews")
cutoff = None
if days:
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=int(days))).strftime("%Y%m%d-%H%M%S")

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def load_meta(path):
    meta = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1); meta[k] = v
    except Exception:
        pass
    return meta

def key(f):
    return f.get("id") or "|".join(str(f.get(k, "")) for k in ("file", "category", "since", "summary"))

projects = []
if os.path.isdir(reviews_root):
    for name in sorted(os.listdir(reviews_root)):
        pdir = os.path.join(reviews_root, name)
        if not os.path.isdir(pdir):
            continue
        rid = ""
        try:
            rid = open(os.path.join(pdir, ".repo"), encoding="utf-8").readline().strip()
        except Exception:
            pass
        if want and rid != want:
            continue
        projects.append((name, pdir))

if not projects:
    print("还没有审查记录。" if not want else "这个项目还没有审查记录。")
    sys.exit(0)

n_reviews = n_full = n_incr = n_clean = 0
costs = {}          # depth -> {n, dur, tok_n, tok, tok_sub}
rows = {}          # (category, severity) -> counters
stale = []
def bump(f, what):
    r = rows.setdefault((f.get("category", "?"), f.get("severity", "?")), {"reported": 0, "resolved": 0, "dismissed": 0, "open": 0, "skipped": 0})
    r[what] += 1

for pname, pdir in projects:
    for target in sorted(os.listdir(pdir)):
        tdir = os.path.join(pdir, target)
        if not os.path.isdir(tdir):
            continue
        stamps = sorted(d for d in os.listdir(tdir) if re.fullmatch(r"\d{8}-\d{6}", d))
        if cutoff:
            stamps = [s for s in stamps if s >= cutoff]
        prev = None            # 上一次(覆盖所有维度的)审查里未解决的发现
        first_seen = {}
        idx = 0
        # 被否定的发现会从 findings.json 里移走,先收集起来,免得被当成"已解决"。
        dismissed_keys = set()
        for stamp in stamps:
            for f in load_json(os.path.join(tdir, stamp, "dismissed.json")):
                dismissed_keys.add(key(f))
        counted_dismissed = set()
        for stamp in stamps:
            rdir = os.path.join(tdir, stamp)
            meta = load_meta(os.path.join(rdir, "meta.txt"))
            findings = load_json(os.path.join(rdir, "findings.json"))
            dismissed = load_json(os.path.join(rdir, "dismissed.json"))
            n_reviews += 1
            c = costs.setdefault(meta.get("depth", "standard"), {"n": 0, "dur_n": 0, "dur": 0, "tok_n": 0, "tok": 0, "sub": 0})
            c["n"] += 1
            if meta.get("duration_s", "").isdigit():
                c["dur_n"] += 1; c["dur"] += int(meta["duration_s"])
            if meta.get("usage_total_tokens", "").isdigit():
                c["tok_n"] += 1; c["tok"] += int(meta["usage_total_tokens"])
                if meta.get("usage_subagents") == "included": c["sub"] += 1
            scope = meta.get("review_scope", "full")
            if scope == "incremental": n_incr += 1
            else: n_full += 1
            if not findings and not dismissed: n_clean += 1
            for f in dismissed:
                k = key(f)
                if k in counted_dismissed:
                    continue
                counted_dismissed.add(k)
                if k not in first_seen:
                    first_seen[k] = idx; bump(f, "reported")
                bump(f, "dismissed")
            if meta.get("only", "all") != "all":
                continue       # 只查部分维度的审查不参与前后对比
            cur = {key(f): f for f in findings}
            for k, f in cur.items():
                if k not in first_seen:
                    first_seen[k] = idx; bump(f, "reported")
            if prev is not None:
                for k, f in prev.items():
                    if k in cur or k in dismissed_keys:
                        continue
                    bump(f, "resolved" if scope == "incremental" else "skipped")
            prev = cur; idx += 1
        if prev:
            for k, f in prev.items():
                bump(f, "open")
                if idx - 1 - first_seen.get(k, idx - 1) >= 2:
                    stale.append((pname, target, f))

tot = {"reported": 0, "resolved": 0, "dismissed": 0, "open": 0, "skipped": 0}
for r in rows.values():
    for k in tot: tot[k] += r[k]
def rate(r):
    d = r["resolved"] + r["dismissed"]
    return "%d%%" % round(100.0 * r["resolved"] / d) if d else "—"

scope_txt = "全部项目" if not want else projects[0][0]
print("## crosscheck 审查统计:%s%s" % (scope_txt, "(最近 %s 天)" % days if days else ""))
print()
print("审查 %d 次(全量 %d,增量 %d),其中 %d 次没有任何发现。共报告 %d 条发现。" % (n_reviews, n_full, n_incr, n_clean, tot["reported"]))
print()
print("| 维度 | 严重程度 | 报告 | 已解决 | 被否定 | 未处理 | 采纳率 |")
print("|---|---|---|---|---|---|---|")
sev_order = {"critical": 0, "major": 1, "minor": 2}
sev_name = {"critical": "严重", "major": "重要", "minor": "轻微"}
for (cat, sev), r in sorted(rows.items(), key=lambda kv: (kv[0][0], sev_order.get(kv[0][1], 9))):
    print("| %s | %s | %d | %d | %d | %d | %s |" % (cat, sev_name.get(sev, sev), r["reported"], r["resolved"], r["dismissed"], r["open"], rate(r)))
print("| **合计** | | %d | %d | %d | %d | %s |" % (tot["reported"], tot["resolved"], tot["dismissed"], tot["open"], rate(tot)))
print()
print("采纳率 = 已解决 /(已解决 + 被否定)。已解决指下一次增量审查里这条发现不在了,是近似值。")
if tot["skipped"]:
    print("另有 %d 条因为紧接着做了全量重审,去向无法对应,没有计入。" % tot["skipped"])
def human(n):
    return "%.1fM" % (n / 1e6) if n >= 1e6 else ("%.0fK" % (n / 1e3) if n >= 1e3 else "%d" % n)
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
        note = "%d" % c["tok_n"] + ("(其中 %d 次含子代理)" % c["sub"] if c["tok_n"] else "")
        print("| %s | %d | %s | %s | %s |" % (depth, c["n"], dur, tok, note))
    print()
    print("token 用量来自宿主的会话记录,只在装了 hook 的宿主里才有,未必包含子代理的消耗,仅供参考。")
if stale:
    print()
    print("### 连续 3 次以上审查都没处理的发现(%d 条)" % len(stale))
    for pname, target, f in stale[:10]:
        print("- `%s:%s` — %s(%s,%s)" % (f.get("file", "?"), f.get("line", "?"), f.get("summary", ""), f.get("category", "?"), target))
    if len(stale) > 10:
        print("- ……还有 %d 条" % (len(stale) - 10))
    print()
    print("一直没人处理的发现,要么是不值得报,要么是被忽略了。前者可以否定掉并写进 `.cr/ignore.md` 或 `REVIEW.md`。")
PY
