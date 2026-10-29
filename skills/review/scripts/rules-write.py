#!/usr/bin/env python3
"""rules-write.py — 按 plan.md(规则沉淀计划)里标了「写入」的块,把规则写进仓库。

用法: python3 rules-write.py WORK_DIR --check [--repo DIR]     预演:校验并打印会写什么,不写任何文件
      python3 rules-write.py WORK_DIR --apply [--repo DIR]     确定写入,并把 plan.md 里写过的块改成「已写入」

  WORK_DIR  mine-mrs.sh 的工作目录(里面有 mine-check.py 写的 candidates.json 和 plan.md)
  --repo    仓库根目录,默认取 WORK_DIR/meta.txt 的 repo_root

plan.md 每条候选一个「## 序号. 标题」块,字段:候选、去向、动作、规则、原因、严重级别、范围、反例、正例、不要报
(格式和字段见 rules_plan.py、improve/templates/plan.md)。动作是 待定 / 写入 / 跳过 / 已写入:只处理「写入」,
「待定」只统计,「跳过」「已写入」忽略。去向是下面两种之一:
  .agents/rules/<主题>.md                    规则写成一个「## 标题」块;不要报类写进文件末尾的「## 不要报」一节
  <目录>/REVIEW.md#每次都要检查 | #不要报     (根目录写 REVIEW.md#…)在该章节最后一个列表项后追加一行;
                                             章节不存在就在文件末尾新建,文件不存在就新建(# Review 约定);不改头部和其他内容
rule 类只能去 .agents/rules 或「每次都要检查」,suppress 类只能去 .agents/rules 的「不要报」一节或 REVIEW.md 的「不要报」。

先逐块校验:格式、规则句(按 mine-check.py 的写法要求)、去向、候选编号是否在 candidates.json 里。任一块 ERROR 则整批
什么都不写,退出码 2。来源注释 <!-- 来源:!123 !145 --> 按「候选」编号从 candidates.json 取,不取 plan.md 里的文字。
写 .agents/rules 时只往带 crosscheck 沉淀标记的文件里追加,团队手写的文件(没有标记,或头部解析不了)跳过,另起 <主题>-2.md;
目标里已经有同一句规则的跳过。根目录 REVIEW.md 头部(---)没写完整时拒绝写。不 git add、不提交。

输出(每行一条):
  WRITE: <相对路径> +<n>行        --check 会写 / --apply 写了
  SKIP: 块 <n> 已在 <相对路径> 里
  ERROR: 块 <n> <字段>: <原因>
  WARN: …                          文件超过约 15 条或 4000 字符,或 REVIEW.md 里的规则范围比所在目录宽
  PENDING: <k> 块还是待定
  NOTE: …
"""
import argparse
import os
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules
import rules_plan

SUPPRESS_HEAD = "## 不要报"
MARK = "由 crosscheck 从 MR 评论沉淀"
MAX_RULES, MAX_CHARS = 15, 4000
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
PUNCT = "。.;；"


def source_note(c):
    refs = rules_plan.refs(c)
    return "<!-- 来源:%s -->" % " ".join(refs) if refs else ""


def code(s):
    s = str(s).strip()
    return "`%s`" % s.replace("`", "'") if s else ""


def block_of(c):
    """一条规则一个 ## 块。"""
    title = c.get("title") or c["rule"][:24]
    if crlib.is_suppress_title(title):
        title = c["rule"][:24]      # 不能让这条规则被当成「不要报」一节
    lines = ["## " + title, ""]
    lines.append("- **规则**:" + c["rule"])
    if c.get("why"):
        lines.append("- **原因**:" + c["why"])
    lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
    if c.get("bad_example"):
        lines.append("- **反例**:" + code(c["bad_example"]))
    if c.get("good_example"):
        lines.append("- **正例**:" + code(c["good_example"]))
    lines.append("- **不要报**:" + c.get("exceptions", ""))
    note = source_note(c)
    if note:
        lines.append(note)
    return lines


