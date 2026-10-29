"""校验 analyst 的回复,写出 analysis.json 供 PR/MR 描述使用。

用法: python3 analysis_check.py DIR     读 DIR/analyst.json(analyst 的原样回复)和 describe-facts.json

描述不经 verifier,所以 analyst 写的每一句都要能对上材料:
- 一句话必须带 quote,quote 必须是需求材料(intent.md、读到的需求单和需求文档、PR/MR 描述或提交说明)
  里的原文片段(空白归一后是子串,至少 6 个字),对不上就删掉这句;
- 不许出现结论性的词(已实现、无风险、建议……),出现就删掉那一句;
- 需求对照里的验收标准必须是 intent.md 里列出的那几条,代码块只能用 describe-facts.json 里的编号(H1…)。
输出 STATUS: ok | partial(删过东西)| failed(没有可用内容,描述只用脚本的事实)。
"""
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "review", "scripts"))
import crlib  # noqa: E402

BANNED = re.compile(r"已实现|已覆盖|覆盖了|无风险|没有风险|有风险|风险较|没问题|没有问题|是正确的|正确地|建议|可能出错|"
                    r"会出错|行为不变|行为变化|行为变了|不影响|bug|缺陷|漏洞", re.I)


def compact(s):
    """去掉所有空白,用于核对引文是不是原文的子串。"""
    return re.sub(r"\s+", "", str(s or ""))


def corpus(d):
    parts = []
    for name in ("intent.md", "description.md"):
        try:
            parts.append(open(os.path.join(d, name), encoding="utf-8", errors="replace").read())
        except OSError:
            pass
    meta = crlib.read_kv(os.path.join(d, "meta.txt"))
    sd = meta.get("source_dir", "")
    if sd and os.path.isdir(sd):
        for name in sorted(os.listdir(sd)):
            try:
                parts.append(open(os.path.join(sd, name), encoding="utf-8", errors="replace").read())
            except OSError:
                pass
    return compact("\n".join(parts))


def acceptance(d):
    try:
        text = open(os.path.join(d, "intent.md"), encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"## 验收标准\n\n(.*?)(\n## |\Z)", text, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).split("\n"):
        mm = re.match(r"\d+\.\s*(.+?)(?:(来自 [^)]*))?$", line.strip())
        if mm:
            out.append(mm.group(1).strip())
    return out


def check(d):
    facts = crlib.load_json(os.path.join(d, "describe-facts.json"), {})
    raw = crlib.load_json(os.path.join(d, "analyst.json"), {})
    dropped = []
    if not isinstance(raw, dict) or not raw:
        result = {"status": "failed", "summary": None, "requirements": [],
                  "dropped": [{"what": "analyst.json", "why": "缺失或不是 JSON 对象"}]}
        crlib.save_json(os.path.join(d, "analysis.json"), result)
        return result
    text = corpus(d)
    hunks = {h["id"]: h for h in facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}

    def quoted(item, what, limit):
        if not isinstance(item, dict):
            dropped.append({"what": what, "why": "格式不对"})
            return None
        body = str(item.get("text") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not body:
            return None
        if len(body) > limit:
            dropped.append({"what": what, "why": "超过 %d 字" % limit})
            return None
        if BANNED.search(body):
            dropped.append({"what": what, "why": "含结论性用语:" + BANNED.search(body).group(0)})
            return None
        if len(compact(quote)) < 6 or compact(quote) not in text:
            dropped.append({"what": what, "why": "引文在需求材料里找不到"})
            return None
        return {"text": body, "quote": quote, "source": str(item.get("source") or "")}

    summary = quoted(raw.get("summary"), "一句话", 60) if raw.get("summary") else None

    criteria = acceptance(d)
    reqs = []
    for r in raw.get("requirements") or []:
        if not isinstance(r, dict):
            continue
        c = str(r.get("criterion") or "").strip()
        match = next((x for x in criteria if compact(c) and (compact(c) in compact(x) or compact(x) in compact(c))), None)
        if not match:
            dropped.append({"what": "需求对照:%s" % c[:30], "why": "不是 intent.md 里列出的验收标准"})
            continue
        ids = [h for h in r.get("hunks") or [] if h in hunks]
        if not ids:
            dropped.append({"what": "需求对照:%s" % c[:30], "why": "没有有效的代码块"})
            continue
        reqs.append({"criterion": match, "hunks": ids, "files": list(dict.fromkeys(hunks[h]["file"] for h in ids))})

    status = "ok" if not dropped else "partial"
    if not summary and not reqs:
        status = "failed"
    result = {"status": status, "summary": summary, "requirements": reqs, "dropped": dropped}
    crlib.save_json(os.path.join(d, "analysis.json"), result)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: analysis_check.py DIR")
    r = check(sys.argv[1])
    print("STATUS: %s" % r["status"])
    for x in r["dropped"]:
        print("DROPPED: %s — %s" % (x["what"], x["why"]))
