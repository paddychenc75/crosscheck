"""保存审查记录时补全跨审查识别所需的信息。由 save-review.sh 调用。

用法: python3 review_store.py REVIEW_DIR DEST
- 给 DEST/findings.json 里每条发现记下它指向的那一行代码(code_line)和上下文哈希(context_hash),
  作为跨审查识别「同一个问题」的依据;
- 增量审查时写 DEST/transitions.json:上次的每条发现去哪了(代码改了、代码没变但这次没报、文件已不在改动里)。
"""
import os
import sys

import crlib


def fill_context(findings, src):
    for f in findings:
        if f.get("context_hash"):
            continue
        ls = src.lines(f.get("file", "")) if src else None
        try:
            n = int(f.get("line", 0))
        except (TypeError, ValueError):
            n = 0
        if ls and 1 <= n <= len(ls):
            f.setdefault("code_line", ls[n - 1].strip()[:200])
            f["context_hash"] = crlib.ctx_hash(ls, n)


def transitions(review_dir, findings, prev, src):
    status = {}
    try:
        with open(os.path.join(review_dir, "incremental.txt"), encoding="utf-8") as f:
            for line in f:
                s, p = line.rstrip("\n").split("\t", 1)
                status[p] = s
    except (OSError, ValueError):
        pass
    cur = {crlib.finding_key(f) for f in findings}
    out = []
    for p in prev:
        if crlib.finding_key(p) in cur:
            continue
        if status.get(p.get("file")) in (None, "removed"):
            t = "out_of_scope"
        elif not p.get("context_hash"):
            t = "unknown"
        else:
            ls = (src.lines(p.get("file", "")) if src else None) or []
            same = any(crlib.ctx_hash(ls, i + 1) == p["context_hash"] for i, x in enumerate(ls)
                       if crlib.norm(x) == crlib.norm(p.get("code_line", "")))
            t = "not_reported_code_unchanged" if same else "code_changed"
        out.append(dict({k: p.get(k) for k in ("id", "file", "line", "category", "severity", "confidence", "summary", "since")}, transition=t))
    return out


def main(review_dir, dest):
    meta = crlib.read_kv(os.path.join(review_dir, "meta.txt"))
    src = crlib.code_source(meta)
    fp = os.path.join(dest, "findings.json")
    findings = crlib.load_json(fp, [])
    fill_context(findings, src)
    crlib.save_json(fp, findings)
    prev = crlib.load_json(os.path.join(review_dir, "prev-findings.json"), [])
    if meta.get("review_scope") == "incremental" and prev:
        crlib.save_json(os.path.join(dest, "transitions.json"), transitions(review_dir, findings, prev, src))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
