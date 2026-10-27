"""aggregate.sh 的实现,用法和说明见 aggregate.sh。"""
import glob, json, os, re, sys
sys.dont_write_bytecode = True
import crlib

d = os.path.abspath(sys.argv[1])
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
settings = crlib.load_json(os.path.join(d, "settings.json"), {})
rule_blocks = crlib.load_json(os.path.join(d, "rules-index.json"), [])
ranges = crlib.ranges_of(d)
src = crlib.code_source(meta)
if src is None:
    # 读不到被审查的代码时,所有依据都会被判为不存在,报告会变成"没有确认发现"。宁可停下来。
    print("aggregate: 读不到被审查的代码(code_ref=%s),无法校验依据;不生成结论。请先让 head 可读(拉取 PR/MR 的代码)再重新审查"
          % meta.get("code_ref", ""), file=sys.stderr)
    sys.exit(2)
skip = settings.get("skip") or []
THRESHOLD = 80

verification_errors = []
try:
    with open(os.path.join(d, "candidates.json"), encoding="utf-8") as f:
        cands = json.load(f)
    if not isinstance(cands, list) or any(not isinstance(c, dict) for c in cands):
        raise ValueError("候选必须是对象数组")
except (OSError, ValueError):
    cands = []
    verification_errors.append("候选文件缺失或格式错误")
# 增量:上次的发现
status = {}
try:
    for line in open(os.path.join(d, "incremental.txt"), encoding="utf-8"):
        s, p = line.rstrip("\n").split("\t", 1); status[p] = s
except (OSError, ValueError):
    pass
prev = crlib.load_json(os.path.join(d, "prev-findings.json"), []) if meta.get("review_scope") == "incremental" else []
results = []
for p in sorted(glob.glob(os.path.join(d, "verify", "result-*.json"))):
    try:
        x = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        x = None
    if isinstance(x, dict):
        x = x.get("candidates") or x.get("results") or []
    if not isinstance(x, list) or any(not isinstance(r, dict) for r in x):
        verification_errors.append("核实文件格式错误: " + os.path.basename(p))
    else:
        results.extend(x)
by_cid = {}
for r in results:
    if r.get("cid") and not isinstance(r["cid"], str):
        verification_errors.append("cid 必须是字符串")
        continue
    if r.get("cid"):
        if r["cid"] in by_cid:
            verification_errors.append("重复核实 cid: " + str(r["cid"]))
        by_cid[r["cid"]] = r
def loose_key(x):
    return (x.get("file"), str(x.get("line")), crlib.norm(x.get("summary", ""))[:60])
by_loose = {}
for r in results:
    key = loose_key(r)
    by_loose.setdefault(key, []).append(r)
def result_for(c):
    if c.get("cid"):
        return by_cid.get(c["cid"])
    matches = by_loose.get(loose_key(c), [])
    return matches[0] if len(matches) == 1 else None

