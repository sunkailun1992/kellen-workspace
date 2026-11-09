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

    def test_idea_registers_web_content_root_without_replacing_gradle_modules(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        physical = self.base / '前端 源码' / 'beta'
        physical.parent.mkdir()
        (self.root / 'beta').rename(physical)
        (self.root / 'beta').symlink_to(physical, target_is_directory=True)
        idea = self.root / '.idea'
        idea.mkdir()
        modules = idea / 'modules.xml'
        modules.write_text('<project version="4"><component name="ProjectModuleManager"><modules>'
                           '<module filepath="$PROJECT_DIR$/java.iml" group="Java" />'
                           '</modules></component><component name="UserSetting" keep="yes" /></project>')
        before = modules.read_bytes()
        self.cli('idea', '--dry-run')
        self.assertEqual(modules.read_bytes(), before)
        output = idea / 'workspace-web/beta.iml'
        self.assertFalse(output.exists())
        self.cli('idea')
        tree = ET.parse(output)
        self.assertEqual(tree.getroot().get('type'), 'WEB_MODULE')
        content = tree.find('.//content')
        expected = 'file://' + str(physical.resolve())
        self.assertEqual(content.get('url'), expected)
        self.assertEqual({x.get('url') for x in content.findall('excludeFolder')},
                         {expected + '/' + name for name in ('node_modules', 'dist', 'coverage')})
        doc = ET.parse(modules)
        self.assertIsNotNone(doc.find('.//module[@group="Java"]'))
        self.assertIsNotNone(doc.find('.//component[@name="UserSetting"][@keep="yes"]'))
        references = doc.findall('.//module')
        self.assertEqual(len(references), 2)
        snapshot = (modules.read_bytes(), output.read_bytes())
        self.cli('idea')
        self.assertEqual((modules.read_bytes(), output.read_bytes()), snapshot)
        self.assertTrue((self.root / 'beta').is_symlink())

    def test_idea_migrates_owned_project_macro_to_absolute_content_urls(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        self.cli('idea')
        path = self.root / '.idea/workspace-web/beta.iml'
        tree = ET.parse(path)
        content = tree.find('.//content')
        legacy = 'file://$PROJECT_DIR$/beta'
        content.set('url', legacy)
        content.set('custom', 'preserved')
        for item in content.findall('excludeFolder'):
            item.set('url', legacy + '/' + item.get('url').rsplit('/', 1)[1])
        ET.SubElement(content, 'excludeFolder', url=legacy + '/.cache', custom='preserved')
        ET.SubElement(content, 'sourceFolder', url=legacy + '/src', isTestSource='false')
        unrelated = 'file://' + str(self.base / 'unrelated')
        ET.SubElement(content, 'excludeFolder', url=unrelated)
        ET.SubElement(tree.getroot(), 'component', name='UserSetting', keep='yes')
        tree.write(path)
        registry = self.root / '.idea/modules.xml'
        before_registry = registry.read_bytes()
        self.cli('idea')
        doc = ET.parse(path)
        content = doc.find('.//content')
        expected = 'file://' + str((self.root / 'beta').resolve())
        self.assertEqual(content.get('url'), expected)
        self.assertTrue(Path(content.get('url').removeprefix('file://')).is_absolute())
        self.assertEqual(content.get('custom'), 'preserved')
        self.assertEqual({item.get('url') for item in content.findall('excludeFolder')},
                         {expected + '/' + name for name in ('node_modules', 'dist', 'coverage', '.cache')} | {unrelated})
        self.assertEqual(content.find('sourceFolder').get('url'), expected + '/src')
        self.assertIsNotNone(doc.find('.//component[@name="UserSetting"][@keep="yes"]'))
        self.assertEqual(registry.read_bytes(), before_registry)
        snapshot = path.read_bytes()
        self.cli('idea')
        self.assertEqual(path.read_bytes(), snapshot)

    def test_idea_invalid_module_registry_is_preserved_before_any_write(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        (idea / 'modules.xml').write_text('broken modules')
        self.cli('idea', code=1)
        self.assertEqual((idea / 'modules.xml').read_text(), 'broken modules')
        self.assertFalse((idea / 'vcs.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_rejects_dtd_in_all_metadata_without_writing(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        self.cli('idea')
        idea = self.root / '.idea'
        registry = idea / 'modules.xml'
        custom = self.root / 'beta/custom.iml'
        custom.write_text('<module type="WEB_MODULE"><component name="Custom" /></module>')
        tree = ET.parse(registry)
        ET.SubElement(tree.find('.//modules'), 'module', filepath='$PROJECT_DIR$/beta/custom.iml')
        tree.write(registry)
        files = [idea / 'vcs.xml', registry, idea / 'workspace-web/beta.iml', custom, idea / 'gradle.xml']
        original = {path: path.read_bytes() for path in files}
        for target in files:
            with self.subTest(target=target.name):
                root = 'module' if target.suffix == '.iml' else 'project'
                target.write_text('<!DOCTYPE ' + root + ' [<!ENTITY private "SYNTHETIC_XML_CANARY">]>'
                                  '<' + root + ' value="&private;" />')
                before = {path: path.read_bytes() for path in files}
                result = self.cli('idea', code=1)
                self.assertNotIn('SYNTHETIC_XML_CANARY', json.dumps(result))
                self.assertEqual({path: path.read_bytes() for path in files}, before)
                target.write_bytes(original[target])

    def test_idea_rejects_external_dtd_and_utf16_entity_declarations(self):
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        for payload in [b'<!DOCTYPE project SYSTEM "http://127.0.0.1:9/forbidden"><project />',
                        '<!DOCTYPE project [<!ENTITY x "SYNTHETIC_XML_CANARY">]><project>&x;</project>'.encode('utf-16')]:
            with self.subTest(payload=payload[:16]):
                path.write_bytes(payload)
                result = self.cli('idea', code=1)
                self.assertNotIn('SYNTHETIC_XML_CANARY', json.dumps(result))
                self.assertEqual(path.read_bytes(), payload)

    def test_idea_rejects_xml_resource_exhaustion_before_writing(self):
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        cases = {
            'bytes': '<project><!--' + 'x' * (2 * 1024 * 1024) + '--></project>',
            'depth': '<project>' + '<x>' * 128 + '</x>' * 128 + '</project>',
            'nodes': '<project>' + '<x/>' * 50000 + '</project>',
            'attributes': '<project ' + ' '.join('x%d="a"' % i for i in range(257)) + '/>',
            'namespace-uri': '<project xmlns:p="urn:' + 'x' * 512 + '"/>',
            'namespace-count': '<project ' + ' '.join('xmlns:p%d="urn:%d"' % (i, i) for i in range(129)) + '/>',
            'name': '<project><' + 'x' * 1025 + '/></project>',
            'expanded-names': '<project xmlns:p="urn:' + 'x' * 508 + '">' +
                              ''.join('<p:e%d/>' % i for i in range(17000)) + '</project>',
        }
        for name, payload in cases.items():
            with self.subTest(limit=name):
                path.write_text(payload)
                before = path.read_bytes()
                self.cli('idea', code=1)
                self.assertEqual(path.read_bytes(), before)

    def test_idea_safe_xml_preserves_comments_namespaces_and_builtin_entities(self):
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        path.write_text('<project version="4"><!--literal <!DOCTYPE is harmless here-->'
                        '<component name="User" xmlns:p="urn:fixture" p:flag="a&amp;b">'
                        '<p:value>&lt;data&gt;&#65;</p:value></component></project>')
        self.cli('idea')
        doc = ET.parse(path)
        self.assertEqual(doc.find('.//component[@name="User"]').get('{urn:fixture}flag'), 'a&b')
        self.assertEqual(doc.find('.//{urn:fixture}value').text, '<data>A')
        self.assertIn('literal <!DOCTYPE', path.read_text())

    def test_idea_unknown_xml_encoding_returns_sanitized_error_without_writing(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        path = idea / 'vcs.xml'
        payload = b'<?xml version="1.0" encoding="SYNTHETIC_UNKNOWN_ENCODING"?><project />'
        path.write_bytes(payload)
        run = subprocess.run([sys.executable, str(self.root / 'scripts/workspace.py'), 'idea', '--json'],
                             cwd=self.base, env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['exit_code'], 1)
        self.assertNotIn('SYNTHETIC_UNKNOWN_ENCODING', run.stdout + run.stderr)
        self.assertEqual(run.stderr, '')
        self.assertEqual(path.read_bytes(), payload)
        self.assertFalse((idea / 'modules.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_rejects_oversized_formatted_output_before_any_file_write(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        vcs = idea / 'vcs.xml'
        vcs.write_text('<project>' + '<x>' * 120 + '<z/>' * 10000 + '</x>' * 120 + '</project>')
        before = vcs.read_bytes()
        self.cli('idea', code=1)
        self.assertEqual(vcs.read_bytes(), before)
        self.assertFalse((idea / 'modules.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_rejects_generated_node_overflow_before_any_file_write(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        vcs = idea / 'vcs.xml'
        vcs.write_text('<project>' + '<x/>' * 49999 + '</project>')
        before = vcs.read_bytes()
        self.cli('idea', code=1)
        self.assertEqual(vcs.read_bytes(), before)
        self.assertFalse((idea / 'modules.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_rejects_cumulative_module_input_bytes_before_writing(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        registry = idea / 'modules.xml'
        entries = []
        payload = '<module><!--' + 'x' * (2 * 1024 * 1024 - 128) + '--></module>'
        for index in range(5):
            name = 'custom%d.iml' % index
            (idea / name).write_text(payload)
            entries.append('<module filepath="$PROJECT_DIR$/.idea/' + name + '"/>')
        registry.write_text('<project><component name="ProjectModuleManager"><modules>' +
                            ''.join(entries) + '</modules></component></project>')
        before = {path: path.read_bytes() for path in idea.iterdir()}
        self.cli('idea', code=1)
        self.assertEqual({path: path.read_bytes() for path in idea.iterdir()}, before)
        self.assertFalse((idea / 'vcs.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_rejects_too_many_module_input_files_before_writing(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        registry = idea / 'modules.xml'
        entries = []
        for index in range(513):
            name = 'custom%d.iml' % index
            (idea / name).write_text('<module />')
            entries.append('<module filepath="$PROJECT_DIR$/.idea/' + name + '"/>')
        registry.write_text('<project><component name="ProjectModuleManager"><modules>' +
                            ''.join(entries) + '</modules></component></project>')
        before = {path: path.read_bytes() for path in idea.iterdir()}
        self.cli('idea', code=1)
        self.assertEqual({path: path.read_bytes() for path in idea.iterdir()}, before)
        self.assertFalse((idea / 'vcs.xml').exists())
        self.assertFalse((idea / 'workspace-web').exists())

    def test_idea_counts_repeated_canonical_module_input_only_once(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        custom = idea / 'custom.iml'
        custom.write_text('<module><!--' + 'x' * (512 * 1024) + '--></module>')
        before = custom.read_bytes()
        registry = idea / 'modules.xml'
        entries = ['<module filepath="$PROJECT_DIR$/.idea/' + prefix + 'custom.iml"/>'
                   for prefix in ('', './') for unused in range(260)]
        registry.write_text('<project><component name="ProjectModuleManager"><modules>' +
                            ''.join(entries) + '</modules></component></project>')
        self.cli('idea')
        self.assertEqual(custom.read_bytes(), before)
        self.assertTrue((idea / 'workspace-web/beta.iml').is_file())
        snapshot = registry.read_bytes()
        self.cli('idea')
        self.assertEqual(registry.read_bytes(), snapshot)

    def test_idea_web_output_directory_symlink_is_rejected(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        outside = self.base / 'preserved private files'
        outside.mkdir()
        (idea / 'workspace-web').symlink_to(outside, target_is_directory=True)
        self.cli('idea', code=1)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((idea / 'vcs.xml').exists())

    def test_idea_reuses_registered_custom_frontend_module(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        custom = idea / 'my-frontend.iml'
        custom.write_text('<module type="WEB_MODULE" version="4"><component name="NewModuleRootManager">'
                          '<content url="file://$PROJECT_DIR$/beta" /></component>'
                          '<component name="UserSetting" keep="yes" /></module>')
        (idea / 'modules.xml').write_text('<project version="4"><component name="ProjectModuleManager">'
                                          '<modules><module filepath="$PROJECT_DIR$/.idea/my-frontend.iml" />'
                                          '</modules></component></project>')
        before = custom.read_bytes()
        self.cli('idea')
        self.assertEqual(custom.read_bytes(), before)
        self.assertFalse((idea / 'workspace-web/beta.iml').exists())
        self.assertEqual(len(ET.parse(idea / 'modules.xml').findall('.//module')), 1)

    def test_idea_reuses_module_dir_content_root_without_modifying_custom_module(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        idea = self.root / '.idea'
        idea.mkdir()
        custom = self.root / 'beta/custom.iml'
        custom.write_text('<module type="WEB_MODULE" version="4"><component name="NewModuleRootManager">'
                          '<content url="file://$MODULE_DIR$" /></component>'
                          '<component name="UserSetting" keep="yes" /></module>')
        (idea / 'modules.xml').write_text('<project version="4"><component name="ProjectModuleManager">'
                                          '<modules><module filepath="$PROJECT_DIR$/beta/custom.iml" />'
                                          '</modules></component></project>')
        before = custom.read_bytes()
        self.cli('idea')
        self.assertEqual(custom.read_bytes(), before)
        self.assertFalse((idea / 'workspace-web/beta.iml').exists())
        self.assertEqual(len(ET.parse(idea / 'modules.xml').findall('.//module')), 1)

    def test_idea_custom_frontend_replaces_only_old_generated_registration(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        self.cli('idea')
        idea = self.root / '.idea'
        custom = self.root / 'beta/custom.iml'
        custom.write_text('<module type="WEB_MODULE" version="4"><component name="NewModuleRootManager">'
                          '<content url="file://$MODULE_DIR$" /></component></module>')
        before = custom.read_bytes()
        registry = ET.parse(idea / 'modules.xml')
        ET.SubElement(registry.find('.//modules'), 'module', filepath='$PROJECT_DIR$/beta/custom.iml')
        registry.write(idea / 'modules.xml')
        self.cli('idea')
        self.assertEqual(custom.read_bytes(), before)
        entries = ET.parse(idea / 'modules.xml').findall('.//module')
        self.assertEqual([x.get('filepath') for x in entries], ['$PROJECT_DIR$/beta/custom.iml'])

    def test_idea_preserves_equivalent_content_macro_rewritten_by_ide(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        self.cli('idea')
        path = self.root / '.idea/workspace-web/beta.iml'
        tree = ET.parse(path)
        base = 'file://$MODULE_DIR$/../../beta'
        content = tree.find('.//content')
        content.set('url', base)
        for item in content.findall('excludeFolder'):
            item.set('url', base + '/' + item.get('url').rsplit('/', 1)[1])
        tree.write(path)
        self.cli('idea')
        content = ET.parse(path).find('.//content')
        self.assertEqual(content.get('url'), base)
        self.assertEqual(len(content.findall('excludeFolder')), 3)

    def test_idea_web_module_custom_settings_and_duplicate_references_are_preserved(self):
        self.repos[1]['kind'] = 'web'
        self.manifest()
        self.clone()
        self.cli('idea')
        idea = self.root / '.idea'
        path = idea / 'workspace-web/beta.iml'
        module = ET.parse(path)
        ET.SubElement(module.getroot(), 'component', name='UserSetting', keep='yes')
        module.write(path)
        registry = ET.parse(idea / 'modules.xml')
        ET.SubElement(registry.find('.//modules'), 'module', filepath=str(path))
        registry.write(idea / 'modules.xml')
        self.cli('idea')
        self.assertIsNotNone(ET.parse(path).find('.//component[@name="UserSetting"][@keep="yes"]'))
        self.assertEqual(len(ET.parse(idea / 'modules.xml').findall('.//module')), 1)

    def test_idea_maps_reused_repository_by_real_path(self):
        self.clone()
        target = self.base / '复用 源码' / 'alpha'
        target.parent.mkdir()
        (self.root / 'alpha').rename(target)
        (self.root / 'alpha').symlink_to(target, target_is_directory=True)
        (target / 'local.txt').write_text('preserve local changes')
        before = self.git('status', '--porcelain=v1', cwd=target)
        self.cli('idea')
        values = [m.get('directory') for m in ET.parse(self.root / '.idea/vcs.xml').findall('.//mapping')]
        expected = '$PROJECT_DIR$/' + os.path.relpath(target.resolve(), self.root.resolve())
        self.assertIn(expected, values)
        self.assertNotIn('$PROJECT_DIR$/alpha', values)
        self.assertEqual(self.git('status', '--porcelain=v1', cwd=target), before)

    def test_idea_deduplicates_real_and_symlink_mappings_without_losing_custom_settings(self):
        self.clone()
        target = self.base / '复用 源码' / 'alpha'
        target.parent.mkdir()
        (self.root / 'alpha').rename(target)
        (self.root / 'alpha').symlink_to(target, target_is_directory=True)
        directory = self.root / '.idea'
        directory.mkdir()
        path = directory / 'vcs.xml'
        canonical = '$PROJECT_DIR$/' + os.path.relpath(target.resolve(), self.root.resolve())
        project = ET.Element('project', version='4')
        component = ET.SubElement(project, 'component', name='VcsDirectoryMappings')
        for value in ['$PROJECT_DIR$/alpha', canonical, str(target.resolve()), canonical,
                      '$PROJECT_DIR$/custom', '$PROJECT_DIR$', '$PROJECT_DIR$']:
            ET.SubElement(component, 'mapping', directory=value, vcs='Git')
        ET.SubElement(component, 'mapping', directory='$PROJECT_DIR$/alpha', vcs='Subversion')
        ET.SubElement(project, 'component', name='Other')
        ET.ElementTree(project).write(path)
        original = path.read_bytes()
        self.cli('idea', '--dry-run')
        self.assertEqual(path.read_bytes(), original)
        self.cli('idea')
        document = ET.parse(path)
        values = [m.get('directory') for m in document.findall('.//mapping') if m.get('vcs') == 'Git']
        self.assertEqual(values.count(canonical), 1)
        self.assertEqual(values.count('$PROJECT_DIR$'), 1)
        self.assertNotIn('$PROJECT_DIR$/alpha', values)
        self.assertNotIn(str(target.resolve()), values)
        self.assertIn('$PROJECT_DIR$/custom', values)
        self.assertIsNotNone(document.find('.//mapping[@vcs="Subversion"]'))
        self.assertIsNotNone(document.find('.//component[@name="Other"]'))
        after = path.read_bytes()
        self.cli('idea')
        self.assertEqual(path.read_bytes(), after)
        self.assertTrue((self.root / 'alpha').is_symlink())

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
