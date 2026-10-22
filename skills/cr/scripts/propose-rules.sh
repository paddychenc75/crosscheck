#!/bin/sh
# propose-rules.sh — 把批准的规则改动写进规则文件:分配编号、按固定格式写入、跑 rules-lint。
#
# 用法: propose-rules.sh --ops 改动.json [--target project|ignore] [--repo DIR]
#                        [--worktree | --mr] [--preview 仓库外的预览.json] [--title 标题] [--yes]
#
#   改动.json 是一个数组,每项是下面三种之一:
#     {"op": "add", "text": "一句能对照代码检查的话", "reason": "原因", "scope": "src/**", "severity": "major",
#      "src": "!301,!317", "file": "src/orders/REVIEW.md"}         file 可选,默认根目录 REVIEW.md
#      组织规则由外部平台维护,本命令不修改组织来源。
#     {"op": "remove", "id": "PRJ-004"}
#     {"op": "replace", "id": "PRJ-009", "text": "改写后的规则", "reason": "原因"}
#     {"op": "confirm", "id": "PRJ-003"}        负责人复核后确认保留:记下 reviewed=<本月>
#   --target   project(默认 REVIEW.md；也支持已选定的原生 rules)、ignore(.cr/ignore.md)
#   原生格式用 file + old_text 唯一定位正文；paths.match / paths.miss 验证路径，保留 frontmatter。
#   --preview  保存具体 diff 的基准与未合入提案；--yes 时必须一致。--mr 必填，工作区旧调用可不填。
#   --worktree 直接改到工作区,由你检查后自己提交(审查后顺手定的单条规则用这个)
#   --mr       在临时 worktree 里基于默认分支修改,推送新分支并创建带 crosscheck-rule 标签的 MR(只创建、不合并)
#   不带 --yes 时只预览 diff 并保存指定的 preview，不修改规则文件。
# 编号:REVIEW.md 项目规则 PRJ-NNN、抑制 IGN-NNN，只增不复用；原生 rules 不强制编号。需要 python3。

set -eu

die() { printf 'propose-rules: %s\n' "$*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "需要 python3"
here=$(cd "$(dirname "$0")" && pwd)
ops=""; target=project; repo=""; how=worktree; title=""; yes=no; preview=""
while [ $# -gt 0 ]; do
  case "$1" in
    --preview) preview=${2:-}; shift 2 ;;
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
  pack:*) die "组织规则只读：请到外部规则平台维护；Crosscheck 只支持 --target project 或 ignore" ;;
  *) die "--target 应为 project 或 ignore" ;;
esac
repo=$(cd "$repo" && git rev-parse --show-toplevel)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/cr-propose.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# 修改的基准:--worktree 用工作区;--mr 用远程的默认分支(这样不受本地未提交改动的影响)
base_ref=""
base_sha=""
if [ "$how" = mr ]; then
  base=$(git -C "$repo" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)
  base=${base:-main}
  git -C "$repo" fetch -q origin "$base" >/dev/null 2>&1 || die "无法从 origin 拉取 $base(网络、权限或沙箱限制)"
  base_ref=$(git -C "$repo" rev-parse "origin/$base")
  base_sha=$base_ref
  [ -n "$preview" ] || die "--mr 需要 --preview <仓库外的预览JSON>；先预览、确认后用同一文件加 --yes"
fi

CR_PREVIEW="$preview" CR_YES="$yes" CR_HOW="$how" CR_SCRIPTS="$here" CR_OPS="$ops" CR_TARGET="$target" CR_REPO="$repo" CR_REF="$base_ref" CR_TMP="$tmp" python3 - <<'PY' || exit 1
import datetime, difflib, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib
import project_rules

ops = json.load(open(os.environ["CR_OPS"], encoding="utf-8"))
target, repo, ref, tmp = os.environ["CR_TARGET"], os.environ["CR_REPO"], os.environ["CR_REF"] or None, os.environ["CR_TMP"]
src = crlib.Source(repo, ref)
preview = os.environ["CR_PREVIEW"]
if preview and os.path.realpath(preview).startswith(os.path.realpath(repo) + os.sep):
    sys.exit("propose-rules: 预览文件请放在仓库外")
# 以当前未合入提案列表为预览的一部分；创建前再次读取。语义去重由宿主核对。
opened = []
if os.environ["CR_HOW"] == "mr":
    for page in range(1, 10001):
        r = subprocess.run(["glab", "api", "projects/:id/merge_requests?state=opened&labels=crosscheck-rule&per_page=100&page=%d" % page],
                           cwd=repo, capture_output=True, text=True)
        try:
            rows = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            sys.exit("propose-rules: 未合入规则 MR 未读全，不能创建重复提案；请重试预览")
        if rows and any(x.get('iid') in {y.get('iid') for y in opened} for x in rows):
            sys.exit("propose-rules: MR 分页重复，未读全")
        opened.extend(rows)
        if len(rows) < 100:
            break
    else:
        sys.exit("propose-rules: MR 分页未完成")
    opened = sorted([dict(iid=m.get('iid'), title=m.get('title'), description=m.get('description'), url=m.get('web_url')) for m in opened], key=lambda x: x['iid'])
    for m in opened:
        print("已有未合入提案 !%s %s %s\n%s" % (m['iid'], m['title'], m['url'], m['description'] or ''))

