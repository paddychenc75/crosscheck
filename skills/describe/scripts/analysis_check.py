"""校验 analyst 的回复,写出 analysis.json 供 PR/MR 描述使用。

用法: python3 analysis_check.py DIR     读 DIR/analyst.json(analyst 的原样回复)和 describe-facts.json

代码块多、分批起草时,每批的回复写成 analyst-<批次>.json(如 analyst-B1.json),这里按文件名顺序合并:
一句话取第一份里有的,需求对照按验收标准合并代码块,改动组依次接上(标题和一句话都相同的算重复),
验证条目的 change 换成合并后的序号,同一句的只留一条。

描述不经 verifier,所以 analyst 写的每一句都要能对上材料,有一项不过就整条删掉(输出 DROPPED:):
- 一句话必须带 quote,quote 必须是需求材料(intent.md、读到的需求单和需求文档、PR/MR 描述或提交说明)
  里的原文片段(空白归一后是子串,至少 6 个字);
- 不许出现结论性的词(已实现、无风险、建议……);
- 只含多语言文案文件(.strings、lproj、strings.xml)的组不收,由脚本汇总;一句话里反引号标出的代码名最多 4 个;
- 链接(links)必须是网页链接,并且原样出现在需求材料里,类别只有 doc(需求文档)和 design(设计稿);
- 需求对照里的验收标准必须是 intent.md 里列出的那几条,代码块只能用 describe-facts.json 里的编号(H1…);
- 改了什么(changes)和怎么验证(verify):代码块编号都要存在;每个锚点至少 3 个字符,必须原样出现在所引代码块的
  改动行(+/−)里,verify 的锚点也可以只在被描述的这一版代码里(只读的 git grep -wF);一句话里反引号标出的词都必须是锚点;
  verify 要写成「入口 → 能看到什么」,不写改动前后对比,change 必须是存在的改动组序号,每组只留一条。
输出 STATUS: ok | partial(删过东西)| failed(没有可用内容,描述只用脚本的事实)。
"""
import glob
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "review", "scripts"))
sys.path.insert(0, HERE)
import crlib  # noqa: E402
import describe_facts  # noqa: E402

BANNED = re.compile(r"已实现|已覆盖|覆盖了|无风险|没有风险|有风险|风险较|没问题|没有问题|是正确的|正确地|建议|可能出错|"
                    r"会出错|行为不变|行为变化|行为变了|不影响|bug|缺陷|漏洞", re.I)
BEFORE_AFTER = re.compile(r"改为|改成|变为|变成|换成|替换为|原来|原先|之前|以前|不再")
TICK = re.compile(r"`([^`\n]+)`")
ANCHOR_MIN = 3
LINK_KINDS = ("doc", "design")   # 从需求单里找的需求文档、设计稿链接
CHANGE_LIMIT = 60    # 改动组的一句话,反引号不计
MAX_NAMES = 4        # 一句话里最多几个反引号标出的代码名(提示词要求 2 个,这里是硬上限)
VERIFY_LIMIT = 48    # 验证一行约 40 字,反引号不计


def compact(s):
    """去掉所有空白,用于核对引文是不是原文的子串。"""
    return re.sub(r"\s+", "", str(s or ""))


def plain_len(text):
    return len(TICK.sub(r"\1", text))


