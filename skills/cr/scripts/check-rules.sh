#!/bin/sh
# check-rules.sh — 核对 rules-auditor 交来的检查清单:每个适用的规则单位都检查过了吗,核对码对不对。
#
# 用法: check-rules.sh DIR
#
#   读取 DIR/rules-auditor-*.json(编排者把每次 rules-auditor 的回复原样存成这些文件),
#   对照 rules-index.json 和 .rules-nonce,写出 rules-coverage.json,并输出:
#     OK: 已检查 a/b · 核对码一致                       全部覆盖
#     MISSING: <单位>(<文件>…);…                        有漏掉的,同时写出 rerun-rules.md,编排者据此补跑一次
#     NONCE: 核对码不一致或缺失                          规则可能没读到底
#   有漏检或核对码不对时以 1 退出。没有适用的规则时直接输出 OK。
# 另外把 rules-auditor 给出的「规则可能过时:」「规则冲突:」说明收进 rules-notes.json。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "check-rules: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "check-rules: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$1" && pwd) python3 - <<'PY'
import glob, json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

d = os.environ["CR_DIR"]
units = crlib.load_json(os.path.join(d, "rules-index.json"), [])
try:
    want_nonce = open(os.path.join(d, ".rules-nonce")).read().strip()
except OSError:
    want_nonce = ""
ranges = crlib.ranges_of(d)

outputs, checked, notes, nonces = [], [], [], []
for p in sorted(glob.glob(os.path.join(d, "rules-auditor-*.json"))):
    try:
        x = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        x = None
    outputs.append(os.path.basename(p))
    if isinstance(x, dict):
        nonces.append(str(x.get("nonce", "")).strip())
        for c in x.get("checked") or []:
            if isinstance(c, dict):
                checked.append(c)
        fs = x.get("findings") or []
    elif isinstance(x, list):
        nonces.append("")
        fs = x
    else:
        nonces.append("")
        fs = []
    for f in fs:
        s = str((f or {}).get("summary", ""))
        if s.startswith(("规则可能过时", "规则冲突")):
            notes.append({"kind": "stale" if s.startswith("规则可能过时") else "conflict", "summary": s,
                          "rule_id": f.get("rule_id", ""), "file": f.get("file", ""), "detail": f.get("failure_scenario", "")})

required, covered, missing = 0, 0, []
ok_entries, bad_lines, ok_without_lines, na_without_note = [], [], 0, 0
for u in units:
    files = u.get("applies_files") or []
    hits = [c for c in checked if str(c.get("rule_id", "")).strip() == u["unit"]]
    seen = set()
    for c in hits:
        cf = c.get("files") or ([c["file"]] if c.get("file") else files)
        seen.update(cf)
        res = str(c.get("result", "")).strip()
        if res == "ok":
            for f in cf:
                rg = crlib.parse_lines(c.get("lines"))
                if rg is None:
                    ok_without_lines += 1
                    continue
                if f in ranges and not crlib.in_ranges(ranges, f, rg[0], rg[1]):
                    bad_lines.append({"unit": u["unit"], "file": f, "lines": c.get("lines")})
                    continue
                ok_entries.append({"unit": u["unit"], "file": f, "lines": "%d-%d" % rg, "text": u.get("text", "")[:300]})
        elif res in ("n/a", "na", "不适用") and not str(c.get("note", "")).strip():
            na_without_note += 1
    lost = [f for f in files if f not in seen]
    required += len(files)
    covered += len(files) - len(lost)
    if lost:
        missing.append({"unit": u["unit"], "files": lost, "layer": u.get("layer")})

nonce_ok = bool(want_nonce) and bool(nonces) and all(n == want_nonce for n in nonces)
if not units:
    nonce_ok = True
cov = {"units": len(units), "required_pairs": required, "covered_pairs": covered, "missing": missing,
       "nonce_ok": nonce_ok, "outputs": outputs, "ok_without_lines": ok_without_lines, "bad_lines": bad_lines,
       "na_without_note": na_without_note, "checked_ok": ok_entries}
crlib.save_json(os.path.join(d, "rules-coverage.json"), cov)
crlib.save_json(os.path.join(d, "rules-notes.json"), notes)

if not units:
    print("OK: 没有适用的规则")
    sys.exit(0)
bad = False
if missing:
    bad = True
    print("MISSING: " + ";".join("%s(%s)" % (m["unit"], "、".join(m["files"][:5])) for m in missing))
    with open(os.path.join(d, "rerun-rules.md"), "w", encoding="utf-8") as f:
        f.write("上一次的检查清单漏掉了下面这些规则。只检查这些,检查清单照常交,核对码不变。\n\n")
        for m in missing:
            f.write("- %s:%s\n" % (m["unit"], "、".join(m["files"])))
if not nonce_ok:
    bad = True
    print("NONCE: 核对码不一致或缺失(应为任务里规则段落末尾的核对码),规则可能没读到底")
if not bad:
    print("OK: 已检查 %d/%d · 核对码一致%s" % (covered, required, "" if not ok_without_lines else " · %d 条「没违反」没写检查了哪些行" % ok_without_lines))
sys.exit(1 if bad else 0)
PY
