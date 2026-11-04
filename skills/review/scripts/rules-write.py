#!/usr/bin/env python3
"""历史候选构建器:rules-write.py WORK --prepare [--repo ROOT]。

读取 candidates.json 与 mined-candidates.md 的冻结配对,保留规则/例外分流、
范围校验、来源注释和完整内容去重,把选中内容编译到 rules-draft.json/md。
不写仓库。所有维护的预演、确认版本校验和写入由 rules-draft.py 统一执行。
"""
import argparse
import os
import subprocess
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules
import rule_candidates
import rules_draft

MARK = "由 crosscheck 从 MR 评论沉淀"
MAX_RULES, MAX_CHARS = 15, 4000
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
PUNCT = "。.;；"


def source_note(c):
    """来源注释:典型 MR 的网页地址加总数、判断记录来源,写法见 rule_candidates.source_refs。"""
    refs = rule_candidates.source_refs(c)
    return "<!-- 来源:%s -->" % refs if refs else ""


def block_of(c):
    """一条规则一个 ## 块;反例 / 正例是字段行后的围栏代码块,和候选规则文件同一写法。"""
    lines = ["## " + (c.get("title") or c["rule"][:24]), ""]
    lines.append("- **规则**:" + c["rule"].rstrip(crlib.RULE_PUNCT))
    if c.get("why"):
        lines.append("- **原因**:" + c["why"])
    lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
    lines += rule_candidates.example_lines("反例", c.get("bad_example"), c.get("bad_lang", ""))
    lines += rule_candidates.example_lines("正例", c.get("good_example"), c.get("good_lang", ""))
    lines.append("- **不要报**:" + c.get("exceptions", ""))
    note = source_note(c)
    if note:
        lines.append(note)
    return lines


def suppress_text(c, scope):
    """一条不要报的文字:规则句(原因)<!-- 来源 -->。范围不等于目标本身的范围 scope 时,把范围写进句子开头。"""
    text = c["rule"]
    if set(c["paths"]) - {"**"} and sorted(set(c["paths"])) != sorted(set(scope)):
        text = "在 %s 范围内,%s" % (", ".join(c["paths"]), text)
    note = source_note(c)
    return text + (("(" + c["why"] + ")") if c.get("why") else "") + (" " + note if note else "")


def review_scope(rel):
    d = os.path.dirname(rel)
    return [d + "/**"] if d else ["**"]


def has_rule(lines, c):
    """恢复必须匹配完整块;范围由 pick_file 核对。「不要报」字段后来并进了别的例外(「；」追加)也算同一块。"""
    wanted = block_of(c)
    same = lambda got, want: got == want or (want.startswith("- **不要报**:") and got.startswith(want.rstrip(PUNCT) + "；"))
    return any(len(lines) - i >= len(wanted) and all(same(g, w) for g, w in zip(lines[i:], wanted)) for i in range(len(lines)))


def has_review_line(lines, item):
    r = section_range(lines, "不要报")
    return bool(r and item in lines[r[0] + 1:r[1]])


# ---------- 规则的例外 ----------

def find_rule(lines, title, rel):
    """标题为 title 的「## 」规则块的行号;没有或不止一个时抛 ValueError。"""
    hits = [i for i in headings(lines) if lines[i][3:].strip() == title.strip()]
    if not hits:
        raise ValueError("%s 里没有标题为「%s」的规则" % (rel, title))
    if len(hits) > 1:
        raise ValueError("%s 里标题为「%s」的规则有 %d 条,无法确定是哪条的例外" % (rel, title, len(hits)))
    return hits[0]


def merge_exception(lines, head, item):
    """把 item 并进 head 处规则块的「不要报」字段:已有内容用「；」追加,没有这个字段就在块的最后一个字段后补一行。
    字段里已经有 item 时返回 False。"""
    end = next((j for j in headings(lines) if j > head), len(lines))
    fenced = crlib.fence_mask(lines)
    for i in range(head + 1, end):
        f = rule_candidates.FIELD.match(lines[i])
        if f and not fenced[i] and f.group(2).strip() == "不要报":
            value = f.group(4).strip()
            if item in value:
                return False
            lines[i] = lines[i][:f.start(4)] + (value.rstrip(PUNCT) + "；" if value else "") + item
            return True
    last = max(j for j in range(head, end) if fenced[j] or (lines[j].strip() and not lines[j].lstrip().startswith("<!--")))
    lines.insert(last + 1, "- **不要报**:" + item)
    return True


