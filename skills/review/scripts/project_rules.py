"""项目规则的来源识别与范围解析,不执行正文。"""
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

import crlib


def kind(path):
    name = path.rsplit('/', 1)[-1]
    if name == 'REVIEW.md':
        return 'review'
    if path.startswith('.claude/rules/') and path.endswith('.md'):
        return 'claude'
    if path.startswith('.agents/rules/') and path.endswith('.md'):
        return 'agents-rules'
    if path.startswith('.cursor/rules/') and path.endswith(('.mdc', '.md')):
        return 'cursor'
    if path.startswith('.github/instructions/') and path.endswith('.instructions.md'):
        return 'copilot'
    if path == '.github/copilot-instructions.md':
        return 'copilot'
    return {'CLAUDE.md': 'claude', 'AGENTS.md': 'agents', 'AGENTS.override.md': 'agents'}.get(name)


def patterns(value):
    """逗号只在花括号外分隔；兼容列表及字符串。"""
    if isinstance(value, dict):
        raise ValueError('范围必须是字符串或字符串列表')
    out = []
    for item in crlib.as_list(value):
        for part in crlib._split_top(item):
            out.extend(crlib.expand_braces(part.strip()))
    for p in out:
        if p.startswith(('/', '!')) or '\\' in p or '..' in p.split('/'):
            raise ValueError('范围须为仓库根目录相对 glob，不支持否定模式: ' + p)
        if any(c in p for c in '[]{}'):
            raise ValueError('范围含未支持或不完整的 glob 语法: ' + p)
    return out


def native_match(pattern, path):
    # 与旧 REVIEW.md 的 gitignore 风格分开：*.py 不匹配 src/a.py。
    return bool(crlib.glob_re(pattern).fullmatch(path))


def describe(path, text):
    source = kind(path)
    if source is None:
        raise ValueError('不是支持的项目规则文件: ' + path)
    fm, body = crlib.parse_frontmatter(text)
    native = source in ('claude', 'cursor', 'copilot', 'agents-rules') and path.startswith(('.claude/rules/', '.cursor/rules/', '.github/instructions/', '.agents/rules/'))
    scope = [os.path.dirname(path) + '/**'] if '/' in path else ['**']
    if path in ('.claude/CLAUDE.md', '.github/copilot-instructions.md'):
        scope = ['**']
    issues = []
    if native:
        key = {'claude': 'paths', 'cursor': 'globs', 'copilot': 'applyTo', 'agents-rules': 'paths'}[source]
        if source == 'agents-rules' and 'globs' in fm and 'paths' not in fm:
            key = 'globs'      # .agents/rules 没有统一的标准,paths 和 globs 都认
        wrong = set(fm) & ({'path', 'paths', 'glob', 'globs', 'applyTo', 'apply_to'} - {key} - ({'paths', 'globs'} if source == 'agents-rules' else set()))
        if wrong:
            issues.append('范围字段应为 %s，不能使用 %s' % (key, ', '.join(sorted(wrong))))
        try:
            scope = patterns(fm.get(key))
            if key in fm and not scope:
                issues.append(key + ' 为空，不能隐式扩大到全局')
        except ValueError as e:
            issues.append(str(e)); scope = []
        if source == 'cursor' and str(fm.get('alwaysApply', '')).lower() == 'true':
            scope = ['**']
        elif key not in fm:
            scope = [] if source == 'cursor' else ['**']
        if text.startswith('---') and body == text:
            issues.append('frontmatter 缺少结束分隔符')
    excluded = source == 'copilot' and 'code-review' in crlib.as_list(fm.get('excludeAgent'))
    return dict(file=path, source=source, text=text, body=body, frontmatter=fm,
                offset=len(text.split('\n')) - len(body.split('\n')),
                patterns=scope, native=native, excluded=excluded, issues=issues)


def matches(record, path):
    fn = native_match if record['native'] else crlib.glob_match
    return not record['issues'] and any(fn(p, path) for p in record['patterns'])


def discover(src):
    return [describe(p, text) for p in sorted(set(src.files())) if kind(p)
            for text in [src.read(p)] if text is not None]


_RULE_DIRS = ('.claude/rules', '.agents/rules', '.cursor/rules', '.github/instructions')


def _credential_path(path):
    """拒绝明显的凭证位置；同时检查逻辑路径与符号链接的真实目标。"""
    for part in Path(path).parts:
        name = part.lower()
        if (name == '.env' or name.startswith('.env.') or
                name in {'.git', '.ssh', '.aws', '.gnupg', '.azure', '.kube', '.netrc',
                         '.bashrc', '.bash_profile', '.bash_login', '.profile',
                         '.zshrc', '.zshenv', '.zprofile', '.zlogin', 'config.fish',
                         '.npmrc', '.pypirc', '.git-credentials', 'private_key',
                         'private-key', 'credentials', 'credentials.json',
                         'credentials.yml', 'credentials.yaml'} or
                name.startswith(('id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa')) or
                name.endswith(('.pem', '.key', '.p12', '.pfx', '.keystore')) or
                name in {'secrets.json', 'secrets.yml', 'secrets.yaml'}):
            return True
    return False


