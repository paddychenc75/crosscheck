#!/bin/sh
# collect-rules.sh — 按分层装配这次审查适用的规则。
#
# 用法: collect-rules.sh DIR              DIR 是 collect-diff.sh 的输出目录(推荐)
#       collect-rules.sh DIR/files.txt    旧用法:同样写出下面的文件,并把 rules.txt 打印到 stdout
#
# 写入 DIR:
#   rules.txt         规则正文,按「越具体越先」排列:
#                       审查设置 → L3 目录级 REVIEW.md(深的在前)→ L2 项目级 REVIEW.md → 抑制(.cr/ignore.md)
#                       → L1 组织规则包(REVIEW.md 头部 extends 引用的,去掉 disable 和 deprecated 的规则)
#                       → 外部规则(CLAUDE.md、AGENTS.md、Cursor / Copilot 规则等,只读)
#                     每块以一行头部开始:=== RULES layer=<L1|L2|L3|ignore|external> source=<来源> file=<路径> scope=<glob> ===
#   rules-index.json  需要检查的单位:有编号的规则按编号,没有编号的规则文件按文件(FILE:<路径>),
#                     以及每个单位覆盖哪些改动文件。check-rules.sh 用它核对检查清单。
#   rules-meta.txt    条数、规则指纹(fingerprint)、规则从哪里读的、组织规则版本、关掉和过时的规则等
#   task-rules.md     交给 rules-auditor 和 verifier 的规则段落:含规则指纹和末尾的核对码,编排者原样放进任务
#   .rules-nonce      核对码(角色不要读这个文件)
#
# 读哪一版:本地审查自己的分支读工作区(改了规则马上生效);PR/MR、其他分支、提交范围读 base 那一版,
# 改动本身改不了审查用的规则。组织规则包在 CROSSCHECK_RULES_DIR、配置里的 rules_dir 或
# ~/.crosscheck/org-rules(由 sync-rules.sh 同步)。需要 python3。

set -eu

command -v python3 >/dev/null 2>&1 || { echo "collect-rules: 需要 python3" >&2; exit 1; }
arg=${1:-}
case "$arg" in
  -h|--help|"") sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
if [ -d "$arg" ]; then dir=$arg; compat=no; else dir=$(dirname "$arg"); compat=yes; fi
[ -f "$dir/meta.txt" ] || { echo "collect-rules: $dir 不是 collect-diff.sh 的输出目录" >&2; exit 1; }

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_DIR=$(cd "$dir" && pwd) CR_COMPAT=$compat python3 - <<'PY'
import hashlib, json, os, re, subprocess, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

d = os.environ["CR_DIR"]
compat = os.environ["CR_COMPAT"] == "yes"
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
root = meta.get("repo_root", "")
mode = meta.get("mode", "local")
base = meta.get("base_sha", "")

ref = None
source_note = "worktree"
if mode != "local":
    if base and subprocess.run(["git", "-C", root, "cat-file", "-e", base + "^{commit}"], capture_output=True).returncode == 0:
        ref, source_note = base, "base:" + base[:12]
    else:
        source_note = "worktree(base 不在本地)"
src = crlib.Source(root, ref)

def lines_of(p):
    try:
        with open(p, encoding="utf-8") as f:
            return [x.strip() for x in f if x.strip()]
    except OSError:
        return []
changed = lines_of(os.path.join(d, "files.txt"))

settings = crlib.load_json(os.path.join(d, "settings.json"), {})
if not settings:
    t = src.read("REVIEW.md")
    fm = crlib.parse_frontmatter(t)[0] if t else {}
    settings = {"extends": crlib.as_list(fm.get("extends")), "disable": {}, "skip": crlib.as_list(fm.get("skip")),
                "limits": crlib.as_map(fm.get("limits")), "severity": crlib.as_map(fm.get("severity"))}
    for item in crlib.as_list(fm.get("disable")):
        k, _, why = item.partition(":")
        settings["disable"][k.strip()] = why.strip()