def safe_target(root, path):
    """检查最终物理目标和 Git 实际元数据目录,包括 worktree。"""
    root, real = os.path.realpath(root), os.path.realpath(path)
    if os.path.commonpath([root, real]) != root or ".git" in os.path.relpath(path, root).split(os.sep):
        raise ValueError("目标不在仓库工作区: " + path)
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        r = subprocess.run(["git", "-C", root, "rev-parse", flag], capture_output=True, text=True)
        if r.returncode:
            raise ValueError("无法核对仓库 Git 元数据目录")
        gitdir = os.path.realpath(os.path.join(root, r.stdout.strip()))
        if os.path.commonpath([gitdir, real]) == gitdir:
            raise ValueError("目标指向 Git 元数据: " + path)
    if os.path.lexists(path) and not os.path.isfile(real):
        raise ValueError("目标不是普通文件: " + path)
    return real


def pick_file(rules_dir, topic, paths, pending, root):
    """pending 是这次已经读进来、还没写回的文件。只追加到带 MARK 的文件,手写的跳过。"""
    want = sorted(paths)
    n = 1
    while True:
        name = topic + (".md" if n == 1 else "-%d.md" % n)
        p = os.path.join(rules_dir, name)
        safe_target(root, p)
        if p not in pending and not os.path.exists(p):
            return p, True
        text = "\n".join(pending[p]) if p in pending else open(p, encoding="utf-8").read()
        if MARK in text and not crlib.frontmatter_unclosed(text):
            if sorted(file_scope(p, text, root)) == want:
                return p, False
        n += 1


def file_scope(path, text, root):
    """规则文件的适用范围(paths 或 globs,没有就是 **);头部写错时返回 []。"""
    try:
        r = project_rules.describe(os.path.relpath(path, root).replace(os.sep, "/"), text)
    except ValueError:
        return []
    return [] if r["issues"] else r["patterns"]


def front(paths):
    """新建规则文件的头部:Claude 只读 paths,Cursor 只读 globs(逗号分隔的字符串),两个都写同一串。范围是 ** 时不写头部。"""
    if paths == ["**"]:
        return ""
    value = '"%s"' % ", ".join(paths)
    return "---\npaths: %s\nglobs: %s\n---\n" % (value, value)


# ---------- REVIEW.md ----------

def body_start(lines):
    """头部(--- 之间)之后的第一行;没有完整头部时为 0。"""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def headings(lines):
    """所有 ## 标题的行号;围栏代码块里的行不算。"""
    fenced = crlib.fence_mask(lines)
    return [i for i, x in enumerate(lines) if x.startswith("## ") and not fenced[i]]


def section_range(lines, section):
    """(标题行, 下一个 ## 之前) ;没有这一节返回 None。「不要报」用 is_suppress_title 判定。"""
    heads = [i for i in headings(lines) if i >= body_start(lines)]
    for i in heads:
        title = lines[i][3:].strip()
        if (crlib.is_suppress_title(title) if section == "不要报" else title.rstrip(":：").strip() == section):
            end = next((j for j in heads if j > i), len(lines))
            return i, end
    return None


def insert_review(lines, section, item):
    r = section_range(lines, section)
    if r is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ([""] if lines else []) + ["## " + section, "", item]
        return
    head, end = r
    items = [i for i in range(head + 1, end) if LIST_ITEM.match(lines[i])]
    if items:
        last = items[-1]
        for j in range(last + 1, end):         # 列表项下面缩进的续行也算这一项
            if lines[j].strip() and lines[j][:1] in " \t":
                last = j
            elif lines[j].strip():
                break
        lines.insert(last + 1, item)
        return
    last = max([head] + [j for j in range(head + 1, end) if lines[j].strip()])
    add = ["", item] + ([""] if last + 1 < len(lines) and lines[last + 1].strip() else [])
    lines[last + 1:last + 1] = add


# ---------- 校验 ----------

