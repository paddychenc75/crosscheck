"""PR/MR 描述:给评审人看的改动说明,只在本地生成,由作者自己复制到 PR/MR;另有一份完整的网页版。

用法: python3 describe.py DIR [--tested TEXT] [--focus JSON]
  DIR       collect.sh 输出的材料目录
  --tested  作者给的执行记录(可选,不提示也不强制),原样引用;--focus 作者点名请评审人重点看的地方,
            JSON 数组(最多 3 条),如 [{"where": "api/orders.ts:42", "ask": "兼容期够不够"}]。
            两者记在这个目标的描述目录里,之后重新生成时沿用;传空字符串清除

DIR 里有 analyst.json(或分批的 analyst-<批次>.json)时先用 analysis_check.py 校验(输出 STATUS: / DROPPED:),再写出
<描述目录>/describe.md(贴进 PR/MR 描述,正文约 30 行,仅作参考,不含负责人固定填写区)和 describe.html(完整版)。
描述目录是 ${CROSSCHECK_HOME:-~/.crosscheck}/describe/<项目名>/<目标>/,和审查记录(reviews/)分开存放。输出:
  describe=<describe.md 的路径>  md_link=<file:// 链接>  html=<describe.html 的路径>  link=<file:// 链接>
  NOTE: …  需要转告作者的话(没推送、一句话或验证步骤由 AI 起草等)

只读:不调用托管平台,不发布、不修改 PR/MR 的描述、评论或状态。
内容只有事实和链接:一句话和基本信息(每项一行);改了什么(analyst 按功能分的组,经校验,没被任何组引用的非测试代码块
由脚本补一条「其他」);影响面(接口 > 数据库与迁移 > 配置与依赖 > 资源 > 内部函数,每层最多 2 条,资源最多 3 条,不在识别范围的语言写明
未检查);作者点名的请重点看;怎么验证(脚本认出的入口在前、analyst 按组写的「入口 → 能看到什么」在后,最多 5 行,
标「由代码推断,未执行」,作者给了 --tested 才有执行记录);有验收标准时的需求对照。超过约 MAX_LINES 行时按 TRIM 的顺序裁剪(30 行只是参考,裁完仍超出照样输出)。
来源:sources.json、describe-facts.json(脚本)、analysis.json(analyst 的回复,经校验)。
结构和措辞在 ../templates/describe.md、describe.html(团队可以改它们适配自己的格式),本脚本只算事实、填进去;模板写法见 render()。
"""
import datetime
import html
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "review", "scripts"))
sys.path.insert(0, HERE)
import crlib  # noqa: E402
import sources as srcmod  # noqa: E402
import analysis_check  # noqa: E402
import analysis_facts as af  # noqa: E402
from entrypoints import KIND_NAME  # noqa: E402

LAYERS = [("api", "接口"), ("db", "数据库与迁移"), ("config", "配置与依赖"), ("res", "资源"), ("func", "内部函数")]
VERB = {"added": "新增", "removed": "删除", "modified": "修改", "signature_changed": "签名变化"}
RANK = {"removed": 0, "signature_changed": 2, "modified": 2, "added": 3}   # 签名变化且 diff 外有调用方是 1
CHECKED = "接口、迁移、配置、签名"
VERIFY_NOTE = "由代码推断,未执行"
AI_VERIFY = "验证步骤由 AI 依据代码起草,贴之前请核对"
# 脚本认出的入口写成一行:(前, 后),中间是入口本身
TRIGGER = {"route": ("请求 ", ""), "cli_command": ("运行子命令 ", ""), "cli_flag": ("命令行带上 ", ""),
           "event": ("事件/队列 ", " 收到消息"), "cron": ("定时任务 ", " 到点触发")}
PLURAL = {"route": "请求 ", "cli_command": "运行子命令 ", "cli_flag": "命令行带上 ", "event": "事件/队列 ", "cron": "定时任务 ",
          "config": "配置开关 "}
TRIGGER_ORDER = ["route", "cli_command", "cli_flag", "event", "cron", "config"]
# 超过 MAX_LINES(约 30 行,仅作参考)时逐步多裁一项:「其他」组并进标题 → 资源明细改成一行汇总 → 需求对照改一行汇总 → 验证只留 MAX_VERIFY_CUT 行
# → 段落之间的空行。「改了什么」的分组本身不裁。
TRIM = ["other", "resources", "requirements", "verify", "blank"]
MAX_LINES = 30
PER_LAYER = 2
MAX_RESOURCES = 3
MAX_VERIFY = 5
MAX_VERIFY_CUT = 3
MAX_OTHER_FILES = 3
MAX_UNTESTED = 3
MAX_REQS = 4
MAX_FOCUS = 3


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def short(items, n, fmt=str, sep="、", unit="个"):
    items = list(items)
    s = sep.join(fmt(x) for x in items[:n])
    return s + (" 另 %d %s" % (len(items) - n, unit) if len(items) > n else "")


