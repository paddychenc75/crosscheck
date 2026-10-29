#!/usr/bin/env python3
"""describe_facts.py — 为 PR/MR 描述整理改动的事实,不调用模型、不下结论。

用法: python3 describe_facts.py <dir>     dir 是 collect.sh 输出的材料目录

在 review 的 analysis_facts.py(代码块编号、符号、调用方)之上补描述要用的几项,写 <dir>/describe-facts.json;
review 自己的 analysis-facts.json 不变:
  hunks、symbols  同 analysis_facts.py,另外把 collect-diff 移进 skipped.patch 的测试文件也编进代码块;
                  公开符号(含只改实现的)都查调用方(import、require、再导出的行不算调用),另列提到它的测试文件(tests;没查过是 null)
  attention       影响面里的迁移、配置键和配置文件、新环境变量、依赖,每条带 change(added / removed / modified)
  entrypoints     entrypoints.py 认出的入口:changes(本次增删改的路由、命令行、定时任务、事件)、
                  triggers(和改动有关、能用来触发的入口,含改动行里的配置开关)、cases(新增用例名)、
                  assertions(新增断言原文)、command(项目测试命令)
相关测试只是按名字搜到的文件,没有确认执行情况。出错时尽量写出已有的部分,并在 limits 里注明。
"""
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "review", "scripts"))
sys.path.insert(0, HERE)
import analysis_facts as af  # noqa: E402
import entrypoints as ep  # noqa: E402

MIGRATION = re.compile(r'(^|/)(migrations?|migrate|db/migrate|alembic/versions|flyway|liquibase|changelogs?)/|'
                       r'(^|/)V\d+[_.\d]*__[^/]*\.sql$|\.sql$', re.I)
