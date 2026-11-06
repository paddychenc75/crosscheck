# rule_candidates.py — 历史归纳的内部候选格式:mined-candidates.md,不直接用于仓库写入。
# 只被本目录下的脚本 import,不单独运行。字段名、动作、去向、证据区和来源注释的写法都以这里为准。

import datetime
import hashlib
import json
import os
import re

import crlib

FILE = "mined-candidates.md"    # 内部材料,与 candidates.json 按指纹冻结配对;人读总草稿是 rules-draft.md
TITLE = "# 候选规则"
FIELDS = ("候选", "去向", "动作", "复核", "规则", "原因", "严重级别", "情形", "范围", "反例", "正例", "不要报")
# 各类候选必填的字段;suppress 类不用严重级别、情形、反例、正例、不要报。「复核」只在写入时必填,由 rules-write.py 查
REQUIRED = {"rule": ("候选", "去向", "动作", "规则", "范围", "严重级别", "情形", "不要报"),
            "suppress": ("候选", "去向", "动作", "规则", "范围")}
SUPPRESS_ONLY_FORBIDS = ("严重级别", "情形", "反例", "正例", "不要报")
EXAMPLES = ("反例", "正例")
ACTIONS = ("待定", "写入", "跳过")
# 独立读者复核(roles/editor.md)的结论:可读 = 四个问题都通过,表达上可以原样写,不是准入;改写 = 已按意见改过;案例 = 只是一次修复,退回不写
VERDICTS = ("可读", "改写", "案例")
RULES_DIR = ".agents/rules"
# 头部字段:写在头部(第一个 ## 块之前),不属于任何候选块。0.3.13 起没有可写的头部字段:
# AGENTS.md 提示和 .claude/rules 链接由 rules_draft 按规则变更自动生成,旧的「接入 AGENTS.md」报错提示删掉。
HEAD_FIELDS = ()
RETIRED_HEAD_FIELDS = {"接入 AGENTS.md": "0.3.13 起 AGENTS.md 提示和 .claude/rules 链接由脚本自动生成,删掉这一行"}

# 证据区和来源注释只放几个典型 MR(挑法见 typical),其余只给个数:候选规则文件和规则文件会给别人看,
# 证据要靠链接点回平台独立看懂。这是展示条数,不是数据上限:完整证据、判断依据仍在 candidates.json 里。
TYPICAL = 3
QUOTE_CHARS = 60          # 典型例子里评论原话最多显示的字数,超过截断并加省略号
REVIEW_CHARS = 100        # 语义复核只留第一句,最多这么多字
MR_STATE = {"accepted": "模型判断已采纳", "unknown": "未知", "mixed": "结论不一致", "rejected": "模型判断未采纳"}
STATE_RANK = {"accepted": 0, "unknown": 1, "mixed": 1, "rejected": 2}
# MR 网页地址:快照里没有现成地址时按仓库地址拼
MR_PATHS = {"github": "/pull/%s", "gitlab": "/-/merge_requests/%s"}
WEB_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")

HEADING = re.compile(r"^##\s+(.*)$")
NUMBERED = re.compile(r"^(\d+)\s*[.．、]\s*(.*)$")
FIELD = re.compile(r"^(\s*[-*]\s+\*\*)([^*]+?)(\*\*\s*[::]\s*)(.*)$")


def one(s):
    """折成一行。文字里的 HTML 注释记号换成全角破折号:live_lines 会把它当注释,吞掉后面的行。"""
    return " ".join(str(s or "").split()).replace("<!--", "<!——").replace("-->", "——>")


