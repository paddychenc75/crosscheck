# crlib.py — 各脚本共用的 Python 工具。只被本目录下的脚本 import,不单独运行。
# 只用标准库,兼容 Python 3.8+。

import hashlib
import json
import os
import random
import re
import string
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


def norm(s):
    return " ".join(str(s).split())


def ctx_hash(lines, n):
    """出问题的那一行加上下各 2 行,比单独一行更能代表"这段代码"。n 从 1 开始。"""
    return hashlib.sha1("\n".join(norm(x) for x in lines[max(0, n - 3):n + 2]).encode("utf-8")).hexdigest()[:12]


def short_hash(s, n=8):
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:n]


def nonce(n=4):
    alphabet = string.ascii_uppercase + string.digits
    rng = random.SystemRandom()
    return "".join(rng.choice(alphabet) for _ in range(n))


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


# ---------- 规则条目 ----------

META_RE = re.compile(r"<!--\s*cr:\s*(.*?)-->")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")


def parse_meta(s):
    d = {}
    for m in re.finditer(r"([A-Za-z_]+)=(\S+)", s):
        d[m.group(1)] = m.group(2)
    return d


def split_rules(body):
    """把规则文件正文拆成条目。一条规则是一个列表项(可以续行),后面紧跟的
    <!-- cr: id=... --> 是它的元数据。返回 [{id, meta, text, start, end}],行号从 1 开始。"""
    lines = body.split("\n")
    rules, cur = [], None

    def close():
        if cur is not None:
            rules.append(cur)

    for i, line in enumerate(lines, 1):
        m = META_RE.search(line)
        b = BULLET_RE.match(line)
        if b and len(b.group(1)) <= 1 and not line.strip().startswith("<!--"):
            close()
            text = META_RE.sub("", b.group(2)).strip()
            cur = {"id": None, "meta": {}, "text": text, "start": i, "end": i}
            if m:
                cur["meta"] = parse_meta(m.group(1))
                cur["id"] = cur["meta"].get("id")
            continue
        if cur is None:
            continue
        if m and line.strip().startswith("<!--"):
            cur["meta"] = parse_meta(m.group(1))
            cur["id"] = cur["meta"].get("id")
            cur["end"] = i
            continue
        if line.strip() and line[:1] in (" ", "\t") and not cur["meta"]:
            cur["text"] += " " + line.strip()
            cur["end"] = i
            continue
        if not line.strip():
            continue
        close()
        cur = None
    close()
    return rules


def remove_rules(body, ids):
    """去掉指定编号的规则(连同元数据行)。"""
    if not ids:
        return body
    lines = body.split("\n")
    drop = set()
    for r in split_rules(body):
        if r["id"] in ids:
            drop.update(range(r["start"], r["end"] + 1))
    return "\n".join(x for i, x in enumerate(lines, 1) if i not in drop)


# ---------- git / 仓库 ----------

def git(root, *args, check=True):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


class Source:
    """读文件:工作区,或者某个 commit(不检出)。"""

    def __init__(self, root, ref=None):
        self.root, self.ref = root, ref
        self._tree = None

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


def config_path():
    return os.environ.get("CROSSCHECK_CONFIG") or os.path.join(
        os.environ.get("CROSSCHECK_HOME") or os.path.join(os.path.expanduser("~"), ".crosscheck"), "config")


def config():
    d = {}
    try:
        with open(config_path(), encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                v = re.sub(r"\s+#.*$", "", v).strip()
                if v.startswith("~/"):
                    v = os.path.join(os.path.expanduser("~"), v[2:])
                d[k.strip()] = v
    except OSError:
        pass
    return d


def home():
    return os.environ.get("CROSSCHECK_HOME") or os.path.join(os.path.expanduser("~"), ".crosscheck")


def rules_dir():
    d = os.environ.get("CROSSCHECK_RULES_DIR") or config().get("rules_dir") or os.path.join(home(), "org-rules")
    return d if os.path.isdir(d) else ""


def data_dir():
    d = config().get("data_dir") or os.path.join(home(), "data")
    return d


def project_key(repo_id):
    return re.sub(r"[^A-Za-z0-9._-]", "_", repo_id.strip("/"))


SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}
SEV_NAME = {"critical": "严重", "major": "重要", "minor": "轻微"}


# ---------- 人的判断(本机 + 团队共享) ----------

def _read_jsonl(path):
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


def team_feedback_files(repo_id):
    base = os.path.join(data_dir(), "feedback", project_key(repo_id))
    out = []
    for dp, _, names in os.walk(base):
        out.extend(os.path.join(dp, n) for n in names if n.endswith(".jsonl"))
    return sorted(out)


def feedback_entries(pdir, repo_id=None):
    """项目的判断记录:本机的 feedback.jsonl,加上已同步下来的团队记录。按 key 合并,时间晚的为准。"""
    if repo_id is None:
        try:
            repo_id = open(os.path.join(pdir, ".repo"), encoding="utf-8").readline().strip()
        except OSError:
            repo_id = ""
    entries = _read_jsonl(os.path.join(pdir, "feedback.jsonl")) if pdir else []
    if repo_id:
        for p in team_feedback_files(repo_id):
            entries.extend(_read_jsonl(p))
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