def code(text):
    """Markdown 行内代码;内容里有反引号时用两个反引号包。"""
    text = str(text)
    return "`` %s ``" % text if "`" in text else "`%s`" % text


def ticks_html(text):
    """analyst 写的一句话:先转义,再把反引号标出的名字换成 <code>。"""
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", html.escape(text))


def md_link(text, url):
    return "[%s](%s)" % (re.sub(r"([\[\]])", r"\\\1", text), url.replace(")", "%29").replace(" ", "%20"))


def is_local(url):
    return not re.match(r"[A-Za-z][\w+.-]*://", url or "") or url.startswith("file:")


def split_where(where):
    m = re.match(r"(.+):(\d+)$", where or "")
    return (m.group(1), m.group(2)) if m else (where, "")


def output_dir(d, meta):
    """<home>/describe/<项目名>/<目标>/:和 review 的 reviews/<项目名>/<目标>/ 同名,但不在 reviews/ 下。"""
    store = meta.get("store_dir", "")
    if not store:
        return d
    project = os.path.dirname(store)
    return os.path.join(os.path.dirname(os.path.dirname(project)), "describe", os.path.basename(project), os.path.basename(store))


def parse_focus(value):
    try:
        items = json.loads(value)
        assert isinstance(items, list) and all(isinstance(x, dict) and x.get("where") for x in items)
    except (ValueError, AssertionError):
        sys.exit('describe: --focus 要写成 JSON 数组,如 [{"where":"a.ts:42","ask":"并发安全吗"}]')
    return [{"where": str(x["where"]), "ask": str(x.get("ask") or "")} for x in items[:MAX_FOCUS]]


CR_OWNER = "### CR负责人:\n\n@xx\n"


def body_lines(text):
    """正文额度不含负责人固定填写区。"""
    return len(text.replace(CR_OWNER, "").strip("\n").splitlines())


def drop_blanks(text):
    """去掉段落之间的空行,保留负责人固定填写区里的空行。"""
    lines = text.split("\n")
    owner = CR_OWNER.splitlines()
    keep = {j for i in range(len(lines)) if lines[i:i + len(owner)] == owner for j in range(i, i + len(owner))}
    return "\n".join(line for i, line in enumerate(lines) if line.strip() or i in keep or i == len(lines) - 1)


