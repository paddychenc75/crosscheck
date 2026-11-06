"""项目规则维护草稿:真实基线、受限文件清单、可确认的 diff 与按版本写入。

只用标准库。rules-draft.md 是机器清单的人读视图,由 propose/历史编译生成;
确认发生在对话中,expected_digest 仅防止写入未经预览的新版本。
写代码一侧的接入由脚本按规则变更生成:AGENTS.md 一句固定提示(Codex、Cursor 读),
.claude/rules/<相对路径>.md 指向规则文件的符号链接(Claude Code 按 paths 原生加载);同样进 diff 和确认指纹。
"""
import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import crlib
import project_rules

STATE = "rules-draft.json"
VIEW = "rules-draft.md"
VERSION = 2   # 0.3.13 起 AGENTS.md 只写提示、Claude 走 .claude/rules 链接;旧草稿须重新起草
RULES = ".agents/rules/"
LINKS = ".claude/rules/"
AGENTS = "AGENTS.md"
# 只在删除规则时扫描其中的手写引用,不再是可写目标
INSTRUCTION_FILES = (AGENTS, "CLAUDE.md", ".claude/CLAUDE.md")
AGENTS_SECTION = "代码约束"
AGENTS_HINT = "改代码前先查看 .agents/rules/ 下的规则文件:按文件头的 paths 判断是否适用于要改的文件,适用的读完并遵守。"
# 0.3.12 及更早写进「## 代码约束」的引导句和逐条受管索引;迁移时换成 AGENTS_HINT
LEGACY_INTROS = ("改动对应路径前先打开链接的规则文件读完并遵守。",
                 "改动下面某一行写的路径之前,先打开那一行链接的规则文件读完并遵守(由 crosscheck 从 MR 评论沉淀,审查时也按它检查)。")
