"""report-lines.sh 的实现,用法和说明见 report-lines.sh。"""
import json, os, re, sys
sys.dont_write_bytecode = True
import crlib

d = os.path.abspath(sys.argv[1])
m = crlib.read_kv(os.path.join(d, "meta.txt"))
rm = crlib.read_kv(os.path.join(d, "rules-meta.txt"))
if os.path.isfile(os.path.join(d, "verification-errors.json")):
    sys.exit("report-lines: 核实未完成，请先补齐结果并重新 aggregate")
final = crlib.load_json(os.path.join(d, "final.json"), {})
for fs in final.get("groups", {}).values():
    for f in fs:
        if f.get("no"):
            print("MARK: %s\t%s" % (f["no"], f.get("mark") or "读代码推断"))
            if f.get("raised_in_discussion"):
                print("DISCUSSED: %s\t讨论中已有人提出,代码仍未改" % f["no"])
DEPTH = {"quick": "快速", "standard": "标准", "deep": "深度"}

# 审查范围
mode, plat = m.get("mode"), m.get("platform")
if mode == "pr":
    what = ("PR #%s" if plat == "github" else "MR !%s") % m.get("number", "")
elif mode == "range" and m.get("target_key", "").startswith("branch-"):
    what = "分支 %s" % m.get("source_branch", m.get("target", ""))
elif mode == "range":
    what = "提交范围 %s" % m.get("target", "")
else:
    what = "当前分支 %s(含未提交的改动)" % (m.get("source_branch") or "")
sb, tb = m.get("source_branch", ""), m.get("target_branch", "")
if sb and tb and mode == "pr":
    what += "(%s → %s)" % (sb, tb)
elif tb and mode != "pr":
    what += ",对比 %s" % tb
scope = "全量"
if m.get("review_scope") == "incremental":
    prev = m.get("prev_review", "")
    prev_meta = crlib.read_kv(os.path.join(m.get("store_dir", ""), prev, "meta.txt"))
    scope = "增量(对比 %s-%s %s:%s 的%s审查,%s 个文件没变)" % (
        prev[4:6], prev[6:8], prev[9:11], prev[11:13], DEPTH.get(prev_meta.get("depth"), ""), m.get("unchanged_files", "0"))
ref_note = ""
if m.get("base_desc"):
    ref_note += " · 对比基准:%s" % m["base_desc"]
if m.get("head_checked_out") == "no":
    ref_note += " · 代码读自 %s" % (m.get("code_ref", "")[:12])
if m.get("merged") == "yes":
    # 已合入的 MR 改描述、改代码都影响不了这次合入;位置见 report-format
    print("MERGED: %s 已合入%s。按合入前的改动审查;描述已无法影响合入;要修请另开分支" % (
        "分支 %s" % sb if mode != "pr" else ("这个 PR" if plat == "github" else "这个 MR"), " " + tb.replace("origin/", "", 1) if tb else ""))
depth_from = {"arg": "本次指定", "review": "REVIEW.md 设定", "default": "默认"}.get(m.get("depth_source", ""), "")
if m.get("depth_invalid"):
    depth_from += ";REVIEW.md 的 depth「%s」不认识" % m["depth_invalid"]
print("SCOPE: 审查范围:%s · 深度:%s%s · %s%s" % (what, DEPTH.get(m.get("depth"), m.get("depth")),
      "(%s)" % depth_from if depth_from else "", scope, ref_note))

# 意图。acceptance_state 是读取状态(extracted|not_extracted|no_requirement);旧记录没有这个字段,按验收标准条数写
num = lambda k: int(m.get(k, "0") or 0)
accept_state = m.get("acceptance_state")
intent_note = ""
if accept_state == "extracted":
    intent_note = ";验收标准 %d 条(自动提取,可能不全)" % num("intent_acceptance")
elif accept_state == "not_extracted":
    intent_note = ";需求材料 %d 份,未自动提取到验收标准" % num("requirement_materials")
elif accept_state is None and num("intent_acceptance"):
    intent_note = ";验收标准 %d 条" % num("intent_acceptance")
if num("materials_clipped"):
    intent_note += ";%d 份材料较长,intent.md 只放摘录,全文在 materials/" % num("materials_clipped")
if num("materials_partial"):
    intent_note += ";%d 份只读到部分内容" % num("materials_partial")
print("INTENT: 意图来源:%s%s" % (m.get("intent_sources", "无"), intent_note))

# 规则
rm_blocks = int(rm.get("blocks", "0") or 0)
quick = m.get("depth") == "quick"
if quick and rm_blocks:
    print("RULES: 规则:quick 不对照项目规则(有 %d 块适用的规则,用 standard 或 deep 才会检查)" % rm_blocks)
