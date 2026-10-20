#!/bin/sh
# propose-rules.sh — 把批准的规则改动写进规则文件:分配编号、按固定格式写入、跑 rules-lint。
#
# 用法: propose-rules.sh --ops 改动.json [--target project|ignore|pack:<包名>] [--repo DIR]
#                        [--worktree | --mr] [--title 标题] [--yes]
#
#   改动.json 是一个数组,每项是下面三种之一:
#     {"op": "add", "text": "一句能对照代码检查的话", "reason": "原因", "scope": "src/**", "severity": "major",
#      "src": "!301,!317", "file": "src/orders/REVIEW.md"}         file 可选,默认根目录 REVIEW.md
#      组织规则包(pack:)还要给正反例:"bad": "违反的代码", "good": "正确的代码", "ext": "ts",
#      写成 packs/<包名>/examples/<编号>.bad.ts / .good.ts
#     {"op": "remove", "id": "PRJ-004"}
#     {"op": "replace", "id": "PRJ-009", "text": "改写后的规则", "reason": "原因"}
#     {"op": "confirm", "id": "PRJ-003"}        负责人复核后确认保留:记下 reviewed=<本月>
#   --target   project(默认,REVIEW.md)、ignore(.cr/ignore.md)、pack:<包名>(组织规则仓库的 packs/<包名>/rules.md)
#   --worktree 直接改到工作区,由你检查后自己提交(审查后顺手定的单条规则用这个)
#   --mr       在临时 worktree 里基于默认分支修改,推送新分支并创建带 crosscheck-rule 标签的 MR(只创建、不合并)
#   不带 --yes 时只打印改动后的 diff,不写任何文件。
# 编号:项目规则 PRJ-NNN、抑制 IGN-NNN、组织规则用包的 prefix;只增不复用。需要 python3。

set -eu

die() { printf 'propose-rules: %s\n' "$*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "需要 python3"
here=$(cd "$(dirname "$0")" && pwd)
ops=""; target=project; repo=""; how=worktree; title=""; yes=no
while [ $# -gt 0 ]; do
  case "$1" in
    --ops) ops=${2:-}; shift 2 ;;
    --target) target=${2:-}; shift 2 ;;
    --repo) repo=${2:-}; shift 2 ;;
    --worktree) how=worktree; shift ;;
    --mr) how=mr; shift ;;
    --title) title=${2:-}; shift 2 ;;
    --yes) yes=yes; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done
[ -f "$ops" ] || die "需要 --ops 指向改动的 JSON 文件"
case "$target" in
  project|ignore) [ -n "$repo" ] || repo=$(git rev-parse --show-toplevel 2>/dev/null) || die "当前目录不是 git 仓库,请用 --repo 指定" ;;
  pack:*)
    repo=${CROSSCHECK_RULES_DIR:-$(sh "$here/config.sh" get rules_dir)}
    repo=${repo:-${CROSSCHECK_HOME:-$HOME/.crosscheck}/org-rules}
    [ -d "$repo/.git" ] || die "本地没有组织规则仓库,先运行 sync-rules.sh"
    how=mr ;;
  *) die "--target 应为 project、ignore 或 pack:<包名>" ;;
esac
repo=$(cd "$repo" && git rev-parse --show-toplevel)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/cr-propose.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# 修改的基准:--worktree 用工作区;--mr 用远程的默认分支(这样不受本地未提交改动的影响)
base_ref=""
if [ "$how" = mr ]; then
  base=$(git -C "$repo" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)
  base=${base:-main}
  git -C "$repo" fetch -q origin "$base" >/dev/null 2>&1 || die "无法从 origin 拉取 $base(网络、权限或沙箱限制)"
  base_ref="origin/$base"
fi

CR_SCRIPTS="$here" CR_OPS="$ops" CR_TARGET="$target" CR_REPO="$repo" CR_REF="$base_ref" CR_TMP="$tmp" python3 - <<'PY' || exit 1
import datetime, difflib, json, os, re, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

ops = json.load(open(os.environ["CR_OPS"], encoding="utf-8"))
target, repo, ref, tmp = os.environ["CR_TARGET"], os.environ["CR_REPO"], os.environ["CR_REF"] or None, os.environ["CR_TMP"]
src = crlib.Source(repo, ref)
month = datetime.date.today().strftime("%Y-%m")

