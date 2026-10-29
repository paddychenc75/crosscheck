# crlib.py — 各脚本共用的 Python 工具。只被本目录下的脚本 import,不单独运行。
# 只用标准库,兼容 Python 3.8+。

import hashlib
import json
import os
import re
import subprocess


def read_kv(path):
    """key=value 文件(meta.txt 一类);重复的 key 以最后一次为准。"""
    d = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    d[k] = v
    except OSError:
        pass
    return d


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_text(path):
    """读文本文件;读不到返回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def clip(s, n):
    """截到 n 个字符,并注明已截断。"""
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "\n……(已截断)"


def norm(s):
    return " ".join(str(s).split())


def ctx_hash(lines, n):
    """出问题的那一行加上下各 2 行,比单独一行更能代表"这段代码"。n 从 1 开始。"""
    return hashlib.sha1("\n".join(norm(x) for x in lines[max(0, n - 3):n + 2]).encode("utf-8")).hexdigest()[:12]


def short_hash(s, n=8):
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:n]


def finding_key(f):
    """记录与状态迁移沿用报告身份。"""
    return f.get("id") or "|".join(str(f.get(k, "")) for k in ("file", "category", "since", "summary"))


def feedback_key(f):
    """已确认根因的反馈跨报告稳定;旧记录保留原来的身份键。"""
    root = root_cause_key(f)
    if root:
        return "root:" + hashlib.sha256(json.dumps([f.get("category"), f.get("rule_file"), f.get("rule_quote"), root], ensure_ascii=False).encode("utf-8")).hexdigest()
    return finding_key(f)


def root_cause_key(f):
    """核实者确认的错误位置、行为约束及局部修复;摘要不参与。"""
    r = f.get("root_cause")
    fields = ("file", "lines", "mechanism", "constraint", "fix")
    if not isinstance(r, dict) or any(not isinstance(r.get(k), str) or not r[k].strip() for k in fields):
        return None
    return tuple(r[k].strip() for k in fields)


def history_related(f, h):
    """只找重核线索,不证明历史前提仍然适用。兼容没有稳定根因的旧记录。"""
    if f.get("id") and f.get("id") == h.get("id"):
        return True
    root = root_cause_key(f)
    if root and root == root_cause_key(h):
        return True
    return bool(f.get("file") == h.get("file") and f.get("category") == h.get("category")
                and f.get("code_line") and norm(f["code_line"]) == norm(h.get("code_line", ""))
                and f.get("context_hash") and f["context_hash"] == h.get("context_hash"))


def evidence_snapshot(f, source):
    """复用 evidence 的文件集合,整文件哈希覆盖行移动和调用输入变化。"""
    paths = {f.get("file")}
    evidence = f.get("evidence")
    for e in evidence if isinstance(evidence, list) else []:
        if isinstance(e, dict):
            paths.add(e.get("file") or f.get("file"))
    result = {}
    for path in sorted(p for p in paths if isinstance(p, str) and p):
        text = source.read(path) if source else None
        if text is not None:
            result[path] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return result


def recheck_reason(f, status, history, source):
    if status.get(f.get("file")) in (None, "removed"):
        return ""
    if status.get(f.get("file")) in ("changed", "dependent"):
        return "主文件或符号依赖变化"
    if any(history_related(f, h) for h in history if h.get("verdict") in ("invalid", "wontfix")):
        return "历史反馈需重新核对当前前提"
    snapshot = f.get("evidence_snapshot")
    if not isinstance(snapshot, dict) or not snapshot or f.get("file") not in snapshot:
        return "旧发现缺少证据依赖快照,保守重核"
    current = evidence_snapshot(f, source)
    if snapshot != current:
        return "引用证据或调用方文件变化"
    return ""


# ---------- glob ----------

def expand_braces(p):
    s = depth = 0
    start = -1
    for i, c in enumerate(p):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                body = p[start + 1:i]
                parts, cur, d = [], "", 0
                for ch in body:
                    if ch == "{":
                        d += 1
                    if ch == "}":
                        d -= 1
                    if ch == "," and d == 0:
                        parts.append(cur)
                        cur = ""
                    else:
                        cur += ch
                parts.append(cur)
                out = []
                for part in parts:
                    out.extend(expand_braces(p[:start] + part + p[i + 1:]))
                return out
    return [p]


def glob_re(pattern):
    p = pattern.strip()
    if p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def glob_match(pattern, path):
    """类 gitignore 的匹配:不含 / 的模式(如 *.go)匹配任意目录下的文件名;以 / 结尾表示整个目录。"""
    for p in expand_braces(pattern):
        p = p.strip()
        if not p:
            continue
        if p.endswith("/"):
            p += "**"
        if "/" not in p.rstrip("/"):
            if glob_re(p).match(path.rsplit("/", 1)[-1]) or glob_re(p).match(path):
                return True
        elif glob_re(p).match(path):
            return True
    return False


def any_match(patterns, path):
    return any(glob_match(p, path) for p in patterns)


# ---------- frontmatter(只支持本插件用到的 YAML 子集) ----------

def _strip_comment(v):
    out, q = "", None
    for i, c in enumerate(v):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#" and (i == 0 or v[i - 1] in " \t"):
            break
        out += c
    return out.strip()


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _split_top(s):
    parts, cur, depth, q = [], "", 0, None
    for c in s:
        if q:
            cur += c
            if c == q:
                q = None
            continue
        if c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _scalar(v):
    v = _strip_comment(v)
    if v.startswith("[") and v.endswith("]"):
        return [_unquote(x) for x in _split_top(v[1:-1])]
    if v.startswith("{") and v.endswith("}"):
        d = {}
        for item in _split_top(v[1:-1]):
            if ":" in item:
                k, x = item.split(":", 1)
                d[_unquote(k)] = _unquote(x)
        return d
    return _unquote(v)


def parse_frontmatter(text):
    """返回 (dict, 正文)。没有 frontmatter 时 dict 为空。列表项里的 "ID: 原因" 原样保留为字符串。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    data, key = {}, None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[:1] in (" ", "\t")
        s = raw.strip()
        if indented and key is not None:
            if s.startswith("- "):
                if not isinstance(data.get(key), list):
                    data[key] = []
                data[key].append(_unquote(_strip_comment(s[2:])))
            elif ":" in s:
                if not isinstance(data.get(key), dict):
                    data[key] = {}
                k, v = s.split(":", 1)
                data[key][_unquote(k)] = _scalar(v)
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            key = _unquote(k)
            v = v.strip()
            data[key] = _scalar(v) if v else None
    body = "\n".join(lines[end + 1:])
    return data, body


