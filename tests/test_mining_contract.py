"""固定输入与模型结果的协议回归；这里的人工 JSON 不代表模型语义评测。"""
import json
from pathlib import Path

from fixture import Base


class MiningContract(Base):
    def setUp(self):
        super().setUp()
        self.work = Path(self.tmp, 'run')
        self.work.mkdir()
        self.root = Path(self.tmp, 'repo')
        self.root.mkdir()
        self.git(str(self.root), 'init', '-q')
        (self.root / 'checks.json').write_text('{"checks": ["format"]}\n')
        self.git(str(self.root), 'add', '.')
        self.git(str(self.root), 'commit', '-qm', 'fixture')
        self.head = self.git(str(self.root), 'rev-parse', 'HEAD').strip()
        self.snapshot = dict(version=1, complete=True, feedback=[], records=[])
        for mr in (12, 15):
            notes = [dict(id=mr * 10, discussion='d', author='reviewer%d' % mr,
                          body='分页参数必须校验上限', resolved=True, changed_after=True),
                     dict(id=mr * 10 + 1, discussion='d', author='owner', body='已按公共协议补上校验')]
            self.snapshot['records'].append(dict(iid=mr, author='owner', notes=notes))
            batch = [dict(n, mr=mr, note=n['id'], thread='%d:d' % mr) for n in notes]
            self.put('batch-%d.json' % (1 if mr == 12 else 2), batch)
            self.put('result-%d.json' % (1 if mr == 12 else 2), dict(
                processed=['%d:d' % mr], unprocessed=[], excluded=[],
                observations=[dict(pattern='公共查询上限', evidence=[self.evidence(mr)])]))
        self.put('snapshot.json', self.snapshot)
        self.meta(2)
        self.put('rules-sources.json', [dict(file='REVIEW.md', text='日志不能包含邮箱', issues=[])])
        self.candidate = dict(observations=['batch-1:1', 'batch-2:1'], kind='rule', topic='api',
                              rule='在 src/api/** 中必须校验分页参数上限', why='避免一次读取过多数据',
                              paths=['src/api/**'], severity='重要', exceptions='离线导出使用独立协议',
                              bad_example='query(input)', good_example='query(validate(input))',
                              review='两份讨论要求同一协议，离线导出除外',
                              coverage=dict(status='uncovered', reason='已核对固定规范',
                                            sources=[dict(file='REVIEW.md', quote='日志不能包含邮箱')]),
                              evidence=[dict(self.evidence(mr), assessment=dict(state='accepted',
                                        reason='作者明确说明修改', basis=[dict(mr=mr, note=mr*10+1,
                                        quote='已按公共协议补上校验')])) for mr in (12, 15)])

    def evidence(self, mr):
        return dict(mr=mr, note=mr*10, quote='分页参数必须校验上限')

    def put(self, name, value):
        (self.work / name).write_text(json.dumps(value, ensure_ascii=False))

    def get(self, name):
        return json.loads((self.work / name).read_text())

    def meta(self, batches):
        (self.work / 'meta.txt').write_text('repo_root=%s\nbatches=%d\nhead=%s\n' % (self.root, batches, self.head))

    def observe(self):
        return self.run_py('mine-check.py', str(self.work), '--observations', check=False)

    def synthesize(self, candidates=None, excluded=None):
        self.put('synthesis.json', dict(candidates=[self.candidate] if candidates is None else candidates,
                                        excluded=excluded or []))
        return self.run_py('mine-check.py', str(self.work), str(self.work / 'synthesis.json'), check=False)

    def assertRejected(self, result, text):
        self.assertNotEqual(0, result.returncode)
        self.assertIn(text, result.stderr)
        self.assertFalse((self.work / 'plan.md').exists())

    def test_single_mr_observations_merge_only_in_global_stage(self):
        self.assertEqual(0, self.observe().returncode)
        self.assertEqual(2, len(self.get('observations.json')['observations']))
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(2, self.get('candidates.json')[0]['support'])

    def test_missing_and_array_result_are_not_completed(self):
        (self.work / 'result-2.json').unlink()
        self.assertRejected(self.observe(), '批次不齐')
        self.put('result-2.json', [])
        self.assertRejected(self.observe(), '空数组不代表完成')

    def test_partial_collection_cannot_generate_observations(self):
        self.snapshot['complete'] = False
        self.put('snapshot.json', self.snapshot)
        self.assertRejected(self.observe(), '采集不完整')

    def test_unfinished_or_unexplained_threads_stop(self):
        result = self.get('result-1.json')
        result.update(processed=[], unprocessed=['12:d'])
        self.put('result-1.json', result)
        self.assertRejected(self.observe(), '仍有未完成线程')
        result.update(processed=['12:d'], unprocessed=[], observations=[])
        self.put('result-1.json', result)
        self.assertRejected(self.observe(), '缺少观察或排除理由')

    def test_batch_must_preserve_every_reply_once(self):
        batch = self.get('batch-1.json')
        self.put('batch-1.json', batch[:1])
        self.assertRejected(self.observe(), '缺少线程中的评论')
        self.put('batch-1.json', batch + batch[:1])
        self.assertRejected(self.observe(), '重复评论')

    def test_observation_quote_must_match_snapshot(self):
        result = self.get('result-1.json')
        result['observations'][0]['evidence'][0]['quote'] = '不存在的评论'
        self.put('result-1.json', result)
        self.assertRejected(self.observe(), '对不上的证据')

    def test_global_missing_observation_and_empty_exclusion_stop(self):
        self.assertRejected(self.synthesize([]), '遗漏观察')
        excluded = [dict(observations=['batch-1:1', 'batch-2:1'], feedback=[], reason='各为一次业务例外')]
        r = self.synthesize([], excluded)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('业务例外', self.get('dropped.json')[0]['reason'])
        excluded.append(dict(observations=[], feedback=[], reason='无关'))
        self.assertRejected(self.synthesize([], excluded), '没有关联观察')

    def test_wrong_thread_cannot_prove_acceptance(self):
        self.candidate['evidence'][0]['assessment']['basis'][0]['mr'] = 15
        self.candidate['evidence'][0]['assessment']['basis'][0]['note'] = 151
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('不在同一讨论', r.stdout)

    def test_invalid_extra_evidence_cannot_silently_disappear(self):
        self.candidate['evidence'].append(dict(mr=12, note=120, quote='编造的反对证据'))
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('证据未全部核对通过', r.stdout)

    def test_candidate_cannot_borrow_unreferenced_observation(self):
        self.candidate['observations'] = ['batch-1:1']
        r = self.synthesize(excluded=[dict(observations=['batch-2:1'], feedback=[], reason='另一个主题')])
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('不属于引用的观察线程', r.stdout)

    def test_resolved_and_changed_do_not_override_model_rejection(self):
        self.candidate['evidence'][1]['assessment']['state'] = 'rejected'
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('明确未采纳', r.stdout)

    def test_unknown_is_visible_and_not_counted_as_acceptance(self):
        self.candidate['evidence'][1]['assessment'].update(state='unknown', basis=[])
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        c = self.get('candidates.json')[0]
        self.assertEqual((1, 1), (c['support'], c['unknown']))
        self.assertIn('待确认', (self.work / 'plan.md').read_text())

    def test_boolean_and_null_do_not_substitute_for_review_reasons(self):
        for value in (True, None, 42):
            with self.subTest(value=value):
                self.candidate['review'] = value
                r = self.synthesize()
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertEqual([], self.get('candidates.json'))
                self.assertIn('缺少语义复核说明', r.stdout)

    def test_rule_source_read_failure_cannot_be_hidden(self):
        sources = self.get('rules-sources.json')
        sources.append(dict(file='.agents/rules/shared.md', text='', issues=['断链']))
        self.put('rules-sources.json', sources)
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual('unknown', self.get('candidates.json')[0]['coverage']['status'])

    def test_fixed_repository_context_ignores_worktree_edits(self):
        self.candidate['coverage']['sources'] = [dict(file='checks.json', quote='"checks": ["format"]')]
        (self.root / 'checks.json').write_text('changed after collection')
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        c = self.get('candidates.json')[0]
        self.assertEqual(self.head, c['coverage']['sources'][0]['ref'])

    def test_mutable_cache_and_rules_do_not_change_plan(self):
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        before = (self.work / 'plan.md').read_bytes()
        (self.root / 'REVIEW.md').write_text('已经改过的规范')
        self.put('cache.json', {'records': []})
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(before, (self.work / 'plan.md').read_bytes())

    def test_all_feedback_requires_a_disposition(self):
        self.snapshot['feedback'] = [dict(key='k1', id='f1', reason='单次不修', verdict='wontfix')]
        self.put('snapshot.json', self.snapshot)
        self.assertRejected(self.synthesize(), '遗漏判断记录')
        r = self.synthesize(excluded=[dict(observations=[], feedback=['k1'], reason='一次不修不是长期约束')])
        self.assertEqual(0, r.returncode, r.stderr)

    def test_empty_complete_input_can_explicitly_produce_no_candidates(self):
        self.put('snapshot.json', dict(version=1, complete=True, records=[], feedback=[]))
        self.meta(0)
        for p in self.work.glob('result-*.json'):
            p.unlink()
        r = self.synthesize([])
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
