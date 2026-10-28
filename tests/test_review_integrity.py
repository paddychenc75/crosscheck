"""目录边界和核实完整性的回归，不调用模型。"""
import json
import sys
from pathlib import Path

import fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import project_rules


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
        self.run_py('mr_note.py', str(rd))
        for name in ('mr.md', 'mr.html'):
            self.assertIn('按历史判断抑制 1 条(不计为已修复)', (rd/name).read_text())
        result.pop('history_context')
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(len(self.load(rd/'findings.json')), 1, '局部代码未变不足以抑制当前结论')
        result['history_context'] = '当前发现重新成立'
        (rd/'history.json').write_text(json.dumps([dict(c, verdict='invalid', reason='old guard', same_code=True)]))
        (rd/'verify/result-1.json').write_text(json.dumps([result]))
        self.run_script('aggregate.sh', str(rd))
        self.assertEqual(len(self.load(rd/'findings.json')), 1, '历史 invalid 不得盖过当前确认事实')

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

