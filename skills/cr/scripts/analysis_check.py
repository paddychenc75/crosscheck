"""校验 analyst 的回复,写出 analysis.json 供 MR 说明使用。

用法: python3 analysis_check.py DIR     读 DIR/analyst.json(analyst 的原样回复)和 analysis-facts.json

MR 说明不经 verifier,所以 analyst 写的每一句都要能对上材料:
- 一句话和每组的目的必须带 quote,quote 必须是需求材料(intent.md、读到的需求单和需求文档、MR 描述)
  里的原文片段(空白归一后是子串,至少 6 个字),对不上就删掉这句;
- 分组只能用 analysis-facts.json 里的代码块编号(H1…),不存在的删掉,同一个代码块只算第一组,
  没分到的进「其他」;组的顺序由脚本按 接口和类型 → 实现 → 配置 → 测试 重排;
- 不许出现结论性的词(已实现、无风险、建议……),出现就删掉那一句;
- 需求对应位置里的验收标准必须是 intent.md 里列出的那几条。
输出 STATUS: ok | partial(删过东西)| failed(没有可用内容,MR 说明只用脚本的事实)。
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True
import crlib

BANNED = re.compile(r"已实现|已覆盖|覆盖了|无风险|没有风险|有风险|风险较|没问题|没有问题|是正确的|正确地|建议|可能出错|"
                    r"会出错|行为不变|行为变化|行为变了|不影响|bug|缺陷|漏洞", re.I)
MAX_GROUPS = 6


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


def rank(hunk, facts):
    kind = hunk.get("kind", "other")
    if kind == "generated":
        return 4
    if kind == "test":
        return 3
    if kind == "config":
        return 2
    contract = any(s.get("hunk") == hunk["id"] and (s.get("change") == "signature_changed" or s.get("kind") in ("type", "class"))
                   for s in facts.get("symbols", []))
    return 0 if contract else 1


def check(d):
    facts = crlib.load_json(os.path.join(d, "analysis-facts.json"), {})
    raw = crlib.load_json(os.path.join(d, "analyst.json"), {})
    dropped = []
    if not raw:
        result = {"status": "failed", "summary": None, "groups": [], "requirements": [],
                  "dropped": [{"what": "analyst.json", "why": "缺失或不是 JSON 对象"}]}
        crlib.save_json(os.path.join(d, "analysis.json"), result)
        return result
    text = corpus(d)
    hunks = {h["id"]: h for h in facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}

    def quoted(item, what, limit):
        if not isinstance(item, dict):
            dropped.append({"what": what, "why": "格式不对"})
            return None
        body = str(item.get("text") or item.get("purpose") or "").strip()
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

    groups, used = [], set()
    for i, g in enumerate(raw.get("groups") or []):
        if not isinstance(g, dict):
            continue
        title = str(g.get("title") or "").strip()[:20] or "第 %d 组" % (i + 1)
        if BANNED.search(title):
            dropped.append({"what": "组标题 %s" % title, "why": "含结论性用语"})
            title = "第 %d 组" % (i + 1)
        ids = []
        for h in g.get("hunks") or []:
            if h not in hunks:
                dropped.append({"what": "组「%s」的 %s" % (title, h), "why": "没有这个代码块"})
            elif h not in used:
                ids.append(h)
                used.add(h)
        if not ids:
            continue
        purpose = quoted({"text": g.get("purpose"), "quote": g.get("quote"), "source": g.get("source")}, "组「%s」的目的" % title, 60) \
            if g.get("purpose") else None
        groups.append({"title": title, "purpose": purpose, "hunks": ids})
    rest = [h for h in hunks if h not in used]
    if len(groups) > MAX_GROUPS:
        for g in groups[MAX_GROUPS - 1:]:
            rest = g["hunks"] + rest
        dropped.append({"what": "分组", "why": "超过 %d 组,多的并进「其他」" % MAX_GROUPS})
        groups = groups[:MAX_GROUPS - 1]
    if rest and hunks:
        groups.append({"title": "其他", "purpose": None, "hunks": rest})
    for g in groups:
        g["hunks"].sort(key=lambda h: (rank(hunks[h], facts), int(h[1:]) if h[1:].isdigit() else 0))
        g["files"] = list(dict.fromkeys(hunks[h]["file"] for h in g["hunks"]))
        g["rank"] = min(rank(hunks[h], facts) for h in g["hunks"])
    groups.sort(key=lambda g: (g["title"] == "其他", g["rank"]))

    criteria = acceptance(d)
    reqs = []
    for r in raw.get("requirements") or []:
        if not isinstance(r, dict):
            continue
        c = str(r.get("criterion") or "").strip()
        match = next((x for x in criteria if compact(c) and (compact(c) in compact(x) or compact(x) in compact(c))), None)
        if not match:
            dropped.append({"what": "需求对应:%s" % c[:30], "why": "不是 intent.md 里列出的验收标准"})
            continue
        ids = [h for h in r.get("hunks") or [] if h in hunks]
        if not ids:
            dropped.append({"what": "需求对应:%s" % c[:30], "why": "没有有效的代码块"})
            continue
        reqs.append({"criterion": match, "hunks": ids, "files": list(dict.fromkeys(hunks[h]["file"] for h in ids))})

    status = "ok" if not dropped else "partial"
    if not summary and not any(g["title"] != "其他" for g in groups) and not reqs:
        status = "failed"
    result = {"status": status, "summary": summary, "groups": groups, "requirements": reqs, "dropped": dropped}
    crlib.save_json(os.path.join(d, "analysis.json"), result)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: analysis_check.py DIR")
    r = check(sys.argv[1])
    print("STATUS: %s" % r["status"])
    for x in r["dropped"]:
        print("DROPPED: %s — %s" % (x["what"], x["why"]))
