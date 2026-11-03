"""目录边界和核实完整性的回归，不调用模型。"""
import json
import sys
from pathlib import Path

import fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import project_rules
import weakening


class ReportContractTests(fixture.Base):
    def report(self, groups=None, findings=None, completeness='OK: 查找角色和核实结果都已落盘\n'):
        rd = Path(self.tmp, 'report-input')
        rd.mkdir(exist_ok=True)
        (rd/'meta.txt').write_text('mode=local\ndepth=standard\nonly=bug\nsubagent_note=独立性存疑\n')
        (rd/'final.json').write_text(json.dumps(dict(groups=groups or {}, trimmed={'defects': 2},
            pre_existing=[dict(file='old.py', line=1)], ignored_by_history=[dict(id='history-1')],
            counts=dict(passed=99))))
        (rd/'findings.json').write_text(json.dumps(findings or []))
        (rd/'rejected.json').write_text('[{"id":"rejected-1","no":1}]')
        (rd/'completeness.txt').write_text(completeness)
        (rd/'weakening.txt').write_text('suppression-added\ta.py\t新增抑制\n')
        return self.run_script('report-lines.sh', str(rd)).stdout

    def test_completeness_uses_display_count_and_not_independence_note(self):
        for count in (0, 2):
            groups = {'defects': [dict(no=i+1, id='f%d' % i) for i in range(count)]}
            for gap in ('缺 finders/reviewer.json', 'reviewer: 覆盖受限:预算达限'):
                with self.subTest(count=count, gap=gap):
                    out = self.report(groups, completeness='INCOMPLETE: '+gap+'\n')
                    self.assertIn('INCOMPLETE: 审查未完成，已检查部分确认 %d 条发现' % count, out)
                    self.assertIn(gap, out)
            with self.subTest(count=count, complete=True):
                out = self.report(groups)
                self.assertNotIn('INCOMPLETE:', out)
                self.assertNotIn('审查未完成', out)

    def test_feedback_requires_displayed_record_and_uses_its_number(self):
        for groups, findings in (
            ({}, []),
            ({}, [dict(id='hidden-1')]),
            ({'rules': [dict(no=4, id='missing')]}, [dict(id='other')]),
        ):
            with self.subTest(groups=groups, findings=findings):
                self.assertNotIn('FEEDBACK:', self.report(groups, findings))
        out = self.report({'rules': [dict(no=4, id='f4')]}, [dict(no=4, id='f4')])
        self.assertIn('FEEDBACK:', out)
        self.assertIn('「4 误报:说明原因」', out)
        self.assertIn('代码或判断前提变化时会重新核实', out)
        self.assertNotIn('不再重复报', out)

    def test_different_change_signals_keep_same_neutral_report_protocol(self):
        rd = Path(self.tmp, 'signals')
        rd.mkdir()
        changes = [
            ('tests/deleted.test.ts', 'expect(a).toBe(1);', '', True),
            ('tests/changed.test.ts', 'expect(a).toBe(1);', 'expect(a).toBe(2);', False),
            ('tests/removed.test.ts', 'assert result', '', False),
            ('src/a.ts', '', '// @ts-ignore', False),
            ('src/b.py', '', '# noqa', False),
            ('.github/workflows/test.yml', 'run: test', 'continue-on-error: true', False),
        ]
        patch = ''
        for path, old, new, deleted in changes:
            patch += 'diff --git a/{0} b/{0}\n'.format(path)
            if deleted:
                patch += 'deleted file mode 100644\n'
            patch += '@@ -7,1 +7,1 @@\n'
            patch += ('-'+old+'\n') if old else ''
            patch += ('+'+new+'\n') if new else ''
        (rd/'diff.patch').write_text(patch)
        weak = weakening.scan(str(rd))
        self.assertTrue({'test-file-deleted', 'assertion-changed', 'assertions-removed',
                         'suppression-added', 'ci-check-removed', 'ci-weakened'} <= {x[0] for x in weak})
        weakening.write(str(rd), weak)
        (rd/'meta.txt').write_text('mode=local\ndepth=standard\nonly=bug\n')
        out = self.run_script('report-lines.sh', str(rd)).stdout
        self.assertEqual(out.count('WEAKEN:'), len(weak))
        self.assertNotIn('FEEDBACK:', out)

    def test_agent_template_requires_neutral_signals_and_patch_locations(self):
        root = Path(fixture.SCRIPTS).parent
        template = (root/'templates/report.md').read_text()
        contract = (root/'references/report-format.md').read_text()
        self.assertIn('### 测试与检查变更', template)
        self.assertNotIn('测试和检查被削弱', template)
        for phrase in ('审查未完成', '已检查部分确认 <N> 条发现', '不计入发现条数'):
            self.assertIn(phrase, template)
        for phrase in ('hunk 的新旧侧行号', '删除行注明「旧版」', '不得从说明文字猜行号',
                       '无法定位时如实说明', '不得编造修复 diff', '**问题代码**必须是原文',
                       '仅有 `subagent_note=`', '0 展示条目时不默认提示'):
            self.assertIn(phrase, contract)