def example_lines(name, text, lang="", indent="  "):
    """反例 / 正例:字段行后跟一个围栏代码块,候选规则文件和规则文件同一写法。围栏比代码里最长的反引号串再长一格,代码原样保留。"""
    rows = [x.rstrip() for x in str(text or "").strip("\n").split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    if not any(rows):
        return []
    run = max((len(m) for m in re.findall(r"`+", "\n".join(rows))), default=0)
    fence = "`" * max(3, run + 1)
    return (["- **%s**:" % name, indent + fence + (lang or "").strip()]
            + [indent + x if x else "" for x in rows] + [indent + fence])


def clip(text, limit):
    """折成一行,超过 limit 个字截断并加省略号。"""
    s = one(text)
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def first_sentence(text, limit):
    """第一句(到第一个句号、问号、叹号为止),再按 limit 截断。"""
    s = one(text)
    m = re.search(r"[。!?!?]", s)
    return clip(s[:m.end()] if m else s, limit)


def web_url(url):
    """完整的 http(s) 地址原样返回;空、带空白、尖括号、括号等写进 Markdown 链接或注释会出问题的,返回空串。"""
    url = str(url or "").strip()
    return url if WEB_URL.fullmatch(url) else ""


def mr_web_url(snapshot_url, repo_url, platform, number):
    """MR 的网页地址:优先用快照里平台给的地址;没有时按仓库网页地址和平台拼(GitHub /pull/N,GitLab /-/merge_requests/N);
    拼不出返回空串,调用方退回只写 !N。不猜平台、不补协议。"""
    url = web_url(snapshot_url)
    if url:
        return url
    base = web_url(repo_url).rstrip("/")
    if base and platform in MR_PATHS and str(number).isdigit():
        return base + MR_PATHS[platform] % number
    return ""


def mr_ref(mr, url, markdown=True):
    """有网页地址时 Markdown 写 [!N](地址),注释里直接写地址(注释里的 Markdown 不渲染);没有地址时写 !N。"""
    if not url:
        return "!%s" % mr
    return "[!%s](%s)" % (mr, url) if markdown else url


def feedback_ref(e):
    """判断记录的来源写成「审查 <对象> 发现 <id>」,与 after-report.md 定规则时的写法一致;不加链接。"""
    return " ".join(x for x in ("审查", one(e.get("target")), "发现", one(e["feedback"])) if x)


def typical(c, n=TYPICAL):
    """典型 MR -> (例子, 其余 MR 编号)。例子是 [{mr, url, quote, state}],每个 MR 最多一条,最多 n 条。
    MR 的结论:各条证据的模型判断(assessment.state)都一样就取它,不一样记为 mixed(结论不一致)。
    排序:已采纳优先,其次未知 / 结论不一致,最后未采纳;同一档里编号大的(较新的)在前。MR 内取第一条证据。
    只看 candidates.json 里的字段,同一输入结果固定;其余 MR 按编号从小到大。"""
    by = {}
    for e in c.get("evidence", []):
        if e.get("mr") is not None:
            by.setdefault(e["mr"], []).append(e)
    rows = []
    for mr, es in by.items():
        states = {(e.get("assessment") or {}).get("state") or "unknown" for e in es}
        state = states.pop() if len(states) == 1 else "mixed"
        rows.append(dict(mr=mr, url=web_url(es[0].get("url")), quote=es[0].get("quote", ""), state=state))
    num = lambda mr: int(mr) if str(mr).isdigit() else 0
    rows.sort(key=lambda r: (STATE_RANK.get(r["state"], 1), -num(r["mr"]), str(r["mr"])))
    return rows[:n], sorted((r["mr"] for r in rows[n:]), key=lambda mr: (num(mr), str(mr)))


def feedback_rows(c):
    """候选里的判断记录证据,同一条只算一次,保持原顺序。"""
    seen, out = set(), []
    for e in c.get("evidence", []):
        if e.get("feedback") and e["feedback"] not in seen:
            seen.add(e["feedback"])
            out.append(e)
    return out


def source_refs(c, n=TYPICAL):
    """规则文件 / REVIEW.md 末尾来源注释里的文字:典型 MR 的网页地址(没有就写 !N),再加总数;判断记录同样只列前 n 条。"""
    picked, rest = typical(c, n)
    parts = []
    if picked:
        refs = " ".join(mr_ref(r["mr"], r["url"], markdown=False) for r in picked)
        parts.append(refs + (" 等 %d 个 MR" % (len(picked) + len(rest)) if rest else "(共 %d 个 MR)" % len(picked)))
    fb = feedback_rows(c)
    if fb:
        refs = " ".join(feedback_ref(e) for e in fb[:n])
        parts.append(refs + (" 等 %d 条判断记录" % len(fb) if len(fb) > n else ""))
    return ";".join(parts)


def count_refs(c):
    """「候选」行括号里的来源个数,只给人看。"""
    fb = len(feedback_rows(c))
    return "、".join(x for x in ("%d 个 MR" % len(c.get("mrs", [])) if c.get("mrs") else "",
                                  "判断记录 %d 条" % fb if fb else "") if x)


def suggest_dest(c, root):
    """rule 类去 .agents/rules/<主题>.md。suppress 类:是已有规则的例外(exception_of)时去那条规则的「不要报」字段;
    其余去 REVIEW.md 的「不要报」,范围恰好是仓库里一个目录(dir/**)时用该目录的 REVIEW.md,否则用根目录的。"""
    if c.get("kind") != "suppress":
        return "%s/%s.md" % (RULES_DIR, c.get("topic") or "general")
    of = c.get("exception_of")
    if of:
        return "%s#%s" % (of["file"], one(of["title"]))
    paths = c.get("paths") or []
    if len(paths) == 1:
        m = re.fullmatch(r"([^*?\[\]{}]+?)/+\*\*", paths[0])
        if m and root and os.path.isdir(os.path.join(root, m.group(1))):
            return "%s/REVIEW.md#不要报" % m.group(1)
    return "REVIEW.md#不要报"


def evidence_lines(c):
    """块末尾的证据说明,写成引用块(> 开头),parse 不读这些行,只给人看。第一行是统计;随后是至多 TYPICAL 个典型 MR,
    每个一行(链接、截断的评论原话、采纳结论),其余 MR 只列编号;判断记录同样至多 TYPICAL 条。
    判断依据长文和逐条评论不写进来(仍在 candidates.json 里),语义复核只留第一句。这份文件会给别人看,不写指向本机文件的话。"""
    mrs = len(c["mrs"])
    fb = c["mentions"] - mrs                      # 判断记录一律算接受
    accepted = c["support"] - fb
    head = []
    if mrs:
        head.append("提及 %d 个 MR:模型判断已采纳 %d,未知/结论不一致 %d,未采纳 %d" % (mrs, accepted, c["unknown"], mrs - accepted - c["unknown"]))
    if fb:
        head.append("判断记录 %d 条" % fb)
    out = ["", "> " + ";".join(head)]
    picked, rest = typical(c)
    for r in picked:
        out.append("> - %s「%s」[%s]" % (mr_ref(r["mr"], r["url"]), clip(r["quote"], QUOTE_CHARS), MR_STATE[r["state"]]))
    if rest:
        out.append("> 另有 %d 个 MR:%s" % (len(rest), " ".join("!%s" % mr for mr in rest)))
    fb = feedback_rows(c)
    for e in fb[:TYPICAL]:
        out.append("> - %s「%s」[%s]" % (feedback_ref(e), clip(e["quote"], QUOTE_CHARS), e["state"]))
    if len(fb) > TYPICAL:
        out.append("> 另有 %d 条判断记录" % (len(fb) - TYPICAL))
    code = []
    for evidence in c.get("evidence", []):
        for source in evidence.get("assessment", {}).get("code", []):
            if source not in code:
                code.append(source)
    for source in code[:TYPICAL]:
        out.append("> 落实依据:%s:%s @ %s[%s]「%s」" % (
            one(source["file"]), source["line"], source["ref"],
            "已核对原文" if source.get("status") == "verified" else "版本不可读",
            clip(source["quote"], QUOTE_CHARS)))
    if len(code) > TYPICAL:
        out.append("> 另有 %d 份最终代码依据" % (len(code) - TYPICAL))
    practice = c.get("practice") or {}
    if practice:
        out.append("> 仓库惯例(不计入 MR 采纳数):" + first_sentence(practice["reason"], REVIEW_CHARS))
        out.append("> 核对范围:" + ", ".join(practice["scope"]))
        for key, label in (("examples", "惯例"), ("counterexamples", "相反实现")):
            rows = practice.get(key, [])
            for source in rows[:TYPICAL]:
                out.append("> %s:%s:%s @ %s[%s]「%s」" % (
                    label, one(source["file"]), source["line"], source["ref"],
                    "已核对原文" if source.get("status") == "verified" else "版本不可读",
                    clip(source["quote"], QUOTE_CHARS)))
            if len(rows) > TYPICAL:
                out.append("> 另有 %d 份%s依据" % (len(rows) - TYPICAL, label))
    if c.get("review"):
        out.append("> 语义复核(模型判断):" + first_sentence(c["review"], REVIEW_CHARS))
    if c.get("severity_reason"):
        out.append("> 严重级别依据(模型判断):" + one(c["severity_reason"]))
    if c.get("next_change"):
        # 前推测试的答案:和情形不同、按这条也该判的改动及其位置;只展示,不是可编辑字段
        out.append("> 下一次(前推测试):" + one(c["next_change"]))
    if c["kind"] == "suppress":
        of = c.get("exception_of")
        out.append("> 不要报的类别(模型判断):" + ("已有规则的例外,%s「%s」" % (one(of["file"]), one(of["title"])) if of else "通用直觉"))
    coverage = c.get("coverage", {})
    if coverage:
        out.append("> 已有约束(%s):%s" % (coverage["status"], one(coverage["reason"])))
        for source in coverage.get("sources", []):
            out.append("> - %s「%s」" % (one(source["file"]), clip(source["quote"], QUOTE_CHARS)))
    return out


def render_block(c, dest):
    lines = ["## %d. %s" % (c["no"], one(c.get("title")) or one(c["rule"])[:24]), ""]
    if c["unmet"]:
        lines += ["> " + ";".join(c["unmet"]), ""]
    lines += ["- **候选**:%d(%s;评审人 %d 位)" % (c["no"], count_refs(c) or "-", c.get("people", 0)),
             "- **去向**:" + dest,
             "- **动作**:待定",
             "- **复核**:",
             "- **规则**:" + one(c["rule"]).rstrip(crlib.RULE_PUNCT)]
    if c.get("why"):
        lines.append("- **原因**:" + one(c["why"]))
    if c["kind"] == "rule":
        lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
        lines.append("- **情形**:" + one(c.get("incident")))
    lines.append("- **范围**:" + ", ".join(c.get("paths") or ["**"]))
    if c["kind"] == "rule":
        for name, key in (("反例", "bad_example"), ("正例", "good_example")):
            lines += example_lines(name, c.get(key), c.get("lang", ""))
        lines.append("- **不要报**:" + one(c.get("exceptions")))
    return lines + evidence_lines(c)


def fingerprint(cands):
    return hashlib.sha256(json.dumps(cands, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bound(text, cands):
    return "<!-- candidates-sha256: %s -->" % fingerprint(cands) in text


def render(cands, root, meta):
    project = os.path.basename((meta.get("repo_root") or root or "").rstrip("/")) or meta.get("repo_id", "")
    lines = [TITLE, "",
             "- 项目:" + project,
             "- 扫描:最近 %s 天,%s 个已合并 MR(这是扫描量;每条候选的支持数见各块「候选」行)" % (meta.get("days", "?"), meta.get("mrs", "?")),
             "- 生成:" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")]
    lines += ["",
              "<!-- candidates-sha256: %s -->" % fingerprint(cands),
              "<!-- 逐条把「动作」改成「写入」或「跳过」,「待定」不会写。每块末尾 > 开头的引用块是证据,只给人看,改它不影响写入。"]
    lines.append("改完用 rules-write.py <work_dir> --prepare 编译到 rules-draft.md;最终预演和确认写入只用 rules-draft.py。-->")
    for c in cands:
        lines += [""] + render_block(c, suggest_dest(c, root))
    return "\n".join(lines) + "\n"


def live_lines(text):
    """逐行去掉 HTML 注释(可跨行),返回 [(行号, 去注释后的文字)];最后一个值表示文件末尾还有没闭合的注释。
    围栏代码块里的行原样保留:示例代码里的 <!-- 不是注释。"""
    out, in_comment = [], False
    rows = text.split("\n")
    fenced = crlib.fence_mask(rows)
    for i, raw in enumerate(rows):
        line = raw
        if fenced[i] and not in_comment:
            out.append((i, line))
            continue
        if in_comment:
            if "-->" not in line:
                out.append((i, ""))
                continue
            line = line.split("-->", 1)[1]
            in_comment = False
        line = re.sub(r"<!--.*?-->", "", line)
        if "<!--" in line:
            line = line.split("<!--", 1)[0]
            in_comment = True
        out.append((i, line))
    return out, in_comment


def parse(text):
    """-> (blocks, errors)。block: n 序号、title、head 标题行号、cand 候选编号、fields {字段: 值}、at {字段: 行号}、
    lang {字段: 代码块语言}。反例 / 正例可以是字段行后的围栏代码块(多行),也可以写在同一行。
    errors: [(块标号, 字段, 原因)]。只检查格式,内容(规则句、去向)由调用方校验。"""
    lines, open_comment = live_lines(text)
    fenced = crlib.fence_mask([x for _, x in lines])
    blocks, errors, cur, seen = [], [], None, set()
    skip_to = -1          # 已经并进字段值的代码块行

    def code_after(k):
        """第 k 行是值为空的字段行时,取紧跟的围栏代码块 -> (代码, 语言, 结束下标);没有代码块返回 None。"""
        j = k + 1
        m = crlib.FENCE.match(lines[j][1]) if j < len(lines) else None
        if not m or not fenced[j]:
            return None
        token, indent, lang = m.group(1), lines[j][1][:m.start(1)], lines[j][1][m.end(1):].strip()
        body = []
        for t in range(j + 1, len(lines)):
            row = lines[t][1]
            end = crlib.FENCE.match(row)
            if end and end.group(1)[0] == token[0] and len(end.group(1)) >= len(token):
                return "\n".join(body), lang, t
            body.append(row[len(indent):] if row.startswith(indent) else (row.lstrip() if row.strip() else ""))
        return "\n".join(body), lang, -1

    def close():
        nonlocal cur
        if cur is None:
            return
        cand = re.match(r"\s*(\d+)", cur["fields"].get("候选", ""))
        if "候选" not in cur["fields"]:
            errors.append((cur["label"], "候选", "缺少「候选」字段:不能新增没有候选编号的块"))
        elif not cand:
            errors.append((cur["label"], "候选", "应以候选编号开头,如「候选:3(…)」"))
        else:
            cur["cand"] = int(cand.group(1))
        blocks.append(cur)
        cur = None

    for k, (i, line) in enumerate(lines):
        if k <= skip_to:
            continue
        if fenced[k]:
            errors.append(((cur or {}).get("label", "-"), "格式", "代码块只能紧跟在「反例」或「正例」字段行后面"))
            skip_to = next((t for t in range(k, len(lines)) if not fenced[t]), len(lines)) - 1
            continue
        s = line.strip()
        h = HEADING.match(s)
        if h:
            close()
            m = NUMBERED.match(h.group(1).strip())
            if not m:
                errors.append((h.group(1).strip()[:20] or "?", "格式", "标题应为「## 序号. 标题」"))
                cur = dict(n=None, label=h.group(1).strip()[:20], title="", head=i, cand=None, fields={}, at={}, bad=True)
                continue
            n = int(m.group(1))
            if n in seen:
                errors.append((str(n), "格式", "序号重复"))
            seen.add(n)
            cur = dict(n=n, label=str(n), title=m.group(2).strip(), head=i, cand=None, fields={}, at={}, lang={})
            continue
        if cur is None or not s or s.startswith(">"):     # > 开头是证据引用块,不是字段
            continue
        f = FIELD.match(line)
        if not f:
            errors.append((cur["label"], "格式", "看不懂这一行(字段应写成「- **字段**:值」):" + s[:30]))
            continue
        name, value = f.group(2).strip(), f.group(4).strip()
        lang = ""
        if name in EXAMPLES and not value:
            code = code_after(k)
            if code:
                value, lang, end = code
                if end < 0:
                    errors.append((cur["label"], name, "代码块没有结束的围栏"))
                    skip_to = len(lines)
                else:
                    skip_to = end
        if name not in FIELDS:
            errors.append((cur["label"], name, "未知字段(可用:%s)" % "、".join(FIELDS)))
        elif name in cur["fields"]:
            errors.append((cur["label"], name, "字段重复"))
        else:
            cur["fields"][name] = value
            cur["at"][name] = i
            if name in EXAMPLES:
                cur["lang"][name] = lang
    close()
    if open_comment:
        errors.append((blocks[-1]["label"] if blocks else "-", "格式", "有没闭合的 <!-- 注释"))
    blocks = [b for b in blocks if not b.get("bad")]
    return blocks, errors


def head_fields(text):
    """头部(第一个 ## 块之前)的字段 -> ({字段: (值, 行号)}, errors)。errors 的块标号是 None。
    头部其余的普通列表行(项目、扫描、生成)不是字段,不管;加粗写成字段样子却不认识的报错。"""
    lines, _ = live_lines(text)
    fields, errors = {}, []
    for i, line in lines:
        if HEADING.match(line.strip()):
            break
        f = FIELD.match(line)
        if not f:
            continue
        name, value = f.group(2).strip(), f.group(4).strip()
        if name in RETIRED_HEAD_FIELDS:
            errors.append((None, name, RETIRED_HEAD_FIELDS[name]))
        elif name not in HEAD_FIELDS:
            errors.append((None, name, "未知的头部字段(%s)" % ("头部只能写:" + "、".join(HEAD_FIELDS) if HEAD_FIELDS else "头部没有可写字段")))
        elif name in fields:
            errors.append((None, name, "字段重复"))
        else:
            fields[name] = (value, i)
    return fields, errors


def parse_dest(value):
    """去向 -> (文件, # 后面的部分或 None);写法不对时抛 ValueError。只看写法,目录在不在、能不能写由调用方查。
    REVIEW.md 后面是章节,只能是「不要报」;.agents/rules/<主题>.md 后面是规则标题(这条不要报是那条规则的例外)。"""
    v = value.strip().strip("`").strip()
    path, _, section = v.partition("#")
    path, section = path.strip(), section.strip()
    if not path:
        raise ValueError("没有写文件路径")
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("应为仓库内的相对路径,不含 .. 和绝对路径")
    path = os.path.normpath(path).replace(os.sep, "/")
    if "#" in v and not section:
        raise ValueError("# 后面为空")
    if os.path.basename(path) == "REVIEW.md":
        if section != "不要报":
            raise ValueError("REVIEW.md 后要带章节 #不要报(REVIEW.md 只收不要报类)")
        return path, section
    if path.startswith(RULES_DIR + "/") and path.endswith(".md") and len(path) > len(RULES_DIR) + 4:
        return path, section or None
    raise ValueError("应为 .agents/rules/<主题>.md、.agents/rules/<主题>.md#<规则标题>,或 <目录>/REVIEW.md#不要报")


def set_action(text, block, action):
    """把某个块的「动作」改成 action,其余文字原样不动。"""
    return set_value(text, block["at"]["动作"], action)


def set_value(text, i, action):
    """把第 i 行字段的值(第一个词)改成 action,其余文字原样不动。头部字段也用它。"""
    lines = text.split("\n")
    f = FIELD.match(lines[i])
    if f:
        lines[i] = lines[i][:f.start(4)] + re.sub(r"[^<\s]+", action, lines[i][f.start(4):], count=1)
    return "\n".join(lines)
