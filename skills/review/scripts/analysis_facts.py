#!/usr/bin/env python3
"""analysis_facts.py — 为审查整理改动的事实,不调用模型、不下结论。

用法: python3 analysis_facts.py <review_dir>

读 review_dir 里的 diff.full.patch(没有就用 diff.patch)、meta.txt,写 <review_dir>/analysis-facts.json:
  hunks       每个 hunk 的编号(H1..)、位置、所在函数、粗分类
  symbols     改动涉及的定义(函数、类、类型、配置键):增删、签名变化还是只改实现;
              签名变化 / 新增 / 删除的公开符号,在本仓库按名字查调用方
定义识别覆盖 TS/JS、Go、Python、Java/Kotlin;其他语言退回 git hunk 头里的函数名,再不行只给到文件。
调用方是按名字的文本匹配(git grep -w),同名、反射、依赖注入、跨仓库调用都会多报或漏报,
所以只当线索用。出错时尽量写出已有的部分,并在 limits 里注明;只有完全失败才以非零退出。

review 另外写三类线索(describe 复用 Facts.run,不带这些),都只是线索、不下结论:
  callers_to_check  签名变化或删除的公开符号,在 diff 之外的调用方:要逐个确认是否仍兼容
  fix_clues         被删改的 base 行最初由修复类提交引入(git blame):核对本次是否让那次修复失效
  cochange          历史上常和改动文件一起改、这次没改的文件(git log):核对是否漏改
"""
import json
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import crlib

GREP_BUDGET = 10.0       # 查调用方的总耗时上限(秒)
MAX_SYMBOLS = 30         # 最多查多少个符号的调用方
MAX_HITS = 50            # 单个符号命中超过这个数只给数量
MAX_LISTED = 20          # 每个符号最多列出多少处调用方
METHOD = '按名字在本仓库文本匹配(git grep -w),只看签名变化'
BLAME_BUDGET = 10.0      # 查修复线索(git blame)的总耗时上限(秒)
COCHANGE_BUDGET = 10.0   # 读共同变更历史(git log)的总耗时上限(秒)
# 修复类提交说明的关键词。英文按单词边界(不配 prefix、debug、fixture),中文没有词边界,只收不易误配的词
# (不收「回退」:常指 fallback)。大小写不敏感。
FIX_WORDS = re.compile(r'(?<![A-Za-z0-9_])(fix(e[sd]|ing|up)?|bug(s|fix(es)?)?|hotfix(es)?|revert(s|ed|ing)?|'
                       r'regressions?)(?![A-Za-z0-9_])|修复|修正|回滚|热修', re.I)
LOCK_FILE = re.compile(r'(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Cargo\.lock|'
                       r'Gemfile\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock|go\.sum)$|\.lock$')
# 共同变更:CHID(Empirical Software Engineering, 2025)把共同变更比例超过 50% 视为强关联。
COCHANGE_RATIO = 0.5
# 最少一起出现 2 次:只一起改过一次可能是巧合
COCHANGE_MIN_TOGETHER = 2
# 按提交规模加权后的共同变更量至少为 1,即至少相当于一次只改这两个文件的提交,见 cochange_stats
COCHANGE_MIN_WEIGHT = 1.0
CLUES_METHOD = {
    'callers_to_check': '签名变化或删除的公开符号在改动行之外的调用方(同上的文本匹配,ambiguous 是有同名定义,truncated 是没列全)',
    'fix_clues': '被删改的 base 行用 git blame -w -M -C 找引入它的提交,提交说明含修复类关键词的列出',
    'cochange': '从 base 往前的 git log,按提交规模加权(每对 1/(n-1)),比例超过 50%%、一起改至少 %d 次、'
                '加权量至少 %g 的未改文件' % (COCHANGE_MIN_TOGETHER, COCHANGE_MIN_WEIGHT),
}

COMMON = {w.lower() for w in """
get set run init main handle handler process update create delete remove list value values data name type index
render start stop load save test setup execute apply call build parse format tostring equals hashcode close open
read write size length config options default constructor valueof from item items result
error errors message status state props children request response context reset clear check validate convert
""".split()}

LANG = {'.go': 'go', '.ts': 'ts', '.tsx': 'ts', '.mts': 'ts', '.cts': 'ts', '.js': 'js', '.jsx': 'js', '.mjs': 'js',
        '.cjs': 'js', '.vue': 'js', '.py': 'python', '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin'}
TEXT_EXT = ('.md', '.markdown', '.rst', '.txt', '.adoc', '.html', '.htm', '.css', '.scss', '.less', '.svg', '.png',
            '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.pdf', '.csv', '.lock', '.sum', '.snap', '.map')
CONFIG_EXT = ('.yml', '.yaml', '.json', '.properties', '.toml', '.env', '.ini', '.conf')
DEP_FILES = re.compile(r'(^|/)(package\.json|go\.mod|go\.sum|pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|'
                       r'requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|'
                       r'package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Pipfile\.lock)$')
