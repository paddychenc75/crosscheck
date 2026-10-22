#!/bin/sh
# aggregate.sh — 汇总核实结果:校验依据、套用审查设置、过滤、排序、编号,写出报告和记录要用的文件。
#
# 用法: aggregate.sh DIR
#
# 读取 DIR 里的 candidates.json(prepare-verify.sh 编过号的候选)和 verify/result-*.json(编排者把每个
# verifier 的回复原样存成这些文件),以及 meta.txt、ranges.txt、settings.json、rules-index.json、
# incremental.txt、prev-findings.json。按顺序做这些事,都不需要判断:
#   1. 依据校验:verifier 给出的 evidence 引用的文件和行在被审查的那一版里必须真实存在
#      (review_dir 里的 intent.md、rules.txt 等材料只核对文件存在;位置写成文字的,核对那段文字在文件里);
#      引用不存在或者干脆没给依据的,置信度最高 50。
#   2. 规则发现:rule_id 必须是这次适用的规则、作用域覆盖这个文件;rule_quote 必须逐字出现在规则原文里。
#      对不上的置信度为 0。严重程度按 REVIEW.md 的 severity 设置 → 规则自己的 severity → 核实后的分级 → 轻微。
#   3. 只看改动的行;REVIEW.md 的 skip 路径不报;带 history_match 的单独列为「已按历史判断忽略」。
#   4. 置信度 80 分以上的才报,其余连同分数和理由写进 rejected.json。
#   5. 抽查:抽查的规则检查如果被 verifier 判定确实违反(80 分以上),直接作为规则发现报出,并记为抽查发现漏检。
#   6. 增量:上次的发现分成遗留(文件没变的原样带入;文件变了且再次通过核实的)和已解决。
#   7. 同一条规则在同一个文件里的多处合并成一条;按严重程度、置信度排序;套用条数上限
#      (默认缺陷 10 条、quick 5 条,需求核对、规范、可简化各 5 条;REVIEW.md 的 limits 可覆盖,
#      还可以按 critical/major/minor 限制缺陷和需求核对的条数),超出的不展示但仍写进 findings.json。
#   8. 给展示的发现编号(no),给新发现分配 id 和 since。
# 写出:
#   final.json     报告要用的全部内容:groups(defects/intent/rules/simplify,每条带 no 和 status:new|carried)、
#                  resolved、ignored_by_history、trimmed、counts、spot、notes
#   findings.json  本次结束时仍然成立的全部发现(含因条数上限没展示的),供 save-review.sh 保存和下次增量
#   rejected.json  没通过的候选,带分数和理由,用于校准阈值
# 最后一行输出:passed=… rejected=… new=… carried=… resolved=… trimmed=… spot_failed=…

set -eu

