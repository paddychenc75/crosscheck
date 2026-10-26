"""审查上下文的哈希:规则、意图、审查设置、模型或插件本身变了,增量缓存就不能用,要全量重审。

用法: python3 context_hash.py DIR    写出 DIR/context-hash.txt
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import crlib

RULE_FILES = re.compile(r'(^|/)(REVIEW\.md|CLAUDE\.md|AGENTS(\.override)?\.md|GEMINI\.md|CONVENTIONS\.md)$'
                        r'|(^|/)(\.claude|\.cursor)/|^\.github/(instructions/|copilot-instructions\.md)|^\.(cursorrules|windsurfrules)$')


def review_context(directory):
    d = Path(directory)
    meta = crlib.read_kv(str(d / 'meta.txt'))
    src = crlib.Source(meta['repo_root'], meta.get('base_sha') if meta.get('mode') != 'local' else None)
    rule_files = {f: src.read(f) for f in src.files() if RULE_FILES.search(f)}
    plugin = Path(__file__).resolve().parent.parent
    implementation = {str(f.relative_to(plugin)): f.read_text(encoding='utf-8')
                      for f in sorted(plugin.rglob('*')) if f.is_file() and f.suffix in ('.py', '.sh', '.md')}
    inputs = dict(project_rules=rule_files, implementation=implementation,
                  settings=crlib.load_json(str(d / 'settings.json'), {}),
                  models={k: meta.get(k, '') for k in ('finder_model', 'verifier_model')})
    for name in ('intent.md', 'description.md', 'discussions.json', 'context-notes.txt'):
        inputs[name] = (d / name).read_text(encoding='utf-8') if (d / name).exists() else ''
    if meta.get('store_dir'):
        inputs['feedback'] = crlib.feedback_entries(os.path.dirname(meta['store_dir']))
    text = json.dumps(inputs, ensure_ascii=False, sort_keys=True)
    (d / 'context-hash.txt').write_text(hashlib.sha256(text.encode('utf-8')).hexdigest() + '\n', encoding='utf-8')


if __name__ == '__main__':
    review_context(sys.argv[1])