GENERATED_PATH = re.compile(r'\.pb\.go$|_pb2(_grpc)?\.py$|\.generated\.|_generated\.|\.min\.(js|css)$|(^|/)generated/')
GENERATED_HEAD = re.compile(r'@generated|code generated .* do not edit|do not edit|auto-?generated|this file (is|was) generated', re.I)
CFG_KEY = re.compile(r'^(\s*)(?:export\s+)?["\']?([A-Za-z0-9_.\-]+)["\']?\s*[:=]\s*(.*)$')


def is_test(p):
    # 和 collect-diff.sh 的 is_test 保持一致
    return bool(re.search(r'(^|/)(tests?|__tests__|e2e|testdata|test-data|__fixtures__|__mocks__|__snapshots__)/', p)
                or re.search(r'_test\.(go|py|rb|c|cc|cpp|rs|dart|exs?)$', p)
                or re.search(r'(^|/)(test_[^/]*|conftest)\.py$', p)
                or re.search(r'\.(test|spec)\.[cm]?[jt]sx?$', p)
                or re.search(r'(Test|Tests|TestCase|IT|Spec)\.(java|kt|kts|scala|groovy|cs|swift|php)$', p)
                or re.search(r'_spec\.rb$', p))


def lang_of(path):
    base = path.rsplit('/', 1)[-1]
    if base == '.env' or base.startswith('.env.') or os.path.splitext(base)[1].lower() in CONFIG_EXT:
        return 'config'
    return LANG.get(os.path.splitext(base)[1].lower())


def read_text(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


# ---------- diff 解析 ----------

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$')


def parse_patch(text):
    """返回文件列表;每个 hunk 的 lines 是 (标记, 文本, 旧行号, 新行号)。"""
    files, cur, hunk = [], None, None
    old_no = new_no = 0
    for line in text.split('\n'):
        if line.startswith('diff --git '):
            m = re.match(r'^diff --git a/(.*) b/(.*)$', line)
            cur = {'path': m.group(2) if m else line[11:], 'old_path': m.group(1) if m else None,
                   'status': 'modified', 'similarity': None, 'hunks': []}
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if hunk is None:
            if line.startswith('new file mode'):
                cur['status'] = 'added'
            elif line.startswith('deleted file mode'):
                cur['status'] = 'deleted'
            elif line.startswith('similarity index '):
                cur['similarity'] = int(re.sub(r'\D', '', line) or 0)
            elif line.startswith('rename from '):
                cur['old_path'], cur['status'] = line[12:], 'renamed'
            elif line.startswith('rename to '):
                cur['path'] = line[10:]
        m = HUNK.match(line)
        if m:
            hunk = {'old_start': int(m.group(1)), 'new_start': int(m.group(3)),
                    'new_len': int(m.group(4)) if m.group(4) is not None else 1, 'func': m.group(5).strip(), 'lines': []}
            old_no, new_no = hunk['old_start'], hunk['new_start']
            cur['hunks'].append(hunk)
            continue
        if hunk is None or line.startswith('\\'):
            continue
        tag = line[:1]
        if tag == '+':
            hunk['lines'].append(('+', line[1:], None, new_no))
            new_no += 1
        elif tag == '-':
            hunk['lines'].append(('-', line[1:], old_no, None))
            old_no += 1
        elif tag == ' ' or (line == '' and new_no < hunk['new_start'] + hunk['new_len']):
            hunk['lines'].append((' ', line[1:], old_no, new_no))
            old_no += 1
            new_no += 1
    return files


def hunk_range(h):
    return h['new_start'], h['new_start'] + max(h['new_len'], 1) - 1


# ---------- 定义识别 ----------

JAVA_KW = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'else', 'throw', 'synchronized', 'try', 'do', 'super', 'this'}
# (正则, 类别, 是否公开):公开写 True/False,或者 'case'(Go 首字母大写)、'py'(Python 下划线和 __all__)、
# 'mod'(Java/Kotlin 看修饰符,第 1 组是修饰符、第 2 组是名字)
DEFS = {
    'ts': [
        (r'^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]', 'function', True),
        (r'^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)', 'class', True),
        (r'^\s*export\s+(?:declare\s+)?(?:interface|enum)\s+([A-Za-z_$][\w$]*)', 'type', True),
        (r'^\s*export\s+(?:declare\s+)?type\s+([A-Za-z_$][\w$]*)\s*[<=]', 'type', True),
        (r'^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>', 'function', True),
        (r'^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)', 'const', True),
        (r'^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]', 'function', False),
        (r'^(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)', 'class', False),
        (r'^(?:interface|enum)\s+([A-Za-z_$][\w$]*)', 'type', False),
        (r'^type\s+([A-Za-z_$][\w$]*)\s*[<=]', 'type', False),
        (r'^\s+(?:(?:public|protected|static|async|readonly|override|abstract|get|set)\s+)*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^;]*\)\s*(?::\s*[^={]+)?\{\s*$', 'method', None),
        (r'^\s+private\s+(?:(?:static|async|readonly)\s+)*([A-Za-z_$][\w$]*)\s*\(', 'method', False),
    ],
    'go': [
        (r'^func\s+\([^)]*\)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(', 'method', 'case'),
        (r'^func\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(', 'function', 'case'),
        (r'^type\s+([A-Za-z_]\w*)\s+(?:\[[^\]]*\]\s*)?(?:struct|interface)\b', 'class', 'case'),
        (r'^type\s+([A-Za-z_]\w*)\b', 'type', 'case'),
        (r'^(?:const|var)\s+([A-Za-z_]\w*)\b', 'const', 'case'),
    ],
    'python': [
        (r'^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(', 'function', 'py'),
        (r'^class\s+([A-Za-z_]\w*)', 'class', 'py'),
        (r'^\s+(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(', 'method', 'py_method'),
    ],
    'java': [
        (r'^\s*((?:public|protected|private|internal)\s+)?(?:(?:static|final|abstract|sealed|data|open|inner|enum)\s+)*(?:class|interface|enum|record|object|@interface)\s+([A-Za-z_]\w*)', 'class', 'mod'),
        (r'^\s*((?:public|protected|private|internal)\s+)?(?:(?:override|open|suspend|inline|operator|abstract)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(', 'function', 'mod'),
        (r'^\s*((?:public|protected|private)\s+)(?:(?:static|final|abstract|synchronized|native|default)\s+)*(?:<[^>]*>\s*)?[\w<>\[\],.?@ ]+?\s+([A-Za-z_]\w*)\s*\(', 'method', 'mod'),
        (r'^\s+()(?:(?:static|final|abstract|synchronized)\s+)*(?:<[^>]*>\s*)?[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\([^;]*$', 'method', 'mod'),
    ],
}
DEFS['js'] = DEFS['ts']
DEFS['kotlin'] = DEFS['java']
DEFS = {k: [(re.compile(p), kind, exp) for p, kind, exp in v] for k, v in DEFS.items()}
FUNC_IDENT = re.compile(r'([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(')