def _local_references(text):
    """只接受 Markdown 链接及独占一行的 @path，忽略代码块与图片。"""
    rows = text.splitlines()
    fenced = crlib.fence_mask(rows)
    for i, line in enumerate(rows):
        if fenced[i] or line.startswith(('    ', '\t')):
            continue
        # 不从行内代码、示例命令里采集路径。
        prose = re.sub(r'(`+).*?\1', '', line)
        targets = re.findall(r'(?<!!)\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s()]+)(?:\s+[\"\'][^\n]*?[\"\'])?\s*\)', prose)
        mention = re.fullmatch(r'\s*@([^\s`$|<>]+)\s*', line)
        if mention and '@' not in mention[1] and ('/' in mention[1] or '.' in Path(mention[1]).name):
            targets.append(mention[1])
        for target in targets:
            target = target[1:-1] if target.startswith('<') and target.endswith('>') else target
            try:
                url = urlsplit(target)
            except ValueError:
                continue
            if url.scheme or url.netloc or url.query or not url.path:
                continue
            path = unquote(url.path)
            if any(c in path for c in ('\x00', '\n', '\r', '`', '$', '|', '<', '>')):
                continue
            yield path


def mining_sources(root):
    """采集工作区规则及其明确共享来源，返回可 JSON 序列化的全文记录。

    仅在已识别的规则目录内跟随目录链接；正文引用只读取明确的单个
    文本文件。引用使用真实文档所在目录解析，适用范围继承逻辑规则。
    读取失败也保留记录，供冻结 existing-rules 时报告不完整的采集。
    discover(Source(...)) 的 review/commit 语义不受此入口影响。
    """
    root = Path(root).absolute()
    records, seen = [], set()

    def record(file, text, source_path, parent=None, issue=None):
        if parent is None and kind(file):
            item = describe(file, text)
        elif parent is not None:
            item = dict(parent, file=file, text=text, body=text, frontmatter={},
                        offset=0, issues=[], referenced_by=parent['file'])
        else:
            item = dict(file=file, text=text, body=text, source='shared',
                        frontmatter={}, offset=0, patterns=[], native=False,
                        excluded=False, issues=[])
        item['source_path'] = str(source_path)
        if issue:
            item['issues'].append(issue)
        records.append(item)
        return item

    def read_file(file, path, ancestors=(), parent=None):
        # 真实路径既作来源标记，也用于跨链接/引用的循环检测。
        real = os.path.realpath(path)
        key = (file, real, tuple(parent['patterns']) if parent else None)
        if real in ancestors:
            record(file, '', real, parent, '规则来源循环引用: ' + real)
            return
        if key in seen:
            return
        seen.add(key)
        if _credential_path(file) or _credential_path(path) or _credential_path(real):
            record(file, '', real, parent, '拒绝读取明显的凭证路径')
            return
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise ValueError('引用必须是普通文本文件')
            with open(path, encoding='utf-8') as stream:
                text = stream.read()
            if '\x00' in text:
                raise ValueError('来源不是纯文本')
        except (OSError, UnicodeError, ValueError) as exc:
            record(file, '', real, parent, '无法读取规则来源: ' + str(exc))
            return
        item = record(file, text, real, parent)
        for ref in _local_references(item['body']):
            expanded = os.path.expanduser(ref)
            physical = Path(real).parent / expanded
            logical = os.path.normpath(os.path.join(os.path.dirname(file), expanded))
            read_file(logical, physical, ancestors + (real,), item)

    def walk_rules(file, path, ancestors=()):
        real = os.path.realpath(path)
        if real in ancestors:
            record(file, '', real, issue='规则目录符号链接循环: ' + real)
            return
        if _credential_path(path) or _credential_path(real):
            record(file, '', real, issue='拒绝读取明显的凭证路径')
            return
        try:
            if not path.is_dir():
                raise ValueError('规则目录不存在或不是目录')
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except (OSError, ValueError) as exc:
            record(file, '', real, issue='无法读取规则目录: ' + str(exc))
            return
        for entry in entries:
            logical = file + '/' + entry.name
            if entry.is_dir():
                walk_rules(logical, entry, ancestors + (real,))
            elif kind(logical):
                read_file(logical, entry)
            elif entry.is_symlink() and not entry.exists():
                # 无扩展名的断链也可能原本指向一个规则子目录。
                record(logical, '', os.path.realpath(entry), issue='规则目录内的符号链接已断开或循环')

    files = set(crlib.Source(str(root)).files())
    # 目录本身也可能是链接，Git 此时不会列出目录内的入口文件。
    for file in ('REVIEW.md', 'CLAUDE.md', 'AGENTS.md', 'AGENTS.override.md',
                 '.claude/CLAUDE.md', '.github/copilot-instructions.md'):
        if os.path.lexists(root / file):
            files.add(file)
    for file in sorted(files):
        path = root / file
        if kind(file) and not (path.is_dir() and file.startswith(tuple(d + '/' for d in _RULE_DIRS))):
            read_file(file, path)
    for container in sorted({d.split('/')[0] for d in _RULE_DIRS}):
        path = root / container
        if path.is_symlink() and not path.is_dir():
            record(container, '', os.path.realpath(path), issue='规则配置目录的符号链接已断开或循环')
    for directory in _RULE_DIRS:
        path = root / directory
        if os.path.lexists(path):
            walk_rules(directory, path)
    return records
