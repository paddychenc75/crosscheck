"""项目规则的装配、维护和采集回归；只使用临时仓库和假 GitLab。"""
import json
import os
from pathlib import Path
import sys
import subprocess

import test_crosscheck as fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import project_rules


class ProjectRulesTests(fixture.Base):
    run_pipeline = fixture.TestPipeline.run_pipeline

    def repo_with_changes(self, extra=None):
        files = {'a.py': 'old\n', 'src/a.py': 'old\n', 'src/a.ts': 'old\n', 'ios/A.swift': 'old\n', 'android/A.kt': 'old\n'}
        files.update(extra or {})
        repo = self.make_repo('app', files)
        self.git(repo, 'switch', '-qc', 'feature')
        self.commit(repo, {p: 'new\n' for p in files if not project_rules.kind(p)}, 'change')
        return repo

    def collect(self, repo):
        rd = self.review_dir(self.run_script('collect-diff.sh', cwd=repo).stdout)
        self.run_script('collect-rules.sh', rd)
        return rd

    def test_native_paths_depth_braces_exclusion_and_legacy_scope(self):
        repo = self.repo_with_changes({
            '.claude/rules/root.md': '---\npaths: ["*.py"]\n---\nRoot only\n',
            '.claude/rules/all.md': '---\npaths: ["**/*.py"]\n---\nAll Python\n',
            '.github/instructions/root.instructions.md': '---\napplyTo: "*.py"\n---\nRoot only\n',
            '.github/instructions/all.instructions.md': '---\napplyTo: "**/*.py,src/*.{ts,tsx}"\n---\nReview\n',
            '.github/instructions/excluded.instructions.md': '---\napplyTo: "**"\nexcludeAgent: "code-review"\n---\nCoding only\n',
            '.cursor/rules/mobile.mdc': '---\nglobs: "ios/**/*.swift,android/**/*.kt"\n---\nMobile\n',
            'REVIEW.md': '- Python rule <!-- cr: id=PRJ-001 scope=*.py -->\n',
        })
        rd = self.collect(repo)
        units = {r['file']: r for r in self.load(Path(rd, 'rules-index.json'))}
        self.assertEqual(units['.claude/rules/root.md']['applies_files'], ['a.py'])
        self.assertEqual(units['.github/instructions/root.instructions.md']['applies_files'], ['a.py'])
        self.assertEqual(set(units['.claude/rules/all.md']['applies_files']), {'a.py', 'src/a.py'})
        self.assertEqual(set(units['.github/instructions/all.instructions.md']['applies_files']), {'a.py', 'src/a.py', 'src/a.ts'})
        self.assertEqual(set(units['.cursor/rules/mobile.mdc']['applies_files']), {'ios/A.swift', 'android/A.kt'})
        self.assertEqual(set(units['REVIEW.md']['applies_files']), {'a.py', 'src/a.py'})
        self.assertNotIn('.github/instructions/excluded.instructions.md', units)
        self.assertIn('excludeAgent: code-review', self.run_script('report-lines.sh', rd).stdout)

    def test_wrong_scope_does_not_silently_become_global(self):
        repo = self.repo_with_changes({'.claude/rules/bad.md': '---\npath: src/**\n---\nBad scope\n'})
        rd = self.collect(repo)
        self.assertEqual(self.load(Path(rd, 'rules-index.json')), [])
        self.assertFalse(self.load(Path(rd, 'materials/manifest.json'))['complete'])
        lint = self.run_script('rules-lint.sh', '--repo', repo, check=False)
        self.assertEqual(lint.returncode, 1)
        self.assertIn('paths', lint.stdout)

    def test_unreadable_base_never_uses_worktree(self):
        repo = self.repo_with_changes({'REVIEW.md': '- working tree rule\n'})
        rd = self.collect(repo)
        meta = Path(rd, 'meta.txt').read_text().replace('mode=local', 'mode=range')
        import re
        meta = re.sub(r'^base_sha=.*$', 'base_sha=' + 'f' * 40, meta, flags=re.M)
        Path(rd, 'meta.txt').write_text(meta)
        r = self.run_script('collect-rules.sh', rd, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('不回退工作区', r.stderr)
        r = self.run_script('settings.sh', 'show', repo, 'f' * 40, check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_review_body_severity_survives_aggregation_with_fixed_threshold(self):
        repo = self.repo_with_changes({'REVIEW.md': '## 严重程度\n- 订单审计记录缺失按重要问题报告。\n',
                                       '.claude/rules/audit.md': '- 订单操作必须记录审计事件。\n'})
        rd = self.collect(repo)
        unit = next(x for x in self.load(Path(rd, 'rules-index.json')) if x['file'].endswith('audit.md'))
        cand = dict(file='src/a.ts', line=1, category='rules', rule_id=unit['unit'],
                    rule_quote='订单操作必须记录审计事件。', severity='major', summary='审计缺失', failure_scenario='订单未记录')
        final, _ = self.run_pipeline(rd, [cand], {'审计缺失': (90, [{'file': 'src/a.ts', 'lines': '1'}])})
        self.assertEqual(final['groups']['rules'][0]['severity'], 'major')
        final, _ = self.run_pipeline(rd, [cand], {'审计缺失': (79, [{'file': 'src/a.ts', 'lines': '1'}])})
        self.assertEqual(final['groups']['rules'], [])

    def test_native_maintenance_preserves_frontmatter_and_refuses_widening(self):
        name = '.claude/rules/payments.md'
        original = '---\npaths: ["src/payments/**"]\n---\n# Money\n\n- Use Money.\n'
        repo = self.make_repo('app', {name: original, 'src/payments/a.ts': 'x\n'})
        ops = Path(self.tmp, 'ops.json')
        preview = Path(self.tmp, 'preview.json')
        def run(row, yes=False):
            ops.write_text(json.dumps([row]))
            return self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--preview', str(preview),
                                   *(['--yes'] if yes else []), check=False)
        row = dict(op='replace', file=name, old_text='- Use Money.', text='- Use Money for all totals.',
                   paths={'match': ['src/payments/a.ts'], 'miss': ['src/refunds/a.ts']})
        self.assertEqual(run(row).returncode, 0)
        self.assertEqual(Path(repo, name).read_text(), original)
        self.assertEqual(run(row, True).returncode, 0)
        self.assertEqual(Path(repo, name).read_text().split('# Money')[0], original.split('# Money')[0])
        self.assertNotIn('PRJ-', Path(repo, name).read_text())
        row = dict(op='add', file=name, text='Refund constraint',
                   frontmatter='---\npaths: ["src/**"]\n---',
                   paths={'match': ['src/refunds/a.ts'], 'miss': ['ios/A.swift']})
        self.assertNotEqual(run(row).returncode, 0)
        self.assertIn('不自动扩大', run(row).stderr)
        self.assertIn(name, self.run_script('rules-health.sh', '--repo', repo).stdout)

    def test_preview_rejects_changed_worktree_and_external_origin(self):
        repo = self.make_repo('app', {'REVIEW.md': '# Review\n'})
        ops = Path(self.tmp, 'ops.json'); preview = Path(self.tmp, 'preview.json')
        ops.write_text(json.dumps([dict(op='add', text='Do X', reason='Y')]))
        args = ['--repo', repo, '--ops', str(ops), '--preview', str(preview)]
        self.run_script('propose-rules.sh', *args)
        Path(repo, 'REVIEW.md').write_text('# changed\n')
        r = self.run_script('propose-rules.sh', *args, '--yes', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('重新预览', r.stderr)
        ops.write_text(json.dumps([dict(op='add', text='X', origin='organization')]))
        self.assertNotEqual(self.run_script('propose-rules.sh', *args, check=False).returncode, 0)

    def test_native_create_replace_remove_and_purpose_preserved(self):
        repo = self.make_repo('app', {'src/a.ts': 'x\n', 'other/b.py': 'x\n'})
        cases = [('.claude/rules/a.md', 'paths: ["src/**"]'),
                 ('.cursor/rules/a.mdc', 'globs: "src/**"'),
                 ('.github/instructions/a.instructions.md', 'applyTo: "src/**"\nexcludeAgent: code-review')]
        ops = Path(self.tmp, 'ops.json')
        for file, config in cases:
            with self.subTest(file=file):
                paths = {'match': ['src/a.ts'], 'miss': ['other/b.py']}
                row = dict(op='add', file=file, frontmatter='---\n'+config+'\n---', text='- Keep account boundaries.', paths=paths)
                ops.write_text(json.dumps([row]))
                self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes')
                old = Path(repo, file).read_text()
                ops.write_text(json.dumps([dict(op='replace', file=file, old_text='Keep account boundaries.', text='Keep per-user account boundaries.', paths=paths)]))
                self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes')
                current = Path(repo, file).read_text()
                self.assertEqual(current.split('---')[1], old.split('---')[1])
                ops.write_text(json.dumps([dict(op='remove', file=file, old_text='- Keep per-user account boundaries.', paths=paths)]))
                self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes')
                self.assertNotIn('Keep', Path(repo, file).read_text())

    def test_legacy_replace_updates_scope_and_source(self):
        repo = self.make_repo('app', {'REVIEW.md': '- Old. <!-- cr: id=PRJ-001 scope=src/** src=!1 severity=minor -->\n', 'src/a.py': 'x\n'})
        ops = Path(self.tmp, 'ops.json')
        ops.write_text(json.dumps([dict(op='replace', id='PRJ-001', text='New', scope='src/a.py', src='!2', severity='major')]))
        self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes')
        text = Path(repo, 'REVIEW.md').read_text()
        self.assertIn('scope=src/a.py', text)
        self.assertIn('src=!2', text)
        self.assertIn('severity=major', text)
        self.assertIn('id=PRJ-001', text)

    def test_native_scope_cannot_be_ignored_and_confirm_needs_no_rewrite(self):
        file = '.claude/rules/a.md'
        repo = self.make_repo('app', {file: '---\npaths: ["src/payments/**"]\n---\nPayments\n'})
        ops = Path(self.tmp, 'ops.json')
        row = dict(op='add', file=file, text='Refunds', scope='src/refunds/**',
                   paths={'match': ['src/payments/a.ts'], 'miss': ['ios/A.swift']})
        ops.write_text(json.dumps([row]))
        r = self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('不忽略 scope', r.stderr)
        ops.write_text(json.dumps([dict(op='confirm', file=file)]))
        r = self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes')
        self.assertIn('没有文件改动', r.stdout)
        self.assertEqual(self.git(repo, 'status', '--short'), '')

    def test_organization_copy_and_symlink_cannot_be_written(self):
        file = '.claude/rules/a.md'
        repo = self.make_repo('app', {file: 'Rule\n', 'src/a.ts': 'x\n'})
        ops = Path(self.tmp, 'ops.json')
        ops.write_text(json.dumps([dict(op='add', file=file, text='More', paths={'match': ['src/a.ts'], 'miss': []})]))
        self.config('rules_dir=' + str(Path(repo, '.claude')) + '\n')
        r = self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('组织规则副本只读', r.stderr)
        self.config('')
        Path(repo, file).unlink()
        outside = Path(self.tmp, 'outside.md'); outside.write_text('Original\n')
        Path(repo, file).symlink_to(outside)
        self.assertNotEqual(self.run_script('propose-rules.sh', '--repo', repo, '--ops', str(ops), '--yes', check=False).returncode, 0)
        self.assertEqual(outside.read_text(), 'Original\n')

    def test_relative_health_command_and_paragraph_rules(self):
        repo = self.make_repo('app', {'.claude/rules/plain.md': 'Always clear account caches on logout.\n', 'src/a.ts': 'x\n'})
        plugin = Path(fixture.SCRIPTS).parents[2]
        r = subprocess.run(['sh', 'skills/cr/scripts/rules-health.sh', '--repo', repo],
                           cwd=plugin, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('.claude/rules/plain.md', r.stdout)
        self.assertIn('Always clear', r.stdout)

    def test_mr_preview_rejects_new_pending_proposal_or_new_base(self):
        self.use_mock_glab()
        remote = self.bare('remote')
        repo = self.make_repo('app', {'REVIEW.md': '# Review\n'}, remote=remote)
        self.git(repo, 'push', '-q', 'origin', 'main')
        self.git(repo, 'remote', 'set-head', 'origin', 'main')
        endpoint = 'projects/:id/merge_requests?state=opened&labels=crosscheck-rule&per_page=100&page=1'
        self.mock_api(endpoint, [])
        ops = Path(self.tmp, 'ops.json'); ops.write_text(json.dumps([dict(op='add', text='Constraint', reason='Why')]))
        preview = Path(self.tmp, 'preview.json')
        args = ['--repo', repo, '--ops', str(ops), '--mr', '--preview', str(preview)]
        self.run_script('propose-rules.sh', *args)
        self.mock_api(endpoint, [{'iid': 9, 'title': 'Existing rule', 'description': 'Constraint', 'web_url': 'https://example/9'}])
        result = self.run_script('propose-rules.sh', *args, '--yes', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('重新预览', result.stderr)
        self.run_script('propose-rules.sh', *args)
        self.commit(repo, {'REVIEW.md': '# New base\n'}, 'advance')
        self.git(repo, 'push', '-q', 'origin', 'main')
        result = self.run_script('propose-rules.sh', *args, '--yes', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('crosscheck/rules-', self.git(remote, 'branch', '--list'))
        self.assertFalse(Path(self.mock, 'calls.log').exists())

    def test_mining_pages_context_pending_and_collection_failure(self):
        self.use_mock_glab()
        repo = self.make_repo('shop', {'.github/instructions/a.instructions.md': '---\napplyTo: "**"\nexcludeAgent: code-review\n---\nCoding only\n', 'src/a.ts': 'x\n'}, remote='https://code.example.com/eng/shop.git')
        # 本地已有 head，不需要真实 fetch；用假 git 只截获 fetch。
        binpath = Path(self.tmp, 'bin'); binpath.mkdir()
        import shutil
        realgit = shutil.which('git', path=self.env['PATH'])
        Path(binpath, 'git').write_text('#!/bin/sh\ncase " $* " in *" fetch "*) exit 0;; esac\nexec "' + realgit + '" "$@"\n')
        Path(binpath, 'git').chmod(0o755)
        self.env['PATH'] = str(binpath) + ':' + self.env['PATH']
        sha = self.git(repo, 'rev-parse', 'HEAD')
        self.mock_api('projects/eng%2Fshop/merge_requests/12', {'iid': 12, 'sha': sha, 'web_url': 'https://example/mr/12'})
        prefix = 'projects/eng%2Fshop/merge_requests/12/discussions?per_page=100&page='
        self.mock_api(prefix+'1', [{'id': str(i), 'notes': [{'id': i, 'body': '公共讨论：缓存需要区分账号', 'author': {'username': 'alice'}}]} for i in range(100)])
        self.mock_api(prefix+'2', [{'id': 'last', 'notes': [{'id': 100, 'body': '最后一页的人工讨论需要保留', 'author': {'username': 'bob'}}]}])
        for state in ('closed', 'opened'):
            self.mock_api('projects/eng%2Fshop/merge_requests?state='+state+'&labels=crosscheck-rule&per_page=100&page=1',
                          [] if state == 'closed' else [{'iid': 99, 'title': '已有缓存规则', 'description': '提案内容', 'web_url': 'https://example/99'}])
        args = ['--repo', repo, '--mr', '12', '--out', str(Path(self.tmp, 'mined'))]
        plugin = Path(fixture.SCRIPTS).parents[2]
        result = subprocess.run(['sh', 'skills/cr/scripts/mine-reviews.sh', *args], cwd=plugin,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rd = Path(self.review_dir(result.stdout))
        comments = [json.loads(x) for x in (rd/'comments.jsonl').read_text().splitlines()]
        self.assertEqual(len(comments), 101)
        self.assertTrue(comments[-1]['url'].endswith('#note_100'))
        self.assertEqual(self.load(rd/'collection-status.json')['status'], 'complete')
        self.assertEqual(self.load(rd/'open-candidates.json')[0]['iid'], 99)
        self.assertIn('excludeAgent=code-review', (rd/'existing-rules.md').read_text())
        Path(self.mock, 'api', fixture.api_key(prefix+'2')+'.json').unlink()
        self.run_script('mine-reviews.sh', *args)
        self.assertEqual(self.load(rd/'collection-status.json')['status'], 'partial')
        self.assertIn('未读全', (rd/'collection-status.json').read_text())
