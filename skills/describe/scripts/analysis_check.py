"""模型输出唯一结构与引用校验入口;只确认出处,不判断业务语义或写作风格。

读取 analyst.json 或 analyst-<批次>.json,写出 analysis.json。
上下文引用必须是目标版本的 path + line + quote,不做全仓关键词兜底。
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
from project_rules import _credential_path  # noqa: E402

LINK_KINDS = ("doc", "design")


def compact(s):
    return re.sub(r"\s+", "", s)


def one_line(s):
    return re.sub(r"\s+", " ", s).strip()


def corpus(d, compacted=True):
    parts = []
    for name in ("intent.md", "description.md"):
        try:
            with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
                parts.append(f.read())
        except OSError:
            pass
    meta = crlib.read_kv(os.path.join(d, "meta.txt"))
    sd = meta.get("source_dir", "")
    if sd and os.path.isdir(sd):
        for name in sorted(os.listdir(sd)):
            try:
                with open(os.path.join(sd, name), encoding="utf-8", errors="replace") as f:
                    parts.append(f.read())
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


def criterion_key(text):
    """采集器附加的来源标注不属于验收标准正文,其余文字须精确匹配。"""
    return compact(re.sub(r"\(来自 [^)]*\)$", "", text.strip()))


def replies(d):
    one = os.path.join(d, "analyst.json")
    return ([one] if os.path.isfile(one) else []) + sorted(glob.glob(os.path.join(glob.escape(d), "analyst-*.json")))


class Target:
    """只读指定版本文件;WORKTREE 才读取工作区,并拒绝越界路径。"""

    def __init__(self, d):
        meta = crlib.read_kv(os.path.join(d, "meta.txt"))
        self.root, self.ref = meta.get("repo_root", ""), meta.get("code_ref", "")
        self.cache = {}

    def lines(self, path):
        if path in self.cache:
            return self.cache[path]
        lines = []
        if (self.root and self.ref not in ("", "UNAVAILABLE") and path and not path.startswith("/")
                and not any(p in ("", ".", "..") for p in path.split("/")) and "\\" not in path
                and not _credential_path(path)):
            try:
                if self.ref == "WORKTREE":
                    full = os.path.realpath(os.path.join(self.root, path))
                    if (os.path.commonpath([full, os.path.realpath(self.root)]) == os.path.realpath(self.root)
                            and not _credential_path(full)):
                        with open(full, encoding="utf-8") as f:
                            lines = f.read().splitlines()
                elif not self.ref.startswith("-"):
                    r = subprocess.run(["git", "-C", self.root, "show", "--no-ext-diff", "--no-textconv", "%s:%s" % (self.ref, path)],
                                       capture_output=True, text=True, timeout=10)
                    if r.returncode == 0:
                        lines = r.stdout.splitlines()
            except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError):
                pass
        self.cache[path] = lines
        return lines


def check(d):
    facts = crlib.load_json(os.path.join(d, "describe-facts.json"), {}) or {}
    dropped = []

    def drop(what, why):
        dropped.append({"what": what, "why": why})

    def items(raw, name):
        value = raw.get(name, [])
        if not isinstance(value, list):
            drop(name, "必须是数组")
            return []
        return value

    hunks = {h["id"]: h for h in facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}
    changed = describe_facts.hunk_lines(d)
    target = Target(d)
    material = corpus(d, compacted=False)
    urls = set(re.findall(r'https?://[^\s<>\[\]()"\x27`]+', material))
    criteria = acceptance(d)
    result = {"summary": None, "requirements": [], "changes": [], "impact": [], "verify": [], "links": []}
    paths = replies(d)
    batches = facts.get("batches") or []
    expected = {"analyst-%s.json" % b["id"] for b in batches}
    for name in sorted(expected - {os.path.basename(p) for p in paths}):
        drop(name, "缺少批次分析")
    if not paths:
        drop("analyst.json", "缺少分析")

    allowed_ids = set(hunks)

    def ids_of(item):
        if "ref" in item and item["ref"] != target.ref:
            raise ValueError("引用不是目标版本")
        ids = item.get("hunks")
        if not isinstance(ids, list) or not ids or any(not isinstance(h, str) or h not in hunks for h in ids):
            raise ValueError("代码块编号不存在:%s" % ("、".join(map(str, ids)) if isinstance(ids, list) else ids))
        if any(h not in allowed_ids for h in ids):
            raise ValueError("引用代码块不属于本批次")
        if any(h not in changed or changed[h]["file"] != hunks[h]["file"] for h in ids):
            raise ValueError("代码块与本次 diff 不一致")
        return ids

    def grounded(item, what):
        try:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError("缺少文字或格式不对")
            if not isinstance(item.get("title", ""), str):
                raise ValueError("标题必须是字符串")
            ids = ids_of(item)
            anchors = item.get("anchors")
            if not isinstance(anchors, list) or not anchors or any(not isinstance(a, str) or not a.strip() for a in anchors):
                raise ValueError("没有锚点或锚点格式不对")
            context = item.get("context", [])
            if not isinstance(context, list):
                raise ValueError("上下文引用必须是数组")
            quotes = []
            for ref in context:
                if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                        or type(ref.get("line")) is not int or ref["line"] < 1
                        or not isinstance(ref.get("quote"), str) or not ref["quote"].strip()):
                    raise ValueError("上下文引用需要 path、line、quote")
                if "ref" in ref and ref["ref"] != target.ref:
                    raise ValueError("上下文引用不是目标版本")
                lines = target.lines(ref["path"])
                quote_lines = ref["quote"].splitlines()
                if lines[ref["line"] - 1:ref["line"] - 1 + len(quote_lines)] != quote_lines:
                    raise ValueError("上下文引文在目标版本指定位置找不到")
                quotes.append(ref["quote"])
            lines = [x for h in ids for x in changed[h]["lines"]] + quotes
            for a in anchors:
                if not any(a in x for x in lines):
                    raise ValueError("锚点 %s 不在所引代码块的改动行里或具体上下文引文里" % a)
            out = {"title": item.get("title", ""), "text": item["text"].strip(), "hunks": ids, "anchors": anchors}
            if context:
                out["context"] = context
            return out
        except ValueError as err:
            drop(what, str(err))
            return None

    seen_change, seen_impact, seen_verify = {}, set(), set()
    for path in paths:
        name = os.path.basename(path)
        if expected and name not in expected:
            drop(name, "不是本次指定批次")
            continue
        allowed_ids = next((set(b["hunks"]) for b in batches if name == "analyst-%s.json" % b["id"]), set(hunks))
        raw = crlib.load_json(path, {})
        if not isinstance(raw, dict) or not raw:
            drop(name, "缺少可用 JSON 对象")
            continue
        if "ref" in raw and raw["ref"] != target.ref:
            drop(name, "分析不是目标版本")
            continue
        summary = raw.get("summary")
        if summary is not None:
            if (not isinstance(summary, dict) or not isinstance(summary.get("text"), str)
                    or not summary["text"].strip() or not isinstance(summary.get("quote"), str)
                    or not compact(summary["quote"]) or compact(summary["quote"]) not in compact(material)
                    or not isinstance(summary.get("source", ""), str)):
                drop("一句话", "格式不对或引文在需求材料里找不到")
            elif result["summary"] is None:
                result["summary"] = {k: summary.get(k, "") for k in ("text", "quote", "source")}
        for r in items(raw, "requirements"):
            try:
                if not isinstance(r, dict) or not isinstance(r.get("criterion"), str):
                    raise ValueError("需求对照格式不对")
                match = next((x for x in criteria if criterion_key(x) == criterion_key(r["criterion"])), None)
                if not match:
                    raise ValueError("不是 intent.md 里列出的验收标准")
                ids = ids_of(r)
                old = next((x for x in result["requirements"] if x["criterion"] == match), None)
                if old is None:
                    old = {"criterion": match, "hunks": [], "files": []}
                    result["requirements"].append(old)
                old["hunks"] = list(dict.fromkeys(old["hunks"] + ids))
                old["files"] = list(dict.fromkeys(hunks[h]["file"] for h in old["hunks"]))
            except ValueError as err:
                drop("需求对照", str(err))
        renumber = {}
        for i, c in enumerate(items(raw, "changes"), 1):
            ok = grounded(c, "改了什么 %d" % i)
            if ok:
                key = (one_line(ok["title"]), one_line(ok["text"]), tuple(ok["hunks"]))
                if key not in seen_change:
                    result["changes"].append(ok)
                    seen_change[key] = len(result["changes"])
                renumber[i] = seen_change[key]
        for i, item in enumerate(items(raw, "impact"), 1):
            ok = grounded(item, "主要影响 %d" % i)
            if ok and ok["text"] not in seen_impact:
                seen_impact.add(ok["text"])
                result["impact"].append({k: v for k, v in ok.items() if k != "title"})
        for i, v in enumerate(items(raw, "verify"), 1):
            ok = grounded(v, "怎么验证 %d" % i)
            if not ok:
                continue
            ch = v.get("change")
            if type(ch) is not int or ch not in renumber:
                drop("怎么验证 %d" % i, "对应的改动组 %s 不存在" % ch)
                continue
            group = result["changes"][renumber[ch] - 1]
            if not set(ok["hunks"]) & set(group["hunks"]):
                drop("怎么验证 %d" % i, "引用代码块与对应改动组没有关联")
                continue
            key = (renumber[ch], ok["text"], tuple(ok["hunks"]))
            if key not in seen_verify:
                seen_verify.add(key)
                result["verify"].append(dict(ok, change=renumber[ch]))
        for x in items(raw, "links"):
            if (not isinstance(x, dict) or x.get("kind") not in LINK_KINDS
                    or not isinstance(x.get("url"), str) or not re.fullmatch(r"https?://\S+", x["url"])
                    or not isinstance(x.get("label", ""), str)):
                drop("链接", "类别、网页链接或标签格式不对")
            elif x["url"] not in urls:
                drop("链接", "链接不在需求材料原文里")
            elif all(x["url"] != y["url"] for y in result["links"]):
                result["links"].append({"kind": x["kind"], "url": x["url"], "label": x.get("label", "")})
    result["impact"] = [x for x in result["impact"] if x["text"] not in {c["text"] for c in result["changes"]}]
    result["status"] = ("partial" if dropped else "ok") if any(result.values()) else "failed"
    result["dropped"] = dropped
    crlib.save_json(os.path.join(d, "analysis.json"), result)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: analysis_check.py DIR")
    r = check(sys.argv[1])
    print("STATUS: %s" % r["status"])
    for x in r["dropped"]:
        print("DROPPED: %s — %s" % (x["what"], x["why"]))