class Note:
    def __init__(self, d, out):
        self.d, self.out = d, out
        self.meta = crlib.read_kv(os.path.join(d, "meta.txt"))
        self.root = self.meta.get("repo_root", "")
        self.facts = crlib.load_json(os.path.join(d, "describe-facts.json"), {}) or {}
        self.analysis = crlib.load_json(os.path.join(d, "analysis.json"), {}) or {}
        if self.analysis.get("status") == "failed":
            self.analysis = {}
        self.src = crlib.load_json(os.path.join(d, "sources.json"), {}) or {}
        self.intent = (crlib.read_text(os.path.join(d, "intent-arg.txt")) or "").strip()
        self.author = crlib.load_json(os.path.join(out, "author.json"), {}) or {}
        self.conf = srcmod.settings(skill="describe")
        self.asked = self.conf["required"]
        self.notes = []
        self.hunks = {h["id"]: h for h in self.facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}
        self.entry = self.facts.get("entrypoints") or {}
        self.sha = self.meta.get("head_sha", "")
        self.link_base = self._link_base()
        if self.meta.get("merged") == "yes":
            self.notes.append("这个 PR/MR 已合入:描述照常生成,但已无法影响合入")
        if self.meta.get("code_host_reason"):
            self.notes.append("PR/MR 信息没有读到(tools.code_host:%s),标题和描述没有用上" % self.meta["code_host_reason"])

    # ---------- 链接 ----------
    def _web_base(self):
        """仓库的网页地址(不含末尾斜杠)。依次用:PR/MR 的网页地址、gitlab_host 加仓库路径、https:// 加 repo_id。"""
        m = self.meta
        url = m.get("url", "")
        base = re.sub(r"/(-/)?(merge_requests|merge)/\d+.*$|/pulls?/\d+.*$", "", url)
        if url and base != url:
            return base
        if m.get("gitlab_host") and m.get("repo"):
            host = m["gitlab_host"].rstrip("/")
            return (host if "://" in host else "https://" + host) + "/" + m["repo"]
        rid = m.get("repo_id", "")
        # 没有远程时 repo_id 是本地路径,不是网址
        if "/" in rid and not rid.startswith(("/", ".", "~")):
            return "https://" + rid
        return None

    def _link_base(self):
        sha = self.sha
        base = self._web_base()
        if not sha or not self.root:
            return None
        if not base:
            self.notes.append("没有找到远程仓库的网页地址,代码位置写成 文件:行号,没有链接")
            return None
        # PR/MR 的 head 提交在平台上(refs/pull/N/head、merge-requests/N/head),不需要分支已推送
        if self.meta.get("mode") != "pr" and not git(self.root, "branch", "-r", "--contains", sha):
            self.notes.append("提交 %s 还没有推送到远端,代码位置写成 文件:行号,没有链接;推送后重新生成即可带上链接" % sha[:8])
            return None
        if self.meta.get("code_ref") == "WORKTREE" and git(self.root, "status", "--porcelain", "--untracked-files=no"):
            self.notes.append("工作区有未提交的改动,描述里的代码和 PR/MR 里的不一致;提交后重新生成再贴")
            return None
        github = self.meta.get("platform") == "github" or base.startswith("https://github.com/")
        return "%s/%s/%s/" % (base, "blob" if github else "-/blob", sha)

    def url(self, path, line=None):
        return self.link_base + path + ("#L%s" % line if line else "") if self.link_base else None

    def loc(self, path, line=None, md=True):
        text = "%s:%s" % (path, line) if line else path
        url = self.url(path, line)
        if not url:
            return "`%s`" % text if md else html.escape(text)
        return "[`%s`](%s)" % (text, url) if md else '<a href="%s"><code>%s</code></a>' % (html.escape(url), html.escape(text))

    def hunk_ref(self, hid, md=True):
        """代码块编号;能做链接时链到代码块开头。"""
        h = self.hunks.get(hid) or {}
        url = self.url(h["file"], h.get("start")) if h.get("file") else None
        if md:
            return "[%s](%s)" % (hid, url) if url else hid
        where = "%s:%s" % (h.get("file", ""), h.get("start", "")) if h.get("file") else ""
        text = "%s <code>%s</code>" % (html.escape(hid), html.escape(where)) if where else html.escape(hid)
        return '<a href="%s">%s</a>' % (html.escape(url), text) if url else text

    # ---------- 基本信息 ----------
    def source_items(self):
        """要问的类别(describe.ask 里写了、且配了读取工具)总是列出,没给就写未提供及原因;其他类别只在用户给了时列出。
        返回 [(标签, Markdown, HTML)];本地文件只写文件名。"""
        e, out = html.escape, []
        items = {x.get("kind"): x for x in self.src.get("items", [])}
        for k, c in self.conf["kinds"].items():
            it = dict(items.get(k) or {})
            st = it.get("status", "missing")
            if k not in self.asked and st != "given":
                continue
            url, title = it.get("url") or "", it.get("title") or ""
            if st == "given" and url and is_local(url):
                name = os.path.basename(url.rstrip("/")) or url
                md, page = name, e(name)
            elif st == "given" and url:
                md = md_link(title, url) if title else url
                page = '<a href="%s">%s</a>' % (e(url), e(title or url))
                if it.get("read_error"):
                    tail = "(内容未读取:%s)" % it["read_error"]
                    md, page = md + tail, page + e(tail)
            elif st == "given" and it.get("source") == "本地文档":
                name = "、".join(os.path.basename(x.rstrip("/")) for x in it.get("local_files") or []) or "本地文档"
                md, page = name, e(name)
            else:
                val = {"given": "见提交说明或描述里的编号", "skip": "未提供(作者跳过)", "unasked": "未提供(非交互运行,没有询问)",
                       "none": "无需求单" + (":%s" % it["type"] if it.get("type") else "")}.get(st, "未提供")
                md, page = val, e(val)
            out.append((c["label"], md, page))
        return out

    def basic(self):
        """基本信息,每项一行:需求单(和作者给的需求文档)、规模和分支;已合入、PR/MR 信息没读到时另加一行。"""
        e = html.escape
        sc = self.facts.get("scope") or {}
        size = "%s 个文件 +%s/−%s" % (sc.get("files", self.meta.get("files", "?")), sc.get("added", "?"), sc.get("deleted", "?"))
        md, page = size, e(size)
        tb = self.meta.get("target_branch", "")
        if tb:
            sb = self.meta.get("source_branch") or "?"
            md += " · %s → %s" % (code(sb), code(tb))
            page += " · <code>%s</code> → <code>%s</code>" % (e(sb), e(tb))
        out = self.source_items() + [("规模", md, page)]
        if self.meta.get("merged") == "yes":
            s = "已合入%s,描述已无法影响合入" % (" " + tb if tb else "")
            out.append(("状态", s, e(s)))
        if self.meta.get("code_host_reason"):
            s = "没有读到(tools.code_host:%s)" % self.meta["code_host_reason"]
            out.append(("PR/MR 信息", s, e(s)))
        return out

    def summary(self):
        if self.intent:
            return self.intent, "作者"
        s = self.analysis.get("summary")
        if s:
            self.notes.append("「一句话」是 AI 按需求材料起草的,贴之前请确认,或用 --intent 写你自己的")
            return s["text"], "AI 起草"
        title = self.meta.get("title", "")
        if title:
            return title, "PR/MR 标题"
        self.notes.append("没有一句话说明,标题先写成了分支名;用 --intent 补一句再生成")
        return "分支 %s 的改动" % (self.meta.get("source_branch") or "当前"), "分支名"

    # ---------- 改了什么 ----------
    def changes(self):
        """analyst 按功能分的组(已校验),只留引用了存在的代码块的。"""
        return [c for c in self.analysis.get("changes") or []
                if isinstance(c, dict) and c.get("text") and [h for h in c.get("hunks") or [] if h in self.hunks]]

    def other(self, changes):
        """没被任何组引用的非测试代码块:改动不会因为 analyst 漏写而从描述里消失。"""
        used = {h for c in changes for h in c.get("hunks") or []}
        return [h for h in self.hunks.values() if h.get("kind") != "test" and h["id"] not in used]

    def other_text(self, hunks, grouped):
        names = dict.fromkeys(os.path.basename(h["file"]) for h in hunks)
        return "%s:%d 处(%s)" % ("其他" if grouped else "改动", len(hunks), short(names, MAX_OTHER_FILES, unit="个文件"))

    # ---------- 影响面 ----------
    def code_symbols(self):
        return [s for s in self.facts.get("symbols", []) if s.get("kind") != "config_key" and not af.is_test(s.get("file", ""))]

    @staticmethod
    def item(layer, change, what, name=None, file="", line=None, extra="", rank=None, **more):
        return dict(layer=layer, change=change, what=what, name=name, file=file, line=line, extra=extra,
                    rank=RANK.get(change, 2) if rank is None else rank, **more)

    def layers(self):
        """{层: [条目]};层内按 删除 > 签名变化且 diff 外有调用方 > 签名变化 > 新增 排序。资源单独放在 res,和配置并列。"""
        out = {k: [] for k, _ in LAYERS}
        for e in self.entry.get("changes", []):
            out["api"].append(self.item("api", e["change"], VERB.get(e["change"], "") + KIND_NAME.get(e["kind"], e["kind"]),
                                        e["label"], e["file"], e.get("line"), type=e["kind"]))
        for a in self.facts.get("attention", []):
            t, ch = a.get("type"), a.get("change", "modified")
            if t == "migration":
                out["db"].append(self.item("db", ch, VERB.get(ch, "") + "迁移", None, a["file"], type=t))
            elif t == "config_key":
                out["config"].append(self.item("config", ch, VERB.get(ch, "") + "配置键", a["name"], a["file"], a.get("line"), type=t))
            elif t == "config_file":
                out["config"].append(self.item("config", ch, VERB.get(ch, "") + "配置文件", None, a["file"],
                                               extra="%d 个键" % a["keys"] if a.get("keys") else "", type=t))
            elif t == "project_config":
                out["config"].append(self.item("config", ch, VERB.get(ch, "") + "工程配置", None, a["file"], type=t))
            elif t == "env_var":
                out["config"].append(self.item("config", "added", "新环境变量", a["name"], a["file"], a.get("line"), type=t))
            elif t == "dependency":
                parts = a.get("parts") or []
                out["config"].append(self.item("config", ch, "删除依赖文件" if ch == "removed" else "依赖变化", None, a["file"],
                                               extra=short(parts, 2, unit="项"), type=t))
            elif t == "resource":
                out["res"].append(self.item("res", ch, VERB.get(ch, "") + "资源", a["name"], a["file"],
                                            extra="%d 个文件" % a.get("files", 1), type=t))
        for s in self.code_symbols():
            ch = s.get("change")
            if ch not in ("removed", "signature_changed", "added"):
                continue
            outside = [c for c in s.get("callers") or [] if not c.get("in_diff")]
            extra = "、".join(x for x in ("diff 外 %d 处调用" % len(outside) if outside else "", "同名歧义" if s.get("ambiguous") else "") if x)
            # 删掉的函数只有旧版的行号,对不上这一版,不给行号
            out["func"].append(self.item("func", ch, VERB[ch], s["name"], s["file"], None if ch == "removed" else s.get("line"), extra,
                                         1 if ch == "signature_changed" and outside else None, type=s.get("kind"),
                                         public=s.get("exported") is not False, symbol=s, outside=outside))
        for k in out:
            out[k].sort(key=lambda x: x["rank"])
        return out

    @staticmethod
    def overview(layers):
        """总览一行(只用在网页版):删除和迁移排最前,空的不写;什么都没认出时是空串。"""
        every = [x for v in layers.values() for x in v]
        removed = sum(1 for x in every if x["change"] == "removed")
        sig = sum(1 for x in layers["func"] if x["change"] == "signature_changed")
        parts = ["删除 %d" % removed if removed else "", "迁移 %d" % len(layers["db"]) if layers["db"] else "",
                 "接口 %d" % len(layers["api"]) if layers["api"] else "", "配置 %d" % len(layers["config"]) if layers["config"] else "",
                 "资源 %d" % len(layers["res"]) if layers["res"] else "",
                 "函数 %d%s" % (len(layers["func"]), "(签名变 %d)" % sig if sig else "") if layers["func"] else ""]
        return " · ".join(p for p in parts if p)

    def scope(self, layers):
        """检查范围:让读者分得清「没认出」和「没有」。全部能识别且什么都没认出时写检查过几个文件;
        有不在识别范围的语言时写明未检查;能识别的都认出了东西时不写。"""
        cov = self.facts.get("coverage")
        if not isinstance(cov, dict):
            return ""
        n, un = cov.get("checked", 0), [x for x in cov.get("unchecked") or [] if x.get("files")]
        langs = "、".join(x["lang"] for x in un)
        if not un:
            return "检查范围:%d 个文件按%s检查过,没有认出这几类变化" % (n, CHECKED) if n and not any(layers.values()) else ""
        if not n:
            return "接口、迁移、签名:%s 文件不在自动识别范围,未检查" % langs
        found = any(layers[k] for k in ("api", "db", "func"))
        return "接口、迁移、签名:已检查 %d 个文件%s;另有 %d 个 %s 文件不在自动识别范围,未检查" % (
            n, "" if found else ",没有认出", sum(x["files"] for x in un), langs)

    def body_only(self):
        return [s for s in self.code_symbols() if s.get("change") == "body_only"]

    def item_md(self, x):
        where = self.loc(x["file"], x["line"]) if x["file"] else ""
        if x["name"] is None:
            head, inner = "%s %s" % (x["what"], where), x["extra"]
        elif x["layer"] == "res":
            head, inner = "%s %s" % (x["what"], code(x["name"])), x["extra"]
        else:
            head, inner = "%s %s" % (x["what"], code(x["name"])), ",".join(y for y in (where, x["extra"]) if y)
        return head + ("(%s)" % inner if inner else "")

    def impact_md(self, layers, cut):
        """各层一行,没有内容的层不出现;资源和配置并列一行,被裁剪时只写数量。"""
        lines = []
        for key, title in LAYERS:
            items = layers[key]
            if not items:
                continue
            if key == "res":
                lines.append("- %s:%s" % (title, "%d 处(见完整版)" % len(items) if "resources" in cut else
                                          short(items, MAX_RESOURCES, self.item_md, ";", "项")))
            else:
                lines.append("- %s:%s" % (title, short(items, PER_LAYER, self.item_md, ";", "项")))
        if self.scope(layers):
            lines.append("- " + self.scope(layers))
        n = len(self.body_only())
        if n:
            lines.append("- 未覆盖:另有 %d 个函数只改了实现,行为变化不在此列" % n)
        return "\n".join(lines)

    # ---------- 怎么验证 ----------
    def triggers(self):
        ts = [t for t in self.entry.get("triggers", []) if isinstance(t, dict) and t.get("label")]
        ts = sorted(ts, key=lambda t: TRIGGER_ORDER.index(t["kind"]) if t["kind"] in TRIGGER_ORDER else len(TRIGGER_ORDER))
        seen, out = set(), []
        for t in ts:                     # 按 文件:行 去重
            k = (t.get("file"), t.get("line"))
            if k not in seen:
                seen.add(k)
                out.append(t)
        return out

    @staticmethod
    def trigger_words(t):
        if t["kind"] == "config":
            return ("开启配置 ", "") if t.get("on") else ("配置开关 ", " 改动后为 %s" % t.get("value"))
        return TRIGGER.get(t["kind"], ("", ""))

    def trigger_md(self, t):
        pre, post = self.trigger_words(t)
        head = "%s%s%s" % (pre, code(t["label"]), post)
        if t.get("handler"):
            head += " → 进入 %s" % code(t["handler"])
        return "%s(%s)" % (head, self.loc(t["file"], t.get("line")))

    def merged_md(self, kind, ts):
        """同一类入口太多时合成一行。"""
        if len(ts) == 1:
            return self.trigger_md(ts[0])
        return "%s%s(%s 等)" % (PLURAL.get(kind, ""), short((code(t["label"]) for t in ts), 2), self.loc(ts[0]["file"], ts[0].get("line")))

    def model_verify(self, n_changes):
        return [v for v in self.analysis.get("verify") or []
                if isinstance(v, dict) and v.get("text") and isinstance(v.get("change"), int) and 1 <= v["change"] <= n_changes]

    def verify_lines(self, f, cap):
        """脚本认出的入口在前,analyst 按组写的在后,一共不超过 cap 行;超了先把同类入口合成一行,再截掉后面的。"""
        ts, vs = f["triggers"], f["verify"]
        script = [self.trigger_md(t) for t in ts]
        if len(script) + len(vs) > cap:
            groups = {}
            for t in ts:
                groups.setdefault(t["kind"], []).append(t)
            script = [self.merged_md(k, g) for k, g in groups.items()]
        model = [v["text"] for v in vs]
        return ["- " + x for x in (script + model)[:cap]]

    def tested(self):
        return str(self.author.get("tested") or "").strip()

    def untested(self):
        """新增或签名变化的公开函数里,按名字搜过测试、但改动的测试文件里没有提到的。没搜过(tests 为 null)的不算。"""
        return [s for s in self.code_symbols() if s.get("change") in ("added", "signature_changed") and s.get("exported") is not False
                and s.get("tests") is not None and not any(x.get("changed") for x in s["tests"])]

    def untested_text(self):
        u = self.untested()
        return "%d 个新增/签名变化的函数在改动的测试里没有出现:%s" % (len(u), short((s["name"] for s in u), MAX_UNTESTED)) if u else ""

    # ---------- 需求对照、请重点看 ----------
    def requirements(self):
        """[(验收标准, [代码块编号])];没有验收标准时是空列表,整段不出现。"""
        criteria = analysis_check.acceptance(self.d)
        mapped = {r["criterion"]: [h for h in r.get("hunks", []) if h in self.hunks] for r in self.analysis.get("requirements") or []}
        return [(c, mapped.get(c, [])) for c in criteria]

    def focus(self):
        return [x for x in (self.author.get("focus") or []) if isinstance(x, dict) and x.get("where")][:MAX_FOCUS]

    # ---------- 渲染 ----------
    def facts_once(self):
        changes = self.changes()
        verify = self.model_verify(len(changes))
        if verify:
            self.notes.append(AI_VERIFY)
        return {"layers": self.layers(), "triggers": self.triggers(), "reqs": self.requirements(), "changes": changes,
                "other": self.other(changes), "verify": verify, "summary": self.summary(), "focus": self.focus()}

    def md_ctx(self, f, cut):
        ctx = {"summary": f["summary"][0], "basic": "\n".join("- %s:%s" % (k, v) for k, v, _ in self.basic()),
               "verify_note": VERIFY_NOTE}
        changes, other = f["changes"], f["other"]
        lines = ["%d. %s(%s)" % (i, c["text"], "、".join(self.hunk_ref(h) for h in c["hunks"] if h in self.hunks))
                 for i, c in enumerate(changes, 1)]
        ctx["changes_more"] = ""
        if other and changes and "other" in cut:
            ctx["changes_more"] = "(另有 %d 处未归组,见完整版)" % len(other)
        elif other:
            lines.append("%d. %s" % (len(lines) + 1, self.other_text(other, bool(changes))))
        ctx["changes"] = "\n".join(lines)
        ctx["impact"] = self.impact_md(f["layers"], cut)

        ctx["focus"] = "\n".join("%d. %s — %s" % (i, self.loc(*split_where(x["where"])), x.get("ask", "")) for i, x in enumerate(f["focus"], 1))

        verify = self.verify_lines(f, MAX_VERIFY_CUT if "verify" in cut else MAX_VERIFY)
        if self.tested():
            verify.append("- 执行记录:作者提供:%s" % self.tested())
        ctx["verify"] = "\n".join(verify)

        reqs = f["reqs"]
        if not reqs:
            ctx["requirements"] = ""
        elif "requirements" in cut:
            ctx["requirements"] = "- %d 条验收标准,%d 条标出了对应改动,逐条见完整版" % (len(reqs), sum(1 for _, ids in reqs if ids))
        else:
            lines = ["- %s:%s" % (c, short(dict.fromkeys(self.hunks[h]["file"] for h in ids), 3, lambda p: "`%s`" % p)
                                  if ids else "没有标出对应的改动") for c, ids in reqs[:MAX_REQS]]
            if len(reqs) > MAX_REQS:
                lines.append("- 另有 %d 条,见完整版" % (len(reqs) - MAX_REQS))
            ctx["requirements"] = "\n".join(lines)
        return ctx

    def markdown(self):
        """按 templates/describe.md 填出贴进 PR/MR 描述的那份。事实在这里算好,模板只管结构;超长时按 TRIM 逐项多裁。"""
        template, f = read_template("describe.md"), self.facts_once()
        for i in range(len(TRIM) + 1):
            cut = TRIM[:i]
            text = render(template, self.md_ctx(f, cut))
            if "blank" in cut:
                text = drop_blanks(text)
            if body_lines(text) <= MAX_LINES:
                break
        return text

    def html_page(self):
        """按 templates/describe.html 填出完整版网页。表格的行(tr、td)在这里生成,表头和页面结构在模板里。"""
        e = html.escape
        f = self.facts_once()
        one, by = f["summary"]

        def rows(data):
            return "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in data)

        def where(path, line=None):
            return self.loc(path, line, md=False) if path else ""

        def anchors(xs):
            return "、".join("<code>%s</code>" % e(a) for a in xs)

        ctx = {"info_rows": rows([[e(k), v] for k, _, v in self.basic()])}

        changes, other = f["changes"], f["other"]
        data = [[str(i), ("<b>%s</b><br>" % e(c["title"]) if c.get("title") else "") + ticks_html(c["text"]),
                 anchors(c.get("anchors") or []), "<br>".join(self.hunk_ref(h, md=False) for h in c["hunks"] if h in self.hunks)]
                for i, c in enumerate(changes, 1)]
        if other:
            data.append([str(len(data) + 1), e(self.other_text(other, bool(changes))) + "<br><small>没有被上面任何一组引用的代码块,由脚本补上</small>",
                         "", "<br>".join(self.hunk_ref(h["id"], md=False) for h in other)])
        ctx["change_rows"] = rows(data)
        ctx["change_note"] = e("按功能分组由 AI 依据 diff 起草,锚点都已核对在所引代码块的改动行里") if changes else ""

        layers = f["layers"]
        ctx["overview"] = e(self.overview(layers))
        data = []
        for key, title in sorted(LAYERS, key=lambda kt: kt[0] == "res"):   # 网页版的表里资源放在最后
            for x in layers[key]:
                note = e(x["extra"])
                s = x.get("symbol")
                if s and s.get("old_sig") and s.get("new_sig") and x["change"] == "signature_changed":
                    note += "<br><small>%s → %s</small>" % (e(s["old_sig"]), e(s["new_sig"]))
                if x.get("outside"):
                    note += "<br>" + "<br>".join(where(c["file"], c.get("line")) for c in x["outside"])
                if s and s.get("skipped") == "too_many_hits":
                    note += "<br>" + e("调用命中过多,只给数量(%d 处)" % s.get("callers_total", 0))
                data.append([e(title), e(x["what"]),
                             "<code>%s</code>" % e(x["name"]) if x["name"] is not None else "",
                             where(x["file"], x["line"]), note])
        ctx["impact_rows"] = rows(data)
        n = len(self.body_only())
        notes = ([self.scope(layers)] if self.scope(layers) else []) + \
                (["未覆盖:另有 %d 个函数只改了实现,行为变化不在此列" % n] if n else [])
        ctx["impact_notes"] = "<br>".join(e(x) for x in notes)
        ctx["impact_method"] = e(";".join([self.facts.get("method") or af.METHOD] + list(self.facts.get("limits") or [])))

        ctx["focus_rows"] = rows([[self.loc(*split_where(x["where"]), md=False), e(x.get("ask", ""))] for x in f["focus"]])

        ctx["verify_note"] = e(VERIFY_NOTE)
        data = []
        for t in f["triggers"]:
            pre, post = self.trigger_words(t)
            step = "%s<code>%s</code>%s" % (e(pre), e(t["label"]), e(post)) + (" → 进入 <code>%s</code>" % e(t["handler"]) if t.get("handler") else "")
            data.append([step, "", "", where(t["file"], t.get("line")), e("脚本认出的入口")])
        for v in f["verify"]:
            data.append([ticks_html(v["text"]), e("第 %d 组" % v["change"]), anchors(v.get("anchors") or []),
                         "<br>".join(self.hunk_ref(h, md=False) for h in v.get("hunks") or [] if h in self.hunks), e("AI 起草,请核对")])
        ctx["verify_rows"] = rows(data)
        cases, asserts = self.entry.get("cases") or [], self.entry.get("assertions") or []
        ctx["case_rows"] = rows([[e(c["name"]), where(c["file"], c.get("line"))] for c in cases])
        ctx["assert_rows"] = rows([["<code>%s</code>" % e(a["text"]), where(a["file"], a.get("line"))] for a in asserts])
        ctx["command"] = e(self.entry.get("command") or "")
        ctx["untested"] = e(self.untested_text())
        ctx["tests"] = "1" if cases or asserts or ctx["command"] or ctx["untested"] else ""
        ctx["record"] = e("作者提供:%s" % self.tested()) if self.tested() else ""
        ctx["verify"] = "1" if data or ctx["tests"] or ctx["record"] else ""

        ctx["req_rows"] = rows([[e(c), "<br>".join(where(self.hunks[h]["file"], self.hunks[h].get("start")) for h in ids)
                                 if ids else e("没有标出对应的改动")] for c, ids in f["reqs"]])

        ctx["version"] = e(version())
        ctx["sha"] = e(self.sha[:8])
        ctx["title"] = e(self.meta.get("title") or one or "PR/MR 描述")
        ctx["one"] = e(one)
        ctx["by"] = e(by)
        ctx["now"] = e(now_text())
        return render(read_template("describe.html"), ctx)