# 不完整的回复不是反证。先验证整轮，再允许生成报告或“已解决”。
expected = []
prepared = []
batch_paths = sorted(glob.glob(os.path.join(d, "verify", "batch-*.json")))
for p in batch_paths:
    try:
        with open(p, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = None
    if not isinstance(rows, list) or any(not isinstance(x, dict) or not isinstance(x.get("cid"), str) or not x["cid"] for x in rows):
        verification_errors.append("核实批次格式错误: " + os.path.basename(p))
    else:
        expected.extend(x["cid"] for x in rows)
        prepared.extend(rows)
if len(expected) != len(set(expected)):
    verification_errors.append("核实批次 cid 重复")
if batch_paths:
    normal_ids = [x["cid"] for x in prepared]
    candidate_ids = [c.get("cid") for c in cands]
    if any(not isinstance(x, str) for x in candidate_ids) or sorted(candidate_ids) != sorted(normal_ids):
        verification_errors.append("候选与核实批次不一致")
if expected:
    missing, extra = set(expected) - set(by_cid), set(by_cid) - set(expected)
    if missing: verification_errors.append("缺少核实结果: " + ",".join(sorted(missing)))
    if extra: verification_errors.append("出现不属于本轮批次的 cid: " + ",".join(sorted(extra)))
for c in cands:
    r = result_for(c)
    if r is None:
        verification_errors.append("缺少唯一核实结果: " + str(c.get("cid") or loose_key(c)))
    elif c.get("from_prev") and r.get("id") not in (None, c.get("id")):
        verification_errors.append("核实结果改变历史发现身份: " + str(c.get("id")))
for r in results:
    try:
        n = float(r["confidence"])
        valid = not isinstance(r["confidence"], bool) and 0 <= n <= 100 and n.is_integer()
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        verification_errors.append("核实分数缺失或无效: " + str(r.get("cid")))
previous_candidates = {c.get("id") for c in cands if c.get("from_prev")}
for old in prev:
    if status.get(old.get("file")) in ("changed", "dependent") and old.get("id") not in previous_candidates:
        verification_errors.append("遗漏待重新核实的历史发现: " + str(old.get("id")))
error_file = os.path.join(d, "verification-errors.json")
if verification_errors:
    crlib.save_json(error_file, verification_errors)
    print("aggregate: 核实未完成；不生成已解决结论。" + "；".join(verification_errors), file=sys.stderr)
    sys.exit(2)
if os.path.exists(error_file):
    os.remove(error_file)

_lines_cache = {}
def file_lines(path):
    if path not in _lines_cache:
        t = src.read(path) if src else None
        _lines_cache[path] = t.split("\n") if t is not None else None
    return _lines_cache[path]

def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default

def check_evidence(item):
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        return False, "没有附依据(读过哪些代码)"
    bad = []
    for e in ev:
        if not isinstance(e, dict):
            bad.append(str(e)); continue
        f = e.get("file") or item.get("file")
        where = e.get("lines", e.get("line"))
        if f and "/" not in f and not file_lines(f) and os.path.isfile(os.path.join(d, f)):
            continue  # review_dir 里的材料(intent.md、rules.txt…),位置可以是「验收标准 2」这类文字
        rg = crlib.parse_lines(where)
        ls = file_lines(f) if f else None
        if ls is not None and rg is None and str(where or "").strip() and str(where).strip() in "\n".join(ls):
            continue  # 用文字定位(如 REVIEW.md 里的「PRJ-001」),那段文字在文件里就算存在
        if ls is None or rg is None or rg[0] < 1 or rg[1] > len(ls):
            bad.append("%s:%s" % (f, e.get("lines", e.get("line"))))
    if bad:
        return False, "依据引用的位置不存在:" + "、".join(bad[:3])
    return True, ""

# 运行记录:verifier 声称跑过的命令必须经 run-check.sh 留下记录;报告展示的输出取自记录的日志。
runs_dir = os.path.join(d, "runs")
run_stats = {"failed_claims": 0, "reproduced": 0}

def apply_runs(item, conf, reasons):
    """声称执行(verification.method=executed)的,每个 run id 都要有记录;否则按读代码推断,最高 60 分。"""
    v = item.get("verification") if isinstance(item.get("verification"), dict) else {}
    runs = []
    if v.get("method") == "executed":
        ids = v.get("runs") if isinstance(v.get("runs"), list) else []
        for rid in ids:
            run = crlib.load_json(os.path.join(runs_dir, "%s.json" % rid), {}) if isinstance(rid, str) and re.fullmatch(r"r\d+", rid) else {}
            if run:
                runs.append(run)
        if not ids or len(runs) != len(ids):
            runs = []
            reasons.append("声称执行但没有对应的运行记录")
    elif item.get("repro"):
        reasons.append("写了复现结果但没有经 run-check.sh 的运行记录")
    if runs:
        last = runs[-1]
        try:
            tail = open(os.path.join(runs_dir, last["log"]), encoding="utf-8", errors="replace").read().rstrip("\n").split("\n")[-5:]
        except (OSError, KeyError):
            tail = []
        item["repro"] = {"run": last.get("id"), "cmd": " ".join(last.get("argv") or []), "exit": last.get("exit"),
                         "tests_run": last.get("tests_run"), "tests_failed": last.get("tests_failed"), "tail": tail}
        item["verification"] = dict(v, method="executed")
        item["mark"] = "已复现"
        return conf
    if v.get("method") == "executed" or item.get("repro"):
        run_stats["failed_claims"] += 1
        conf = min(conf, 60)
    item.pop("repro", None)
    item["verification"] = dict(v, method="inferred")
    item["mark"] = "读代码推断"
    return conf

def group_of(cat):
    return {"bug": "defects", "security": "defects", "intent": "intent", "rules": "rules"}.get(cat, "defects")

passed, rejected, history = [], [], []
skipped_by_settings = 0
for c in cands:
    r = result_for(c)
    if r is None:
        rejected.append(dict(c, confidence=0, verdict="没有核实结果", reject_reason="没有核实结果"))
        continue
    item = dict(r)
    for k in ("found_by", "pass_hits", "from_prev", "id", "since", "raised_in_discussion"):
        if k in c and k not in item:
            item[k] = c[k]
    for k in ("from_prev", "id", "since"):
        if k in c:
            item[k] = c[k]
    item.pop("needs_check", None)
    conf = to_int(item.get("confidence"))
    reasons = []
    f = item.get("file", "")
    if any(crlib.glob_match(g, f) for g in skip):
        skipped_by_settings += 1
        continue
    if item.get("history_match"):
        history.append(item)
        continue
    cat = item.get("category", "bug")
    if cat == "rules":
        rf = str(item.get("rule_file") or "").strip()
        q = crlib.norm(item.get("rule_quote") or "")
        mine = [b for b in rule_blocks if not rf or b["file"] == rf]
        hit = next((b for b in mine if q and q in crlib.norm(b["text"]) and f in b["applies_files"]), None)
        if not q:
            conf = 0; reasons.append("没有引用规则原文")
        elif not mine:
            conf = 0; reasons.append("规则文件不在这次适用的规则里")
        elif not hit:
            conf = 0
            reasons.append("规则不覆盖这个文件" if any(q in crlib.norm(b["text"]) for b in mine) else "引用的规则原文对不上")
        else:
            item["rule_file"] = hit["file"]
            item["rule_source"] = hit["source"]
    if item.get("severity") not in crlib.SEV_ORDER:
        item["severity"] = "minor" if cat == "rules" else "major"
    ok, why = check_evidence(item)
    if not ok and conf > 50:
        conf = 50; reasons.append(why)
    elif not ok:
        reasons.append(why)
    conf = apply_runs(item, conf, reasons)
    line = to_int(item.get("line"))
    # 没变的文件不在这次的 diff 里,重新核实的历史发现不按改动行判断
    if not (c.get("from_prev") and status.get(f) in ("unchanged", "dependent")) and not crlib.in_ranges(ranges, f, line):
        conf = 0; reasons.append("不在改动的行上")
    item["confidence"] = conf
    if conf >= THRESHOLD:
        passed.append(item)
    else:
        if reasons:
            item["reject_reason"] = ";".join(reasons)
        rejected.append(item)

carried, resolved = [], []
prev_ids = {p.get("id") for p in prev}
# 上次之后被判为不成立或不修、代码也没变的,不再带入。上下文哈希不含判断记录,所以这里要自己看。
judged = {h.get("id"): h for h in crlib.load_json(os.path.join(d, "history.json"), [])
          if h.get("id") and h.get("verdict") in ("invalid", "wontfix") and h.get("same_code")}
# 这一轮重新提交核实的历史发现:有核实结果就以这一轮为准(通过则带入,没通过则已解决),不看文件有没有变。
resubmitted = {c.get("id") for c in cands if c.get("from_prev")}
reverified = {x.get("id") for x in passed if x.get("from_prev")}
for p in prev:
    st = status.get(p.get("file"))
    if st == "unchanged" and p.get("id") in judged:
        h = judged[p["id"]]
        history.append(dict(p, history_match=p["id"], suppressed_by="history",
                            verdict="已在 %s 被判为%s:%s" % (h.get("time", ""), h.get("verdict_name", ""), h.get("reason", ""))))
    elif st in (None, "removed"):
        resolved.append(dict(p, resolved_reason="文件已不在改动里"))
    elif p.get("id") in resubmitted:
        if p.get("id") not in reverified:
            resolved.append(dict(p, resolved_reason="重新核实后不再成立"))
    elif st == "unchanged":
        carried.append(dict(p, carried_unchanged=True))
    elif st in ("changed", "dependent"):
        resolved.append(dict(p, resolved_reason="代码变了,重新核实后不再成立"))
new, again = [], []
for x in passed:
    (again if x.get("from_prev") else new).append(x)
carried.extend(again)

def rule_key(x):
    return (x.get("rule_file") or "", crlib.norm(x.get("rule_quote") or ""))
def same(a, b):
    return (a.get("file") == b.get("file") and a.get("category") == b.get("category")
            and abs(to_int(a.get("line")) - to_int(b.get("line"))) <= 3 and rule_key(a) == rule_key(b))
dedup = []
for x in new:
    hit = next((c for c in carried if same(c, x)), None)
    if hit:
        hit["line"] = x.get("line", hit.get("line"))
        continue
    dedup.append(x)
new = dedup

# 同一条规则在同一个文件里的多处合并成一条
def merge_rules(items):
    out = []
    for x in sorted(items, key=lambda y: to_int(y.get("line"))):
        if x.get("category") == "rules":
            hit = next((o for o in out if o.get("category") == "rules" and rule_key(o) == rule_key(x) and o.get("file") == x.get("file")), None)
            if hit:
                hit.setdefault("also_lines", []).append(to_int(x.get("line")))
                continue
        out.append(x)
    for o in out:
        if o.get("also_lines"):
            o["summary"] = "%s(共 %d 处,其余在第 %s 行)" % (o.get("summary", ""), 1 + len(o["also_lines"]), "、".join(map(str, o["also_lines"])))
    return out
new = merge_rules(new)

order = lambda x: (crlib.SEV_ORDER.get(x.get("severity"), 3), -to_int(x.get("confidence")), x.get("file", ""), to_int(x.get("line")))
for x in new: x["status"] = "new"
for x in carried: x["status"] = "carried"
groups = {"defects": [], "intent": [], "rules": []}
for x in new + carried:
    groups[group_of(x.get("category"))].append(x)
for k in groups:
    groups[k].sort(key=lambda x: (x["status"] != "new",) + order(x))

limits = {"defects": 5 if meta.get("depth") == "quick" else 10, "intent": 5, "rules": 5}
sev_limits = {}
for k, v in (settings.get("limits") or {}).items():
    n = to_int(v, -1)
    if n < 0: continue
    if k in limits: limits[k] = n
    elif k in crlib.SEV_ORDER: sev_limits[k] = n
trimmed, shown = {}, {}
for k, items in groups.items():
    keep, cut, per_sev = [], [], {}
    for x in items:
        s = x.get("severity")
        if len(keep) >= limits[k] or (k in ("defects", "intent") and s in sev_limits and per_sev.get(s, 0) >= sev_limits[s]):
            cut.append(x); continue
        per_sev[s] = per_sev.get(s, 0) + 1
        keep.append(x)
    shown[k] = keep
    if cut:
        trimmed[k] = len(cut)
        for x in cut: x["status"] = x["status"] + "-trimmed"
    groups[k] = keep + cut

stamp = re.sub(r"\D", "", meta.get("started_at", "")) or "0"
seq = 0
no = 0
# 编号只属于这次展示的发现;带入的发现身上上次的编号要先去掉,否则"第 2 条"可能对应两条。
for k in groups:
    for x in groups[k]:
        x.pop("no", None)
for k in ("defects", "intent", "rules"):
    for x in shown[k]:
        no += 1
        x["no"] = no
for k in groups:
    for x in groups[k]:
        if not x.get("id"):
            seq += 1
            while "%s-%d" % (stamp, seq) in prev_ids:
                seq += 1
            x["id"] = "%s-%d" % (stamp, seq)
            x["since"] = meta.get("started_at", "")
        x.pop("cid", None); x.pop("from_prev", None)

standing = [x for k in ("defects", "intent", "rules") for x in groups[k]]
def record(x):
    y = {k: v for k, v in x.items() if k not in ("status", "carried_unchanged")}
    if not x.get("no"):
        y.pop("no", None)
    return y
crlib.save_json(os.path.join(d, "findings.json"), [record(x) for x in standing])
crlib.save_json(os.path.join(d, "rejected.json"), [{k: v for k, v in x.items() if k != "cid"} for x in rejected])
counts = {"candidates": len(cands), "verified": len([c for c in cands if c.get("cid") in by_cid]) or len(results),
          "passed": len(passed), "rejected": len(rejected), "skipped_by_settings": skipped_by_settings}
run_summary = {"total": len(glob.glob(os.path.join(runs_dir, "r*.json"))),
               "reproduced": len([x for x in passed if x.get("mark") == "已复现"
                                  and (x.get("verification") or {}).get("outcome") == "reproduced"]),
               "failed_claims": run_stats["failed_claims"]}
final = {
    "scope": meta.get("review_scope", "full"), "depth": meta.get("depth", "standard"),
    "groups": {k: [x for x in shown[k]] for k in shown}, "resolved": resolved, "ignored_by_history": history,
    "trimmed": trimmed, "limits": limits, "counts": counts, "runs": run_summary,
    "notes": crlib.load_json(os.path.join(d, "rules-notes.json"), []),
}
crlib.save_json(os.path.join(d, "final.json"), final)
print("passed=%d rejected=%d new=%d carried=%d resolved=%d trimmed=%d ignored_by_history=%d" % (
    len(passed), len(rejected), len([x for x in standing if x["status"].startswith("new")]),
    len([x for x in standing if x["status"].startswith("carried")]), len(resolved), sum(trimmed.values()), len(history)))
