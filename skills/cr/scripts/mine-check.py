#!/usr/bin/env python3
"""mine-check.py — 校验 miner 归纳出的规则候选,去重后编号列给用户挑。

用法: python3 mine-check.py WORK_DIR RESULT.json [RESULT.json ...]

  WORK_DIR     mine-mrs.sh 输出的工作目录
  RESULT.json  每个 miner 的回复(JSON 数组),原样保存的文件

准入检查(不信任 miner 自己写的次数,能查的都由脚本查):
  - 证据:MR 和评论必须在缓存里,引用的片段必须是那条评论原文的一部分(空白归一后比较);
    来自 crosscheck 判断记录的证据,片段必须出现在当时写下的原因里。对不上的证据丢掉。
  - 至少 2 个不同的 MR(或判断记录),否则丢掉;评审人不足 2 位只标出来(MR 作者自己的评论不算评审人)。
  - 改过:证据所在的讨论里,有一处在评论后改了代码或讨论已解决;全都确定没改、没解决的丢掉,不知道的标「?」。
  - 针对改动的代码:至少一条证据是行内评论(有文件位置)。只标出来。
  - 规则写成「必须 / 禁止」一类的单句;严重级别是「重要」或「轻微」;「不要报」(例外边界)必填;范围是仓库根目录相对的 glob。
    不满足的丢掉。正例、反例能给就给,只标出来。
  - miner 的自检(项目特有、工具查不到、不是主观风格、没被后来的 MR 推翻、不和已有规则冲突、能泛化)有一项为 false 的丢掉。
  - 和仓库里已有的规则(REVIEW.md、CLAUDE.md、AGENTS.md、.agents/rules/ 等)说的是同一件事的丢掉;候选之间重复的合并证据。
按涉及的 MR 数、评审人数排序,写入 WORK_DIR/candidates.json 和 dropped.json(丢掉的及原因),并打印编号列表和每条的检查结果。
"""
import difflib
import json
import os
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules

SELF_CHECKS = {
    "project_specific": "项目特有", "not_tool_catchable": "工具查不到", "not_subjective": "非主观风格",
    "not_overturned": "没被推翻", "no_conflict": "不冲突", "generalizable": "能泛化",
}
MUST = re.compile(r"必须|禁止|不得|不能|不要|不允许|只能|应当|应该|一律|统一|must|never|always|do not|don't", re.I)
SEVERITY = {"重要": "重要", "major": "重要", "轻微": "轻微", "minor": "轻微"}


def squash(s):
    """比较用:去掉空白和标点,英文小写。"""
    return re.sub(r"[\s`*_~>#\-—,，。.;；:：!！?？、()()\[\]【】「」\"'“”‘’]+", "", str(s)).lower()