command -v python3 >/dev/null 2>&1 || { echo "aggregate: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "aggregate: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$1" && pwd) python3 - <<'PY'
import glob, json, os, re, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
import rule_materials

d = os.environ["CR_DIR"]
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
settings = crlib.load_json(os.path.join(d, "settings.json"), {})
units = {u["unit"]: u for u in crlib.load_json(os.path.join(d, "rules-index.json"), [])}
ranges = crlib.ranges_of(d)
src = crlib.code_source(meta)
skip = settings.get("skip") or []
sev_override = settings.get("severity") or {}
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
    normal_ids = [x["cid"] for x in prepared if x.get("kind") != "spot"]
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

materials = rule_materials.load(d)
material_files = {x["file"] for x in materials.get("records", []) if x.get("file")}
_lines_cache = {}
def file_lines(path):
    if path not in _lines_cache:
        if path in material_files:
            try:
                with open(os.path.join(d, path), encoding="utf-8") as f:
                    t = f.read()
            except OSError:
                t = None
        else:
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

def group_of(cat):
    return {"bug": "defects", "security": "defects", "intent": "intent", "rules": "rules", "simplify": "simplify"}.get(cat, "defects")

passed, rejected, history, spot_ok, spot_failed = [], [], [], 0, 0
skipped_by_settings = 0
# (候选, 核实结果) 成对处理;抽查只存在于核实结果里。
pairs = [(c, result_for(c)) for c in cands]
pairs += ([(c, by_cid.get(c["cid"])) for c in prepared if c.get("kind") == "spot"] if batch_paths
          else [(r, r) for r in results if r.get("kind") == "spot"])
for c, r in pairs:
    if r is None:
        rejected.append(dict(c, confidence=0, verdict="没有核实结果", reject_reason="没有核实结果"))
        continue
    item = dict(r)
    for k in ("found_by", "pass_hits", "from_prev", "id", "since"):
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
        rid = str(item.get("rule_id") or "").strip()
        u = units.get(rid) or units.get("FILE:" + rid)
        if not rid or not u:
            conf, reasons = 0, reasons + ["规则编号不在这次适用的规则里"]
        else:
            item["rule_id"] = u["unit"]
            if f not in (u.get("applies_files") or []):
                conf, reasons = 0, reasons + ["规则的作用域不覆盖这个文件"]
            q = crlib.norm(item.get("rule_quote") or "")
            if q:
                if q not in crlib.norm(u.get("text", "")):
                    conf, reasons = 0, reasons + ["引用的规则原文对不上"]
            else:
                conf = 0; reasons.append("没有引用规则原文")
            item["severity"] = sev_override.get(u["unit"]) or u.get("severity") or item.get("severity") or "minor"
            item["rule_file"] = u.get("file")
            item["rule_origin"] = u.get("origin", u.get("file"))
            item["rule_source"] = u.get("source_name", u.get("source"))
            item["document_rule"] = u.get("document", False)
    elif cat == "simplify":
        item["severity"] = "minor"
    if item.get("severity") not in crlib.SEV_ORDER:
        item["severity"] = "minor" if cat in ("rules", "simplify") else "major"
    ok, why = check_evidence(item)
    if not ok and conf > 50:
        conf = 50; reasons.append(why)
    elif not ok:
        reasons.append(why)
    line = to_int(item.get("line"))
    if not crlib.in_ranges(ranges, f, line):
        conf = 0; reasons.append("不在改动的行上")
    item["confidence"] = conf
    if c.get("kind") == "spot":
        if conf >= THRESHOLD:
            spot_failed += 1
            item.pop("kind", None)
            item["found_by"] = ["verifier 抽查"]
            item["spot"] = True
            passed.append(item)
        else:
            spot_ok += 1
        continue
    if conf >= THRESHOLD:
        passed.append(item)
    else:
        if reasons:
            item["reject_reason"] = ";".join(reasons)
        rejected.append(item)

carried, resolved = [], []
prev_ids = {p.get("id") for p in prev}
for p in prev:
    st = status.get(p.get("file"))
    if st == "unchanged":
        carried.append(dict(p, carried_unchanged=True))
    elif st in (None, "removed"):
        resolved.append(dict(p, resolved_reason="文件已不在改动里"))
reverified = {x.get("id") for x in passed if x.get("from_prev")}
for p in prev:
    if status.get(p.get("file")) in ("changed", "dependent") and p.get("id") not in reverified:
        resolved.append(dict(p, resolved_reason="代码变了,重新核实后不再成立"))
new, again = [], []
for x in passed:
    (again if x.get("from_prev") else new).append(x)
carried.extend(again)

def same(a, b):
    if str(a.get("rule_id", "")).startswith("FILE:") and (crlib.norm(a.get("summary", "")) != crlib.norm(b.get("summary", "")) or crlib.norm(a.get("rule_quote", "")) != crlib.norm(b.get("rule_quote", ""))):
        return False
    return (a.get("file") == b.get("file") and a.get("category") == b.get("category")
            and abs(to_int(a.get("line")) - to_int(b.get("line"))) <= 3
            and str(a.get("rule_id") or "") == str(b.get("rule_id") or ""))
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
        if x.get("category") == "rules" and x.get("rule_id") and not x.get("document_rule") and not str(x["rule_id"]).startswith("FILE:"):
            hit = next((o for o in out if o.get("category") == "rules" and o.get("rule_id") == x["rule_id"] and o.get("file") == x.get("file")), None)
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
groups = {"defects": [], "intent": [], "rules": [], "simplify": []}
for x in new + carried:
    groups[group_of(x.get("category"))].append(x)
for k in groups:
    groups[k].sort(key=lambda x: (x["status"] != "new",) + order(x))

limits = {"defects": 5 if meta.get("depth") == "quick" else 10, "intent": 5, "rules": 5, "simplify": 5}
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
for k in ("defects", "intent", "rules", "simplify"):
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

standing = [x for k in ("defects", "intent", "rules", "simplify") for x in groups[k]]
def record(x):
    y = {k: v for k, v in x.items() if k not in ("status", "carried_unchanged")}
    if not x.get("no"):
        y.pop("no", None)
    return y
crlib.save_json(os.path.join(d, "findings.json"), [record(x) for x in standing])
crlib.save_json(os.path.join(d, "rejected.json"), [{k: v for k, v in x.items() if k != "cid"} for x in rejected])
counts = {"candidates": len(cands), "verified": len([c for c in cands if c.get("cid") in by_cid]) or len(results),
          "passed": len(passed), "rejected": len(rejected), "skipped_by_settings": skipped_by_settings}
unverified = []
if materials and not materials.get("complete"):
    prior = crlib.load_json(os.path.join(d, "previous-findings.json"), [])
    for f in prior:
        if f.get("category") == "rules" and not any(x.get("category") == "rules" and x.get("file") == f.get("file") and x.get("rule_id") == f.get("rule_id") and crlib.norm(x.get("summary", "")) == crlib.norm(f.get("summary", "")) and crlib.norm(x.get("rule_quote", "")) == crlib.norm(f.get("rule_quote", "")) for x in passed):
            unverified.append(dict(f, status="unverified", status_reason="本轮外部规则材料不完整或有冲突，未能重新核实"))
    resolved = [x for x in resolved if x.get("category") != "rules"]
crlib.save_json(os.path.join(d, "unverified.json"), unverified)
final = {
    "unverified": unverified, "materials_complete": materials.get("complete", True),
    "scope": meta.get("review_scope", "full"), "depth": meta.get("depth", "standard"),
    "groups": {k: [x for x in shown[k]] for k in shown}, "resolved": resolved, "ignored_by_history": history,
    "trimmed": trimmed, "limits": limits, "counts": counts, "spot": {"checked": spot_ok + spot_failed, "failed": spot_failed},
    "notes": crlib.load_json(os.path.join(d, "rules-notes.json"), []),
}
crlib.save_json(os.path.join(d, "final.json"), final)
print("passed=%d rejected=%d new=%d carried=%d resolved=%d trimmed=%d spot_failed=%d ignored_by_history=%d" % (
    len(passed), len(rejected), len([x for x in standing if x["status"].startswith("new")]),
    len([x for x in standing if x["status"].startswith("carried")]), len(resolved), sum(trimmed.values()), spot_failed, len(history)))
PY
