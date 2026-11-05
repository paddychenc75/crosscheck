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
        self.write(str(self.root), {'src/api/orders.py': 'def list_orders(limit):\n    return query(min(limit, 100))\n',
                                   'src/api/users.py': 'def list_users(limit):\n    return load_users(min(limit, 100))\n',
                                   'src/api/exports.py': 'def export_orders(limit):\n    return export_query(limit)\n'})
        self.git(str(self.root), 'add', '.')
        self.git(str(self.root), 'commit', '-qm', 'fixture')
        self.head = self.git(str(self.root), 'rev-parse', 'HEAD').strip()
        self.snapshot = dict(version=1, complete=True, feedback=[], records=[])
        for mr in (12, 15):
            notes = [dict(id=mr * 10, discussion='d', author='reviewer%d' % mr,
                          body='分页参数必须校验上限', resolved=True, changed_after=True),
                     dict(id=mr * 10 + 1, discussion='d', author='owner', body='已按公共协议补上校验')]
            self.snapshot['records'].append(dict(iid=mr, author='owner', head_sha=self.head, notes=notes))
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
                                        reason='最终代码在查询前把 limit 限制为 100', basis=[dict(mr=mr, note=mr*10+1,
                                        quote='已按公共协议补上校验')], code=[self.code()])) for mr in (12, 15)])

    def code(self, **changes):
        row = dict(ref=self.head, file='src/api/orders.py', line=2,
                   quote='    return query(min(limit, 100))', reason='查询前限制上限为 100')
        return dict(row, **changes)

    def reset_outputs(self):
        for name in ('candidates.json', 'dropped.json', 'mined-candidates.md'):
            (self.work / name).unlink(missing_ok=True)

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
        self.assertFalse((self.root / '.agents').exists())  # 拒绝时不写仓库;已存在的材料草稿须保留

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
        c = self.get('candidates.json')[0]
        self.assertEqual(1, c['support'])
        self.assertIn('未采纳或冲突反证', ';'.join(c['unmet']))
        self.assertEqual('rejected', c['evidence'][1]['assessment']['state'])

    def test_unknown_is_visible_and_not_counted_as_acceptance(self):
        self.candidate['evidence'][1]['assessment'].update(state='unknown', basis=[])
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        c = self.get('candidates.json')[0]
        self.assertEqual((1, 1), (c['support'], c['unknown']))
        self.assertIn('待确认', (self.work / 'mined-candidates.md').read_text())

    def test_boolean_and_null_do_not_substitute_for_review_reasons(self):
        for value in (True, None, 42):
            with self.subTest(value=value):
                self.reset_outputs()
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

    def test_mutable_cache_and_rules_do_not_change_candidates(self):
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        before = (self.work / 'mined-candidates.md').read_bytes()
        (self.root / 'REVIEW.md').write_text('已经改过的规范')
        self.put('cache.json', {'records': []})
        r = self.synthesize()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(before, (self.work / 'mined-candidates.md').read_bytes())

    def test_all_feedback_requires_a_disposition(self):
        self.snapshot['feedback'] = [dict(key='k1', id='f1', reason='单次不修', verdict='wontfix')]
        self.put('snapshot.json', self.snapshot)
        self.assertRejected(self.synthesize(), '遗漏判断记录')
        r = self.synthesize(excluded=[dict(observations=[], feedback=['k1'], reason='一次不修不是长期约束')])
        self.assertEqual(0, r.returncode, r.stderr)

    def single(self):
        self.candidate['observations'] = ['batch-1:1']
        self.candidate['evidence'] = self.candidate['evidence'][:1]
        return [dict(pattern='另一个主题', observations=['batch-2:1'], feedback=[], reason='独立主题留待之后审阅')]

    def practice(self):
        return dict(reason='users 使用独立查询入口实现同一上限,不是 orders 的拷贝或生成物',
                    scope=['src/api/**'], examples=[self.code(file='src/api/users.py',
                    quote='    return load_users(min(limit, 100))')], counterexamples=[])

    def test_single_adopted_mr_with_independent_practice_stays_pending(self):
        excluded = self.single()
        self.candidate['practice'] = self.practice()
        result = self.synthesize(excluded=excluded)
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((1, 1), (candidate['mentions'], candidate['support']))
        self.assertEqual('verified', candidate['practice']['examples'][0]['status'])
        self.assertIn('独立性', ';'.join(candidate['unmet']))
        self.assertIn('单次历史', ';'.join(candidate['unmet']))
        rendered = (self.work / 'mined-candidates.md').read_text()
        self.assertIn('落实依据:src/api/orders.py:2 @ ' + self.head, rendered)
        self.assertIn('return query(min(limit, 100))', rendered)
        self.assertIn('仓库惯例(不计入 MR 采纳数)', rendered)
        self.assertIn('惯例:src/api/users.py:2 @ ' + self.head, rendered)
        self.assertIn('return load_users(min(limit, 100))', rendered)
        self.assertIn('模型判断已采纳 1', rendered)

    def test_single_adopted_mr_without_practice_is_not_discarded(self):
        result = self.synthesize(excluded=self.single())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, self.get('candidates.json')[0]['support'])

    def test_final_code_supports_model_adoption_without_reply(self):
        for index, row in enumerate(self.snapshot['records'], 1):
            row['notes'] = row['notes'][:1]
            self.put('batch-%d.json' % index, self.get('batch-%d.json' % index)[:1])
        self.put('snapshot.json', self.snapshot)
        for evidence in self.candidate['evidence']:
            evidence['assessment']['basis'] = []
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        for evidence in self.get('candidates.json')[0]['evidence']:
            self.assertEqual('accepted', evidence['assessment']['state'])
            self.assertEqual([], evidence['assessment']['basis'])
            self.assertEqual(64, len(evidence['assessment']['code'][0]['content_sha256']))

    def test_replies_and_resolved_alone_do_not_prove_adoption(self):
        for evidence in self.candidate['evidence']:
            evidence['assessment']['code'] = []
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((0, 2), (candidate['support'], candidate['unknown']))
        self.assertTrue(all(e['assessment']['declared_state'] == 'accepted' for e in candidate['evidence']))
        self.assertIn('resolved', candidate['evidence'][0]['assessment']['reason'])

    def test_resolved_without_reply_or_code_stays_unknown(self):
        for index, row in enumerate(self.snapshot['records'], 1):
            row['notes'] = row['notes'][:1]
            self.put('batch-%d.json' % index, self.get('batch-%d.json' % index)[:1])
        self.put('snapshot.json', self.snapshot)
        for evidence in self.candidate['evidence']:
            evidence['assessment'].update(basis=[], code=[])
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((0, 2), (candidate['support'], candidate['unknown']))
        self.assertTrue(all(e['resolved'] for e in candidate['evidence']))

    def test_single_unknown_without_practice_remains_watch_with_evidence(self):
        excluded = self.single()
        self.candidate['evidence'][0]['assessment']['code'] = []
        result = self.synthesize(excluded=excluded)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.get('candidates.json'))
        row = next(r for r in self.get('dropped.json') if r['disposition'] == 'watch')
        self.assertEqual(['batch-1:1'], row['observations'])
        self.assertEqual(120, row['evidence'][0]['note'])
        self.assertEqual('unknown', row['evidence'][0]['assessment']['state'])

    def test_single_unknown_with_verified_practice_retains_gap(self):
        excluded = self.single()
        self.candidate['evidence'][0]['assessment']['code'] = []
        self.candidate['practice'] = self.practice()
        result = self.synthesize(excluded=excluded)
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((0, 1), (candidate['support'], candidate['unknown']))
        self.assertIn('补核最终代码', ';'.join(candidate['unmet']))

    def test_unreachable_final_objects_are_unknown_not_rejected(self):
        unavailable = 'a' * 40
        for row in self.snapshot['records']:
            row['head_sha'] = unavailable
        self.put('snapshot.json', self.snapshot)
        for evidence in self.candidate['evidence']:
            evidence['assessment']['code'][0]['ref'] = unavailable
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((0, 2), (candidate['support'], candidate['unknown']))
        code = candidate['evidence'][0]['assessment']['code'][0]
        self.assertEqual('unavailable', code['status'])
        self.assertIsNone(code['content_sha256'])
        self.assertIn('不可读', code['issue'])

    def test_forged_code_reference_quote_and_line_are_explicitly_rejected(self):
        for changes, reason in [(dict(ref='b' * 40), 'ref 必须严格等于'),
                                (dict(quote='    return query(limit)'), '代码引文'),
                                (dict(line=1), '代码引文'), (dict(file='../secret'), '非凭证'),
                                (dict(file='.git/config'), '非凭证'), (dict(file='.env'), '非凭证')]:
            with self.subTest(changes=changes):
                self.reset_outputs()
                self.candidate['evidence'][0]['assessment']['code'] = [self.code(**changes)]
                result = self.synthesize()
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([], self.get('candidates.json'))
                row = self.get('dropped.json')[0]
                self.assertIn(reason, row['reason'])
                self.assertEqual(self.candidate['evidence'], row['input_evidence'])
                self.assertEqual(['batch-1:1', 'batch-2:1'], row['observations'])

    def test_rejection_without_comment_or_code_is_unknown(self):
        for evidence in self.candidate['evidence']:
            evidence['assessment'].update(state='rejected', basis=[], code=[])
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual((0, 2), (candidate['support'], candidate['unknown']))

    def test_explicit_thread_rejection_survives_and_practice_cannot_count_as_support(self):
        self.candidate['practice'] = self.practice()
        for index, evidence in enumerate(self.candidate['evidence']):
            mr = evidence['mr']
            rec = self.snapshot['records'][index]
            rec['notes'][1]['body'] = '明确不采纳,这个接口按导出协议处理'
            batch = self.get('batch-%d.json' % (index + 1))
            batch[1]['body'] = rec['notes'][1]['body']
            self.put('batch-%d.json' % (index + 1), batch)
            evidence['assessment'].update(state='rejected', code=[], reason='作者明确拒绝这项约束',
                basis=[dict(mr=mr, note=mr*10+1, quote=rec['notes'][1]['body'])])
        self.put('snapshot.json', self.snapshot)
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.get('candidates.json'))
        row = self.get('dropped.json')[0]
        self.assertEqual('watch', row['disposition'])
        self.assertTrue(all(e['assessment']['state'] == 'rejected' for e in row['evidence']))
        self.assertEqual('verified', row['practice']['examples'][0]['status'])

    def test_practice_deduplicates_exact_samples_and_keeps_counterexamples(self):
        self.candidate['practice'] = self.practice()
        self.candidate['practice']['examples'] *= 2
        self.candidate['practice']['counterexamples'] = [self.code(file='src/api/exports.py',
            quote='    return export_query(limit)', reason='导出入口没有上限,需确认离线协议的例外边界')]
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        candidate = self.get('candidates.json')[0]
        self.assertEqual(1, len(candidate['practice']['examples']))
        self.assertEqual(1, len(candidate['practice']['counterexamples']))
        self.assertEqual(2, candidate['support'])
        self.assertIn('未解反例', ';'.join(candidate['unmet']))
        rendered = (self.work / 'mined-candidates.md').read_text()
        self.assertIn('落实依据:src/api/orders.py:2 @ ' + self.head, rendered)
        self.assertIn('仓库惯例(不计入 MR 采纳数)', rendered)
        self.assertIn('惯例:src/api/users.py:2 @ ' + self.head, rendered)
        self.assertIn('相反实现:src/api/exports.py:2 @ ' + self.head, rendered)
        self.assertIn('return export_query(limit)', rendered)
        self.assertIn('模型判断已采纳 2', rendered)

    def test_practice_forged_counterexample_rejects_whole_candidate(self):
        self.candidate['practice'] = self.practice()
        self.candidate['practice']['counterexamples'] = [self.code(quote='invented')]
        result = self.synthesize()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.get('candidates.json'))
        self.assertIn('代码引文', self.get('dropped.json')[0]['reason'])
        self.assertEqual('invented', self.get('dropped.json')[0]['input']['practice']['counterexamples'][0]['quote'])

    def test_watch_preserves_practice_observations_and_source_quotes(self):
        row = dict(pattern='分页约束', reason='单次记录暂不足', disposition='watch',
                   observations=['batch-1:1', 'batch-2:1'], feedback=[], practice=self.practice())
        result = self.synthesize([], [row])
        self.assertEqual(0, result.returncode, result.stderr)
        dropped = self.get('dropped.json')[0]
        self.assertEqual('watch', dropped['disposition'])
        self.assertEqual([120, 150], [e['note'] for e in dropped['evidence']])
        self.assertEqual(self.head, dropped['practice']['examples'][0]['ref'])

    def test_maintain_accepts_two_real_passages_in_same_source(self):
        self.put('rules-sources.json', [dict(file='REVIEW.md', text='分页必须限制为100\n分页禁止限制上限\n', issues=[])])
        row = dict(pattern='分页约束冲突', reason='同范围约束相反,需维护现有规则', disposition='maintain',
                   observations=['batch-1:1', 'batch-2:1'], feedback=[],
                   coverage=dict(status='partial', reason='同一文档出现冲突', sources=[
                       dict(file='REVIEW.md', quote='分页必须限制为100', line=1),
                       dict(file='REVIEW.md', quote='分页禁止限制上限', line=2)]))
        result = self.synthesize([], [row])
        self.assertEqual(0, result.returncode, result.stderr)
        dropped = self.get('dropped.json')[0]
        self.assertEqual('maintain', dropped['disposition'])
        self.assertEqual(2, len(dropped['coverage']['sources']))
        self.assertTrue(all(s['content_sha256'] for s in dropped['coverage']['sources']))

    def test_maintain_rejects_duplicate_or_fabricated_sources(self):
        source = dict(file='REVIEW.md', quote='日志不能包含邮箱')
        row = dict(pattern='待维护约束', reason='疑似冲突', disposition='maintain',
                   observations=['batch-1:1', 'batch-2:1'], feedback=[],
                   coverage=dict(status='partial', reason='需要核对双方', sources=[source, dict(source)]))
        self.assertRejected(self.synthesize([], [row]), '双方原文来源')
        row['coverage']['sources'][1]['quote'] = '编造的第二条规范'
        self.assertRejected(self.synthesize([], [row]), '引文在来源里找不到')

    def test_dropped_changes_are_frozen_even_when_candidates_unchanged(self):
        excluded = [dict(pattern='另一个主题', reason='待观察', disposition='watch',
                         observations=['batch-2:1'], feedback=[])]
        self.candidate['observations'] = ['batch-1:1']
        self.candidate['evidence'] = self.candidate['evidence'][:1]
        result = self.synthesize(excluded=excluded)
        self.assertEqual(0, result.returncode, result.stderr)
        originals = {name: (self.work / name).read_bytes() for name in
                     ('candidates.json', 'dropped.json', 'mined-candidates.md', 'rules-draft.md')}
        excluded[0]['reason'] = '换一个观察理由'
        self.assertRejected(self.synthesize(excluded=excluded), 'dropped.json 已冻结')
        self.assertEqual(originals, {name: (self.work / name).read_bytes() for name in originals})

    def test_empty_complete_input_can_explicitly_produce_no_candidates(self):
        self.put('snapshot.json', dict(version=1, complete=True, records=[], feedback=[]))
        self.meta(0)
        for p in self.work.glob('result-*.json'):
            p.unlink()
        r = self.synthesize([])
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.get('candidates.json'))
