"""prepare-verify.sh 的实现,用法和说明见 prepare-verify.sh。"""
import glob, json, os, re, sys
sys.dont_write_bytecode = True
import crlib

d = os.path.abspath(sys.argv[1])
per = max(1, int(sys.argv[2]))
cands = crlib.load_json(os.path.join(d, "candidates.json"), [])
PREV_VERDICT = ("confidence", "verdict", "evidence", "ruled_out", "no", "status", "history_match", "previously",
                "suppressed_by", "reject_reason", "also_lines", "carried_unchanged", "compat", "cid", "verification", "mark", "repro", "pass",
                "history_context", "history_decision", "suppression_reason", "source_refs", "candidate_ids", "merge_reason")
MERGED = re.compile(r"\(共 \d+ 处,其余在第 [^)]* 行\)$")
notes = crlib.load_json(os.path.join(d, "rules-notes.json"), [])
# 原件只在 finders/ 保存。source_refs 用文件名#数组下标定位,不另造原始候选存储。
raw = {}
for path in sorted(glob.glob(os.path.join(d, "finders", "*.json"))):
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        continue  # completeness 会记录损坏的原件,不能伪造候选。
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if isinstance(rows, list):
        for n, row in enumerate(rows):
            if isinstance(row, dict):
                raw["finders/%s#%d" % (os.path.basename(path), n)] = row
identity = ("file", "line", "category", "severity", "summary", "failure_scenario", "needs_check", "suggestion", "rule_file", "rule_quote", "also_at")
used = set()
for c in cands:
    if c.get("from_prev"):
        continue
    refs = c.get("source_refs")
    if not isinstance(refs, list):
        refs = [ref for ref, row in raw.items() if all(c.get(k) == row.get(k) for k in identity)]
    if any(not isinstance(ref, str) or ref not in raw for ref in refs):
        sys.exit("prepare-verify: source_refs 引用了不存在的原始候选")
    if len(refs) > 1 and not str(c.get("merge_reason") or "").strip():
        if not all(all(raw[ref].get(k) == raw[refs[0]].get(k) for k in identity) for ref in refs):
            sys.exit("prepare-verify: 多条候选合并缺少 merge_reason,请说明根因或分开提交")
        c["merge_reason"] = "关键字段完全相同的重复候选"
    c["source_refs"] = list(dict.fromkeys(refs))
    used.update(refs)
    for key in ("failure_scenario", "needs_check"):
        texts = list(dict.fromkeys(str(raw[ref][key]) for ref in refs if raw[ref].get(key)))
        if texts:
            current = str(c.get(key) or "")
            c[key] = "\n".join([current] + [text for text in texts if text not in current]).strip()
for ref, row in raw.items():
    if ref not in used:
        cands.append(dict(row, source_refs=[ref]))
# 历史处理判断也要重新核实调用环境,不能因本文件没变就直接抑制。
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
judged_ids = {h.get("id") for h in crlib.load_json(os.path.join(d, "history.json"), [])
              if h.get("verdict") in ("invalid", "wontfix")}
if meta.get("review_scope") == "incremental":
    present = {c.get("id") for c in cands if c.get("from_prev")}
    for old in crlib.load_json(os.path.join(d, "prev-findings.json"), []):
        if old.get("id") in judged_ids - present:
            cands.append(dict(old, from_prev=True))
keep = []
for c in cands:
    s = str(c.get("summary", ""))
    if s.startswith(("规则可能过时", "规则冲突")):
        previous_note = next((n for n in notes if n.get("summary") == s and n.get("file") == c.get("file", "")), None)
        if previous_note is not None:
            previous_note["source_refs"] = list(dict.fromkeys(previous_note.get("source_refs", []) + c.get("source_refs", [])))
            continue
        notes.append({"kind": "stale" if s.startswith("规则可能过时") else "conflict", "summary": s,
                      "rule_file": c.get("rule_file", ""), "file": c.get("file", ""), "detail": c.get("failure_scenario", ""),
                      "source_refs": c.get("source_refs", [])})
        continue
    if c.get("from_prev"):
        for k in PREV_VERDICT:
            c.pop(k, None)
        c["summary"] = MERGED.sub("", str(c.get("summary", "")))
    keep.append(c)
for i, c in enumerate(keep, 1):
    c.setdefault("cid", "c%d" % i)
crlib.save_json(os.path.join(d, "candidates.json"), keep)
crlib.save_json(os.path.join(d, "rules-notes.json"), notes)

groups = {}
for c in keep:
    groups.setdefault(c.get("file", ""), []).append(c)
batches, cur = [], []
strip = lambda c: {k: v for k, v in c.items() if k not in ("found_by", "pass_hits", "pass", "source_refs", "merge_reason", "candidate_ids")}
for f in sorted(groups):
    for c in groups[f]:
        if c.get("severity") == "critical":
            batches.append([strip(c)])   # 严重问题单独核实,不受同批其他候选的影响
            continue
        if len(cur) >= per:
            batches.append(cur); cur = []
        cur.append(strip(c))
if cur:
    batches.append(cur)
vd = os.path.join(d, "verify")
os.makedirs(vd, exist_ok=True)
for name in os.listdir(vd):
    if name.startswith(("batch-", "result-")):
        os.remove(os.path.join(vd, name))
# 新一轮核实不得误用上一轮的结果。
for name in ("final.json", "findings.json", "rejected.json"):
    path = os.path.join(d, name)
    if os.path.isfile(path): os.remove(path)
for i, b in enumerate(batches, 1):
    p = os.path.join(vd, "batch-%d.json" % i)
    crlib.save_json(p, b)
    print(p)
print("batches=%d candidates=%d" % (len(batches), len(keep)))
