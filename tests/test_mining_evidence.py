"""固定 Git 对象引文的真实性与路径边界；使用独立临时仓库。"""
import hashlib
import os
from pathlib import Path
import sys

import fixture

sys.path.insert(0, fixture.SCRIPTS)
from mining_evidence import CodeEvidence


class FixedCodeEvidence(fixture.Base):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('fixed-code', {
            'src/a.py': 'def load(limit):\n    return query(min(limit, 100))\n',
            'src/b.py': 'def load(limit):\n    return query(min(limit, 100))\n',
        })
        self.head = self.git(self.repo, 'rev-parse', 'HEAD')
        # CodeEvidence 的 Git 子进程和 fixture 使用同一隔离环境。
        self.old_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)
        self.reader = CodeEvidence(self.repo)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        super().tearDown()

    def ref(self, **changes):
        return dict(dict(ref=self.head, file='src/a.py', line=1,
                         quote='def load(limit):\n    return query(min(limit, 100))',
                         reason='最终入口查询前限制上限'), **changes)

    def test_reads_fixed_blob_and_hashes_full_content_ignoring_worktree(self):
        original = Path(self.repo, 'src/a.py').read_text()
        Path(self.repo, 'src/a.py').write_text('mutable wrong version\n')
        evidence = self.reader.reference(self.ref(), self.head)
        self.assertEqual('verified', evidence['status'])
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), evidence['content_sha256'])

    def test_quote_checks_whole_lines_and_whitespace(self):
        for changes in [dict(line=2), dict(line=True), dict(line=0), dict(line=999),
                        dict(quote='    return query(min(limit, 100))'),
                        dict(quote='def load(limit):\nreturn query(min(limit, 100))'),
                        dict(quote='def load(limit):\n    return query(min(limit, 100))\nextra')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reader.reference(self.ref(**changes), self.head)
        self.assertEqual('verified', self.reader.reference(self.ref(quote=self.ref()['quote'] + '\n'), self.head)['status'])

    def test_ref_must_match_frozen_head_even_if_other_commit_exists(self):
        other = self.commit(self.repo, {'src/a.py': 'other\n'}, 'other')
        with self.assertRaisesRegex(ValueError, 'ref 必须严格等于'):
            self.reader.reference(self.ref(ref=other, quote='other'), self.head)
        missing = 'f' * 40
        evidence = self.reader.reference(self.ref(ref=missing), missing)
        self.assertEqual('unavailable', evidence['status'])

    def test_rejects_unsafe_paths_and_missing_files(self):
        for path in ('/etc/passwd', '../a', './src/a.py', 'src/../a.py', '.git/config',
                     '.ENV.local', 'src/credentials.json', '.ssh/id_rsa', 'src\\a.py', 'src//a.py', 'missing.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.reader.reference(self.ref(file=path), self.head)

    def test_fixed_symlink_resolves_internal_file_and_rejects_external_or_credentials(self):
        links = {'inside.py': 'src/a.py', 'external.py': str(Path(self.tmp, 'outside.py')),
                 'credentials.py': '.env', 'git.py': '.git/config', 'loop.py': 'loop.py',
                 'traversal.py': '../outside.py'}
        Path(self.tmp, 'outside.py').write_text('private outside\n')
        for path, target in links.items():
            Path(self.repo, path).symlink_to(target)
        self.git(self.repo, 'add', '.')
        self.git(self.repo, 'commit', '-qm', 'links')
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        result = self.reader.reference(self.ref(ref=head, file='inside.py'), head)
        self.assertEqual('src/a.py', result['resolved_file'])
        self.assertEqual('verified', result['status'])
        for path in links.keys() - {'inside.py'}:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.reader.reference(self.ref(ref=head, file=path), head)

    def test_exact_duplicate_samples_keep_all_distinct_reasons(self):
        first = self.ref()
        second = self.ref(reason='另一项语义说明')
        rows = self.reader.references([first, second, first], self.head, 'examples')
        self.assertEqual(1, len(rows))
        self.assertEqual([first['reason'], second['reason']], rows[0]['reasons'])
        rows = self.reader.references([first, self.ref(file='src/b.py')], self.head, 'examples')
        self.assertEqual(2, len(rows))  # 同源或复制判断交给模型 reason，不靠文本相同臆断独立性。

    def test_every_practice_item_is_checked_against_meta_head(self):
        practice = dict(reason='独立入口,同样限制上限', scope=['src/**'],
                        examples=[self.ref()], counterexamples=[])
        checked = self.reader.practice(practice, self.head)
        self.assertEqual(1, len(checked['examples']))
        for changes in [dict(scope=[]), dict(scope=['../outside/**']), dict(reason=''),
                        dict(counterexamples=[self.ref(quote='fabricated')]),
                        dict(examples=[self.ref(ref='e' * 40)])]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reader.practice(dict(practice, **changes), self.head)
