"""项目规则的装配回归;只使用临时仓库。"""
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