def frontmatter_unclosed(text):
    """第一行是 --- 却没有结束的 ---:parse_frontmatter 会当作没有头部。"""
    lines = (text or "").split("\n")
    return lines[0].strip() == "---" and not any(x.strip() == "---" for x in lines[1:])


# 「不要报」一节的标题:整个标题就是这几个字,最多带一个括号说明和冒号
SUPPRESS_TITLE = re.compile(r"^(不要报|(do not|don't|don’t|dont) (report|flag))\s*([(（][^()（）]*[)）])?\s*[:：]?$", re.I)


def is_suppress_title(title):
    return bool(SUPPRESS_TITLE.match(str(title).strip()))


RULE_MUST = re.compile(r"必须|禁止|不得|不能|不要|不允许|只能|应当|应该|一律|统一|must|never|always|do not|don't", re.I)
RULE_SEVERITY = {"重要": "重要", "major": "重要", "轻微": "轻微", "minor": "轻微"}


def rule_problems(kind, rule, severity="", exceptions=""):
    """规则候选写法不合格的地方,合格时为空列表。"""
    rule = norm(rule)
    if not rule or len(rule) > 200:
        return ["规则应为一句话、不超过 200 字"]
    problems = []
    if kind == "rule":
        if not RULE_MUST.search(rule):
            problems.append("没有写成「必须 / 禁止」的单句")
        if str(severity or "").strip() not in RULE_SEVERITY:
            problems.append("严重级别应为重要或轻微")
        if not norm(exceptions or ""):
            problems.append("没有写「不要报」的边界")
    return problems


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, dict):
        return ["%s: %s" % (k, x) for k, x in v.items()]
    return [str(v)] if str(v).strip() else []


