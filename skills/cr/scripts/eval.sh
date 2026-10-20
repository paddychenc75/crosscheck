#!/bin/sh
# eval.sh — 评测:用一批答案已知的用例给 crosscheck 打分,和基线比较。由插件维护者在改插件前后运行。
#
# 用法:
#   eval.sh prepare [--cases DIR] [--out RUN]
#       读取评测用例(默认组织规则仓库的 evals/cases/*.json),加上规则包里每条规则的正反例,
#       生成这次评测的运行目录 RUN 和 plan.json:每个用例要审查的仓库、提交范围、期望。
#       用例里的仓库名在配置里映射到本机路径:repo.<名字>=<路径>。正反例会在 RUN 里建一个临时仓库。
#   eval.sh done RUN CASE
#       一个用例审查完(跑到 aggregate.sh 为止)后调用:记下用时,复制 token 用量(有的话)。
#   eval.sh match RUN
#       对答案的第一步(确定性的):文件相同、行号范围重叠(前后放宽 3 行)的配成对;规则正反例按编号直接判定。
#       写出 judge.json:需要判断「是不是同一个问题」的配对,以及答案之外、需要判断「站不站得住」的发现。
#   eval.sh score RUN [--baseline FILE]
#       读取 judgments.json(由 judge 角色填写,人工抽查),计算召回、每个干净用例的误报、精确率、
#       平均用时和 token,逐个用例和基线(默认组织规则仓库的 evals/baseline.json)比较,给出能否合入的结论。
#       写出 score.json 和 report.md。
#   eval.sh baseline RUN [--yes]
#       把这次的成绩作为新的基线:通过 MR 更新组织规则仓库的 evals/baseline.json(不带 --yes 只预览)。
#   eval.sh find-cases [--repo DIR] [--since 365d] [--limit 20] [--clean]
#       找候选用例:提交说明含 fix / 修复 的小提交,用 git blame 找到当初引入那几行的提交;
#       --clean 找合并后 30 天内没有相关修复的提交。结果给人确认,不会自动加入评测集。
#   eval.sh add-case --repo-name NAME --base SHA --head SHA --source fix-commit|clean|missed[:说明] [--expect file:行-行:类别:严重程度:描述]...
#                    [--intent-file FILE]   来自 MR 的用例带上当时的需求材料(如审查记录里的 intent.md),评测时作为 --plan 传入
#       写一个用例到 ~/.crosscheck/eval/cases-pending/,确认人写在 --confirmed-by(会存哈希)。
#   eval.sh submit-cases [--yes]
#       把 cases-pending 里的用例通过 MR 提交到组织规则仓库的 evals/cases/。
# 运行时用隔离的记录目录(RUN/home),不读历史判断、不做增量。需要 python3。

set -eu

die() { printf 'eval: %s\n' "$*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "需要 python3"
here=$(cd "$(dirname "$0")" && pwd)
cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  prepare|done|match|score|baseline|find-cases|add-case|submit-cases) ;;
  *) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

