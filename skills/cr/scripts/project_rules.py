"""项目规则的来源识别与范围解析,不执行正文。"""
import os
import re

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
    if path == '.cursor/BUGBOT.md' or path.endswith('/.cursor/BUGBOT.md'):
        return 'bugbot'
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
    native = source in ('claude', 'cursor', 'copilot', 'agents-rules') and path.startswith(('.claude/rules/', '.cursor/rules/', '.github/instructions/', '.agents/rules/'))
    scope = [os.path.dirname(path) + '/**'] if '/' in path else ['**']
    if path in ('.claude/CLAUDE.md', '.github/copilot-instructions.md'):
        scope = ['**']
    if source == 'bugbot':
        # Cursor Bugbot:a/b/.cursor/BUGBOT.md 管 a/b/ 下的改动,根目录的 .cursor/BUGBOT.md 管整个仓库。
        owner = os.path.dirname(os.path.dirname(path))
        scope = [owner + '/**'] if owner else ['**']
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


# REVIEW.md 的引入,照 Claude Code Review 的约定:只在根目录 REVIEW.md 里、单独一行 `@文件名`,
# 文件和 REVIEW.md 在同一目录(或 .claude/skills/<目录>/SKILL.md),只展开一层;
# 最多 8 个文件,REVIEW.md 和引入的文件合计不超过 150000 字节,超过的整个跳过;SKILL.md 单个不超过 30000 字节。
INCLUDE = re.compile(r'^@([^\s/]+|\.claude/skills/[^\s/]+/SKILL\.md)\s*$')
MAX_INCLUDES, MAX_BYTES, MAX_SKILL_BYTES = 8, 150000, 30000


def include_names(text):
    names, fence = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith(('```', '~~~')):
            fence = not fence
        m = None if fence else INCLUDE.match(line)
        if m:
            names.append(m.group(1))
    return names


def expand_includes(text, read):
    """把引入行换成文件内容;读不到的保持原样(和 Claude 一样当普通文字),超出限制的记下来。"""
    out, skipped, used, total, fence = [], [], [], len(text.encode('utf-8')), False
    for line in text.split('\n'):
        if line.lstrip().startswith(('```', '~~~')):
            fence = not fence
        m = None if fence else INCLUDE.match(line)
        body = read(m.group(1)) if m else None
        if body is None:
            out.append(line); continue
        name = m.group(1)
        if name.endswith('/SKILL.md'):
            if len(body.encode('utf-8')) > MAX_SKILL_BYTES:
                skipped.append(name + '(超过 30000 字节)'); continue
            body = crlib.parse_frontmatter(body)[1]
        if name not in used and len(used) >= MAX_INCLUDES:
            skipped.append(name + '(超过 8 个文件)'); continue
        size = len(body.encode('utf-8'))
        if total + size > MAX_BYTES:
            skipped.append(name + '(合计超过 150000 字节)'); continue
        if name not in used:
            used.append(name)
        total += size
        out.append(body.strip('\n'))
    return '\n'.join(out), used, skipped


def discover(src):
    return [describe(p, text) for p in sorted(set(src.files())) if kind(p)
            for text in [src.read(p)] if text is not None]