LEGACY_INDEX = re.compile(r"- \[[^\]\r\n]+\]\(\.agents/rules/[^)\r\n]+\.md\):改 [^\r\n]* 时先读这个文件并遵守")
HINT_SOURCE = dict(type="inferred", basis="规则写入 .agents/rules,读 AGENTS.md 的宿主需要知道去哪里找")
LINK_SOURCE = dict(type="inferred", basis="Claude Code 按 .claude/rules 的 paths 原生加载规则")
LINK_KINDS = ("missing", "symlink", "file", "other")
HISTORY_LABELS = {"watch": "案例", "maintain": "维护已有规范"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def content_hash(text):
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(path):
    # 保留 CRLF;哈希、diff 和最终写入共用相同字节。
    return Path(path).read_bytes().decode("utf-8")


def git(root, *args):
    p = subprocess.run(["git", "-C", root, "rev-parse"] + list(args), capture_output=True, text=True)
    require(p.returncode == 0, "无法核对仓库身份: " + root)
    return os.path.realpath(os.path.join(root, p.stdout.strip()))


def identity(root):
    root = os.path.realpath(root)
    require(git(root, "--show-toplevel") == root, "--repo 必须是仓库根目录")
    return dict(root=root, git_dir=git(root, "--absolute-git-dir"), common_dir=git(root, "--git-common-dir"))


def is_link(rel):
    return isinstance(rel, str) and rel.startswith(LINKS)


def target(repo, rel, allow_agents=False, link=False):
    """普通目标及上级都不能是符号链接;link=True 只给 .claude/rules 链接用,仅最后一段允许是符号链接。"""
    require(isinstance(rel, str) and rel and "\\" not in rel and not any(c in rel for c in "\x00\r\n"), "目标路径不合法")
    parts = rel.split("/")
    require(not os.path.isabs(rel) and all(p not in ("", ".", "..") for p in parts), "目标必须是仓库内相对路径,不含 . 或 ..")
    require(not any(p.lower() == ".git" for p in parts), "目标不能指向 Git 元数据")
    if link:
        require(is_link(rel) and rel.endswith(".md"), "链接只允许 .claude/rules/**/*.md: " + rel)
    else:
        permitted = not is_link(rel) and ((rel.startswith(RULES) and rel.endswith(".md")) or parts[-1] == "REVIEW.md")
        require(permitted or (allow_agents and rel == AGENTS),
                "只维护 .agents/rules/**/*.md、REVIEW.md;AGENTS.md 只允许脚本加规则提示,.claude/rules 只允许脚本建链接")
    path = os.path.join(repo["root"], rel)
    current = repo["root"]
    for i, part in enumerate(parts):
        current = os.path.join(current, part)
        last = i == len(parts) - 1
        if link and last:
            break   # 链接本身的状态由 link_snapshot 记录
        require(not os.path.islink(current), "草稿目标不支持符号链接: " + rel)
        if os.path.lexists(current):
            require(os.path.isfile(current) if last else os.path.isdir(current), "目标或上级不是普通文件/目录: " + rel)
    # 链接的最后一段不跟随,只核对它所在目录
    real = os.path.join(os.path.realpath(os.path.dirname(path)), parts[-1]) if link else os.path.realpath(path)
    require(os.path.commonpath([repo["root"], real]) == repo["root"], "目标逃出仓库: " + rel)
    for metadata in (repo["git_dir"], repo["common_dir"]):
        require(os.path.commonpath([metadata, real]) != metadata, "目标指向 Git 元数据: " + rel)
    return path


def link_snapshot(repo, rel):
    path = target(repo, rel, link=True)
    if os.path.islink(path):
        return dict(content=None, sha256=None, link=os.readlink(path), kind="symlink")
    kind = "missing" if not os.path.lexists(path) else ("file" if os.path.isfile(path) else "other")
    return dict(content=None, sha256=None, link=None, kind=kind)


def snapshot(repo, rel):
    if is_link(rel):
        return link_snapshot(repo, rel)
    path = target(repo, rel, allow_agents=True)
    text = read(path) if os.path.exists(path) else None
    return dict(content=text, sha256=content_hash(text))


def load(work):
    data = json.loads(read(os.path.join(work, STATE)))
    require(isinstance(data, dict) and data.get("version") == VERSION, "草稿格式不受支持,请重新起草")
    return data


def fence(text, language=""):
    n = max([2] + [len(m.group(0)) for m in re.finditer(r"`+", text)]) + 1
    marker = "`" * n
    return marker + language + "\n" + text.rstrip("\n") + "\n" + marker


def file_diff(change, base):
    if change.get("operation") in ("link", "unlink"):
        return ""   # 链接没有正文,由 change_label 显示指向
    old, new = base["content"], change.get("content")
    rows = difflib.unified_diff((old or "").splitlines(True), (new or "").splitlines(True),
                               fromfile=change["path"] if old is not None else "/dev/null",
                               tofile=change["path"] if new is not None else "/dev/null")
    # difflib 不补无换行的行尾,加显示标记避免 -旧+新 粘连。
    return "".join(x if x.endswith("\n") else x + "\n\\ No newline at end of file\n" for x in rows)


def cell(text):
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def source_label(source):
    return "用户要求" if source["type"] == "user" else "材料推断(不因确认而变成历史共识)"


def operation(c, base):
    if c.get("operation") in ("link", "unlink"):
        return "新增链接" if c["operation"] == "link" else "删除链接"
    return "删除" if c["content"] is None else ("新增" if base["content"] is None else "修改")


def counts(data):
    """预演和视图共用的一句统计。"""
    links = [c for c in data["changes"] if c.get("operation") in ("link", "unlink")]
    files = len(data["changes"]) - len(links)
    added = sum(1 for c in links if c["operation"] == "link")
    parts = ["%d 个文件变更" % files]
    if added:
        parts.append("新增 %d 个链接" % added)
    if len(links) - added:
        parts.append("删除 %d 个链接" % (len(links) - added))
    return "、".join(parts) + ";待确认 %d 项" % len(data.get("pending", []))


def render(data):
    """读者顺序:一览 → 待确认 → 规则与 REVIEW.md 的 diff → 接入(AGENTS.md 提示、全部链接合在一处)→ 核对说明 → 参考材料。"""
    base = data["base"]
    links = [c for c in data["changes"] if c.get("operation") in ("link", "unlink")]
    hint = [c for c in data["changes"] if c["path"] == AGENTS]
    files = [c for c in data["changes"] if c not in links and c not in hint]
    lines = ["# 规则维护草稿", "", "- 目标:" + data.get("goal", "待整理"),
             "- 版本:%s" % data["revision"], "- 指纹:`%s`" % digest(data), "",
             "确认这份具体 diff 后才写工作区,不提交。补充要求或内容变化后重新预演、确认。",
             "此文件由脚本生成;对话修改由 agent 更新提案,不要直接改此视图。",
             "", "## 本次变更一览", ""]
    if data["changes"]:
        lines += ["| 文件 | 操作 | 写什么 | 来源 |", "| --- | --- | --- | --- |"]
        for c in files + hint:
            lines.append("| %s | %s | %s | %s |" % (cell(c["path"]), operation(c, base[c["path"]]), cell(c["reason"]),
                                                   "用户要求" if c["source"]["type"] == "user" else "材料推断"))
        for op in ("link", "unlink"):
            group = [c for c in links if c["operation"] == op]
            if group:
                lines.append("| %s | %s %d 个 | %s | 材料推断 |" % (
                    LINKS, "新增链接" if op == "link" else "删除链接", len(group),
                    "让 Claude Code 按 paths 加载规则" if op == "link" else "去掉指向已删除规则的链接"))
        lines += ["", counts(data) + "。"]
    else:
        lines += ["没有选定的文件变更。"]
    lines += ["", "## 待确认", ""]
    lines += ["- " + x for x in data.get("pending", [])] or ["- 无;仍需确认下面的具体改动。"]
    for c in files:
        source = c["source"]
        lines += ["", "## %s %s" % (operation(c, base[c["path"]]), c["path"]), "", "- 理由:" + c["reason"],
                  "- 来源:" + source_label(source)]
        lines += ["- 要求:" + source["request"]] if source["type"] == "user" else ["- 依据:" + source["basis"]]
        lines += ["", fence(file_diff(c, base[c["path"]]), "diff")]
    if hint or links:
        lines += ["", "## 接入", "", "- 来源:材料推断(不因确认而变成历史共识)"]
        for c in hint:
            lines += ["", "%s %s:%s" % (operation(c, base[c["path"]]), c["path"], c["reason"]), "",
                      fence(file_diff(c, base[c["path"]]), "diff")]
        if links:
            lines += ["", "Claude Code 按 .claude/rules 的 paths 原生加载规则,下面的链接都指向对应的 .agents/rules 文件:", "",
                      fence("\n".join(change_label(c, base[c["path"]]) for c in links))]
    if data.get("notes"):
        lines += ["", "## 核对说明", ""] + ["- " + x for x in data["notes"]]
    lines += history_lines(data.get("history"))
    # 版本只写进视图,不进 data 和指纹;插件升级后旧视图与 --check 不一致,按现有错误重新生成
    version = crlib.plugin_version()
    if version:
        lines += ["", "---", "", "生成:crosscheck " + version]
    return "\n".join(lines) + "\n"


def history_lines(past):
    """历史归纳中没有成为规则改动的主题,完整来源在本轮材料文件里。维护建议每个一行;
    案例只列写得出下一次改动(next_change)的,其余只给个数,全部案例仍在材料文件里。"""
    if not past:
        return []
    topics = past.get("topics", [])
    kinds = [(k, [t for t in topics if t["kind"] == k]) for k in ("maintain", "watch")]
    summary = "、".join("%s %d 个" % (HISTORY_LABELS[k], len(group)) for k, group in kinds if group)
    if past.get("excluded"):
        summary += ("、" if summary else "") + "已排除 %d 个" % past["excluded"]
    out = ["", "## 参考材料", "", "历史归纳里没有成为本次改动的主题:%s。只供参考,不需要逐条确认;观察编号、全部引文、"
           "规范原文和仓库核对见本轮材料 %s。" % (summary, past.get("materials", ""))]
    for kind, group in kinds:
        if not group:
            continue
        out += ["", "### %s" % HISTORY_LABELS[kind], ""]
        if kind == "watch":
            listed = [t for t in group if t.get("next_change")]
            out += ["下面只列写得出下一次改动的 %d 个;其余 %d 个只在本轮材料 %s。"
                    % (len(listed), len(group) - len(listed), past.get("materials", "")) if listed else
                    "没有写得出下一次改动的案例,全部 %d 个见本轮材料 %s。" % (len(group), past.get("materials", ""))]
            if listed:
                out.append("")
            group = listed
        for t in group:
            refs = list(t.get("links", []))
            if t.get("more"):
                refs.append("另有 %d 个 MR" % t["more"])
            if t.get("feedback"):
                refs.append("判断记录 %d 条" % t["feedback"])
            if kind == "maintain" and t.get("sources"):
                refs.append("现有规范:" + ";".join(t["sources"]))
            reason = "下一次:%s;缺:%s" % (t["next_change"], t["reason"]) if kind == "watch" else t["reason"]
            out.append("- %s — %s%s" % (t["title"], reason, "(%s)" % ";".join(refs) if refs else ""))
    return out


def atomic(path, text, before=None, verify=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 写记录目录的例外:已校验的工作区目标,或调用方 record_write 包围的 work 文件
    fd, tmp = tempfile.mkstemp(prefix=".crosscheck-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        if os.path.exists(path):
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        if verify:
            verify()
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(work, data):
    # 使用共享记录权限边界;不按 storage_mode 禁止用户确认后的仓库写入。
    for name, text in ((STATE, json.dumps(data, ensure_ascii=False, indent=2) + "\n"), (VIEW, render(data))):
        with crlib.record_write(os.path.join(work, name)):
            atomic(os.path.join(work, name), text)


def capture(work, root, paths, refresh_bindings=False, expected=None):
    repo = identity(root)
    if os.path.exists(os.path.join(work, STATE)):
        data = load(work)
        require(data["repo"] == repo, "草稿属于另一仓库")
        validate(work, data, materials=not refresh_bindings)
    else:
        data = dict(version=VERSION, revision=0, repo=repo, base={}, goal="待整理", changes=[], pending=[], notes=[], bindings={})
    for rel in paths:
        require(not is_link(rel), ".claude/rules 链接由脚本按规则变更自动生成,不用 --capture: " + str(rel))
        current = snapshot(repo, rel)
        if expected is not None and rel in expected:
            require(current["content"] == expected[rel], "构建时目标变化: " + rel)
        if rel not in data["base"]:
            data["base"][rel] = current
    data["revision"] += 1
    save(work, data)
    return data


def scope(rel, text):
    rule = project_rules.describe(rel, text)
    require(not rule["issues"], "%s 的头部范围有误:%s" % (rel, ";".join(rule["issues"])))
    return rule["patterns"]


def hint_content(original):
    """AGENTS.md 只保证有一句通用提示;0.3.12 的受管索引行和引导句换成这一句,手写内容原样保留。"""
    rows = original.splitlines(True)
    bare = [x.rstrip("\r\n") for x in rows]
    fenced = crlib.fence_mask(bare)
    _, body = crlib.parse_frontmatter(original)
    body_start = len(original.split("\n")) - len(body.split("\n"))
    live = {i for i in range(len(rows)) if i >= body_start and not fenced[i]}
    present = any(bare[i].strip() == AGENTS_HINT for i in live)
    heads = sorted(i for i in live if rows[i].startswith("## "))
    matches = [i for i in heads if rows[i][3:].strip().rstrip(":：") == AGENTS_SECTION]
    require(len(matches) <= 1 or present, AGENTS + " 有多个代码约束章节,请先明确提示位置")
    newline = "\r\n" if "\r\n" in original else "\n"
    if not matches:
        if present:
            return original
        return (original + (newline if original and not original.endswith("\n") else "") + (newline if original else "")
                + "## " + AGENTS_SECTION + newline * 2 + AGENTS_HINT + newline)
    if len(matches) > 1:
        return original   # 已有提示,不猜要迁移哪一节
    start = matches[0]
    end = next((i for i in heads if i > start), len(rows))
    legacy = [i for i in range(start + 1, end) if i in live and
              (LEGACY_INDEX.fullmatch(bare[i]) or bare[i].strip() in LEGACY_INTROS)]
    if not legacy:
        if present:
            return original
        last = max(i for i in range(start, end) if bare[i].strip())
        head = rows[:last + 1]
        if not head[-1].endswith("\n"):
            head[-1] += newline
        return "".join(head + [newline, AGENTS_HINT + newline] + rows[last + 1:])
    replace = {} if present else {legacy[0]: AGENTS_HINT + rows[legacy[0]][len(bare[legacy[0]]):]}
    drop = set(legacy) - set(replace)
    out, dropped = [], False
    for i, row in enumerate(rows):
        if i in replace:
            out.append(replace[i])
            dropped = False
        elif i in drop:
            dropped = True
        elif dropped and not row.strip() and out and not out[-1].strip():
            continue   # 删掉旧索引后留下的连续空行只保留一个
        else:
            out.append(row)
            dropped = dropped and not row.strip()
    while dropped and len(out) > 1 and not out[-1].strip():
        out.pop()   # 旧索引在文件末尾时,不留下原来没有的结尾空行
    return "".join(out)


def rule_link(rel):
    """.agents/rules/<p>.md -> (.claude/rules/<p>.md, 相对链接目标)。"""
    link = LINKS + rel[len(RULES):]
    return link, os.path.relpath(rel, os.path.dirname(link)).replace(os.sep, "/")


def linked_rule(link):
    return RULES + link[len(LINKS):]


def resolves_to(root, link, dest, rel):
    """链接 link(仓库相对)的目标 dest 是否就是规则 rel;相对目标按字面解析,不要求规则已存在。"""
    path = os.path.join(root, link)
    if dest is None:
        return False
    if os.path.normpath(os.path.join(os.path.dirname(path), dest)) == os.path.join(root, rel):
        return True
    return os.path.realpath(path) == os.path.realpath(os.path.join(root, rel))


def link_blocker(root, link):
    """链接上级是符号链接(例如共享规则目录)或不是目录时返回原因;这种情况不替用户改。"""
    current = root
    for part in link.split("/")[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return "上级 %s 是符号链接" % os.path.relpath(current, root)
        if os.path.lexists(current) and not os.path.isdir(current):
            return "上级 %s 不是目录" % os.path.relpath(current, root)
    return None


def stale_references(repo, deleted, after):
    """指令文件里仍引用已删除规则的行只列待确认,不自动改这些文件。"""
    root, warnings = repo["root"], []
    for rel in INSTRUCTION_FILES:
        if rel in after:
            text = after[rel]
        else:
            path = os.path.join(root, rel)
            parts = rel.split("/")
            if any(os.path.islink(os.path.join(root, *parts[:i + 1])) for i in range(len(parts))) or not os.path.isfile(path):
                continue
            text = read(path)
        refs = {os.path.normpath(os.path.join(os.path.dirname(rel), x)).replace(os.sep, "/")
                for x in project_rules._local_references(text or "")}
        for gone in deleted:
            if gone in refs:
                warnings.append("%s 仍引用已删除的规则 %s,请确认是否改掉这处引用" % (rel, gone))
    return warnings


def hookup(data):
    """按本草稿的规则变更生成 AGENTS.md 提示和 .claude/rules 链接;不能安全处理的只列待确认。"""
    repo, root, base = data["repo"], data["repo"]["root"], data["base"]
    rules = {c["path"]: c["content"] for c in data["changes"] if c["path"].startswith(RULES)}
    written = [p for p, text in rules.items() if text is not None]
    deleted = [p for p, text in rules.items() if text is None]
    live = written + [p for p in data.get("rule_targets", []) if p not in rules and base[p]["content"] is not None]
    changes, pending, after = [], [], {}
    if written:
        try:
            current = base[AGENTS] if AGENTS in base else snapshot(repo, AGENTS)
        except ValueError as error:   # 例如 AGENTS.md 是指向其他入口的符号链接
            current = None
            why = ("是符号链接,脚本不改它指向的文件" if os.path.islink(os.path.join(root, AGENTS))
                   else "脚本不能安全修改(%s)" % error)
            pending.append("%s %s;如需让 Codex、Cursor 找到规则,请在它实际所在的文件里找「## %s」节手动加一句:%s;没有这一节就在文件末尾新建"
                           % (AGENTS, why, AGENTS_SECTION, AGENTS_HINT))
        if current is not None:
            content = hint_content(current["content"] or "")
            if content != (current["content"] or ""):
                base.setdefault(AGENTS, current)
                after[AGENTS] = content
                changes.append(dict(path=AGENTS, operation="hint", content=content,
                                    reason="提示读 AGENTS.md 的宿主去 .agents/rules 按 paths 找适用规则", source=HINT_SOURCE))
    for rel in dict.fromkeys(live):
        link, dest = rule_link(rel)
        path = os.path.join(root, link)
        blocked = link_blocker(root, link)
        if blocked:
            pending.append("%s 的%s,没有建 Claude Code 规则链接;请确认是否需要另行接入 %s" % (link, blocked, rel))
        elif os.path.islink(path) and resolves_to(root, link, os.readlink(path), rel):
            continue
        elif os.path.lexists(path):
            pending.append("%s 已存在且不是指向 %s 的链接,没有改动;请确认 Claude Code 是否需要加载这条规则" % (link, rel))
        else:
            base[link] = link_snapshot(repo, link)
            changes.append(dict(path=link, operation="link", link=dest,
                                reason="让 Claude Code 按 paths 加载 " + rel, source=LINK_SOURCE))
    for rel in deleted:
        link, _ = rule_link(rel)
        path = os.path.join(root, link)
        if not os.path.lexists(path):
            continue
        blocked = link_blocker(root, link)
        if not blocked and os.path.islink(path) and resolves_to(root, link, os.readlink(path), rel):
            base[link] = link_snapshot(repo, link)
            changes.append(dict(path=link, operation="unlink", link=base[link]["link"],
                                reason="规则 %s 已删除,去掉指向它的链接" % rel, source=LINK_SOURCE))
        else:
            pending.append("%s 仍存在且不是指向已删除规则 %s 的链接%s,没有改动;请确认" % (
                link, rel, "(" + blocked + ")" if blocked else ""))
    return changes, pending + stale_references(repo, deleted, after)


def review_settings(rel, text):
    """沿用实际解析器,拒绝它会忽略的未知设置和错误类型。"""
    fm, _ = crlib.parse_frontmatter(text)
    if not text.startswith("---"):
        return
    rows = text.splitlines()[1:]
    keys = set()
    for row in rows:
        if row.strip() == "---":
            break
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        require(":" in row or row.lstrip().startswith("- "), rel + " 头部包含不能解析的行: " + row)
        if row[:1] not in (" ", "\t"):
            key = row.split(":", 1)[0].strip().strip("\"'")
            require(":" in row and key in {"skip", "limits", "depth"}, rel + " 头部只支持 skip、limits、depth")
            require(key not in keys, rel + " 头部设置重复: " + key)
            keys.add(key)
            raw = crlib._strip_comment(row.split(":", 1)[1].strip())
            if key == "limits" and raw.startswith("{"):
                require(raw.endswith("}"), "limits 的映射没有结束")
                entries = crlib._split_top(raw[1:-1])
                require(all(":" in item for item in entries), "limits 含不能解析的条目")
                require(len({item.split(":", 1)[0].strip() for item in entries}) == len(entries), "limits 含重复类别")
    require(not set(fm) - {"skip", "limits", "depth"}, rel + " 头部只支持 skip、limits、depth")
    require(rel == "REVIEW.md" or not fm, "子目录 REVIEW.md 不读取头部设置,请移到根目录")
    if "skip" in fm:
        value = fm["skip"]
        require(value is None or isinstance(value, (list, str)), "skip 应为路径列表或一个路径")
        if value is not None:
            project_rules.patterns(value)
    if "limits" in fm:
        value = fm["limits"]
        require(value is None or isinstance(value, (dict, list)), "limits 应为条数映射")
        limits = crlib.as_map(value)
        require(value is None or len(limits) == len(value), "limits 含不能解析或重复的条目")
        require(not set(limits) - {"defects", "intent", "rules", "critical", "major", "minor"}, "limits 含未知类别")
        require(all(re.fullmatch(r"\d+", str(v)) for v in limits.values()), "limits 的条数须为非负整数")
    if "depth" in fm:
        require(isinstance(fm["depth"], str) and fm["depth"].lower() in ("quick", "standard", "deep"), "depth 只能是 quick、standard、deep")


def source_valid(source):
    require(isinstance(source, dict) and source.get("type") in ("user", "inferred"), "来源须为 user 或 inferred")
    field = "request" if source["type"] == "user" else "basis"
    require(isinstance(source.get(field), str) and source[field].strip(), "来源缺少 " + field)


def history_valid(past):
    if past is None:
        return
    require(isinstance(past, dict) and isinstance(past.get("topics"), list), "历史材料格式不合法")
    for t in past["topics"]:
        require(isinstance(t, dict) and t.get("kind") in HISTORY_LABELS
                and all(isinstance(t.get(k), str) and t[k] for k in ("title", "reason")), "历史主题格式不合法")


def validate(work, data, view=False, materials=True):
    require(isinstance(data.get("revision"), int) and data["revision"] > 0, "草稿版本不合法")
    require(identity(data["repo"]["root"]) == data["repo"], "仓库身份已变化,请重新起草")
    require(isinstance(data.get("base"), dict) and isinstance(data.get("changes"), list), "草稿缺少基线/变更清单")
    for name in ("pending", "notes"):
        require(isinstance(data.get(name), list) and all(isinstance(s, str) for s in data[name]), name + " 应为文字数组")
    history_valid(data.get("history"))
    root = data["repo"]["root"]
    for rel, base in data["base"].items():
        require(isinstance(base, dict) and (base.get("content") is None or isinstance(base["content"], str)), "基线格式不合法: " + rel)
        require(base.get("sha256") == content_hash(base.get("content")), "基线指纹不一致: " + rel)
        if is_link(rel):
            require(base.get("kind") in LINK_KINDS and base.get("content") is None, "链接基线格式不合法: " + rel)
        require(snapshot(data["repo"], rel) == base, "目标在起草后变化: %s;保留现有修改,用新工作目录重新起草、预演并确认" % rel)
    paths = list(data["base"])
    require(not any(a != b and b.startswith(a + "/") for a in paths for b in paths), "草稿目标互相占用上级目录")
    require(isinstance(data.get("rule_targets", []), list), "rule_targets 应为规则路径数组")
    for rel in data.get("rule_targets", []):
        require(isinstance(rel, str) and rel.startswith(RULES) and rel in data["base"], "确认的规则目标没有捕获基线")
        target(data["repo"], rel)
    rules = {c.get("path"): c.get("content") for c in data["changes"]
             if isinstance(c, dict) and c.get("operation") in ("write", "delete")}
    seen = set()
    for c in data["changes"]:
        require(isinstance(c, dict) and isinstance(c.get("path"), str), "文件变更格式不合法")
        rel, op = c["path"], c.get("operation")
        require(rel in data["base"], "目标未捕获真实基线: " + rel)
        require(rel not in seen, "重复变更目标: " + rel)
        seen.add(rel)
        require(isinstance(c.get("reason"), str) and c["reason"].strip(), "文件变更缺少理由: " + rel)
        source_valid(c.get("source"))
        base = data["base"][rel]
        if op in ("link", "unlink"):
            # 链接只能由脚本按规则变更生成;逐项按基线重算,篡改的指向或时机都拒绝。
            target(data["repo"], rel, link=True)
            require("content" not in c and isinstance(c.get("link"), str), "链接变更只写 link 字段: " + rel)
            rule = linked_rule(rel)
            if op == "link":
                require(base["kind"] == "missing", "链接位置已有内容,不能覆盖: " + rel)
                require(c["link"] == rule_link(rule)[1], "链接只能指向对应的 .agents/rules 规则: " + rel)
                require(rules.get(rule, data["base"].get(rule, {}).get("content")) is not None, "链接的规则不存在: " + rule)
            else:
                require(rule in rules and rules[rule] is None, "只在删除对应规则时去掉链接: " + rel)
                require(base["kind"] == "symlink" and c["link"] == base["link"] and resolves_to(root, rel, base["link"], rule),
                        "只删除指向已删除规则的符号链接: " + rel)
            continue
        target(data["repo"], rel, allow_agents=op == "hint")
        require("content" in c and (c["content"] is None or isinstance(c["content"], str)), "拟写内容必须是文字,删除用 null: " + rel)
        if rel == AGENTS:
            require(op == "hint" and isinstance(c["content"], str), AGENTS + " 只允许脚本加 .agents/rules 提示")
            require(any(text is not None for p, text in rules.items() if p.startswith(RULES)), AGENTS + " 提示只随规则写入生成")
            require(c["content"] == hint_content(base["content"] or ""), AGENTS + " 超出受限提示变化")
            continue
        require(op == ("delete" if c["content"] is None else "write"), "删除必须显式 operation=delete: " + rel)
        if c["content"] is None:
            require(base["content"] is not None, "不能删除不存在的文件: " + rel)
        elif c["content"]:
            require("\x00" not in c["content"] and not crlib.frontmatter_unclosed(c["content"]), "拟写内容含 NUL 或未闭合头部: " + rel)
            if rel.startswith(RULES):
                scope(rel, c["content"])
            elif os.path.basename(rel) == "REVIEW.md":
                review_settings(rel, c["content"])
    require(all(rel in seen for rel in data["base"] if is_link(rel)), "链接基线只随脚本生成的链接变更出现")
    for name, expected in (data.get("bindings", {}) if materials else {}).items():
        require(name in ("candidates.json", "mined-candidates.md", "dropped.json", "snapshot.json", "rules-sources.json"), "不支持的材料绑定: " + name)
        require(content_hash(read(os.path.join(work, name))) == expected, "来源材料已变化: %s;重新编译草稿并预演确认" % name)
    if view:
        require(read(os.path.join(work, VIEW)) == render(data), "rules-draft.md 与机器清单不一致;请更新提案并重新生成草稿")


def propose(work, request, bindings=None):
    data = load(work)
    # 链接及其基线随每次提案重算;旧的先拿掉,别人后来建好的链接不挡新提案。
    data["base"] = {rel: base for rel, base in data["base"].items() if not is_link(rel)}
    data["changes"] = [c for c in data["changes"] if isinstance(c, dict) and c.get("operation") in ("write", "delete")]
    validate(work, data, materials=bindings is None)
    require(isinstance(request, dict) and isinstance(request.get("goal"), str) and request["goal"].strip(), "提案须写 goal")
    require(isinstance(request.get("changes"), list), "提案须写 changes 数组")
    retired = [k for k in ("agents_links", "instruction_files", "agents_source") if k in request]
    require(not retired, "提案不再接受 %s:0.3.13 起写代码一侧的接入改为 AGENTS.md 一句提示加 .claude/rules 链接,"
                         "由脚本按规则变更自动生成" % "、".join(retired))
    require(bindings is not None or "history" not in request, "history 由 rules-write.py --prepare 按历史材料生成,提案里不写")
    changes = copy.deepcopy(request["changes"])
    for c in changes:
        require(isinstance(c, dict), "文件变更格式不合法")
        require(c.get("path") not in INSTRUCTION_FILES and not is_link(c.get("path")),
                "AGENTS.md/CLAUDE.md 和 .claude/rules 不接受正文提案,提示和链接由脚本按规则变更生成")
        require(c.get("operation") in ("write", "delete"), "提案的文件变更只用 operation=write 或 delete: " + str(c.get("path")))
    data.update(goal=request["goal"], changes=changes, pending=list(request.get("pending", [])), notes=request.get("notes", []),
                bindings=bindings if bindings is not None else data.get("bindings", {}), revision=data["revision"] + 1,
                rule_targets=request.get("rule_targets", []))
    if bindings is not None:   # 历史编译重新生成;agent 的后续提案沿用,不需要抄回
        data["history"] = request.get("history")
    if data.get("history") is None:
        data.pop("history", None)
    require(isinstance(data["rule_targets"], list) and all(
        isinstance(p, str) and p.startswith(RULES) and p in data["base"] for p in data["rule_targets"]), "确认的规则目标没有捕获基线")
    for c in changes:
        require(isinstance(c.get("path"), str) and c["path"] in data["base"], "目标未捕获真实基线: " + str(c.get("path")))
    generated, pending = hookup(data)
    data["changes"] += generated
    data["pending"] += pending
    validate(work, data)
    save(work, data)
    return data


def applied_state(change):
    """change 应用后目标应有的快照:链接比对指向,普通文件比对内容。"""
    if change.get("operation") == "link":
        return dict(content=None, sha256=None, link=change["link"], kind="symlink")
    if change.get("operation") == "unlink":
        return dict(content=None, sha256=None, link=None, kind="missing")
    return dict(content=change["content"], sha256=content_hash(change["content"]))


def already_applied(work, data):
    receipt = os.path.join(work, "rules-draft-applied.json")
    if not os.path.exists(receipt):
        return False
    saved = json.loads(read(receipt))
    if saved.get("digest") != digest(data):
        return False
    require(identity(data["repo"]["root"]) == data["repo"], "仓库身份已变化")
    for c in data["changes"]:
        now = snapshot(data["repo"], c["path"])
        require(now == applied_state(c), "本草稿已应用,但文件后来又变化: %s;请另起草稿" % c["path"])
    print("APPLIED: 本草稿已应用,没有重复写入。")
    return True


def change_label(change, base):
    if change.get("operation") == "link":
        return "LINK: %s -> %s" % (change["path"], change["link"])
    if change.get("operation") == "unlink":
        return "UNLINK: " + change["path"]
    if change["content"] is None:
        return "DELETE: " + change["path"]
    added = len(change["content"].splitlines()) - len((base["content"] or "").splitlines())
    return "WRITE: %s %+d行" % (change["path"], added)


def notices(data):
    for message in data["pending"]:
        print("PENDING: " + message)
    for message in data["notes"]:
        print(message if message.startswith(("SKIP:", "WARN:")) else "NOTE: " + message)


def check(work):
    data = load(work)
    if already_applied(work, data):
        print("DIGEST: " + digest(data))
        return data
    validate(work, data, view=True)
    for c in data["changes"]:
        print(change_label(c, data["base"][c["path"]]))
        print(file_diff(c, data["base"][c["path"]]), end="")
    notices(data)
    if not data["changes"]:
        print("NOTE: 没有选定的文件变更。")
    print("SUMMARY: " + counts(data))
    print("DIGEST: " + digest(data))
    return data


def place_link(path, dest, verify):
    """先在同目录建临时链接,核对基线后原子落位;不覆盖已有内容由 verify 保证。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 写记录目录的例外:已校验的工作区 .claude/rules 链接位置,上级不是符号链接
    tmp = os.path.join(os.path.dirname(path), ".crosscheck-%s-%s" % (os.getpid(), os.path.basename(path)))
    os.symlink(dest, tmp)
    try:
        verify()
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def apply(work, expected):
    require(isinstance(expected, str) and expected, "写入缺少 --expected-digest;先预演,用户确认具体 diff 后再传入该指纹")
    data = load(work)
    require(digest(data) == expected, "草稿版本/内容已变化或确认指纹错误;重新预演并让用户确认")
    if already_applied(work, data):
        return data
    validate(work, data, view=True)  # 所有文件、来源和基线检查完才开始写。
    completed = []
    try:
        for c in data["changes"]:
            rel, op = c["path"], c.get("operation")
            def unchanged():
                require(snapshot(data["repo"], rel) == data["base"][rel], "写入前目标变化: " + rel)
            if op == "link":
                place_link(target(data["repo"], rel, link=True), c["link"], unchanged)
            elif op == "unlink":
                path = target(data["repo"], rel, link=True)
                unchanged()
                os.unlink(path)   # 基线已确认是符号链接,只删链接本身
            elif c["content"] is None:
                path = target(data["repo"], rel, allow_agents=True)
                unchanged()
                os.unlink(path)
            else:
                atomic(target(data["repo"], rel, allow_agents=True), c["content"], verify=unchanged)
            completed.append(rel)
            print(change_label(c, data["base"][rel]))
    except (OSError, ValueError) as error:
        remaining = [c["path"] for c in data["changes"] if c["path"] not in completed]
        raise ValueError("写入中断;已完成:%s;未完成:%s。%s。保留已写结果,重新捕获当前文件、起草并确认剩余改动" % (
            ", ".join(completed) or "无", ", ".join(remaining) or "无", error))
    if completed:
        try:
            receipt = os.path.join(work, "rules-draft-applied.json")
            with crlib.record_write(receipt):
                atomic(receipt, json.dumps(dict(digest=expected, completed=completed), ensure_ascii=False) + "\n")
            with crlib.record_write(os.path.join(work, VIEW)):
                atomic(os.path.join(work, VIEW), "已应用到工作区;以下保留本轮确认内容与当时待确认项。\n\n" + render(data))
        except (OSError, ValueError, SystemExit) as error:
            print("NOTE: 工作区已全部写入,完成状态记录失败:%s;请核对实际文件,不要重复套用旧基线。" % error)
    print("NOTE: 只改了工作区,没有 git add 或提交。" if completed else "NOTE: 没有写任何文件。")
    notices(data)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description="统一项目规则维护草稿")
    ap.add_argument("work")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true", help="从磁盘冻结待维护文件;重复调用仅增加目标,不刷新旧基线")
    group.add_argument("--propose", metavar="JSON", help="按已捕获基线生成草稿")
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    ap.add_argument("--repo")
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--expected-digest")
    args = ap.parse_args(argv)
    try:
        if args.capture:
            root = args.repo or crlib.read_kv(os.path.join(args.work, "meta.txt")).get("repo_root")
            require(root and args.path, "--capture 需要仓库根目录和至少一个 --path")
            capture(args.work, root, args.path)
        elif args.propose:
            propose(args.work, json.loads(read(args.propose)))
        elif args.check:
            check(args.work)
        else:
            apply(args.work, args.expected_digest)
        if args.capture or args.propose:
            print("RULES_DRAFT: " + os.path.join(args.work, VIEW))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print("ERROR: " + str(error))
        return 2
