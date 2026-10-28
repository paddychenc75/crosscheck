"""aggregate.sh 先调用的流程完整性检查:查找角色的回复、核实结果是否都已落盘。

只看 review_dir 里的文件,不看宿主自报的数字。缺了就把说明写进 completeness.txt(每条一行 INCOMPLETE: 缺 …),
不阻止汇总和出报告;report-lines.sh 再把它放进报告。
"""
import glob, json, os, re, sys
sys.dont_write_bytecode = True
import crlib

d = os.path.abspath(sys.argv[1])
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
rules = crlib.read_kv(os.path.join(d, "rules-meta.txt"))
depth = meta.get("depth", "standard")
only = meta.get("only", "all")
dims = {"bug", "security", "rules"} if only in ("", "all") else set(only.split(","))
# quick 不分批,由宿主自己执行一遍(见 SKILL.md 第 3 步)
batches = 1 if depth == "quick" else max(1, int(meta.get("batches") or 1))
issues = []

def result_rows(path):
    """核实回复:数组,或者 aggregate 也认的 {"candidates": [...]} / {"results": [...]} 外壳。"""
    try:
        with open(path, encoding="utf-8") as f:
            x = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(x, dict):
        x = x.get("candidates") or x.get("results") or []
    return [r for r in x if isinstance(r, dict)] if isinstance(x, list) else []

# 查找角色:quick 只有 reviewer;--only 排除的不跑;没有适用的规则时不跑 rules-auditor
roles = []
if dims & {"bug", "security"}:
    roles.append("reviewer")
if depth != "quick" and "rules" in dims and int(rules.get("blocks") or 0) > 0:
    roles.append("rules-auditor")
for role in roles:
    names = ["%s-%d.json" % (role, i) for i in range(1, batches + 1)] if batches > 1 else ["%s.json" % role]
    for name in names:
        p = os.path.join(d, "finders", name)
        if not os.path.isfile(p):
            issues.append("finders/%s" % name)
            continue
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
        except (OSError, ValueError):
            issues.append("finders/%s(不是合法的 JSON)" % name)

# 核实:有候选就必须每条都进了某个批次,每个批次都有核实结果
cands = crlib.load_json(os.path.join(d, "candidates.json"), [])
if cands:
    rows_by_batch = {}
    for p in glob.glob(os.path.join(d, "verify", "batch-*.json")):
        m = re.fullmatch(r"batch-(\d+)\.json", os.path.basename(p))
        if m:
            rows_by_batch[int(m.group(1))] = crlib.load_json(p, [])
    if not rows_by_batch:
        issues.append("verify/batch-*.json(核实批次没有生成)")
    else:
        done = set()
        for p in glob.glob(os.path.join(d, "verify", "result-*.json")):
            done.update(r.get("cid") for r in result_rows(p))
        in_batch = set()
        for n in sorted(rows_by_batch):
            cids = [r.get("cid") for r in rows_by_batch[n] if isinstance(r, dict)]
            in_batch.update(cids)
            miss = [c for c in cids if c not in done]
            if miss:
                issues.append("verify/result-%d.json(核实批次 %d 有 %d 条没有核实结果)" % (n, n, len(miss)))
        lost = [c for c in cands if c.get("cid") not in in_batch]
        if lost:
            issues.append("核实批次:%d 条候选没有进入任何 verify/batch-*.json" % len(lost))

with open(os.path.join(d, "completeness.txt"), "w", encoding="utf-8") as f:
    if issues:
        for x in issues:
            f.write("INCOMPLETE: 缺 %s\n" % x)
    else:
        f.write("OK: 查找角色和核实结果都已落盘\n")
for x in issues:
    print("completeness: 流程不完整:缺 %s" % x, file=sys.stderr)
