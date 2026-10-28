"""MR 说明:给 MR 评审人看的改动说明,开发者贴进 MR 描述;另有一份完整的网页版。

用法: python3 mr_note.py DIR [--latest]
  DIR       一次审查的记录目录(save-review.sh 输出的 dir=),或 collect-diff.sh 的输出目录;
            后者已经保存过时,写进它保存后的记录目录(record.txt)
  --latest  DIR 只用来找到这个目标,实际用它最近一次的审查记录(表态、--fix 之后重新生成时用)

写出 DIR/mr.md(贴进 MR 描述,30 行以内,包在 crosscheck:begin/end 标记里)和 DIR/mr.html(完整版),并输出:
  mr=<mr.md 的路径>  md_link=<mr.md 的 file:// 链接>  html=<mr.html 的路径>  link=<mr.html 的 file:// 链接>
  page=<配置的 tools.doc_publisher 说明(一句话,原样照做),没配置为空>  page_url=<之前生成过的网页链接>
  update=<把描述更新到 MR 的命令,由作者自己执行>
  NOTE: …  需要转告作者的话(没推送、审查后又有提交、一句话由 AI 起草等)

内容只有事实和链接,不下结论:基本信息和需求链接、一句话、请重点看、阅读顺序、可跳过、影响面、需注意、
可能相关的测试、CR 情况。来源:sources.json 和分支的来源记录、analysis-facts.json(脚本)、
analysis.json(analyst,经 analysis_check.py 校验)、findings.json / final.json / 判断记录。
"""
import datetime
import html
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import crlib
import sources as srcmod

BEGIN, END = "<!-- crosscheck:begin -->", "<!-- crosscheck:end -->"
SEV = {"critical": "严重", "major": "重要", "minor": "轻微"}
CAT = {"bug": "bug", "security": "安全", "intent": "需求核对", "rules": "规范"}
VERDICT = {"invalid": "作者判为不成立", "wontfix": "作者决定不修", "valid": "作者认可,待修", "disputed": "有争议"}
GROUP_NAME = ["接口和类型", "实现", "配置", "测试"]
SKIP_NAME = {"generated": "生成代码", "whitespace_only": "纯格式化", "rename_only": "纯重命名", "lockfile": "lock 文件"}
ATT_NAME = {"migration": "数据库迁移", "config_key": "配置键", "env_var": "环境变量", "dependency": "依赖变化", "signature": "接口签名变化"}
MAX_LINES = 30
MAJOR_SHOWN = 4   # CR 情况里最多展开的严重和重要发现条数


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def short(items, n, fmt=str):
    items = list(items)
    s = "、".join(fmt(x) for x in items[:n])
    return s + (" 等 %d 个" % len(items) if len(items) > n else "")


def split_where(where):
    m = re.match(r"(.+):(\d+)$", where or "")
    return (m.group(1), m.group(2)) if m else (where, "")


def resolve_dir(d, latest):
    # 传入的是临时的审查目录(review_dir)而它已经保存过时,MR 说明写进保存后的记录目录(save-review.sh 写的 record.txt)
    rec = (crlib.read_text(os.path.join(d, "record.txt")) or "").strip()
    if rec and os.path.isfile(os.path.join(rec, "meta.txt")):
        d = rec
    meta = crlib.read_kv(os.path.join(d, "meta.txt"))
    if latest and meta.get("store_dir"):
        try:
            stamp = open(os.path.join(meta["store_dir"], "latest"), encoding="utf-8").readline().strip()
            if stamp and os.path.isdir(os.path.join(meta["store_dir"], stamp)):
                return os.path.join(meta["store_dir"], stamp)
        except OSError:
            pass
    return d


