"""审查开始时取出这次改动涉及的文件上的历史判断,标明那段代码现在是否原样还在(same_code)。

用法: python3 feedback_history.py REVIEW_DIR OUT.json    由 collect-diff.sh 调用
不删除任何候选。same_code 仅说明局部文本相同,调用环境和历史前提是否仍成立由 verifier 重新核实。
"""
import os
import sys

import crlib

NAMES = {"invalid": "不成立", "wontfix": "成立但不修", "valid": "成立", "disputed": "有争议"}


def changed_files(rdir):
    for name in ("files.full.txt", "files.txt"):
        p = os.path.join(rdir, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return {l.strip() for l in f if l.strip()}
    return set()


def history(rdir, out):
    meta = crlib.read_kv(os.path.join(rdir, "meta.txt"))
    pdir = os.path.dirname(meta["store_dir"]) if meta.get("store_dir") else ""
    files = changed_files(rdir)
    entries = [e for e in crlib.feedback_entries(pdir).values() if e.get("file") in files] if pdir else []
    if not entries:
        return
    src = crlib.code_source(meta)
    result = []
    for e in entries:
        ls = (src.lines(e["file"]) if src else None) or []
        where = [i + 1 for i, x in enumerate(ls)
                 if e.get("code_line") and crlib.norm(x) == crlib.norm(e["code_line"]) and crlib.ctx_hash(ls, i + 1) == e.get("context_hash")]
        result.append({
            "verdict": e.get("verdict"), "verdict_name": NAMES.get(e.get("verdict"), ""), "reason": e.get("reason", ""), "time": e.get("time", ""),
            "rule_file": e.get("rule_file", ""), "rule_quote": e.get("rule_quote", ""),
            "file": e.get("file"), "category": e.get("category"), "summary": e.get("summary"), "recorded_line": e.get("line"),
            "failure_scenario": e.get("failure_scenario", ""), "confidence": e.get("confidence"),
            "root_cause": e.get("root_cause"), "context_hash": e.get("context_hash", ""),
            "code_line": e.get("code_line", ""), "same_code": bool(where), "line_now": where[0] if where else None, "id": e.get("id"),
        })
    crlib.save_json(out, result)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("feedback: 用法 feedback.sh history REVIEW_DIR OUT.json")
    history(sys.argv[1], sys.argv[2])