elif rm_blocks == 0:
    print("RULES: 规则:没有找到适用的规则文件")
else:
    blocks = crlib.load_json(os.path.join(d, "rules-index.json"), [])
    violated = len(final.get("groups", {}).get("rules", [])) + final.get("trimmed", {}).get("rules", 0)
    print("RULES: 规则:%s(读自 %s),违反 %d 条" % ("、".join("`%s`" % b["file"] for b in blocks),
          "工作区" if rm.get("source") == "worktree" else rm.get("source", ""), violated))
if rm.get("excluded"):
    print("RULES_NOTE: 标了 excludeAgent: code-review,没有用于审查:" + rm["excluded"])
if rm.get("errors"):
    print("RULES_NOTE: 规则文件格式有误,这部分没有生效:" + rm["errors"])
for n in final.get("notes", []):
    print("RULES_NOTE: %s%s" % (n.get("summary", ""), "(%s)" % n["file"] if n.get("file") else ""))

# 测试与检查变更信号,不代表已确认缺陷。位置和 diff 片段由 weakening.py 算好:首行是位置和说明,后面紧跟 diff 代码块
try:
    for l in open(os.path.join(d, "weakening.txt"), encoding="utf-8"):
        k, p, why, where, snippet = (l.rstrip("\n").split("\t") + ["", "", ""])[:5]
        print("WEAKEN: - `%s` — %s" % (where or p, why))
        if snippet:
            snippet = json.loads(snippet)
            fence = "`" * max(3, 1 + max(map(len, re.findall("`+", snippet)), default=0))   # 片段里有 ``` 时加长围栏
            print("%sdiff\n%s\n%s" % (fence, snippet, fence))
except OSError:
    pass

# 跳过的文件
NAMES = {"test": "测试", "log": "日志", "lockfile": "lock", "generated": "生成", "vendored-or-built": "vendored",
         "artifact": "产物", "binary": "二进制", "review-skip": "REVIEW.md 指定跳过"}
cnt = {}
try:
    for l in open(os.path.join(d, "skipped.txt"), encoding="utf-8"):
        r = l.rstrip("\n").split("\t")[-1]
        cnt[r] = cnt.get(r, 0) + 1
except OSError:
    pass
if cnt:
    s = "SKIPPED: 跳过文件:%d(%s)" % (sum(cnt.values()), "、".join("%s %d" % (NAMES.get(k, k), v) for k, v in cnt.items()))
    if cnt.get("test") and m.get("with_tests") != "yes":
        s += " · 加 --with-tests 可以连测试一起审"
    print(s)

# 已检查
only = m.get("only", "all")
dims = ["bug", "安全", "需求核对", "项目规则"] if only == "all" else [{"bug": "bug", "security": "安全", "rules": "项目规则"}.get(x, x) for x in only.split(",")]
if (rm_blocks == 0 or quick) and "项目规则" in dims:
    dims.remove("项目规则")
extra = []
if "需求核对" in dims:
    # 没有验收标准时第 3 遍仍按能拿到的材料核对,只是没有逐条验收;说清按什么核对的
    check = None
    if accept_state == "not_extracted":
        check = "需求核对(未自动提取到验收标准,按需求材料原文核对明确的要求)"
    elif accept_state == "no_requirement":
        check = ("需求核对(没有需求材料,按改动说明核对其中点名的行为和对象)" if num("intent_materials")
                 else "需求核对(没有意图和需求材料,未判断需求符合性)")
    elif accept_state is None and num("intent_acceptance") == 0:
        check = "需求核对(没有验收标准,只核对意图里点名的对象)"
    if check:
        dims[dims.index("需求核对")] = check
st = crlib.load_json(os.path.join(d, "settings.json"), {})
if any(st.get(k) for k in ("skip", "limits")) or m.get("depth_source") == "review":
    extra.append("已应用 REVIEW.md 的审查设置(读自 %s)" % st.get("source", ""))
if m.get("code_host_reason"):
    extra.append("MR 信息没有读到(tools.code_host:%s),标题和描述未用上" % m["code_host_reason"])
if m.get("finder_model"):
    extra.append("查找模型配置:%s(实际执行模型由宿主说明)" % m["finder_model"])
if m.get("verifier_model", "host") not in ("", "host"):
    extra.append("核实模型配置:%s(实际执行模型由宿主说明)" % m["verifier_model"])
if final.get("ignored_by_history"):
    extra.append("按历史处理判断抑制重复提醒 %d 条(保留当前事实结论,不计为已解决)" % len(final["ignored_by_history"]))
if m.get("depth") == "deep" and m.get("may_run_checks") != "yes":
    extra.append("没有运行检查:工作区不是被审查的版本")