class Note:
    def __init__(self, d):
        self.d = d
        self.meta = crlib.read_kv(os.path.join(d, "meta.txt"))
        self.root = self.meta.get("repo_root", "")
        self.facts = crlib.load_json(os.path.join(d, "analysis-facts.json"), {}) or {}
        self.analysis = crlib.load_json(os.path.join(d, "analysis.json"), {}) or {}
        if self.analysis.get("status") == "failed":
            self.analysis = {}
        self.src = crlib.load_json(os.path.join(d, "sources.json"), {}) or {}
        self.store_path = srcmod.store_path(self.meta)
        self.store = srcmod.load_store(self.store_path)
        self.conf = srcmod.settings()
        self.notes = []
        self._findings = None
        self.hunks = {h["id"]: h for h in self.facts.get("hunks", []) if isinstance(h, dict) and h.get("id")}
        self.sha = self.meta.get("head_sha", "")
        self.link_base = self._link_base()
        if self.meta.get("merged") == "yes":
            self.notes.append("这个 MR 已合入:说明照常生成,但描述已无法影响合入")
        if self.meta.get("code_host_reason"):
            self.notes.append("MR 信息没有读到(tools.code_host:%s),标题和描述没有用上" % self.meta["code_host_reason"])

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
            self.notes.append("工作区有未提交的改动,MR 里的代码和审查的不一致;提交并重新审查后再贴")
            return None
        github = self.meta.get("platform") == "github" or base.startswith("https://github.com/")
        return "%s/%s/%s/" % (base, "blob" if github else "-/blob", sha)

    def loc(self, path, line=None, md=True):
        text = "%s:%s" % (path, line) if line else path
        if not self.link_base:
            return "`%s`" % text if md else html.escape(text)
        url = self.link_base + path + ("#L%s" % line if line else "")
        return "[`%s`](%s)" % (text, url) if md else '<a href="%s"><code>%s</code></a>' % (html.escape(url), html.escape(text))

    # ---------- 各部分的数据 ----------
    def sources(self):
        out = []
        items = {x.get("kind"): x for x in self.src.get("items", [])}
        for k, c in self.conf["kinds"].items():
            it = dict(items.get(k) or {})
            if isinstance(self.store.get(k), dict):
                it.update({x: y for x, y in self.store[k].items() if y})
            st = it.get("status", "missing")
            if st == "missing" and k not in self.conf["required"]:
                continue
            if st == "given" and it.get("url"):
                val = it["url"] + (" 「%s」" % it["title"] if it.get("title") else "")
                if it.get("read_error"):
                    val += "(内容未读取:%s)" % it["read_error"]
            elif st == "given":
                val = "见提交说明或描述里的编号"
            elif st == "skip":
                val = "未提供(作者跳过)"
            elif st == "none":
                val = "无需求单" + (":%s" % it["type"] if it.get("type") else "")
            elif st == "unasked":
                val = "未提供(非交互运行,没有询问)"
            else:
                val = "未提供"
            out.append((c["label"], val))
        return out

    def state(self):
        """基本信息里的状态行:已合入、MR 信息没有读到。"""
        out = []
        if self.meta.get("merged") == "yes":
            tb = self.meta.get("target_branch", "")
            out.append(("状态", "已合入%s,描述已无法影响合入" % (" " + tb if tb else "")))
        if self.meta.get("code_host_reason"):
            out.append(("MR 信息", "没有读到(tools.code_host:%s)" % self.meta["code_host_reason"]))
        return out

    def summary(self):
        intent = (self.store.get("intent") or "").strip()
        if intent:
            return intent, "作者"
        s = self.analysis.get("summary")
        if s:
            self.notes.append("「一句话」是 AI 按需求材料起草的,贴之前请确认,或用 --intent 写你自己的")
            return s["text"], "AI 起草"
        title = self.meta.get("title", "")
        return (title, "MR 标题") if title else ("", "")

    def groups(self):
        if self.analysis.get("groups"):
            return [{"title": g["title"], "purpose": (g.get("purpose") or {}).get("text", ""), "files": g.get("files", []),
                     "hunks": g["hunks"]} for g in self.analysis["groups"]]
        buckets = {i: [] for i in range(4)}
        contract = {s.get("hunk") for s in self.facts.get("symbols", [])
                    if s.get("change") == "signature_changed" or s.get("kind") in ("type", "class")}
        for hid, h in self.hunks.items():
            kind = h.get("kind", "other")
            if kind == "generated":
                continue
            r = 3 if kind == "test" else 2 if kind == "config" else 0 if hid in contract else 1
            buckets[r].append(hid)
        out = []
        for i, ids in buckets.items():
            if ids:
                files = list(dict.fromkeys(self.hunks[h]["file"] for h in ids))
                out.append({"title": GROUP_NAME[i], "purpose": "", "files": files, "hunks": ids})
        return out

    def skippable(self):
        by = {}
        for x in self.facts.get("skippable", []):
            by.setdefault(x.get("reason"), []).append(x.get("file"))
        return {k: list(dict.fromkeys(v)) for k, v in by.items() if v}

    def impact(self):
        out = []
        for s in self.facts.get("symbols", []):
            if s.get("change") not in ("signature_changed", "removed"):
                continue
            outside = [c for c in s.get("callers", []) if not c.get("in_diff")]
            total = s.get("callers_total", len(s.get("callers", [])))
            out.append(dict(s, outside=outside, total=total))
        out.sort(key=lambda s: (-len(s["outside"]), s.get("file", "")))
        return out

    def tests(self):
        seen = {}
        for s in self.facts.get("symbols", []):
            for t in s.get("tests", []):
                seen[t["file"]] = seen.get(t["file"]) or t.get("changed")
        return seen

    def findings(self):
        """返回 (已修复条数, 未修复的发现);每条未修复的带上作者的判断 verdict 和理由 reason。"""
        if self._findings is None:
            fixed = self.fixed_keys()
            verdicts = self.verdicts()
            standing = []
            for f in crlib.load_json(os.path.join(self.d, "findings.json"), []):
                if crlib.finding_key(f) not in fixed:
                    v = verdicts.get(crlib.finding_key(f), {})
                    standing.append(dict(f, verdict=v.get("verdict", ""), reason=v.get("reason", "")))
            self._findings = (len(fixed), standing)
        return self._findings

    def verdicts(self):
        store = self.meta.get("store_dir", "")
        return crlib.feedback_entries(os.path.dirname(store)) if store else {}

    def fixed_keys(self):
        """已修复:这个目标的历次增量审查里因为代码变了而不再成立的,加上用 --fix 修掉的。"""
        fixed, store = set(), self.meta.get("store_dir", "")
        for stamp in (os.listdir(store) if store and os.path.isdir(store) else []):
            final = crlib.load_json(os.path.join(store, stamp, "final.json"), {})
            fixed |= {crlib.finding_key(r) for r in final.get("resolved") or [] if "代码变了" in str(r.get("resolved_reason", ""))}
        fixed |= {k for k, v in self.verdicts().items()
                  if v.get("verdict") == "valid" and "--fix" in str(v.get("reason", "")) and v.get("target") == self.meta.get("target_key")}
        return fixed

    def focus(self):
        chosen = [x for x in (self.store.get("focus") or []) if isinstance(x, dict) and x.get("where")]
        if chosen:
            return chosen[:3], True
        auto = []
        for s in self.impact():
            if s["outside"]:
                auto.append({"where": "%s:%s" % (s["file"], s.get("line", "")), "ask": "`%s` 签名%s,另有 %d 处调用" % (
                    s["name"], "变化" if s["change"] == "signature_changed" else "被删除", s["total"])})
        for t in ("migration", "env_var"):
            for a in self.facts.get("attention", []):
                if a.get("type") == t:
                    # 位置已经是文件路径,说明里不再重复
                    auto.append({"where": "%s%s" % (a.get("file"), ":%s" % a["line"] if a.get("line") else ""),
                                 "ask": a.get("detail", "").replace(a.get("file", ""), "").strip()})
        if not auto:
            # 没有别的可列时才用发现,而且只列 CR 情况里没有展示的,避免同一条出现两次
            _, standing = self.findings()
            for f in self.major_of(standing)[MAJOR_SHOWN:]:
                if f.get("verdict") != "invalid":
                    auto.append({"where": "%s:%s" % (f.get("file"), f.get("line")), "ask": f.get("summary", "")})
        return auto[:3], False

    @staticmethod
    def major_of(standing):
        return [f for f in standing if f.get("severity") in ("critical", "major")]

    def reviewed_newer(self):
        branch = self.meta.get("source_branch", "")
        if not branch or not self.sha or not self.root:
            return ""
        refs = ["origin/%s^{commit}" % branch]
        if self.meta.get("mode") != "pr":
            refs.insert(0, branch + "^{commit}")   # PR/MR 只和本地已知的平台上的分支头比,本地分支可能有没推送的提交
        now = next((x for x in (git(self.root, "rev-parse", "-q", "--verify", r) for r in refs) if x), "")
        if now and now != self.sha:
            n = git(self.root, "rev-list", "--count", "%s..%s" % (self.sha, now))
            if n.isdigit() and int(n) > 0:
                return n
        return ""

    # ---------- 渲染 ----------
    def markdown(self):
        blocks = []   # (名字, 行);超长时按 OPTIONAL 的顺序整块去掉
        one, by = self.summary()
        head = ["**%s**" % one] if one else []
        info = ["%s:%s" % (lab, val) for lab, val in self.state() + self.sources()]
        sc = self.facts.get("scope") or {}
        size = "%s 个文件 +%s/−%s" % (sc.get("files", self.meta.get("files", "?")), sc.get("added", "?"), sc.get("deleted", "?"))
        tb = self.meta.get("target_branch", "")
        branch = "%s → %s" % (self.meta.get("source_branch") or "?", tb) if tb else ""
        eff = self.facts.get("effort") or {}
        info += [x for x in (branch, size, "审查量 %s/5" % eff["score"] if eff.get("score") else "") if x]
        blocks.append(("info", head + [" · ".join(info)]))

        focus, chosen = self.focus()
        if focus:
            b = ["### 请重点看" if chosen else "### 可能值得重点看(自动列出)"]
            for i, x in enumerate(focus, 1):
                path, line = split_where(x["where"])
                b.append("%d. %s — %s" % (i, self.loc(path, line), x.get("ask", "")))
            blocks.append(("focus", b))

        groups = self.groups()
        if groups:
            b = ["### 阅读顺序"]
            for i, g in enumerate(groups[:5], 1):
                files = short(g["files"], 3, lambda f: "`%s`" % f)
                b.append("%d. %s%s:%s" % (i, g["title"], "(%s)" % g["purpose"] if g.get("purpose") else "", files))
            blocks.append(("read", b))
            skip = self.skippable()
            if skip:
                blocks.append(("skip", ["可跳过:" + "、".join("%s %d 个" % (SKIP_NAME.get(k, k), len(v)) for k, v in skip.items())]))

        imp = [s for s in self.impact() if s["outside"] or s["change"] == "removed"]
        if imp:
            b = ["### 影响面", "> 只按名字在本仓库文本匹配、只看签名变化;签名不变的行为变化不在这里,由 CR 报告负责"]
            for s in imp[:5]:
                where = short(s["outside"], 2, lambda c: self.loc(c["file"], c.get("line")))
                extra = "(同名歧义)" if s.get("ambiguous") else ""
                b.append("- `%s` %s%s:本次之外 %d 处调用 %s" % (
                    s["name"], "签名变化" if s["change"] == "signature_changed" else "被删除", extra, len(s["outside"]), where))
            if len(imp) > 5:
                b.append("- 另有 %d 个,见完整版" % (len(imp) - 5))
            blocks.append(("impact", b))

        att = [a for a in self.facts.get("attention", []) if a.get("type") != "signature"]
        if att:
            b = ["### 需注意"] + ["- %s:%s" % (ATT_NAME.get(a.get("type"), a.get("type")), a.get("detail") or a.get("file")) for a in att[:4]]
            if len(att) > 4:
                b.append("- 另有 %d 处,见完整版" % (len(att) - 4))
            blocks.append(("attention", b))

        tests = self.tests()
        tested = (self.store.get("tested") or "").strip()
        if tests or tested:
            b = ["### 测试"]
            if tested:
                b.append("怎么验证的:%s" % tested)
            if tests:
                b.append("可能相关的测试文件(未执行):%s" % short(sorted(tests, key=lambda f: not tests[f]), 3,
                                                          lambda f: "`%s`%s" % (f, "(本次改过)" if tests[f] else "")))
            blocks.append(("tests", b))

        cr = self.cr_lines()
        page = self.store.get("page_url", "")
        tail = ["基于提交 %s" % (self.sha[:8] or "?")] + (["[完整版](%s)" % page] if page else [])
        cr += [" · ".join(tail)]
        blocks.append(("cr", cr))

        def lines_of(bs):
            out = []
            for name, b in bs:
                if out and name != "skip":
                    out.append("")
                out += b
            return out
        for drop in ("skip", "tests", "attention"):
            if len(lines_of(blocks)) <= MAX_LINES:
                break
            blocks = [x for x in blocks if x[0] != drop]
        return lines_of(blocks)

    def cr_lines(self, md=True):
        fixed, standing = self.findings()
        depth = {"quick": "quick", "deep": "deep"}.get(self.meta.get("depth", ""), "standard")
        head = "**CR 情况(作者本地运行)** · crosscheck %s · %s" % (version(), depth)
        out = [head]
        major = self.major_of(standing)
        minor = [f for f in standing if f.get("severity") not in ("critical", "major")]
        parts = ["已修复 %d 条" % fixed]
        if major:
            parts.append("未修复的严重和重要 %d 条" % len(major))
        if minor:
            parts.append("轻微 %d 条未展开" % len(minor))
        if not major and not minor:
            parts.append("没有未修复的发现")
        out.append(";".join(parts) + (":" if major else ""))
        for f in major[:MAJOR_SHOWN]:
            why = VERDICT.get(f.get("verdict"), "未表态")
            if f.get("reason"):
                why += ":" + f["reason"]
            if f.get("raised_in_discussion"):
                why += "(讨论中已有人提出,代码仍未改)"
            out.append("- [%s] %s %s — %s" % (SEV.get(f.get("severity"), ""), self.loc(f.get("file", ""), f.get("line")), f.get("summary", ""), why))
        if len(major) > MAJOR_SHOWN:
            out.append("- 另有 %d 条,见完整版" % (len(major) - MAJOR_SHOWN))
        newer = self.reviewed_newer()
        if newer:
            out.append("> 审查之后又有 %s 个提交,未经 crosscheck 审查" % newer)
            self.notes.append("审查之后分支上又有 %s 个提交,建议重新审查后再贴" % newer)
        return out

    def html_page(self):
        e = html.escape
        one, by = self.summary()
        sec = []

        def table(head, rows):
            if not rows:
                return ""
            return "<div class=t><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>" % (
                "".join("<th>%s</th>" % e(h) for h in head), "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows))

        info = [(lab, e(val)) for lab, val in self.state() + self.sources()]
        sc = self.facts.get("scope") or {}
        info.append(("分支", e("%s → %s" % (self.meta.get("source_branch") or "?", self.meta.get("target_branch") or "?"))))
        info.append(("规模", e("%s 个文件 +%s/−%s" % (sc.get("files", "?"), sc.get("added", "?"), sc.get("deleted", "?")))))
        eff = self.facts.get("effort") or {}
        if eff.get("score"):
            info.append(("审查量", e("%s/5(%s)" % (eff["score"], eff.get("basis", "")))))
        sec.append("<section><h2>基本信息</h2>%s</section>" % table(["项", "内容"], [[e(a), b] for a, b in info]))

        focus, chosen = self.focus()
        if focus:
            rows = []
            for x in focus:
                path, line = split_where(x["where"])
                rows.append([self.loc(path, line, md=False), e(x.get("ask", ""))])
            sec.append("<section><h2>%s</h2>%s</section>" % ("请重点看" if chosen else "可能值得重点看(自动列出)", table(["位置", "看什么"], rows)))

        groups = self.groups()
        if groups:
            rows = []
            for i, g in enumerate(groups, 1):
                spans = []
                for h in g["hunks"]:
                    hk = self.hunks.get(h, {})
                    spans.append(self.loc(hk.get("file", ""), hk.get("start"), md=False) + (" <small>%s</small>" % e(hk.get("func", "")) if hk.get("func") else ""))
                rows.append([str(i), e(g["title"]), e(g.get("purpose") or ""), "<br>".join(spans)])
            skip = self.skippable()
            extra = ""
            if skip:
                extra = "<p class=muted>可跳过:" + ";".join("%s:%s" % (SKIP_NAME.get(k, k), e("、".join(v))) for k, v in skip.items()) + "</p>"
            sec.append("<section><h2>阅读顺序</h2>%s%s</section>" % (table(["#", "分组", "目的", "代码块"], rows), extra))

        reqs = self.analysis.get("requirements") or []
        if reqs:
            rows = [[e(r["criterion"]), "<br>".join(self.loc(self.hunks[h]["file"], self.hunks[h].get("start"), md=False) for h in r["hunks"] if h in self.hunks)] for r in reqs]
            sec.append("<section><h2>验收标准对应的改动</h2><p class=muted>只标出改动落在哪里,不判断是否做到。</p>%s</section>" % table(["验收标准", "改动位置"], rows))

        imp = self.impact()
        if imp:
            rows = []
            for s in imp:
                callers = "<br>".join(self.loc(c["file"], c.get("line"), md=False) + (" <small>(本次改过)</small>" if c.get("in_diff") else "") for c in s.get("callers", []))
                if s.get("skipped") == "too_many_hits":
                    callers = e("命中过多,只给数量")
                elif s.get("skipped") == "common_name":
                    callers = e("名字太常见,没有搜索")
                note = ("同名歧义;" if s.get("ambiguous") else "") + ("另有 %d 处未列出" % (s["total"] - len(s.get("callers", []))) if s.get("callers_truncated") else "")
                sig = e(s.get("old_sig") or "") + (" → " + e(s.get("new_sig") or "") if s.get("new_sig") else "")
                rows.append(["<code>%s</code>" % e(s["name"]), e("签名变化" if s["change"] == "signature_changed" else "被删除"), sig, str(s["total"]), callers, e(note)])
            limits = self.facts.get("limits") or []
            sec.append("<section><h2>影响面</h2><p class=muted>%s。签名不变的行为变化不在这里,由 CR 报告负责;依赖注入、反射、跨服务调用、配置里的动态引用搜不到。%s</p>%s</section>" % (
                e(self.facts.get("method") or "按名字在本仓库文本匹配,只看签名变化"), e(";".join(limits)),
                table(["符号", "变化", "签名", "调用", "调用方", "说明"], rows)))

        att = self.facts.get("attention", [])
        if att:
            rows = [[e(ATT_NAME.get(a.get("type"), a.get("type", ""))), self.loc(a.get("file", ""), a.get("line"), md=False), e(a.get("detail", ""))] for a in att]
            sec.append("<section><h2>需注意</h2>%s</section>" % table(["类型", "位置", "说明"], rows))

        tests = self.tests()
        tested = (self.store.get("tested") or "").strip()
        if tests or tested:
            body = "<p>怎么验证的:%s</p>" % e(tested) if tested else ""
            body += table(["可能相关的测试文件(未执行)", "本次改过"], [[self.loc(f, md=False), "是" if c else ""] for f, c in sorted(tests.items())])
            sec.append("<section><h2>测试</h2>%s</section>" % body)

        fixed, standing = self.findings()
        rows = []
        for f in standing:
            why = VERDICT.get(f.get("verdict"), "未表态") + (":" + f["reason"] if f.get("reason") else "")
            if f.get("raised_in_discussion"):
                why += "(讨论中已有人提出,代码仍未改)"
            rows.append([e(SEV.get(f.get("severity"), "")), e(CAT.get(f.get("category"), f.get("category", ""))),
                         self.loc(f.get("file", ""), f.get("line"), md=False), e(f.get("summary", "")), e(why)])
        newer = self.reviewed_newer()
        sec.append("<section><h2>CR 情况(作者本地运行)</h2><p>crosscheck %s · %s · 基于提交 <code>%s</code> · 已修复 %d 条%s</p>%s</section>" % (
            e(version()), e(self.meta.get("depth", "standard")), e(self.sha[:8]), fixed,
            e(";审查之后又有 %s 个提交,未经审查" % newer) if newer else "",
            table(["严重程度", "维度", "位置", "问题", "作者的判断"], rows) or "<p>没有未修复的发现。</p>"))

        title = e(self.meta.get("title") or one or "MR 说明")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return PAGE % {"title": title, "one": e(one) if one else "", "by": e(by), "body": "\n".join(sec), "now": e(now), "sha": e(self.sha[:8])}


def version():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "..", "..", ".claude-plugin", "plugin.json"),):
        v = crlib.load_json(p, {})
        if isinstance(v, dict) and v.get("version"):
            return v["version"]
    return ""