disable = settings.get("disable") or {}
disabled_ok = {k: v for k, v in disable.items() if v}
disabled_bad = [k for k, v in disable.items() if not v]

def in_scope(scope, path):
    return scope == "**" or any(crlib.glob_match(g, path) for g in scope.split(","))

dirs = set()
for f in changed:
    p = os.path.dirname(f)
    while p:
        dirs.add(p)
        p = os.path.dirname(p)
dirs_deep_first = sorted(dirs, key=lambda x: (-x.count("/"), x))

blocks, seen, dup_notes = [], set(), []

def add_block(layer, source, path, scope, body, extra=None):
    body = body.strip("\n")
    if not body.strip():
        return
    h = hashlib.sha1(body.encode("utf-8")).hexdigest()
    if h in seen:
        dup_notes.append(path)
        return
    seen.add(h)
    b = {"layer": layer, "source": source, "file": path, "scope": scope, "body": body}
    b.update(extra or {})
    blocks.append(b)

def read_body(path):
    t = src.read(path)
    if t is None:
        return None, {}, 0
    fm, body = crlib.parse_frontmatter(t)
    fm_lines = len(t.split("\n")) - len(body.split("\n"))
    return body, fm, fm_lines

def scoped(path, key, default):
    """作用域来自 frontmatter 的 glob 列表;没有 glob 时 default 为 always(处处适用)或 never(跳过)。"""
    body, fm, off = read_body(path)
    if body is None:
        return None
    globs = []
    for g in crlib.as_list(fm.get(key)):
        globs.extend(crlib.expand_braces(g))
    if not globs:
        return ("**", body, off) if default == "always" else None
    hit = [g for g in globs if any(crlib.glob_match(g, f) for f in changed)]
    return (",".join(hit), body, off) if hit else None

offsets = {}
# L3 目录级 REVIEW.md(深的在前)、L2 项目级 REVIEW.md、抑制
for dd in dirs_deep_first:
    body, fm, off = read_body(dd + "/REVIEW.md")
    if body is not None:
        offsets[dd + "/REVIEW.md"] = off
        add_block("L3", "review", dd + "/REVIEW.md", dd + "/**", body)
body, fm, off = read_body("REVIEW.md")
if body is not None:
    offsets["REVIEW.md"] = off
    add_block("L2", "review", "REVIEW.md", "**", body)
body, fm, off = read_body(".cr/ignore.md")
if body is not None:
    offsets[".cr/ignore.md"] = off
    add_block("ignore", "ignore", ".cr/ignore.md", "**", body)

# L1 组织规则包
rdir = crlib.rules_dir()
org_version, org_stale = "none", "no"
packs_loaded, packs_missing, packs_skipped, deprecated = [], [], [], []
if settings.get("extends"):
    if rdir:
        org_version = (subprocess.run(["git", "-C", rdir, "describe", "--tags", "--always"], capture_output=True, text=True).stdout.strip() or "unknown")
        try:
            if time.time() - os.path.getmtime(os.path.join(rdir, ".last-sync")) > 86400:
                org_stale = "yes"
        except OSError:
            org_stale = "unknown"
    for pack in settings["extends"]:
        p = os.path.join(rdir, "packs", pack, "rules.md") if rdir else ""
        if not p or not os.path.isfile(p):
            packs_missing.append(pack)
            continue
        with open(p, encoding="utf-8") as f:
            t = f.read()
        fm, body = crlib.parse_frontmatter(t)
        applies = []
        for g in crlib.as_list(fm.get("applies_to")):
            applies.extend(crlib.expand_braces(g))
        hit = [g for g in applies if any(crlib.glob_match(g, f) for f in changed)] if applies else ["**"]
        if not hit:
            packs_skipped.append(pack)
            continue
        dep = [r["id"] for r in crlib.split_rules(body) if r["id"] and r["meta"].get("status") == "deprecated"]
        deprecated.extend(dep)
        rel = "packs/%s/rules.md" % pack
        offsets[rel] = len(t.split("\n")) - len(body.split("\n"))
        body = crlib.remove_rules(body, set(disabled_ok) | set(dep))
        add_block("L1", "org", rel, ",".join(hit), body, {"pack": pack, "version": org_version, "owner": str(fm.get("owner") or "")})
        packs_loaded.append(pack)

