"""只读规则来源：兼容 Git 规则包，接收宿主已取得的资料；不连接 MCP。"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import crlib


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def read_import(path):
    if not path:
        return [], []
    try:
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            raise ValueError('材料必须是 JSON 数组')
    except (OSError, ValueError) as e:
        return [], ['无法读取外部材料（%s）' % type(e).__name__]
    records, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError('条目必须是对象')
            for key in ('source', 'ref', 'purpose', 'status'):
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise ValueError('缺少 %s' % key)
            purpose = row['purpose']
            if purpose not in ('rules', 'context'):
                raise ValueError('purpose 应为 rules 或 context')
            status = {'完整': 'complete', '部分': 'partial', '失败': 'unavailable'}.get(row['status'], row['status'])
            if status not in ('complete', 'partial', 'unavailable'):
                raise ValueError('status 无效')
            text = row.get('text', '')
            if not isinstance(text, str) or (status == 'complete' and not text.strip()):
                raise ValueError('完整材料必须有原文')
            scope = row.get('scope', ['**'])
            if isinstance(scope, str):
                scope = [scope]
            if not isinstance(scope, list) or not scope or not all(isinstance(g, str) and g.strip() for g in scope):
                raise ValueError('scope 必须是非空路径模式列表')
            scope = [part for g in scope for part in crlib.expand_braces(g)]
            identity = (row['source'], row['ref'])
            if identity in seen:
                records = [x for x in records if (x['source'], x['ref']) != identity]
                raise ValueError('同一来源和文档重复，不能确认使用哪份正文')
            seen.add(identity)
            records.append(dict(source=row['source'], ref=row['ref'], purpose=purpose,
                                status=status, scope=scope, text=text, note=str(row.get('note', '')),
                                revision=str(row.get('revision', ''))))
        except ValueError as e:
            errors.append('材料第 %d 项：%s' % (i, e))
    return records, errors


def legacy_packs(settings):
    """只做读取，不将组织格式或发布操作扩散到其他来源。"""
    root = crlib.rules_dir()
    records, errors = [], []
    for pack in settings.get('extends') or []:
        # 项目不能利用包路径读取规则副本以外的文件。
        path = os.path.realpath(os.path.join(root, 'packs', pack, 'rules.md')) if root else ''
        base = os.path.realpath(os.path.join(root, 'packs')) if root else ''
        if not path or not path.startswith(base + os.sep):
            errors.append('组织规则包不可读取：%s' % pack)
            continue
        try:
            text = Path(path).read_text(encoding='utf-8')
        except (OSError, ValueError):
            errors.append('组织规则包缺失或不可读取：%s' % pack)
            continue
        records.append(dict(pack=pack, text=text))
    return records, errors


def freeze(directory, import_file=''):
    d = Path(directory)
    settings = crlib.load_json(str(d / 'settings.json'), {})
    if import_file and not os.path.isabs(import_file):
        import_file = os.path.join(crlib.read_kv(str(d / 'meta.txt')).get('repo_root', ''), import_file)
    imported, errors = read_import(import_file)
    legacy, legacy_errors = legacy_packs(settings)
    md = d / 'materials'
    md.mkdir(exist_ok=True)
    # 只清理我们生成的正文，防止 --out 重用目录时混入旧输入。
    for f in md.glob('doc-*.md'):
        f.unlink()
    for item in imported:
        name = 'doc-' + digest([item['source'], item['ref']])[:20] + '.md'
        item['file'] = 'materials/' + name
        (md / name).write_text(item['text'], encoding='utf-8')
    complete = not errors and not legacy_errors and all(x['status'] == 'complete' for x in imported)
    data = dict(records=imported, legacy=legacy, errors=errors + legacy_errors, complete=complete)
    crlib.save_json(str(md / 'manifest.json'), data)
    return data


def load(directory):
    return crlib.load_json(os.path.join(directory, 'materials', 'manifest.json'), {})


def review_context(directory):
    """材料变化就全量重审目标，不维护规则到文件的细粒度依赖。"""
    d = Path(directory)
    meta = crlib.read_kv(str(d / 'meta.txt'))
    snap = load(directory)
    src = crlib.Source(meta['repo_root'], meta.get('base_sha') if meta.get('mode') != 'local' else None)
    rule_files = {}
    names = re.compile(r'(^|/)(REVIEW\.md|CLAUDE\.md|AGENTS(\.override)?\.md|GEMINI\.md|CONVENTIONS\.md)$|(^|/)(\.cr|\.claude|\.cursor)/|^\.github/(instructions/|copilot-instructions\.md)|^\.(cursorrules|windsurfrules)$')
    for f in src.files():
        if names.search(f):
            rule_files[f] = src.read(f)
    plugin = Path(__file__).resolve().parent.parent
    implementation = {str(f.relative_to(plugin)): f.read_text(encoding='utf-8')
                      for f in sorted(plugin.rglob('*')) if f.is_file() and f.suffix in ('.py', '.sh', '.md')}
    inputs = dict(materials=snap, project_rules=rule_files, implementation=implementation,
                  settings=crlib.load_json(str(d / 'settings.json'), {}),
                  models={k: meta.get(k, '') for k in ('finder_model', 'verifier_model')})
    for name in ('intent.md', 'description.md', 'discussions.json', 'context-notes.txt'):
        inputs[name] = (d / name).read_text(encoding='utf-8') if (d / name).exists() else ''
    if meta.get('store_dir'):
        inputs['feedback'] = crlib.feedback_entries(os.path.dirname(meta['store_dir']), meta.get('repo_id'))
    # 保留最近一次未能复核的发现，不能在连续读取失败时丢失。
    store = Path(meta['store_dir']) if meta.get('store_dir') else None
    previous = []
    if store and (store / 'latest').is_file():
        stamp = (store / 'latest').read_text().strip()
        if re.fullmatch(r'[0-9-]+', stamp):
            for name in ('findings.json', 'unverified.json'):
                previous.extend(crlib.load_json(str(store / stamp / name), []))
    crlib.save_json(str(d / 'previous-findings.json'), previous)
    (d / 'context-hash.txt').write_text(digest(inputs) + '\n', encoding='utf-8')
    (d / 'context-complete.txt').write_text('yes\n' if snap.get('complete') else 'no\n', encoding='utf-8')


if __name__ == '__main__':
    freeze(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else '')
    review_context(sys.argv[1])
