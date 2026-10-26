"""项目规则的来源识别与范围解析,不执行正文。"""
import os

import crlib


def kind(path):
    name = path.rsplit('/', 1)[-1]
    if name == 'REVIEW.md':
        return 'review'
    if path.startswith('.claude/rules/') and path.endswith('.md'):
        return 'claude'
    if path.startswith('.cursor/rules/') and path.endswith(('.mdc', '.md')):
        return 'cursor'
    if path.startswith('.github/instructions/') and path.endswith('.instructions.md'):
        return 'copilot'
    return {
        'CLAUDE.md': 'claude', 'AGENTS.md': 'agents', 'AGENTS.override.md': 'agents',
        'GEMINI.md': 'gemini', 'CONVENTIONS.md': 'conventions',
    }.get(name) or {
        '.cursorrules': 'cursor', '.windsurfrules': 'windsurf',
        '.github/copilot-instructions.md': 'copilot',
    }.get(path)


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
    native = source in ('claude', 'cursor', 'copilot') and path.startswith(('.claude/rules/', '.cursor/rules/', '.github/instructions/'))
    scope = [os.path.dirname(path) + '/**'] if '/' in path else ['**']
    if path in ('.claude/CLAUDE.md', '.github/copilot-instructions.md'):
        scope = ['**']
    issues = []
    if native:
        key = {'claude': 'paths', 'cursor': 'globs', 'copilot': 'applyTo'}[source]
        wrong = set(fm) & ({'path', 'paths', 'glob', 'globs', 'applyTo', 'apply_to'} - {key})
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
