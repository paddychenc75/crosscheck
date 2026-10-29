# rules_plan.py — 规则沉淀计划 plan.md 的格式:mine-check.py 生成,rules-write.py 解析。
# 只被本目录下的脚本 import,不单独运行。字段名、动作、去向的写法都以这里为准。

import datetime
import os
import re

import crlib

TITLE = "# 规则沉淀计划"
FIELDS = ("候选", "去向", "动作", "规则", "原因", "严重级别", "范围", "反例", "正例", "不要报")
# 各类候选必填的字段;suppress 类不用严重级别、反例、正例、不要报
REQUIRED = {"rule": ("候选", "去向", "动作", "规则", "范围", "严重级别", "不要报"),
            "suppress": ("候选", "去向", "动作", "规则", "范围")}
SUPPRESS_ONLY_FORBIDS = ("严重级别", "反例", "正例", "不要报")
ACTIONS = ("待定", "写入", "跳过", "已写入")
SECTIONS = ("每次都要检查", "不要报")
RULES_DIR = ".agents/rules"

HEADING = re.compile(r"^##\s+(.*)$")
NUMBERED = re.compile(r"^(\d+)\s*[.．、]\s*(.*)$")
FIELD = re.compile(r"^(\s*[-*]\s+\*\*)([^*]+?)(\*\*\s*[::]\s*)(.*)$")


def refs(c):
    """候选的来源:MR 编号,有判断记录作证据时再加一项。"""
    out = ["!%s" % m for m in c.get("mrs", [])]
    if any(e.get("feedback") for e in c.get("evidence", [])):
        out.append("crosscheck 判断记录")
    return out


def suggest_dest(c, root):
    """rule 类去 .agents/rules;suppress 类范围恰好是仓库里一个目录(dir/**)时去该目录的 REVIEW.md,其余去 .agents/rules。"""
    paths = c.get("paths") or []
    if c.get("kind") == "suppress" and len(paths) == 1:
        m = re.fullmatch(r"([^*?\[\]{}]+?)/+\*\*", paths[0])
        if m and root and os.path.isdir(os.path.join(root, m.group(1))):
            return "%s/REVIEW.md#不要报" % m.group(1)
    return "%s/%s.md" % (RULES_DIR, c.get("topic") or "general")


def render_block(c, dest):
    one = lambda s: " ".join(str(s or "").split())
    marks = "".join({True: "✓", False: "✗", None: "?"}[b] for _, b in c.get("admission", []))
    detail = [" ".join(refs(c)) or "-", "评审人 %d 位" % c.get("people", 0)]
    if marks:
        detail.append("准入 " + marks)
    lines = ["## %d. %s" % (c["no"], one(c.get("title")) or one(c["rule"])[:24]), "",
             "- **候选**:%d(%s)" % (c["no"], ";".join(detail)),
             "- **去向**:" + dest,
             "- **动作**:待定",
             "- **规则**:" + one(c["rule"])]
    if c.get("why"):
        lines.append("- **原因**:" + one(c["why"]))
    if c["kind"] == "rule":
        lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
    lines.append("- **范围**:" + ", ".join(c.get("paths") or ["**"]))
    if c["kind"] == "rule":
        for name, key in (("反例", "bad_example"), ("正例", "good_example")):
            if c.get(key):
                lines.append("- **%s**:%s" % (name, one(c[key])))
        lines.append("- **不要报**:" + one(c.get("exceptions")))
    return lines


def render(cands, root, meta):
    project = os.path.basename((meta.get("repo_root") or root or "").rstrip("/")) or meta.get("repo_id", "")
    lines = [TITLE, "",
             "- 项目:" + project,
             "- 范围:最近 %s 天,%s 个 MR" % (meta.get("days", "?"), meta.get("mrs", "?")),
             "- 生成:" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "",
             "<!-- 逐条把「动作」改成「写入」或「跳过」,「待定」不会写。改完用 rules-write.py <work_dir> --check 预演,没问题再 --apply。-->"]
    for c in cands:
        lines += [""] + render_block(c, suggest_dest(c, root))
    return "\n".join(lines) + "\n"


def live_lines(text):
    """逐行去掉 HTML 注释(可跨行),返回 [(行号, 去注释后的文字)];最后一个值表示文件末尾还有没闭合的注释。"""
    out, in_comment = [], False
    for i, raw in enumerate(text.split("\n")):
        line = raw
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
    """-> (blocks, errors)。block: n 序号、title、head 标题行号、cand 候选编号、fields {字段: 值}、at {字段: 行号}。
    errors: [(块标号, 字段, 原因)]。只检查格式,内容(规则句、去向)由调用方校验。"""
    lines, open_comment = live_lines(text)
    blocks, errors, cur, seen = [], [], None, set()

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

    for i, line in lines:
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
            cur = dict(n=n, label=str(n), title=m.group(2).strip(), head=i, cand=None, fields={}, at={})
            continue
        if cur is None or not s:
            continue
        f = FIELD.match(line)
        if not f:
            errors.append((cur["label"], "格式", "看不懂这一行(字段应写成「- **字段**:值」):" + s[:30]))
            continue
        name, value = f.group(2).strip(), f.group(4).strip()
        if name not in FIELDS:
            errors.append((cur["label"], name, "未知字段(可用:%s)" % "、".join(FIELDS)))
        elif name in cur["fields"]:
            errors.append((cur["label"], name, "字段重复"))
        else:
            cur["fields"][name] = value
            cur["at"][name] = i
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
