"""report-lines.sh 的实现,用法和说明见 report-lines.sh。"""
import os, sys
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
dims = ["bug", "安全", "需求核对", "项目规则"] if only == "all" else [{"bug": "bug", "security": "安全", "rules": "项目规则"}.get(x, x) for x in only.split(",")]
if (rm_blocks == 0 or quick) and "项目规则" in dims:
    dims.remove("项目规则")
extra = []
if "需求核对" in dims and int(m.get("intent_acceptance", "0") or 0) == 0:
    dims.remove("需求核对")
    extra.append("需求核对:缺少明确验收标准,未完成")
st = crlib.load_json(os.path.join(d, "settings.json"), {})
if any(st.get(k) for k in ("skip", "limits")):
    extra.append("已应用 REVIEW.md 的审查设置(读自 %s)" % st.get("source", ""))
if m.get("finder_model"):
    extra.append("查找模型配置:%s(实际执行模型由宿主说明)" % m["finder_model"])
if m.get("verifier_model", "host") not in ("", "host"):
    extra.append("核实模型配置:%s(实际执行模型由宿主说明)" % m["verifier_model"])
rs = final.get("runs") or {}
if rs.get("total") or rs.get("failed_claims"):
    part = "运行 %d 次 · 复现成功 %d" % (rs.get("total", 0), rs.get("reproduced", 0))
    if rs.get("failed_claims"):
        part += " · 声称执行但没有运行记录 %d" % rs["failed_claims"]
    extra.append(part)
print("CHECKED: 已检查:%s · 深度:%s · 范围:%s%s" % ("、".join(dims), m.get("depth"), "增量" if m.get("review_scope") == "incremental" else "全量",
      " · " + " · ".join(extra) if extra else ""))

tr = final.get("trimmed", {})
if tr:
    NAMES2 = {"defects": "缺陷", "intent": "需求核对", "rules": "规范"}
    print("TRIMMED: 因条数上限没有展示:" + "、".join("%s %d 条" % (NAMES2.get(k, k), v) for k, v in tr.items()) + "(仍记在记录里,下次会继续跟踪)")

# hook 在 collect-diff.sh 执行完时会在 review_dir 里留下标记;没有标记说明这次审查没有受到只读拦截。
if not os.path.exists(os.path.join(d, ".guard-active")):
    print("GUARD: 只读拦截:没有检测到(没有以插件方式安装,或在 Codex 里还没有用 /hooks 信任 hook);这次审查只靠说明约束,结束时会核对仓库状态")

print("FEEDBACK: 看完可以直接回复,例如「1 对,2 误报:上游已经重试」「3 先不改:下个版本再处理」,判断会被记下来,之后同样的问题不再重复报。")