def as_map(v):
    """{a: 1} 或 ["a: 1"] 或 "- a: 1" 列表,统一成 dict。"""
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items()}
    out = {}
    for item in as_list(v):
        if ":" in item:
            k, x = item.split(":", 1)
            out[k.strip()] = x.strip()
    return out


# ---------- git / 仓库 ----------

def git(root, *args, check=True):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def pick_base(root, target, head):
    """head 相对目标分支 target 的对比基准,返回 (base, merge_commit)。
    还没合入、或用 squash / rebase 合入(head 不是 target 的祖先)时就是 merge-base(target, head),merge_commit 为空。
    用 merge commit 合入后 target 已包含 head,merge-base 就等于 head、diff 为空:这时在 target 的 first-parent
    链上找到合入它的那个提交 M,基准取 merge-base(M^1, head)。找不到 M(快进合入、head 就是 target)时抛 ValueError。"""
    def run(*args):
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)
        return r.returncode, r.stdout.strip()
    code, full = run("rev-parse", "-q", "--verify", head + "^{commit}")
    if code or not full:
        raise ValueError("找不到提交 %s" % head)
    if run("merge-base", "--is-ancestor", full, target)[0] != 0:
        code, base = run("merge-base", target, full)
        if code or not base:
            raise ValueError("%s 与 %s 没有共同的祖先" % (target, full[:12]))
        return base, ""
    chain = run("rev-list", "--first-parent", "--ancestry-path", "%s..%s" % (full, target))[1].split()
    parent = run("rev-parse", "-q", "--verify", chain[-1] + "^1")[1] if chain else ""
    if not parent or parent == full:
        raise ValueError("%s 已经在 %s 里,但在它的 first-parent 链上找不到合入它的 merge commit(可能是快进合入,"
                         "或者它本来就是 %s 上的提交),算不出这次改动的对比基准" % (full[:12], target, target))
    code, base = run("merge-base", parent, full)
    if code or not base:
        raise ValueError("%s 与 %s 没有共同的祖先" % (parent[:12], full[:12]))
    return base, chain[-1]


class Source:
    """读文件:工作区,或者某个 commit(不检出)。"""

    def __init__(self, root, ref=None):
        self.root, self.ref = root, ref
        self._tree = None
        self._lines = {}

    def files(self):
        if self._tree is None:
            if self.ref:
                out = git(self.root, "ls-tree", "-r", "--name-only", self.ref, check=False)
            else:
                out = git(self.root, "ls-files", "--cached", "--others", "--exclude-standard", check=False)
            self._tree = [x for x in out.split("\n") if x]
        return self._tree

    def exists(self, path):
        if self.ref:
            return path in set(self.files())
        return os.path.isfile(os.path.join(self.root, path))

    def read(self, path):
        try:
            if self.ref:
                return git(self.root, "show", "%s:%s" % (self.ref, path))
            with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, RuntimeError):
            return None

    def lines(self, path):
        """按行读文件(带缓存);读不到返回 None。"""
        if path not in self._lines:
            text = self.read(path)
            self._lines[path] = None if text is None else text.split("\n")
        return self._lines[path]


def code_source(meta):
    """被审查的那一版代码。"""
    root = meta.get("repo_root", "")
    if meta.get("head_checked_out") == "yes":
        return Source(root)
    ref = meta.get("code_ref", "")
    return Source(root, ref) if ref and ref != "UNAVAILABLE" else None


def ranges_of(review_dir):
    out = {}
    try:
        with open(os.path.join(review_dir, "ranges.txt"), encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) == 3:
                    out.setdefault(p[0], []).append((int(p[1]), int(p[2])))
    except (OSError, ValueError):
        pass
    return out


def in_ranges(ranges, path, a, b=None):
    b = a if b is None else b
    return any(not (b < s or a > e) for s, e in ranges.get(path, []))