def similar(a, b):
    a, b = squash(a), squash(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def topic_name(s):
    s = re.sub(r"[^a-z0-9-]+", "-", str(s or "").lower()).strip("-")
    return s[:40] or "general"


def mark(v):
    return {True: "✓", False: "✗", None: "?"}[v]


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    work, results = argv[0], argv[1:]
    meta = crlib.read_kv(os.path.join(work, "meta.txt"))
    if not meta.get("cache"):
        print("mine-check: %s 不是 mine-mrs.sh 的工作目录" % work, file=sys.stderr)
        return 1
    cache = meta["cache"]
    mr_cache = {}

    def mr(iid):
        k = str(iid).lstrip("!#")
        if k not in mr_cache:
            mr_cache[k] = crlib.load_json(os.path.join(cache, "%s.json" % k), {}) if k.isdigit() else {}
        return mr_cache[k]

    feedback = crlib.load_json(os.path.join(work, "feedback.json"), [])
    fb_by = {}
    for e in feedback:
        for k in (e.get("key"), e.get("id")):
            if k:
                fb_by[str(k)] = e

    raw, bad_files = [], []
    for p in results:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if not isinstance(data, list):
            bad_files.append(p)
            continue
        raw.extend(x for x in data if isinstance(x, dict))

    kept, dropped = [], []

    def drop(c, why):
        dropped.append(dict(rule=c.get("rule", ""), kind=c.get("kind", ""), reason=why))

    def fixed_state(rec, note):
        """这条评论所在的讨论:评论后改了代码或已解决 → True;确定没有 → False;不知道 → None。"""
        thread = [n for n in rec.get("notes", []) if n.get("discussion") == note.get("discussion")] or [note]
        if any(n.get("resolved") is True or n.get("changed_after") is True for n in thread):
            return True
        if any(n.get("resolved") is False or n.get("changed_after") is False for n in thread):
            return False
        return None

    for c in raw:
        kind = c.get("kind") or "rule"
        rule = crlib.norm(c.get("rule", ""))
        if kind not in ("rule", "suppress") or not rule or len(rule) > 200:
            drop(c, "格式不对(kind 应为 rule 或 suppress,规则一句话、不超过 200 字)")
            continue
        try:
            paths = project_rules.patterns(c.get("paths") or [])
            if not paths:
                raise ValueError("没有写适用路径")
        except ValueError as e:
            drop(c, "范围不对:%s" % e)
            continue
        severity = SEVERITY.get(str(c.get("severity", "")).strip())
        exceptions = crlib.norm(c.get("exceptions", ""))
        if kind == "rule":
            problems = []
            if not MUST.search(rule):
                problems.append("没有写成「必须 / 禁止」的单句")
            if not severity:
                problems.append("严重级别应为重要或轻微")
            if not exceptions:
                problems.append("没有写「不要报」的边界")
            if problems:
                drop(c, "写法不合格:" + ";".join(problems))
                continue
        checks = c.get("checks") if isinstance(c.get("checks"), dict) else {}
        failed = [SELF_CHECKS[k] for k in SELF_CHECKS if checks.get(k) is False]
        if failed:
            drop(c, "miner 自检不通过:" + "、".join(failed))
            continue

        ev, refs, people, bad, states, inline = [], set(), set(), [], [], False
        for e in c.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            quote = crlib.norm(e.get("quote", ""))
            if e.get("feedback"):
                f = fb_by.get(str(e["feedback"]))
                if f and quote and quote in crlib.norm(f.get("reason", "")):
                    ev.append(dict(feedback=f.get("key") or f.get("id"), quote=quote, verdict=f.get("verdict")))
                    refs.add("fb:" + str(f.get("key") or f.get("id")))
                    people.add("fb:" + str(f.get("author") or ""))
                else:
                    bad.append("判断记录 %s" % e["feedback"])
                continue
            rec = mr(e.get("mr", ""))
            note = next((n for n in rec.get("notes", []) if str(n.get("id")) == str(e.get("note"))), None)
            if not note or not quote or quote not in crlib.norm(note.get("body", "")):
                bad.append("!%s note %s" % (e.get("mr"), e.get("note")))
                continue
            ev.append(dict(mr=rec["iid"], note=note["id"], author=note.get("author", ""), quote=quote,
                           file=note.get("file", ""), line=note.get("line"), url=rec.get("web_url", "")))
            refs.add("mr:%s" % rec["iid"])
            states.append(fixed_state(rec, note))
            inline = inline or bool(note.get("file"))
            if note.get("author") and note.get("author") != rec.get("author"):
                people.add(note["author"])
        unmatched = ";对不上的证据:" + "、".join(bad) if bad else ""
        if len(refs) < 2:
            drop(c, "证据不足:核对后只剩 %d 个 MR/判断记录(至少 2 个)%s" % (len(refs), unmatched))
            continue
        fixed = True if True in states else (False if states and all(s is False for s in states) else None)
        if not states:
            fixed = True          # 只有判断记录作证据:人已经明确表过态
        if fixed is False:
            drop(c, "评论之后代码没改、讨论也没解决(可能没达成共识)%s" % unmatched)
            continue
        mrs = sorted({e["mr"] for e in ev if e.get("mr")})
        admission = [("%d 个 MR" % len(mrs) if mrs else "%d 条判断记录" % len(refs), True),
                     ("%d 位评审人" % len(people), len(people) >= 2 or None),
                     ("改过代码或已解决", fixed)]
        if kind == "rule":
            admission += [("针对改动的代码", inline or (None if not mrs else False)),
                          ("有不要报的边界", True),
                          ("有正反例", bool(c.get("bad_example")) and bool(c.get("good_example")) or None)]
        selfc = [SELF_CHECKS[k] for k in SELF_CHECKS if checks.get(k) is True]
        kept.append(dict(kind=kind, title=crlib.norm(c.get("title", "")) or rule[:24], rule=rule, why=crlib.norm(c.get("why", "")),
                         severity=severity or "", bad_example=str(c.get("bad_example") or "").strip(),
                         good_example=str(c.get("good_example") or "").strip(), exceptions=exceptions,
                         paths=paths, topic=topic_name(c.get("topic")), evidence=ev, mrs=mrs, support=len(refs),
                         people=len(people), admission=[[a, b] for a, b in admission], self_checks=selfc, unmatched=bad))

    # 已有规则:逐行比较(规则通常一行一条)
    existing = []
    root = meta.get("repo_root", "")
    for rec in project_rules.discover(crlib.Source(root)) if root else []:
        for line in rec["body"].split("\n"):
            t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*(?:\*\*[^*]{1,8}\*\*\s*[::]\s*)?", "", line).strip()
            if len(squash(t)) >= 4:
                existing.append((rec["file"], t))
    merged = []
    for c in sorted(kept, key=lambda x: (-x["support"], -x["people"], x["rule"])):
        hit = next((f for f, t in existing if similar(c["rule"], t)), None)
        if hit:
            drop(c, "已有规则(%s)" % hit)
            continue
        same = next((m for m in merged if m["kind"] == c["kind"] and similar(m["rule"], c["rule"])), None)
        if same:
            seen = {(e.get("mr"), e.get("note"), e.get("feedback")) for e in same["evidence"]}
            same["evidence"] += [e for e in c["evidence"] if (e.get("mr"), e.get("note"), e.get("feedback")) not in seen]
            same["mrs"] = sorted(set(same["mrs"]) | set(c["mrs"]))
            same["support"] = max(same["support"], c["support"], len(same["mrs"]))
            continue
        merged.append(c)
    merged.sort(key=lambda x: (-x["support"], -x["people"]))
    for i, c in enumerate(merged, 1):
        c["no"] = i
    crlib.save_json(os.path.join(work, "candidates.json"), merged)
    crlib.save_json(os.path.join(work, "dropped.json"), dropped)

    for p in bad_files:
        print("mine-check: %s 不是 JSON 数组,已跳过;请让对应的 miner 重新回复" % p, file=sys.stderr)
    print("candidates=%d dropped=%d" % (len(merged), len(dropped)))
    for c in merged:
        if c["kind"] == "suppress":
            print("%d. [不要报 · %s] %s(范围 %s)" % (c["no"], c["topic"], c["rule"], ", ".join(c["paths"])))
        else:
            print("%d. [%s · %s] %s(范围 %s)" % (c["no"], c["topic"], c["severity"], c["rule"], ", ".join(c["paths"])))
        if c["why"]:
            print("   原因:%s" % c["why"])
        if c["exceptions"]:
            print("   不要报:%s" % c["exceptions"])
        if c["bad_example"] or c["good_example"]:
            print("   反例:%s  正例:%s" % (c["bad_example"] or "-", c["good_example"] or "-"))
        print("   准入:" + " ".join("%s%s" % (mark(v), a) for a, v in c["admission"])
              + (";自检:" + "、".join(c["self_checks"]) if c["self_checks"] else ""))
        shown = []
        for e in c["evidence"][:3]:
            q = e["quote"] if len(e["quote"]) <= 40 else e["quote"][:40] + "…"
            shown.append(("!%s「%s」" % (e["mr"], q)) if e.get("mr") else ("判断记录「%s」" % q))
        print("   证据:" + ";".join(shown) + (" 等 %d 处" % len(c["evidence"]) if len(c["evidence"]) > 3 else ""))
    reasons = {}
    for d in dropped:
        k = re.split(r"[:(]", d["reason"])[0]
        reasons[k] = reasons.get(k, 0) + 1
    if reasons:
        print("丢掉:" + "、".join("%s %d 条" % kv for kv in sorted(reasons.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
