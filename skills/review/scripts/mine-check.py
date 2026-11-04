#!/usr/bin/env python3
"""核对 improve 的观察和全局归并结果,不替模型判断证据语义。

python3 mine-check.py WORK_DIR --observations
    核对所有 batch/result 的线程覆盖与引文,写 observations.json。
python3 mine-check.py WORK_DIR synthesis.json
    重新核对观察,校验全局归并的来源、范围和结构,生成冻结配对的 plan.md。

所有评论与判断记录均取自本次 snapshot.json,不读取共享缓存。
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True
import crlib
import project_rules
import rules_plan

FB_VERDICTS = {"rule": ("valid",), "suppress": ("invalid",)}
STATES = {"accepted": "模型判断已采纳", "rejected": "模型判断未采纳", "unknown": "未知"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, label):
    require(isinstance(value, list) and all(isinstance(x, str) and x for x in value), label + " 应为字符串数组")
    require(len(value) == len(set(value)), label + " 有重复项")
    return set(value)


class Input:
    def __init__(self, work):
        self.work = work
        self.meta = crlib.read_kv(os.path.join(work, "meta.txt"))
        snapshot = load(os.path.join(work, "snapshot.json"))
        require(snapshot.get("version") == 1 and snapshot.get("complete") is True, "采集不完整或快照版本不支持,请重新采集")
        self.notes, self.threads = {}, set()
        for rec in snapshot["records"]:
            for note in rec["notes"]:
                thread = "%s:%s" % (rec["iid"], note["discussion"])
                key = (str(rec["iid"]), str(note["id"]))
                require(key not in self.notes, "快照里有重复评论编号")
                self.notes[key] = dict(note, mr=rec["iid"], mr_author=rec.get("author"), thread=thread, url=rec.get("web_url", ""))
                self.threads.add(thread)
        self.feedback, self.feedback_ids = {}, set()
        for row in snapshot.get("feedback", []):
            key = str(row.get("key") or row.get("id") or "")
            require(key and key not in self.feedback_ids, "判断记录缺少或重复编号")
            row = dict(row, key=key)
            self.feedback_ids.add(key)
            for alias in (row.get("key"), row.get("id")):
                if alias:
                    require(str(alias) not in self.feedback or self.feedback[str(alias)] == row, "判断记录别名冲突")
                    self.feedback[str(alias)] = row

    def evidence(self, e):
        require(isinstance(e, dict), "证据应为对象")
        require(nonempty(e.get("quote")), "证据引文须为非空字符串")
        quote = crlib.norm(e["quote"])
        if e.get("feedback"):
            row = self.feedback.get(str(e["feedback"]))
            require(row and quote and quote in crlib.norm(row.get("reason", "")), "对不上的证据:判断记录 %s" % e["feedback"])
            return dict(feedback=row["key"], target=row.get("target", ""), quote=crlib.norm(row["reason"]),
                        author=row.get("author", ""), verdict=row.get("verdict"))
        row = self.notes.get((str(e.get("mr")), str(e.get("note"))))
        require(row and quote and quote in crlib.norm(row["body"]), "对不上的证据:!%s note %s" % (e.get("mr"), e.get("note")))
        return dict(mr=row["mr"], note=row["id"], thread=row["thread"], author=row.get("author", ""),
                    mr_author=row.get("mr_author", ""), quote=quote, file=row.get("file", ""), line=row.get("line"),
                    url=row["url"], resolved=row.get("resolved"), changed_after=row.get("changed_after"))

    def observations(self):
        found, observed, excluded = set(), [], []
        count = int(self.meta.get("batches", "0"))
        expected_files = {"result-%d.json" % i for i in range(1, count + 1)}
        actual = {p for p in os.listdir(self.work) if re.fullmatch(r"result-\d+\.json", p)}
        require(actual == expected_files, "观察结果批次不齐或多余:" + ",".join(sorted(actual ^ expected_files)))
        for i in range(1, count + 1):
            batch = load(os.path.join(self.work, "batch-%d.json" % i))
            expected = set()
            batch_keys = set()
            for note in batch:
                raw = self.notes.get((str(note.get("mr")), str(note.get("note"))))
                require(raw and raw["thread"] == note.get("thread") and raw["body"] == note.get("body"), "批次与冻结快照不一致")
                expected.add(raw["thread"])
                key = (str(raw["mr"]), str(raw["id"]))
                require(key not in batch_keys, "批次包含重复评论")
                batch_keys.add(key)
            require(batch_keys == {k for k, n in self.notes.items() if n["thread"] in expected}, "批次缺少线程中的评论")
            require(not found.intersection(expected), "同一线程出现在多个批次")
            found.update(expected)
            result = load(os.path.join(self.work, "result-%d.json" % i))
            require(isinstance(result, dict), "观察回复应为对象,空数组不代表完成")
            done = strings(result.get("processed"), "processed")
            pending = strings(result.get("unprocessed"), "unprocessed")
            require(not done.intersection(pending) and done | pending == expected, "线程覆盖声明与批次不一致")
            require(not pending, "仍有未完成线程:" + ",".join(sorted(pending)))
            covered = set()
            require(isinstance(result.get("observations"), list) and isinstance(result.get("excluded"), list), "缺少 observations/excluded 数组")
            for j, item in enumerate(result["observations"], 1):
                require(isinstance(item, dict) and nonempty(item.get("pattern")), "观察缺少共同约束草稿")
                require(isinstance(item.get("evidence"), list) and item["evidence"], "观察缺少证据")
                evidence = [self.evidence(e) for e in item["evidence"]]
                threads = {e.get("thread") for e in evidence}
                require(threads <= done, "观察引用了其它批次的线程")
                covered.update(threads)
                observed.append(dict(id="batch-%d:%d" % (i, j), pattern=crlib.norm(item["pattern"]), evidence=evidence))
            for item in result["excluded"]:
                require(isinstance(item, dict) and nonempty(item.get("reason")), "排除主题缺少原因")
                ids = strings(item.get("threads"), "excluded.threads")
                require(ids and ids <= done, "排除项引用未知线程")
                covered.update(ids)
                excluded.append(dict(threads=sorted(ids), reason=crlib.norm(item["reason"])))
            require(covered == done, "已处理线程缺少观察或排除理由")
        require(found == self.threads, "采集快照中的线程没有全部进入批次")
        return observed, excluded


def coverage_review(work, root, c):
    require(nonempty(c.get("review")), "缺少语义复核说明,自检布尔值不能代替依据")
    review = c.get("coverage")
    require(isinstance(review, dict) and review.get("status") in ("uncovered", "partial", "covered", "unknown"), "缺少已有约束复核状态")
    require(nonempty(review.get("reason")), "已有约束复核缺少理由")
    require(isinstance(review.get("sources"), list), "已有约束复核缺少来源数组")
    sources = []
    frozen = load(os.path.join(work, "rules-sources.json"))
    require(isinstance(frozen, list), "规则来源快照应为数组")
    head = crlib.read_kv(os.path.join(work, "meta.txt")).get("head")
    repository = crlib.Source(root, head)
    issues = [r["file"] for r in frozen if r.get("issues")]
    for source in review["sources"]:
        require(isinstance(source, dict), "已有约束来源应为对象")
        path, quote = source.get("file"), source.get("quote")
        require(isinstance(path, str) and path and isinstance(quote, str) and quote.strip(), "已有约束来源缺少文件或原文")
        records = [r for r in frozen if r.get("file") == path]
        if records:
            text = next((r["text"] for r in records if not r.get("issues") and quote in r.get("text", "")), "")
        else:
            require(head and re.fullmatch(r"[0-9a-f]{40,64}", head), "额外复核来源缺少固定仓库版本")
            require(not os.path.isabs(path) and ".." not in path.split("/") and not project_rules._credential_path(path), "复核来源须为仓库内非凭证文件")
            require(path in repository.files(), "复核来源不是固定版本的仓库文件")
            text = repository.read(path) or ""
        require(quote in text, "已有约束引文在来源里找不到:" + path)
        sources.append(dict(file=path, quote=quote, content_sha256=rules_plan.fingerprint(text), ref="snapshot" if records else head))
    require(sources or review["status"] == "unknown", "没有核对来源时只能标记 unknown,不能宣称未覆盖")
    status, reason = review["status"], crlib.norm(review["reason"])
    if issues and status != "covered":
        status = "unknown"
        reason += ";规则来源未读完整:" + ",".join(issues)
    return dict(status=status, reason=reason, sources=sources)


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    work, result_path = argv
    try:
        inputs = Input(work)
        observations, excluded = inputs.observations()
        if result_path == "--observations":
            crlib.save_json(os.path.join(work, "observations.json"), dict(observations=observations, excluded=excluded))
            print("observations=%d excluded=%d;线程覆盖声明完整,不代表语义完整" % (len(observations), len(excluded)))
            return 0
        result = load(result_path)
        require(isinstance(result, dict) and isinstance(result.get("candidates"), list) and isinstance(result.get("excluded"), list), "全局归并回复须包含 candidates/excluded 数组")
        obs_ids = {o["id"] for o in observations}
        obs_threads = {o["id"]: {e["thread"] for e in o["evidence"]} for o in observations}
        used = set()
        feedback_used = set()
        dropped = [dict(rule="线程 " + ",".join(x["threads"]), reason=x["reason"]) for x in excluded]
        for item in result["candidates"] + result["excluded"]:
            require(isinstance(item, dict), "归并条目应为对象")
            refs = strings(item.get("observations"), "observations")
            require(refs <= obs_ids, "归并引用未知观察")
            used.update(refs)
        require(used == obs_ids, "全局归并遗漏观察:" + ",".join(sorted(obs_ids - used)))
        for item in result["excluded"]:
            require(nonempty(item.get("reason")), "全局排除项缺少原因")
            ids = strings(item.get("feedback", []), "excluded.feedback")
            require(ids or item["observations"], "排除项没有关联观察或判断记录")
            require(ids <= inputs.feedback_ids, "排除项引用未知判断记录")
            feedback_used.update(ids)
            dropped.append(dict(rule=crlib.norm(item.get("pattern", "")) or ",".join(item["observations"]), reason=crlib.norm(item["reason"])))
        kept = []
        root = inputs.meta.get("repo_root", "")
        for c in result["candidates"]:
            rule, kind = crlib.norm(c.get("rule", "")).rstrip(crlib.RULE_PUNCT), c.get("kind", "rule")
            # 即使候选不合格也记录其已核对的判断记录,避免把排除误报当漏处理。
            for e in c.get("evidence", []) if isinstance(c.get("evidence"), list) else []:
                if isinstance(e, dict) and str(e.get("feedback", "")) in inputs.feedback:
                    feedback_used.add(inputs.feedback[str(e["feedback"])]["key"])
            try:
                require(kind in FB_VERDICTS, "kind 应为 rule 或 suppress")
                problems = crlib.rule_problems(kind, rule, c.get("severity", ""), c.get("exceptions", ""))
                require(not problems, "写法不合格:" + ";".join(problems))
                paths = project_rules.patterns(c.get("paths") or [])
                require(paths, "范围不对:没有适用路径")
                # 不要报类要标明来源:已有规则的例外写进那条规则,通用直觉写进 REVIEW.md,由 suggest_dest 给默认去向
                exception_of = None
                if kind == "suppress":
                    require("exception_of" in c, "suppress 须写 exception_of:已有规则的例外写 {file, title},通用直觉写 null")
                    of = c["exception_of"]
                    if of is not None:
                        require(isinstance(of, dict) and nonempty(of.get("title")), "exception_of 应为 {file, title} 或 null")
                        file, section = rules_plan.parse_dest(str(of.get("file", "")))
                        require(file.startswith(rules_plan.RULES_DIR + "/") and not section, "exception_of.file 应为 .agents/rules/<主题>.md")
                        exception_of = dict(file=file, title=crlib.norm(of["title"]))
                coverage = coverage_review(work, root, c)
                require(coverage["status"] != "covered", "已有约束覆盖:" + coverage["reason"])
                ev, refs, people, states, bad = [], set(), set(), {}, []
                for item in c.get("evidence", []):
                    try:
                        e = inputs.evidence(item)
                        if e.get("feedback"):
                            require(e["verdict"] in FB_VERDICTS[kind], "判断记录 %s(结论与类别不符)" % item["feedback"])
                            e["state"] = "判为成立" if kind == "rule" else "判为不成立"
                            ref, state = "fb:" + e["feedback"], "accepted"
                            if e["author"]:
                                people.add(e["author"])
                        else:
                            require(any(e["thread"] in obs_threads[o] for o in c["observations"]), "候选证据不属于引用的观察线程")
                            assessment = item.get("assessment", {})
                            state = assessment.get("state")
                            require(state in STATES and nonempty(assessment.get("reason")), "评论缺少采纳判断与依据")
                            require(isinstance(assessment.get("basis"), list), "采纳依据 basis 应为数组")
                            basis = [inputs.evidence(b) for b in assessment["basis"]]
                            require(all(b.get("thread") == e["thread"] for b in basis), "采纳依据不在同一讨论")
                            require(basis or state == "unknown", "采纳/拒绝须附同讨论的原文依据")
                            e.update(state=STATES[state], assessment=dict(state=state, reason=crlib.norm(assessment["reason"]), basis=basis))
                            ref = "mr:%s" % e["mr"]
                            if e["author"] and e["author"] != e["mr_author"]:
                                people.add(e["author"])
                        ev.append(e)
                        refs.add(ref)
                        states.setdefault(ref, set()).add(state)
                    except (ValueError, TypeError, AttributeError) as error:
                        bad.append(str(error))
                require(len(refs) >= 2, "证据不足:核对后只剩 %d 个 MR/判断记录(至少 2 个);%s" % (len(refs), ";".join(bad)))
                require(not bad, "证据未全部核对通过:" + ";".join(bad))
                # 同一 MR 的矛盾结论不自动算支持,由模型重新核对,脚本只保守汇总。
                accepted = {r for r, s in states.items() if s == {"accepted"}}
                unknown = {r for r, s in states.items() if "unknown" in s or len(s) > 1}
                require(len(accepted | unknown) >= 2, "接受案例不足:明确未采纳的 MR 不凑支持案例")
                unmet = []
                if len(people) < 2:
                    unmet.append("待确认:评审人仅 %d 位" % len(people))
                if len(accepted) < 2:
                    unmet.append("待确认:模型判断已采纳的只有 %d 个" % len(accepted))
                if coverage["status"] == "unknown":
                    unmet.append("待确认:已有约束未核对完整")
                if kind == "rule" and not (c.get("bad_example") and c.get("good_example")):
                    unmet.append("待确认:缺反例或正例")
                why = crlib.norm(c.get("why", ""))
                if crlib.MR_REF.search(why):
                    unmet.append("待确认:原因里写了 MR 号,写入前要删掉(来源由脚本写在注释里)")
                severity_reason = c.get("severity_reason", "")
                require(severity_reason == "" or nonempty(severity_reason), "severity_reason 应为非空字符串")
                # 示例的代码块语言:证据所在文件的扩展名一致时取它,否则留空,不猜
                exts = {os.path.splitext(e["file"])[1][1:].lower() for e in ev if e.get("file")}
                kept.append(dict(kind=kind, title=crlib.norm(c.get("title", "")) or rule[:24], rule=rule,
                                 why=why, severity=crlib.RULE_SEVERITY.get(c.get("severity"), ""),
                                 severity_reason=crlib.norm(severity_reason),
                                 bad_example=str(c.get("bad_example") or ""), good_example=str(c.get("good_example") or ""),
                                 lang=next(iter(exts)) if len(exts) == 1 else "",
                                 exceptions=crlib.norm(c.get("exceptions", "")), paths=paths, exception_of=exception_of,
                                 topic=re.sub(r"[^a-z0-9-]+", "-", str(c.get("topic") or "general").lower()).strip("-") or "general",
                                 evidence=ev, mrs=sorted({e["mr"] for e in ev if "mr" in e}),
                                 mentions=len(refs), support=len(accepted), unknown=len(unknown), people=len(people),
                                 unmet=unmet, unmatched=bad, coverage=coverage,
                                 review=crlib.norm(c["review"]), observations=sorted(c["observations"])))
            except (ValueError, TypeError, AttributeError) as error:
                dropped.append(dict(rule=rule, kind=kind, reason=str(error)))
        require(feedback_used == inputs.feedback_ids, "全局归并遗漏判断记录:" + ",".join(sorted(inputs.feedback_ids - feedback_used)))
        merged = []
        for c in sorted(kept, key=lambda x: (-x["support"], -x["people"], x["rule"], rules_plan.fingerprint(x))):
            if c not in merged:
                merged.append(c)
        for i, c in enumerate(merged, 1):
            c["no"] = i
        plan = os.path.join(work, "plan.md")
        if os.path.exists(plan):
            previous = load(os.path.join(work, "candidates.json"))
            with open(plan, encoding="utf-8") as f:
                require(previous == merged and rules_plan.bound(f.read(), previous), "已有计划的候选配对已冻结,原文件未修改;请在新采集目录生成另一份计划")
        else:
            crlib.save_json(os.path.join(work, "candidates.json"), merged)
            crlib.save_json(os.path.join(work, "dropped.json"), dropped)
        print("candidates=%d dropped=%d" % (len(merged), len(dropped)))
        for c in merged:
            tag = "不要报 · " + c["topic"] if c["kind"] == "suppress" else "%s · %s" % (c["topic"], c["severity"])
            print("%d. [%s] %s → %s%s" % (c["no"], tag, c["rule"], rules_plan.suggest_dest(c, root),
                                         "(" + ";".join(c["unmet"]) + ")" if c["unmet"] else ""))
        for item in dropped:
            print("排除: %s — %s" % (item["rule"], item["reason"]))
        if merged:
            if os.path.exists(plan):
                print("NOTE: %s 已存在,没有覆盖;候选与计划配对保持不变。" % plan)
            else:
                with open(plan, "w", encoding="utf-8") as f:
                    f.write(rules_plan.render(merged, root, inputs.meta))
            print("PLAN: " + plan)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print("mine-check: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