def match_def(lang, text, py_all=None):
    """一行是不是定义:返回 (名字, 类别, 是否公开) 或 None;是否公开不确定时为 None。"""
    for rx, kind, exp in DEFS.get(lang, []):
        m = rx.match(text)
        if not m:
            continue
        if exp == 'mod':
            mod, name = (m.group(1) or '').strip(), m.group(2)
            if name in JAVA_KW or re.match(r'^\s*(return|throw|new|else)\b', text):
                continue
            if mod in ('public', 'protected'):
                exported = True
            elif mod in ('private', 'internal'):
                exported = False
            else:   # 没写修饰符:Kotlin 默认公开,Java 是包内可见
                exported = True if lang == 'kotlin' else (None if kind == 'class' else False)
            return name, kind, exported
        name = m.group(1)
        if kind == 'method' and name in JAVA_KW:
            continue
        if exp == 'case':
            exported = name[:1].isupper()
        elif exp == 'py':
            exported = (name in py_all) if py_all is not None else not name.startswith('_')
        elif exp == 'py_method':
            exported = False if name.startswith('_') else None
        else:
            exported = exp
        return name, kind, exported
    return None


def full_sig(lines, i):
    """从第 i 行起把跨行的签名拼起来(括号闭合为止,最多 6 行),空白归一。"""
    parts, depth, j = [lines[i]], lines[i].count('(') - lines[i].count(')'), i
    while depth > 0 and j + 1 < len(lines) and j - i < 6:
        j += 1
        parts.append(lines[j])
        depth += lines[j].count('(') - lines[j].count(')')
    s = re.sub(r'\s+', ' ', ' '.join(p.strip() for p in parts)).strip()
    s = re.sub(r'\s*\{\s*$', '', s)
    return re.sub(r'\s*:\s*$', '', s) if s.startswith(('def ', 'async def ', 'class ')) else s


def python_all(text):
    m = re.search(r'^__all__\s*=\s*[\[(]([^\])]*)[\])]', text or '', re.M | re.S)
    return set(re.findall(r'["\']([A-Za-z_]\w*)["\']', m.group(1))) if m else None


# ---------- 配置键 ----------

def config_key_at(text, lineno):
    """配置文件第 lineno 行的键;YAML 按缩进带上父键(order.cursor.enabled)。不是键值行返回 None。"""
    lines = (text or '').split('\n')
    if not 1 <= lineno <= len(lines):
        return None
    m = CFG_KEY.match(lines[lineno - 1])
    if not m or not m.group(3).strip() or lines[lineno - 1].lstrip().startswith(('#', '-')):
        return None   # 只有子键的父键不算一个配置项
    indent, parts = len(m.group(1)), [m.group(2)]
    for prev in reversed(lines[:lineno - 1]):
        p = CFG_KEY.match(prev)
        if indent == 0:
            break
        if p and len(p.group(1)) < indent and not p.group(3).strip():
            parts.insert(0, p.group(2))
            indent = len(p.group(1))
    return '.'.join(parts)


# ---------- 共同变更 ----------

