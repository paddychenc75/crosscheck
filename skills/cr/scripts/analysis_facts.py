#!/usr/bin/env python3
"""analysis_facts.py — 为 MR 说明整理改动的事实,不调用模型、不下结论。

用法: python3 analysis_facts.py <review_dir>

读 review_dir 里的 diff.full.patch(没有就用 diff.patch)、skipped.patch、skipped.txt、meta.txt,
写 <review_dir>/analysis-facts.json:
  hunks       每个 hunk 的编号(H1..)、位置、所在函数、粗分类
  symbols     改动涉及的定义(函数、类、类型、配置键):增删、签名变化还是只改实现;
              签名变化 / 新增 / 删除的公开符号,在本仓库按名字查调用方和提到它的测试文件
  attention   需注意的改动:迁移、配置键增删、新环境变量、依赖、签名变化
  skippable   可以跳过的文件:生成、只改空白、纯重命名、lock 文件
  effort      审查量 1–5 分及依据
定义识别覆盖 TS/JS、Go、Python、Java/Kotlin;其他语言退回 git hunk 头里的函数名,再不行只给到文件。
调用方是按名字的文本匹配(git grep -w),同名、反射、依赖注入、跨仓库调用都会多报或漏报,
所以只当线索用。出错时尽量写出已有的部分,并在 limits 里注明;只有完全失败才以非零退出。
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
LOCK_FILES = re.compile(r'(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|'
                        r'Cargo\.lock|Gemfile\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock|go\.sum)$|\.lock$')
MIGRATION = re.compile(r'(^|/)(migrations?|migrate|db/migrate|alembic/versions|flyway|liquibase|changelogs?)/|'
                       r'(^|/)V\d+[_.\d]*__[^/]*\.sql$|\.sql$', re.I)
GENERATED_PATH = re.compile(r'\.pb\.go$|_pb2(_grpc)?\.py$|\.generated\.|_generated\.|\.min\.(js|css)$|(^|/)generated/')
GENERATED_HEAD = re.compile(r'@generated|code generated .* do not edit|do not edit|auto-?generated|this file (is|was) generated', re.I)
ENV_USES = [re.compile(p) for p in (
    r'process\.env\.([A-Z][A-Z0-9_]+)', r'process\.env\[\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'import\.meta\.env\.([A-Z][A-Z0-9_]+)', r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'os\.environ(?:\.get)?[\[(]\s*["\']([A-Z][A-Z0-9_]+)["\']', r'System\.getenv\(\s*"([A-Z][A-Z0-9_]+)"',
    r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]+)"')]
CFG_ENV = re.compile(r'\$\{([A-Z][A-Z0-9_]+)(?::[^}]*)?\}')
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


def count_config_keys(text):
    return sum(1 for line in (text or '').split('\n')
               if (m := CFG_KEY.match(line)) and m.group(3).strip() and not line.lstrip().startswith(('#', '-')))


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
        # 测试文件被 collect-diff 移进了 skipped.patch,MR 说明里也要列出来
        self.files = parse_patch(read_text(os.path.join(rd, self.diff_name))) + \
            parse_patch(read_text(os.path.join(rd, 'skipped.patch')))
        self.skipped = [tuple(l.split('\t', 1)) for l in read_text(os.path.join(rd, 'skipped.txt')).splitlines() if '\t' in l]
        self.changed_paths = {f['path'] for f in self.files} | {p for p, _ in self.skipped}
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
        """配置文件改动行里新增、删除、改了值的键。新增或删除整个配置文件时在 attention 里算一项。"""
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
                'callers': [], 'callers_total': 0, 'callers_truncated': False, 'ambiguous': False, 'skipped': None, 'tests': []}

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
        body_only = [s for s in public if s['change'] == 'body_only' and s['kind'] != 'config_key'][:MAX_SYMBOLS]
        if len(candidates) > MAX_SYMBOLS:
            self.limits.append('改动的公开符号有 %d 个,只查了前 %d 个的调用方' % (len(candidates), MAX_SYMBOLS))
            candidates = candidates[:MAX_SYMBOLS]
        try:
            hits = self.grep(sorted({s['name'] for s in candidates + body_only}), time.time() + GREP_BUDGET)
        except TimeoutError:
            self.limits.append('查调用方超过 %d 秒,已停止;调用方和相关测试没有列出' % GREP_BUDGET)
            return
        except OSError as e:
            self.limits.append('git grep 失败:%s' % e)
            return
        for s in candidates + body_only:
            defs, callers, tests = set(), [], {}
            for path, line, text in hits.get(s['name'], []):
                if path == s['file'] and line == s['line']:
                    continue
                d = match_def(s['lang'], text) if s['lang'] != 'config' else None
                if d and d[0] == s['name'] and lang_of(path) == s['lang']:
                    defs.add(path)
                elif is_test(path):
                    tests.setdefault(path, path in self.changed_paths)
                else:
                    callers.append((path, line))
            s['tests'] = [{'file': p, 'changed': c} for p, c in sorted(tests.items())]
            if s in body_only:
                continue   # 只改实现的只列相关测试,不列调用方
            s['ambiguous'] = bool(defs - {s['file']}) and s['change'] != 'removed'
            s['callers_total'] = len(callers)
            if len(callers) > MAX_HITS:
                s['skipped'] = 'too_many_hits'
                continue
            s['callers'] = [{'file': p, 'line': ln, 'in_diff': self.in_diff(p, ln)} for p, ln in callers[:MAX_LISTED]]
            s['callers_truncated'] = len(callers) > MAX_LISTED

    # ----- attention -----
    def attention(self, symbols):
        out = []
        for f in self.files:
            p = f['path']
            if lang_of(p) == 'config' and f['status'] in ('added', 'deleted') and not DEP_FILES.search(p):
                n = count_config_keys(self.repo.new(p) if f['status'] == 'added' else self.old_text(f))
                out.append({'type': 'config_key', 'file': p, 'line': None, 'detail': '%s配置文件 %s%s' % (
                    '新增' if f['status'] == 'added' else '删除', p, '(%d 个键)' % n if n else '')})
            if MIGRATION.search(p) and not is_test(p):
                verb = {'added': '新增迁移', 'deleted': '删除迁移'}.get(f['status'], '修改迁移')
                out.append({'type': 'migration', 'file': p, 'line': None, 'detail': '%s %s' % (verb, p)})
            if DEP_FILES.search(p):
                out.append({'type': 'dependency', 'file': p, 'line': None, 'detail': self.dep_detail(f)})
            out += self.new_env_vars(f)
        listed = {a['file'] for a in out if a['type'] == 'dependency'}
        out += [{'type': 'dependency', 'file': p, 'line': None, 'detail': 'lock 文件变化 %s' % p}
                for p, r in self.skipped if r == 'lockfile' and p not in listed]
        for s in symbols:
            if s['kind'] == 'config_key' and s['change'] in ('added', 'removed'):
                out.append({'type': 'config_key', 'file': s['file'], 'line': s['line'],
                            'detail': '%s配置键 %s' % ('新增' if s['change'] == 'added' else '删除', s['name'])})
            elif s['kind'] != 'config_key' and s['exported'] is not False and \
                    s['change'] in ('signature_changed', 'removed') and not is_test(s['file']):
                detail = '`%s` 签名变化:%s → %s' % (s['name'], s['old_sig'], s['new_sig']) \
                    if s['change'] == 'signature_changed' else '删除 `%s`' % s['name']
                if s['callers_total']:
                    detail += '(本仓库另有 %d 处引用)' % s['callers_total']
                out.append({'type': 'signature', 'file': s['file'], 'line': s['line'], 'detail': detail})
        return out

    def new_env_vars(self, f):
        """改动行里新用到的环境变量;改动前同一文件里已经用过的不算。"""
        plus, minus, cfg = {}, set(), lang_of(f['path']) == 'config'
        for h in f['hunks']:
            for tag, text, _, new_no in h['lines']:
                found = [m.group(1) for rx in ENV_USES for m in rx.finditer(text)] + (CFG_ENV.findall(text) if cfg else [])
                for v in found:
                    if tag == '+':
                        plus.setdefault(v, new_no)
                    elif tag == '-':
                        minus.add(v)
        old = self.old_text(f) or ''
        return [{'type': 'env_var', 'file': f['path'], 'line': line, 'detail': '新环境变量 %s' % v}
                for v, line in plus.items() if v not in minus and v not in old]

    def dep_detail(self, f):
        p = f['path']
        if p.rsplit('/', 1)[-1] == 'package.json':
            try:
                old = json.loads(self.old_text(f) or '{}')
                new = json.loads(self.repo.new(p) or '{}') if f['status'] != 'deleted' else {}
            except ValueError:
                old = new = None
            parts = []
            for sec in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies') if old is not None else ():
                o, n = old.get(sec) or {}, new.get(sec) or {}
                for k in sorted(set(o) | set(n)):
                    if k not in o:
                        parts.append('新增 %s@%s' % (k, n[k]))
                    elif k not in n:
                        parts.append('删除 %s' % k)
                    elif o[k] != n[k]:
                        parts.append('%s %s → %s' % (k, o[k], n[k]))
            if parts:
                return '依赖变化(%s):%s' % (p, ';'.join(parts[:8]) + (' 等 %d 项' % len(parts) if len(parts) > 8 else ''))
        adds = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '+')
        dels = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '-')
        return '依赖文件变化 %s(+%d/−%d 行)' % (p, adds, dels)

    # ----- skippable / effort -----
    def skippable(self):
        out, seen = [], set()
        for f in self.files:
            p = f['path']
            reason = 'lockfile' if LOCK_FILES.search(p) else \
                'generated' if f.get('kind') == 'generated' else \
                'rename_only' if f['status'] == 'renamed' and f['similarity'] == 100 and not f['hunks'] else \
                'whitespace_only' if self.whitespace_only(f) else None
            if reason:
                out.append({'file': p, 'reason': reason})
                seen.add(p)
        return out + [{'file': p, 'reason': r} for p, r in self.skipped if r in ('lockfile', 'generated') and p not in seen]

    @staticmethod
    def whitespace_only(f):
        plus = sorted(s for h in f['hunks'] for t, x, *_ in h['lines'] if t == '+' and (s := re.sub(r'\s+', '', x)))
        minus = sorted(s for h in f['hunks'] for t, x, *_ in h['lines'] if t == '-' and (s := re.sub(r'\s+', '', x)))
        changed = any(t in '+-' for h in f['hunks'] for t, *_ in h['lines'])
        return changed and plus == minus

    def effort(self, skippable, symbols):
        skip = {s['file'] for s in skippable}
        files = [f for f in self.files if f['path'] not in skip]
        adds = sum(1 for f in files for h in f['hunks'] for t, *_ in h['lines'] if t == '+')
        dels = sum(1 for f in files for h in f['hunks'] for t, *_ in h['lines'] if t == '-')
        dirs = {f['path'].rsplit('/', 1)[0] if '/' in f['path'] else '.' for f in files}
        sigs = sum(1 for s in symbols if s['change'] in ('signature_changed', 'removed')
                   and s['exported'] is not False and s['kind'] != 'config_key')
        n = adds + dels
        score = 1 if n <= 50 else 2 if n <= 200 else 3 if n <= 500 else 4 if n <= 1000 else 5
        score = max(1, min(5, score + (len(files) > 15 or len(dirs) > 4) + (sigs >= 5)))
        basis = '%d 文件 +%d/−%d,%d 个目录,%d 处签名变化或删除' % (len(files), adds, dels, len(dirs), sigs)
        return {'score': score, 'basis': basis + ('(不含可跳过的 %d 个文件)' % len(skip) if skip else '')}

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
        if any(r in ('lockfile', 'generated', 'binary') for _, r in self.skipped):
            self.limits.append('lock、生成和二进制文件的行数没有计入')
        skippable = self.skippable()
        return {
            'version': 1,
            'scope': {'diff': self.diff_name, 'files': len(self.changed_paths),
                      'added': sum(h['add'] for h in hunks), 'deleted': sum(h['del'] for h in hunks)},
            'hunks': hunks,
            'symbols': symbols,
            'attention': self.attention(symbols),
            'skippable': skippable,
            'effort': self.effort(skippable, symbols),
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
        data = {'version': 1, 'scope': {'diff': facts.diff_name, 'files': len(facts.changed_paths), 'added': 0, 'deleted': 0},
                'hunks': [], 'symbols': [], 'attention': [], 'skippable': [], 'effort': {'score': 1, 'basis': '未能计算'},
                'method': METHOD, 'limits': facts.limits}
    with open(os.path.join(rd, 'analysis-facts.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('analysis_facts: hunks=%d symbols=%d attention=%d' % (len(data['hunks']), len(data['symbols']), len(data['attention'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