def parse_lines(v):
    """"38-47"、"42"、42、[38, 47] → (a, b)。"""
    if isinstance(v, int):
        return v, v
    if isinstance(v, (list, tuple)) and v:
        return int(v[0]), int(v[-1])
    m = re.match(r"^\s*(\d+)\s*(?:[-–~,]\s*(\d+))?\s*$", str(v or ""))
    if not m:
        return None
    a = int(m.group(1))
    return a, int(m.group(2) or a)


# 按安装位置加载插件共享入口,不依赖 cwd、PYTHONPATH 或通用模块名 config。
# 技能目录须保留在 <plugin>/skills/review 下;缺共享目录直接报错,不回退旧路径。
import importlib.util
from pathlib import Path

_config_file = Path(__file__).resolve().parents[3] / "scripts" / "config.py"
_config_spec = importlib.util.spec_from_file_location("crosscheck_config", _config_file)
_config_module = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)
config = _config_module.config
config_path = _config_module.config_path
plugin_config_path = _config_module.plugin_config_path
home = _config_module.home


def strip_remote(url):
    """origin 去凭证；HTTP 非默认端口属于身份，SSH 端口仅属于传输。"""
    import host_target
    try:
        parsed = host_target.address(url)
        return parsed["host"] + "/" + parsed["path"]
    except host_target.TargetError:
        pass  # 本地裸仓库等非平台地址保留原有本地记录键。
    u = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", url.strip())
    u = re.sub(r"^[^@/]*@", "", u)
    u = re.sub(r"^([^/:]*):[0-9]+/", r"\1/", u)
    u = u.replace(":", "/", 1)
    return re.sub(r"\.git$", "", u.rstrip("/"))


def repo_identity(cwd=None):
    """项目的身份:origin 去凭证后的地址;没有远程时用主工作区的路径(各个 worktree 一致)。"""
    args = ["git"] + (["-C", cwd] if cwd else [])
    url = subprocess.run(args + ["remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    if url:
        return strip_remote(url)
    common = subprocess.run(args + ["rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    if not os.path.isabs(common):
        common = os.path.join(cwd or os.getcwd(), common)
    return os.path.dirname(os.path.abspath(common))


def project_dir(repo_id):
    """项目的记录目录:reviews/ 下 .repo 第一行等于 repo_id 的那个;没有返回 None。"""
    reviews = os.path.join(home(), "reviews")
    if not os.path.isdir(reviews):
        return None
    for name in sorted(os.listdir(reviews)):
        try:
            with open(os.path.join(reviews, name, ".repo"), encoding="utf-8") as f:
                if f.readline().strip() == repo_id:
                    return os.path.join(reviews, name)
        except OSError:
            pass
    return None


SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}
SEV_NAME = {"critical": "严重", "major": "重要", "minor": "轻微"}


# ---------- 人的判断 ----------

def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def feedback_entries(pdir):
    """项目的判断记录 feedback.jsonl,按 key 合并,时间晚的为准。"""
    entries = read_jsonl(os.path.join(pdir, "feedback.jsonl")) if pdir else []
    latest = {}
    for e in entries:
        k = e.get("key") or e.get("id")
        if not k:
            continue
        if k not in latest or str(e.get("time", "")) >= str(latest[k].get("time", "")):
            latest[k] = e
    return latest


def author_hash(root=None):
    args = ["git"] + (["-C", root] if root else []) + ["config", "user.email"]
    try:
        who = subprocess.run(args, capture_output=True, text=True).stdout.strip()
    except OSError:
        who = ""
    return short_hash(who or os.environ.get("USER", "unknown"))



if __name__ == "__main__":
    # 给 shell 脚本用:repo-id / base 仓库 目标 head;配置 CLI 在插件 scripts/config.py
    import sys
    cmd = sys.argv[1:2]
    if cmd == ["base"] and len(sys.argv) == 5:
        try:
            b, m = pick_base(sys.argv[2], sys.argv[3], sys.argv[4])
        except ValueError as e:
            sys.exit(str(e))
        print("base=%s\nmerge_commit=%s" % (b, m))
    elif cmd == ["repo-id"]:
        print(repo_identity())
    else:
        sys.exit(2)