def suppress_line(c):
    note = source_note(c)
    return "- " + c["rule"] + (("(" + c["why"] + ")") if c.get("why") else "") + (" " + note if note else "")


def review_line(c):
    """REVIEW.md 里的一行:- <规则句>。原因:<原因> <!-- 来源 -->"""
    note = source_note(c)
    why = c.get("why", "")
    return "- " + c["rule"].rstrip(PUNCT) + "。" + ("原因:" + why if why else "") + (" " + note if note else "")


def is_suppress_head(line):
    return line.startswith("## ") and crlib.is_suppress_title(line[3:])


def has_rule(lines, c):
    """目标文件里已经有同一句规则(精确匹配规则原文)。"""
    if c["kind"] == "suppress":
        head = "- " + c["rule"]
        return any(x == head or x.startswith((head + "(", head + " <!--")) for x in (y.strip() for y in lines))
    return "- **规则**:" + c["rule"] in [x.strip() for x in lines]


def has_review_line(lines, c):
    if c["kind"] == "suppress":
        return has_rule(lines, c)
    head = "- " + c["rule"].rstrip(PUNCT)
    return any(x.startswith(head) and (len(x) == len(head) or x[len(head)] in "。.;；(( <")
               for x in (y.strip() for y in lines))


def pick_file(rules_dir, topic, paths, pending):
    """pending 是这次已经读进来、还没写回的文件。只追加到带 MARK 的文件,手写的跳过。"""
    want = sorted(paths)
    n = 1
    while True:
        name = topic + (".md" if n == 1 else "-%d.md" % n)
        p = os.path.join(rules_dir, name)
        if p not in pending and not os.path.exists(p):
            return p, True
        text = "\n".join(pending[p]) if p in pending else open(p, encoding="utf-8").read()
        if MARK in text and not crlib.frontmatter_unclosed(text):
            fm, _ = crlib.parse_frontmatter(text)
            have = sorted(crlib.as_list(fm.get("paths") or fm.get("globs")) or ["**"])
            if have == want:
                return p, False
        n += 1


# ---------- REVIEW.md ----------

def body_start(lines):
    """头部(--- 之间)之后的第一行;没有完整头部时为 0。"""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def section_range(lines, section):
    """(标题行, 下一个 ## 之前) ;没有这一节返回 None。「不要报」用 is_suppress_title 判定。"""
    for i in range(body_start(lines), len(lines)):
        x = lines[i]
        if not x.startswith("## "):
            continue
        title = x[3:].strip()
        if (crlib.is_suppress_title(title) if section == "不要报" else title.rstrip(":：").strip() == section):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
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
    """去向 -> (相对路径, 章节或 None);不合法时抛 ValueError。"""
    path, section = rules_plan.parse_dest(value)
    if os.path.basename(path) == "REVIEW.md":
        if kind == "rule" and section != "每次都要检查":
            raise ValueError("规则类只能写进「每次都要检查」,不能写进「不要报」")
        if kind == "suppress" and section != "不要报":
            raise ValueError("不要报类只能写进「不要报」,不能写进「每次都要检查」")
        d = os.path.dirname(path)
        if d:
            real = os.path.realpath(os.path.join(root, d))
            if ".git" in d.split("/") or not os.path.isdir(real) or os.path.commonpath([real, os.path.realpath(root)]) != os.path.realpath(root):
                raise ValueError("目录 %s 不在仓库里" % d)
        full = os.path.join(root, path)
        if os.path.exists(full) and crlib.frontmatter_unclosed(open(full, encoding="utf-8").read()):
            raise ValueError("%s 的头部(---)没有结束,先修好再写" % path)
    elif kind == "rule" and section:
        raise ValueError("规则类不能写进「不要报」")
    return path, section