month = datetime.date.today().strftime("%Y-%m")

changed = {}
if target == "ignore":
    prefix, files_of = "IGN", lambda: [".cr/ignore.md"]
elif target == "project":
    prefix, files_of = "PRJ", lambda: sorted(set(f for f in src.files() if project_rules.kind(f) and f != ".cr/ignore.md") | set(changed))

used = 0
for f in files_of():
    for m in re.finditer(r"id=%s-(\d+)" % re.escape(prefix), src.read(f) or ""):
        used = max(used, int(m.group(1)))
# 删掉的编号不再分配:删除时记进 retired-ids(项目在 .cr/retired-ids,规则仓库在根目录 retired-ids)
retired = ".cr/retired-ids"
for m in re.finditer(r"%s-(\d+)" % re.escape(prefix), src.read(retired) or ""):
    used = max(used, int(m.group(1)))

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
    try:
        project_rules.writable(repo, path)
    except ValueError as e:
        sys.exit("propose-rules: " + str(e))
    if op.get("origin", "project") != "project":
        sys.exit("propose-rules: 组织及外部规则只读，请在原来源维护")
    if target == "project" and path == ".cr/ignore.md":
        sys.exit("propose-rules: ignore 文件须使用 --target ignore")
    native = project_rules.kind(path) not in ("review", "ignore")
    if native or (kind in ("replace", "remove") and op.get("old_text") and not op.get("id")):
        body = get(path)
        if kind == "add":
            if not op.get("text", "").strip():
                sys.exit("propose-rules: 规则正文不能为空")
            block = op['text'].strip()
            if op.get('reason'):
                block += "\n\n原因：" + op['reason'].strip()
            if not body:
                body = op.get('frontmatter', '')
                if body and (not body.startswith('---\n') or not body.rstrip().endswith('---')):
                    sys.exit("propose-rules: frontmatter 必须是完整的 YAML 头部")
            elif 'frontmatter' in op:
                sys.exit("propose-rules: 不自动扩大已有文件范围；请选对应文件或新建小文件")
            if crlib.norm(block) in crlib.norm(body) and crlib.norm(block):
                sys.exit("propose-rules: 已有同文规则，请更新原处")
            new = body.rstrip() + "\n\n" + block + "\n"
        elif kind in ('replace', 'remove'):
            old = op.get('old_text', '')
            _, content = crlib.parse_frontmatter(body)
            if not old or content.count(old) != 1:
                sys.exit("propose-rules: 原文定位不唯一或已改变，需要 file + old_text 精确定位正文")
            offset = len(body) - len(content)
            replacement = op.get('text', '') if kind == 'replace' else ''
            if kind == 'replace' and crlib.META_RE.findall(old) != crlib.META_RE.findall(replacement):
                sys.exit("propose-rules: 改写需保留已有编号和元数据；只选正文或使用编号操作")
            new = body[:offset] + content.replace(old, replacement, 1)
        elif kind == 'confirm':
            print("复核保留 %s；不强制给原生格式增加编号或日期，以 MR 说明记录复核" % path)
            continue
        else:
            sys.exit("propose-rules: 不认识的 op " + str(kind))
        record = project_rules.describe(path, new)
        if op.get('scope') and sorted(project_rules.patterns(op['scope'])) != sorted(record['patterns']):
            sys.exit("propose-rules: 候选 scope 与文件范围不同，请选择对应文件或新建；不忽略 scope")
        if record['issues']:
            sys.exit("propose-rules: " + '; '.join(record['issues']))
        before_record = project_rules.describe(path, get(path)) if get(path) else None
        if before_record and (record['frontmatter'] != before_record['frontmatter']):
            sys.exit("propose-rules: 改写不得隐式改变范围或用途；请单独展示文件级变更")
        # 不能仅凭文件写入成功判断范围；要求原生格式提供正反例。
        checks = op.get('paths', {})
        if not checks.get('match') or 'miss' not in checks or (record['patterns'] != ['**'] and not checks['miss']):
            sys.exit("propose-rules: 原生规则需要 paths.match / paths.miss 路径正反例")
        for should_match, key in ((True, 'match'), (False, 'miss')):
            for sample in checks[key]:
                if project_rules.matches(record, sample) != should_match:
                    sys.exit("propose-rules: 路径验收失败 %s (%s)" % (sample, key))
        print("路径验收 %s: 命中 %s；不命中 %s%s" % (path, checks['match'], checks['miss'],
              "；保留 excludeAgent: code-review，审查不加载" if record['excluded'] else ''))
        changed[path] = new
        continue
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
    elif kind in ("remove", "replace", "confirm"):
        rid = op.get("id")
        if not rid:
            sys.exit("propose-rules: 改写或删除需要 id，或 file + old_text 唯一定位")
        hits = []
        for f in files_of():
            if op.get("file") and f != path:
                continue
            body = get(f)
            for r in crlib.split_rules(crlib.parse_frontmatter(body)[1]):
                if r["id"] == rid:
                    hits.append(f)
        if len(hits) > 1:
            sys.exit("propose-rules: 规则编号不唯一，请指定 file 并核对原文")
        hit = hits[0] if hits else None
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
            for key in ("scope", "severity", "src"):
                if key in op: meta[key] = str(op[key]).replace(" ", "")
            lines[a:b] = render(op.get("text", r["text"]), op.get("reason", ""), meta).split("\n")
        changed[hit] = "\n".join(lines)
    else:
        print("propose-rules: 不认识的 op %s" % kind, file=sys.stderr); sys.exit(1)

