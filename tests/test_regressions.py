"""对端到端测试发现的规则校验、上下文和 hook 缺陷做隔离回归。"""
import json
import os
from pathlib import Path
import subprocess

import test_crosscheck as fixture


class PipelineRegressions(fixture.ProjectFixture):
    run_pipeline = fixture.TestPipeline.run_pipeline

    def test_missing_quote_is_rejected(self):
        rd, _ = self.collect('feature')
        p = 'src/orders/paginate.ts'
        c = dict(file=p, line=2, category='rules', rule_file='REVIEW.md', summary='missing quote', failure_scenario='x')
        final, _ = self.run_pipeline(rd, [c], {'missing quote': (100, [dict(file=p, lines='2')])})
        self.assertEqual(final['groups']['rules'], [])
        self.assertIn('没有引用规则原文', self.load(os.path.join(rd, 'rejected.json'))[0]['reject_reason'])

    def test_report_distinguishes_configuration_and_missing_intent(self):
        rd, _ = self.collect('feature')
        with Path(rd, 'meta.txt').open('a') as f:
            f.write('\nfinder_model=sonnet\nverifier_model=sonnet\n')
        s = self.run_script('report-lines.sh', rd).stdout
        self.assertIn('查找模型配置:sonnet', s)
        self.assertNotIn('查找模型:sonnet', s)
        self.assertIn('需求核对:缺少明确验收标准,未完成', s)


class ContextRegressions(fixture.Base):
    def test_range_collects_issue_and_preserves_commit_body(self):
        self.use_mock_glab()
        self.config('gitlab_hosts=code.example.test\n')
        repo = self.make_repo('shop', {'a.ts': 'old\n'}, remote='http://code.example.test:8929/eng/shop.git')
        base = self.git(repo, 'rev-parse', 'HEAD')
        head = self.commit(repo, {'a.ts': 'new\n'}, 'fix pages\n\nCloses #1\n保留这段正文')
        self.mock_api('projects/:id/issues/1', dict(title='pages', description='## 验收标准\n- 空列表返回 0 页'))
        r = self.run_script('collect-diff.sh', base+'..'+head, cwd=repo)
        rd = self.review_dir(r.stdout)
        self.assertEqual(self.kv(os.path.join(rd, 'meta.txt'))['platform'], 'gitlab')
        self.assertEqual(self.kv(os.path.join(rd, 'meta.txt'))['intent_acceptance'], '1')
        self.assertIn('保留这段正文', Path(rd, 'intent.md').read_text())
        self.assertIn('空列表返回 0 页', Path(rd, 'intent.md').read_text())
        self.assertNotIn('需要 glab 登录', r.stdout)

    def test_unknown_platform_reports_limitation_not_login(self):
        repo = self.make_repo('plain', {'a': 'old\n'})
        base = self.git(repo, 'rev-parse', 'HEAD')
        head = self.commit(repo, {'a': 'new\n'}, 'change\n\nCloses #1')
        r = self.run_script('collect-diff.sh', base+'..'+head, cwd=repo)
        self.assertIn('未识别为 GitLab', r.stdout)
        self.assertNotIn('需要 glab 登录', r.stdout)


class BaseRegressions(fixture.Base):
    """本地审查的对比基准:以远端最新为准,可以用 --base 指定,有更近的长期分支时提示。"""

    def setUp(self):
        super().setUp()
        origin = self.bare('shop')
        self.repo = self.make_repo('shop', {'a.txt': '1\n'}, remote=origin)
        self.old = self.git(self.repo, 'rev-parse', 'HEAD')
        self.git(self.repo, 'push', '-q', 'origin', 'main')
        self.git(self.repo, 'checkout', '-qb', 'develop')
        self.commit(self.repo, {'d.txt': 'd\n'}, 'dev')
        self.git(self.repo, 'push', '-q', 'origin', 'develop')
        self.git(self.repo, 'checkout', '-q', 'main')
        self.commit(self.repo, {'other.txt': '2\n'}, 'other')
        self.git(self.repo, 'push', '-q', 'origin', 'main')

    def files(self, *args, env=None):
        r = self.run_script('collect-diff.sh', *args, '--full', cwd=self.repo, env=dict(self.env, **(env or {})))
        return Path(self.review_dir(r.stdout), 'files.txt').read_text().split(), r.stdout

    def test_stale_remote_tracking_branch_is_refreshed(self):
        self.git(self.repo, 'checkout', '-qb', 'feat')
        self.commit(self.repo, {'f.txt': 'f\n'}, 'feat')
        self.git(self.repo, 'update-ref', 'refs/remotes/origin/main', self.old)
        files, out = self.files()
        self.assertEqual(files, ['f.txt'])
        self.assertNotIn('NOTE:', out)

    def test_unfetchable_base_is_reported(self):
        self.git(self.repo, 'checkout', '-qb', 'feat')
        self.commit(self.repo, {'f.txt': 'f\n'}, 'feat')
        self.git(self.repo, 'update-ref', 'refs/remotes/origin/main', self.old)
        files, out = self.files(env=dict(CR_FETCH='0'))
        self.assertIn('other.txt', files)
        self.assertIn('按本地已有的 origin/main', out)

    def test_closer_long_lived_branch_is_suggested_and_base_overrides(self):
        self.git(self.repo, 'checkout', '-qb', 'feat', 'origin/develop')
        self.commit(self.repo, {'g.txt': 'g\n'}, 'g')
        files, out = self.files()
        self.assertEqual(sorted(files), ['d.txt', 'g.txt'])
        self.assertIn('BASE_CHOICE:', out)
        self.assertIn('--base develop', out)
        files, out = self.files('--base', 'develop')
        self.assertEqual(files, ['g.txt'])
        self.assertNotIn('BASE_CHOICE:', out)
        self.assertEqual(self.kv(os.path.join(self.review_dir(out), 'meta.txt'))['target_branch'], 'develop')