def one_line(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def corpus(d, compacted=True):
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
    text = "\n".join(parts)
    return compact(text) if compacted else text


def acceptance(d):
    try:
        with open(os.path.join(d, "intent.md"), encoding="utf-8") as f:
            text = f.read()
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


def replies(d):
    """analyst.json 和分批的 analyst-<批次>.json,按文件名顺序。"""
    one = os.path.join(d, "analyst.json")
    return ([one] if os.path.isfile(one) else []) + sorted(glob.glob(os.path.join(glob.escape(d), "analyst-*.json")))


def merge(raws):
    """把几份回复合成一份,结构同单份回复。"""
    if len(raws) == 1:
        return raws[0]
    out = {"summary": next((r["summary"] for r in raws if r.get("summary")), None), "requirements": [], "changes": [], "verify": [],
           "links": [x for r in raws for x in r.get("links") or []]}
    reqs, seen_change, seen_verify = {}, {}, set()
    for r in raws:
        for x in r.get("requirements") or []:
            if isinstance(x, dict):
                c = str(x.get("criterion") or "").strip()
                if c not in reqs:
                    reqs[c] = {"criterion": c, "hunks": []}
                    out["requirements"].append(reqs[c])
                reqs[c]["hunks"] += [h for h in x.get("hunks") or [] if h not in reqs[c]["hunks"]]
        index = {}
        for i, c in enumerate(r.get("changes") or [], 1):
            key = (one_line(c.get("title")), one_line(c.get("text"))) if isinstance(c, dict) else (i, id(c))
            if key not in seen_change:
                out["changes"].append(c)
                seen_change[key] = len(out["changes"])
            index[i] = seen_change[key]
        for v in r.get("verify") or []:
            if not isinstance(v, dict) or one_line(v.get("text")) in seen_verify:
                continue
            seen_verify.add(one_line(v.get("text")))
            out["verify"].append(dict(v, change=index.get(v.get("change")) if isinstance(v.get("change"), int) else None))
    return out


class Head:
    """只读查被描述的那一版代码里有没有这个词(git grep -wF),结果缓存。"""

    def __init__(self, d):
        meta = crlib.read_kv(os.path.join(d, "meta.txt"))
        self.root, self.ref = meta.get("repo_root", ""), meta.get("code_ref", "")
        self.cache = {}

    def has(self, word):
        if word not in self.cache:
            self.cache[word] = self._grep(word)
        return self.cache[word]

    def _grep(self, word):
        if not self.root or not os.path.isdir(self.root) or self.ref in ("", "UNAVAILABLE"):
            return False
        args = ["git", "-C", self.root, "grep", "-q", "-I", "-w", "-F", "-e", word]
        args += ["--untracked"] if self.ref == "WORKTREE" else [self.ref, "--"]
        try:
            return subprocess.run(args, capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


def check(d):
    facts = crlib.load_json(os.path.join(d, "describe-facts.json"), {}) or {}
    raws = [crlib.load_json(p, {}) for p in replies(d)]
    raws = [r for r in raws if isinstance(r, dict) and r]
    dropped = []
    if not raws:
        result = {"status": "failed", "summary": None, "requirements": [], "changes": [], "verify": [], "links": [],
                  "dropped": [{"what": "analyst.json", "why": "缺失或不是 JSON 对象"}]}
        crlib.save_json(os.path.join(d, "analysis.json"), result)
        return result
    raw = merge(raws)
    material = corpus(d, compacted=False)
    text = compact(material)
    urls = set(re.findall(r'https?://[^\s<>\[\]()"\x27`]+', material))
    hunks = {h["id"]: h for h in facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}
    changed = describe_facts.hunk_lines(d)
    head = Head(d)

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

    def grounded(item, what, limit, in_head):
        """改动组和验证条目共用的核对;通过返回整理好的条目,不通过记下原因返回 None。"""
        if not isinstance(item, dict):
            dropped.append({"what": what, "why": "格式不对"})
            return None
        body, title = one_line(item.get("text")), one_line(item.get("title"))
        ids = [str(h) for h in item.get("hunks") or []] if isinstance(item.get("hunks"), list) else []
        anchors = [str(a) for a in item.get("anchors") or []] if isinstance(item.get("anchors"), list) else []
        why = None
        if not body:
            why = "没有一句话"
        elif plain_len(body) > limit:
            why = "超过 %d 字" % limit
        elif limit == CHANGE_LIMIT and len(TICK.findall(body)) > MAX_NAMES:
            why = "反引号里的代码名超过 %d 个" % MAX_NAMES
        elif BANNED.search(body + title):
            why = "含结论性用语:" + BANNED.search(body + title).group(0)
        elif not ids or any(h not in hunks for h in ids):
            why = "代码块编号不存在:%s" % "、".join(h for h in ids if h not in hunks) if ids else "没有代码块编号"
        elif not anchors:
            why = "没有锚点"
        if why is None:
            lines = [x for h in ids for x in (changed.get(h) or {}).get("lines", [])]
            for a in anchors:
                if len(a) < ANCHOR_MIN:
                    why = "锚点 %s 不到 %d 个字符" % (a, ANCHOR_MIN)
                elif not any(a in x for x in lines) and not (in_head and head.has(a)):
                    why = "锚点 %s 不在所引代码块的改动行里" % a + ("" if not in_head else ",这一版代码里也没有")
                if why:
                    break
        if why is None:
            extra = [w for w in TICK.findall(body) if w not in anchors]
            if extra:
                why = "反引号里的 %s 不是锚点" % "、".join(extra)
        if why:
            dropped.append({"what": what, "why": why})
            return None
        return {"title": title, "text": body, "hunks": ids, "anchors": anchors}

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

    changes, renumber = [], {}
    for i, c in enumerate(raw.get("changes") or [], 1):
        ids = [h for h in c.get("hunks") or [] if h in hunks] if isinstance(c, dict) and isinstance(c.get("hunks"), list) else []
        if ids and all(describe_facts.is_i18n(hunks[h]["file"]) for h in ids):
            continue   # 多语言文案由脚本按文件数汇总成一条,不用写进改了什么(不算被删,也不占序号)
        what = "改了什么 %d:%s" % (i, one_line(c.get("title") or c.get("text"))[:30] if isinstance(c, dict) else "")
        ok = grounded(c, what, CHANGE_LIMIT, False)
        if ok:
            changes.append(ok)
            renumber[i] = len(changes)

    verify, used = [], set()
    for i, v in enumerate(raw.get("verify") or [], 1):
        what = "怎么验证 %d:%s" % (i, one_line(v.get("text"))[:30] if isinstance(v, dict) else "")
        ok = grounded(v, what, VERIFY_LIMIT, True)
        if not ok:
            continue
        ok["text"] = ok["text"].replace("->", "→")
        ch = v.get("change")
        why = None
        if "→" not in ok["text"]:
            why = "不是「入口 → 能看到什么」的写法"
        elif BEFORE_AFTER.search(ok["text"]):
            why = "写了改动前后对比:" + BEFORE_AFTER.search(ok["text"]).group(0)
        elif not isinstance(ch, int) or isinstance(ch, bool) or ch not in renumber:
            why = "对应的改动组 %s 不存在" % ch
        elif renumber[ch] in used:
            why = "第 %d 组已经有一条验证" % ch
        if why:
            dropped.append({"what": what, "why": why})
            continue
        used.add(renumber[ch])
        verify.append(dict(ok, change=renumber[ch]))

    links = []
    for x in raw.get("links") or []:
        if not isinstance(x, dict):
            continue
        kind, url = x.get("kind"), str(x.get("url") or "").strip()
        why = None
        if kind not in LINK_KINDS:
            why = "类别只能是 doc(需求文档)或 design(设计稿)"
        elif not re.match(r"https?://\S+$", url):
            why = "不是网页链接"
        elif url not in urls:
            why = "链接不在需求材料原文里"
        if why:
            dropped.append({"what": "链接:%s" % url[:40], "why": why})
        elif all(url != y["url"] for y in links):
            links.append({"kind": kind, "url": url, "label": one_line(x.get("label"))[:40]})

    status = "ok" if not dropped else "partial"
    if not summary and not reqs and not changes and not verify and not links:
        status = "failed"
    result = {"status": status, "summary": summary, "requirements": reqs, "changes": changes, "verify": verify, "links": links,
              "dropped": dropped}
    crlib.save_json(os.path.join(d, "analysis.json"), result)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: analysis_check.py DIR")
    r = check(sys.argv[1])
    print("STATUS: %s" % r["status"])
    for x in r["dropped"]:
        print("DROPPED: %s — %s" % (x["what"], x["why"]))
