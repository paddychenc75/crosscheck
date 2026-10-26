#!/bin/sh
# feedback.sh — 记录人对某条发现的判断;审查时把相关的历史判断交给 verifier 参考。
#
# 用法: feedback.sh VERDICT TARGET [原因] [--repo DIR]
#       feedback.sh history REVIEW_DIR OUT.json
#
#   VERDICT  invalid   不成立(误报)          必须给原因
#            wontfix   成立,但不打算修        必须给原因
#            valid     成立(修了,或认可)
#            disputed  有争议,还没有结论
#   TARGET   发现的 id(报告和记录里都有);也可以写 file:line,但多条记录对得上时会要求改用 id
#
# 判断记在项目级的 ~/.crosscheck/reviews/<项目名>/feedback.jsonl,对这个项目的所有分支、MR 和范围
# 都有效,不需要知道是哪一次审查。每条记录带着发现的身份:文件、维度、摘要、出问题的那一行,
# 以及那一行上下各 2 行代码的哈希;还记着是谁判断的(邮箱的哈希,不存人名)。
#
# history 由 collect-diff.sh 在每次审查开始时调用:取出这次改动涉及的文件上的历史判断,并标明
# 那段代码现在是否原样还在(same_code),写进 OUT.json。它不删除任何候选,怎么用由 verifier 判断:
# 同一个问题、代码原样未变的,不再报;代码变了,重新评估。需要 python3。

set -eu

die() { printf 'feedback: %s\n' "$*" >&2; exit 1; }
case "${1:-}" in -h|--help|"") sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
command -v python3 >/dev/null 2>&1 || die "需要 python3。这次判断没有被记录,请如实告诉用户"

mode=$1; shift
repo_arg=""; args=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || die "--repo 需要一个值"; repo_arg=$2; shift 2 ;;
    *) args="${args}$1
"; shift ;;
  esac
done

# 当前项目的身份,算法与 collect-diff.sh 一致;原始的远程地址不会被输出。
repo_id=""
if [ "$mode" != history ]; then
  if [ -n "$repo_arg" ]; then cd "$repo_arg" 2>/dev/null || die "--repo 指定的目录不存在: $repo_arg"; fi
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库,请用 --repo 指定发现所属的仓库"
  u=$(git remote get-url origin 2>/dev/null || true)
  if [ -n "$u" ]; then
    repo_id=$(printf '%s\n' "$u" | sed -e 's#^[A-Za-z][A-Za-z0-9+.-]*://##' -e 's#^[^@/]*@##' -e 's#^\([^/:]*\):[0-9][0-9]*/#\1/#' -e 's#:#/#' -e 's#/*$##' -e 's#\.git$##')
  else
    repo_id=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd)")
  fi
fi

CR_SCRIPTS=$(cd "$(dirname "$0")" && pwd) CR_MODE="$mode" CR_ARGS="$args" CR_REPO_ID="$repo_id" CR_HOME="${CROSSCHECK_HOME:-$HOME/.crosscheck}" python3 - <<'PY'
import datetime, hashlib, json, os, re, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["CR_SCRIPTS"])
import crlib

mode = os.environ["CR_MODE"]; args = [a for a in os.environ["CR_ARGS"].split("\n") if a != ""]
home = os.environ["CR_HOME"]; reviews = os.path.join(home, "reviews")
NAMES = {"invalid": "不成立", "wontfix": "成立但不修", "valid": "成立", "disputed": "有争议"}

def fail(msg):
    print("feedback: " + msg, file=sys.stderr); sys.exit(1)
norm = lambda s: " ".join(str(s).split())
def ctx_hash(ls, n):
    return hashlib.sha1("\n".join(norm(x) for x in ls[max(0, n - 3):n + 2]).encode("utf-8")).hexdigest()[:12]
def read_kv(path):
    d = {}
    try:
        for line in open(path, encoding="utf-8"):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1); d[k] = v
    except Exception:
        pass
    return d
def load(path, default):
    try:
        x = json.load(open(path, encoding="utf-8")); return x if isinstance(x, type(default)) else default
    except Exception:
        return default
def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n")
def key(f):
    return f.get("id") or "|".join(str(f.get(k, "")) for k in ("file", "category", "since", "summary"))
def verdicts(pdir):
    return list(crlib.feedback_entries(pdir).values())      # 同一条发现以最后一次判断为准

