"""目录边界、核实完整性和报告分组的回归，不调用模型。"""
import copy
import json
import sys
from pathlib import Path

import test_crosscheck as fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import project_rules
import report_groups


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
        self.run_script('prepare-verify.sh', str(rd), '--spot', '0')
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

    def test_missing_spot_result_stops_even_without_normal_candidates(self):
        _, rd, _ = self.setup_review()
        (rd/'meta.txt').write_text((rd/'meta.txt').read_text().replace('review_scope=incremental','review_scope=full'))
        (rd/'candidates.json').write_text('[]')
        (rd/'verify/batch-1.json').write_text('[{"cid":"s1","kind":"spot"}]')
        r = self.run_script('aggregate.sh', str(rd), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn('s1', r.stderr)

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
        for file in ['verify/result-1.json','final.json','findings.json','report.json','display-groups.json']:
            (rd/file).write_text('[]')
        self.run_script('prepare-verify.sh', str(rd), '--spot', '0')
        for file in ['verify/result-1.json','final.json','findings.json','report.json','display-groups.json']:
            self.assertFalse((rd/file).exists())

    def test_hidden_directory_rules_do_not_escape_in_collection_or_maintenance(self):
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
        ops=Path(self.tmp,'ops.json')
        ops.write_text(json.dumps([dict(op='add',file=local[0],text='new rule',paths={'match':['src/a.py'],'miss':[]})]))
        r=self.run_script('propose-rules.sh','--repo',repo,'--ops',str(ops),'--yes',check=False)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.git(repo,'status','--short'),'')

    def display_fixture(self):
        security=dict(id='sql-security',no=1,category='security',file='src/a.py',line=1,confidence=95,severity='major',summary='SQL input changes query',evidence=[{'file':'src/a.py','lines':'1'}])
        rule=dict(id='sql-rule',no=2,category='rules',file='src/a.py',line=1,confidence=90,severity='minor',summary='SQL must bind params',rule_id='PRJ-001',rule_quote='Use bound parameters',rule_origin='REVIEW.md',evidence=[{'file':'src/a.py','lines':'1'}])
        partial=dict(id='partial-page',no=3,category='bug',file='src/a.py',line=2,confidence=95,severity='major',summary='Rounding loses a page')
        empty=dict(id='empty-page',no=4,category='intent',file='src/a.py',line=2,confidence=95,severity='major',summary='Empty list should have zero pages')
        return {'groups':{'defects':[security,partial],'intent':[empty],'rules':[rule],'simplify':[]}}

    def test_display_group_keeps_all_evidence_and_independent_issues(self):
        final=self.display_fixture();original=copy.deepcopy(final)
        plan=Path(self.tmp,'groups.json')
        plan.write_text(json.dumps([{'primary':'sql-security','members':['sql-security','sql-rule'],'reason':'Same interpolation and parameter-binding repair'}]))
        report=report_groups.build(final,str(plan))
        self.assertEqual(final,original)
        self.assertEqual(len(report['groups']['defects']),2)
        self.assertEqual(len(report['groups']['intent']),1)
        self.assertEqual(report['groups']['rules'],[])
        root=report['groups']['defects'][0]
        self.assertEqual(root['confidence'],90)
        self.assertEqual(root['related_findings'][0],final['groups']['rules'][0])
        self.assertEqual([x['no'] for x in root['display_members']],[1,2])

    def test_display_never_groups_failed_unknown_or_overlapping_records(self):
        final=self.display_fixture();p=Path(self.tmp,'groups.json')
        row={'primary':'sql-security','members':['sql-security','sql-rule'],'reason':'same'}
        for plan in [[dict(row,members=['sql-security','unknown'])],[row,row],[dict(row,reason='')]]:
            p.write_text(json.dumps(plan))
            with self.assertRaises(ValueError):report_groups.build(final,str(p))
        p.write_text(json.dumps([row]))
        for score in [79,50]:
            bad=copy.deepcopy(final);bad['groups']['rules'][0]['confidence']=score
            with self.assertRaises(ValueError):report_groups.build(bad,str(p))
        bad=copy.deepcopy(final);bad['groups']['rules'][0]['file']='src/b.py'
        with self.assertRaises(ValueError):report_groups.build(bad,str(p))

    def test_display_ignores_unvalidated_nested_findings_and_no_plan_is_compatible(self):
        final=self.display_fixture();f=final['groups']['defects'][0]
        f['related_findings']=[dict(id='invented',confidence=100)]
        report=report_groups.build(final,str(Path(self.tmp,'absent.json')))
        self.assertNotIn('related_findings',report['groups']['defects'][0])
        self.assertEqual(len(report['groups']['rules']),1)

    def test_report_lines_generates_display_without_rewriting_archive(self):
        _,rd,_=self.setup_review()
        final=self.display_fixture()
        (rd/'final.json').write_text(json.dumps(final))
        (rd/'findings.json').write_text(json.dumps([f for fs in final['groups'].values() for f in fs]))
        original=(rd/'findings.json').read_bytes()
        (rd/'display-groups.json').write_text(json.dumps([{'primary':'sql-security','members':['sql-security','sql-rule'],'reason':'Same SQL construction'}]))
        self.run_script('report-lines.sh',str(rd))
        self.assertEqual(self.load(rd/'report.json')['groups']['rules'],[])
        self.assertEqual((rd/'findings.json').read_bytes(),original)
        self.assertEqual(self.load(rd/'final.json'),final)
        (rd/'display-groups.json').write_text('[{"primary":"unknown","members":["unknown","sql-rule"],"reason":"same"}]')
        self.assertNotEqual(self.run_script('report-lines.sh',str(rd),check=False).returncode,0)
        self.assertFalse((rd/'report.json').exists())