def check_dest(root, kind, value):
    """去向 -> (相对路径, REVIEW.md 的章节、规则标题或 None);不合法时抛 ValueError。"""
    path, section = rule_candidates.parse_dest(value)
    full = os.path.join(root, path)
    safe_target(root, full)
    if os.path.basename(path) == "REVIEW.md":
        if kind == "rule":
            raise ValueError("规则类是代码约束,写进 .agents/rules/<主题>.md;REVIEW.md 只收不要报类")
        d = os.path.dirname(path)
        if d:
            real = os.path.realpath(os.path.join(root, d))
            if ".git" in d.split("/") or not os.path.isdir(real) or os.path.commonpath([real, os.path.realpath(root)]) != os.path.realpath(root):
                raise ValueError("目录 %s 不在仓库里" % d)
        if os.path.exists(full) and crlib.frontmatter_unclosed(open(full, encoding="utf-8").read()):
            raise ValueError("%s 的头部(---)没有结束,先修好再写" % path)
    elif kind == "rule" and section:
        raise ValueError("规则类写成新的规则块,去向不带 #:.agents/rules/<主题>.md")
    elif kind == "suppress":
        if not section:
            raise ValueError("不要报类写进 .agents/rules 时要指明是哪条规则的例外:.agents/rules/<主题>.md#<规则标题>;"
                             "不是某条规则的例外就写进 REVIEW.md#不要报")
        if not os.path.isfile(full):
            raise ValueError("%s 不存在,找不到规则「%s」" % (path, section))
        find_rule(open(full, encoding="utf-8").read().split("\n"), section, path)
    return path, section


def check_block(b, c, root):
    """返回 ([(字段, 原因)], 写入用的候选字典)。"""
    f, kind, errs = b["fields"], c["kind"], []
    for name in rule_candidates.REQUIRED[kind]:
        if not f.get(name, "").strip():
            errs.append((name, "缺少这个字段" if name not in f else "不能为空"))
    if kind == "suppress":
        errs += [(name, "不要报类不用这个字段") for name in rule_candidates.SUPPRESS_ONLY_FORBIDS if f.get(name, "").strip()]
    rule = crlib.norm(f.get("规则", ""))
    exceptions = crlib.norm(f.get("不要报", ""))
    severity = f.get("严重级别", "")
    for p in crlib.rule_problems(kind, rule, severity, exceptions) if rule else []:
        errs.append(("严重级别" if "严重级别" in p else "不要报" if "不要报" in p else "规则", p))
    paths = []
    if f.get("范围", "").strip():
        try:
            paths = project_rules.patterns(f["范围"].replace("，", ","))
            if not paths:
                raise ValueError("没有写适用路径")
        except ValueError as e:
            errs.append(("范围", str(e)))
    dest = None
    if f.get("去向", "").strip():
        try:
            dest = check_dest(root, kind, f["去向"])
            directory = os.path.dirname(dest[0])
            if os.path.basename(dest[0]) == "REVIEW.md" and directory:
                outside = [p for p in paths if p != "**" and not p.startswith(directory + "/")]
                if outside:
                    errs.append(("范围", "%s 不在 %s 里:目录的 REVIEW.md 只对所在目录生效,改去根目录 REVIEW.md#不要报"
                                 % (", ".join(outside), directory)))
        except ValueError as e:
            errs.append(("去向", str(e)))

    why = crlib.norm(f.get("原因", ""))
    if crlib.MR_REF.search(why):
        errs.append(("原因", "不要写 MR 号(%s):来源由脚本按候选编号写在末尾的注释里" % crlib.MR_REF.search(why).group(0)))

    def example(name):
        s = f.get(name, "")
        if "\n" not in s:               # 写在同一行的,去掉包着的反引号
            s = s.strip()
            s = s[1:-1] if len(s) > 1 and s[0] == s[-1] == "`" else s
        return s
    merged = dict(c, title=b["title"], rule=rule, why=why, exceptions=exceptions, paths=paths,
                  severity=crlib.RULE_SEVERITY.get(severity.strip(), ""), dest=dest,
                  bad_example=example("反例"), good_example=example("正例"),
                  bad_lang=b.get("lang", {}).get("反例", ""), good_lang=b.get("lang", {}).get("正例", ""))
    return errs, merged