if mode == "history":
    if len(args) < 2: fail("用法: feedback.sh history REVIEW_DIR OUT.json")
    rdir, out = args[0], args[1]
    meta = read_kv(os.path.join(rdir, "meta.txt"))
    pdir = os.path.dirname(meta.get("store_dir", "")) if meta.get("store_dir") else ""
    try:
        files = {l.strip() for l in open(os.path.join(rdir, "files.full.txt") if os.path.exists(os.path.join(rdir, "files.full.txt")) else os.path.join(rdir, "files.txt"), encoding="utf-8") if l.strip()}
    except Exception:
        files = set()
    entries = [e for e in verdicts(pdir) if e.get("file") in files] if pdir else []
    if not entries:
        sys.exit(0)
    root, ref, tree = meta.get("repo_root", ""), meta.get("code_ref", ""), meta.get("head_checked_out") == "yes"
    cache = {}
    def lines_of(path):
        if path not in cache:
            try:
                if tree:
                    cache[path] = open(os.path.join(root, path), encoding="utf-8", errors="replace").read().split("\n")
                else:
                    cache[path] = subprocess.run(["git", "-C", root, "show", "%s:%s" % (ref, path)], capture_output=True, text=True, check=True).stdout.split("\n")
            except Exception:
                cache[path] = None
        return cache[path]
    out_list = []
    for e in entries:
        ls = lines_of(e["file"]) or []
        where = [i + 1 for i, x in enumerate(ls) if e.get("code_line") and norm(x) == norm(e["code_line"]) and ctx_hash(ls, i + 1) == e.get("context_hash")]
        out_list.append({
            "verdict": e.get("verdict"), "verdict_name": NAMES.get(e.get("verdict"), ""), "reason": e.get("reason", ""), "time": e.get("time", ""),
            "rule_file": e.get("rule_file", ""), "rule_quote": e.get("rule_quote", ""),
            "file": e.get("file"), "category": e.get("category"), "summary": e.get("summary"), "recorded_line": e.get("line"),
            "code_line": e.get("code_line", ""), "same_code": bool(where), "line_now": where[0] if where else None, "id": e.get("id"),
        })
    save(out, out_list)
    sys.exit(0)

if mode not in NAMES: fail("VERDICT 应为 invalid、wontfix、valid 或 disputed")
if not args: fail("需要指定发现:id 或 file:line")
target = args[0]; reason = args[1] if len(args) > 1 else ""
if mode in ("invalid", "wontfix") and not reason.strip():
    fail("判为%s时必须写明原因。请向用户确认原因后再记录" % NAMES[mode])

want = os.environ["CR_REPO_ID"]; pdir = None
if os.path.isdir(reviews):
    for name in os.listdir(reviews):
        p = os.path.join(reviews, name)
        try:
            if open(os.path.join(p, ".repo"), encoding="utf-8").readline().strip() == want:
                pdir = p; break
        except Exception:
            pass
if not pdir: fail("这个项目还没有审查记录")

m = re.fullmatch(r"(.+):(\d+)", target)
def matches(f):
    if f.get("id") == target: return True
    return bool(m) and f.get("file") == m.group(1) and str(f.get("line")) == m.group(2)

hits = {}      # key -> (stamp, target_dir, finding),每条发现取最近一次出现
for tname in os.listdir(pdir):
    tdir = os.path.join(pdir, tname)
    if not os.path.isdir(tdir): continue
    for stamp in sorted((d for d in os.listdir(tdir) if re.fullmatch(r"\d{8}-\d{6}", d)), reverse=True):
        rdir = os.path.join(tdir, stamp)
        for f in load(os.path.join(rdir, "findings.json"), []) + load(os.path.join(rdir, "dismissed.json"), []):
            if matches(f) and key(f) not in hits:
                hits[key(f)] = (stamp, tdir, f)
if not hits:
    fail("在这个项目的审查记录里找不到 %s。请用报告里的 id 再试一次" % target)
if len(hits) > 1:
    lines = ["  - id %s:%s:%s %s(%s)" % (f.get("id"), f.get("file"), f.get("line"), f.get("summary", ""), os.path.basename(t)) for _, t, f in hits.values()]
    fail("%s 对应多条不同的发现,请改用 id 指定:\n%s" % (target, "\n".join(lines)))
stamp, tdir, f = list(hits.values())[0]
fid = key(f)
entry = {
    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "verdict": mode, "reason": reason,
    "id": f.get("id"), "key": fid, "file": f.get("file"), "line": f.get("line"), "category": f.get("category"),
    "severity": f.get("severity"), "confidence": f.get("confidence"), "summary": f.get("summary"),
    "code_line": f.get("code_line", ""), "context_hash": f.get("context_hash", ""), "target": os.path.basename(tdir),
    "rule_file": f.get("rule_file", ""), "rule_quote": f.get("rule_quote", ""), "author": crlib.author_hash(), "project": want,
}
with open(os.path.join(pdir, "feedback.jsonl"), "a", encoding="utf-8") as out:
    out.write(json.dumps(entry, ensure_ascii=False) + "\n")

# 判为不成立或不修的,从各目标最新的记录和增量基准里移走,下一次增量审查不会再作为遗留带入。
moved = 0
if mode in ("invalid", "wontfix"):
    for tname in os.listdir(pdir):
        tdir2 = os.path.join(pdir, tname)
        if not os.path.isdir(tdir2): continue
        for ptr in ("latest", "baseline"):
            try: s2 = open(os.path.join(tdir2, ptr), encoding="utf-8").readline().strip()
            except Exception: continue
            rdir = os.path.join(tdir2, s2); fp = os.path.join(rdir, "findings.json")
            fs = load(fp, None)
            if not isinstance(fs, list): continue
            keep = [x for x in fs if key(x) != fid]
            if len(keep) != len(fs):
                save(fp, keep); moved += 1
                dp = os.path.join(rdir, "dismissed.json"); ds = load(dp, [])
                ds.append(dict(f, dismissed_at=entry["time"], dismiss_reason=reason, verdict=mode)); save(dp, ds)

print("已记录:`%s:%s` %s → %s%s" % (f.get("file"), f.get("line"), f.get("summary", ""), NAMES[mode], "(原因:%s)" % reason if reason else ""))
if mode in ("invalid", "wontfix"):
    print("之后在这个项目的任何分支、MR 和范围上审查到这段代码时,verifier 会参考这条判断:代码原样未变就不再报,代码变了才重新评估。")
PY
