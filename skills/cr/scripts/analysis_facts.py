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
调用方是按名字的文本匹配(git grep -w),同名、反射、依赖注入、跨仓库调用都会多报或漏报,
所以只当线索用。出错时尽量写出已有的部分,并在 limits 里注明;只有完全失败才以非零退出。
"""
import json
import os
import re
import subprocess
import sys
import time

GREP_BUDGET = 10.0       # 查调用方的总耗时上限(秒)
MAX_SYMBOLS = 30         # 最多查多少个符号的调用方
MAX_HITS = 50            # 单个符号命中超过这个数只给数量
MAX_LISTED = 20          # 每个符号最多列出多少处调用方

COMMON = set("""
get set run init main handle handler process update create delete remove list value values data name type index
render start stop load save test setup execute apply call build parse format tostring equals hashcode close open
read write size length config options default constructor init__ toString hashCode valueOf from item items result
error errors message status state props children request response context reset clear check validate convert
""".split())
COMMON = {w.lower() for w in COMMON}

LANG = {
    '.go': 'go', '.ts': 'ts', '.tsx': 'ts', '.mts': 'ts', '.cts': 'ts', '.js': 'js', '.jsx': 'js', '.mjs': 'js',
    '.cjs': 'js', '.vue': 'js', '.py': 'python', '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin',
    '.scala': 'java', '.groovy': 'java', '.cs': 'csharp', '.php': 'php', '.rb': 'ruby',
    '.sh': 'sh', '.bash': 'sh', '.zsh': 'sh',
}
TEXT_EXT = ('.md', '.markdown', '.rst', '.txt', '.adoc', '.html', '.htm', '.css', '.scss', '.less', '.svg', '.png',
            '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.pdf', '.csv', '.lock', '.sum', '.snap', '.map')
CONFIG_EXT = ('.yml', '.yaml', '.json', '.properties', '.toml', '.env', '.ini', '.conf')
DEP_FILES = re.compile(r'(^|/)(package\.json|go\.mod|go\.sum|pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|'
                       r'requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|Gemfile|composer\.json|'
                       r'Cargo\.toml|[^/]*\.csproj|packages\.config|'
                       r'package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Cargo\.lock|'
                       r'Gemfile\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock)$')
LOCK_FILES = re.compile(r'(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|'
                        r'Cargo\.lock|Gemfile\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock|go\.sum)$|\.lock$')
MIGRATION = re.compile(r'(^|/)(migrations?|migrate|db/migrate|alembic/versions|flyway|liquibase|changelogs?)/|'
                       r'(^|/)V\d+[_.\d]*__[^/]*\.sql$|\.sql$', re.I)
GENERATED_PATH = re.compile(r'\.pb\.go$|_pb2(_grpc)?\.py$|\.generated\.|_generated\.|\.g\.dart$|\.min\.(js|css)$|(^|/)generated/')
GENERATED_HEAD = re.compile(r'@generated|code generated .* do not edit|do not edit|auto-?generated|this file (is|was) generated', re.I)


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
    if base == '.env' or base.startswith('.env.'):
        return 'config'
    ext = os.path.splitext(base)[1].lower()
    if ext in CONFIG_EXT:
        return 'config'
    return LANG.get(ext)


# ---------- diff 解析 ----------

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$')


def parse_patch(text):
    files = []
    cur = None
    hunk = None
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
                cur['old_path'] = line[12:]
                cur['status'] = 'renamed'
            elif line.startswith('rename to '):
                cur['path'] = line[10:]
            elif line.startswith('+++ ') and line[4:] != '/dev/null':
                cur['path'] = line[6:] if line.startswith('+++ b/') else cur['path']
            elif line.startswith('--- ') and line[4:] != '/dev/null' and line.startswith('--- a/'):
                cur['old_path'] = line[6:]
        m = HUNK.match(line)
        if m:
            hunk = {'old_start': int(m.group(1)), 'new_start': int(m.group(3)),
                    'new_len': int(m.group(4)) if m.group(4) is not None else 1,
                    'func': m.group(5).strip(), 'lines': []}
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
        elif tag == ' ' or line == '':
            if line == '' and not hunk['lines'] and hunk['new_len'] == 0:
                continue
            hunk['lines'].append((' ', line[1:], old_no, new_no))
            old_no += 1
            new_no += 1
    for f in files:
        for h in f['hunks']:
            # 去掉补丁结尾多余的空上下文行
            while h['lines'] and h['lines'][-1][0] == ' ' and h['lines'][-1][1] == '' and \
                    h['lines'][-1][3] is not None and h['lines'][-1][3] >= h['new_start'] + h['new_len']:
                h['lines'].pop()
    return files


# ---------- 定义识别 ----------

def _rx(p):
    return re.compile(p)


JS_DEFS = [
    (_rx(r'^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]'), 'function', True),
    (_rx(r'^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)'), 'class', True),
    (_rx(r'^\s*export\s+(?:declare\s+)?(?:interface|enum)\s+([A-Za-z_$][\w$]*)'), 'type', True),
    (_rx(r'^\s*export\s+(?:declare\s+)?type\s+([A-Za-z_$][\w$]*)\s*[<=]'), 'type', True),
    (_rx(r'^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>'), 'function', True),
    (_rx(r'^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)'), 'const', True),
    (_rx(r'^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]'), 'function', False),
    (_rx(r'^(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)'), 'class', False),
    (_rx(r'^(?:interface|enum)\s+([A-Za-z_$][\w$]*)'), 'type', False),
    (_rx(r'^type\s+([A-Za-z_$][\w$]*)\s*[<=]'), 'type', False),
    (_rx(r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>'), 'function', False),
    (_rx(r'^\s+(?:(?:public|protected|static|async|readonly|override|abstract|get|set)\s+)*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^;]*\)\s*(?::\s*[^={]+)?\{\s*$'), 'method', None),
    (_rx(r'^\s+private\s+(?:(?:static|async|readonly)\s+)*([A-Za-z_$][\w$]*)\s*\('), 'method', False),
]
GO_DEFS = [
    (_rx(r'^func\s+\([^)]*\)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\('), 'method', 'case'),
    (_rx(r'^func\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\('), 'function', 'case'),
    (_rx(r'^type\s+([A-Za-z_]\w*)\s+(?:\[[^\]]*\]\s*)?(?:struct|interface)\b'), 'class', 'case'),
    (_rx(r'^type\s+([A-Za-z_]\w*)\b'), 'type', 'case'),
    (_rx(r'^(?:const|var)\s+([A-Za-z_]\w*)\b'), 'const', 'case'),
]
PY_DEFS = [
    (_rx(r'^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\('), 'function', 'py'),
    (_rx(r'^class\s+([A-Za-z_]\w*)'), 'class', 'py'),
    (_rx(r'^\s+(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\('), 'method', 'pym'),
]
JAVA_KW = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'else', 'throw', 'synchronized', 'try', 'do', 'super', 'this'}
JAVA_DEFS = [
    (_rx(r'^\s*((?:public|protected|private|internal)\s+)?(?:(?:static|final|abstract|sealed|non-sealed|strictfp|partial|data|open|inner|enum|annotation|value)\s+)*(?:class|interface|enum|record|object|@interface)\s+([A-Za-z_]\w*)'), 'class', 'mod'),
    (_rx(r'^\s*((?:public|protected|private|internal)\s+)?(?:(?:static|final|abstract|synchronized|native|default|override|open|suspend|inline|operator|infix|tailrec|external|virtual|async|unsafe|extern|sealed|new)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\('), 'function', 'kotlin'),
    (_rx(r'^\s*((?:public|protected|private|internal)\s+)(?:(?:static|final|abstract|synchronized|native|default|override|virtual|async|unsafe|extern|sealed|new|readonly)\s+)*(?:<[^>]*>\s*)?[\w<>\[\],.?@ ]+?\s+([A-Za-z_]\w*)\s*\('), 'method', 'mod'),
    (_rx(r'^\s+((?:static|final|abstract|synchronized)\s+)?(?:<[^>]*>\s*)?[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\([^;]*$'), 'method', 'pkg'),
]
PHP_DEFS = [
    (_rx(r'^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)'), 'class', True),
    (_rx(r'^\s*((?:public|protected|private)\s+)?(?:static\s+|abstract\s+|final\s+)*function\s+&?([A-Za-z_]\w*)\s*\('), 'method', 'phpmod'),
]
RB_DEFS = [
    (_rx(r'^\s*(?:class|module)\s+([A-Z]\w*(?:::\w+)*)'), 'class', True),
    (_rx(r'^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)'), 'method', None),
]
SH_DEFS = [
    (_rx(r'^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?'), 'function', None),
    (_rx(r'^\s*function\s+([A-Za-z_][\w-]*)\b'), 'function', None),
]
DEFS = {'sh': SH_DEFS, 'ts': JS_DEFS, 'js': JS_DEFS, 'go': GO_DEFS, 'python': PY_DEFS, 'java': JAVA_DEFS,
        'kotlin': JAVA_DEFS, 'csharp': JAVA_DEFS, 'php': PHP_DEFS, 'ruby': RB_DEFS}


def match_def(lang, text, py_all=None):
    """返回 (name, kind, exported) 或 None。"""
    for rx, kind, exp in DEFS.get(lang, []):
        m = rx.match(text)
        if not m:
            continue
        if exp in ('mod', 'kotlin', 'phpmod', 'pkg'):
            mod = (m.group(1) or '').strip()
            name = m.group(2)
            if name in JAVA_KW or (mod == '' and exp == 'pkg' and name in JAVA_KW):
                continue
            if exp == 'pkg':
                if re.match(r'^\s*(return|throw|new|else)\b', text):
                    continue
                exported = False
            elif exp == 'phpmod':
                exported = mod != 'private'
            elif mod in ('public', 'protected'):
                exported = True
            elif mod in ('private', 'internal'):
                exported = False
            else:
                # Kotlin 默认 public;Java/C# 没写修饰符的类型是包内可见
                exported = True if lang == 'kotlin' else (None if kind == 'class' and lang != 'kotlin' else False)
            return name, kind, exported
        name = m.group(1)
        if kind == 'method' and name in JAVA_KW:
            continue
        if exp == 'case':
            exported = name[:1].isupper()
        elif exp == 'py':
            exported = (name in py_all) if py_all is not None else not name.startswith('_')
        elif exp == 'pym':
            exported = None if not name.startswith('_') else False
        else:
            exported = exp
        return name, kind, exported
    return None


def norm_sig(text):
    s = re.sub(r'\s+', ' ', text).strip()
    s = re.sub(r'\s*\{\s*$', '', s)
    s = re.sub(r'\s*:\s*$', '', s) if s.startswith(('def ', 'async def ', 'class ')) else s
    return s


def full_sig(lines, i):
    """从第 i 行起把跨行的签名拼起来(括号闭合或遇到 { : => 为止,最多 6 行)。"""
    parts = [lines[i]]
    depth = lines[i].count('(') - lines[i].count(')')
    j = i
    while depth > 0 and j + 1 < len(lines) and j - i < 6:
        j += 1
        parts.append(lines[j])
        depth += lines[j].count('(') - lines[j].count(')')
    return norm_sig(' '.join(p.strip() for p in parts))


FUNC_IDENT = re.compile(r'([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(')


# ---------- 配置键 ----------

def flatten_json(obj, prefix='', out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = prefix + '.' + str(k) if prefix else str(k)
            if isinstance(v, dict) and v:
                flatten_json(v, key, out)
            else:
                out[key] = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return out


def flatten_yaml(text):
    out = {}
    stack = []  # (indent, key)
    for raw in text.split('\n'):
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith('#') or s in ('---', '...'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if s.startswith('- '):
            continue
        m = re.match(r'^(["\']?)([^"\':#]+?)\1\s*:(\s+(.*))?$', s)
        if not m:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = '.'.join([k for _, k in stack] + [m.group(2).strip()])
        val = (m.group(4) or '').strip()
        if val == '' or val in ('|', '>', '|-', '>-'):
            stack.append((indent, m.group(2).strip()))
            if val:
                out[key] = val
        else:
            out[key] = val
    # 只有子键的父键不算一个配置项
    return {k: v for k, v in out.items() if not any(o.startswith(k + '.') for o in out)}


def flatten_props(text):
    out = {}
    for raw in text.split('\n'):
        s = raw.strip()
        if not s or s.startswith(('#', '!', ';')):
            continue
        m = re.match(r'^(?:export\s+)?([A-Za-z0-9_.\-\[\]]+)\s*[=:]\s*(.*)$', s)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def flatten_toml(text):
    out = {}
    section = ''
    for raw in text.split('\n'):
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'^\[\[?([^\]]+)\]\]?$', s)
        if m:
            section = m.group(1).strip()
            continue
        m = re.match(r'^([A-Za-z0-9_.\-"\']+)\s*=\s*(.*)$', s)
        if m:
            k = m.group(1).strip('"\'')
            out[(section + '.' + k) if section else k] = m.group(2).strip()
    return out


def config_keys(path, text):
    if text is None:
        return None
    base = path.rsplit('/', 1)[-1].lower()
    try:
        if base.endswith('.json'):
            return flatten_json(json.loads(text))
        if base.endswith(('.yml', '.yaml')):
            return flatten_yaml(text)
        if base.endswith('.toml'):
            return flatten_toml(text)
        return flatten_props(text)
    except ValueError:
        return None


# ---------- 读代码 ----------

class Repo:
    def __init__(self, root, code_ref, base_sha):
        self.root, self.code_ref, self.base_sha = root, code_ref, base_sha
        self.cache = {}

    def git(self, *args, timeout=20):
        return subprocess.run(['git', '-C', self.root] + list(args), capture_output=True, timeout=timeout)

    def show(self, ref, path):
        key = (ref, path)
        if key in self.cache:
            return self.cache[key]
        text = None
        if ref == 'WORKTREE':
            try:
                with open(os.path.join(self.root, path), encoding='utf-8', errors='replace') as f:
                    text = f.read()
            except OSError:
                text = None
        elif ref:
            r = self.git('show', '%s:%s' % (ref, path))
            if r.returncode == 0:
                text = r.stdout.decode('utf-8', 'replace')
        self.cache[key] = text
        return text

    def new(self, path):
        return self.show(self.code_ref, path) if self.code_ref and self.code_ref != 'UNAVAILABLE' else None

    def old(self, path):
        return self.show(self.base_sha, path) if self.base_sha else None


def read_kv(path):
    d = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if '=' in line:
                    k, v = line.rstrip('\n').split('=', 1)
                    d[k] = v
    except OSError:
        pass
    return d


def read_text(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


# ---------- 主流程 ----------

class Facts:
    def __init__(self, rd):
        self.rd = rd
        self.limits = []
        self.meta = read_kv(os.path.join(rd, 'meta.txt'))
        self.repo = Repo(self.meta.get('repo_root') or '.', self.meta.get('code_ref', ''), self.meta.get('base_sha', ''))
        full = os.path.join(rd, 'diff.full.patch')
        self.diff_name = 'diff.full.patch' if os.path.exists(full) else 'diff.patch'
        self.files = parse_patch(read_text(os.path.join(rd, self.diff_name)))
        self.test_files = parse_patch(read_text(os.path.join(rd, 'skipped.patch')))
        self.skipped = []
        for line in read_text(os.path.join(rd, 'skipped.txt')).splitlines():
            if '\t' in line:
                p, r = line.split('\t', 1)
                self.skipped.append((p, r))
        self.changed_paths = {f['path'] for f in self.files + self.test_files} | {p for p, _ in self.skipped}
        self.new_ranges = {}
        for f in self.files + self.test_files:
            for h in f['hunks']:
                lo = h['new_start']
                hi = lo + max(h['new_len'], 1) - 1
                self.new_ranges.setdefault(f['path'], []).append((lo, hi))

    def in_diff(self, path, line):
        return any(lo <= line <= hi for lo, hi in self.new_ranges.get(path, []))

    # ----- hunks -----
    def build_hunks(self):
        hunks = []
        n = 0
        for f in self.files + self.test_files:
            kind = self.file_kind(f['path'])
            f['kind'] = kind
            for h in f['hunks']:
                n += 1
                h['id'] = 'H%d' % n
                adds = sum(1 for t, *_ in h['lines'] if t == '+')
                dels = sum(1 for t, *_ in h['lines'] if t == '-')
                lo = h['new_start']
                hi = lo + max(h['new_len'], 1) - 1
                hunks.append({'id': h['id'], 'file': f['path'], 'start': lo, 'end': hi, 'add': adds, 'del': dels,
                              'func': h['func'], 'kind': kind, 'symbols': []})
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
        text = self.repo.new(path)
        if text:
            head = '\n'.join(text.split('\n')[:5])
            return bool(GENERATED_HEAD.search(head))
        return False

    # ----- symbols -----
    def py_all(self, text):
        if not text:
            return None
        m = re.search(r'^__all__\s*=\s*[\[(]([^\])]*)[\])]', text, re.M | re.S)
        if not m:
            return None
        return set(re.findall(r'["\']([A-Za-z_]\w*)["\']', m.group(1)))

    def symbols_in_file(self, f):
        lang = lang_of(f['path'])
        if lang is None:
            return []
        if lang == 'config':
            return self.config_symbols(f)
        new_all = self.py_all(self.repo.new(f['path'])) if lang == 'python' else None
        old_all = self.py_all(self.repo.old(f.get('old_path') or f['path'])) if lang == 'python' else None
        sides = {'-': {}, '+': {}}   # name -> {kind, exported, sigs, line, hunk}
        body = {}                    # name -> (kind, exported, line, hunk) 定义未变、实现变了
        for h in f['hunks']:
            lines = h['lines']
            texts = [t for _, t, _, _ in lines]
            cur = None
            hdr = h['func']
            if hdr:
                d = match_def(lang, hdr, new_all)
                if d:
                    cur = (d[0], d[1], d[2], None)
                else:
                    m = FUNC_IDENT.search(hdr)
                    if m and m.group(1) not in JAVA_KW:
                        cur = (m.group(1), 'unknown', None, None)
            for i, (tag, text, old_no, new_no) in enumerate(lines):
                side_lines = [t for (tg, t, _, _) in lines if tg in (' ', tag)] if tag != ' ' else texts
                d = match_def(lang, text, new_all if tag != '-' else old_all)
                if d:
                    name, kind, exported = d
                    if tag == ' ':
                        cur = (name, kind, exported, new_no)
                        continue
                    idx = [k for k, (tg, _, _, _) in enumerate(lines) if tg in (' ', tag)].index(i)
                    sig = full_sig(side_lines, idx)
                    rec = sides[tag].setdefault(name, {'kind': kind, 'exported': exported, 'sigs': [], 'line': None, 'hunk': h['id']})
                    rec['sigs'].append(sig)
                    if tag == '+' and rec['line'] is None:
                        rec['line'] = new_no
                    if tag == '-' and rec['line'] is None:
                        rec['line'] = old_no
                    if exported and not rec['exported']:
                        rec['exported'] = exported
                    cur = (name, kind, exported, new_no if tag == '+' else None)
                    continue
                if tag in '+-' and cur:
                    name, kind, exported, line = cur
                    body.setdefault(name, (kind, exported, line, h['id']))
        out = []
        for name in list(dict.fromkeys(list(sides['+']) + list(sides['-']))):
            p, m = sides['+'].get(name), sides['-'].get(name)
            if p and not m:
                change = 'added'
            elif m and not p:
                change = 'removed'
            elif sorted(set(p['sigs'])) != sorted(set(m['sigs'])):
                change = 'signature_changed'
            else:
                change = 'body_only'
            rec = p or m
            exported = (p or {}).get('exported')
            if exported is None:
                exported = (m or {}).get('exported')
            out.append({'name': name, 'file': f['path'], 'line': rec['line'], 'lang': lang, 'kind': rec['kind'],
                        'change': change, 'old_sig': m['sigs'][0] if m else None, 'new_sig': p['sigs'][0] if p else None,
                        'exported': exported, 'hunk': rec['hunk']})
        seen = {s['name'] for s in out}
        for name, (kind, exported, line, hid) in body.items():
            if name in seen:
                continue
            out.append({'name': name, 'file': f['path'], 'line': line, 'lang': lang, 'kind': kind,
                        'change': 'body_only', 'old_sig': None, 'new_sig': None, 'exported': exported, 'hunk': hid})
        return out

    def config_symbols(self, f):
        path = f['path']
        if DEP_FILES.search(path):
            return []
        if f['status'] in ('added', 'deleted'):
            return []
        old_text = self.repo.old(f.get('old_path') or path) if f['status'] != 'added' else ''
        new_text = self.repo.new(path) if f['status'] != 'deleted' else ''
        old_keys = config_keys(path, old_text)
        new_keys = config_keys(path, new_text)
        out = []
        if old_keys is None or new_keys is None:
            # 读不到两侧完整内容时,只看改动行里的键
            self.limits.append('%s:读不到改动前后的完整内容,配置键按改动行识别' % path)
            plus, minus = {}, {}
            for h in f['hunks']:
                for tag, text, old_no, new_no in h['lines']:
                    m = re.match(r'^\s*(?:export\s+)?["\']?([A-Za-z0-9_.\-]+)["\']?\s*[:=]', text)
                    if m and tag == '+':
                        plus.setdefault(m.group(1), (new_no, h['id']))
                    elif m and tag == '-':
                        minus.setdefault(m.group(1), (old_no, h['id']))
            for k, (line, hid) in plus.items():
                out.append(self._cfg(path, k, 'added' if k not in minus else 'body_only', line, hid))
            for k, (line, hid) in minus.items():
                if k not in plus:
                    out.append(self._cfg(path, k, 'removed', line, hid))
            return out
        for k in new_keys:
            if k not in old_keys:
                out.append(self._cfg(path, k, 'added', *self.key_pos(f, k, '+')))
            elif new_keys[k] != old_keys[k]:
                out.append(self._cfg(path, k, 'body_only', *self.key_pos(f, k, '+')))
        for k in old_keys:
            if k not in new_keys:
                out.append(self._cfg(path, k, 'removed', *self.key_pos(f, k, '-')))
        return out

    def key_pos(self, f, key, tag):
        leaf = re.escape(key.rsplit('.', 1)[-1])
        for h in f['hunks']:
            for tg, text, old_no, new_no in h['lines']:
                if tg == tag and re.search(r'(^|[\s"\'.])' + leaf + r'["\']?\s*[:=]', text):
                    return (new_no if tag == '+' else old_no), h['id']
        return None, (f['hunks'][0]['id'] if f['hunks'] else None)

    def _cfg(self, path, key, change, line, hid):
        return {'name': key, 'file': path, 'line': line, 'lang': 'config', 'kind': 'config_key', 'change': change,
                'old_sig': None, 'new_sig': None, 'exported': True, 'hunk': hid}

    # ----- callers -----
    def grep(self, names, deadline):
        """一次 git grep 查所有名字;返回 {name: [(path, line, text)]}。"""
        res = {n: [] for n in names}
        if not names:
            return res
        args = ['grep', '-n', '-I', '-w', '-F']
        for n in names:
            args += ['-e', n]
        ref = self.repo.code_ref
        if ref and ref not in ('WORKTREE', 'UNAVAILABLE'):
            args.append(ref)
        elif ref == 'WORKTREE':
            args.append('--untracked')
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError
        try:
            r = self.repo.git(*args, timeout=left)
        except subprocess.TimeoutExpired:
            raise TimeoutError
        prefix = (ref + ':') if ref and ref not in ('WORKTREE', 'UNAVAILABLE') else ''
        pats = {n: re.compile(r'(?<![\w$])' + re.escape(n) + r'(?![\w$])') for n in names}
        for raw in r.stdout.decode('utf-8', 'replace').splitlines():
            if prefix and raw.startswith(prefix):
                raw = raw[len(prefix):]
            parts = raw.split(':', 2)
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            path, line, text = parts[0], int(parts[1]), parts[2]
            for n, p in pats.items():
                if p.search(text):
                    res[n].append((path, line, text))
        return res

    def add_callers(self, symbols):
        candidates = []
        tests_only = []
        for s in symbols:
            s.update({'callers': [], 'callers_total': 0, 'callers_truncated': False, 'ambiguous': False,
                      'skipped': None, 'tests': []})
            if s['exported'] is False:
                continue
            name = s['name']
            short = name.rsplit('.', 1)[-1] if s['kind'] == 'config_key' else name
            if s['kind'] != 'config_key' and (len(name) < 4 or name.lower() in COMMON):
                s['skipped'] = 'common_name'
                continue
            if s['kind'] == 'config_key' and (len(short) < 3 or ('.' not in name and len(name) < 8)):
                s['skipped'] = 'common_name'
                continue
            if s['change'] in ('signature_changed', 'removed', 'added'):
                candidates.append(s)
            elif s['change'] == 'body_only' and s['kind'] != 'config_key':
                tests_only.append(s)
        order = {'signature_changed': 0, 'removed': 1, 'added': 2}
        candidates.sort(key=lambda s: order[s['change']])
        if len(candidates) > MAX_SYMBOLS:
            self.limits.append('改动的公开符号有 %d 个,只查了前 %d 个的调用方' % (len(candidates), MAX_SYMBOLS))
            candidates = candidates[:MAX_SYMBOLS]
        tests_only = tests_only[:MAX_SYMBOLS]
        names = sorted({s['name'] for s in candidates + tests_only})
        deadline = time.time() + GREP_BUDGET
        try:
            hits = self.grep(names, deadline)
        except TimeoutError:
            self.limits.append('查调用方超过 %d 秒,已停止;调用方和相关测试没有列出' % GREP_BUDGET)
            return
        except OSError as e:
            self.limits.append('git grep 失败:%s' % e)
            return
        for s in candidates + tests_only:
            lang = s['lang']
            rows = hits.get(s['name'], [])
            defs, callers, tests = [], [], {}
            for path, line, text in rows:
                if path == s['file'] and line == s['line']:
                    continue
                d = match_def(lang, text) if lang != 'config' else None
                if d and d[0] == s['name'] and lang_of(path) == lang:
                    defs.append((path, line))
                    continue
                if is_test(path):
                    tests.setdefault(path, path in self.changed_paths)
                    continue
                callers.append((path, line))
            s['tests'] = [{'file': p, 'changed': c} for p, c in sorted(tests.items())]
            if s not in candidates:
                continue
            s['ambiguous'] = len({p for p, _ in defs if not (p == s['file'])}) >= 1 and s['change'] != 'removed'
            s['callers_total'] = len(callers)
            if len(callers) > MAX_HITS:
                s['skipped'] = 'too_many_hits'
                continue
            s['callers'] = [{'file': p, 'line': ln, 'in_diff': self.in_diff(p, ln)} for p, ln in callers[:MAX_LISTED]]
            s['callers_truncated'] = len(callers) > MAX_LISTED

    # ----- attention / skippable -----
    ENV = [re.compile(p) for p in (
        r'process\.env\.([A-Z][A-Z0-9_]+)', r'process\.env\[\s*["\']([A-Z][A-Z0-9_]+)["\']',
        r'import\.meta\.env\.([A-Z][A-Z0-9_]+)', r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
        r'os\.environ(?:\.get)?[\[(]\s*["\']([A-Z][A-Z0-9_]+)["\']', r'System\.getenv\(\s*"([A-Z][A-Z0-9_]+)"',
        r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]+)"', r'ENV\[\s*["\']([A-Z][A-Z0-9_]+)["\']',
        r'ENV\.fetch\(\s*["\']([A-Z][A-Z0-9_]+)["\']', r'\bgetenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
        r'Environment\.GetEnvironmentVariable\(\s*"([A-Z][A-Z0-9_]+)"')]
    CFG_ENV = re.compile(r'\$\{([A-Z][A-Z0-9_]+)(?::[^}]*)?\}')

    def attention(self, symbols):
        out = []
        for f in self.files + self.test_files:
            p = f['path']
            if lang_of(p) == 'config' and f['status'] in ('added', 'deleted') and not DEP_FILES.search(p):
                keys = config_keys(p, self.repo.new(p) if f['status'] == 'added' else self.repo.old(f.get('old_path') or p))
                n = ('(%d 个键)' % len(keys)) if keys else ''
                out.append({'type': 'config_key', 'file': p, 'line': None,
                            'detail': '%s配置文件 %s%s' % ('新增' if f['status'] == 'added' else '删除', p, n)})
            if MIGRATION.search(p) and not is_test(p):
                verb = {'added': '新增迁移', 'deleted': '删除迁移'}.get(f['status'], '修改迁移')
                out.append({'type': 'migration', 'file': p, 'line': None, 'detail': '%s %s' % (verb, p)})
            if DEP_FILES.search(p):
                out.append({'type': 'dependency', 'file': p, 'line': None, 'detail': self.dep_detail(f)})
            plus, minus = {}, set()
            cfg = lang_of(p) == 'config'
            for h in f['hunks']:
                for tag, text, _, new_no in h['lines']:
                    found = [m.group(1) for rx in self.ENV for m in rx.finditer(text)]
                    if cfg:
                        found += self.CFG_ENV.findall(text)
                    for v in found:
                        if tag == '+':
                            plus.setdefault(v, new_no)
                        elif tag == '-':
                            minus.add(v)
            for v, line in plus.items():
                if v not in minus and not self.env_known(v, f):
                    out.append({'type': 'env_var', 'file': p, 'line': line, 'detail': '新环境变量 %s' % v})
        seen_dep = {a['file'] for a in out if a['type'] == 'dependency'}
        for p, r in self.skipped:
            if r == 'lockfile' and p not in seen_dep:
                out.append({'type': 'dependency', 'file': p, 'line': None, 'detail': 'lock 文件变化 %s' % p})
        for s in symbols:
            if s['kind'] == 'config_key' and s['change'] in ('added', 'removed'):
                verb = '新增' if s['change'] == 'added' else '删除'
                out.append({'type': 'config_key', 'file': s['file'], 'line': s['line'],
                            'detail': '%s配置键 %s' % (verb, s['name'])})
            elif s['kind'] != 'config_key' and s['exported'] is not False and \
                    s['change'] in ('signature_changed', 'removed') and not is_test(s['file']):
                if s['change'] == 'signature_changed':
                    detail = '`%s` 签名变化:%s → %s' % (s['name'], s['old_sig'], s['new_sig'])
                else:
                    detail = '删除 `%s`' % s['name']
                if s.get('callers_total'):
                    detail += '(本仓库另有 %d 处引用)' % s['callers_total']
                out.append({'type': 'signature', 'file': s['file'], 'line': s['line'], 'detail': detail})
        return out

    def env_known(self, var, f):
        """改动前的同一文件里已经用过的变量不算新的。"""
        old = self.repo.old(f.get('old_path') or f['path']) if f['status'] != 'added' else ''
        return bool(old) and var in old

    def dep_detail(self, f):
        p = f['path']
        base = p.rsplit('/', 1)[-1]
        try:
            if base == 'package.json':
                old = json.loads(self.repo.old(f.get('old_path') or p) or '{}') if f['status'] != 'added' else {}
                new = json.loads(self.repo.new(p) or '{}') if f['status'] != 'deleted' else {}
                parts = []
                for sec in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
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
            if base == 'go.mod' or re.match(r'requirements[^/]*\.txt$', base):
                plus, minus = [], []
                for h in f['hunks']:
                    for tag, text, _, _ in h['lines']:
                        s = text.strip()
                        if not s or s.startswith(('#', '//', 'module ', 'go ', ')', 'require (')):
                            continue
                        s = re.sub(r'^require\s+', '', s)
                        (plus if tag == '+' else minus if tag == '-' else []).append(s)
                parts = ['+ ' + x for x in plus[:5]] + ['− ' + x for x in minus[:5]]
                if parts:
                    return '依赖变化(%s):%s' % (p, ';'.join(parts))
        except (ValueError, AttributeError):
            pass
        adds = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '+')
        dels = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '-')
        return '依赖文件变化 %s(+%d/−%d 行)' % (p, adds, dels)

    def skippable(self):
        out = []
        seen = set()
        for f in self.files + self.test_files:
            p = f['path']
            reason = None
            if LOCK_FILES.search(p):
                reason = 'lockfile'
            elif f.get('kind') == 'generated' or self.is_generated(p):
                reason = 'generated'
            elif f['status'] == 'renamed' and f['similarity'] == 100 and not f['hunks']:
                reason = 'rename_only'
            elif f['hunks'] and self.whitespace_only(f):
                reason = 'whitespace_only'
            if reason:
                out.append({'file': p, 'reason': reason})
                seen.add(p)
        for p, r in self.skipped:
            if p in seen:
                continue
            if r == 'lockfile':
                out.append({'file': p, 'reason': 'lockfile'})
            elif r == 'generated':
                out.append({'file': p, 'reason': 'generated'})
        return out

    @staticmethod
    def whitespace_only(f):
        plus, minus = [], []
        for h in f['hunks']:
            for tag, text, _, _ in h['lines']:
                s = re.sub(r'\s+', '', text)
                if tag == '+' and s:
                    plus.append(s)
                elif tag == '-' and s:
                    minus.append(s)
        has_change = any(t in '+-' for h in f['hunks'] for t, *_ in h['lines'])
        return has_change and sorted(plus) == sorted(minus)

    # ----- effort -----
    def effort(self, skippable, symbols):
        skip = {s['file'] for s in skippable}
        files = [f for f in self.files + self.test_files if f['path'] not in skip]
        adds = sum(1 for f in files for h in f['hunks'] for t, *_ in h['lines'] if t == '+')
        dels = sum(1 for f in files for h in f['hunks'] for t, *_ in h['lines'] if t == '-')
        lines = adds + dels
        dirs = {f['path'].rsplit('/', 1)[0] if '/' in f['path'] else '.' for f in files}
        sigs = sum(1 for s in symbols if s['change'] in ('signature_changed', 'removed') and s['exported'] is not False
                   and s['kind'] != 'config_key')
        score = 1 if lines <= 50 else 2 if lines <= 200 else 3 if lines <= 500 else 4 if lines <= 1000 else 5
        if len(files) > 15 or len(dirs) > 4:
            score += 1
        if sigs >= 5:
            score += 1
        score = max(1, min(5, score))
        basis = '%d 文件 +%d/−%d,%d 个目录,%d 处签名变化或删除' % (len(files), adds, dels, len(dirs), sigs)
        if skip:
            basis += '(不含可跳过的 %d 个文件)' % len(skip)
        return {'score': score, 'basis': basis}

    def run(self):
        hunks = self.build_hunks()
        by_id = {h['id']: h for h in hunks}
        symbols = []
        unknown = []
        for f in self.files + self.test_files:
            if f['status'] == 'renamed' and not f['hunks']:
                continue
            try:
                found = self.symbols_in_file(f)
            except Exception as e:  # noqa: BLE001 一个文件出错不影响其他文件
                self.limits.append('%s:识别定义出错(%s)' % (f['path'], type(e).__name__))
                continue
            if lang_of(f['path']) is None and f['hunks'] and not is_test(f['path']) and \
                    not f['path'].lower().endswith(TEXT_EXT) and not DEP_FILES.search(f['path']):
                unknown.append(f['path'])
            symbols.extend(found)
        if unknown:
            self.limits.append('%d 个文件的类型不认识,只给到文件和 hunk:%s' % (len(unknown), '、'.join(unknown[:5])
                               + (' 等' if len(unknown) > 5 else '')))
        for s in symbols:
            h = by_id.get(s['hunk'])
            if h is not None and s['name'] not in h['symbols']:
                h['symbols'].append(s['name'])
        code_ok = self.repo.code_ref and self.repo.code_ref != 'UNAVAILABLE'
        if code_ok:
            self.add_callers(symbols)
        else:
            self.limits.append('读不到被审查的代码,没有查调用方')
            for s in symbols:
                s.update({'callers': [], 'callers_total': 0, 'callers_truncated': False, 'ambiguous': False,
                          'skipped': None, 'tests': []})
        skippable = self.skippable()
        attention = self.attention(symbols)
        added = sum(h['add'] for h in hunks)
        deleted = sum(h['del'] for h in hunks)
        nfiles = len(self.changed_paths)
        if any(r in ('lockfile', 'generated', 'binary') for _, r in self.skipped):
            self.limits.append('lock、生成和二进制文件的行数没有计入')
        keys = ('name', 'file', 'line', 'lang', 'kind', 'change', 'old_sig', 'new_sig', 'exported', 'hunk',
                'callers', 'callers_total', 'callers_truncated', 'ambiguous', 'skipped', 'tests')
        return {
            'version': 1,
            'scope': {'diff': self.diff_name, 'files': nfiles, 'added': added, 'deleted': deleted},
            'hunks': hunks,
            'symbols': [{k: s.get(k) for k in keys} for s in symbols],
            'attention': attention,
            'skippable': skippable,
            'effort': self.effort(skippable, symbols),
            'method': '按名字在本仓库文本匹配(git grep -w),只看签名变化',
            'limits': self.limits,
        }


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        sys.stderr.write('用法: analysis_facts.py <review_dir>\n')
        return 2
    rd = argv[1]
    out = os.path.join(rd, 'analysis-facts.json')
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
                'method': '按名字在本仓库文本匹配(git grep -w),只看签名变化', 'limits': facts.limits}
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('analysis_facts: hunks=%d symbols=%d attention=%d' % (len(data['hunks']), len(data['symbols']), len(data['attention'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
