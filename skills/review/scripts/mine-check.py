#!/usr/bin/env python3
"""mine-check.py — 校验 miner 归纳出的规则候选,去重后编号列给用户挑。

用法: python3 mine-check.py WORK_DIR RESULT.json [RESULT.json ...]

  WORK_DIR     mine-mrs.sh 输出的工作目录
  RESULT.json  每个 miner 的回复(JSON 数组),原样保存的文件

校验引用真实性、结构与 miner 自检,不证明语义支持。两类候选均须完整自检。
提及与接受信号分开统计,确定未接受的 MR 不凑案例,未知保留;wontfix 不支持 suppress。
完整相同的输入去重,其余相似仅提示供计划判断。已有计划冻结候选配对。
"""
import difflib
import json
import os
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules
import rules_plan

SELF_CHECKS = {
    "project_specific": "项目特有", "not_tool_catchable": "工具查不到", "not_subjective": "非主观风格",
    "not_overturned": "没被推翻", "no_conflict": "不冲突", "generalizable": "能泛化",
}
# 判断记录只认与类别一致的结论;成立暂不修不能泛化为长期不报。
FB_VERDICTS = {"rule": ("valid",), "suppress": ("invalid",)}


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
        if any(n.get("resolved") is False for n in thread) and all(n.get("changed_after") is False for n in thread):
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
        severity = crlib.RULE_SEVERITY.get(str(c.get("severity", "")).strip())
        exceptions = crlib.norm(c.get("exceptions", ""))
        problems = crlib.rule_problems(kind, rule, c.get("severity", ""), exceptions)
        if problems:
            drop(c, "写法不合格:" + ";".join(problems))
            continue
        checks = c.get("checks") if isinstance(c.get("checks"), dict) else {}
        missing = [SELF_CHECKS[k] for k in SELF_CHECKS if k not in checks]
        if missing:
            drop(c, "miner 自检缺项:" + "、".join(missing))
            continue
        failed = [SELF_CHECKS[k] for k in SELF_CHECKS if checks[k] is not True]
        if failed:
            drop(c, "miner 自检不通过:" + "、".join(failed))
            continue

        ev, refs, people, bad, states, inline = [], set(), set(), [], {}, False
        accepted, unknown = set(), set()
        for e in c.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            quote = crlib.norm(e.get("quote", ""))
            if e.get("feedback"):
                f = fb_by.get(str(e["feedback"]))
                if f and f.get("verdict") not in FB_VERDICTS[kind]:
                    bad.append("判断记录 %s(结论与类别不符)" % e["feedback"])
                elif f and quote and quote in crlib.norm(f.get("reason", "")):
                    ev.append(dict(feedback=f.get("key") or f.get("id"), quote=quote, verdict=f.get("verdict")))
                    refs.add("fb:" + str(f.get("key") or f.get("id")))
                    accepted.add("fb:" + str(f.get("key") or f.get("id")))
                    if f.get("author"):
                        people.add("fb:" + str(f["author"]))
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
            states.setdefault("mr:%s" % rec["iid"], []).append(fixed_state(rec, note))
            inline = inline or bool(note.get("file"))
            if note.get("author") and note.get("author") != rec.get("author"):
                people.add(note["author"])
        unmatched = ";对不上的证据:" + "、".join(bad) if bad else ""
        if len(refs) < 2:
            drop(c, "证据不足:核对后只剩 %d 个 MR/判断记录(至少 2 个)%s" % (len(refs), unmatched))
            continue
        for ref, values in states.items():
            if True in values:
                accepted.add(ref)
            elif None in values:
                unknown.add(ref)
        if len(accepted | unknown) < 2:
            drop(c, "接受案例不足:评论之后代码没改、讨论也没解决的 MR 不算接受案例%s" % unmatched)
            continue
        fixed = True if len(accepted) >= 2 else None
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
                         paths=paths, topic=topic_name(c.get("topic")), evidence=ev, mrs=mrs, support=len(accepted),
                         mentions=len(refs), unknown=len(unknown),
                         people=len(people), admission=[[a, b] for a, b in admission], self_checks=selfc, unmatched=bad))

    # 已有规则没有统一的逐条边界结构,仅提示相似,不据此删除。
    existing = []
    root = meta.get("repo_root", "")
    for rec in project_rules.discover(crlib.Source(root)) if root else []:
        for line in rec["body"].split("\n"):
            if len(squash(line)) >= 4:
                existing.append((rec["file"], line))
    merged = []
    for c in sorted(kept, key=lambda x: (-x["support"], -x["people"], x["rule"], rules_plan.fingerprint(x))):
        # 仅完整候选相同才去掉重复输入;不合并不同范围、边界或来源。
        if c in merged:
            continue
        for f, t in existing:
            if similar(c["rule"], t):
                print("WARN: 可能重复或冲突,请在计划中核对 %s: %s" % (f, c["rule"]))
                break
        if any(similar(m["rule"], c["rule"]) for m in merged):
            print("WARN: 候选可能重复或冲突,请核对范围和边界: " + c["rule"])
        merged.append(c)
    merged.sort(key=lambda x: (-x["support"], -x["people"], x["rule"], rules_plan.fingerprint(x)))
    for i, c in enumerate(merged, 1):
        c["no"] = i
    plan = os.path.join(work, "plan.md")
    if os.path.exists(plan):
        previous = crlib.load_json(os.path.join(work, "candidates.json"), [])
        with open(plan, encoding="utf-8") as f:
            bound = rules_plan.bound(f.read(), previous)
        if previous != merged or not bound:
            print("ERROR: 已有计划的候选配对已冻结,原文件未修改。请重新运行 mine-mrs.sh 生成新采集目录,再运行 mine-check.py 生成另一份计划。", file=sys.stderr)
            return 2
    else:
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
            print("   标题:%s" % c["title"])
        if c["why"]:
            print("   原因:%s" % c["why"])
        if c["exceptions"]:
            print("   不要报:%s" % c["exceptions"])
        if c["bad_example"] or c["good_example"]:
            print("   反例:%s  正例:%s" % (c["bad_example"] or "-", c["good_example"] or "-"))
        print("   准入:" + " ".join("%s%s" % (mark(v), a) for a, v in c["admission"])
              + (";自检:" + "、".join(c["self_checks"]) if c["self_checks"] else ""))
        print("   案例:提及 %d,接受信号 %d,未知 %d(信号不证明语义支持)" % (c["mentions"], c["support"], c["unknown"]))
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
    if merged:
        plan = os.path.join(work, "plan.md")
        if os.path.exists(plan):
            print("NOTE: %s 已存在,没有覆盖;候选与计划配对保持不变。" % plan)
        else:
            with open(plan, "w", encoding="utf-8") as f:
                f.write(rules_plan.render(merged, root, meta))
        print("PLAN: " + plan)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
