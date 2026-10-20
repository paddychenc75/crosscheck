#!/bin/sh
# report-lines.sh — 生成报告里由数据决定的那几行,编排者原样放进报告,不用自己拼。
#
# 用法: report-lines.sh DIR
#
# 输出若干行,每行以一个标签开头(标签本身不写进报告):
#   SCOPE:     报告第一行:审查范围、深度、全量还是增量
#   INTENT:    意图取自哪些来源
#   RULES:     规则的使用情况(适用几条、检查了几条、违反几条)
#   VERIFY:    规则读取的验证状态(任务含规则 / 核对码 / 抽查)
#   RULES_NOTE: 规则相关的提示(可能过时、冲突、被覆盖、未同步、缺编号等),可能有多行
#   RISK:      风险等级和建议深度
#   WEAKEN:    测试或检查被削弱的每一处,可能有多行(没有就不输出)
#   SKIPPED:   跳过的文件按原因计数
#   CHECKED:   「已检查:」那一行
#   TRIMMED:   因条数上限没有展示的条数
#   FEEDBACK:  报告末尾提示怎么快速表态

set -eu

command -v python3 >/dev/null 2>&1 || { echo "report-lines: 需要 python3" >&2; exit 2; }
[ -f "${1:-}/meta.txt" ] || { echo "report-lines: 需要 collect-diff.sh 的输出目录" >&2; exit 2; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$1" && pwd) python3 - <<'PY'
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

d = os.environ["CR_DIR"]
m = crlib.read_kv(os.path.join(d, "meta.txt"))
rm = crlib.read_kv(os.path.join(d, "rules-meta.txt"))
final = crlib.load_json(os.path.join(d, "final.json"), {})
cov = crlib.load_json(os.path.join(d, "rules-coverage.json"), {})
risk = crlib.read_kv(os.path.join(d, "risk.txt"))
DEPTH = {"quick": "快速", "standard": "标准", "deep": "深度"}

# 审查范围
mode, plat = m.get("mode"), m.get("platform")
if mode == "pr":
    what = ("MR !%s" if plat == "gitlab" else "PR #%s") % m.get("number", "")
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
    scope = "增量(上次审过的 %s 个文件没变)" % m.get("unchanged_files", "0")
ref_note = ""
if m.get("head_checked_out") == "no":
    ref_note = " · 代码读自 %s" % (m.get("code_ref", "")[:12])
print("SCOPE: 审查范围:%s · 深度:%s · %s%s" % (what, DEPTH.get(m.get("depth"), m.get("depth")), scope, ref_note))

# 意图
print("INTENT: 意图来源:%s%s" % (m.get("intent_sources", "无"),
      ";验收标准 %s 条" % m["intent_acceptance"] if m.get("intent_acceptance", "0") not in ("", "0") else ""))

# 规则
units = int(rm.get("units", "0") or 0)
if units == 0:
    print("RULES: 规则:没有找到适用的规则")
else:
    org = int(rm.get("units_L1", "0") or 0)
    prj = int(rm.get("units_L2", "0") or 0) + int(rm.get("units_L3", "0") or 0)
    ext = int(rm.get("units_external", "0") or 0)
    parts = [x for x in ("组织 %d" % org if org else "", "项目 %d" % prj if prj else "", "外部 %d" % ext if ext else "") if x]
    checked = cov.get("units", 0) - len({x["unit"] for x in cov.get("missing", [])}) if cov else 0
    violated = len(final.get("groups", {}).get("rules", [])) + final.get("trimmed", {}).get("rules", 0)
    print("RULES: 规则:适用 %d 条(%s),已检查 %d 条,违反 %d 条" % (units, " · ".join(parts), checked, violated))
    guard = os.path.exists(os.path.join(d, ".guard-active"))
    hook_ok = os.path.exists(os.path.join(d, ".hook-fp-ok"))
    v1 = "任务含规则 ✓" if hook_ok else ("任务含规则 未验证(未安装 hook)" if not guard else "任务含规则 未验证(这个宿主的 hook 看不到子代理的任务)")
    v2 = ("核对码一致 ✓" if cov.get("nonce_ok") else "核对码不一致 ✗") if cov else "核对码 未核对"
    sp = final.get("spot", {})
    if sp.get("checked"):
        v3 = "抽查 %d 条无异常 ✓" % sp["checked"] if not sp.get("failed") else "抽查 %d 条发现 %d 处漏检,已补进发现" % (sp["checked"], sp["failed"])
    else:
        v3 = "抽查 未做"
    print("VERIFY: 验证:%s · %s · %s" % (v1, v2, v3))
    if cov.get("missing"):
        print("RULES_NOTE: 未检查:" + "、".join(x["unit"] for x in cov["missing"]))
for n in final.get("notes", []):
    print("RULES_NOTE: %s%s" % (n.get("summary", ""), "(%s)" % n["file"] if n.get("file") else ""))
if rm.get("disabled"):
    print("RULES_NOTE: 项目关掉了这些组织规则:" + "、".join(x.split(":", 1)[0] for x in rm["disabled"].split(";") if x))
if rm.get("disable_without_reason"):
    print("RULES_NOTE: REVIEW.md 里关掉 %s 没有写原因,这条 disable 没有生效" % rm["disable_without_reason"])
if rm.get("deprecated"):
    print("RULES_NOTE: 即将删除的组织规则(deprecated,已不再检查):" + rm["deprecated"])
if rm.get("packs_missing"):
    print("RULES_NOTE: REVIEW.md 引用了 %s,但本地没有这个组织规则包(先运行 sync-rules.sh,或检查配置里的 rules_repo)" % rm["packs_missing"])
if rm.get("org_rules_stale") == "yes":
    print("RULES_NOTE: 组织规则的本地副本超过一天没有同步,可以运行 sync-rules.sh 更新")
if rm.get("missing_ids", "0") not in ("", "0"):
    print("RULES_NOTE: 有 %s 条项目规则没有编号,只能按文件核对;规则体检时可以补上" % rm["missing_ids"])
if rm.get("source", "").startswith("worktree(") :
    print("RULES_NOTE: base 提交不在本地,规则读自当前工作区")

# 风险
if risk.get("level"):
    names = {"low": "低", "medium": "中", "high": "高"}
    reasons = [l.rstrip("\n").split("=", 1)[1] for l in open(os.path.join(d, "risk.txt"), encoding="utf-8") if l.startswith("reason=")]
    line = "RISK: 风险:%s" % names.get(risk["level"], risk["level"])
    if reasons:
        line += "(%s)" % ";".join(reasons)
    if risk["level"] == "high" and m.get("depth") != "deep":
        line += " · 建议用 deep 深度重新审"
    print(line)

# 测试或检查被削弱
try:
    for l in open(os.path.join(d, "weakening.txt"), encoding="utf-8"):
        k, p, why = (l.rstrip("\n").split("\t") + ["", ""])[:3]
        print("WEAKEN: - `%s` — %s" % (p, why))
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
dims = ["bug", "安全", "需求核对", "项目规则", "简化"] if only == "all" else [{"bug": "bug", "security": "安全", "rules": "项目规则", "simplify": "简化"}.get(x, x) for x in only.split(",")]
if units == 0 and "项目规则" in dims:
    dims.remove("项目规则")
extra = []
if os.path.exists(os.path.join(d, "settings.json")) and crlib.load_json(os.path.join(d, "settings.json"), {}).get("found"):
    extra.append("已应用 REVIEW.md 的审查设置(读自 %s)" % crlib.load_json(os.path.join(d, "settings.json"), {}).get("source", ""))
if m.get("finder_model"):
    extra.append("查找模型:%s" % m["finder_model"])
if m.get("verifier_model", "host") not in ("", "host"):
    extra.append("核实模型:%s" % m["verifier_model"])
print("CHECKED: 已检查:%s · 深度:%s · 范围:%s%s" % ("、".join(dims), m.get("depth"), "增量" if m.get("review_scope") == "incremental" else "全量",
      " · " + " · ".join(extra) if extra else ""))

tr = final.get("trimmed", {})
if tr:
    NAMES2 = {"defects": "缺陷", "intent": "需求核对", "rules": "规范", "simplify": "可简化"}
    print("TRIMMED: 因条数上限没有展示:" + "、".join("%s %d 条" % (NAMES2.get(k, k), v) for k, v in tr.items()) + "(仍记在记录里,下次会继续跟踪)")

print("FEEDBACK: 看完可以直接回复,例如「1 对,2 误报:上游已经重试」「3 先不改:下个版本再处理」,判断会被记下来,之后同样的问题不再重复报。")
PY
