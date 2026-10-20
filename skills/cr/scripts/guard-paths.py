"""Read shell text, report a literal repository write target; never execute it.

This is an accidental-write guard, not a shell interpreter or a security boundary.
Variables, dynamically generated commands and arbitrary program effects require
host permissions as well as the final check-state snapshot.
"""
import os
import shlex
import subprocess
import sys

cwd = sys.argv[1] if len(sys.argv) > 1 else ""
if not cwd:
    sys.exit(0)
r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
if r.returncode:
    sys.exit(0)
root = os.path.realpath(r.stdout.strip())
try:
    lexer = shlex.shlex(sys.stdin.read(), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
except ValueError:
    sys.exit(0)

def repo_path(path):
    if not path or path.startswith(("-", "$")) or path.isdigit():
        return False
    resolved = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    return resolved == root or resolved.startswith(root + os.sep)

targets = []
command = True
for i, token in enumerate(tokens):
    if token in (";", "&&", "||", "|", "(", ")"):
        command = True
        continue
    if token in (">", ">>", ">|", "&>", "&>>", "<>") and i + 1 < len(tokens):
        targets.append(tokens[i + 1])
    if command:
        if "=" in token:
            continue
        command = False
        name = os.path.basename(token)
        # tee 的输出文件以及常见直接文件操作;读取命令和 stdout 不受影响。
        if name in ("tee", "touch", "rm", "mkdir", "rmdir", "cp", "mv", "install"):
            end = next((j for j in range(i + 1, len(tokens)) if tokens[j] in (";", "&&", "||", "|")), len(tokens))
            args = tokens[i + 1:end]
            if name in ("cp", "install"):
                args = args[-1:]
            targets.extend(args)
for path in targets:
    if repo_path(path):
        print(path)
        break
