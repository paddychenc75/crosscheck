"""settings.sh 的实现,用法和说明见 settings.sh。"""
import json, os, subprocess, sys
sys.dont_write_bytecode = True
import crlib

args = sys.argv[1:]
mode = args[0]
if mode == "apply":
    out, root, ref = args[1], args[2], (args[3] if len(args) > 3 else "")
else:
    out, root, ref = "", args[1], (args[2] if len(args) > 2 else "")

if ref and subprocess.run(["git", "-C", root, "cat-file", "-e", ref + "^{commit}"], capture_output=True).returncode:
    print("settings: base 无法读取，不回退工作区", file=sys.stderr)
    sys.exit(1)
src = crlib.Source(root, ref or None)
text = src.read("REVIEW.md")
fm = crlib.parse_frontmatter(text)[0] if text else {}
if text and crlib.frontmatter_unclosed(text):
    print("settings: REVIEW.md 头部以 --- 开头但没有结束的 ---,skip、limits 都没有生效", file=sys.stderr)
settings = {
    "source": ("base:" + ref[:12]) if ref else "worktree",
    "found": text is not None,
    "skip": crlib.as_list(fm.get("skip")),
    "limits": crlib.as_map(fm.get("limits")),
}

if mode == "show":
    print(json.dumps(settings, ensure_ascii=False, indent=2))
    sys.exit(0)

crlib.save_json(os.path.join(out, "settings.json"), settings)
if not settings["skip"]:
    sys.exit(0)

# 按文件把 diff.patch 拆开,skip 命中的移出去。
patch = os.path.join(out, "diff.patch")
chunks, cur = [], None
with open(patch, encoding="utf-8", errors="surrogateescape") as f:
    for line in f:
        if line.startswith("diff --git "):
            cur = [line]
            chunks.append(cur)
        elif cur is not None:
            cur.append(line)
keep, moved = [], []
for c in chunks:
    path = c[0].rstrip("\n").split(" b/", 1)[-1]
    (moved if crlib.any_match(settings["skip"], path) else keep).append((path, c))
if not moved:
    sys.exit(0)
with open(patch, "w", encoding="utf-8", errors="surrogateescape") as f:
    for _, c in keep:
        f.writelines(c)
with open(os.path.join(out, "files.txt"), "w", encoding="utf-8") as f:
    for p, _ in keep:
        f.write(p + "\n")
with open(os.path.join(out, "skipped.txt"), "a", encoding="utf-8") as f:
    for p, _ in moved:
        f.write("%s\treview-skip\n" % p)
