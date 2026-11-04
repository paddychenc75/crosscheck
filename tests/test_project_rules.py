"""项目规则的装配回归;只使用临时仓库。"""
import json
import os
from pathlib import Path
import sys
import subprocess

import fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import project_rules


class ProjectRulesTests(fixture.Base):

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

    def test_native_paths_depth_braces_and_exclusion(self):
        repo = self.repo_with_changes({
            '.claude/rules/root.md': '---\npaths: ["*.py"]\n---\nRoot only\n',
            '.claude/rules/all.md': '---\npaths: ["**/*.py"]\n---\nAll Python\n',
            '.github/instructions/root.instructions.md': '---\napplyTo: "*.py"\n---\nRoot only\n',
            '.github/instructions/all.instructions.md': '---\napplyTo: "**/*.py,src/*.{ts,tsx}"\n---\nReview\n',
            '.github/instructions/excluded.instructions.md': '---\napplyTo: "**"\nexcludeAgent: "code-review"\n---\nCoding only\n',
            '.cursor/rules/mobile.mdc': '---\nglobs: "ios/**/*.swift,android/**/*.kt"\n---\nMobile\n',
            'REVIEW.md': '- Python rule\n',
        })
        rd = self.collect(repo)
        units = {r['file']: r for r in self.load(Path(rd, 'rules-index.json'))}
        self.assertEqual(units['.claude/rules/root.md']['applies_files'], ['a.py'])
        self.assertEqual(units['.github/instructions/root.instructions.md']['applies_files'], ['a.py'])
        self.assertEqual(set(units['.claude/rules/all.md']['applies_files']), {'a.py', 'src/a.py'})
        self.assertEqual(set(units['.github/instructions/all.instructions.md']['applies_files']), {'a.py', 'src/a.py', 'src/a.ts'})
        self.assertEqual(set(units['.cursor/rules/mobile.mdc']['applies_files']), {'ios/A.swift', 'android/A.kt'})
        self.assertEqual(set(units['REVIEW.md']['applies_files']), {'a.py', 'src/a.py', 'src/a.ts', 'ios/A.swift', 'android/A.kt'})
        self.assertNotIn('.github/instructions/excluded.instructions.md', units)
        self.assertIn('excludeAgent: code-review', self.run_script('report-lines.sh', rd).stdout)

    def test_wrong_scope_does_not_silently_become_global(self):
        repo = self.repo_with_changes({'.claude/rules/bad.md': '---\npath: src/**\n---\nBad scope\n'})
        rd = self.collect(repo)
        self.assertEqual(self.load(Path(rd, 'rules-index.json')), [])
        self.assertIn('paths', self.kv(Path(rd, 'rules-meta.txt'))['errors'])
        self.assertIn('格式有误', self.run_script('report-lines.sh', rd).stdout)

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

    def test_unclosed_root_review_frontmatter_is_reported(self):
        repo = self.repo_with_changes({'REVIEW.md': '---\nskip: ["src/**"]\n\n- Python rule\n'})
        r = self.run_script('settings.sh', 'show', repo)
        self.assertEqual(json.loads(r.stdout)['skip'], [])
        self.assertIn('没有结束的 ---', r.stderr)
        rd = self.collect(repo)
        self.assertIn('REVIEW.md: frontmatter 缺少结束分隔符', self.kv(Path(rd, 'rules-meta.txt'))['errors'])

    def test_agents_rules_paths_and_globs_must_match(self):
        repo = self.repo_with_changes({
            '.agents/rules/bad.md': '---\npaths: "src/**"\nglobs: "ios/**"\n---\n- 不一致\n',
            '.agents/rules/good.md': '---\npaths: "src/**, ios/**"\nglobs: "ios/**,src/**"\n---\n- 一致\n',
        })
        rd = self.collect(repo)
        errors = self.kv(Path(rd, 'rules-meta.txt'))['errors']
        self.assertIn('.agents/rules/bad.md', errors)
        self.assertIn('paths 与 globs 不一致', errors)
        self.assertNotIn('good.md', errors)
        units = {r['file']: r for r in self.load(Path(rd, 'rules-index.json'))}
        self.assertEqual(set(units['.agents/rules/good.md']['applies_files']), {'src/a.py', 'src/a.ts', 'ios/A.swift'})

    def linked_repo(self):
        """.claude/rules、.cursor/rules 链接到 .agents/rules 的同一份规则;CLAUDE.md 链接到 AGENTS.md;.cursor 链接到 .claude 里的手写规则。"""
        repo = self.repo_with_changes({
            '.agents/rules/api.md': '---\npaths: "src/**"\nglobs: "src/**"\n---\n- 接口必须校验参数\n',
            '.claude/rules/own.md': '- 自己写的规则\n',
            'AGENTS.md': '- 约定\n',
        })
        links = {'.claude/rules/api.md': '../../.agents/rules/api.md', '.cursor/rules/api.mdc': '../../.agents/rules/api.md',
                 '.cursor/rules/own.mdc': '../../.claude/rules/own.md', 'CLAUDE.md': 'AGENTS.md'}
        for link, target in links.items():
            os.makedirs(os.path.dirname(os.path.join(repo, link)) or repo, exist_ok=True)
            os.symlink(target, os.path.join(repo, link))
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-qm', 'links')
        return repo

    def test_symlinked_rules_are_read_once(self):
        import crlib
        repo = self.linked_repo()
        want = ['.agents/rules/api.md', '.claude/rules/own.md', 'AGENTS.md']
        for ref in (None, 'HEAD'):                          # 工作区读文件系统,commit 里按链接内容解析
            with self.subTest(ref=ref):
                got = sorted(r['file'] for r in project_rules.discover(crlib.Source(repo, ref)))
                self.assertEqual(got, want)
        units = [r['file'] for r in self.load(Path(self.collect(repo), 'rules-index.json'))]
        self.assertEqual(sorted(units), want)
        records = [r['file'] for r in project_rules.mining_sources(repo) if not r.get('referenced_by')]
        self.assertEqual(sorted(records), want)

    def test_review_body_severity_survives_aggregation_with_fixed_threshold(self):
        repo = self.repo_with_changes({'REVIEW.md': '## 严重程度\n- 订单审计记录缺失按重要问题报告。\n',
                                       '.claude/rules/audit.md': '- 订单操作必须记录审计事件。\n'})
        rd = self.collect(repo)
        cand = dict(file='src/a.ts', line=1, category='rules', rule_file='.claude/rules/audit.md',
                    rule_quote='订单操作必须记录审计事件。', severity='major', summary='审计缺失', failure_scenario='订单未记录')
        final, _ = self.run_pipeline(rd, [cand], {'审计缺失': (90, [{'file': 'src/a.ts', 'lines': '1'}])})
        self.assertEqual(final['groups']['rules'][0]['severity'], 'major')
        final, _ = self.run_pipeline(rd, [cand], {'审计缺失': (79, [{'file': 'src/a.ts', 'lines': '1'}])})
        self.assertEqual(final['groups']['rules'], [])