if target == "ignore":
    prefix, files_of = "IGN", lambda: [".cr/ignore.md"]
elif target == "project":
    prefix, files_of = "PRJ", lambda: sorted(f for f in src.files() if f == "REVIEW.md" or f.endswith("/REVIEW.md"))
else:
    pack = target.split(":", 1)[1]
    rel = "packs/%s/rules.md" % pack
    t = src.read(rel)
    if t is None:
        print("propose-rules: 规则仓库里没有 %s" % rel, file=sys.stderr); sys.exit(1)
    prefix = str(crlib.parse_frontmatter(t)[0].get("prefix") or "")
    if not prefix:
        print("propose-rules: %s 头部没有 prefix" % rel, file=sys.stderr); sys.exit(1)
    files_of = lambda: sorted(f for f in src.files() if f.startswith("packs/") and f.endswith("/rules.md"))

used = 0
for f in files_of():
    for m in re.finditer(r"id=%s-(\d+)" % re.escape(prefix), src.read(f) or ""):
        used = max(used, int(m.group(1)))
# 删掉的编号不再分配:删除时记进 retired-ids(项目在 .cr/retired-ids,规则仓库在根目录 retired-ids)
retired = "retired-ids" if target.startswith("pack:") else ".cr/retired-ids"
for m in re.finditer(r"%s-(\d+)" % re.escape(prefix), src.read(retired) or ""):
    used = max(used, int(m.group(1)))

changed = {}
def get(path):
    if path not in changed:
        changed[path] = src.read(path) or ""
    return changed[path]

def render(text, reason, meta):
    text = text.strip().rstrip("。.")
    line = "- %s。" % text + ("原因:%s。" % reason.strip().rstrip("。.") if reason and reason.strip() else "")
    m = " ".join("%s=%s" % (k, v) for k, v in meta.items() if v)
    return line + "\n  <!-- cr: %s -->" % m

assigned = []
for op in ops:
    kind = op.get("op")
    if target == "project":
        path = op.get("file") or "REVIEW.md"
    elif target == "ignore":
        path = ".cr/ignore.md"
    else:
        path = rel
    if kind == "add":
        used += 1
        rid = "%s-%03d" % (prefix, used)
        assigned.append(rid)
        meta = {"id": rid, "scope": op.get("scope", ""), "severity": op.get("severity", ""), "src": str(op.get("src", "")).replace(" ", ""), "added": month}
        block = render(op.get("text", ""), op.get("reason", ""), meta)
        body = get(path)
        if target == "project" and "## 每次都要检查" in body:
            # 放在「每次都要检查」这一节的末尾
            lines = body.split("\n")
            i = next(k for k, x in enumerate(lines) if x.strip().startswith("## 每次都要检查"))
            j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("## ")), len(lines))
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines[j:j] = block.split("\n")
            changed[path] = "\n".join(lines)
        else:
            head = ""
            if not body.strip():
                head = "# Review 约定\n\n## 每次都要检查\n\n" if target == "project" else ("# 不想再被提醒的模式\n\n" if target == "ignore" else "")
            changed[path] = (body.rstrip("\n") + "\n" if body.strip() else head) + block + "\n"
        if target.startswith("pack:"):
            # 组织规则必须带正反例(rules-lint 会检查)
            for k in ("bad", "good"):
                if op.get(k):
                    changed["packs/%s/examples/%s.%s.%s" % (pack, rid, k, op.get("ext") or "txt")] = op[k]
    elif kind in ("remove", "replace", "confirm"):
        rid = op.get("id")
        hit = None
        for f in files_of():
            body = get(f)
            for r in crlib.split_rules(crlib.parse_frontmatter(body)[1]):
                if r["id"] == rid:
                    hit = f
        if not hit:
            print("propose-rules: 找不到规则 %s" % rid, file=sys.stderr); sys.exit(1)
        body = get(hit)
        fm_body = crlib.parse_frontmatter(body)[1]
        off = len(body.split("\n")) - len(fm_body.split("\n"))
        r = next(x for x in crlib.split_rules(fm_body) if x["id"] == rid)
        lines = body.split("\n")
        a, b = r["start"] + off - 1, r["end"] + off
        if kind == "confirm":
            meta = dict(r["meta"]); meta["id"] = rid; meta["reviewed"] = month
            m = " ".join("%s=%s" % (k, v) for k, v in meta.items() if v)
            block = lines[a:b]
            if any(crlib.META_RE.search(x) for x in block):
                block = [crlib.META_RE.sub("<!-- cr: %s -->" % m, x) if crlib.META_RE.search(x) else x for x in block]
            else:
                block.append("  <!-- cr: %s -->" % m)
            lines[a:b] = block
        elif kind == "remove":
            del lines[a:b]
            changed[retired] = get(retired) + rid + "\n"
        else:
            meta = dict(r["meta"]); meta["id"] = rid; meta["added"] = meta.get("added", month)
            meta["changed"] = month
            lines[a:b] = render(op.get("text", r["text"]), op.get("reason", ""), meta).split("\n")
        changed[hit] = "\n".join(lines)
    else:
        print("propose-rules: 不认识的 op %s" % kind, file=sys.stderr); sys.exit(1)

