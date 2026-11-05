"""历史归纳的固定代码引用校验；只验证出处，不替模型证明语义对应。"""
import hashlib
import os
import posixpath
import re
import subprocess

import crlib
import project_rules


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_path(path):
    require(isinstance(path, str) and path and not path.startswith('/') and
            not any(c in path for c in ('\\', '\x00', '\n', '\r')) and
            all(part not in ('', '.', '..') for part in path.split('/')) and
            not project_rules._credential_path(path),
            '代码来源须为仓库内非凭证文件,不能越界或访问 .git')
    return path


class CodeEvidence:
    def __init__(self, root):
        self.root = root
        self._trees = {}
        self._blobs = {}

    def git(self, *args):
        env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
        return subprocess.run(['git', '-C', self.root, '--no-replace-objects'] + list(args),
                              capture_output=True, env=env)

    def tree(self, ref):
        if ref not in self._trees:
            result = self.git('ls-tree', '-r', '-z', ref)
            if result.returncode:
                self._trees[ref] = None
            else:
                rows = {}
                for row in result.stdout.split(b'\0'):
                    if row:
                        info, name = row.split(b'\t', 1)
                        mode, kind, oid = info.decode('ascii').split()
                        rows[name.decode('utf-8', errors='surrogateescape')] = (mode, kind, oid)
                self._trees[ref] = rows
        return self._trees[ref]

    def blob(self, oid):
        if oid not in self._blobs:
            result = self.git('cat-file', 'blob', oid)
            self._blobs[oid] = result.stdout if result.returncode == 0 else None
        return self._blobs[oid]

    def read(self, ref, path):
        """固定树内解析链接；从不打开工作区文件或跟随工作区链接。"""
        safe_path(path)
        tree = self.tree(ref)
        if tree is None:
            return None, path, '固定代码对象不可读:' + ref
        current, seen = path, set()
        while True:
            safe_path(current)
            require(current not in seen, '代码来源存在符号链接循环:' + path)
            seen.add(current)
            parts = current.split('/')
            link = next(('/'.join(parts[:i]) for i in range(1, len(parts) + 1)
                         if tree.get('/'.join(parts[:i]), ('',))[0] == '120000'), None)
            if link is None:
                break
            target = self.blob(tree[link][2])
            if target is None:
                return None, current, '固定代码链接对象不可读:' + link
            try:
                target = target.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('代码来源链接不是有效文本:' + link)
            require(not target.startswith('/'), '代码来源符号链接越界:' + path)
            suffix = current[len(link):].lstrip('/')
            current = posixpath.normpath(posixpath.join(posixpath.dirname(link), target, suffix))
        require(current in tree, '代码来源不是固定版本的仓库文件:' + path)
        mode, kind, oid = tree[current]
        require(kind == 'blob' and mode in ('100644', '100755'), '代码来源不是普通文本文件:' + path)
        data = self.blob(oid)
        if data is None:
            return None, current, '固定代码内容不可读:' + path
        require(b'\x00' not in data, '代码来源不是文本文件:' + path)
        try:
            return data.decode('utf-8'), current, ''
        except UnicodeDecodeError:
            raise ValueError('代码来源不是 UTF-8 文本:' + path)

    def reference(self, item, expected_ref):
        require(isinstance(item, dict), '代码引用应为对象')
        ref, path, line, quote, reason = (item.get(k) for k in ('ref', 'file', 'line', 'quote', 'reason'))
        require(isinstance(ref, str) and re.fullmatch(r'[0-9a-f]{40,64}', ref) and ref == expected_ref,
                '代码引用 ref 必须严格等于本次冻结的最终版本')
        safe_path(path)
        require(type(line) is int and line > 0, '代码引用 line 须为正整数')
        require(isinstance(quote, str) and quote.strip(), '代码引用缺少原文 quote')
        require(isinstance(reason, str) and reason.strip(), '代码引用缺少语义对应 reason')
        text, resolved, issue = self.read(ref, path)
        result = dict(ref=ref, file=path, line=line, quote=quote, reason=crlib.norm(reason),
                      status='unavailable' if text is None else 'verified', content_sha256=None)
        if resolved != path:
            result['resolved_file'] = resolved
        if text is None:
            result['issue'] = issue
            return result
        # 不归一空格或缩进：行号与完整的多行原文均须准确，最后一行可不带换行。
        lines = text.splitlines(keepends=True)
        quoted = quote.splitlines(keepends=True)
        actual = ''.join(lines[line - 1:line - 1 + len(quoted)])
        if not quote.endswith(('\n', '\r')):
            actual = actual.removesuffix('\n').removesuffix('\r')
        require(line <= len(lines) and actual == quote, '代码引文或行号与固定版本不符:%s:%d' % (path, line))
        result['content_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        return result

    def references(self, items, expected_ref, label):
        require(isinstance(items, list), label + ' 应为数组')
        unique = {}
        for item in items:
            row = self.reference(item, expected_ref)
            key = tuple(row[k] for k in ('ref', 'file', 'line', 'quote'))
            if key in unique:
                previous = unique[key]
                if row['reason'] != previous['reason']:
                    reasons = previous.setdefault('reasons', [previous['reason']])
                    if row['reason'] not in reasons:
                        reasons.append(row['reason'])
            else:
                unique[key] = row
        return list(unique.values())

    def practice(self, value, head):
        require(isinstance(value, dict), 'practice 应为对象')
        require(isinstance(value.get('reason'), str) and value['reason'].strip(), 'practice 缺少独立性与相关性说明 reason')
        scope = value.get('scope')
        require(isinstance(scope, list) and scope and all(isinstance(p, str) and p.strip() for p in scope),
                'practice.scope 应为非空 glob 数组')
        scope = project_rules.patterns(scope)
        require(scope, 'practice.scope 缺少适用范围')
        examples = self.references(value.get('examples'), head, 'practice.examples')
        counterexamples = self.references(value.get('counterexamples'), head, 'practice.counterexamples')
        return dict(reason=crlib.norm(value['reason']), scope=scope, examples=examples, counterexamples=counterexamples)
