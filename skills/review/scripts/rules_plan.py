# rules_plan.py — 规则沉淀计划 plan.md 的格式:mine-check.py 生成,rules-write.py 解析。
# 只被本目录下的脚本 import,不单独运行。字段名、动作、去向的写法都以这里为准。

import datetime
import hashlib
import json
import os
import re

import crlib

TITLE = "# 规则沉淀计划"
FIELDS = ("候选", "去向", "动作", "规则", "原因", "严重级别", "范围", "反例", "正例", "不要报")
# 各类候选必填的字段;suppress 类不用严重级别、反例、正例、不要报
REQUIRED = {"rule": ("候选", "去向", "动作", "规则", "范围", "严重级别", "不要报"),
            "suppress": ("候选", "去向", "动作", "规则", "范围")}
SUPPRESS_ONLY_FORBIDS = ("严重级别", "反例", "正例", "不要报")
EXAMPLES = ("反例", "正例")
ACTIONS = ("待定", "写入", "跳过", "已写入")
SECTIONS = ("每次都要检查", "不要报")
RULES_DIR = ".agents/rules"

HEADING = re.compile(r"^##\s+(.*)$")
NUMBERED = re.compile(r"^(\d+)\s*[.．、]\s*(.*)$")
FIELD = re.compile(r"^(\s*[-*]\s+\*\*)([^*]+?)(\*\*\s*[::]\s*)(.*)$")


def one(s):
    """折成一行。文字里的 HTML 注释记号换成全角破折号:live_lines 会把它当注释,吞掉后面的行。"""
    return " ".join(str(s or "").split()).replace("<!--", "<!——").replace("-->", "——>")


def example_lines(name, text, lang="", indent="  "):
    """反例 / 正例:字段行后跟一个围栏代码块,plan.md 和规则文件同一写法。围栏比代码里最长的反引号串再长一格,代码原样保留。"""
    rows = [x.rstrip() for x in str(text or "").strip("\n").split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    if not any(rows):
        return []
    run = max((len(m) for m in re.findall(r"`+", "\n".join(rows))), default=0)
    fence = "`" * max(3, run + 1)
    return (["- **%s**:" % name, indent + fence + (lang or "").strip()]
            + [indent + x if x else "" for x in rows] + [indent + fence])


def refs(c):
    """候选的来源:MR 编号;判断记录写成「审查 <对象> 发现 <id>」,与 after-report.md 定规则时的写法一致。"""
    out = ["!%s" % m for m in c.get("mrs", [])]
    for e in c.get("evidence", []):
        if e.get("feedback"):
            out.append(" ".join(x for x in ("审查", one(e.get("target")), "发现", one(e["feedback"])) if x))
    return list(dict.fromkeys(out))


def suggest_dest(c, root):
    """rule 类去 .agents/rules;suppress 类范围恰好是仓库里一个目录(dir/**)时去该目录的 REVIEW.md,其余去 .agents/rules。"""
    paths = c.get("paths") or []
    if c.get("kind") == "suppress" and len(paths) == 1:
        m = re.fullmatch(r"([^*?\[\]{}]+?)/+\*\*", paths[0])
        if m and root and os.path.isdir(os.path.join(root, m.group(1))):
            return "%s/REVIEW.md#不要报" % m.group(1)
    return "%s/%s.md" % (RULES_DIR, c.get("topic") or "general")


def evidence_lines(c):
    """块末尾的证据说明,写成引用块(> 开头):提及与模型采纳判断、逐条原文、复核来源。parse 不读这些行,只给人看。"""
    mrs = len(c["mrs"])
    fb = c["mentions"] - mrs                      # 判断记录一律算接受
    accepted = c["support"] - fb
    head = []
    if mrs:
        head.append("提及 %d 个 MR:模型判断已采纳 %d,未知/结论不一致 %d,未采纳 %d" % (mrs, accepted, c["unknown"], mrs - accepted - c["unknown"]))
    if fb:
        head.append("判断记录 %d 条" % fb)
    out = ["", "> " + ";".join(head)]
    for e in c["evidence"]:
        who = " ".join(x for x in ("!%s" % e["mr"], one(e.get("author"))) if x) if e.get("mr") else "判断记录 " + one(e["feedback"])
        out.append("> - %s「%s」[%s]" % (who, one(e["quote"]), e["state"]))
        assessment = e.get("assessment", {})
        if assessment:
            out.append(">   判断依据:" + one(assessment["reason"]))
            for b in assessment.get("basis", []):
                out.append(">   !%s 评论 %s「%s」" % (b["mr"], b["note"], one(b["quote"])))
    if c.get("review"):
        out.append("> 语义复核(模型判断):" + one(c["review"]))
    if c.get("severity_reason"):
        out.append("> 严重级别依据(模型判断):" + one(c["severity_reason"]))
    coverage = c.get("coverage", {})
    if coverage:
        out.append("> 已有约束(%s):%s" % (coverage["status"], one(coverage["reason"])))
        for source in coverage.get("sources", []):
            out.append("> - %s「%s」" % (one(source["file"]), one(source["quote"])))
    return out


def render_block(c, dest):
    lines = ["## %d. %s" % (c["no"], one(c.get("title")) or one(c["rule"])[:24]), ""]
    if c["unmet"]:
        lines += ["> " + ";".join(c["unmet"]), ""]
    lines += ["- **候选**:%d(%s;评审人 %d 位)" % (c["no"], " ".join(refs(c)) or "-", c.get("people", 0)),
             "- **去向**:" + dest,
             "- **动作**:待定",
             "- **规则**:" + one(c["rule"]).rstrip(crlib.RULE_PUNCT)]
    if c.get("why"):
        lines.append("- **原因**:" + one(c["why"]))
    if c["kind"] == "rule":
        lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
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
             "- 生成:" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "",
             "<!-- candidates-sha256: %s -->" % fingerprint(cands),
             "<!-- 逐条把「动作」改成「写入」或「跳过」,「待定」不会写。每块末尾 > 开头的引用块是证据,只给人看,改它不影响写入。",
             "改完用 rules-write.py <work_dir> --check 预演,没问题再 --apply。-->"]
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


def parse_dest(value):
    """去向 -> (文件, 章节或 None);写法不对时抛 ValueError。只看写法,目录在不在、能不能写由调用方查。"""
    v = value.strip().strip("`").strip()
    path, _, section = v.partition("#")
    path, section = path.strip(), section.strip()
    if not path:
        raise ValueError("没有写文件路径")
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("应为仓库内的相对路径,不含 .. 和绝对路径")
    path = os.path.normpath(path).replace(os.sep, "/")
    if "#" in v and not section:
        raise ValueError("章节名为空")
    if os.path.basename(path) == "REVIEW.md":
        if section not in SECTIONS:
            raise ValueError("REVIEW.md 后要带章节:#每次都要检查 或 #不要报")
        return path, section
    if path.startswith(RULES_DIR + "/") and path.endswith(".md") and len(path) > len(RULES_DIR) + 4:
        if section and section != "不要报":
            raise ValueError(".agents/rules 的文件只能带 #不要报(不要报类候选)或不带章节")
        return path, section or None
    raise ValueError("应为 .agents/rules/<主题>.md,或 <目录>/REVIEW.md#每次都要检查 / #不要报")


def set_action(text, block, action):
    """把某个块的「动作」改成 action,其余文字原样不动。"""
    lines = text.split("\n")
    i = block["at"]["动作"]
    f = FIELD.match(lines[i])
    if f:
        lines[i] = lines[i][:f.start(4)] + re.sub(r"[^<\s]+", action, lines[i][f.start(4):], count=1)
    return "\n".join(lines)