# 外部规则(各个 coding agent 的约定文件,只读)
def nearest(source, name):
    for dd in dirs_deep_first:
        body, fm, off = read_body(dd + "/" + name)
        if body is not None:
            add_block("external", source, dd + "/" + name, dd + "/**", body)
    body, fm, off = read_body(name)
    if body is not None:
        add_block("external", source, name, "**", body)

def listed(prefix, exts):
    return sorted(f for f in src.files() if f.startswith(prefix) and f.endswith(exts))

nearest("claude", "CLAUDE.md")
b, fm, off = read_body(".claude/CLAUDE.md")
if b is not None:
    add_block("external", "claude", ".claude/CLAUDE.md", "**", b)
for f in listed(".claude/rules/", (".md",)):
    r = scoped(f, "paths", "always")
    if r: add_block("external", "claude", f, r[0], r[1])
nearest("agents", "AGENTS.override.md")
nearest("agents", "AGENTS.md")
for f in listed(".cursor/rules/", (".mdc", ".md")):
    body, fm, off = read_body(f)
    if body is None: continue
    if str(fm.get("alwaysApply", "")).lower() == "true":
        add_block("external", "cursor", f, "**", body)
    else:
        r = scoped(f, "globs", "never")
        if r: add_block("external", "cursor", f, r[0], r[1])
for name, source in ((".cursorrules", "cursor"), (".github/copilot-instructions.md", "copilot")):
    b, fm, off = read_body(name)
    if b is not None: add_block("external", source, name, "**", b)
for f in listed(".github/instructions/", (".instructions.md",)):
    r = scoped(f, "applyTo", "always")
    if r: add_block("external", "copilot", f, r[0], r[1])
nearest("gemini", "GEMINI.md")
for name, source in ((".windsurfrules", "windsurf"), ("CONVENTIONS.md", "conventions")):
    b, fm, off = read_body(name)
    if b is not None: add_block("external", source, name, "**", b)

# 需要检查的单位
units, missing_ids = [], 0
for b in blocks:
    files_in = [f for f in changed if in_scope(b["scope"], f)]
    if not files_in:
        continue
    if b["layer"] == "ignore":
        continue
    rules = crlib.split_rules(b["body"]) if b["layer"] != "external" else []
    no_id = [r for r in rules if not r["id"]]
    if b["layer"] in ("L1", "L2", "L3"):
        missing_ids += len(no_id)
    for r in rules:
        if not r["id"]:
            continue
        scope = r["meta"].get("scope")
        af = [f for f in files_in if not scope or any(crlib.glob_match(g, f) for g in scope.split(","))]
        if not af:
            continue
        units.append({
            "unit": r["id"], "layer": b["layer"], "source": b["source"], "file": b["file"], "pack": b.get("pack", ""),
            "text": r["text"], "text_hash": crlib.short_hash(crlib.norm(r["text"]), 12),
            "severity": r["meta"].get("severity", ""), "applies_files": af, "line": r["start"] + offsets.get(b["file"], 0),
        })
    if b["layer"] == "external" or no_id or not rules:
        units.append({
            "unit": "FILE:" + b["file"], "layer": b["layer"], "source": b["source"], "file": b["file"], "pack": b.get("pack", ""),
            "text": b["body"][:20000], "text_hash": crlib.short_hash(crlib.norm(b["body"]), 12),
            "severity": "", "applies_files": files_in, "line": 0,
        })