if [ "$cmd" = baseline ] || [ "$cmd" = submit-cases ]; then
  run=""; yes=""
  while [ $# -gt 0 ]; do case "$1" in --yes) yes=--yes; shift ;; *) run=$1; shift ;; esac; done
  rd=${CROSSCHECK_RULES_DIR:-$(sh "$here/config.sh" get rules_dir)}; rd=${rd:-${CROSSCHECK_HOME:-$HOME/.crosscheck}/org-rules}
  [ -d "$rd/.git" ] || die "本地没有组织规则仓库(先配置 rules_repo 并运行 sync-rules.sh)"
  if [ "$cmd" = baseline ]; then
    [ -f "$run/score.json" ] || die "$run 里没有 score.json,先运行 eval.sh score"
    exec sh "$here/open-mr.sh" --repo "$rd" --branch "crosscheck/eval-baseline-$(date +%Y%m%d-%H%M%S)" \
      --title "crosscheck: 更新评测基线" --put "evals/baseline.json=$run/score.json" \
      --check "sh '$here/rules-lint.sh' --rules-repo \"\$1\"" $yes
  fi
  pend="${CROSSCHECK_HOME:-$HOME/.crosscheck}/eval/cases-pending"
  set --
  for f in "$pend"/*.json; do [ -f "$f" ] && set -- "$@" --put "evals/cases/$(basename "$f")=$f"; done
  [ $# -gt 0 ] || die "cases-pending 里没有用例"
  sh "$here/open-mr.sh" --repo "$rd" --branch "crosscheck/eval-cases-$(date +%Y%m%d-%H%M%S)" --title "crosscheck: 新增评测用例" \
    --check "sh '$here/rules-lint.sh' --rules-repo \"\$1\"" "$@" $yes
  [ -z "$yes" ] || rm -f "$pend"/*.json
  exit 0
fi

CR_SCRIPTS="$here" CR_CMD=$cmd CR_ARGS=$(printf '%s\n' "$@") CR_CWD=$(pwd) python3 - <<'PY'
import collections, datetime, glob, json, os, re, shutil, subprocess, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

cmd = os.environ["CR_CMD"]
argv = [a for a in os.environ["CR_ARGS"].split("\n") if a != ""]
def opt(name, default=None, flag=False):
    if name in argv:
        i = argv.index(name)
        if flag:
            argv.pop(i); return True
        v = argv[i + 1] if i + 1 < len(argv) else default
        del argv[i:i + 2]
        return v
    return False if flag else default
def multi(name):
    out = []
    while name in argv:
        i = argv.index(name); out.append(argv[i + 1]); del argv[i:i + 2]
    return out
def git(root, *a):
    return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True)
cfg = crlib.config()
rd = crlib.rules_dir()

if cmd == "prepare":
    cases_dir = opt("--cases") or (os.path.join(rd, "evals", "cases") if rd else "")
    run = opt("--out") or os.path.join(crlib.home(), "eval", "runs", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    run = os.path.abspath(run)
    os.makedirs(os.path.join(run, "home"), exist_ok=True)
    plan, skipped = [], []
    for p in sorted(glob.glob(os.path.join(cases_dir, "*.json"))) if cases_dir else []:
        c = crlib.load_json(p, {})
        path = cfg.get("repo." + str(c.get("repo", "")), "")
        if not path or not os.path.isdir(path):
            skipped.append("%s(仓库 %s 在本机的位置没有配置:repo.%s=<路径>)" % (c.get("id"), c.get("repo"), c.get("repo"))); continue
        if git(path, "cat-file", "-e", c.get("head", "") + "^{commit}").returncode != 0:
            git(path, "fetch", "-q", "origin")
        item = {"case": c["id"], "kind": c.get("source", "fix-commit").split(":")[0], "repo_path": path,
                "target": "%s..%s" % (c["base"], c["head"]), "expect": c.get("expect", [])}
        if str(c.get("intent") or "").strip():
            # 当时的需求材料(MR 描述、验收标准):只有提交范围时意图会丢,需求核对类的期望就测不出来
            cdir = os.path.join(run, "cases", c["id"])
            os.makedirs(cdir, exist_ok=True)
            with open(os.path.join(cdir, "intent.md"), "w", encoding="utf-8") as f:
                f.write(c["intent"])
            item["plan_file"] = os.path.join(cdir, "intent.md")
        plan.append(item)
    # 规则包的正反例:每条建一个只有两个提交的临时仓库(base 里引用这个包,head 加入例子文件)
    for ex in sorted(glob.glob(os.path.join(rd, "packs", "**", "examples", "*.*"), recursive=True)) if rd else []:
        name = os.path.basename(ex)
        m = re.match(r"^(.+?)\.(bad|good)\.(.+)$", name)
        if not m:
            continue
        rid, kind, ext = m.groups()
        pack = os.path.relpath(os.path.dirname(os.path.dirname(ex)), os.path.join(rd, "packs"))
        repo = os.path.join(run, "examples", "%s-%s" % (rid, kind))
        if not os.path.isdir(repo):
            os.makedirs(repo)
            for a in (["init", "-q", "-b", "main"], ["config", "user.email", "eval@crosscheck"], ["config", "user.name", "eval"]):
                git(repo, *a)
            with open(os.path.join(repo, "REVIEW.md"), "w", encoding="utf-8") as f:
                f.write("---\nextends: [%s]\n---\n" % pack)
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "base")
            os.makedirs(os.path.join(repo, "src"), exist_ok=True)
            shutil.copy(ex, os.path.join(repo, "src", "example.%s" % ext))
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "example")
        base = git(repo, "rev-parse", "HEAD~1").stdout.strip(); head = git(repo, "rev-parse", "HEAD").stdout.strip()
        plan.append({"case": "rule-%s-%s" % (rid, kind), "kind": "rule-example", "rule_id": rid, "example": kind,
                     "repo_path": repo, "target": "%s..%s" % (base, head), "expect": []})
    crlib.save_json(os.path.join(run, "plan.json"), plan)
    with open(os.path.join(run, "env.sh"), "w") as f:
        f.write("export CROSSCHECK_HOME='%s'\nexport CROSSCHECK_CONFIG='%s'\n" % (os.path.join(run, "home"), crlib.config_path()))
        if rd:
            f.write("export CROSSCHECK_RULES_DIR='%s'\n" % rd)
        f.write("export CROSSCHECK_OPEN=0\n")
    print("评测运行目录:%s" % run)
    print("用例 %d 个(其中规则正反例 %d 个)" % (len(plan), sum(1 for x in plan if x["kind"] == "rule-example")))
    for s in skipped:
        print("跳过:" + s)
    print()
    print("对 plan.json 里的每个用例(先 . %s/env.sh):" % run)
    print("  1. sh collect-diff.sh <target> --repo <repo_path> --full --out %s/cases/<case>/review [--depth 和基线一致]" % run)
    print("     用例有 plan_file 时加 --plan <plan_file>(当时的需求材料);规则正反例加 --only rules")
    print("  2. 照常查找、核实,跑到 aggregate.sh 为止(不写报告、不保存记录)")
    print("  3. sh eval.sh done %s <case>" % run)
    print("全部完成后:sh eval.sh match %s" % run)
    sys.exit(0)

run = os.path.abspath(argv[0]) if argv else ""
if cmd in ("done", "match", "score") and not os.path.isfile(os.path.join(run, "plan.json")):
    print("eval: %s 不是 eval.sh prepare 生成的运行目录" % run, file=sys.stderr); sys.exit(2)

if cmd == "done":
    case = argv[1]
    rv = os.path.join(run, "cases", case, "review")
    meta = crlib.read_kv(os.path.join(rv, "meta.txt"))
    started = int(meta.get("started_epoch", "0") or 0)
    info = {"duration_s": int(time.time()) - started if started else None, "depth": meta.get("depth"), "verifier_model": meta.get("verifier_model")}
    usage = crlib.read_kv(os.path.join(rv, "usage.txt"))
    if usage.get("usage_total_tokens"):
        info["tokens"] = int(usage["usage_total_tokens"])
    crlib.save_json(os.path.join(run, "cases", case, "done.json"), info)
    print("ok: %s 完成%s" % (case, ",用时 %d 秒" % info["duration_s"] if info["duration_s"] else ""))
    sys.exit(0)

plan = crlib.load_json(os.path.join(run, "plan.json"), [])

def findings_of(case):
    rv = os.path.join(run, "cases", case, "review")
    return crlib.load_json(os.path.join(rv, "findings.json"), []), os.path.isfile(os.path.join(rv, "final.json"))

if cmd == "match":
    pairs, extras, auto, missing = [], [], [], []
    for c in plan:
        fs, ran = findings_of(c["case"])
        if not ran:
            missing.append(c["case"]); continue
        if c["kind"] == "rule-example":
            hit = [f for f in fs if f.get("category") == "rules" and f.get("rule_id") == c["rule_id"]]
            auto.append({"case": c["case"], "kind": c["example"], "rule_id": c["rule_id"], "reported": bool(hit)})
            continue
        used = set()
        for i, e in enumerate(c["expect"]):
            rg = crlib.parse_lines(e.get("lines")) or (0, 0)
            for f in fs:
                try:
                    ln = int(f.get("line", 0))
                except (TypeError, ValueError):
                    continue
                if f.get("file") == e.get("file") and rg[0] - 3 <= ln <= rg[1] + 3:
                    pairs.append({"case": c["case"], "expect_index": i, "expect": e,
                                  "finding": {k: f.get(k) for k in ("id", "file", "line", "category", "severity", "summary", "failure_scenario")}})
                    used.add(f.get("id"))
        # 配对过的发现也列进来(paired):如果它的配对都判为不是同一个问题,它就是答案之外的发现,同样要判断站不站得住
        for f in fs:
            x = {"case": c["case"], "case_kind": c["kind"],
                 "finding": {k: f.get(k) for k in ("id", "file", "line", "category", "severity", "summary", "failure_scenario", "rule_id")}}
            if f.get("id") in used:
                x["paired"] = True
            extras.append(x)
    crlib.save_json(os.path.join(run, "judge.json"), {"pairs": pairs, "extras": extras})
    crlib.save_json(os.path.join(run, "auto.json"), auto)
    print("配对 %d 组需要判断「是不是同一个问题」,答案之外的发现 %d 条需要判断「站不站得住」(另有 %d 条配对过的,配对都不成立时也要判断);规则正反例 %d 个已自动判定。" % (
        len(pairs), sum(1 for x in extras if not x.get("paired")), sum(1 for x in extras if x.get("paired")), len(auto)))
    if missing:
        print("还没跑完的用例:" + "、".join(missing))
    print("由 judge 角色(mode=eval)读 %s/judge.json,把结论写进 %s/judgments.json,人工抽查几条后运行 eval.sh score。" % (run, run))
    sys.exit(0)

if cmd == "score":
    base_path = opt("--baseline") or (os.path.join(rd, "evals", "baseline.json") if rd else "")
    j = crlib.load_json(os.path.join(run, "judgments.json"), {})
    jp = {(x.get("case"), x.get("expect_index"), x.get("finding_id")): bool(x.get("same")) for x in j.get("pairs", [])}
    je = {(x.get("case"), x.get("finding_id")): bool(x.get("valid")) for x in j.get("extras", [])}
    judge = crlib.load_json(os.path.join(run, "judge.json"), {})
    auto = crlib.load_json(os.path.join(run, "auto.json"), [])
    per_case = {}
    tot = collections.Counter(); by_cat = collections.defaultdict(collections.Counter)
    for c in plan:
        r = {"kind": c["kind"], "expected": len(c["expect"]), "hit": 0, "valid": 0, "invalid": 0, "unjudged": 0}
        if c["kind"] == "rule-example":
            a = next((x for x in auto if x["case"] == c["case"]), None)
            if a and a["kind"] == "bad":
                r["expected"] = 1; r["hit"] = int(a["reported"]); by_cat["rules"]["expected"] += 1; by_cat["rules"]["hit"] += r["hit"]
            elif a:
                r["invalid"] = int(a["reported"])
            per_case[c["case"]] = r; continue
        hit_idx, valid_ids = set(), set()
        for p in judge.get("pairs", []):
            if p["case"] != c["case"]:
                continue
            same = jp.get((p["case"], p["expect_index"], p["finding"]["id"]))
            if same:
                hit_idx.add(p["expect_index"]); valid_ids.add(p["finding"]["id"])
        for i, e in enumerate(c["expect"]):
            cat = e.get("category", "bug")
            by_cat[cat]["expected"] += 1
            if i in hit_idx:
                by_cat[cat]["hit"] += 1
        r["hit"] = len(hit_idx); r["valid"] = len(valid_ids)
        for x in judge.get("extras", []):
            if x["case"] != c["case"] or x["finding"]["id"] in valid_ids:
                continue
            v = je.get((x["case"], x["finding"]["id"]))
            if v is None: r["unjudged"] += 1
            elif v: r["valid"] += 1
            else: r["invalid"] += 1
        d = crlib.load_json(os.path.join(run, "cases", c["case"], "done.json"), {})
        r["duration_s"], r["tokens"] = d.get("duration_s"), d.get("tokens")
        per_case[c["case"]] = r
    exp = sum(r["expected"] for r in per_case.values()); hit = sum(r["hit"] for r in per_case.values())
    clean = [r for r in per_case.values() if r["kind"] == "clean"]
    valid = sum(r["valid"] for r in per_case.values()); invalid = sum(r["invalid"] for r in per_case.values())
    durs = [r["duration_s"] for r in per_case.values() if r.get("duration_s")]
    toks = [r["tokens"] for r in per_case.values() if r.get("tokens")]
    score = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "cases": len(per_case),
        "recall": {"hit": hit, "expected": exp}, "by_category": {k: dict(v) for k, v in by_cat.items()},
        "fp_per_clean": round(sum(r["invalid"] for r in clean) / len(clean), 2) if clean else None,
        "precision": {"valid": valid, "invalid": invalid}, "unjudged": sum(r["unjudged"] for r in per_case.values()),
        "avg_duration_s": int(sum(durs) / len(durs)) if durs else None, "avg_tokens": int(sum(toks) / len(toks)) if toks else None,
        "per_case": per_case,
    }
    crlib.save_json(os.path.join(run, "score.json"), score)
    base = crlib.load_json(base_path, {}) if base_path else {}
    L = []
    pct = lambda a, b: "%d/%d" % (a, b) if b else "—"
    L.append("## 评测:%d 个用例%s" % (score["cases"], " · 对比基线 %s" % base.get("time", "") if base else "(没有基线,这次结果可以作为第一份基线)"))
    L.append("")
    L.append("| 指标 | 基线 | 本次 |")
    L.append("|---|---|---|")
    br = base.get("recall", {})
    L.append("| 召回 | %s | %s |" % (pct(br.get("hit", 0), br.get("expected", 0)) if base else "—", pct(hit, exp)))
    L.append("| 误报 / 干净用例 | %s | %s |" % (base.get("fp_per_clean", "—") if base else "—", score["fp_per_clean"] if score["fp_per_clean"] is not None else "—"))
    bp = base.get("precision", {})
    pr = lambda p: "%d%%" % round(100.0 * p["valid"] / (p["valid"] + p["invalid"])) if p and (p.get("valid", 0) + p.get("invalid", 0)) else "—"
    L.append("| 精确率 | %s | %s |" % (pr(bp) if base else "—", pr(score["precision"])))
    L.append("| 平均用时 | %s | %s |" % ("%s 秒" % base.get("avg_duration_s") if base.get("avg_duration_s") else "—", "%s 秒" % score["avg_duration_s"] if score["avg_duration_s"] else "—"))
    L.append("| 平均 token | %s | %s |" % (base.get("avg_tokens") or "—", score["avg_tokens"] or "—"))
    newly_hit, newly_miss, new_fp = [], [], []
    for cid, r in per_case.items():
        b = (base.get("per_case") or {}).get(cid)
        if not b:
            continue
        if r["hit"] > b.get("hit", 0): newly_hit.append(cid)
        if r["hit"] < b.get("hit", 0): newly_miss.append(cid)
        if r["invalid"] > b.get("invalid", 0): new_fp.append(cid)
    L.append("")
    if base:
        L.append("新抓到:%s · 新漏掉:%s · 新增误报:%s" % ("、".join(newly_hit) or "无", "、".join(newly_miss) or "无", "、".join(new_fp) or "无"))
    if score["unjudged"]:
        L.append("还有 %d 条答案之外的发现没有判断,先补上判断再下结论。" % score["unjudged"])
    verdict = ""
    if base:
        recall_down = hit * max(br.get("expected", 1), 1) < br.get("hit", 0) * max(exp, 1)
        fp_up = (score["fp_per_clean"] or 0) > (base.get("fp_per_clean") or 0)
        cost_up = (score["avg_tokens"] and base.get("avg_tokens") and score["avg_tokens"] > base["avg_tokens"] * 1.3) or \
                  (score["avg_duration_s"] and base.get("avg_duration_s") and score["avg_duration_s"] > base["avg_duration_s"] * 1.3)
        if recall_down or fp_up or newly_miss:
            small = len(newly_miss) + len(new_fp) <= 1
            verdict = "不建议合入:%s%s" % ("、".join(x for x, y in (("召回下降", recall_down), ("误报上升", fp_up), ("有用例新漏掉", bool(newly_miss))) if y),
                                         "。差距只有一个用例,可能是模型输出的波动,建议重跑一次再判断" if small else "")
        elif cost_up:
            verdict = "效果没有变差,但成本增加超过 30%,合入前写明理由"
        else:
            verdict = "可以合入:召回不降、误报不升,成本增加在预算内"
        L.append("结论:" + verdict)
    if exp < 50:
        L.append("")
        L.append("说明:期望问题只有 %d 个,误差很大,只能发现明显的倒退;比较门槛、模型这类决定至少需要 50 个期望问题。" % exp)
    score["verdict"] = verdict
    crlib.save_json(os.path.join(run, "score.json"), score)
    with open(os.path.join(run, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    sys.exit(0)

if cmd == "find-cases":
    repo = os.path.abspath(opt("--repo") or os.environ["CR_CWD"])
    root = git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo
    since = re.sub(r"\D", "", opt("--since", "365d") or "365") or "365"
    limit = int(opt("--limit", "20") or 20)
    clean = opt("--clean", flag=True)
    def stat(sha):
        out = git(root, "show", "--numstat", "--format=", sha).stdout
        files, n = [], 0
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) == 3 and p[0].isdigit():
                files.append(p[2]); n += int(p[0]) + int(p[1])
        return files, n
    test_re = re.compile(r"(^|/)(tests?|__tests__|spec)/|_test\.|\.test\.|\.spec\.|Test\.")
    cands = []
    if not clean:
        log = git(root, "log", "--since=%s days ago" % since, "--no-merges", "-i", "-E", "--grep=fix|修复|bug|hotfix", "--format=%H\t%s").stdout
        for line in log.splitlines():
            sha, subj = line.split("\t", 1)
            files, n = stat(sha)
            prod = [f for f in files if not test_re.search(f)]
            if not prod or len(files) > 5 or n > 200:
                continue
            origins = collections.Counter(); where = {}
            for f in prod:
                diff = git(root, "show", "-U0", "--format=", sha, "--", f).stdout
                for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M):
                    a, cnt = int(m.group(1)), int(m.group(2) or 1)
                    if cnt == 0:
                        continue
                    bl = git(root, "blame", "--porcelain", "-L", "%d,%d" % (a, a + cnt - 1), sha + "^", "--", f).stdout
                    for b in re.finditer(r"^([0-9a-f]{40}) (\d+) (\d+)", bl, re.M):
                        origins[b.group(1)] += 1
                        where.setdefault(b.group(1), {}).setdefault(f, []).append(int(b.group(2)))
            if not origins:
                continue
            origin = origins.most_common(1)[0][0]
            parent = git(root, "rev-parse", origin + "^").stdout.strip()
            if not parent:
                continue
            exp = [{"file": f, "lines": "%d-%d" % (min(ls), max(ls))} for f, ls in where[origin].items()]
            cands.append({"fix": sha[:12], "fix_subject": subj, "base": parent[:12], "head": origin[:12],
                          "origin_subject": git(root, "log", "-1", "--format=%s", origin).stdout.strip(), "expect_where": exp})
            if len(cands) >= limit:
                break
    else:
        log = git(root, "log", "--since=%s days ago" % since, "--no-merges", "--format=%H\t%ct\t%s").stdout
        for line in log.splitlines():
            sha, ts, subj = line.split("\t", 2)
            if re.search(r"fix|修复|bug|revert", subj, re.I):
                continue
            files, n = stat(sha)
            if not 20 <= n <= 400 or not files:
                continue
            later = git(root, "log", "--since=@%s" % ts, "--until=@%d" % (int(ts) + 30 * 86400), "-i", "-E", "--grep=fix|修复|bug|revert", "--format=%H", "--", *files).stdout.split()
            if [x for x in later if x != sha]:
                continue
            parent = git(root, "rev-parse", sha + "^").stdout.strip()
            if parent:
                cands.append({"base": parent[:12], "head": sha[:12], "subject": subj, "files": files, "lines": n})
            if len(cands) >= limit:
                break
    out = os.path.join(crlib.home(), "eval", "candidates-%s%s.json" % (datetime.date.today().strftime("%Y%m%d"), "-clean" if clean else ""))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    crlib.save_json(out, cands)
    print("找到 %d 个候选,写在 %s。逐个给人确认后用 eval.sh add-case 加入。" % (len(cands), out))
    for i, c in enumerate(cands, 1):
        if clean:
            print("%d. %s..%s %s(%d 行)" % (i, c["base"], c["head"], c["subject"], c["lines"]))
        else:
            print("%d. %s..%s 引入,%s 修复(%s)期望位置 %s" % (i, c["base"], c["head"], c["fix"], c["fix_subject"],
                  "、".join("%s:%s" % (e["file"], e["lines"]) for e in c["expect_where"])))
    sys.exit(0)

if cmd == "add-case":
    name, base, head, source = opt("--repo-name"), opt("--base"), opt("--head"), opt("--source", "fix-commit")
    who = multi("--confirmed-by")
    if source.split(":")[0] not in ("fix-commit", "clean", "missed"):
        print("eval: --source 应为 fix-commit、clean 或 missed(可以加说明,如 missed:MR !128 人工指出)", file=sys.stderr); sys.exit(2)
    exps = []
    for e in multi("--expect"):
        p = e.split(":", 4)
        if len(p) < 5:
            print("eval: --expect 的格式是 文件:行-行:类别:严重程度:描述", file=sys.stderr); sys.exit(2)
        exps.append({"file": p[0], "lines": p[1], "category": p[2], "severity": p[3], "desc": p[4]})
    if not (name and base and head):
        print("eval: 需要 --repo-name、--base、--head", file=sys.stderr); sys.exit(2)
    pend = os.path.join(crlib.home(), "eval", "cases-pending")
    os.makedirs(pend, exist_ok=True)
    cid = "%s-%s" % (re.sub(r"[^A-Za-z0-9._-]", "_", name), head[:8])
    case = {"id": cid, "repo": name, "base": base, "head": head, "source": source, "expect": exps,
            "confirmed_by": [crlib.short_hash(w) for w in who]}
    if opt("--intent-file"):
        # 来自 MR 的用例带上当时的需求材料,例如那次审查记录里的 intent.md
        with open(opt("--intent-file"), encoding="utf-8") as f:
            case["intent"] = f.read()
    crlib.save_json(os.path.join(pend, cid + ".json"), case)
    print("ok: 已写入 %s(%s,期望 %d 个问题)。攒够一批后用 eval.sh submit-cases 提交到规则仓库。" % (os.path.join(pend, cid + ".json"), "干净用例" if not exps else source, len(exps)))
PY