changed = {p: t for p, t in changed.items() if t != (src.read(p) or "")}
plan = {"repo": os.path.realpath(repo), "mode": os.environ["CR_HOW"], "base": ref,
        "ops": ops, "opened": opened,
        "files": {p: {"before": src.read(p), "after": t} for p, t in changed.items()}}
if preview:
    if os.environ["CR_YES"] == "yes":
        if crlib.load_json(preview, {}) != plan:
            sys.exit("propose-rules: 基准、文件、提案或操作已改变，请重新预览并确认")
    else:
        crlib.save_json(preview, plan)
        print("预览已保存: " + preview)
manifest = []
for i, (path, text) in enumerate(sorted(changed.items())):
    try:
        if path != retired:
            project_rules.writable(repo, path)
        elif os.path.islink(os.path.join(repo, '.cr')) or os.path.islink(os.path.join(repo, retired)):
            raise ValueError('retired-ids 不能是符号链接')
    except ValueError as e:
        sys.exit("propose-rules: " + str(e))
    old = src.read(path) or ""
    out = os.path.join(tmp, "f%d" % i)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    manifest.append("%s=%s" % (path, out))
    sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), open(out, encoding="utf-8").read().splitlines(True), "a/" + path, "b/" + path))
with open(os.path.join(tmp, "manifest"), "w") as f:
    f.write("\n".join(manifest) + "\n")
with open(os.path.join(tmp, "body.md"), "w", encoding="utf-8") as f:
    f.write("项目规则改动，保留原维护位置。请核对规则、范围、例外与来源；只创建 MR，不自动合并。\n\n")
    f.write("基准: %s\n\n" % (ref or 'worktree'))
    f.write("确认的操作与证据：\n\n```json\n" + json.dumps(ops, ensure_ascii=False, indent=2) + "\n```\n")
    if opened:
        f.write("\n已核对的未合入提案：\n" + "\n".join("- !%s %s" % (m['iid'], m['url']) for m in opened) + "\n")
with open(os.path.join(tmp, "assigned"), "w") as f:
    f.write(" ".join(assigned))
PY

[ "$yes" = yes ] || { echo; echo "以上是将要做的改动。确认后加 --yes 再运行一次(--worktree 改到工作区,--mr 创建 MR)。"; exit 0; }
assigned=$(cat "$tmp/assigned")
if ! grep -q . "$tmp/manifest"; then
  echo "没有文件改动，不创建 MR；复核结论保留在本次预览中。"
  exit 0
fi
if [ "$how" = worktree ]; then
  backup="$tmp/backup"; mkdir -p "$backup"
  while IFS= read -r spec; do
    [ -n "$spec" ] || continue
    rel=${spec%%=*}; from=${spec#*=}
    if [ -f "$repo/$rel" ]; then mkdir -p "$backup/$(dirname "$rel")"; cp "$repo/$rel" "$backup/$rel"; fi
    mkdir -p "$repo/$(dirname "$rel")"; cp "$from" "$repo/$rel"
  done < "$tmp/manifest"
  lint_target="--repo"
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
  lint_target="--repo"
  sh "$here/open-mr.sh" --repo "$repo" --base "$base" --expected-base "$base_sha" --branch "$name" --title "${title:-crosscheck: 更新审查规则}" --label crosscheck-rule --body "$tmp/body.md" \
    --check "sh '$here/rules-lint.sh' $lint_target \"\$1\"" --yes "$@"
fi