PAGE = """<title>%(title)s</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5d6570;--line:#e3e0d8;--card:#ffffff;--accent:#2f5d8a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15181c;--fg:#e6e8eb;--muted:#9aa3ad;--line:#2b3138;--card:#1c2026;--accent:#7fb0e0;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#15181c;--fg:#e6e8eb;--muted:#9aa3ad;--line:#2b3138;--card:#1c2026;--accent:#7fb0e0;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;
 padding-block:24px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:980px;margin:0 auto;display:grid;gap:18px}
h1{font-size:22px;margin:0;text-wrap:balance}h2{font-size:16px;margin:0 0 8px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;min-width:0}
.muted,small{color:var(--muted)}a{color:var(--accent)}code{font:13px ui-monospace,Menlo,monospace}
.t{overflow-x:auto}table{border-collapse:collapse;width:100%%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
</style>
<main>
<header><h1>%(title)s</h1><p>%(one)s <small>%(by)s</small></p>
<p class=muted>MR 说明 · 最后更新 %(now)s · 基于提交 <code>%(sha)s</code> · 只列事实和链接,不下结论</p></header>
%(body)s
</main>
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    d = resolve_dir(os.path.abspath(argv[0]), "--latest" in argv)
    if not os.path.isfile(os.path.join(d, "meta.txt")):
        sys.exit("mr_note: %s 不是审查目录" % d)
    note = Note(d)
    text = "\n".join([BEGIN] + note.markdown() + [END]) + "\n"
    with open(os.path.join(d, "mr.md"), "w", encoding="utf-8") as f:
        f.write(text)
    page = note.html_page()
    hp = os.path.join(d, "mr.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(page)
    meta = note.meta
    mp = os.path.join(d, "mr.md")
    if meta.get("platform") == "github":
        update = ("gh pr edit %s --body-file %s" % (meta["number"], mp)) if meta.get("number") else \
                 "gh pr create --base %s --body-file %s" % (meta.get("target_branch") or "<目标分支>", mp)
    else:
        update = ('glab mr update %s --description "$(cat %s)"' % (meta["number"], mp)) if meta.get("number") else \
                 'glab mr create --target-branch %s --description "$(cat %s)"' % (meta.get("target_branch") or "<目标分支>", mp)
    def url(p):
        return "file://" + p.replace("%", "%25").replace(" ", "%20").replace("#", "%23")
    print("mr=%s" % mp)
    print("md_link=%s" % url(mp))
    print("html=%s" % hp)
    print("link=%s" % url(hp))
    print("page=%s" % note.conf.get("page", ""))
    print("page_url=%s" % note.store.get("page_url", ""))
    print("update=%s" % update)
    for n in dict.fromkeys(note.notes):
        print("NOTE: %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