ENV_USES = [re.compile(p) for p in (
    r'process\.env\.([A-Z][A-Z0-9_]+)', r'process\.env\[\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'import\.meta\.env\.([A-Z][A-Z0-9_]+)', r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'os\.environ(?:\.get)?[\[(]\s*["\']([A-Z][A-Z0-9_]+)["\']', r'System\.getenv\(\s*"([A-Z][A-Z0-9_]+)"',
    r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]+)"')]
CFG_ENV = re.compile(r'\$\{([A-Z][A-Z0-9_]+)(?::[^}]*)?\}')
SWITCH = {'true': True, 'on': True, 'yes': True, 'enabled': True, 'false': False, 'off': False, 'no': False, 'disabled': False}
IMPORT = re.compile(r'(import\b|from\s+\S+\s+import\b|export\s*(\*|\{[^}]*\})\s*(from\b|;?$)|using\s|use\s|#include\b|'
                    r'(const|let|var)\s+[\w${},\s:]+=\s*require\(\s*["\'][^"\']+["\']\s*\)\s*;?$)')
MAX_CALLER_FILES = 20   # 在多少个调用方文件里找入口


def count_config_keys(text):
    return sum(1 for line in (text or '').split('\n')
               if (m := af.CFG_KEY.match(line)) and m.group(3).strip() and not line.lstrip().startswith(('#', '-')))


class Facts(af.Facts):
    def __init__(self, rd):
        super().__init__(rd)
        # 测试文件被 collect-diff 移进了 skipped.patch,描述里也要列出来
        self.files += af.parse_patch(af.read_text(os.path.join(rd, 'skipped.patch')))
        self.skipped = [tuple(l.split('\t', 1)) for l in af.read_text(os.path.join(rd, 'skipped.txt')).splitlines() if '\t' in l]
        self.changed_paths = {f['path'] for f in self.files} | {p for p, _ in self.skipped}
        self.ranges = {}
        for f in self.files:
            self.ranges.setdefault(f['path'], []).extend(af.hunk_range(h) for h in f['hunks'])

    @staticmethod
    def symbol(*args, **kwargs):
        return dict(af.Facts.symbol(*args, **kwargs), tests=None)

    # ----- callers 和相关测试 -----
    def add_callers(self, symbols):
        order = {'signature_changed': 0, 'removed': 1, 'added': 2, 'body_only': 3}
        public = [s for s in symbols if s['exported'] is not False and self.searchable(s)
                  and not (s['kind'] == 'config_key' and s['change'] == 'body_only')]
        candidates = sorted((s for s in public if s['change'] in order), key=lambda s: order[s['change']])
        if len(candidates) > af.MAX_SYMBOLS:
            self.limits.append('改动的公开符号有 %d 个,只查了前 %d 个的调用方' % (len(candidates), af.MAX_SYMBOLS))
            candidates = candidates[:af.MAX_SYMBOLS]
        try:
            hits = self.grep(sorted({s['name'] for s in candidates}), time.time() + af.GREP_BUDGET)
        except TimeoutError:
            self.limits.append('查调用方超过 %d 秒,已停止;调用方和相关测试没有列出' % af.GREP_BUDGET)
            return
        except OSError as e:
            self.limits.append('git grep 失败:%s' % e)
            return
        for s in candidates:
            defs, callers, tests = set(), [], {}
            for path, line, text in hits.get(s['name'], []):
                if path == s['file'] and line == s['line']:
                    continue
                d = af.match_def(s['lang'], text) if s['lang'] != 'config' else None
                if d and d[0] == s['name'] and af.lang_of(path) == s['lang']:
                    defs.add(path)
                elif af.is_test(path):
                    tests.setdefault(path, path in self.changed_paths)
                elif not self.is_import(text, s['name']):
                    callers.append((path, line))
            s['tests'] = [{'file': p, 'changed': c} for p, c in sorted(tests.items())]
            s['ambiguous'] = bool(defs - {s['file']}) and s['change'] != 'removed'
            s['callers_total'] = len(callers)
            if len(callers) > af.MAX_HITS:
                s['skipped'] = 'too_many_hits'
                continue
            s['callers'] = [{'file': p, 'line': ln, 'in_diff': self.in_diff(p, ln)} for p, ln in callers[:af.MAX_LISTED]]
            s['callers_truncated'] = len(callers) > af.MAX_LISTED

    @staticmethod
    def is_import(text, name):
        """import / require / 再导出的那一行,以及多行 import 里单独占一行的名字:它们提到了函数,但不是调用。"""
        s = text.strip()
        return bool(IMPORT.match(s)) or s.rstrip(',;') == name

    # ----- attention -----
    def attention(self, symbols):
        out = []
        for f in self.files:
            p, st = f['path'], f['status']
            change = {'added': 'added', 'deleted': 'removed'}.get(st, 'modified')
            if af.lang_of(p) == 'config' and st in ('added', 'deleted') and not af.DEP_FILES.search(p):
                n = count_config_keys(self.repo.new(p) if st == 'added' else self.old_text(f))
                out.append({'type': 'config_file', 'change': change, 'name': p, 'file': p, 'line': None, 'keys': n,
                            'detail': '%s配置文件 %s%s' % ('新增' if st == 'added' else '删除', p, '(%d 个键)' % n if n else '')})
            if MIGRATION.search(p) and not af.is_test(p):
                out.append({'type': 'migration', 'change': change, 'name': p, 'file': p, 'line': None,
                            'detail': '%s %s' % ({'added': '新增迁移', 'removed': '删除迁移'}.get(change, '修改迁移'), p)})
            if af.DEP_FILES.search(p):
                parts = self.dep_parts(f)
                out.append({'type': 'dependency', 'change': change, 'name': p, 'file': p, 'line': None, 'parts': parts,
                            'detail': '依赖变化(%s):%s' % (p, ';'.join(parts)) if parts else self.dep_lines(f)})
            out += self.new_env_vars(f)
        listed = {a['file'] for a in out if a['type'] == 'dependency'}
        out += [{'type': 'dependency', 'change': 'modified', 'name': p, 'file': p, 'line': None, 'parts': [], 'detail': 'lock 文件变化 %s' % p}
                for p, r in self.skipped if r == 'lockfile' and p not in listed]
        verb = {'added': '新增', 'removed': '删除', 'body_only': '修改'}
        for s in symbols:
            if s['kind'] == 'config_key' and s['change'] in verb:
                out.append({'type': 'config_key', 'change': 'modified' if s['change'] == 'body_only' else s['change'],
                            'name': s['name'], 'file': s['file'], 'line': s['line'],
                            'detail': '%s配置键 %s' % (verb[s['change']], s['name'])})
        return out

    def new_env_vars(self, f):
        """改动行里新用到的环境变量;改动前同一文件里已经用过的不算。"""
        plus, minus, cfg = {}, set(), af.lang_of(f['path']) == 'config'
        for h in f['hunks']:
            for tag, text, _, new_no in h['lines']:
                found = [m.group(1) for rx in ENV_USES for m in rx.finditer(text)] + (CFG_ENV.findall(text) if cfg else [])
                for v in found:
                    if tag == '+':
                        plus.setdefault(v, new_no)
                    elif tag == '-':
                        minus.add(v)
        old = self.old_text(f) or ''
        return [{'type': 'env_var', 'change': 'added', 'name': v, 'file': f['path'], 'line': line, 'detail': '新环境变量 %s' % v}
                for v, line in plus.items() if v not in minus and v not in old]

    def dep_parts(self, f):
        """package.json 里增删改的依赖,每项一句;其他依赖文件返回空列表。"""
        p = f['path']
        if p.rsplit('/', 1)[-1] != 'package.json':
            return []
        try:
            old = json.loads(self.old_text(f) or '{}')
            new = json.loads(self.repo.new(p) or '{}') if f['status'] != 'deleted' else {}
        except ValueError:
            return []
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
        return parts

    @staticmethod
    def dep_lines(f):
        adds = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '+')
        dels = sum(1 for h in f['hunks'] for t, *_ in h['lines'] if t == '-')
        return '依赖文件变化 %s(+%d/−%d 行)' % (f['path'], adds, dels)

    # ----- entrypoints -----
    def entrypoints(self, symbols):
        changes, triggers, cases, asserts = [], [], [], []
        code = [s for s in symbols if s['kind'] != 'config_key' and not af.is_test(s['file'])]
        touched = {s['name'] for s in code if s['change'] != 'removed'}
        for f in self.files:
            p, lang = f['path'], af.lang_of(f['path'])
            lines = [x for h in f['hunks'] for x in h['lines']]
            if af.is_test(p):
                cases += [{'name': n, 'file': p, 'line': ln} for n, ln in ep.test_cases(lang, lines)]
                asserts += [{'text': t, 'file': p, 'line': ln} for t, ln in ep.assertions(lines)]
                continue
            if lang is None or af.DEP_FILES.search(p):
                continue
            if lang == 'config' and f['status'] != 'deleted':
                triggers += self.switches(f)
            new = ep.scan(lang, self.repo.new(p) or '') if f['status'] != 'deleted' else []
            old = ep.scan(lang, self.old_text(f) or '') if f['status'] != 'added' else []
            plus = {n for t, _, _, n in lines if t == '+'}
            old_keys, new_keys = {(e['kind'], e['label']) for e in old}, {(e['kind'], e['label']) for e in new}
            for e in new:
                change = 'added' if (e['kind'], e['label']) not in old_keys else 'modified' if e['line'] in plus else None
                if change:
                    changes.append(dict(e, file=p, change=change))
                if change or self.in_diff(p, e['line']) or e['handler'] in touched:
                    triggers.append(dict(e, file=p))
            # 删掉的入口只有旧版的行号,对不上被审查的这一版,不给行号
            changes += [dict(e, file=p, line=None, change='removed') for e in old if (e['kind'], e['label']) not in new_keys]
        triggers += self.caller_entries(code)
        seen, uniq = set(), []
        for t in triggers:
            if (t['kind'], t['label'], t['file']) not in seen:
                seen.add((t['kind'], t['label'], t['file']))
                uniq.append(t)
        return {'changes': changes, 'triggers': uniq, 'cases': cases, 'assertions': asserts,
                'command': ep.test_command(self.repo.new)}

    def switches(self, f):
        """改动行里值是开关(true / false、on / off……)的配置键。"""
        p, text = f['path'], self.repo.new(f['path']) or ''
        lines, out = text.split('\n'), []
        for h in f['hunks']:
            for tag, _, _, n in h['lines']:
                key = af.config_key_at(text, n) if tag == '+' else None
                m = af.CFG_KEY.match(lines[n - 1]) if key else None
                value = m.group(3).strip().rstrip(',').strip('"\'').lower() if m else ''
                if value in SWITCH:
                    out.append({'kind': 'config', 'label': key, 'value': value, 'on': SWITCH[value], 'file': p, 'line': n, 'handler': None})
        return out

    def caller_entries(self, code):
        """diff 外的调用方那一行本身是入口(如 router.post('/orders', createOrder)):也算触发方式。"""
        want = {}
        for s in code:
            if s['change'] == 'removed':
                continue
            for c in s.get('callers') or []:
                if not c['in_diff']:
                    want.setdefault(c['file'], set()).add(c['line'])
        out = []
        for p in sorted(want)[:MAX_CALLER_FILES]:
            out += [dict(e, file=p) for e in ep.scan(af.lang_of(p), self.repo.new(p) or '') if e['line'] in want[p]]
        return out

    def run(self):
        data = super().run()
        if any(r in ('lockfile', 'generated', 'binary') for _, r in self.skipped):
            self.limits.append('lock、生成和二进制文件的行数没有计入')
        data['scope']['files'] = len(self.changed_paths)
        data.update(attention=self.attention(data['symbols']), entrypoints=self.entrypoints(data['symbols']), limits=self.limits)
        return data


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        sys.stderr.write('用法: describe_facts.py <dir>\n')
        return 2
    rd = argv[1]
    try:
        facts = Facts(rd)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write('describe_facts: 无法读取材料目录(%s)\n' % e)
        return 1
    try:
        data = facts.run()
    except Exception as e:  # noqa: BLE001 尽量写出已有的部分
        facts.limits.append('整理事实时出错(%s: %s),结果不完整' % (type(e).__name__, e))
        data = {'version': 1, 'scope': {'diff': facts.diff_name, 'files': len(facts.changed_paths), 'added': 0, 'deleted': 0},
                'hunks': [], 'symbols': [], 'attention': [], 'entrypoints': {}, 'method': af.METHOD, 'limits': facts.limits}
    with open(os.path.join(rd, 'describe-facts.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    ent = data.get('entrypoints') or {}
    print('describe_facts: hunks=%d symbols=%d attention=%d entrypoints=%d' % (
        len(data['hunks']), len(data['symbols']), len(data['attention']), len(ent.get('changes', [])) + len(ent.get('triggers', []))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