rs = final.get("runs") or {}
if rs.get("total") or rs.get("failed_claims"):
    part = "运行 %d 次 · 复现成功 %d" % (rs.get("total", 0), rs.get("reproduced", 0))
    if rs.get("failed_claims"):
        part += " · 声称执行但没有运行记录 %d" % rs["failed_claims"]
    extra.append(part)
print("CHECKED: 已检查:%s · 深度:%s · 范围:%s%s" % ("、".join(dims), DEPTH.get(m.get("depth"), m.get("depth")), "增量" if m.get("review_scope") == "incremental" else "全量",
      " · " + " · ".join(extra) if extra else ""))

# 调用方、修复线索、共同变更没查完(analysis-facts.json 的 limits,以及待核对调用方里同名歧义、没列全的条目)
facts = crlib.load_json(os.path.join(d, "analysis-facts.json"), {})
todo = facts.get("callers_to_check") or []
parts = list(facts.get("limits") or [])
amb, cut = sum(1 for c in todo if c.get("ambiguous")), sum(1 for c in todo if c.get("truncated"))
if amb or cut:
    parts.append("待核对调用方里有同名定义 %d 个、调用方没列全 %d 个" % (amb, cut))
if parts:
    print("FACTS_NOTE: 影响面线索没有查完(按名字和 git 历史的文本线索):%s · %s" % (
        ";".join(parts), "deep 的影响面一遍已从使用方往回查" if m.get("depth") == "deep" else "要从使用方往回再查一遍可以用 deep"))

tr = final.get("trimmed", {})
if tr:
    NAMES2 = {"defects": "缺陷", "intent": "需求核对", "rules": "规范"}
    print("TRIMMED: 因条数上限没有展示:" + "、".join("%s %d 条" % (NAMES2.get(k, k), v) for k, v in tr.items()) + "(仍记在记录里,下次会继续跟踪;说「还有哪些没展示」可以查看)")

# 流程不完整:查找角色的回复或核实结果没有落盘(completeness.py 写出)。报告照出,但要说清缺了什么
gaps = []
try:
    for l in open(os.path.join(d, "completeness.txt"), encoding="utf-8"):
        if l.startswith("INCOMPLETE: "):
            gaps.append(l.rstrip("\n").split(": ", 1)[1])
except OSError:
    pass
shown = [f for fs in final.get("groups", {}).values() for f in fs]
if gaps:
    print("INCOMPLETE: 审查未完成，已检查部分确认 %d 条发现；流程不完整:%s" % (len(shown), "；".join(gaps)))

# 改动代码里的原有问题:真实存在,但 base 版本里同样存在、不是本次引入的(不计入发现,放在报告末尾单列)
for p in final.get("pre_existing") or []:
    print("PREEXIST: %s:%s\t%s\t%s" % (p.get("file", ""), p.get("line", ""), p.get("summary", ""), p.get("verdict", "")))

# 需求核对里交付范围待明确的条目:要求和限定两处原文都核对过,不计入发现;同一要求上核实结论不一致的单独提示
flat = lambda v: " ".join(str(v or "").split())
for n in final.get("scope_notes") or []:
    basis = n.get("scope_basis") or {}
    print("UNCLEAR: %s:%s\t%s\t%s\t%s\t%s" % (n.get("file", ""), n.get("line", ""), flat(n.get("summary")),
          flat((basis.get("requirement") or {}).get("quote")), flat((basis.get("limit") or {}).get("quote")), flat(basis.get("missing"))))
for c in final.get("scope_conflicts") or []:
    print("SCOPE_CONFLICT: %s 上不同核实结论的交付范围不一致:%s" % (c.get("requirement", ""),
          ";".join("%s=%s" % (k, ",".join(v)) for k, v in (c.get("scopes") or {}).items() if v)))

# 默认只为本次展示且能对应记录的发现提示反馈;被隐藏或否决的条目按用户询问另行处理。
findings = crlib.load_json(os.path.join(d, "findings.json"), [])
feedback_ids = {f.get("id") for f in findings if f.get("id")}
feedback = [f for f in shown if f.get("no") and f.get("id") in feedback_ids]
if feedback:
    if crlib.storage_mode(m) == "temporary":
        print("FEEDBACK: 看完可以直接回复,例如「%s 误报:说明原因」或「%s 先不改:说明原因」;判断仅保留在本次临时目录,后续反馈须使用这份报告的运行目录。" % (feedback[0]["no"], feedback[0]["no"]))
    else:
        print("FEEDBACK: 看完可以直接回复,例如「%s 误报:说明原因」或「%s 先不改:说明原因」,判断会被记录并供后续审查参考;代码或判断前提变化时会重新核实。" % (feedback[0]["no"], feedback[0]["no"]))