def unquote_path(p):
    """git 输出里带特殊字符的路径是加了引号的 C 风格转义(\\346\\226…),还原成原样。"""
    p = p.strip()
    if len(p) < 2 or p[0] != '"' or p[-1] != '"':
        return p
    raw = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), p[1:-1])
    raw = raw.replace('\\"', '"').replace('\\t', '\t').replace('\\n', '\n').replace('\\\\', '\\')
    return raw.encode('latin-1', 'replace').decode('utf-8', 'replace')


def cochange_stats(commits, anchors):
    """commits:历史上每个提交改过的文件集合;anchors:本次改动的文件。返回强关联的
    (anchor, partner, 一起改的次数, anchor 被改的次数, 比例),比例从高到低。

    批量提交(格式化、重命名、依赖升级)一次改很多文件,按次数算会让其中任意两个文件都像强关联。
    这里不设文件数上限,而是按提交规模加权:每个提交给其中每个文件的共同变更证据合计为 1,
    平均分给其余 n-1 个文件,即每对文件得 1/(n-1)(只改一个文件的提交按 1 计入 anchor 的分母)。
    只改两个文件的提交给这一对完整的 1,改了 200 个文件的提交只给每对 1/199。
      比例 = 一起改的提交的权重之和 / anchor 所在提交的权重之和(anchor 被改时 partner 也被改的加权比例)
    强关联要同时满足:比例 > COCHANGE_RATIO(CHID 的 50%);一起改的次数 >= COCHANGE_MIN_TOGETHER(排除一次巧合);
    加权共同变更量 >= COCHANGE_MIN_WEIGHT(至少相当于一次只改这两个文件的提交,只靠批量提交凑不够)。"""
    total, changes, pair_n, pair_w = {}, {}, {}, {}
    for files in commits:
        w = 1.0 / max(len(files) - 1, 1)
        for a in anchors & files:
            total[a] = total.get(a, 0.0) + w
            changes[a] = changes.get(a, 0) + 1
            for b in files - {a}:
                pair_n[(a, b)] = pair_n.get((a, b), 0) + 1
                pair_w[(a, b)] = pair_w.get((a, b), 0.0) + w
    out = []
    for (a, b), n in pair_n.items():
        ratio = pair_w[(a, b)] / total[a]
        if n >= COCHANGE_MIN_TOGETHER and pair_w[(a, b)] >= COCHANGE_MIN_WEIGHT - 1e-9 and ratio > COCHANGE_RATIO:
            out.append((a, b, n, changes[a], round(ratio, 2)))
    return sorted(out, key=lambda x: (-x[4], -x[2], x[0], x[1]))


# ---------- 读代码 ----------

class Repo:
    """按 meta.txt 读被审查的一版(code_ref,WORKTREE 时读工作区)和对比基准一版的文件。"""

    def __init__(self, root, code_ref, base_sha):
        self.root, self.code_ref, self.base_sha = root, code_ref, base_sha
        self.cache = {}

    def git(self, *args, timeout=20):
        return subprocess.run(['git', '-C', self.root] + list(args), capture_output=True, timeout=timeout)

    def show(self, ref, path):
        if (ref, path) not in self.cache:
            text = None
            if ref == 'WORKTREE':
                text = read_text(os.path.join(self.root, path)) or None
            elif ref:
                r = self.git('show', '%s:%s' % (ref, path))
                text = r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None
            self.cache[(ref, path)] = text
        return self.cache[(ref, path)]

    def new(self, path):
        return self.show(self.code_ref, path) if self.code_ref and self.code_ref != 'UNAVAILABLE' else None

    def old(self, path):
        return self.show(self.base_sha, path) if self.base_sha else None


# ---------- 主流程 ----------