class HookRegistration(fixture.Base):
    """注册文件只用各家文档里写明的事件和工具名。"""
    OFFICIAL = {
        'hooks.json': ({'PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'UserPromptSubmit'},
                       {'Bash', 'Read', 'Edit', 'Write', 'NotebookEdit'}),
        'codex-hooks.json': ({'PreToolUse', 'PostToolUse', 'UserPromptSubmit'}, {'Bash', 'apply_patch'}),
        'cursor-hooks.json': ({'beforeShellExecution', 'afterShellExecution', 'preToolUse', 'beforeReadFile',
                               'beforeSubmitPrompt'}, {'Write', 'Delete'}),
    }

    def test_only_documented_events_and_tools(self):
        hooks = Path(fixture.SCRIPTS).parents[2] / 'hooks'
        for name, (events, tools) in self.OFFICIAL.items():
            with self.subTest(name=name):
                d = json.loads((hooks / name).read_text(encoding='utf-8'))['hooks']
                self.assertLessEqual(set(d), events)
                for groups in d.values():
                    for g in groups:
                        if g.get('matcher'):
                            self.assertLessEqual(set(g['matcher'].split('|')), tools)
        manifest = json.loads((hooks.parent / '.codex-plugin' / 'plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['hooks'], './hooks/codex-hooks.json')


class GuardRegressions(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('repo', {'a.ts': 'original\n'})
        self.rd = Path(self.tmp, 'review'); self.rd.mkdir()
        (self.rd/'meta.txt').write_text('')
        (self.rd/'rules-meta.txt').write_text('units=1\nfingerprint=abc\n')
        (self.rd/'task-rules.md').write_text('RULES-FP=abc\n规则正文\n核对码:ABCD\n')

    def hook(self, event='PreToolUse', tool='Bash', inp=None, **fields):
        payload = dict(hook_event_name=event, session_id='session', tool_name=tool,
                       tool_input=inp or {}, cwd=self.repo, **fields)
        r = subprocess.run(['sh', os.path.join(fixture.SCRIPTS, 'guard.sh')], input=json.dumps(payload),
                           text=True, capture_output=True, env=self.env, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def start(self, tool='Bash', cursor=False):
        cmd = 'sh /example/collect-diff.sh target'
        self.hook('beforeShellExecution' if cursor else 'PreToolUse', tool, {'command': cmd}, command=cmd)

    def test_codex_failed_collect_releases_without_exit_code(self):
        self.start()
        cmd = 'sh /example/collect-diff.sh target'
        self.hook('PostToolUse', inp={'command': cmd}, tool_response='collect-diff: 找不到默认分支')
        self.assertFalse(self.denied())

    def test_usage_reason_is_recorded_when_hook_ran(self):
        self.start()
        cmd = 'sh /example/save-review.sh %s' % self.rd
        self.hook(inp={'command': cmd}, command=cmd)
        self.assertEqual((self.rd/'usage.txt').read_text(), 'usage_status=no_start\n')

    def denied(self):
        return 'deny' in self.hook(inp={'command': 'git commit -m test'})

    def test_only_successful_executed_end_script_releases(self):
        for command in ['cat /example/check-state.sh', 'cat /example/save-review.sh',
                        'sh /example/check-state.sh --help', 'sh /example/check-state.sh /missing']:
            with self.subTest(command=command):
                self.start()
                self.hook(inp={'command': command})
                self.assertTrue(self.denied())
                self.hook('PostToolUseFailure', inp={'command': command}, error='exit 1')
                self.assertTrue(self.denied())
        command = 'sh /example/check-state.sh /review'
        self.start(); self.hook(inp={'command': command})
        self.assertTrue(self.denied())
        self.hook('PostToolUse', inp={'command': command}, tool_response={'stdout': 'OK: unchanged'})
        self.assertFalse(self.denied())

    def test_common_tool_shapes_and_literal_shell_writes(self):
        self.start()
        for tool, inp in [('apply_patch', {'command':'*** Begin Patch\n*** Update File: a.ts\n*** End Patch'}),
                          ('Bash', {'command':'echo changed > a.ts'}),
                          ('Bash', {'command':'printf changed | tee a.ts'}),
                          ('Bash', {'command':'touch a.ts'})]:
            with self.subTest(tool=tool, inp=inp):
                self.assertIn('deny', self.hook(tool=tool, inp=inp))
        for cmd in ['git diff', 'cat a.ts', 'echo text > '+str(self.rd/'result'), 'printf text']:
            self.assertNotIn('deny', self.hook(inp={'command':cmd}))
        self.assertEqual(Path(self.repo, 'a.ts').read_text(), 'original\n')

    def test_cursor_failure_and_claude_failure_release(self):
        for event in ['afterShellExecution', 'PostToolUseFailure']:
            self.start(cursor=event=='afterShellExecution')
            cmd='sh /example/collect-diff.sh target'
            self.hook(event, inp={'command':cmd}, command=cmd, output='collect-diff: git 无法对 x 做 diff', error='exit 1')
            self.assertFalse(self.denied())
