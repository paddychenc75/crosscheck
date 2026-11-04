"""项目规则维护草稿:真实基线、受限文件清单、可确认的 diff 与按版本写入。

只用标准库。rules-draft.md 是机器清单的人读视图,由 propose/历史编译生成;
确认发生在对话中,expected_digest 仅防止写入未经预览的新版本。
"""
import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

import crlib
import project_rules

STATE = "rules-draft.json"
VIEW = "rules-draft.md"
VERSION = 1


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


def target(repo, rel, allow_agents=False):
    require(isinstance(rel, str) and rel and "\\" not in rel and not any(c in rel for c in "\x00\r\n"), "目标路径不合法")
    parts = rel.split("/")
    require(not os.path.isabs(rel) and all(p not in ("", ".", "..") for p in parts), "目标必须是仓库内相对路径,不含 . 或 ..")
    require(not any(p.lower() == ".git" for p in parts), "目标不能指向 Git 元数据")
    permitted = (rel.startswith(".agents/rules/") and rel.endswith(".md")) or parts[-1] == "REVIEW.md"
    require(permitted or (allow_agents and rel == "AGENTS.md"), "只维护 .agents/rules/**/*.md、REVIEW.md;AGENTS.md 仅允许脚本接入规则索引")
    path = os.path.join(repo["root"], rel)
    current = repo["root"]
    for i, part in enumerate(parts):
        current = os.path.join(current, part)
        require(not os.path.islink(current), "草稿目标不支持符号链接: " + rel)
        if os.path.lexists(current):
            require(os.path.isdir(current) if i < len(parts) - 1 else os.path.isfile(current), "目标或上级不是普通文件/目录: " + rel)
    real = os.path.realpath(path)
    require(os.path.commonpath([repo["root"], real]) == repo["root"], "目标逃出仓库: " + rel)
    for metadata in (repo["git_dir"], repo["common_dir"]):
        require(os.path.commonpath([metadata, real]) != metadata, "目标指向 Git 元数据: " + rel)
    return path


def snapshot(repo, rel):
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
    old, new = base["content"], change.get("content")
    rows = difflib.unified_diff((old or "").splitlines(True), (new or "").splitlines(True),
                               fromfile=change["path"] if old is not None else "/dev/null",
                               tofile=change["path"] if new is not None else "/dev/null")
    # difflib 不补无换行的行尾,加显示标记避免 -旧+新 粘连。
    return "".join(x if x.endswith("\n") else x + "\n\\ No newline at end of file\n" for x in rows)


def render(data):
    lines = ["# 规则维护草稿", "", "- 目标:" + data.get("goal", "待整理"),
             "- 版本:%s" % data["revision"], "- 指纹:`%s`" % digest(data), "",
             "确认这份具体 diff 后才写工作区,不提交。补充要求或内容变化后重新预演、确认。",
             "此文件由脚本生成;对话修改由 agent 更新提案,不要直接改此视图。"]
    for item in data.get("notes", []):
        lines += ["", "- 复核:" + item]
    lines += ["", "## 待确认", ""]
    lines += ["- " + x for x in data.get("pending", [])] or ["- 无;仍需确认下面的具体改动。"]
    for c in data["changes"]:
        source = c["source"]
        label = "用户要求" if source["type"] == "user" else "材料推断(不因确认而变成历史共识)"
        op = "删除" if c["content"] is None else ("新增" if data["base"][c["path"]]["content"] is None else "修改")
        lines += ["", "## %s %s" % (op, c["path"]), "", "- 理由:" + c["reason"], "- 来源:" + label]
        if source["type"] == "user":
            lines += ["- 要求:" + source["request"]]
        else:
            lines += ["- 依据:" + source["basis"]]
        lines += ["", fence(file_diff(c, data["base"][c["path"]]), "diff")]
    if not data["changes"]:
        lines += ["", "没有选定的文件变更。"]
    return "\n".join(lines) + "\n"


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


