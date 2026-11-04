"""规则挖掘的明确共享来源；所有读取只使用本地临时仓库。"""
import json
from pathlib import Path
import sys
from unittest import mock

import fixture

sys.dont_write_bytecode = True
sys.path.insert(0, fixture.SCRIPTS)
import crlib
import project_rules


class MiningRuleSourcesTests(fixture.Base):
    def put_file(self, path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_directory_links_only_collect_rules_and_keep_logical_scope(self):
        repo = Path(self.make_repo('app', {'a.py': 'x\n'}))
        shared = Path(self.tmp, 'shared')
        self.put_file(shared / 'python.md', '---\npaths: src/**/*.py\n---\nShared policy\n[Style](guides/style.txt)\n')
        self.put_file(shared / 'guides/style.txt', 'Text policy\n')

        self.put_file(shared / 'notes.txt', 'Not a rule\n')
        self.put_file(shared / 'nested/deep.md', 'Deep policy\n')
        (shared / 'nested/again').symlink_to(shared, target_is_directory=True)
        (repo / '.claude').mkdir()
        (repo / '.claude/rules').symlink_to(shared, target_is_directory=True)
        (repo / 'external').symlink_to(shared, target_is_directory=True)
        records = project_rules.mining_sources(repo)
        indexed = {r['file']: r for r in records}
        item = indexed['.claude/rules/python.md']
        self.assertEqual(item['source_path'], str((shared / 'python.md').resolve()))
        self.assertIn('Shared policy', item['text'])
        self.assertTrue(project_rules.matches(item, 'src/a.py'))
        self.assertFalse(project_rules.matches(item, 'a.py'))
        self.assertEqual(indexed['.claude/rules/guides/style.txt']['patterns'], ['src/**/*.py'])
        self.assertIn('.claude/rules/nested/deep.md', indexed)
        self.assertTrue(indexed['.claude/rules/nested/again']['issues'])
        self.assertNotIn('.claude/rules/notes.txt', indexed)
        self.assertFalse(any(r['file'].startswith('external/') for r in records))
        self.assertEqual(json.loads(json.dumps(records)), records)

    def test_file_link_and_references_read_real_origin_and_inherit_scope(self):
        repo = Path(self.make_repo('app', {'src/a.py': 'x\n'}))
        shared = Path(self.tmp, 'shared')
        origin = self.put_file(shared / 'main.md', '[Style](style.txt#format)\n@./extra.md\n')
        style = self.put_file(shared / 'style.txt', 'Entire policy\nSecond line\n')
        self.put_file(shared / 'extra.md', 'Additional policy\n')
        self.put_file(repo / 'src/style.txt', 'Wrong local policy\n')
        (repo / 'src/AGENTS.md').symlink_to(origin)
        records = project_rules.mining_sources(repo)
        indexed = {r['file']: r for r in records}
        self.assertEqual(indexed['src/AGENTS.md']['source_path'], str(origin.resolve()))
        item = indexed['src/style.txt']
        self.assertEqual(item['source_path'], str(style.resolve()))
        self.assertEqual(item['text'], style.read_text())
        self.assertEqual(item['referenced_by'], 'src/AGENTS.md')
        self.assertEqual(item['patterns'], ['src/**'])
        self.assertTrue(project_rules.matches(item, 'src/a.py'))
        self.assertFalse(project_rules.matches(item, 'a.py'))
        self.assertEqual(indexed['src/extra.md']['patterns'], ['src/**'])

    def test_only_explicit_local_file_references_are_read(self):
        repo = Path(self.make_repo('app', {'CLAUDE.md': '''Ordinary prose docs/prose.md and user@example.com
@someone
@user@example.com
    [Code](docs/indented.md)
Run cat docs/command.md
@cat docs/command.md
`@docs/code.md`
```sh
@docs/fenced.md
```
![image](docs/image.md)
[Remote](https://example.invalid/rules.md)
[Network](//example.invalid/rules.md)
[Email](mailto:user@example.com)
[Anchor](#local)
[Title](docs/linked.txt "Local rules")
[Spaces](<docs/with%20space.txt>)
@docs/standalone.txt
[Directory](docs)
'''}))
        for name in ('prose.md', 'command.md', 'code.md', 'fenced.md', 'image.md',
                     'linked.txt', 'with space.txt', 'standalone.txt'):
            self.put_file(repo / 'docs' / name, 'Policy ' + name)
        records = project_rules.mining_sources(repo)
        indexed = {r['file']: r for r in records}
        self.assertEqual(set(indexed), {'CLAUDE.md', 'docs/linked.txt', 'docs/with space.txt',
                                       'docs/standalone.txt', 'docs'})
        self.assertTrue(indexed['docs']['issues'])

    def test_broken_links_cycles_and_unreadable_files_are_records(self):
        repo = Path(self.make_repo('app', {'CLAUDE.md': '[Missing](missing.txt)\n[Cycle](other.md)\n[Unreadable](denied.txt)\n'}))
        self.put_file(repo / 'other.md', '[Back](CLAUDE.md)\n')
        denied = self.put_file(repo / 'denied.txt', 'Hidden rule\n')
        (repo / 'AGENTS.md').symlink_to(repo / 'absent.md')
        (repo / 'REVIEW.md').symlink_to(repo / 'REVIEW.md')
        (repo / '.agents').mkdir()
        (repo / '.agents/rules').symlink_to(repo / 'absent-dir', target_is_directory=True)
        (repo / '.cursor').symlink_to(repo / 'absent-config', target_is_directory=True)
        original_open = open

        def guarded_open(path, *args, **kwargs):
            if Path(path).resolve() == denied.resolve():     # 临时目录可能经过符号链接,比较物理路径
                raise PermissionError('test denied')
            return original_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=guarded_open):
            records = project_rules.mining_sources(repo)
        for file in ('missing.txt', 'denied.txt', 'AGENTS.md', 'REVIEW.md', '.agents/rules', '.cursor'):
            item = next(r for r in records if r['file'] == file)
            self.assertTrue(item['issues'], file)
            self.assertEqual(item['text'], '')
            self.assertTrue(Path(item['source_path']).is_absolute())
        self.assertTrue(any(r['file'] == 'CLAUDE.md' and r['issues'] for r in records))

    def test_credential_paths_and_nontext_are_never_frozen(self):
        repo = Path(self.make_repo('app', {'CLAUDE.md': '[Env](.env)\n[Key](private.pem)\n[Alias](policy.txt)\n[Binary](binary.txt)\n[Shell](.zshrc)\n[Git](.git/config)\n'}))
        env = self.put_file(repo / '.env', 'DO_NOT_READ\n')
        key = self.put_file(repo / 'private.pem', 'DO_NOT_READ\n')
        (repo / 'policy.txt').symlink_to(env)
        (repo / 'binary.txt').write_bytes(b'\x00binary')
        original_open = open

        def guarded_open(path, *args, **kwargs):
            if Path(path) in (env, key, repo / 'policy.txt', repo / '.zshrc', repo / '.git/config'):
                self.fail('attempted credential read')
            return original_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=guarded_open):
            indexed = {r['file']: r for r in project_rules.mining_sources(repo)}
        for file in ('.env', 'private.pem', 'policy.txt', 'binary.txt', '.zshrc', '.git/config'):
            self.assertTrue(indexed[file]['issues'])
            self.assertEqual(indexed[file]['text'], '')

    def test_source_and_commit_discovery_do_not_follow_shared_directories(self):
        repo = Path(self.make_repo('app', {'CLAUDE.md': 'Committed policy\n'}))
        shared = Path(self.tmp, 'shared')
        self.put_file(shared / 'rule.md', 'Shared old\n')
        (repo / '.claude').mkdir()
        (repo / '.claude/rules').symlink_to(shared, target_is_directory=True)
        self.git(repo, 'add', '.claude/rules')
        self.git(repo, 'commit', '-qm', 'record local symlink')
        ref = self.git(repo, 'rev-parse', 'HEAD').strip()
        self.put_file(repo / 'CLAUDE.md', 'Working policy\n')
        self.put_file(shared / 'rule.md', 'Shared changed\n')
        current = project_rules.discover(crlib.Source(str(repo)))
        committed = project_rules.discover(crlib.Source(str(repo), ref))
        self.assertEqual([(r['file'], r['text']) for r in current], [('CLAUDE.md', 'Working policy\n')])
        self.assertEqual([(r['file'], r['text']) for r in committed], [('CLAUDE.md', 'Committed policy\n')])
        self.assertIn('.claude/rules/rule.md', {r['file'] for r in project_rules.mining_sources(repo)})

    def test_linked_configuration_directory_exposes_known_entry_only(self):
        repo = Path(self.make_repo('app', {'a.py': 'x\n'}))
        shared = Path(self.tmp, 'configuration')
        self.put_file(shared / 'CLAUDE.md', 'Configuration policy\n')
        self.put_file(shared / 'random/AGENTS.md', 'Not an explicit rule root\n')
        (repo / '.claude').symlink_to(shared, target_is_directory=True)
        records = project_rules.mining_sources(repo)
        self.assertEqual([r['file'] for r in records], ['.claude/CLAUDE.md'])
        self.assertEqual(records[0]['patterns'], ['**'])
