"""记录人对某条发现的判断,写进项目级的 feedback.jsonl。

用法: python3 feedback_record.py VERDICT TARGET [原因] [--repo DIR]    由 feedback.sh 调用,说明见 feedback.sh
"""
import datetime
import json
import os
import re
import subprocess
import sys

import crlib

NAMES = {"invalid": "不成立", "wontfix": "成立但不修", "valid": "成立", "disputed": "有争议"}
STAMP = re.compile(r"\d{8}-\d{6}")


def fail(msg):
    print("feedback: " + msg, file=sys.stderr)
    sys.exit(1)


def parse(argv):
    repo, rest = None, []
    it = iter(argv)
    for a in it:
        if a == "--repo":
            repo = next(it, None) or fail("--repo 需要一个值")
        else:
            rest.append(a)
    return repo, rest


def find(pdir, target):
    """在项目的各次审查记录里找这条发现,每条取最近一次出现:{key: (目标目录, 发现)}。"""
    m = re.fullmatch(r"(.+):(\d+)", target)

    def matches(f):
        return f.get("id") == target or (bool(m) and f.get("file") == m.group(1) and str(f.get("line")) == m.group(2))

    hits = {}
    for tname in os.listdir(pdir):
        tdir = os.path.join(pdir, tname)
        if not os.path.isdir(tdir):
            continue
        for stamp in sorted((d for d in os.listdir(tdir) if STAMP.fullmatch(d)), reverse=True):
            rdir = os.path.join(tdir, stamp)
            for f in crlib.load_json(os.path.join(rdir, "findings.json"), []):
                if matches(f) and crlib.finding_key(f) not in hits:
                    hits[crlib.finding_key(f)] = (tdir, f)
    return hits


def main(argv):
    repo, args = parse(argv)
    if not args or args[0] not in NAMES:
        fail("VERDICT 应为 invalid、wontfix、valid 或 disputed")
    verdict, args = args[0], args[1:]
    if not args:
        fail("需要指定发现:id 或 file:line")
    target, reason = args[0], (args[1] if len(args) > 1 else "")
    if verdict in ("invalid", "wontfix") and not reason.strip():
        fail("判为%s时必须写明原因。请向用户确认原因后再记录" % NAMES[verdict])
    if repo and not os.path.isdir(repo):
        fail("--repo 指定的目录不存在: %s" % repo)
    if subprocess.run(["git"] + (["-C", repo] if repo else []) + ["rev-parse", "--is-inside-work-tree"], capture_output=True).returncode:
        fail("当前目录不是 git 仓库,请用 --repo 指定发现所属的仓库")
    repo_id = crlib.repo_identity(repo)
    pdir = crlib.project_dir(repo_id) or fail("这个项目还没有审查记录")

    hits = find(pdir, target)
    if not hits:
        fail("在这个项目的审查记录里找不到 %s。请用报告里的 id 再试一次" % target)
    if len(hits) > 1:
        lines = ["  - id %s:%s:%s %s(%s)" % (f.get("id"), f.get("file"), f.get("line"), f.get("summary", ""), os.path.basename(t)) for t, f in hits.values()]
        fail("%s 对应多条不同的发现,请改用 id 指定:\n%s" % (target, "\n".join(lines)))
    tdir, f = list(hits.values())[0]
    entry = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "verdict": verdict, "reason": reason,
        "id": f.get("id"), "key": crlib.feedback_key(f), "file": f.get("file"), "line": f.get("line"), "category": f.get("category"),
        "severity": f.get("severity"), "confidence": f.get("confidence"), "summary": f.get("summary"),
        "root_cause": f.get("root_cause"),
        "failure_scenario": f.get("failure_scenario", ""),
        "code_line": f.get("code_line", ""), "context_hash": f.get("context_hash", ""), "target": os.path.basename(tdir),
        "rule_file": f.get("rule_file", ""), "rule_quote": f.get("rule_quote", ""), "author": crlib.author_hash(repo), "project": repo_id,
    }
    with open(os.path.join(pdir, "feedback.jsonl"), "a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print("已记录:`%s:%s` %s → %s%s" % (f.get("file"), f.get("line"), f.get("summary", ""), NAMES[verdict], "(原因:%s)" % reason if reason else ""))
    if verdict in ("invalid", "wontfix"):
        print("之后在这个项目的任何分支、MR 和范围上审查到这段代码时,verifier 会参考这条判断:先重新核对是否同一场景、调用环境与判断前提;仍适用才抑制重复提醒。「不修」不否定缺陷事实或置信度。")


if __name__ == "__main__":
    main(sys.argv[1:])