def index_content(data, links, warnings=None):
    """只同步明确列出的规则索引;识别不了的手写引用保留并列待确认。"""
    require("AGENTS.md" in data["base"], "接入索引前须 --capture --path AGENTS.md")
    original = data["base"]["AGENTS.md"]["content"] or ""
    after = {c["path"]: c["content"] for c in data["changes"] if c["path"] != "AGENTS.md"}
    rows = original.splitlines(True)
    fenced = crlib.fence_mask([x.rstrip("\r\n") for x in rows])
    _, body = crlib.parse_frontmatter(original)
    body_start = len(original.split("\n")) - len(body.split("\n"))
    heads = [i for i, row in enumerate(rows) if i >= body_start and not fenced[i] and row.startswith("## ")]
    matches = [i for i in heads if rows[i][3:].strip().rstrip(":：") == "代码约束"]
    require(len(matches) <= 1, "AGENTS.md 有多个代码约束章节,请先明确接入位置")
    start = matches[0] if matches else -1
    end = next((i for i in heads if i > start), len(rows)) if matches else len(rows)
    managed = re.compile(r"^- \[[^\]\r\n]+\]\((\.agents/rules/[^)\r\n]+\.md)\):改 [^\r\n]* 时先读这个文件并遵守(?:\r?\n)?$")
    newline = "\r\n" if "\r\n" in original else "\n"
    items, replacements = [], {}
    for rel in dict.fromkeys(links):
        require(isinstance(rel, str) and rel.startswith(".agents/rules/") and rel.endswith(".md"), "AGENTS 索引只允许 .agents/rules 下的 Markdown")
        require(rel in data["base"], "接入索引前须捕获规则文件基线: " + rel)
        target(data["repo"], rel)
        content = after.get(rel, data["base"][rel]["content"])
        require(isinstance(content, str) or (rel in after and content is None), "不能接入不存在的规则: " + rel)
        known, other = [], []
        for i, row in enumerate(rows):
            if fenced[i]:
                continue
            refs = {os.path.normpath(x).replace(os.sep, "/") for x in project_rules._local_references(row)}
            if rel not in refs:
                continue
            match = managed.fullmatch(row)
            if matches and start < i < end and match and match.group(1) == rel:
                known.append(i)
            else:
                other.append(i)
        if other or len(known) > 1:
            if warnings is not None:
                warnings.append("AGENTS.md 对 %s 有不能安全同步的手写或重复引用;已保留,请确认其范围/删除后的链接" % rel)
        if len(known) > 1:
            continue
        if content is None:
            for i in known:
                replacements[i] = ""
            continue
        patterns = scope(rel, content)
        where = "所有文件" if patterns == ["**"] else ", ".join(patterns)
        line = "- [%s](%s):改 %s 时先读这个文件并遵守" % (Path(rel).stem, rel, where)
        if known:
            i = known[0]
            ending = "\r\n" if rows[i].endswith("\r\n") else ("\n" if rows[i].endswith("\n") else "")
            replacements[i] = line + ending
        elif not other:
            items.append(line)
    changed = [replacements.get(i, row) for i, row in enumerate(rows)]
    if not items:
        return "".join(changed)
    body = newline.join(items) + newline
    if matches:
        prefix = "".join(changed[:end])
        return prefix + (newline if prefix and not prefix.endswith("\n") else "") + body + "".join(changed[end:])
    return original + (newline if original and not original.endswith("\n") else "") + (newline if original else "") + "## 代码约束" + newline * 2 + "改动对应路径前先打开链接的规则文件读完并遵守。" + newline * 2 + body


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


def validate(work, data, view=False, materials=True):
    require(isinstance(data.get("revision"), int) and data["revision"] > 0, "草稿版本不合法")
    require(identity(data["repo"]["root"]) == data["repo"], "仓库身份已变化,请重新起草")
    require(isinstance(data.get("base"), dict) and isinstance(data.get("changes"), list), "草稿缺少基线/变更清单")
    for name in ("pending", "notes"):
        require(isinstance(data.get(name), list) and all(isinstance(s, str) for s in data[name]), name + " 应为文字数组")
    for rel, base in data["base"].items():
        require(isinstance(base, dict) and (base.get("content") is None or isinstance(base["content"], str)), "基线格式不合法: " + rel)
        require(base.get("sha256") == content_hash(base.get("content")), "基线指纹不一致: " + rel)
        require(snapshot(data["repo"], rel) == base, "目标在起草后变化: %s;保留现有修改,用新工作目录重新起草、预演并确认" % rel)
    paths = list(data["base"])
    require(not any(a != b and b.startswith(a + "/") for a in paths for b in paths), "草稿目标互相占用上级目录")
    seen = set()
    for c in data["changes"]:
        require(isinstance(c, dict) and isinstance(c.get("path"), str), "文件变更格式不合法")
        rel = c["path"]
        require(rel in data["base"], "目标未捕获真实基线: " + rel)
        require(rel not in seen, "重复变更目标: " + rel)
        seen.add(rel)
        target(data["repo"], rel, allow_agents=c.get("operation") == "index")
        require(isinstance(c.get("reason"), str) and c["reason"].strip(), "文件变更缺少理由: " + rel)
        source_valid(c.get("source"))
        require("content" in c and (c["content"] is None or isinstance(c["content"], str)), "拟写内容必须是文字,删除用 null: " + rel)
        if rel == "AGENTS.md":
            require(c.get("operation") == "index" and isinstance(c.get("links"), list), "AGENTS.md 只允许接入规则索引")
            require(c["content"] == index_content(data, c["links"]), "AGENTS.md 超出受限索引变化")
        else:
            require(c.get("operation") == ("delete" if c["content"] is None else "write"), "删除必须显式 operation=delete: " + rel)
        if c["content"] is None:
            require(data["base"][rel]["content"] is not None, "不能删除不存在的文件: " + rel)
        elif c["content"]:
            require("\x00" not in c["content"] and not crlib.frontmatter_unclosed(c["content"]), "拟写内容含 NUL 或未闭合头部: " + rel)
            if rel.startswith(".agents/rules/"):
                scope(rel, c["content"])
            elif os.path.basename(rel) == "REVIEW.md":
                review_settings(rel, c["content"])
    require(isinstance(data.get("rule_targets", []), list), "rule_targets 应为规则路径数组")
    for rel in data.get("rule_targets", []):
        require(isinstance(rel, str) and rel.startswith(".agents/rules/") and rel in data["base"], "确认的规则目标没有捕获基线")
        target(data["repo"], rel)
    for name, expected in (data.get("bindings", {}) if materials else {}).items():
        require(name in ("candidates.json", "mined-candidates.md"), "不支持的材料绑定: " + name)
        require(content_hash(read(os.path.join(work, name))) == expected, "来源材料已变化: %s;重新编译草稿并预演确认" % name)
    if view:
        require(read(os.path.join(work, VIEW)) == render(data), "rules-draft.md 与机器清单不一致;请更新提案并重新生成草稿")