# ---------- 要写的内容 ----------

def build(root, todo, originals=None):
    """todo: [(块, 候选字典)]。返回 (SKIP 提示, 要写的文件 {绝对路径: 行}, 每个文件原来的行数, 要改的文件, 写过的块, 警告,
    本次写入或确认已在其中的 .agents/rules 文件)。"""
    touched, before, changed, skips, wrote, warns, rule_files = {}, {}, [], [], [], [], []

    def load(p, new_text=None):
        """读进 touched;文件不存在时用 new_text 起头(None 表示必须已存在)。"""
        if p not in touched:
            exists = os.path.exists(p)
            if exists or new_text is None:
                text = rules_draft.read(p)
                before[p] = len(text.rstrip("\n").split("\n"))
            else:
                text, before[p] = new_text, 0
            if originals is not None:
                originals[os.path.relpath(p, root)] = text if exists else None
            touched[p] = text.rstrip("\n").split("\n")
        return touched[p]

    for b, c in todo:
        rel_dest, section = c["dest"]
        if os.path.basename(rel_dest) == "REVIEW.md":
            path = os.path.join(root, rel_dest)
            lines = load(path, "# Review 约定")
            item = "- " + suppress_text(c, review_scope(rel_dest))
            if has_review_line(lines, item):
                skips.append("SKIP: 块 %s 已在 %s 里" % (b["n"], rel_dest))
                wrote.append(b)
                continue
            insert_review(lines, section, item)
        elif c["kind"] == "suppress":
            path = os.path.join(root, rel_dest)
            lines = load(path)
            head = find_rule(lines, section, rel_dest)
            if path not in rule_files:
                rule_files.append(path)
            if not merge_exception(lines, head, suppress_text(c, file_scope(path, "\n".join(lines), root))):
                skips.append("SKIP: 块 %s 已在 %s 里" % (b["n"], rel_dest))
                wrote.append(b)
                continue
        else:
            rules_dir = os.path.join(root, os.path.dirname(rel_dest))
            topic = os.path.basename(rel_dest)[:-3]
            path, new = pick_file(rules_dir, topic, c["paths"], touched, root)
            title = os.path.splitext(os.path.basename(path))[0]
            lines = load(path, front(c["paths"]) + "# %s\n\n%s,经人工挑选。每条规则后面的注释是证据所在的 MR。\n" % (title, MARK) if new else None)
            if path not in rule_files:
                rule_files.append(path)
            if has_rule(lines, c):
                skips.append("SKIP: 块 %s 已在 %s 里" % (b["n"], os.path.relpath(path, root)))
                wrote.append(b)
                continue
            while lines and not lines[-1].strip():
                lines.pop()
            lines += [""] + block_of(c)
        if path not in changed:
            changed.append(path)
        wrote.append(b)
    for path in changed:
        lines, rel = touched[path], os.path.relpath(path, root)
        text = "\n".join(lines)
        if os.path.basename(path) == "REVIEW.md":
            fenced = crlib.fence_mask(lines)
            n = sum(1 for i, x in enumerate(lines) if re.match(r"^[-*+]\s", x) and not fenced[i])
        else:
            n = len(headings(lines))
        if n > MAX_RULES or len(text) > MAX_CHARS:
            warns.append("WARN: %s 已有 %d 条规则、%d 个字符,超过约 %d 条或 %d 字符,建议按目录或主题拆成几个文件" % (rel, n, len(text), MAX_RULES, MAX_CHARS))
    return skips, touched, before, changed, wrote, warns, rule_files