def check_block(b, c, root):
    """返回 ([(字段, 原因)], 写入用的候选字典)。"""
    f, kind, errs = b["fields"], c["kind"], []
    for name in rules_plan.REQUIRED[kind]:
        if not f.get(name, "").strip():
            errs.append((name, "缺少这个字段" if name not in f else "不能为空"))
    if kind == "suppress":
        errs += [(name, "不要报类不用这个字段") for name in rules_plan.SUPPRESS_ONLY_FORBIDS if f.get(name, "").strip()]
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
        except ValueError as e:
            errs.append(("去向", str(e)))

    def strip_ticks(s):
        s = s.strip()
        return s[1:-1] if len(s) > 1 and s[0] == s[-1] == "`" else s
    merged = dict(c, title=b["title"], rule=rule, why=crlib.norm(f.get("原因", "")), exceptions=exceptions, paths=paths,
                  severity=crlib.RULE_SEVERITY.get(severity.strip(), ""), dest=dest,
                  bad_example=strip_ticks(f.get("反例", "")), good_example=strip_ticks(f.get("正例", "")))
    return errs, merged


# ---------- 写入计划 ----------

def build(root, todo):
    """todo: [(块, 候选字典)]。返回 (SKIP 提示, 要写的文件 {绝对路径: 行}, 每个文件原来的行数, 写过的块, 警告)。"""
    touched, before, changed, skips, wrote, warns = {}, {}, [], [], [], []

    def load_review(rel):
        p = os.path.join(root, rel)
        if p not in touched:
            if os.path.exists(p):
                text = open(p, encoding="utf-8").read()
                before[p] = len(text.rstrip("\n").split("\n"))
                touched[p] = text.rstrip("\n").split("\n")
            else:
                before[p] = 0
                touched[p] = ["# Review 约定"]
        return p

    for b, c in todo:
        rel_dest, section = c["dest"]
        if os.path.basename(rel_dest) == "REVIEW.md":
            path = load_review(rel_dest)
            lines = touched[path]
            d = os.path.dirname(rel_dest)
            if (d and not all(p == d + "/**" or p.startswith(d + "/") for p in c["paths"])) or (not d and c["paths"] != ["**"]):
                warns.append("WARN: 块 %s 范围 %s 写进 %s 后,会对 %s 下的所有文件生效" % (
                    b["n"], ", ".join(c["paths"]), rel_dest, d + "/" if d else "整个仓库"))
            if has_review_line(lines, c):
                skips.append("SKIP: 块 %s 已在 %s 里" % (b["n"], rel_dest))
                continue
            insert_review(lines, section, suppress_line(c) if c["kind"] == "suppress" else review_line(c))
            if path not in changed:
                changed.append(path)
        else:
            rules_dir = os.path.join(root, os.path.dirname(rel_dest))
            topic = os.path.basename(rel_dest)[:-3]
            path, new = pick_file(rules_dir, topic, c["paths"], touched)
            if path not in touched:
                if new:
                    head = ""
                    if c["paths"] != ["**"]:
                        head = "---\npaths: [%s]\n---\n" % ", ".join('"%s"' % p for p in c["paths"])
                    title = os.path.splitext(os.path.basename(path))[0]
                    text = head + "# %s\n\n%s,经人工挑选。每条规则后面的注释是证据所在的 MR。\n" % (title, MARK)
                    before[path] = 0
                else:
                    text = open(path, encoding="utf-8").read()
                    before[path] = len(text.rstrip("\n").split("\n"))
                touched[path] = text.rstrip("\n").split("\n")
            lines = touched[path]
            if has_rule(lines, c):
                skips.append("SKIP: 块 %s 已在 %s 里" % (b["n"], os.path.relpath(path, root)))
                continue
            if path not in changed:
                changed.append(path)
            if c["kind"] == "suppress":
                if not any(is_suppress_head(x.strip()) for x in lines):
                    lines += ["", SUPPRESS_HEAD, ""]
                lines.append(suppress_line(c))
            else:
                heads = [i for i, x in enumerate(lines) if is_suppress_head(x.strip())]
                at = heads[0] if heads else len(lines)
                while at > 0 and not lines[at - 1].strip():
                    at -= 1
                lines[at:at] = [""] + block_of(c) + ([""] if heads else [])
        wrote.append(b)
    for path in changed:
        lines, rel = touched[path], os.path.relpath(path, root)
        text = "\n".join(lines)
        if os.path.basename(path) == "REVIEW.md":
            n = sum(1 for x in lines if re.match(r"^[-*+]\s", x))
        else:
            n = sum(1 for x in lines if x.startswith("## ") and not is_suppress_head(x.strip()))
        if n > MAX_RULES or len(text) > MAX_CHARS:
            warns.append("WARN: %s 已有 %d 条规则、%d 个字符,超过约 %d 条或 %d 字符,建议按目录或主题拆成几个文件" % (rel, n, len(text), MAX_RULES, MAX_CHARS))
    return skips, touched, before, changed, wrote, warns


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("work")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="预演:只打印会写什么,不写文件")
    mode.add_argument("--apply", action="store_true", help="确定写入")
    ap.add_argument("--repo", default="")
    a = ap.parse_args()

    cands = {c["no"]: c for c in crlib.load_json(os.path.join(a.work, "candidates.json"), [])}
    if not cands:
        print("rules-write: %s 里没有候选,请先运行 mine-check.py" % a.work, file=sys.stderr)
        return 1
    plan_path = os.path.join(a.work, "plan.md")
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_text = f.read()
    except OSError:
        print("rules-write: 找不到 %s,请先运行 mine-check.py 生成" % plan_path, file=sys.stderr)
        return 1
    root = a.repo or crlib.read_kv(os.path.join(a.work, "meta.txt")).get("repo_root", "")
    if not root or not os.path.isdir(root):
        print("rules-write: 找不到仓库目录,请用 --repo 指定", file=sys.stderr)
        return 1

    blocks, errors = rules_plan.parse(plan_text)
    todo, pending = [], 0
    for b in blocks:
        action = b["fields"].get("动作", "")
        if "动作" not in b["fields"]:
            errors.append((b["label"], "动作", "缺少这个字段"))
        elif action not in rules_plan.ACTIONS:
            errors.append((b["label"], "动作", "应为 %s 之一,现在是「%s」" % ("、".join(rules_plan.ACTIONS), action)))
        if b.get("cand") is None:
            continue
        if b["cand"] not in cands:
            errors.append((b["label"], "候选", "编号 %d 不在 candidates.json 里" % b["cand"]))
            continue
        if action in ("跳过", "已写入"):
            continue
        errs, merged = check_block(b, cands[b["cand"]], root)
        errors += [(b["label"], name, why) for name, why in errs]
        if action == "待定":
            pending += 1
        elif action == "写入" and not errs:
            todo.append((b, merged))
    if errors:
        for label, name, why in errors:
            print("ERROR: 块 %s %s: %s" % (label, name, why))
        print("NOTE: 有 %d 处错误,什么都没写。改好 plan.md 再运行。" % len(errors))
        return 2

    skips, touched, before, changed, wrote, warns = build(root, todo)
    for x in skips:
        print(x)
    for path in changed:
        lines = touched[path]
        print("WRITE: %s +%d行" % (os.path.relpath(path, root), len(lines) - before[path]))
    for w in warns:
        print(w)
    if pending:
        print("PENDING: %d 块还是待定" % pending)
    if a.check:
        if not todo:
            print("NOTE: 没有动作为「写入」的块,不会写任何文件。")
        return 0

    for path in changed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(touched[path]).rstrip("\n") + "\n")
    if wrote:
        for b in wrote:
            plan_text = rules_plan.set_action(plan_text, b, "已写入")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(plan_text)
    if changed:
        note = "NOTE: 只改了工作区,没有 git add 或提交。"
        if any(os.path.relpath(p, root).startswith(rules_plan.RULES_DIR + "/") for p in changed):
            note += ("crosscheck 审查时会读 .agents/rules/;Claude Code、Cursor 写代码时不会自动读这个目录,"
                     "需要的话可以在 AGENTS.md 或 CLAUDE.md 里加一行 `@.agents/rules/<文件>` 引入(先问用户)。")
        print(note)
    else:
        print("NOTE: 没有写任何文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
