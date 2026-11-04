#!/usr/bin/env python3
"""rules-write.py — 按 plan.md(规则沉淀计划)里标了「写入」的块,把规则写进仓库。

用法: python3 rules-write.py WORK_DIR --check [--repo DIR]     预演:校验并打印会写什么,不写任何文件
      python3 rules-write.py WORK_DIR --apply [--repo DIR]     确定写入,并把 plan.md 里写过的块改成「已写入」

  WORK_DIR  mine-mrs.sh 的工作目录(里面有 mine-check.py 写的 candidates.json 和 plan.md)
  --repo    仓库根目录,默认取 WORK_DIR/meta.txt 的 repo_root

plan.md 每条候选一个「## 序号. 标题」块,字段:候选、去向、动作、规则、原因、严重级别、范围、反例、正例、不要报
(格式和字段见 rules_plan.py、improve/templates/plan.md;块末尾 > 开头的引用块是证据,只给人看,不读)。动作是 待定 / 写入 / 跳过 / 已写入:只处理「写入」,
「待定」只统计,「跳过」「已写入」忽略。去向是下面三种之一:
  .agents/rules/<主题>.md                 rule 类:写成一个「## 标题」块;新建的文件头部 paths、globs 写同一串逗号分隔的 glob
  .agents/rules/<主题>.md#<规则标题>      suppress 类,这条是该规则的例外:并进那个「## 规则标题」块的「不要报」字段,
                                          已有内容用「；」追加;文件不存在、找不到标题或标题重复时 ERROR
  <目录>/REVIEW.md#不要报                 suppress 类的其余情况(根目录写 REVIEW.md#不要报):在「不要报」一节最后一个列表项后追加一行;
                                          章节不存在就在文件末尾新建,文件不存在就新建(# Review 约定);不改头部和其他内容
范围不等于目标本身的范围(REVIEW.md 所在目录、规则文件的 paths)时,把范围写进句子开头;目录 REVIEW.md 的范围要在该目录里。

先逐块校验:格式、规则句(按 mine-check.py 的写法要求)、去向、候选编号是否在 candidates.json 里。任一块 ERROR 则整批
什么都不写,退出码 2。来源注释 <!-- 来源:!123 !145 --> 按「候选」编号从 candidates.json 取,不取 plan.md 里的文字。
写新规则时只往带 crosscheck 沉淀标记的文件里追加,团队手写的文件(没有标记,或头部解析不了)跳过,另起 <主题>-2.md;
目标里完整内容、范围和边界都一致才跳过并补计划状态。根目录 REVIEW.md 头部(---)没写完整时拒绝写。不 git add、不提交。
--apply 写了 .agents/rules 后打印接入建议(软链接命令、AGENTS.md 里的链接行),只打印,不执行;已经接上的不提示。

输出(每行一条):
  WRITE: <相对路径> +<n>行        --check 会写 / --apply 写了
  SKIP: 块 <n> 已在 <相对路径> 里
  ERROR: 块 <n> <字段>: <原因>
  WARN: …                          文件超过约 15 条或 4000 字符
  PENDING: <k> 块还是待定
  SUGGEST: …                       接入建议,由用户决定做不做
  NOTE: …
"""
import argparse
import difflib
import os
import shlex
import stat
import subprocess
import tempfile
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules
import rules_plan

MARK = "由 crosscheck 从 MR 评论沉淀"
MAX_RULES, MAX_CHARS = 15, 4000
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
PUNCT = "。.;；"


def source_note(c):
    refs = rules_plan.refs(c)
    return "<!-- 来源:%s -->" % " ".join(refs) if refs else ""


def block_of(c):
    """一条规则一个 ## 块;反例 / 正例是字段行后的围栏代码块,和 plan.md 同一写法。"""
    lines = ["## " + (c.get("title") or c["rule"][:24]), ""]
    lines.append("- **规则**:" + c["rule"].rstrip(crlib.RULE_PUNCT))
    if c.get("why"):
        lines.append("- **原因**:" + c["why"])
    lines.append("- **严重级别**:" + (c.get("severity") or "轻微"))
    lines += rules_plan.example_lines("反例", c.get("bad_example"), c.get("bad_lang", ""))
    lines += rules_plan.example_lines("正例", c.get("good_example"), c.get("good_lang", ""))
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
        f = rules_plan.FIELD.match(lines[i])
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