# rules.txt
out = []
s_lines = []
if settings.get("skip"): s_lines.append("跳过的路径(收集 diff 时已经排除):" + "、".join(settings["skip"]))
if settings.get("limits"): s_lines.append("条数上限(汇总时套用):" + "、".join("%s %s" % kv for kv in settings["limits"].items()))
if settings.get("severity"): s_lines.append("按编号指定的严重程度(汇总时套用):" + "、".join("%s → %s" % kv for kv in settings["severity"].items()))
if disabled_ok: s_lines.append("关掉的组织规则:" + "、".join("%s(%s)" % kv for kv in disabled_ok.items()))
if s_lines:
    out.append("=== SETTINGS file=REVIEW.md(%s) ===" % source_note)
    out.extend(s_lines)
    out.append("以上设置由脚本执行,角色不需要再处理。REVIEW.md 正文里的其他审查要求(证据要求、严重程度的定义)照常采用。")
    out.append("")
for b in blocks:
    head = "=== RULES layer=%s source=%s file=%s scope=%s" % (b["layer"], b["source"], b["file"], b["scope"])
    if b.get("pack"):
        head += " pack=%s version=%s" % (b["pack"], b["version"])
    out.append(head + " ===")
    out.append(b["body"])
    out.append("")
for p in dup_notes:
    out.append("=== DUPLICATE file=%s(内容与上面某个块相同,已跳过)===" % p)
rules_txt = "\n".join(out).rstrip("\n") + ("\n" if out else "")

fp = hashlib.sha1(rules_txt.encode("utf-8")).hexdigest()[:10]
code = crlib.nonce()
with open(os.path.join(d, ".rules-nonce"), "w") as f:
    f.write(code + "\n")
if not compat:
    with open(os.path.join(d, "rules.txt"), "w", encoding="utf-8") as f:
        f.write(rules_txt)
crlib.save_json(os.path.join(d, "rules-index.json"), units)

by_layer = {}
for u in units:
    by_layer[u["layer"]] = by_layer.get(u["layer"], 0) + 1
ids = [u for u in units if not u["unit"].startswith("FILE:")]
m = {
    "units": len(units), "ids": len(ids), "files": len({b["file"] for b in blocks}),
    "units_L1": by_layer.get("L1", 0), "units_L2": by_layer.get("L2", 0), "units_L3": by_layer.get("L3", 0),
    "units_external": by_layer.get("external", 0), "fingerprint": fp, "source": source_note,
    "org_rules": ("%s@%s" % (rdir, org_version)) if rdir and settings.get("extends") else "none",
    "org_rules_stale": org_stale, "packs_loaded": ",".join(packs_loaded), "packs_missing": ",".join(packs_missing),
    "packs_skipped": ",".join(packs_skipped), "disabled": ";".join("%s:%s" % kv for kv in disabled_ok.items()),
    "disable_without_reason": ",".join(disabled_bad), "deprecated": ",".join(deprecated), "missing_ids": missing_ids,
}
with open(os.path.join(d, "rules-meta.txt"), "w", encoding="utf-8") as f:
    for k, v in m.items():
        f.write("%s=%s\n" % (k, v))

with open(os.path.join(d, "task-rules.md"), "w", encoding="utf-8") as f:
    f.write("## 本次审查适用的规则 · RULES-FP=%s\n\n" % fp)
    if not units:
        f.write("这次改动没有适用的规则。\n")
    else:
        f.write("需要检查的单位共 %d 个,检查清单要覆盖下面每一项(有编号的按编号,没有编号的规则文件按文件):\n\n" % len(units))
        for u in units:
            f.write("- %s(%s;涉及 %s)\n" % (u["unit"], u["layer"], "、".join(u["applies_files"][:6]) + ("等 %d 个文件" % len(u["applies_files"]) if len(u["applies_files"]) > 6 else "")))
        f.write("\n规则原文:\n\n")
        f.write(rules_txt)
        f.write("\n---\n核对码:%s —— 交检查清单时把它原样写进 nonce 字段。\n" % code)

if compat:
    sys.stdout.write(rules_txt)
else:
    print("rules: units=%d ids=%d files=%d fingerprint=%s source=%s%s%s" % (
        len(units), len(ids), m["files"], fp, source_note,
        (" packs_missing=" + m["packs_missing"]) if packs_missing else "",
        (" org_rules_stale=yes") if org_stale == "yes" else ""))
PY
