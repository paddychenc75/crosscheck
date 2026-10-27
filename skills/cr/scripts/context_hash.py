"""审查上下文的哈希:规则、用户给的意图、审查设置、审查范围、模型或插件本身变了,增量缓存就不能用,要全量重审。

只算会改变"怎么审"的输入。提交说明、MR 描述和讨论、需求单和需求文档、项目里的判断记录不算:它们随每次
提交或表态都会变,算进来增量就几乎不会触发;这些内容照样交给角色阅读。插件里只服务 MR 说明和规则沉淀的
文件(MR_NOTE_ONLY)也不算,改它们不影响 CR 报告。

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
import project_rules

MR_NOTE_ONLY = re.compile(r'(^|/)(analysis_facts\.py|analysis_check\.py|mr_note\.py|mr-note\.sh|sources\.py'
                          r'|mine[-_][^/]*|rules[-_]write[^/]*|roles/analyst\.md|references/mr-note\.md)$')
RULE_FILES = re.compile(r'(^|/)(REVIEW\.md|CLAUDE\.md|AGENTS(\.override)?\.md|GEMINI\.md|CONVENTIONS\.md)$'
                        r'|(^|/)(\.claude|\.cursor)/|^\.agents/rules/|^\.github/(instructions/|copilot-instructions\.md)|^\.(cursorrules|windsurfrules)$')


def review_context(directory):
    d = Path(directory)
    meta = crlib.read_kv(str(d / 'meta.txt'))
    src = crlib.Source(meta['repo_root'], meta.get('base_sha') if meta.get('mode') != 'local' else None)
    rule_files = {f: src.read(f) for f in src.files() if RULE_FILES.search(f)}
    for name in project_rules.include_names(rule_files.get('REVIEW.md') or ''):
        rule_files[name] = src.read(name)
    plugin = Path(__file__).resolve().parent.parent
    implementation = {str(f.relative_to(plugin)): f.read_text(encoding='utf-8')
                      for f in sorted(plugin.rglob('*')) if f.is_file() and f.suffix in ('.py', '.sh', '.md')
                      and not MR_NOTE_ONLY.search(str(f.relative_to(plugin)))}
    docs = [x.strip() for x in ((d / 'doc-files.txt').read_text(encoding='utf-8') if (d / 'doc-files.txt').exists() else '').split('\n') if x.strip()]
    if not docs and meta.get('plan_file'):
        docs = [meta['plan_file']]

    def doc_text(p):
        path = Path(p if os.path.isabs(p) else os.path.join(meta['repo_root'], p))
        return path.read_text(encoding='utf-8', errors='replace') if path.is_file() else ''
    inputs = dict(project_rules=rule_files, implementation=implementation,
                  settings=crlib.load_json(str(d / 'settings.json'), {}),
                  models={k: meta.get(k, '') for k in ('finder_model', 'verifier_model')},
                  scope={k: meta.get(k, '') for k in ('with_tests', 'only')},
                  intent_arg=(d / 'intent-arg.txt').read_text(encoding='utf-8') if (d / 'intent-arg.txt').exists() else '',
                  plan={p: doc_text(p) for p in docs})
    text = json.dumps(inputs, ensure_ascii=False, sort_keys=True)
    (d / 'context-hash.txt').write_text(hashlib.sha256(text.encode('utf-8')).hexdigest() + '\n', encoding='utf-8')


if __name__ == '__main__':
    review_context(sys.argv[1])