def version():
    v = crlib.load_json(os.path.join(HERE, "..", "..", "..", ".claude-plugin", "plugin.json"), {})
    return v.get("version", "") if isinstance(v, dict) else ""


def now_text():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- 模板 ----------
# 模板是普通文本,只有四种标记(名字由字母、数字、下划线组成):
#   {{名字}}               换成同名的值
#   {{#名字}}…{{/名字}}    值不为空才保留这一段;块里的 {{名字}} 就是这个块自己的值
#   {{^名字}}…{{/名字}}    值为空才保留这一段
#   {{! 说明 }}            注释,不输出(说明里不要有花括号)
# 标记单独占一行时整行不输出,不留空行。同名的块不能嵌套。值原样放入,不再解析其中的标记;
# 模板里写了脚本没有提供的名字,或者块没有闭合,都直接报错,不静默留空。
TEMPLATES = os.path.join(HERE, "..", "templates")
TAG = re.compile(r"\{\{([#^/]?)\s*(\w+)\s*\}\}")
TAG_LINE = re.compile(r"^[ \t]*(\{\{[#^/!][^{}\n]*\}\})[ \t]*\n", re.M)
COMMENT = re.compile(r"\{\{![^{}\n]*\}\}")


class TemplateError(Exception):
    pass


def read_template(name):
    path = os.path.join(TEMPLATES, name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as err:
        raise TemplateError("读不到模板 %s:%s" % (os.path.normpath(path), err.strerror))


def render(template, values):
    try:
        # 单独占一行的标记连同换行一起去掉:注释整行不要,块标记只留标记本身
        text = TAG_LINE.sub(lambda m: "" if m.group(1).startswith("{{!") else m.group(1), template)
        return _render(COMMENT.sub("", text), values)
    except TemplateError as err:
        raise TemplateError("模板有误:%s" % err)


def _render(text, values):
    out, pos = [], 0
    while True:
        m = TAG.search(text, pos)
        if not m:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:m.start()])
        kind, name = m.groups()
        if name not in values:
            raise TemplateError("{{%s}} 不是 PR/MR 描述的内容" % name)
        if kind == "":
            out.append(str(values[name]))
            pos = m.end()
            continue
        if kind == "/":
            raise TemplateError("{{/%s}} 前面没有对应的开头" % name)
        end = re.compile(r"\{\{/\s*%s\s*\}\}" % name).search(text, m.end())
        if not end:
            raise TemplateError("{{%s%s}} 没有对应的 {{/%s}}" % (kind, name, name))
        inner = _render(text[m.end():end.start()], values)   # 不保留时也渲染一遍,模板里的错误才不会被藏起来
        if bool(values[name]) == (kind == "#"):
            out.append(inner)
        pos = end.end()