class Facts:
    def __init__(self, rd):
        self.limits = []
        meta = crlib.read_kv(os.path.join(rd, 'meta.txt'))
        self.repo = Repo(meta.get('repo_root') or '.', meta.get('code_ref', ''), meta.get('base_sha', ''))
        self.diff_name = 'diff.full.patch' if os.path.exists(os.path.join(rd, 'diff.full.patch')) else 'diff.patch'
        self.files = parse_patch(read_text(os.path.join(rd, self.diff_name)))
        self.ranges = {}
        for f in self.files:
            self.ranges.setdefault(f['path'], []).extend(hunk_range(h) for h in f['hunks'])

    def in_diff(self, path, line):
        return any(lo <= line <= hi for lo, hi in self.ranges.get(path, []))

    def old_text(self, f):
        return self.repo.old(f.get('old_path') or f['path']) if f['status'] != 'added' else ''

    # ----- hunks -----
    def build_hunks(self):
        hunks = []
        for f in self.files:
            f['kind'] = self.file_kind(f['path'])
            for h in f['hunks']:
                h['id'] = 'H%d' % (len(hunks) + 1)
                lo, hi = hunk_range(h)
                hunks.append({'id': h['id'], 'file': f['path'], 'start': lo, 'end': hi,
                              'add': sum(1 for t, *_ in h['lines'] if t == '+'),
                              'del': sum(1 for t, *_ in h['lines'] if t == '-'),
                              'func': h['func'], 'kind': f['kind'], 'symbols': []})
        return hunks

    def file_kind(self, path):
        if is_test(path):
            return 'test'
        if self.is_generated(path):
            return 'generated'
        if lang_of(path) == 'config' or DEP_FILES.search(path):
            return 'config'
        return 'other'

    def is_generated(self, path):
        if GENERATED_PATH.search(path):
            return True
        head = '\n'.join((self.repo.new(path) or '').split('\n')[:5])
        return bool(GENERATED_HEAD.search(head))

    # ----- symbols -----
    def code_symbols(self, f, lang):
        new_all = python_all(self.repo.new(f['path'])) if lang == 'python' else None
        old_all = python_all(self.old_text(f)) if lang == 'python' else None
        sides = {'-': {}, '+': {}}   # 名字 -> {kind, exported, sigs, line, hunk}:改动行上的定义
        body = {}                    # 名字 -> (kind, exported, line, hunk):定义没变、函数体变了
        for h in f['hunks']:
            lines, cur = h['lines'], None
            if h['func']:
                d = match_def(lang, h['func'], new_all)
                m = FUNC_IDENT.search(h['func'])
                cur = (d[0], d[1], d[2], None) if d else \
                    ((m.group(1), 'unknown', None, None) if m and m.group(1) not in JAVA_KW else None)
            for i, (tag, text, old_no, new_no) in enumerate(lines):
                d = match_def(lang, text, old_all if tag == '-' else new_all)
                if d and tag == ' ':
                    cur = (d[0], d[1], d[2], new_no)
                elif d:
                    side = [k for k, x in enumerate(lines) if x[0] in (' ', tag)]
                    sig = full_sig([lines[k][1] for k in side], side.index(i))
                    rec = sides[tag].setdefault(d[0], {'kind': d[1], 'exported': d[2], 'sigs': [], 'line': None, 'hunk': h['id']})
                    rec['sigs'].append(sig)
                    rec['line'] = rec['line'] or (new_no if tag == '+' else old_no)
                    rec['exported'] = rec['exported'] or d[2]
                    cur = (d[0], d[1], d[2], new_no if tag == '+' else None)
                elif tag in '+-' and cur:
                    body.setdefault(cur[0], cur[1:] + (h['id'],))
        out = []
        for name in dict.fromkeys(list(sides['+']) + list(sides['-'])):
            p, m = sides['+'].get(name), sides['-'].get(name)
            change = 'added' if not m else 'removed' if not p else \
                'signature_changed' if sorted(set(p['sigs'])) != sorted(set(m['sigs'])) else 'body_only'
            rec = p or m
            exported = (p or {}).get('exported')
            out.append(self.symbol(name, f['path'], rec['line'], lang, rec['kind'], change,
                                   m['sigs'][0] if m else None, p['sigs'][0] if p else None,
                                   exported if exported is not None else (m or {}).get('exported'), rec['hunk']))
        for name, (kind, exported, line, hid) in body.items():
            if name not in sides['+'] and name not in sides['-']:
                out.append(self.symbol(name, f['path'], line, lang, kind, 'body_only', None, None, exported, hid))
        return out

    def config_symbols(self, f):
        """配置文件改动行里新增、删除、改了值的键。新增或删除整个配置文件时不列。"""
        if DEP_FILES.search(f['path']) or f['status'] in ('added', 'deleted'):
            return []
        new_text, old_text = self.repo.new(f['path']), self.old_text(f)
        plus, minus = {}, {}
        for h in f['hunks']:
            for tag, _, old_no, new_no in h['lines']:
                if tag == '+' and (k := config_key_at(new_text, new_no)):
                    plus.setdefault(k, (new_no, h['id']))
                elif tag == '-' and (k := config_key_at(old_text, old_no)):
                    minus.setdefault(k, (old_no, h['id']))
        out = [self.symbol(k, f['path'], line, 'config', 'config_key', 'body_only' if k in minus else 'added', exported=True, hunk=hid)
               for k, (line, hid) in plus.items()]
        return out + [self.symbol(k, f['path'], line, 'config', 'config_key', 'removed', exported=True, hunk=hid)
                      for k, (line, hid) in minus.items() if k not in plus]

    @staticmethod
    def symbol(name, path, line, lang, kind, change, old_sig=None, new_sig=None, exported=None, hunk=None):
        return {'name': name, 'file': path, 'line': line, 'lang': lang, 'kind': kind, 'change': change,
                'old_sig': old_sig, 'new_sig': new_sig, 'exported': exported, 'hunk': hunk,
                'callers': [], 'callers_total': 0, 'callers_truncated': False, 'ambiguous': False, 'skipped': None}

    # ----- callers -----
    def grep(self, names, deadline):
        """一次 git grep 查所有名字;返回 {name: [(path, line, text)]}。超时抛 TimeoutError。"""
        ref = self.repo.code_ref
        args = ['grep', '-n', '-I', '-w', '-F'] + [x for n in names for x in ('-e', n)]
        args += [ref] if ref not in ('WORKTREE', 'UNAVAILABLE', '') else (['--untracked'] if ref == 'WORKTREE' else [])
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError
        try:
            out = self.repo.git(*args, timeout=left).stdout.decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            raise TimeoutError
        prefix = ref + ':' if ref not in ('WORKTREE', 'UNAVAILABLE', '') else ''
        pats = {n: re.compile(r'(?<![\w$])' + re.escape(n) + r'(?![\w$])') for n in names}
        res = {n: [] for n in names}
        for raw in out.splitlines():
            parts = raw[len(prefix):].split(':', 2) if raw.startswith(prefix) else raw.split(':', 2)
            if len(parts) == 3 and parts[1].isdigit():
                for n, p in pats.items():
                    if p.search(parts[2]):
                        res[n].append((parts[0], int(parts[1]), parts[2]))
        return res

    def searchable(self, s):
        """要不要按名字查;名字太短或太常见就标 common_name。"""
        name = s['name']
        if s['kind'] == 'config_key':
            short = name.rsplit('.', 1)[-1]
            ok = len(short) >= 3 and ('.' in name or len(name) >= 8)
        else:
            ok = len(name) >= 4 and name.lower() not in COMMON
        if not ok:
            s['skipped'] = 'common_name'
        return ok

    def add_callers(self, symbols):
        order = {'signature_changed': 0, 'removed': 1, 'added': 2}
        public = [s for s in symbols if s['exported'] is not False and self.searchable(s)]
        candidates = sorted((s for s in public if s['change'] in order), key=lambda s: order[s['change']])
        if len(candidates) > MAX_SYMBOLS:
            self.limits.append('改动的公开符号有 %d 个,只查了前 %d 个的调用方' % (len(candidates), MAX_SYMBOLS))
            candidates = candidates[:MAX_SYMBOLS]
        try:
            hits = self.grep(sorted({s['name'] for s in candidates}), time.time() + GREP_BUDGET)
        except TimeoutError:
            self.limits.append('查调用方超过 %d 秒,已停止;调用方没有列出' % GREP_BUDGET)
            return
        except OSError as e:
            self.limits.append('git grep 失败:%s' % e)
            return
        for s in candidates:
            defs, callers = set(), []
            for path, line, text in hits.get(s['name'], []):
                if path == s['file'] and line == s['line']:
                    continue
                d = match_def(s['lang'], text) if s['lang'] != 'config' else None
                if d and d[0] == s['name'] and lang_of(path) == s['lang']:
                    defs.add(path)
                elif not is_test(path):
                    callers.append((path, line))
            s['ambiguous'] = bool(defs - {s['file']}) and s['change'] != 'removed'
            s['callers_total'] = len(callers)
            if len(callers) > MAX_HITS:
                s['skipped'] = 'too_many_hits'
                continue
            s['callers'] = [{'file': p, 'line': ln, 'in_diff': self.in_diff(p, ln)} for p, ln in callers[:MAX_LISTED]]
            s['callers_truncated'] = len(callers) > MAX_LISTED

    # ----- review 用的线索:只给事实,结论由 reviewer / verifier 下 -----
    def new_side_line(self, hid, old_no):
        """删掉的定义在新侧的落点:删除处之后(没有就之前)最近的新侧行,落在该 hunk 的新侧范围里;没有新侧行时为 None。"""
        for f in self.files:
            for h in f['hunks']:
                if h.get('id') != hid or h['new_len'] == 0:
                    continue
                lines, before = h['lines'], None
                for i, (tag, _, o, n) in enumerate(lines):
                    if tag == '-' and o == old_no:
                        after = next((x[3] for x in lines[i:] if x[3] is not None), None)
                        return after if after is not None else before
                    if n is not None:
                        before = n
                return hunk_range(h)[0]
        return None

    def callers_to_check(self, symbols, reviewed):
        """签名变化或删除、不确定是私有的符号,在改动行之外还有调用方的:逐个确认是否仍兼容。
        line 是被改的定义在新侧的行(删除时取删除处所在 hunk 的新侧行),发现定位到这里。"""
        out = []
        for s in symbols:
            if s['change'] not in ('signature_changed', 'removed') or s['exported'] is False:
                continue
            if reviewed is not None and s['file'] not in reviewed:
                continue
            outside = [{'file': c['file'], 'line': c['line']} for c in s['callers'] if not c['in_diff']]
            truncated = bool(s['callers_truncated'] or s['skipped'] == 'too_many_hits')
            if not outside and not truncated:
                continue
            line = s['line'] if s['change'] == 'signature_changed' else self.new_side_line(s['hunk'], s['line'])
            out.append({'name': s['name'], 'file': s['file'], 'line': line, 'change': s['change'],
                        'old_sig': s['old_sig'], 'new_sig': s['new_sig'], 'callers': outside,
                        'callers_total': s['callers_total'], 'truncated': truncated, 'ambiguous': s['ambiguous']})
        return out

    def fix_clues(self, reviewed, deadline):
        """被删除或修改的 base 行,由哪个提交引入;提交说明像修复(FIX_WORDS)的列出来。
        每个文件一次 git blame(所有 hunk 的行范围用多个 -L 一起传),再一次 git log 取这些提交的说明。"""
        todo = []
        for f in self.files:
            if f['status'] == 'added' or (reviewed is not None and f['path'] not in reviewed):
                continue
            spans = []
            for h in f['hunks']:
                for tag, _, o, _ in h['lines']:
                    if tag != '-':
                        continue
                    if spans and spans[-1][2] == h['id'] and spans[-1][1] == o - 1:
                        spans[-1][1] = o
                    else:
                        spans.append([o, o, h['id']])
            if spans:
                todo.append((f.get('old_path') or f['path'], f['path'], spans))
        blamed, done = [], 0     # (path, hid, base 行号, sha)
        for old_path, path, spans in todo:
            left = deadline - time.time()
            if left <= 0:
                break
            args = ['blame', '-w', '-M', '-C', '--porcelain'] + \
                [x for lo, hi, _ in spans for x in ('-L', '%d,%d' % (lo, hi))] + [self.repo.base_sha, '--', old_path]
            try:
                r = self.repo.git(*args, timeout=left)
            except subprocess.TimeoutExpired:
                break
            done += 1
            if r.returncode != 0:
                continue
            for line in r.stdout.decode('utf-8', 'replace').split('\n'):
                m = re.match(r'^([0-9a-f]{40}) \d+ (\d+)', line)
                if m:
                    n = int(m.group(2))
                    hid = next((s[2] for s in spans if s[0] <= n <= s[1]), None)
                    blamed.append((path, hid, n, m.group(1)))
        if done < len(todo):
            self.limits.append('查最近修复的线索超过 %d 秒,已停止;%d 个文件只查了 %d 个' % (BLAME_BUDGET, len(todo), done))
        shas = sorted({b[3] for b in blamed})
        if not shas:
            return []
        left = deadline - time.time()
        try:
            if left <= 0:
                raise subprocess.TimeoutExpired('git', 0)
            r = self.repo.git('log', '--no-walk=unsorted', '--format=%H%x00%s%x00%B%x1e', *shas, timeout=left)
        except subprocess.TimeoutExpired:
            self.limits.append('查最近修复的线索超过 %d 秒,已停止;提交说明没有读完,修复线索没有列出' % BLAME_BUDGET)
            return []
        fixes = {}
        for rec in r.stdout.decode('utf-8', 'replace').split('\x1e'):
            parts = rec.strip('\n').split('\x00')
            if len(parts) == 3 and FIX_WORDS.search(parts[1] + '\n' + parts[2]):
                fixes[parts[0]] = parts[1].strip()
        out = []
        for path, hid, n, sha in sorted((b for b in blamed if b[3] in fixes), key=lambda b: (b[0], b[3], b[2])):
            last = out[-1] if out else None
            if last and (last['file'], last['commit'], last['hunk']) == (path, sha[:10], hid) and last['_end'] == n - 1:
                last['_end'] = n
            else:
                out.append({'file': path, 'hunk': hid, '_start': n, '_end': n, 'commit': sha[:10], 'title': fixes[sha]})
        for c in out:
            a, b = c.pop('_start'), c.pop('_end')
            c['base_lines'] = str(a) if a == b else '%d-%d' % (a, b)
        return sorted(out, key=lambda c: (c['file'], int(c['base_lines'].split('-')[0])))

    def cochange(self, reviewed, changed, skip, deadline):
        """历史上常和本次改动的文件一起改、这次没改的文件。只读一次 git log(从 base 往前,只取碰过这些文件的提交),
        按 cochange_stats 判强关联。超时用已经读到的最近那部分提交,并写进 limits。"""
        anchors = {}   # 历史里的路径 -> 本次的路径(改名时历史在旧名下)
        for f in self.files:
            if reviewed is not None and f['path'] not in reviewed:
                continue
            if f.get('kind') in ('test', 'generated') or LOCK_FILE.search(f['path']):
                continue
            anchors[f.get('old_path') or f['path']] = f['path']
        if not anchors:
            return []
        args = ['log', '--no-merges', '--no-renames', '--full-diff', '--name-only', '--format=%x1e%H',
                self.repo.base_sha, '--'] + [':(literal)' + p for p in sorted(anchors)]
        left, partial = deadline - time.time(), False
        try:
            if left <= 0:
                raise subprocess.TimeoutExpired('git', 0)
            out = self.repo.git(*args, timeout=left).stdout
        except subprocess.TimeoutExpired as e:
            out, partial = e.stdout or b'', True
        chunks = out.decode('utf-8', 'replace').split('\x1e')[1:]
        if partial and chunks:
            chunks = chunks[:-1]     # 最后一个提交可能没读完
        # 测试、lock、生成文件(按路径认)不参与统计,也不算进提交规模:改代码顺带改测试和 lock 不该稀释代码文件之间的关联
        commits = [{p for p in (unquote_path(x) for x in c.split('\n')[1:] if x.strip())
                    if not (is_test(p) or LOCK_FILE.search(p) or GENERATED_PATH.search(p))} for c in chunks]
        if partial:
            self.limits.append('读共同变更历史超过 %d 秒,已停止;只统计了最近 %d 个相关提交' % (COCHANGE_BUDGET, len(commits)))
        res = []
        for a, b, together, changes, ratio in cochange_stats(commits, set(anchors)):
            if b in changed or crlib.any_match(skip, b):
                continue
            if self.repo.new(b) is None or self.is_generated(b):
                continue     # 被审查的版本里已经没有这个文件,或者是生成的
            res.append({'file': b, 'with': anchors[a], 'together': together, 'changes': changes, 'ratio': ratio})
        return res

    def review_clues(self, data, rd):
        files_txt = os.path.join(rd, 'files.txt')
        reviewed = set(read_text(files_txt).split('\n')) - {''} if os.path.exists(files_txt) else None
        data['callers_to_check'] = self.callers_to_check(data['symbols'], reviewed)
        data['fix_clues'], data['cochange'] = [], []
        if not self.repo.base_sha:
            self.limits.append('没有对比基准的提交,没有查修复线索和共同变更')
            return
        data['fix_clues'] = self.fix_clues(reviewed, time.time() + BLAME_BUDGET)
        changed = {p for f in self.files for p in (f['path'], f.get('old_path')) if p}
        changed |= {l.split('\t', 1)[0] for l in read_text(os.path.join(rd, 'skipped.txt')).split('\n') if l.strip()}
        skip = crlib.load_json(os.path.join(rd, 'settings.json'), {}).get('skip') or []
        data['cochange'] = self.cochange(reviewed, changed, skip, time.time() + COCHANGE_BUDGET)

    def run(self):
        hunks = self.build_hunks()
        symbols, unknown = [], []
        for f in self.files:
            lang = lang_of(f['path'])
            if lang is None:
                if f['hunks'] and not is_test(f['path']) and not f['path'].lower().endswith(TEXT_EXT) \
                        and not DEP_FILES.search(f['path']):
                    unknown.append(f['path'])
                continue
            try:
                symbols += self.config_symbols(f) if lang == 'config' else self.code_symbols(f, lang)
            except Exception as e:  # noqa: BLE001 一个文件出错不影响其他文件
                self.limits.append('%s:识别定义出错(%s)' % (f['path'], type(e).__name__))
        if unknown:
            self.limits.append('%d 个文件不是能识别定义的语言,只给到文件和 hunk:%s' % (
                len(unknown), '、'.join(unknown[:5]) + (' 等' if len(unknown) > 5 else '')))
        by_id = {h['id']: h for h in hunks}
        for s in symbols:
            h = by_id.get(s['hunk'])
            if h is not None and s['name'] not in h['symbols']:
                h['symbols'].append(s['name'])
        if self.repo.code_ref and self.repo.code_ref != 'UNAVAILABLE':
            self.add_callers(symbols)
        else:
            self.limits.append('读不到被审查的代码,没有查调用方')
        return {
            'version': 1,
            'scope': {'diff': self.diff_name, 'files': len(self.files),
                      'added': sum(h['add'] for h in hunks), 'deleted': sum(h['del'] for h in hunks)},
            'hunks': hunks,
            'symbols': symbols,
            'method': METHOD,
            'limits': self.limits,
        }


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        sys.stderr.write('用法: analysis_facts.py <review_dir>\n')
        return 2
    rd = argv[1]
    try:
        facts = Facts(rd)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write('analysis_facts: 无法读取审查目录(%s)\n' % e)
        return 1
    try:
        data = facts.run()
    except Exception as e:  # noqa: BLE001 尽量写出已有的部分
        facts.limits.append('整理事实时出错(%s: %s),结果不完整' % (type(e).__name__, e))
        data = {'version': 1, 'scope': {'diff': facts.diff_name, 'files': len(facts.files), 'added': 0, 'deleted': 0},
                'hunks': [], 'symbols': [],
                'method': METHOD, 'limits': facts.limits}
    try:
        facts.review_clues(data, rd)
    except Exception as e:  # noqa: BLE001 线索出错不影响已有的事实
        facts.limits.append('整理线索时出错(%s: %s),待核对调用方、修复线索、共同变更可能不全' % (type(e).__name__, e))
    for k in ('callers_to_check', 'fix_clues', 'cochange'):
        data.setdefault(k, [])
    data['clues_method'] = CLUES_METHOD
    with open(os.path.join(rd, 'analysis-facts.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('analysis_facts: hunks=%d symbols=%d callers_to_check=%d fix_clues=%d cochange=%d' % (
        len(data['hunks']), len(data['symbols']), len(data['callers_to_check']), len(data['fix_clues']), len(data['cochange'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
