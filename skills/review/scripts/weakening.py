"""测试与检查变更的启发式检测信号,不代表已确认缺陷:删了测试文件或断言、改了断言的期望值、新增 skip / only / 抑制注释、
降低覆盖率阈值、删除或放宽 CI 检查。由 collect-context 调用,结果写进 weakening.txt;每条信号自带 file:line 和 diff 片段。"""
import json
import os
import re

import crlib

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def chunks(path):
    """按文件切 diff:路径、是否删除、增加的行 add、删除的行 rem(都只有文本)。
    add_at / rem_at 与它们一一对应,是 (hunk 序号, 在 hunk 里的下标);hunks 按 @@ 切开,每行是 (符号, 行号, 文本):
    增加行是新版行号,删除行是旧版行号,上下文行用新版行号。"""
    out, cur, old, new = [], None, 0, 0
    for line in (crlib.read_text(path) or "").split("\n"):
        m = HUNK.match(line)
        if line.startswith("diff --git "):
            cur = {"path": line.split(" b/", 1)[-1], "deleted": False, "add": [], "rem": [], "add_at": [], "rem_at": [], "hunks": []}
            out.append(cur)
        elif cur is None:
            continue
        elif m:
            old, new = int(m.group(1)), int(m.group(2))
            cur["hunks"].append([])
        elif not cur["hunks"]:
            if line.startswith("deleted file mode"):
                cur["deleted"] = True
        elif line[:1] in ("+", "-", " "):   # 第一个 @@ 之后才是内容,--- / +++ 文件头不会混进来
            rows, sign, text = cur["hunks"][-1], line[0], line[1:]
            at = (len(cur["hunks"]) - 1, len(rows))
            if sign == "+":
                cur["add"].append(text); cur["add_at"].append(at); rows.append((sign, new, text)); new += 1
            elif sign == "-":
                cur["rem"].append(text); cur["rem_at"].append(at); rows.append((sign, old, text)); old += 1
            else:
                rows.append((sign, new, text)); old += 1; new += 1
    return out


def locate(c, *refs):
    """信号的位置和 diff 片段。refs 是 ("add" | "rem", 下标),指向 c["add"] / c["rem"] 里触发信号的行。
    只取 diff 顺序里第一个 hunk 中的相关行:有增加行用新版行号,只有删除行用旧版行号并标「旧版」;多行写成首行-末行。
    片段是首行到末行之间的原始 +/- 行,前后各带紧邻的一行上下文。"""
    at = sorted((c[side + "_at"][i], side) for side, i in refs)
    hit = [(r, side) for (h, r), side in at if h == at[0][0][0]]
    rows = c["hunks"][at[0][0][0]]
    used = [rows[r][1] for r, side in hit if side == "add"] or [rows[r][1] for r, side in hit]
    where = "%s:%d" % (c["path"], min(used)) + ("-%d" % max(used) if max(used) > min(used) else "")
    if not any(side == "add" for _, side in hit):
        where += "(旧版)"
    return where, "\n".join(s + t for s, _, t in rows[max(hit[0][0] - 1, 0):hit[-1][0] + 2])


ASSERT = re.compile(r"\b(assert\w*|expect\s*\(|should\.|\.should\b|require\.\w+\s*\(|t\.(Error|Errorf|Fatal|Fatalf|Fail)\b|XCTAssert\w*|EXPECT_\w+|ASSERT_\w+|verify\s*\()")
SKIP = re.compile(r"(\.skip\s*\(|\bx(it|describe|test)\s*\(|@Disabled\b|@Ignore\b|\bt\.Skip(Now|f)?\s*\(|pytest\.mark\.(skip|xfail)|@unittest\.skip|\.todo\s*\()")
ONLY = re.compile(r"\b(it|describe|test)\.only\s*\(|\bf(it|describe)\s*\(")
SUPPRESS = re.compile(r"(@ts-ignore|@ts-nocheck|@ts-expect-error|eslint-disable|//\s*nolint|#\s*noqa|#\s*type:\s*ignore|@SuppressWarnings|NOSONAR|pragma:\s*no cover|istanbul ignore)")
COVERAGE_FILE = re.compile(r"(^|/)((jest|vitest|karma)\.config\.[cm]?[jt]s|\.nycrc(\.json)?|\.c8rc(\.json)?|codecov\.ya?ml|pyproject\.toml|setup\.cfg|\.coveragerc|tox\.ini|pom\.xml|build\.gradle(\.kts)?|package\.json)$")
COV_LINE = re.compile(r"(threshold|fail_under|fail-under|minimum|coverage|branches|lines|functions|statements)[^0-9]*?(\d+(?:\.\d+)?)", re.I)
CI_FILE = re.compile(r"(^|/)(\.gitlab-ci\.ya?ml|Jenkinsfile|azure-pipelines\.ya?ml|\.circleci/config\.ya?ml|\.github/workflows/[^/]+\.ya?ml)$")
CI_CHECK = re.compile(r"\b(test|tests|lint|check|coverage|vet|sonar|scan|audit|typecheck|tsc)\b", re.I)
CI_WEAK = re.compile(r"(allow_failure:\s*true|continue-on-error:\s*true|\|\|\s*true\b|--no-verify|-DskipTests|--skip-tests|-Dmaven\.test\.skip)")


