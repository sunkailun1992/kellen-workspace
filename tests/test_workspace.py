"""Real Git integration tests; all repositories and configuration are temporary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'workspace.py'
PROJECT_ROOT = SCRIPT.parent.parent
MOBILE = 'ai-wechat-mini-programs'


class ManifestScopeTests(unittest.TestCase):
    def test_workspace_contains_only_eleven_java_and_admin_web(self):
        manifest = json.loads((PROJECT_ROOT / 'repos.json').read_text())
        repos = manifest['repositories']
        self.assertEqual(len(repos), 12)
        self.assertEqual(sum(repo['kind'].startswith('java-') for repo in repos), 11)
        self.assertEqual([repo['name'] for repo in repos if repo['kind'] == 'web'], ['admin-web'])
        self.assertNotIn(MOBILE, [repo['name'] for repo in repos])

    def test_legacy_mobile_checkout_stays_ignored(self):
        result = subprocess.run(['git', 'check-ignore', '--no-index', MOBILE],
                                cwd=PROJECT_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / '工作 区'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, self.root / 'scripts/workspace.py')
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(self.base / 'gitconfig'),
                        GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        for key in list(self.env):
            if key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')) or key in ('GIT_CONFIG_COUNT', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
                self.env.pop(key)
        self.git('config', '--global', 'protocol.file.allow', 'always')
        self.repos = []
        self.seeds = {}
        for name in ('alpha', 'beta'):
            remote = self.base / f'{name}.git'
            seed = self.base / f'seed-{name}'
            self.git('init', '--bare', '--initial-branch=main', str(remote))
            self.git('clone', str(remote), str(seed))
            (seed / 'file.txt').write_text('initial\n')
            self.git('add', '.', cwd=seed)
            self.git('commit', '-m', 'initial', cwd=seed)
            self.git('push', 'origin', 'main', cwd=seed)
            url = f'https://github.com/example/{name}.git'
            self.git('config', '--global', f'url.{remote.as_uri()}.insteadOf', url)
            self.repos.append(dict(name=name, url=url, branch='main', kind='java-library'))
            self.seeds[name] = seed
        self.manifest()

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

    def manifest(self):
        (self.root / 'repos.json').write_text(json.dumps(dict(schemaVersion=1, repositories=self.repos)))

    def cli(self, command, *args, code=0):
        run = subprocess.run([sys.executable, str(self.root / 'scripts/workspace.py'), command, '--json', *args],
                             cwd=self.base, env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, code, (run.stdout, run.stderr))
        result = json.loads(run.stdout)
        self.assertIsInstance(result, dict)
        return result

    def clone(self):
        self.cli('bootstrap')

    def advance(self, name='alpha'):
        seed = self.seeds[name]
        with (seed / 'file.txt').open('a') as f:
            f.write('remote update\n')
        self.git('commit', '-am', 'remote update', cwd=seed)
        self.git('push', 'origin', 'main', cwd=seed)
        return self.git('rev-parse', 'HEAD', cwd=seed)

    def test_bootstrap_clone_idempotent_and_status(self):
        self.clone()
        before = self.git('rev-parse', 'HEAD', cwd=self.root / 'alpha')
        self.cli('bootstrap')
        result = self.cli('status')
        self.assertEqual(len(result['repositories']), 2)
        alpha = result['repositories'][0]
        self.assertEqual(alpha['branch'], 'main')
        self.assertFalse(alpha['dirty'])
        self.assertEqual(alpha['ahead'], 0)
        self.assertEqual(alpha['behind'], 0)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.root / 'alpha'), before)

    def test_bootstrap_tracks_other_remote_branches_but_checks_out_main(self):
        seed = self.seeds['alpha']
        for branch in ('test', 'preprod'):
            self.git('branch', branch, cwd=seed)
            self.git('push', 'origin', branch, cwd=seed)
        self.cli('bootstrap', '--only', 'alpha')
        child = self.root / 'alpha'
        self.assertEqual(self.git('branch', '--show-current', cwd=child), 'main')
        for branch in ('test', 'preprod'):
            self.git('show-ref', '--verify', 'refs/remotes/origin/' + branch, cwd=child)

    def test_only_and_dry_run_are_nonmutating(self):
        self.cli('bootstrap', '--only', 'alpha', '--dry-run')
        self.assertFalse((self.root / 'alpha').exists())
        self.cli('bootstrap', '--only', 'alpha')
        self.assertFalse((self.root / 'beta').exists())
        self.cli('bootstrap', '--only', 'unknown', code=1)
        self.cli('idea', '--dry-run')
        self.assertFalse((self.root / '.idea').exists())

    def test_dirty_reuse_symlink_spaces_chinese_and_existing_untouched(self):
        source = self.base / '本機 projects'
        source.mkdir()
        for repo in self.repos:
            name = repo['name']
            self.git('clone', repo['url'], str(source / name))
        (source / 'alpha' / 'dirty.txt').write_text('keep')
        before = (source / 'alpha' / '.git/config').read_bytes()
        self.cli('bootstrap', '--reuse-existing', str(source))
        self.assertTrue((self.root / 'alpha').is_symlink())
        self.assertEqual((self.root / 'alpha').resolve(), (source / 'alpha').resolve())
        self.cli('bootstrap', '--reuse-existing', str(source))
        self.assertEqual((source / 'alpha' / '.git/config').read_bytes(), before)
        self.assertEqual((source / 'alpha/dirty.txt').read_text(), 'keep')
        # A same-name destination and source are already valid; never self-link.
        self.cli('bootstrap', '--reuse-existing', str(self.root))

    def test_bootstrap_existing_dirty_nonmain_does_not_checkout(self):
        self.clone()
        child = self.root / 'alpha'
        self.git('switch', '-c', 'feature', cwd=child)
        (child / 'dirty').write_text('keep')
        self.cli('bootstrap')
        self.assertEqual(self.git('branch', '--show-current', cwd=child), 'feature')

    def test_ff_update_and_idempotent(self):
        self.clone()
        target = self.advance()
        self.cli('update')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.root / 'alpha'), target)
        self.cli('update')

    def test_update_preserves_ignored_file_when_remote_starts_tracking_it(self):
        seed = self.seeds['alpha']
        filename = 'local-settings.txt'
        (seed / '.gitignore').write_text(filename + '\n')
        self.git('add', '.gitignore', cwd=seed)
        self.git('commit', '-m', 'ignore local settings', cwd=seed)
        self.git('push', 'origin', 'main', cwd=seed)
        self.cli('bootstrap', '--only', 'alpha')
        child = self.root / 'alpha'
        original = b'private local value\n'
        (child / filename).write_bytes(original)
        head = self.git('rev-parse', 'HEAD', cwd=child)
        self.assertFalse(self.cli('status', '--only', 'alpha')['repositories'][0]['dirty'])
        (seed / filename).write_text('remote default\n')
        self.git('add', '-f', filename, cwd=seed)
        self.git('commit', '-m', 'track default settings', cwd=seed)
        self.git('push', 'origin', 'main', cwd=seed)
        result = self.cli('update', '--only', 'alpha', code=1)
        self.assertEqual(result['repositories'][0]['state'], 'error')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=child), head)
        self.assertEqual((child / filename).read_bytes(), original)
        self.assertNotIn('private local value', json.dumps(result))

    def test_status_does_not_fetch(self):
        self.clone()
        child = self.root / 'alpha'
        old = self.git('rev-parse', 'origin/main', cwd=child)
        self.advance()
        self.cli('status')
        self.assertEqual(self.git('rev-parse', 'origin/main', cwd=child), old)
        self.assertFalse((child / '.git/FETCH_HEAD').exists())

    def test_update_dry_run_does_not_fetch(self):
        self.clone()
        child = self.root / 'alpha'
        old = self.git('rev-parse', 'HEAD', cwd=child)
        self.advance()
        self.cli('update', '--dry-run')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=child), old)
        self.assertFalse((child / '.git/FETCH_HEAD').exists())

    def test_all_dirty_states_protected(self):
        self.clone()
        child = self.root / 'alpha'
        head = self.git('rev-parse', 'HEAD', cwd=child)
        for mode in ('untracked', 'unstaged', 'staged'):
            with self.subTest(mode=mode):
                path = child / ('new.txt' if mode == 'untracked' else 'file.txt')
                path.write_text(mode)
                if mode == 'staged':
                    self.git('add', '.', cwd=child)
                self.cli('update', '--only', 'alpha', code=2)
                self.assertEqual(self.git('rev-parse', 'HEAD', cwd=child), head)
                self.assertEqual(path.read_text(), mode)
                # Test fixture cleanup only.
                self.git('reset', '--hard', 'HEAD', cwd=child)
                if mode == 'untracked':
                    path.unlink()

    def test_nonmain_detached_ahead_diverged_protected(self):
        self.clone()
        child = self.root / 'alpha'
        self.git('switch', '-c', 'feature', cwd=child)
        self.cli('update', '--only', 'alpha', code=2)
        self.git('checkout', '--detach', cwd=child)
        self.cli('update', '--only', 'alpha', code=2)
        self.git('switch', 'main', cwd=child)
        (child / 'local.txt').write_text('local')
        self.git('add', '.', cwd=child)
        self.git('commit', '-m', 'local', cwd=child)
        local = self.git('rev-parse', 'HEAD', cwd=child)
        self.cli('update', '--only', 'alpha', code=2)
        self.advance()
        self.cli('update', '--only', 'alpha', code=2)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=child), local)

    def test_partial_failure_retry_and_error_precedence(self):
        (self.root / 'alpha').mkdir()
        self.cli('bootstrap', code=1)
        self.assertTrue((self.root / 'beta/.git').is_dir())
        (self.root / 'alpha').rmdir()
        self.clone()
        (self.root / 'beta/dirty').write_text('preserve')
        self.git('remote', 'set-url', 'origin', 'https://github.com/wrong/repo.git', cwd=self.root / 'alpha')
        self.cli('update', code=1)

    def test_identity_wrong_nested_non_git_broken_symlink(self):
        self.clone()
        child = self.root / 'alpha'
        self.git('remote', 'set-url', 'origin', 'https://github.com/wrong/repo.git', cwd=child)
        self.cli('status', code=1)
        self.cli('bootstrap', code=1)
        self.cli('update', code=1)
        shutil.rmtree(child)
        child.symlink_to(self.base / 'absent')
        self.cli('bootstrap', code=1)
        child.unlink()
        child.mkdir()
        self.git('init', '--initial-branch=main', str(self.root))
        self.cli('bootstrap', '--only', 'alpha', code=1)

    def test_common_origin_spellings_and_credentials_rejected(self):
        self.clone()
        child = self.root / 'alpha'
        for url in ('git@github.com:example/alpha.git', 'ssh://git@github.com/example/alpha',
                    'https://github.com/example/alpha', 'https://github.com/EXAMPLE/alpha.git'):
            self.git('remote', 'set-url', 'origin', url, cwd=child)
            self.cli('status', '--only', 'alpha')
        for url in ('https://secret:password@github.com/example/alpha.git',
                    'https://github.com.evil/example/alpha.git', 'file:///tmp/alpha',
                    'ssh://secret@github.com/example/alpha.git'):
            self.git('remote', 'set-url', 'origin', url, cwd=child)
            result = self.cli('status', '--only', 'alpha', code=1)
            self.assertNotIn('password', json.dumps(result))

    def test_bad_manifest_and_path_traversal(self):
        for name in ('../escape', '/absolute', '.', 'alpha/beta', 'alpha\\beta'):
            self.repos[0]['name'] = name
            self.manifest()
            self.cli('bootstrap', code=1)
        self.repos[0]['name'] = 'alpha'
        self.repos[0]['url'] = 'https://token:secret@github.com/example/alpha.git'
        self.manifest()
        result = self.cli('bootstrap', code=1)
        self.assertNotIn('secret', json.dumps(result))

    def test_missing_status_and_update_error(self):
        self.cli('status', code=1)
        self.cli('update', code=1)
        self.cli('bootstrap', '--reuse-existing', str(self.base / 'missing'), code=1)

    def test_invalid_arguments_have_pure_json(self):
        self.cli('update', '--reuse-existing', str(self.base), code=1)
        self.cli('status', '--unknown', 'secret-value', code=1)

    def test_invalid_manifest_shapes_and_duplicate_names(self):
        original = [dict(repo) for repo in self.repos]
        for field, value in (('branch', 'test'), ('kind', 'unknown')):
            self.repos[0][field] = value
            self.manifest()
            self.cli('bootstrap', code=1)
            self.repos = [dict(repo) for repo in original]
        self.repos[1]['name'] = 'alpha'
        self.manifest()
        self.cli('bootstrap', code=1)
        (self.root / 'repos.json').write_text('[]')
        self.cli('bootstrap', code=1)
        (self.root / 'repos.json').write_text('{invalid')
        self.cli('bootstrap', code=1)

    def test_reuse_nested_root_rejected_without_link(self):
        source = self.base / 'source'
        source.mkdir()
        self.git('init', '--initial-branch=main', str(source))
        self.git('remote', 'add', 'origin', self.repos[0]['url'], cwd=source)
        (source / 'alpha').mkdir()
        self.cli('bootstrap', '--only', 'alpha', '--reuse-existing', str(source), code=1)
        self.assertFalse(os.path.lexists(self.root / 'alpha'))

    def test_idea_symlink_output_rejected(self):
        outside = self.base / 'private-idea'
        outside.mkdir()
        (self.root / '.idea').symlink_to(outside, target_is_directory=True)
        self.cli('idea', code=1)
        self.assertFalse((outside / 'vcs.xml').exists())

    def test_git_failure_sanitizes_stderr(self):
        self.clone()
        remote = self.base / 'alpha.git'
        shutil.rmtree(remote)
        result = self.cli('update', '--only', 'alpha', code=1)
        self.assertNotIn(str(self.base), json.dumps(result))

    def test_idea_merges_mappings_and_is_idempotent(self):
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        path.write_text('<project version="4"><component name="VcsDirectoryMappings"><mapping directory="$PROJECT_DIR$/custom" vcs="Git" /></component><component name="Other" /></project>')
        self.cli('idea')
        before = path.read_bytes()
        self.cli('idea')
        self.assertEqual(path.read_bytes(), before)
        doc = ET.parse(path)
        values = [x.attrib['directory'] for x in doc.findall('.//mapping')]
        self.assertEqual(set(values), {'$PROJECT_DIR$', '$PROJECT_DIR$/alpha', '$PROJECT_DIR$/beta', '$PROJECT_DIR$/custom'})
        self.assertIsNotNone(doc.find('.//component[@name="Other"]'))

    def test_idea_invalid_xml_is_preserved(self):
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        path.write_text('not xml')
        self.cli('idea', code=1)
        self.assertEqual(path.read_text(), 'not xml')

    def test_mobile_requests_are_rejected_without_touching_legacy_checkout(self):
        mobile = self.root / MOBILE
        mobile.mkdir()
        (mobile / 'keep.txt').write_bytes(b'keep independent mobile checkout\n')
        for command in ('bootstrap', 'update', 'status', 'idea'):
            self.cli(command, '--only', MOBILE, code=1)
        self.cli('bootstrap')
        self.cli('status')
        self.cli('update', '--dry-run')
        self.assertEqual((mobile / 'keep.txt').read_bytes(), b'keep independent mobile checkout\n')
        self.assertFalse((mobile / '.git').exists())

    def test_manifest_rejects_mobile_kind_and_retired_name(self):
        for name, kind in (('old-client', 'wechat'), (MOBILE, 'web')):
            self.repos.append(dict(name=name, kind=kind, branch='main',
                                   url=self.repos[0]['url']))
            self.manifest()
            self.cli('bootstrap', code=1)
            self.repos.pop()

    def test_idea_removes_exact_legacy_mobile_mappings_and_preserves_symlink(self):
        source = self.base / '独立 mobile checkout'
        source.mkdir()
        (source / 'keep.txt').write_bytes(b'mobile contents preserved\n')
        link = self.root / MOBILE
        link.symlink_to(source, target_is_directory=True)
        directory = self.root / '.idea'
        directory.mkdir()
        path = directory / 'vcs.xml'
        project = ET.Element('project', version='4')
        component = ET.SubElement(project, 'component', name='VcsDirectoryMappings')
        removed = {f'$PROJECT_DIR$/{MOBILE}', str(self.root.resolve() / MOBILE), str(source.resolve())}
        kept = {'$PROJECT_DIR$/custom', str(source / 'custom-child'),
                f'$PROJECT_DIR$/{MOBILE}-archive'}
        for value in removed | kept:
            ET.SubElement(component, 'mapping', directory=value, vcs='Git')
        ET.SubElement(component, 'mapping', directory=f'$PROJECT_DIR$/{MOBILE}', vcs='Mercurial')
        ET.ElementTree(project).write(path, encoding='utf-8')
        before = path.read_bytes()
        planned = self.cli('idea', '--dry-run')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(planned['idea']['removed']), removed)
        self.cli('idea')
        mappings = ET.parse(path).findall('.//mapping')
        git_dirs = {item.get('directory') for item in mappings if item.get('vcs') == 'Git'}
        self.assertTrue(removed.isdisjoint(git_dirs))
        self.assertTrue(kept.issubset(git_dirs))
        self.assertTrue(any(item.get('vcs') == 'Mercurial' for item in mappings))
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), source.resolve())
        self.assertEqual((source / 'keep.txt').read_bytes(), b'mobile contents preserved\n')
        after = path.read_bytes()
        self.cli('idea')
        self.assertEqual(path.read_bytes(), after)

    def test_idea_cleanup_preserves_active_custom_mapping_with_only_selection(self):
        active = self.root / 'alpha'
        active.mkdir()
        (self.root / MOBILE).symlink_to(active, target_is_directory=True)
        directory = self.root / '.idea'
        directory.mkdir()
        path = directory / 'vcs.xml'
        project = ET.Element('project', version='4')
        component = ET.SubElement(project, 'component', name='VcsDirectoryMappings')
        ET.SubElement(component, 'mapping', directory=str(active.resolve()), vcs='Git')
        ET.SubElement(component, 'mapping', directory=f'$PROJECT_DIR$/{MOBILE}', vcs='Git')
        ET.ElementTree(project).write(path, encoding='utf-8')
        self.cli('idea', '--only', 'beta')
        values = {item.get('directory') for item in ET.parse(path).findall('.//mapping')}
        self.assertIn(str(active.resolve()), values)
        self.assertNotIn(f'$PROJECT_DIR$/{MOBILE}', values)

    def test_idea_cleanup_preserves_independent_custom_alias(self):
        source = self.base / 'independent mobile'
        source.mkdir()
        (self.root / MOBILE).symlink_to(source, target_is_directory=True)
        alias = self.root / 'custom-alias'
        alias.symlink_to(source, target_is_directory=True)
        directory = self.root / '.idea'
        directory.mkdir()
        path = directory / 'vcs.xml'
        project = ET.Element('project', version='4')
        component = ET.SubElement(project, 'component', name='VcsDirectoryMappings')
        kept = {str(alias), '$PROJECT_DIR$/custom-alias'}
        for value in kept | {str(source.resolve())}:
            ET.SubElement(component, 'mapping', directory=value, vcs='Git')
        ET.ElementTree(project).write(path, encoding='utf-8')
        self.cli('idea')
        values = {item.get('directory') for item in ET.parse(path).findall('.//mapping')}
        self.assertTrue(kept.issubset(values))
        self.assertNotIn(str(source.resolve()), values)
        self.assertTrue(alias.is_symlink())


if __name__ == '__main__':
    unittest.main()