def atomic_write(path, text, root=None):
    target = safe_target(root, path) if root else path
    os.makedirs(os.path.dirname(target), exist_ok=True)  # 写记录目录的例外:工作区规则或已授权的计划目录
    fd, tmp = tempfile.mkstemp(prefix=".crosscheck-", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(target):
            os.chmod(tmp, stat.S_IMODE(os.stat(target).st_mode))
        if root and safe_target(root, path) != target:
            raise ValueError("目标物理路径在写入前改变")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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
    path, section = rules_plan.parse_dest(value)
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


# ---------- 写入计划 ----------

def build(root, todo):
    """todo: [(块, 候选字典)]。返回 (SKIP 提示, 要写的文件 {绝对路径: 行}, 每个文件原来的行数, 写过的块, 警告)。"""
    touched, before, changed, skips, wrote, warns = {}, {}, [], [], [], []

    def load(p, new_text=None):
        """读进 touched;文件不存在时用 new_text 起头(None 表示必须已存在)。"""
        if p not in touched:
            if os.path.exists(p) or new_text is None:
                text = open(p, encoding="utf-8").read()
                before[p] = len(text.rstrip("\n").split("\n"))
            else:
                text, before[p] = new_text, 0
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
    return skips, touched, before, changed, wrote, warns


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("work")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="预演:只打印会写什么,不写文件")
    mode.add_argument("--apply", action="store_true", help="确定写入")
    ap.add_argument("--repo", default="")
    a = ap.parse_args()

    candidates = crlib.load_json(os.path.join(a.work, "candidates.json"), [])
    cands = {c["no"]: c for c in candidates}
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
    if not rules_plan.bound(plan_text, candidates):
        print("ERROR: candidates.json 与 plan.md 的绑定不一致;请使用原配对,或在新采集目录生成计划。")
        return 2
    root = a.repo or crlib.read_kv(os.path.join(a.work, "meta.txt")).get("repo_root", "")
    if not root or not os.path.isdir(root):
        print("rules-write: 找不到仓库目录,请用 --repo 指定", file=sys.stderr)
        return 1

    blocks, errors = rules_plan.parse(plan_text)
    todo, pending, unmet = [], 0, []
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
            if cands[b["cand"]].get("unmet"):      # 待确认项只提醒,写不写由负责人定
                unmet.append("WARN: 块 %s 标了写入,仍有%s" % (b["label"], ";".join(cands[b["cand"]]["unmet"])))
    if errors:
        for label, name, why in errors:
            print("ERROR: 块 %s %s: %s" % (label, name, why))
        print("NOTE: 有 %d 处错误,什么都没写。改好 plan.md 再运行。" % len(errors))
        return 2

    try:
        skips, touched, before, changed, wrote, warns = build(root, todo)
        for path in touched:
            safe_target(root, path)
    except (OSError, ValueError) as e:
        print("ERROR: 目标校验失败,什么都没写: %s" % e)
        return 2
    for x in skips:
        print(x)
    if a.check:
        for path in changed:
            print("WRITE: %s +%d行" % (os.path.relpath(path, root), len(touched[path]) - before[path]))
            old = open(path, encoding="utf-8").read().splitlines(True) if os.path.exists(path) else []
            new = ("\n".join(touched[path]).rstrip("\n") + "\n").splitlines(True)
            print("".join(difflib.unified_diff(old, new, fromfile=os.path.relpath(path, root), tofile=os.path.relpath(path, root))), end="")
    for w in unmet + warns:
        print(w)
    if pending:
        print("PENDING: %d 块还是待定" % pending)
    if a.check:
        if not todo:
            print("NOTE: 没有动作为「写入」的块,不会写任何文件。")
        return 0

    completed = []
    try:
        for path in changed:
            atomic_write(path, "\n".join(touched[path]).rstrip("\n") + "\n", root)
            completed.append(path)
            print("WRITE: %s +%d行" % (os.path.relpath(path, root), len(touched[path]) - before[path]))
        if wrote:
            for b in wrote:
                plan_text = rules_plan.set_action(plan_text, b, "已写入")
            with crlib.record_write(plan_path):
                atomic_write(plan_path, plan_text)
    except (OSError, ValueError, SystemExit) as e:
        print("ERROR: 写入中断;已完成: %s;未完成: %s;plan.md 未更新,修复后重跑同一命令核对完整内容并补状态。" % (
            ", ".join(os.path.relpath(p, root) for p in completed) or "无",
            ", ".join(os.path.relpath(p, root) for p in changed if p not in completed) or "无(仅计划状态待更新)"))
        if isinstance(e, SystemExit):
            return e.code
        print(str(e), file=sys.stderr)
        return 1
    if not changed:
        print("NOTE: 没有写任何文件。")
        return 0
    print("NOTE: 只改了工作区,没有 git add 或提交。")
    rules = [os.path.relpath(p, root).replace(os.sep, "/") for p in changed]
    tips = [x for rel in rules if rel.startswith(rules_plan.RULES_DIR + "/") for x in hookup(root, rel)]
    if tips:
        print("NOTE: crosscheck 审查时会读 .agents/rules/,写代码的工具不会自动读。下面是接入建议,只打印、没有执行;"
              "命令在仓库根目录运行,做不做由用户决定。")
        for x in tips:
            print("SUGGEST: " + x)
    return 0


def hookup(root, rel):
    """让写代码的工具也读到 rel 这个规则文件的建议:仓库里有 .claude/、.cursor/ 时给软链接命令,有 AGENTS.md 时给一行
    普通 Markdown 链接(不用 @ 引用)。链接或这行已经有了的不再提示。"""
    name = os.path.splitext(os.path.basename(rel))[0]
    out = []
    for tool, link, note in ((".claude", ".claude/rules/%s.md" % name, ""),
                             (".cursor", ".cursor/rules/%s.mdc" % name, "  # Cursor 官方未写明软链接和多余字段的处理,请自行确认")):
        if os.path.isdir(os.path.join(root, tool)) and not os.path.lexists(os.path.join(root, link)):
            target = os.path.relpath(rel, os.path.dirname(link)).replace(os.sep, "/")
            out.append("mkdir -p %s && ln -s %s %s%s" % (shlex.quote(os.path.dirname(link)), shlex.quote(target), shlex.quote(link), note))
    agents = os.path.join(root, "AGENTS.md")
    if os.path.isfile(agents) and rel not in open(agents, encoding="utf-8").read():
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        scope = file_scope(os.path.join(root, rel), text, root)
        where = "所有文件" if scope == ["**"] else ", ".join(scope) or "(头部范围写错了,先修好)"
        out.append("在 AGENTS.md 里加一行:- [%s](%s):改 %s 时遵守" % (name, rel, where))
    return out


if __name__ == "__main__":
    sys.exit(main())
