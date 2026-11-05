"""历史归纳未入新增候选的材料,仍须进入统一草稿且受来源绑定保护。"""
import hashlib
import json
from pathlib import Path
import re

from fixture import Base


class HistoryDraft(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('repo', {'REVIEW.md': '# 审查重点\n'})
        self.work = Path(self.tmp, 'history')
        self.work.mkdir()
        (self.work / 'meta.txt').write_text('repo_root=%s\n' % self.repo)
        self.put('candidates.json', [])
        digest = hashlib.sha256(b'[]').hexdigest()
        (self.work / 'mined-candidates.md').write_text('# 候选规则\n<!-- candidates-sha256: %s -->\n' % digest)
        self.put('snapshot.json', {'version': 1, 'complete': True, 'records': [], 'feedback': []})
        self.put('rules-sources.json', [])
        self.put('dropped.json', [])

    def put(self, name, data):
        (self.work / name).write_text(json.dumps(data, ensure_ascii=False))

    def prepare(self):
        return self.run_py('rules-write.py', str(self.work), '--prepare')

    def test_no_candidates_still_displays_maintenance_and_watch(self):
        self.put('dropped.json', [
            {'disposition': 'maintain', 'rule': '统一请求重试约定', 'reason': '同一范围的两处要求相反,需要确认取舍',
             'observations': ['batch-1:1'], 'feedback': [], 'coverage': {'sources': [
                 {'file': '.agents/rules/network.md', 'quote': '失败后重试'},
                 {'file': 'REVIEW.md', 'quote': '写请求不得自动重试'}]}},
            {'disposition': 'watch', 'rule': '关闭页面后的回调', 'reason': '一次改动已修复,适用范围尚未核对',
             'observations': ['batch-2:1'], 'feedback': [],
             'evidence': [{'mr': 7, 'quote': '页面关闭后不能继续更新组件',
                           'url': 'https://code.example.com/demo/shop/-/merge_requests/7'}]},
            {'disposition': 'exclude', 'rule': '合并提醒', 'reason': '没有具体约束'},
        ])
        self.prepare()
        view = (self.work / 'rules-draft.md').read_text()
        refs = view.split('## 参考材料', 1)[1]
        for text in ('维护已有规范', '统一请求重试约定', '失败后重试', '写请求不得自动重试',
                     '待观察', '关闭页面后的回调', '一次改动已修复',
                     '[!7](https://code.example.com/demo/shop/-/merge_requests/7)', '已排除 1 个', 'rules-materials.md'):
            self.assertIn(text, refs)
        # 待观察、维护项只做参考,不进待确认;观察编号和引文留在本轮材料
        pending = view.split('## 待确认', 1)[1].split('## 参考材料', 1)[0]
        self.assertNotIn('关闭页面后的回调', pending)
        self.assertNotIn('batch-2:1', view)
        self.assertNotIn('- 复核:', view)
        materials = (self.work / 'rules-materials.md').read_text()
        for text in ('batch-2:1', '页面关闭后不能继续更新组件', '合并提醒', '没有具体约束', '写请求不得自动重试'):
            self.assertIn(text, materials)
        draft = self.load(str(self.work / 'rules-draft.json'))
        self.assertEqual([], draft['changes'])
        self.assertEqual([], draft['pending'])
        self.assertEqual(['maintain', 'watch'], [t['kind'] for t in draft['history']['topics']])
        self.assertEqual('', self.git(self.repo, 'status', '--porcelain'))
        self.assertTrue({'snapshot.json', 'rules-sources.json', 'dropped.json'} <= draft['bindings'].keys())

    def test_changed_history_material_invalidates_apply(self):
        self.prepare()
        check = self.run_py('rules-draft.py', str(self.work), '--check')
        digest = re.search(r'DIGEST: ([0-9a-f]{64})', check.stdout).group(1)
        self.put('dropped.json', [{'disposition': 'watch', 'rule': '后来补充', 'reason': '预演后变更'}])
        result = self.run_py('rules-draft.py', str(self.work), '--apply', '--expected-digest', digest, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('', self.git(self.repo, 'status', '--porcelain'))

    def test_watch_shows_practice_and_opposing_implementation(self):
        self.put('dropped.json', [{'disposition': 'watch', 'rule': '统一资源释放', 'reason': '不同生命周期需要分清',
            'observations': ['batch-1:1'], 'practice': {'reason': '核对会话与后台任务两类入口',
                'examples': [{'file': 'src/session.ts', 'line': 4, 'ref': 'a' * 40, 'reason': '会话结束释放'}],
                'counterexamples': [{'file': 'src/worker.ts', 'line': 8, 'ref': 'a' * 40, 'reason': '后台任务仍使用资源'}]}}])
        self.prepare()
        materials = (self.work / 'rules-materials.md').read_text()
        self.assertIn('惯例:src/session.ts:4', materials)
        self.assertIn('相反实现:src/worker.ts:8', materials)
        self.assertIn('后台任务仍使用资源', materials)
        self.assertIn('统一资源释放', (self.work / 'rules-draft.md').read_text())
        self.assertEqual('', self.git(self.repo, 'status', '--porcelain'))

    def test_agent_proposal_keeps_history_without_copying_it(self):
        self.put('dropped.json', [{'disposition': 'watch', 'rule': '关闭页面后的回调', 'reason': '适用范围尚未核对',
                                   'observations': ['batch-2:1']}])
        self.prepare()
        (self.work / 'proposal.json').write_text(json.dumps(dict(goal='补充说明', changes=[], notes=['已核对'], pending=[]),
                                                            ensure_ascii=False))
        self.run_py('rules-draft.py', str(self.work), '--propose', str(self.work / 'proposal.json'))
        view = (self.work / 'rules-draft.md').read_text()
        self.assertIn('关闭页面后的回调', view.split('## 参考材料', 1)[1])
        self.assertIn('- 已核对', view.split('## 核对说明', 1)[1])
        (self.work / 'proposal.json').write_text(json.dumps(dict(goal='补充说明', changes=[], history={'topics': []}),
                                                            ensure_ascii=False))
        result = self.run_py('rules-draft.py', str(self.work), '--propose', str(self.work / 'proposal.json'), check=False)
        self.assertIn('history 由 rules-write.py --prepare', result.stdout)

    def test_unknown_disposition_does_not_silently_disappear(self):
        self.put('dropped.json', [{'disposition': 'typo', 'rule': '约束', 'reason': '待核对'}])
        result = self.run_py('rules-write.py', str(self.work), '--prepare', check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('未知的历史主题去向', result.stdout + result.stderr)
        self.assertEqual('', self.git(self.repo, 'status', '--porcelain'))
