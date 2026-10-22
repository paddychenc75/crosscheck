"""外部只读材料与保守重审的端到端回归；不调用外部服务。"""
import json
import os
from pathlib import Path

import test_crosscheck as fixture


class MaterialsTests(fixture.Base):
    run_pipeline = fixture.TestPipeline.run_pipeline
    auditor = fixture.TestPipeline.auditor

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('app', {'src/a.ts': 'export const a = 1;\n'})
        self.git(self.repo, 'switch', '-qc', 'feature')
        self.commit(self.repo, {'src/a.ts': 'export const a = 2;\nexport const b = 3;\n'}, 'change')
        self.input = Path(self.tmp, 'materials.json')

    def material(self, source='team', ref='doc', text='禁止直接 fetch。\n日志不能包含邮箱。', **extra):
        return dict(dict(source=source, ref=ref, purpose='rules', scope=['src/**'], status='complete', text=text), **extra)

    def collect(self, rows=None, rules=True):
        if rows is not None:
            self.input.write_text(json.dumps(rows, ensure_ascii=False))
        r = self.run_script('collect-diff.sh', '--materials', str(self.input), cwd=self.repo)
        rd = self.review_dir(r.stdout)
        if rules:
            self.run_script('collect-rules.sh', rd)
        return rd, r.stdout

    def save(self, rd, findings=None):
        self.write(rd, {'report.md': '报告\n'})
        if findings is not None:
            self.write(rd, {'findings.json': json.dumps(findings)})
        r = self.run_script('save-review.sh', rd)
        return r.stdout.split('dir=')[1].splitlines()[0]

    def test_distinct_sources_same_native_rule_id_and_context(self):
        text = '- 禁止 fetch。 <!-- cr: id=ORG-001 -->'
        rd, _ = self.collect([self.material('one', text=text), self.material('two', text=text),
                              self.material('design', purpose='context', text='调用者已经验证输入'),
                              self.material('other', scope=['python/**'])])
        units = self.load(os.path.join(rd, 'rules-index.json'))
        self.assertEqual(len(units), 2)
        self.assertEqual(len({x['unit'] for x in units}), 2)
        self.assertEqual({x['source_name'] for x in units}, {'one', 'two'})
        self.assertTrue(all(x['document'] for x in units))
        self.assertIn('调用者已经验证输入', Path(rd, 'context-materials.md').read_text())
        self.assertNotIn('调用者已经验证输入', Path(rd, 'rules.txt').read_text())

    def test_brace_scopes_match_without_becoming_comma_fragments(self):
        rd, _ = self.collect([self.material(scope=['src/*.{ts,tsx}'])])
        self.assertEqual(len(self.load(os.path.join(rd, 'rules-index.json'))), 1)

    def test_missing_and_invalid_input_are_visible(self):
        rd, _ = self.collect()
        self.assertFalse(self.load(os.path.join(rd, 'materials/manifest.json'))['complete'])
        self.assertIn('未完成', self.run_script('report-lines.sh', rd).stdout)
        for rows in [[self.material(status='complete', text='')], [self.material(purpose='tool')],
                     [self.material(), self.material(text='另一份正文')]]:
            with self.subTest(rows=rows):
                rd, _ = self.collect(rows)
                self.assertFalse(self.load(os.path.join(rd, 'materials/manifest.json'))['complete'])
                self.assertEqual(self.load(os.path.join(rd, 'rules-index.json')), [])

    def test_snapshot_is_frozen_and_archived(self):
        rd, _ = self.collect([self.material(text='旧规范')], rules=False)
        self.input.write_text(json.dumps([self.material(text='新规范')]))
        self.run_script('collect-rules.sh', rd)
        self.assertIn('旧规范', Path(rd, 'rules.txt').read_text())
        self.assertNotIn('新规范', Path(rd, 'rules.txt').read_text())
        saved = self.save(rd, [])
        self.assertTrue(Path(saved, 'context-hash.txt').exists())
        manifest = self.load(os.path.join(saved, 'materials/manifest.json'))
        self.assertEqual(Path(saved, manifest['records'][0]['file']).read_text(), '旧规范')

    def test_material_change_or_failure_forces_full_target_review(self):
        rd, _ = self.collect([self.material()])
        self.save(rd, [])
        rd2, out = self.collect()
        self.assertIn('UNCHANGED', out)
        rd3, out = self.collect([self.material(text='新规范')])
        self.assertEqual(self.kv(os.path.join(rd3, 'meta.txt'))['review_scope'], 'full')
        self.assertIn('src/a.ts', Path(rd3, 'files.txt').read_text())
        rd4, _ = self.collect([self.material(status='partial')])
        self.assertEqual(self.kv(os.path.join(rd4, 'meta.txt'))['review_scope'], 'full')

    def test_context_change_and_project_rule_change_force_full_review(self):
        rd, _ = self.collect([self.material(purpose='context', text='上游校验')])
        self.save(rd, [])
        rd2, _ = self.collect([self.material(purpose='context', text='上游不再校验')])
        self.assertEqual(self.kv(os.path.join(rd2, 'meta.txt'))['review_scope'], 'full')
        self.save(rd2, [])
        self.write(self.repo, {'REVIEW.md': '- 新增项目要求。 <!-- cr: id=PRJ-001 -->'})
        rd3, _ = self.collect()
        self.assertEqual(self.kv(os.path.join(rd3, 'meta.txt'))['review_scope'], 'full')

    def test_two_requirements_in_document_remain_two_findings(self):
        rd, _ = self.collect([self.material()])
        unit = self.load(os.path.join(rd, 'rules-index.json'))[0]
        cands = [dict(file='src/a.ts', line=1, category='rules', rule_id=unit['unit'],
                      rule_quote=q, severity='minor', summary=q, failure_scenario=q)
                 for q in ['禁止直接 fetch。', '日志不能包含邮箱。']]
        scores = {c['summary']: (100, [dict(file='src/a.ts', lines='1'), dict(file=unit['file'], lines='1-2')]) for c in cands}
        final, _ = self.run_pipeline(rd, cands, scores)
        self.assertEqual(len(final['groups']['rules']), 2)
        self.assertTrue(all(x['rule_origin'] == 'doc' for x in final['groups']['rules']))

    def test_partial_document_does_not_resolve_other_requirement(self):
        rd, _ = self.collect([self.material()])
        rid = self.load(os.path.join(rd, 'rules-index.json'))[0]['unit']
        old = dict(id='old-email', file='src/a.ts', line=1, category='rules', rule_id=rid,
                   summary='邮箱日志', rule_quote='日志不能包含邮箱。')
        self.save(rd, [old])
        rd2, _ = self.collect([self.material(text='禁止直接 fetch。', status='partial')])
        c = dict(file='src/a.ts', line=1, category='rules', rule_id=rid, summary='直接 fetch',
                 rule_quote='禁止直接 fetch。', failure_scenario='x')
        final, _ = self.run_pipeline(rd2, [c], {'直接 fetch': (100, [dict(file='src/a.ts', lines='1')])})
        self.assertEqual([x['id'] for x in final['unverified']], ['old-email'])

    def test_zero_candidates_still_spot_checks_material_rules(self):
        rd, _ = self.collect([self.material()])
        unit = self.load(os.path.join(rd, 'rules-index.json'))[0]
        self.auditor(rd, [dict(rule_id=unit['unit'], files=['src/a.ts'], result='ok', lines='1-2')])
        self.run_script('check-rules.sh', rd)
        final, _ = self.run_pipeline(rd, [], {})
        self.assertEqual(final['spot']['checked'], 1)

    def test_source_failure_preserves_previous_findings_as_unverified(self):
        rd, _ = self.collect([self.material()])
        rid = self.load(os.path.join(rd, 'rules-index.json'))[0]['unit']
        finding = dict(id='prior-1', file='src/a.ts', line=1, category='rules', rule_id=rid, summary='邮箱日志', confidence=100)
        self.save(rd, [finding])
        for _ in range(2):
            rd2, _ = self.collect([self.material(status='unavailable', text='', note='无权限')])
            final, _ = self.run_pipeline(rd2, [], {})
            self.assertEqual([x['id'] for x in final['unverified']], ['prior-1'])
            self.assertEqual(final['resolved'], [])
            self.assertIn('未能复核', self.run_script('report-lines.sh', rd2).stdout)
            self.save(rd2)


