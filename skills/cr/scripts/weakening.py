"""测试和检查被削弱的地方:删了测试文件或断言、改了断言的期望值、新增 skip / only / 抑制注释、
降低覆盖率阈值、删除或放宽 CI 检查。由 collect-context 调用,结果写进 weakening.txt。"""
import os
import re

import crlib


def chunks(path):
    """按文件切 diff:每个文件的路径、是否删除、增加的行、删除的行。"""
    out, cur = [], None
    for line in (crlib.read_text(path) or "").split("\n"):
        if line.startswith("diff --git "):
            cur = {"path": line.split(" b/", 1)[-1], "deleted": False, "add": [], "rem": []}
            out.append(cur)
        elif cur is not None:
            if line.startswith("deleted file mode"):
                cur["deleted"] = True
            elif line.startswith("+") and not line.startswith("+++"):
                cur["add"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                cur["rem"].append(line[1:])
    return out


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
    """扫描 diff 和被跳过的测试改动,返回 [(类型, 文件, 说明)]。"""
    weak = []
    for c in chunks(os.path.join(review_dir, "skipped.patch")) + chunks(os.path.join(review_dir, "diff.patch")):
        p = c["path"]
        if c["deleted"] and c["rem"] and re.search(r"(^|/)(tests?|__tests__|spec)/|_test\.|\.test\.|\.spec\.|Test\.", p):
            weak.append(("test-file-deleted", p, "删除了测试文件"))
            continue
        ra = sum(1 for x in c["rem"] if ASSERT.search(x)); aa = sum(1 for x in c["add"] if ASSERT.search(x))
        if ra > aa:
            weak.append(("assertions-removed", p, "断言减少 %d 处(删 %d、加 %d)" % (ra - aa, ra, aa)))
        # 断言被改了期望值:同一个断言的写法还在,只是期望的结果变了(常见于把测试改成迎合新行为)
        olds = [x.strip() for x in c["rem"] if ASSERT.search(x)]
        news = [x.strip() for x in c["add"] if ASSERT.search(x)]
        # 把数字和字符串字面量换掉后一样、原文不一样,就是同一个断言改了值(适用于 expect(...).toBe(0)、assert x == 0 等写法)
        key = lambda x: re.sub(r"\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|\b(?:true|false|True|False|nil|None|null)\b", "#", x)
        for o in olds:
            n = next((x for x in news if x != o and key(x) == key(o)), None)
            if n:
                weak.append(("assertion-changed", p, "断言改了值:%s → %s" % (o[:80], n[:80])))
        for x in c["add"]:
            if SKIP.search(x):
                weak.append(("skip-added", p, "新增跳过:" + x.strip()[:120]))
            elif ONLY.search(x):
                weak.append(("only-added", p, "新增 only(其余测试不再运行):" + x.strip()[:120]))
            elif SUPPRESS.search(x):
                weak.append(("suppression-added", p, "新增抑制:" + x.strip()[:120]))
        if COVERAGE_FILE.search(p):
            old = {}
            for x in c["rem"]:
                m = COV_LINE.search(x)
                if m: old[m.group(1).lower()] = float(m.group(2))
            for x in c["add"]:
                m = COV_LINE.search(x)
                if m and m.group(1).lower() in old and float(m.group(2)) < old[m.group(1).lower()]:
                    weak.append(("coverage-lowered", p, "覆盖率阈值 %s 从 %g 降到 %g" % (m.group(1), old[m.group(1).lower()], float(m.group(2)))))
        if CI_FILE.search(p):
            added = {crlib.norm(x) for x in c["add"]}
            gone = [x.strip() for x in c["rem"] if CI_CHECK.search(x) and crlib.norm(x) not in added and x.strip() and not x.strip().startswith("#")]
            if gone:
                weak.append(("ci-check-removed", p, "删除了检查步骤:" + " / ".join(g[:60] for g in gone[:3]) + (" 等 %d 行" % len(gone) if len(gone) > 3 else "")))
            for x in c["add"]:
                if CI_WEAK.search(x):
                    weak.append(("ci-weakened", p, "放宽了检查:" + x.strip()[:120]))
    return weak


def write(review_dir, weak):
    with open(os.path.join(review_dir, "weakening.txt"), "w", encoding="utf-8") as f:
        for k, p, why in weak:
            f.write("%s\t%s\t%s\n" % (k, p, why.replace("\t", " ")))