manifest = []
for i, (path, text) in enumerate(sorted(changed.items())):
    old = src.read(path) or ""
    out = os.path.join(tmp, "f%d" % i)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    manifest.append("%s=%s" % (path, out))
    sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), open(out, encoding="utf-8").read().splitlines(True), "a/" + path, "b/" + path))
with open(os.path.join(tmp, "manifest"), "w") as f:
    f.write("\n".join(manifest) + "\n")
with open(os.path.join(tmp, "assigned"), "w") as f:
    f.write(" ".join(assigned))
PY

[ "$yes" = yes ] || { echo; echo "以上是将要做的改动。确认后加 --yes 再运行一次(--worktree 改到工作区,--mr 创建 MR)。"; exit 0; }
assigned=$(cat "$tmp/assigned")
if [ "$how" = worktree ]; then
  backup="$tmp/backup"; mkdir -p "$backup"
  while IFS= read -r spec; do
    [ -n "$spec" ] || continue
    rel=${spec%%=*}; from=${spec#*=}
    if [ -f "$repo/$rel" ]; then mkdir -p "$backup/$(dirname "$rel")"; cp "$repo/$rel" "$backup/$rel"; fi
    mkdir -p "$repo/$(dirname "$rel")"; cp "$from" "$repo/$rel"
  done < "$tmp/manifest"
  lint_target="--repo"
  [ "${target#pack:}" = "$target" ] || lint_target="--rules-repo"
  if ! sh "$here/rules-lint.sh" $lint_target "$repo"; then
    while IFS= read -r spec; do
      [ -n "$spec" ] || continue
      rel=${spec%%=*}
      if [ -f "$backup/$rel" ]; then cp "$backup/$rel" "$repo/$rel"; else rm -f "$repo/$rel"; fi
    done < "$tmp/manifest"
    die "rules-lint 有错误,改动已经撤回"
  fi
  echo "ok: 已改到工作区${assigned:+(新规则编号 $assigned)},没有提交。检查后由你提交。"
else
  set --
  while IFS= read -r spec; do [ -n "$spec" ] && set -- "$@" --put "$spec"; done < "$tmp/manifest"
  name="crosscheck/rules-$(date +%Y%m%d-%H%M%S)"
  printf '由 crosscheck 根据评审反馈提出的规则改动%s。\n\n请审查每条规则:能否对照代码检查、作用域是否合适、原因是否写清楚。被关闭时请在 MR 里写明原因,下次提炼就不会再提同样的规则。\n' "${assigned:+,新规则编号 $assigned}" > "$tmp/body.md"
  lint_target="--repo"
  [ "${target#pack:}" = "$target" ] || lint_target="--rules-repo"
  sh "$here/open-mr.sh" --repo "$repo" --branch "$name" --title "${title:-crosscheck: 更新审查规则}" --label crosscheck-rule --body "$tmp/body.md" \
    --check "sh '$here/rules-lint.sh' $lint_target \"\$1\"" --yes "$@"
fi