def prepare(work, root=""):
    """把冻结的历史候选构建成统一文件清单;不写仓库,不代替最终确认。"""
    candidates = crlib.load_json(os.path.join(work, "candidates.json"), [])
    cands = {c["no"]: c for c in candidates}
    md_path = os.path.join(work, rule_candidates.FILE)
    md_text = rules_draft.read(md_path)
    rules_draft.require(rule_candidates.bound(md_text, candidates),
                        "candidates.json 与历史候选的绑定不一致;保留原配对,在新采集目录重新生成")
    root = root or crlib.read_kv(os.path.join(work, "meta.txt")).get("repo_root", "")
    rules_draft.require(root and os.path.isdir(root), "找不到仓库目录,请用 --repo 指定")
    root = os.path.realpath(root)
    blocks, errors = rule_candidates.parse(md_text)
    fields, field_errors = rule_candidates.head_fields(md_text)
    errors += field_errors
    hook = fields.get(rule_candidates.HOOKUP, (None, None))[0]
    if hook is not None and hook not in rule_candidates.ACTIONS:
        errors.append((None, rule_candidates.HOOKUP, "应为 %s 之一,现在是「%s」" % ("、".join(rule_candidates.ACTIONS), hook)))
    todo, pending, unmet = [], [], []
    for b in blocks:
        action = b["fields"].get("动作", "")
        if action not in rule_candidates.ACTIONS:
            errors.append((b["label"], "动作", "应为 %s 之一" % "、".join(rule_candidates.ACTIONS)))
        if b.get("cand") is None:
            continue
        if b["cand"] not in cands:
            errors.append((b["label"], "候选", "编号 %d 不在 candidates.json 里" % b["cand"]))
            continue
        if action == "跳过":
            continue
        errs, merged = check_block(b, cands[b["cand"]], root)
        # 初次展示允许未完善的待定候选;选入拟写内容才要求所有字段可用。
        if action == "待定":
            pending.append("候选 %s:%s;尚未选入拟写内容" % (b["label"], b["title"]))
            unmet += ["候选 %s:%s" % (b["label"], x) for x in cands[b["cand"]].get("unmet", [])]
            unmet += ["候选 %s %s:%s" % (b["label"], name, why) for name, why in errs]
        else:
            errors += [(b["label"], name, why) for name, why in errs]
            if not errs:
                todo.append((b, merged))
                unmet += ["候选 %s:%s" % (b["label"], x) for x in cands[b["cand"]].get("unmet", [])]
    if errors:
        raise ValueError("\nERROR: ".join(("块 %s %s: %s" % row) if row[0] is not None else "头部 %s: %s" % row[1:] for row in errors))
    originals = {}
    skips, touched, before, changed, wrote, warns, rule_files = build(root, todo, originals)
    rels = [os.path.relpath(p, root) for p in touched]
    links = [os.path.relpath(p, root) for p in rule_files] if hook == "写入" else []
    if hook == "待定":
        pending.append("接入 AGENTS.md 尚未决定")
    if hook == "写入" and not rule_files:
        pending.append("接入 AGENTS.md 已选择,但还没有选入规则文件")
    if links:
        rels.append("AGENTS.md")
    # 新材料只刷新来源绑定,已冻结的目标基线永不悄悄重置。
    rules_draft.capture(work, root, rels, refresh_bindings=True, expected=originals)
    refs = sorted({b["cand"] for b, _ in todo})
    source = dict(type="inferred", basis="历史候选 " + ", ".join(map(str, refs)), candidates=refs)
    changes = [dict(path=os.path.relpath(p, root), operation="write", content="\n".join(touched[p]).rstrip("\n") + "\n",
                    reason="根据已核对材料整理项目约束或误报边界", source=source) for p in changed]
    bindings = {name: rules_draft.content_hash(rules_draft.read(os.path.join(work, name)))
                for name in ("candidates.json", rule_candidates.FILE)}
    request = dict(goal="从明确指定的历史材料维护项目规则", changes=changes, agents_links=links,
                   rule_targets=[os.path.relpath(p, root) for p in rule_files],
                   agents_source=dict(type="user", request="选择接入本次规则的 AGENTS.md 索引"),
                   pending=pending + unmet, notes=skips + warns)
    data = rules_draft.propose(work, request, bindings=bindings)
    print("RULES_DRAFT: " + os.path.join(work, rules_draft.VIEW))
    return data


def main():
    ap = argparse.ArgumentParser(description="将历史候选编译成统一维护草稿;最终预演/写入使用 rules-draft.py")
    ap.add_argument("work")
    ap.add_argument("--prepare", action="store_true", required=True)
    ap.add_argument("--repo", default="")
    args = ap.parse_args()
    try:
        prepare(args.work, args.repo)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("ERROR: " + str(error))
        return 2


if __name__ == "__main__":
    sys.exit(main())