def options(argv):
    args, opts = [], {}
    i = 0
    while i < len(argv):
        if argv[i] in ("--tested", "--focus"):
            if i + 1 >= len(argv):
                sys.exit("describe: %s 需要一个值" % argv[i])
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1
    return args, opts


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    args, opts = options(argv)
    if len(args) != 1:
        sys.exit("用法: describe.py DIR [--tested TEXT] [--focus JSON]")
    d = os.path.abspath(args[0])
    if not os.path.isfile(os.path.join(d, "meta.txt")):
        sys.exit("describe: %s 不是 collect.sh 输出的材料目录" % d)
    out = output_dir(d, crlib.read_kv(os.path.join(d, "meta.txt")))
    os.makedirs(out, exist_ok=True)
    if opts:
        author = crlib.load_json(os.path.join(out, "author.json"), {}) or {}
        if "tested" in opts:
            author["tested"] = opts["tested"].strip()
        if "focus" in opts:
            author["focus"] = parse_focus(opts["focus"]) if opts["focus"].strip() else []
        crlib.save_json(os.path.join(out, "author.json"), author)
    if analysis_check.replies(d):
        r = analysis_check.check(d)
        print("STATUS: %s" % r["status"])
        for x in r["dropped"]:
            print("DROPPED: %s — %s" % (x["what"], x["why"]))
    note = Note(d, out)
    try:
        text, page = note.markdown(), note.html_page()
    except TemplateError as err:
        sys.exit("describe: %s" % err)
    mp, hp = os.path.join(out, "describe.md"), os.path.join(out, "describe.html")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(text)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(page)

    def url(p):
        return "file://" + p.replace("%", "%25").replace(" ", "%20").replace("#", "%23")
    print("describe=%s" % mp)
    print("md_link=%s" % url(mp))
    print("html=%s" % hp)
    print("link=%s" % url(hp))
    for n in dict.fromkeys(note.notes):
        print("NOTE: %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
