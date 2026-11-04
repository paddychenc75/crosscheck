"""识别 hook 命令中的脚本调用;只解析文字,不执行 shell 或展开变量。
不加引号的 heredoc 结束标记下,shell 会展开正文里的 $(...);这里把正文一律当文字,不计数。"""
import os
import re
import shlex
import sys


def heredoc_word(text, i):
    """读 << 或 <<- 后面的结束标记,去掉引号和反斜杠;返回 (标记, 下一个位置)。"""
    while i < len(text) and text[i] in ' \t':
        i += 1
    word, quote = [], None
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "\"'":
            quote = char
        elif char == '\\' and i + 1 < len(text):
            i += 1
            word.append(text[i])
        elif char.isspace() or char in ';|&()<>':
            break
        else:
            word.append(char)
        i += 1
    return ''.join(word), i


def skip_heredocs(text, i, pending):
    """从 i(换行之后)起跳过每个待处理 heredoc 的正文,直到它的结束标记行。"""
    for word, strip_tabs in pending:
        while i < len(text):
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            line = text[i:end]
            i = end + 1
            if (line.lstrip('\t') if strip_tabs else line) == word:
                break
    return min(i, len(text))


def scan(text, i=0, close=None):
    """按未引用的 shell 分隔符拆成简单命令的原文;返回 (命令列表, 结束位置)。

    $(...) 和反引号里的内容(不在单引号里时)递归拆成独立命令,在外层命令中替换为空;
    heredoc 只跳过正文行,<< 所在行和正文之后的命令照常拆分。close 是命令替换的结束符。
    """
    parts, cur, quote, depth, pending = [], [], None, 0, []
    def flush():
        parts.append(''.join(cur))
        del cur[:]
    while i < len(text):
        char = text[i]
        if quote == "'":
            cur.append(char)
            quote = None if char == "'" else quote
            i += 1
            continue
        if char == '\\':
            if text[i + 1:i + 2] == '\n':       # 续行
                i += 2
            else:
                cur.append(text[i:i + 2])
                i += 2
            continue
        if text.startswith('$(', i) or (char == '`' and close != '`'):
            inner, i = scan(text, i + (2 if char == '$' else 1), ')' if char == '$' else '`')
            parts.extend(inner)
            continue
        if quote == '"':
            cur.append(char)
            quote = None if char == '"' else quote
            i += 1
            continue
        if char == close and (close == '`' or depth == 0):
            flush()
            return parts, i + 1
        if char in "\"'":
            quote = char
            cur.append(char)
        elif char == '#' and (not cur or cur[-1].isspace()):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        elif text.startswith('<<<', i):
            cur.append('<<<')
            i += 3
            continue
        elif text.startswith('<<', i):
            strip_tabs = text[i + 2:i + 3] == '-'
            word, i = heredoc_word(text, i + 2 + strip_tabs)
            pending.append((word, strip_tabs))
            continue
        elif char == '\n':
            flush()
            i = skip_heredocs(text, i + 1, pending)
            pending = []
            continue
        elif char in ';|&()':
            if close == ')' and char == '(':
                depth += 1
            elif close == ')' and char == ')':
                depth -= 1
            flush()
        else:
            cur.append(char)
        i += 1
    if quote or close:
        raise ValueError('引号或命令替换没有结束')
    flush()
    return parts, i


def commands(text):
    """拆成简单命令后交给 shlex 解析参数与转义;只看文字,不执行、不展开变量。
    单引号里的内容、heredoc 正文、注释都只是文字,不会产生脚本调用。"""
    try:
        parts = scan(text)[0]
    except ValueError:
        return []
    out = []
    for part in parts:
        try:
            out.append(shlex.split(part))
        except ValueError:
            continue
    return out


def invocation(text, name):
    for words in commands(text):
        while words and (re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0]) or
                         words[0] in ('{', '}', '!', 'then', 'do', 'else')):
            words = words[1:]
        if not words or '--help' in words or '-h' in words:
            continue
        if os.path.basename(words[0]) in ('sh', 'bash', 'zsh', 'dash', 'ksh'):
            words = words[1:]
            while words and words[0].startswith('-'):
                option, words = words[0], words[1:]
                if option == '--':
                    break
                if (option in ('-', '--noexec', '--command') or
                        (not option.startswith('--') and any(c in option[1:] for c in 'ncs'))):
                    words = []
                    break
                if option == '-o':
                    if not words or words[0] == 'noexec':
                        words = []
                        break
                    words = words[1:]
        if words and os.path.basename(words[0]) == name:
            return words[1:]
    return None


if __name__ == '__main__':
    args = invocation(sys.stdin.read(), sys.argv[1])
    if args is None:
        sys.exit(1)
    if args:
        print(args[0])
