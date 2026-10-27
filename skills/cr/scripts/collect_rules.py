"""collect-rules.sh 的实现,用法和说明见 collect-rules.sh。"""
import os, re, subprocess, sys
sys.dont_write_bytecode = True
import crlib
import project_rules

d = os.path.abspath(sys.argv[1])
meta = crlib.read_kv(os.path.join(d, "meta.txt"))
root, base = meta.get("repo_root", ""), meta.get("base_sha", "")

ref, source_note = None, "worktree"
if meta.get("mode", "local") != "local":
    if not base or subprocess.run(["git", "-C", root, "cat-file", "-e", base + "^{commit}"], capture_output=True).returncode:
        print("collect-rules: base 无法读取,不回退工作区", file=sys.stderr)
        sys.exit(1)
    ref, source_note = base, "base:" + base[:12]
src = crlib.Source(root, ref)

try:
    changed = [x.strip() for x in open(os.path.join(d, "files.txt"), encoding="utf-8") if x.strip()]
except OSError:
    changed = []

blocks, errors, excluded = [], [], []
HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
SUPPRESS = re.compile(r"^(不要报|(do not|don't|dont) (report|flag))", re.I)
def split_suppress(body):
    """把「不要报」一节(到下一个同级或更高级标题为止)和其余正文分开。"""
    keep, sup, level = [], [], None
    for line in body.split("\n"):
        m = HEAD.match(line)
        if m and level is not None and len(m.group(1)) <= level:
            level = None
        if m and level is None and SUPPRESS.match(m.group(2)):
            level = len(m.group(1))
        (sup if level is not None else keep).append(line)
    return "\n".join(keep).strip("\n"), "\n".join(sup).strip("\n")
def add(layer, record, scope, applies):
    body = record["body"].strip("\n")
    if body.strip() and applies:
        text, suppress = split_suppress(body) if record["source"] in ("review", "agents-rules") else (body, "")
        blocks.append({"layer": layer, "source": record["source"], "file": record["file"], "scope": scope,
                       "applies_files": applies, "text": text, "suppress": suppress, "offset": record["offset"]})

records = {r["file"]: r for r in project_rules.discover(src)}
# 目录级 REVIEW.md:改动文件所在的每一级目录,深的在前
dirs = set()
for f in changed:
    p = os.path.dirname(f)
    while p:
        dirs.add(p); p = os.path.dirname(p)
for dd in sorted(dirs, key=lambda x: (-x.count("/"), x)):
    r = records.get(dd + "/REVIEW.md")
    if r:
        add("L3", r, dd + "/**", [f for f in changed if f.startswith(dd + "/")])
if "REVIEW.md" in records:
    add("L2", records["REVIEW.md"], "**", list(changed))
for path, r in sorted(records.items()):
    if r["source"] == "review":
        continue
    errors.extend(path + ": " + x for x in r["issues"])
    if r["excluded"]:
        excluded.append(path); continue
    add("external", r, ",".join(r["patterns"]), [f for f in changed if project_rules.matches(r, f)])

out = []
for b in blocks:
    out.append("=== RULES layer=%s source=%s file=%s scope=%s ===" % (b["layer"], b["source"], b["file"], b["scope"]))
    out.append(b["text"])
    if b["suppress"]:
        out.append(b["suppress"])
    out.append("")
with open(os.path.join(d, "rules.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
crlib.save_json(os.path.join(d, "rules-index.json"), blocks)
with open(os.path.join(d, "rules-meta.txt"), "w", encoding="utf-8") as f:
    f.write("blocks=%d\nsource=%s\nerrors=%s\nexcluded=%s\n" % (len(blocks), source_note, ";".join(errors), ",".join(excluded)))
for e in errors:
    print("collect-rules: " + e, file=sys.stderr)
print("rules: blocks=%d files=%s source=%s" % (len(blocks), ",".join(b["file"] for b in blocks) or "-", source_note))