class EvalInputsTests(fixture.Base):
    def test_explicit_cases_do_not_import_live_org_examples(self):
        repo = self.make_repo('app', {'a.ts': 'old'})
        head = self.commit(repo, {'a.ts': 'new'}, 'change')
        cases = Path(self.tmp, 'cases'); cases.mkdir()
        case = dict(id='one', repo='app', base=self.git(repo, 'rev-parse', 'HEAD~1'), head=head, source='clean', expect=[])
        (cases/'one.json').write_text(json.dumps(case))
        self.write(self.home, {'org-rules/packs/x/examples/ORG-001.bad.ts': 'bad'})
        self.config('repo.app=%s\n' % repo)
        run = Path(self.tmp, 'run')
        self.run_script('eval.sh', 'prepare', '--cases', str(cases), '--out', str(run))
        plan = self.load(str(run/'plan.json'))
        self.assertEqual([x['case'] for x in plan], ['one'])
        self.assertEqual(self.load(plan[0]['materials_file']), [])
        self.assertFalse((run/'rules/packs').exists())
        self.assertTrue(self.load(str(run/'dataset.json'))['hash'])

    def test_baseline_is_local_and_requires_confirmation(self):
        run = Path(self.tmp, 'run'); run.mkdir()
        (run/'score.json').write_text(json.dumps(dict(dataset_hash='abc', unjudged=0, incomplete=[])))
        self.run_script('eval.sh', 'baseline', str(run))
        dest = Path(self.home, 'eval/baseline.json')
        self.assertFalse(dest.exists())
        self.run_script('eval.sh', 'baseline', str(run), '--yes')
        self.assertTrue(dest.exists())
        (run/'score.json').write_text(json.dumps(dict(dataset_hash='abc', unjudged=1)))
        self.assertNotEqual(self.run_script('eval.sh', 'baseline', str(run), '--yes', check=False).returncode, 0)

    def test_score_blocks_unjudged_regression_and_incomparable_data(self):
        run = Path(self.tmp, 'score-run'); review = run/'cases/bug/review'; review.mkdir(parents=True)
        plan = [dict(case='bug', kind='fix-commit', expect=[dict(category='bug')])]
        pair = dict(case='bug', expect_index=0, finding_id='hit', same=True)
        extra = dict(case='bug', finding=dict(id='extra'))
        (run/'plan.json').write_text(json.dumps(plan))
        (run/'dataset.json').write_text(json.dumps(dict(hash='fixed-data')))
        (review/'final.json').write_text('{}')
        (review/'findings.json').write_text('[]')
        (run/'judge.json').write_text(json.dumps(dict(pairs=[dict(case='bug', expect_index=0, finding=dict(id='hit'))], extras=[extra])))
        baseline = dict(dataset_hash='fixed-data', recall=dict(hit=1, expected=1), precision=dict(valid=1, invalid=0), per_case=dict(bug=dict(hit=1, invalid=0)))
        base = Path(self.tmp, 'baseline.json'); base.write_text(json.dumps(baseline))
        for extras, expected in [([], '暂定'), ([dict(case='bug', finding_id='extra', valid=False)], '不建议合入'),
                                 ([dict(case='bug', finding_id='extra', valid=True)], '可以合入')]:
            (run/'judgments.json').write_text(json.dumps(dict(pairs=[pair], extras=extras)))
            self.run_script('eval.sh', 'score', str(run), '--baseline', str(base))
            self.assertIn(expected, self.load(str(run/'score.json'))['verdict'])
        baseline['dataset_hash'] = 'other-data'; base.write_text(json.dumps(baseline))
        self.run_script('eval.sh', 'score', str(run), '--baseline', str(base))
        self.assertIn('不可比较', self.load(str(run/'score.json'))['verdict'])