def scan(review_dir):
    """扫描 diff 和被跳过的测试改动,返回 [(类型, 文件, 说明, 位置, 片段)]。"""
    weak = []
    for c in chunks(os.path.join(review_dir, "skipped.patch")) + chunks(os.path.join(review_dir, "diff.patch")):
        p = c["path"]
        sig = lambda kind, why, *refs: weak.append((kind, p, why) + locate(c, *refs))
        if c["deleted"] and c["rem"] and re.search(r"(^|/)(tests?|__tests__|spec)/|_test\.|\.test\.|\.spec\.|Test\.", p):
            sig("test-file-deleted", "删除了测试文件(共 %d 行)" % len(c["rem"]), ("rem", 0))
            continue
        # 断言:(在 rem / add 里的下标, 去掉首尾空白的原文)
        olds = [(i, x.strip()) for i, x in enumerate(c["rem"]) if ASSERT.search(x)]
        news = [(i, x.strip()) for i, x in enumerate(c["add"]) if ASSERT.search(x)]
        if len(olds) > len(news):
            sig("assertions-removed", "断言减少 %d 处(删 %d、加 %d)" % (len(olds) - len(news), len(olds), len(news)), *[("rem", i) for i, _ in olds])
        # 断言被改了期望值:同一个断言的写法还在,只是期望的结果变了(常见于把测试改成迎合新行为)
        # 把数字和字符串字面量换掉后一样、原文不一样,就是同一个断言改了值(适用于 expect(...).toBe(0)、assert x == 0 等写法)
        key = lambda x: re.sub(r"\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|\b(?:true|false|True|False|nil|None|null)\b", "#", x)
        for i, o in olds:
            n = next(((j, x) for j, x in news if x != o and key(x) == key(o)), None)
            if n:
                sig("assertion-changed", "断言改了值:%s → %s" % (o[:80], n[1][:80]), ("rem", i), ("add", n[0]))
        for i, x in enumerate(c["add"]):
            if SKIP.search(x):
                sig("skip-added", "新增跳过:" + x.strip()[:120], ("add", i))
            elif ONLY.search(x):
                sig("only-added", "新增 only(其余测试不再运行):" + x.strip()[:120], ("add", i))
            elif SUPPRESS.search(x):
                sig("suppression-added", "新增抑制:" + x.strip()[:120], ("add", i))
        if COVERAGE_FILE.search(p):
            old = {}
            for i, x in enumerate(c["rem"]):
                m = COV_LINE.search(x)
                if m: old[m.group(1).lower()] = (float(m.group(2)), i)
            for j, x in enumerate(c["add"]):
                m = COV_LINE.search(x)
                if m and m.group(1).lower() in old and float(m.group(2)) < old[m.group(1).lower()][0]:
                    was, i = old[m.group(1).lower()]
                    sig("coverage-lowered", "覆盖率阈值 %s 从 %g 降到 %g" % (m.group(1), was, float(m.group(2))), ("rem", i), ("add", j))
        if CI_FILE.search(p):
            added = {crlib.norm(x) for x in c["add"]}
            gone = [(i, x.strip()) for i, x in enumerate(c["rem"]) if CI_CHECK.search(x) and crlib.norm(x) not in added and x.strip() and not x.strip().startswith("#")]
            if gone:
                sig("ci-check-removed", "删除了检查步骤:" + " / ".join(g[:60] for _, g in gone[:3]) + (" 等 %d 行" % len(gone) if len(gone) > 3 else ""), *[("rem", i) for i, _ in gone])
            for i, x in enumerate(c["add"]):
                if CI_WEAK.search(x):
                    sig("ci-weakened", "放宽了检查:" + x.strip()[:120], ("add", i))
    return weak


def write(review_dir, weak):
    """每行 kind<TAB>file<TAB>说明<TAB>file:line<TAB>片段(JSON 字符串,换行转义成 \\n)。"""
    with open(os.path.join(review_dir, "weakening.txt"), "w", encoding="utf-8") as f:
        for k, p, why, where, snippet in weak:
            f.write("%s\t%s\t%s\t%s\t%s\n" % (k, p, why.replace("\t", " "), where.replace("\t", " "), json.dumps(snippet, ensure_ascii=False)))
