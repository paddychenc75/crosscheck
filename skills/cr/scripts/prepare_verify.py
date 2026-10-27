"""prepare-verify.sh 的实现,用法和说明见 prepare-verify.sh。"""
import os, re, sys
sys.dont_write_bytecode = True
import crlib

d = os.path.abspath(sys.argv[1])
per = max(1, int(sys.argv[2]))
cands = crlib.load_json(os.path.join(d, "candidates.json"), [])
PREV_VERDICT = ("confidence", "verdict", "evidence", "ruled_out", "no", "status", "history_match", "previously",
                "suppressed_by", "reject_reason", "also_lines", "carried_unchanged", "compat", "cid", "verification", "mark", "repro", "pass")
MERGED = re.compile(r"\(共 \d+ 处,其余在第 [^)]* 行\)$")
notes = crlib.load_json(os.path.join(d, "rules-notes.json"), [])
keep = []
for c in cands:
    s = str(c.get("summary", ""))
    if s.startswith(("规则可能过时", "规则冲突")):
        if any(n.get("summary") == s and n.get("file") == c.get("file", "") for n in notes):
            continue
        notes.append({"kind": "stale" if s.startswith("规则可能过时") else "conflict", "summary": s,
                      "rule_file": c.get("rule_file", ""), "file": c.get("file", ""), "detail": c.get("failure_scenario", "")})
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
strip = lambda c: {k: v for k, v in c.items() if k not in ("found_by", "pass_hits", "pass")}
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