def propose(work, request, bindings=None):
    data = load(work)
    validate(work, data, materials=bindings is None)
    require(isinstance(request, dict) and isinstance(request.get("goal"), str) and request["goal"].strip(), "提案须写 goal")
    require(isinstance(request.get("changes"), list), "提案须写 changes 数组")
    changes = copy.deepcopy(request["changes"])
    require(all(c.get("path") != "AGENTS.md" for c in changes if isinstance(c, dict)), "AGENTS.md 不接受正文提案,请使用 agents_links")
    data.update(goal=request["goal"], changes=changes, pending=request.get("pending", []), notes=request.get("notes", []),
                bindings=bindings if bindings is not None else data.get("bindings", {}), revision=data["revision"] + 1,
                rule_targets=request.get("rule_targets", []))
    links = request.get("agents_links", [])
    require(isinstance(links, list), "agents_links 应为数组")
    if links:
        content = index_content(data, links, warnings=data["pending"])
        if content != (data["base"]["AGENTS.md"]["content"] or ""):
            source = request.get("agents_source")
            source_valid(source)
            data["changes"].append(dict(path="AGENTS.md", operation="index", links=links, content=content,
                                        reason="接入本次规则索引", source=source))
    validate(work, data)
    save(work, data)
    return data


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
        require(now["content"] == c["content"], "本草稿已应用,但文件后来又变化: %s;请另起草稿" % c["path"])
    print("APPLIED: 本草稿已应用,没有重复写入。")
    return True


def change_label(change, base):
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
    print("DIGEST: " + digest(data))
    return data


def hookup_suggestions(data):
    root = data["repo"]["root"]
    after = {c["path"]: c["content"] for c in data["changes"]}
    selected = set(data.get("rule_targets", [])) | {p for p in after if p.startswith(".agents/rules/")}
    tips = []
    for rel in sorted(selected):
        if after.get(rel, data["base"][rel]["content"]) is None:
            continue
        for tool, extension in ((".claude", ".md"), (".cursor", ".mdc")):
            nested = str(Path(rel[len(".agents/rules/"):]).with_suffix(extension))
            link = "%s/rules/%s" % (tool, nested)
            if os.path.isdir(os.path.join(root, tool)) and not os.path.lexists(os.path.join(root, link)):
                ref = os.path.relpath(rel, os.path.dirname(link))
                command = "mkdir -p %s && ln -s %s %s" % tuple(map(shlex.quote, (os.path.dirname(link), ref, link)))
                if tool == ".cursor":
                    command += "  # Cursor 官方未写明软链接和多余字段的处理,请自行确认"
                tips.append(command)
    if tips:
        print("NOTE: 以下宿主规则接入命令仅为建议,没有执行;请在仓库根目录按需运行。")
        for tip in tips:
            print("SUGGEST: " + tip)


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
            rel = c["path"]
            path = target(data["repo"], rel, allow_agents=True)
            def unchanged():
                require(snapshot(data["repo"], rel) == data["base"][rel], "写入前目标变化: " + rel)
            if c["content"] is None:
                unchanged()
                os.unlink(path)
            else:
                atomic(path, c["content"], verify=unchanged)
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
    hookup_suggestions(data)
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