class DepthSettingTests(fixture.Base):
    """REVIEW.md 头部的 depth:参数 > REVIEW.md > standard;非法值报出来按 standard;非本地审查读 base 版。"""

    def repo(self, review, branch_review=None, name='app'):
        repo = self.make_repo(name, {'REVIEW.md': review, 'src/a.py': 'def a():\n    return 1\n'})
        self.git(repo, 'switch', '-qc', 'feature')
        files = {'src/a.py': 'def a():\n    return 2\n'}
        if branch_review is not None:
            files['REVIEW.md'] = branch_review
        self.commit(repo, files, 'change')
        return repo

    def collect(self, repo, *args):
        r = self.run_script('collect-diff.sh', *args, cwd=repo)
        return r.stdout, self.review_dir(r.stdout)

    def test_precedence_arg_then_review_then_standard(self):
        repo = self.repo('---\ndepth: deep\n---\n- rule\n')
        out, rd = self.collect(repo)
        meta = self.kv(Path(rd, 'meta.txt'))
        self.assertEqual((meta['depth'], meta['depth_source'], meta['depth_invalid']), ('deep', 'review', ''))
        self.assertEqual(meta['may_run_checks'], 'yes', 'REVIEW.md 设 deep 时和参数一样会在工作区运行检查')
        self.assertIn('深度:深度(REVIEW.md 设定)', self.run_script('report-lines.sh', rd).stdout)
        _, rd = self.collect(repo, '--depth', 'quick')
        meta = self.kv(Path(rd, 'meta.txt'))
        self.assertEqual((meta['depth'], meta['depth_source']), ('quick', 'arg'))
        self.assertEqual(meta['may_run_checks'], 'no')
        self.assertIn('深度:快速(本次指定)', self.run_script('report-lines.sh', rd).stdout)
        repo2 = self.repo('- rule\n', name='app2')
        _, rd = self.collect(repo2)
        meta = self.kv(Path(rd, 'meta.txt'))
        self.assertEqual((meta['depth'], meta['depth_source']), ('standard', 'default'))
        self.assertIn('深度:标准(默认)', self.run_script('report-lines.sh', rd).stdout)

    def test_invalid_value_is_reported_and_falls_back_to_standard(self):
        repo = self.repo('---\ndepth: thorough\n---\n')
        out, rd = self.collect(repo)
        meta = self.kv(Path(rd, 'meta.txt'))
        self.assertEqual((meta['depth'], meta['depth_source'], meta['depth_invalid']), ('standard', 'default', 'thorough'))
        self.assertIn('depth「thorough」不认识', out)
        self.assertIn('REVIEW.md 的 depth「thorough」不认识', self.run_script('report-lines.sh', rd).stdout)
        shown = json.loads(self.run_script('settings.sh', 'show', repo).stdout)
        self.assertEqual((shown['depth'], shown['depth_invalid']), (None, 'thorough'))

    def test_branch_review_reads_depth_from_base(self):
        repo = self.repo('---\ndepth: quick\n---\n', branch_review='---\ndepth: deep\n---\n')
        self.git(repo, 'switch', '-q', 'main')
        _, rd = self.collect(repo, 'feature')
        meta = self.kv(Path(rd, 'meta.txt'))
        self.assertEqual((meta['depth'], meta['depth_source']), ('quick', 'review'), '改动本身改的 depth 不在审查它时生效')
        self.assertTrue(self.load(Path(rd, 'settings.json'))['source'].startswith('base:'))
        shown = json.loads(self.run_script('settings.sh', 'show', repo).stdout)
        self.assertEqual(shown['depth'], 'quick')