class ReviewIntegrityTests(fixture.Base):
    def setup_review(self):
        repo = self.make_repo('app', {'src/a.py': 'old\nold\n'})
        base = self.git(repo, 'rev-parse', 'HEAD')
        head = self.commit(repo, {'src/a.py': 'new\nnew\n'}, 'change')
        rd = Path(self.review_dir(self.run_script('collect-diff.sh', base+'..'+head, cwd=repo).stdout))
        self.run_script('collect-rules.sh', str(rd))
        c = dict(cid='c1', id='prior-1', from_prev=True, file='src/a.py', line=1,
                 category='bug', severity='major', summary='prior bug', failure_scenario='x')
        (rd/'candidates.json').write_text(json.dumps([c]))
        self.run_script('prepare-verify.sh', str(rd))
        meta = (rd/'meta.txt').read_text().replace('review_scope=full', 'review_scope=incremental')
        (rd/'meta.txt').write_text(meta)
        (rd/'prev-findings.json').write_text(json.dumps([dict(c, confidence=95)]))
        (rd/'incremental.txt').write_text('changed\tsrc/a.py\n')
        return repo, rd, c

    def result(self, c, score=10):
        return dict(c, confidence=score, verdict='checked', evidence=[{'file':'src/a.py','lines':'1'}])

    def test_missing_result_stops_without_resolving_or_exposing_stale_report(self):
        _, rd, c = self.setup_review()
        (rd/'final.json').write_text('{"sentinel":true}')
        (rd/'findings.json').write_text('[]')
        (rd/'report.md').write_text('stale report')
        r = self.run_script('aggregate.sh', str(rd), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn('核实未完成', r.stderr)
        self.assertEqual(json.loads((rd/'final.json').read_text()), {'sentinel':True})
        self.assertNotEqual(self.run_script('report-lines.sh', str(rd), check=False).returncode, 0)
        self.assertNotEqual(self.run_script('save-review.sh', str(rd), check=False).returncode, 0)
        (rd/'verify/result-1.json').write_text(json.dumps([self.result(c)]))
        self.run_script('aggregate.sh', str(rd))
        self.assertFalse((rd/'verification-errors.json').exists())
        self.assertEqual([x['id'] for x in self.load(rd/'final.json')['resolved']], ['prior-1'])

    def test_omitted_previous_candidate_is_not_resolved(self):
        _, rd, c = self.setup_review()
        (rd/'candidates.json').write_text('[]')
        for p in (rd/'verify').glob('*'):p.unlink()
        r = self.run_script('aggregate.sh', str(rd), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn('遗漏待重新核实', r.stderr)
        self.assertFalse((rd/'final.json').exists())

    def test_invalid_duplicate_and_wrong_identity_results_stop(self):
        _, rd, c = self.setup_review()
        good = self.result(c)
        variants = ['{bad', json.dumps([good,good]), json.dumps([dict(good, confidence='bad')]),
                    json.dumps([dict(good, id='wrong')]), json.dumps([dict(good, cid='other')])]
        for text in variants:
            with self.subTest(text=text):
                (rd/'verify/result-1.json').write_text(text)
                self.assertEqual(self.run_script('aggregate.sh', str(rd), check=False).returncode, 2)
                self.assertFalse((rd/'final.json').exists())

    def test_verified_batch_cannot_be_silently_removed_from_candidates(self):
        _, rd, c = self.setup_review()
        (rd/'meta.txt').write_text((rd/'meta.txt').read_text().replace('review_scope=incremental','review_scope=full'))
        (rd/'candidates.json').write_text('[]')
        (rd/'verify/result-1.json').write_text(json.dumps([self.result(c,95)]))
        r=self.run_script('aggregate.sh',str(rd),check=False)
        self.assertEqual(r.returncode,2)
        self.assertIn('候选与核实批次不一致',r.stderr)

    def test_prepare_invalidates_previous_verification_outputs(self):
        _, rd, c = self.setup_review()
        for file in ['verify/result-1.json','final.json','findings.json']:
            (rd/file).write_text('[]')
        self.run_script('prepare-verify.sh', str(rd))
        for file in ['verify/result-1.json','final.json','findings.json']:
            self.assertFalse((rd/file).exists())

    def test_finder_mapping_restores_omitted_candidate_and_preserves_merge_scenarios(self):
        _, rd, c = self.setup_review()
        (rd/'meta.txt').write_text((rd/'meta.txt').read_text().replace('review_scope=incremental', 'review_scope=full'))
        raw = [dict(c, summary='first', failure_scenario='input A fails', needs_check='guard A'),
               dict(c, summary='second', failure_scenario='input B fails', needs_check='guard B')]
        for row in raw:
            row.pop('from_prev'); row.pop('id'); row.pop('cid')
        (rd/'finders').mkdir()
        original = json.dumps({'candidates': raw, 'coverage': {'limited': False, 'budget_reached': False}})
        (rd/'finders/reviewer.json').write_text(original)
        merged = dict(raw[0], source_refs=['finders/reviewer.json#0', 'finders/reviewer.json#1'], merge_reason='same branch and local fix')
        (rd/'candidates.json').write_text(json.dumps([merged]))
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'candidates.json')
        self.assertEqual(len(sent), 1)
        self.assertIn('input B fails', sent[0]['failure_scenario'])
        self.assertIn('guard B', sent[0]['needs_check'])
        batch = self.load(rd/'verify/batch-1.json')[0]
        self.assertNotIn('source_refs', batch)
        self.assertNotIn('merge_reason', batch)
        self.assertEqual((rd/'finders/reviewer.json').read_text(), original)
        (rd/'verify/result-1.json').write_text(json.dumps([self.result(batch, 95)]))
        self.run_script('aggregate.sh', str(rd))
        finding = self.load(rd/'findings.json')[0]
        self.assertEqual(finding['source_refs'], merged['source_refs'])
        self.assertEqual(finding['candidate_ids'], ['c1'])
        (rd/'candidates.json').write_text(json.dumps([raw[0]]))
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'candidates.json')
        self.assertEqual(len(sent), 2, '已交的遗漏候选必须补回')
        self.assertEqual({ref for row in sent for ref in row['source_refs']}, {'finders/reviewer.json#0', 'finders/reviewer.json#1'})

    def test_exact_duplicates_retain_both_candidate_ids(self):
        _, rd, c = self.setup_review()
        (rd/'meta.txt').write_text((rd/'meta.txt').read_text().replace('review_scope=incremental', 'review_scope=full'))
        c = {k: v for k, v in c.items() if k not in ('from_prev', 'id', 'cid')}
        (rd/'candidates.json').write_text(json.dumps([c, c]))
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'verify/batch-1.json')
        (rd/'verify/result-1.json').write_text(json.dumps([self.result(x, 95) for x in sent]))
        self.run_script('aggregate.sh', str(rd))
        findings = self.load(rd/'findings.json')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['candidate_ids'], ['c1', 'c2'])

    def test_wontfix_preserves_fact_score_and_requires_current_context(self):
        _, rd, c = self.setup_review()
        (rd/'history.json').write_text(json.dumps([dict(c, verdict='wontfix', reason='defer', same_code=True)]))
        result = dict(self.result(c, 95), history_match=c['id'], history_context='调用输入和历史前提仍相同')
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        final = self.load(rd/'final.json')
        ignored = final['ignored_by_history'][0]
        self.assertEqual((ignored['confidence'], ignored['verdict'], ignored['suppression_reason']), (95, 'checked', 'defer'))
        self.assertEqual(final['resolved'], [])
        lines = self.run_script('report-lines.sh', str(rd)).stdout
        self.assertIn('抑制重复提醒 1 条', lines)
        result.pop('history_context')
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(len(self.load(rd/'findings.json')), 1, '局部代码未变不足以抑制当前结论')
        result['history_context'] = '当前发现重新成立'
        (rd/'history.json').write_text(json.dumps([dict(c, verdict='invalid', reason='old guard', same_code=True)]))
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(len(self.load(rd/'findings.json')), 1, '历史 invalid 不得盖过当前确认事实')

    def test_null_evidence_is_rejected_without_snapshot_crash(self):
        _, rd, c = self.setup_review()
        (rd/'verify/result-1.json').write_text(json.dumps([dict(self.result(c, 95), evidence=None)]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(self.load(rd/'findings.json'), [])
        self.assertIn('没有附依据', self.load(rd/'rejected.json')[0]['reject_reason'])

    def test_evidence_change_rechecks_callee_and_legacy_without_snapshot(self):
        repo, rd, c = self.setup_review()
        self.write(repo, {'src/caller.py': 'call(21)\n', 'src/unrelated.py': 'one\n'})
        import crlib
        source = crlib.code_source(self.kv(rd/'meta.txt'))
        old = dict(c, evidence=[{'file':'src/caller.py', 'lines':'1'}], confidence=95,
                   verdict='old', root_cause={'file':'old'})
        old['evidence_snapshot'] = crlib.evidence_snapshot(old, source)
        (rd/'incremental.txt').write_text('unchanged\tsrc/a.py\nchanged\tsrc/unrelated.py\n')
        (rd/'prev-findings.json').write_text(json.dumps([old]))
        (rd/'candidates.json').write_text('[]')
        self.write(repo, {'src/unrelated.py':'two\n'})
        self.run_script('prepare-verify.sh', str(rd))
        self.assertEqual(self.load(rd/'candidates.json'), [])
        self.run_script('aggregate.sh', str(rd))
        self.assertTrue(self.load(rd/'findings.json')[0]['evidence_snapshot'])
        self.write(repo, {'src/caller.py': 'call(31)\n'})
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'candidates.json')
        self.assertEqual([x['id'] for x in sent], ['prior-1'])
        self.assertIn('证据', sent[0]['recheck_reason'])
        self.assertNotIn('evidence', sent[0])
        self.assertNotIn('root_cause', sent[0])
        self.assertEqual(self.run_script('aggregate.sh', str(rd), check=False).returncode, 2)
        old.pop('evidence_snapshot')
        (rd/'prev-findings.json').write_text(json.dumps([old]))
        (rd/'candidates.json').write_text('[]')
        self.run_script('prepare-verify.sh', str(rd))
        self.assertIn('缺少', self.load(rd/'candidates.json')[0]['recheck_reason'])

    def test_feedback_anchor_survives_full_report_id_change(self):
        _, rd, c = self.setup_review()
        import crlib
        old = dict(c, id='full-new-id', code_line='new', context_hash='anchor',
                   evidence=[{'file':'src/a.py','lines':'1'}])
        old['evidence_snapshot'] = crlib.evidence_snapshot(old, crlib.code_source(self.kv(rd/'meta.txt')))
        h = dict(old, id='original-id', verdict='wontfix', reason='old premise')
        (rd/'history.json').write_text(json.dumps([h]))
        (rd/'prev-findings.json').write_text(json.dumps([old]))
        (rd/'incremental.txt').write_text('unchanged\tsrc/a.py\n')
        (rd/'candidates.json').write_text('[]')
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'verify/batch-1.json')[0]
        self.assertEqual(sent['id'], 'full-new-id')
        self.assertIn('历史反馈', sent['recheck_reason'])
        result = dict(self.result(sent, 95), history_match='original-id', history_context='premise checked')
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(len(self.load(rd/'final.json')['ignored_by_history']), 1)
        self.assertEqual(self.load(rd/'findings.json'), [])

    def test_reverified_callee_merges_new_caller_and_refreshes_input(self):
        repo, rd, c = self.setup_review()
        self.write(repo, {'src/caller.py':'call(21)\n'})
        import crlib
        old = dict(c, failure_scenario='input 21', evidence=[{'file':'src/caller.py','lines':'1'}])
        old['evidence_snapshot'] = crlib.evidence_snapshot(old, crlib.code_source(self.kv(rd/'meta.txt')))
        self.write(repo, {'src/caller.py':'call(31)\n'})
        (rd/'prev-findings.json').write_text(json.dumps([old]))
        (rd/'incremental.txt').write_text('unchanged\tsrc/a.py\nchanged\tsrc/caller.py\n')
        with (rd/'ranges.txt').open('a') as stream: stream.write('src/caller.py\t1\t1\n')
        fresh = dict(file='src/caller.py', line=1, summary='caller fails for 31',
                     category='bug', severity='major', failure_scenario='input 31')
        (rd/'candidates.json').write_text(json.dumps([fresh]))
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'candidates.json')
        self.assertEqual(len(sent), 2)
        root = dict(file='src/a.py', lines='1', mechanism='floor(total/size)',
                    constraint='cover all records', fix='ceil(total/size)')
        results = [dict(x, confidence=95, verdict='current input checked', failure_scenario='input 31',
                        root_cause=root, evidence=[{'file':'src/a.py','lines':'1'},
                                                  {'file':'src/caller.py','lines':'1','note':'input 31'}]) for x in sent]
        (rd/'verify/result-1.json').write_text(json.dumps(results))
        self.run_script('aggregate.sh', str(rd))
        findings = self.load(rd/'findings.json')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['id'], 'prior-1')
        self.assertEqual(set(findings[0]['candidate_ids']), {'c1','c2'})
        self.assertNotIn('21', findings[0]['failure_scenario'])
        self.assertNotIn('carried_unchanged', self.load(rd/'final.json')['groups']['defects'][0])

    def test_verified_root_merges_different_locations_but_not_different_causes(self):
        _, rd, c = self.setup_review()
        (rd/'meta.txt').write_text((rd/'meta.txt').read_text().replace('review_scope=incremental','review_scope=full'))
        root = dict(file='src/a.py', lines='1', mechanism='floor(total/size)',
                    constraint='cover all records', fix='ceil(total/size)')
        rows = [dict(c, summary='callee', line=1), dict(c, summary='caller', line=2),
                dict(c, summary='other condition', line=2)]
        for row in rows:
            for k in ('id','cid','from_prev'): row.pop(k, None)
        (rd/'candidates.json').write_text(json.dumps(rows))
        self.run_script('prepare-verify.sh', str(rd))
        sent = self.load(rd/'verify/batch-1.json')
        results = [dict(self.result(x, 95), root_cause=dict(root), failure_scenario=x['summary']) for x in sent]
        results[2]['root_cause']['constraint'] = 'size must be positive'
        results[2]['root_cause']['mechanism'] = 'division by zero'
        (rd/'verify/result-1.json').write_text(json.dumps(results))
        self.run_script('aggregate.sh', str(rd))
        findings = self.load(rd/'findings.json')
        self.assertEqual(len(findings), 2)
        merged = next(x for x in findings if len(x['candidate_ids']) == 2)
        self.assertEqual(merged['candidate_ids'], ['c1','c2'])
        self.assertIn('caller', merged['failure_scenario'])
        import crlib
        self.assertEqual(crlib.feedback_key(dict(merged, id='one')), crlib.feedback_key(dict(merged, id='two')))

    def test_hidden_directory_rules_do_not_escape_collection(self):
        local = ['.github/workflows/AGENTS.md', '.claude/helpers/CLAUDE.md', '.cursor/sub/AGENTS.md', '.github/sub/REVIEW.md', '.claude/rules/nested/REVIEW.md']
        files = {'src/a.py':'x\n', '.claude/CLAUDE.md':'global claude\n', '.github/copilot-instructions.md':'global copilot\n'}
        for name in local:
            files[name] = '- local rule\n'
            files[str(Path(name).parent/'job.py')] = 'x\n'
        repo = self.make_repo('rules', files)
        for name in local:
            r = project_rules.describe(name, files[name])
            self.assertFalse(project_rules.matches(r,'src/a.py'))
            self.assertTrue(project_rules.matches(r,str(Path(name).parent/'job.py')))
        rd = Path(self.tmp,'rd');rd.mkdir()
        (rd/'meta.txt').write_text('repo_root='+repo+'\nmode=local\n')
        (rd/'files.txt').write_text('src/a.py\n')
        self.run_script('collect-rules.sh',str(rd))
        loaded = {x['file'] for x in self.load(rd/'rules-index.json')}
        self.assertFalse(loaded.intersection(local))
        self.assertEqual(loaded,{'.claude/CLAUDE.md','.github/copilot-instructions.md'})

