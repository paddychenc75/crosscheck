#!/usr/bin/env python3
"""describe_facts.py — 为 PR/MR 描述整理改动的事实,不调用模型、不下结论。

用法: python3 describe_facts.py <dir>     dir 是 collect.sh 输出的材料目录

在 review 的 analysis_facts.py(代码块编号、符号、调用方)之上补描述要用的几项,写 <dir>/describe-facts.json;
review 自己的 analysis-facts.json 不变:
  hunks、symbols  同 analysis_facts.py,另外把 collect-diff 移进 skipped.patch 的测试文件也编进代码块;
                  每个代码块另带摘要:strings_added / strings_removed(改动行里新增、删除的字符串字面量,各最多 5 个),
                  资源目录里的文件 kind 是 resource(xcassets 的 Contents.json 不当配置文件,也不出配置键);
                  公开符号(含只改实现的)都查调用方(import、require、再导出的行不算调用),另列提到它的测试文件(tests;没查过是 null)
  attention       影响面里的迁移、配置键和配置文件、新环境变量、依赖,以及按路径认的工程配置(project_config:.plist、
                  .entitlements、.xcconfig、.pbxproj)、依赖文件(Podfile、Package.swift、pubspec.yaml、Cargo.toml、*.csproj 等)
                  和资源(resource:xcassets、lproj 等目录及 .strings、图片),每条带 change(added / removed / modified)
  coverage        检查范围:checked(按语言识别过的非测试文件数)、unchecked([{lang, files}],不在自动识别范围的语言)
  entrypoints     entrypoints.py 认出的入口:changes(本次增删改的路由、命令行、定时任务、事件)、
                  triggers(和改动有关、能用来触发的入口,含改动行里的配置开关)、cases(新增用例名)、
                  assertions(新增断言原文)、command(项目测试命令)
  batches         代码块超过 BATCH_HUNKS 个时才有:按目录分好的批次 [{id, dirs, hunks}],每批交给一个 analyst
相关测试只是按名字搜到的文件,没有确认执行情况。出错时尽量写出已有的部分,并在 limits 里注明。
hunk_lines(dir) 给 analysis_check.py 核对锚点用:{代码块编号: {file, lines}},lines 是改动行(+/−)的原文。
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
# 与语言无关、按路径认的工程配置、依赖和资源(Swift、Dart、Rust、C# 等不在语言识别范围的工程也适用)
PROJECT_CONFIG = re.compile(r'\.(plist|entitlements|xcconfig|pbxproj)$', re.I)
DEP_EXTRA = re.compile(r'(^|/)(Podfile(\.lock)?|Package\.swift|Package\.resolved|pubspec\.(yaml|lock)|Cargo\.(toml|lock)|'
                       r'Cartfile(\.resolved)?|[^/]+\.csproj|packages\.config)$')
RESOURCE_DIR = re.compile(r'^(.*?[^/]+\.(?:xcassets|lproj|bundle))/|^(.*?(?:^|/)res/(?:drawable|mipmap|values|layout|raw|font)[^/]*)/')
RESOURCE_FILE = re.compile(r'\.(strings|stringsdict|xcstrings|xib|storyboard|png|jpe?g|gif|webp|svg|ttf|otf|mp3|mp4)$', re.I)
# 不在语言识别范围的源码扩展名 → 读者认得的语言名;没列出的用扩展名本身
LANG_NAME = {'.swift': 'Swift', '.m': 'Objective-C', '.mm': 'Objective-C', '.h': 'C/Objective-C 头文件', '.dart': 'Dart',
             '.rs': 'Rust', '.cs': 'C#', '.rb': 'Ruby', '.php': 'PHP', '.c': 'C', '.cc': 'C++', '.cpp': 'C++', '.hpp': 'C++',
             '.scala': 'Scala', '.sh': 'Shell', '.lua': 'Lua', '.sql': 'SQL', '.gradle': 'Gradle', '.groovy': 'Groovy'}
STRING = re.compile(r'"((?:[^"\\\n]|\\.){2,80})"|\'((?:[^\'\\\n]|\\.){2,80})\'')
MAX_STRINGS = 5
BATCH_HUNKS = 40        # 代码块超过这个数就按目录分批,每批交给一个 analyst


def resource_of(p):
    """资源文件归到的容器(xcassets、lproj 等目录,其他资源文件就是它自己);不是资源返回 None。"""
    m = RESOURCE_DIR.match(p)
    if m:
        return m.group(1) or m.group(2)
    return p if RESOURCE_FILE.search(p) else None


# 多语言文案只认文案文件本身;同目录里的 storyboard、colors.xml、styles.xml、图片等是其他资源
I18N_FILE = re.compile(r'\.strings(?:dict)?$|\.xcstrings$|(?:^|/)res/values[^/]*/(?:strings|plurals|arrays)[^/]*\.xml$', re.I)
# Android 的 values-night、values-v21 这类不是语言;iOS 的 Base.lproj 也不是
LANG_DIR = re.compile(r'([^/]+)\.lproj(?:/|$)|/values-((?!car(?:/|$))[a-z]{2,3}(?:-r[A-Z]{2})?|b\+[A-Za-z+]+)(?:/|$)')


def is_i18n(p):
    """多语言文案文件(.strings、.stringsdict、.xcstrings、Android 的 strings/plurals/arrays.xml);描述里由脚本按文件数汇总,不逐条写。"""
    return bool(I18N_FILE.search(p or ''))


def lang_of(p):
    """从路径认语言目录(en.lproj → en,values-zh-rCN → zh-rCN);Base.lproj、values-night 这类不是语言,返回 None。"""
    m = LANG_DIR.search(p or '')
    lang = (m.group(1) or m.group(2)) if m else None
    return None if lang == 'Base' else lang


def path_rule(p):
    """按路径认的类别:resource / project_config / dependency,都不是返回 None。"""
    if resource_of(p):
        return 'resource'
    if PROJECT_CONFIG.search(p):
        return 'project_config'
    if DEP_EXTRA.search(p):
        return 'dependency'
    return None


def lang_name(p):
    ext = os.path.splitext(p.rsplit('/', 1)[-1])[1].lower()
    return LANG_NAME.get(ext, ext or p.rsplit('/', 1)[-1])


def strings_of(lines, tag):
    """改动行里的字符串字面量;另一侧也有的不算(只是挪了位置)。"""
    def found(t):
        return dict.fromkeys(m.group(1) or m.group(2) for _, text, *_ in [x for x in lines if x[0] == t] for m in STRING.finditer(text))
    other = found('-' if tag == '+' else '+')
    return [s for s in found(tag) if s not in other][:MAX_STRINGS]


def patch_files(rd):
    """diff 在前、skipped.patch(测试文件)在后,和 Facts 的代码块编号顺序一致。"""
    name = 'diff.full.patch' if os.path.exists(os.path.join(rd, 'diff.full.patch')) else 'diff.patch'
    return af.parse_patch(af.read_text(os.path.join(rd, name))) + af.parse_patch(af.read_text(os.path.join(rd, 'skipped.patch')))


def hunk_lines(rd):
    out, n = {}, 0
    for f in patch_files(rd):
        for h in f['hunks']:
            n += 1
            out['H%d' % n] = {'file': f['path'], 'lines': [text for tag, text, *_ in h['lines'] if tag in '+-']}
    return out


def batches(hunks):
    """按目录(前两级)把代码块分批,每批不超过 BATCH_HUNKS 个;代码块不多时不分批。"""
    if len(hunks) <= BATCH_HUNKS:
        return []
    groups = {}
    for h in hunks:
        groups.setdefault('/'.join(h['file'].split('/')[:-1][:2]) or '.', []).append(h['id'])
    out, cur, dirs = [], [], []
    for d, ids in sorted(groups.items()):
        while ids:
            if len(cur) >= BATCH_HUNKS:
                out.append({'dirs': dirs, 'hunks': cur})
                cur, dirs = [], []
            take = ids[:BATCH_HUNKS - len(cur)]
            ids = ids[len(take):]
            cur += take
            if d not in dirs:
                dirs.append(d)
    if cur:
        out.append({'dirs': dirs, 'hunks': cur})
    return [dict(b, id='B%d' % i) for i, b in enumerate(out, 1)]


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
        out, resources = [], {}
        for f in self.files:
            p, st = f['path'], f['status']
            change = {'added': 'added', 'deleted': 'removed'}.get(st, 'modified')
            rule = path_rule(p) if not af.is_test(p) else None
            if rule == 'resource':
                resources.setdefault(resource_of(p), []).append((p, change))
                continue
            if rule == 'project_config':
                out.append({'type': 'project_config', 'change': change, 'name': p, 'file': p, 'line': None,
                            'detail': '%s工程配置 %s' % ({'added': '新增', 'removed': '删除'}.get(change, '修改'), p)})
                continue
            if rule == 'dependency':
                out.append({'type': 'dependency', 'change': change, 'name': p, 'file': p, 'line': None, 'parts': [],
                            'detail': self.dep_lines(f)})
                continue
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
        in_patch = {f['path'] for f in self.files}
        for p, r in self.skipped:
            if r == 'binary' and resource_of(p) and p not in in_patch:
                resources.setdefault(resource_of(p), []).append((p, 'modified'))
        for p, entries in resources.items():
            changes = {c for _, c in entries}
            change = changes.pop() if len(changes) == 1 else 'modified'
            out.append({'type': 'resource', 'change': change, 'name': p, 'file': p, 'line': None, 'files': len(entries),
                        'paths': [{'path': f, 'change': c} for f, c in entries],   # 容器里实际改的文件,影响面据此分类
                        'detail': '资源 %s(%d 个文件)' % (p, len(entries))})
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
            if lang is None or af.DEP_FILES.search(p) or path_rule(p):
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

    def coverage(self):
        """检查范围:按语言识别过的非测试文件数,和不在识别范围的源码文件(按语言名计数)。
        测试、文档图片、生成文件、依赖文件和按路径认的工程配置、资源不算在两边。"""
        checked, unchecked = 0, {}
        for f in self.files:
            p = f['path']
            if not f['hunks'] or af.is_test(p) or self.is_generated(p) or af.DEP_FILES.search(p) or path_rule(p):
                continue
            if af.lang_of(p) is not None:
                checked += 1
            elif not p.lower().endswith(af.TEXT_EXT):
                unchecked[lang_name(p)] = unchecked.get(lang_name(p), 0) + 1
        return {'checked': checked, 'unchecked': [{'lang': k, 'files': n} for k, n in sorted(unchecked.items(), key=lambda x: -x[1])]}

    def run(self):
        data = super().run()
        if any(r in ('lockfile', 'generated', 'binary') for _, r in self.skipped):
            self.limits.append('lock、生成和二进制文件的行数没有计入')
        data['scope']['files'] = len(self.changed_paths)
        # 按路径认的资源、工程配置、依赖文件不出配置键(如 xcassets 的 Contents.json、pubspec.yaml 的依赖项)
        data['symbols'] = [s for s in data['symbols'] if not (s['kind'] == 'config_key' and path_rule(s['file']))]
        lines = {h['id']: h['lines'] for f in self.files for h in f['hunks']}
        for h in data['hunks']:
            if path_rule(h['file']) == 'resource' and h['kind'] != 'test':
                h['kind'] = 'resource'
            h['strings_added'] = strings_of(lines.get(h['id'], []), '+')
            h['strings_removed'] = strings_of(lines.get(h['id'], []), '-')
        data.update(attention=self.attention(data['symbols']), coverage=self.coverage(),
                    entrypoints=self.entrypoints(data['symbols']), limits=self.limits)
        if (b := batches(data['hunks'])):
            data['batches'] = b
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
