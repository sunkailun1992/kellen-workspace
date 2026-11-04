#!/usr/bin/env python3
"""Manage the declared independent repositories without changing existing setup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_REPOSITORIES = ('ai-wechat-mini-programs',)


class WorkspaceError(Exception):
    """An error whose message contains no external command output or secrets."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may include arbitrary user input in its standard error text.
        raise WorkspaceError('Invalid command arguments; use --help for usage.')


def github_identity(value):
    """Return a canonical GitHub owner/repository, rejecting credential URLs."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise WorkspaceError('Invalid GitHub origin URL.')
    scp = re.fullmatch(r'git@github\.com:([^?#]+)', value, flags=re.I)
    if scp:
        path = scp.group(1)
    else:
        try:
            url = urlsplit(value)
            if (url.hostname or '').lower() != 'github.com' or url.port is not None:
                raise ValueError()
            if url.query or url.fragment or url.password is not None:
                raise ValueError()
            if not ((url.scheme == 'https' and url.username is None)
                    or (url.scheme == 'ssh' and url.username == 'git')):
                raise ValueError()
            path = url.path.removeprefix('/')
        except ValueError:
            raise WorkspaceError('Invalid GitHub origin URL.') from None
    path = path.removesuffix('.git')
    parts = path.split('/')
    if (len(parts) != 2 or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', p)
                               or p in ('.', '..') for p in parts)):
        raise WorkspaceError('Invalid GitHub origin URL.')
    return path.lower()


def load_manifest(root):
    try:
        manifest = json.loads((root / 'repos.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise WorkspaceError('Cannot read a valid repos.json.') from None
    if (not isinstance(manifest, dict) or type(manifest.get('schemaVersion')) is not int
            or manifest.get('schemaVersion') != 1):
        raise WorkspaceError('Unsupported manifest schemaVersion.')
    repos = manifest.get('repositories')
    if not isinstance(repos, list) or not repos:
        raise WorkspaceError('Manifest repositories must be a nonempty list.')
    seen = set()
    for repo in repos:
        if not isinstance(repo, dict):
            raise WorkspaceError('Invalid repository declaration.')
        name = repo.get('name')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name)
                or name.lower() in seen):
            raise WorkspaceError('Invalid or duplicate repository name.')
        seen.add(name.lower())
        if name.lower() in EXCLUDED_REPOSITORIES:
            raise WorkspaceError('Mobile repositories are outside this workspace scope.')
        github_identity(repo.get('url'))
        if repo.get('branch') != 'main':
            raise WorkspaceError('Repository branch must be main.')
        if repo.get('kind') not in ('java-library', 'java-service', 'web'):
            raise WorkspaceError('Invalid repository kind.')
    return repos


def git(path, *args, optional=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    # A shell's Git context must not redirect a declared child to another repo.
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key, None)
    try:
        result = subprocess.run(['git', '-C', str(path), *args], env=env,
                                capture_output=True, text=True, errors='replace')
    except OSError:
        raise WorkspaceError('Git could not be started.') from None
    if result.returncode and not optional:
        # Never expose stderr: Git may print credential-bearing URLs or local paths.
        raise WorkspaceError('Git operation failed (' + args[0] + ').')
    return result


def validate_repository(path, repo):
    if not path.is_dir():
        raise WorkspaceError('Repository directory is missing or invalid.')
    top = git(path, 'rev-parse', '--show-toplevel').stdout.strip()
    if Path(top).resolve() != path.resolve():
        raise WorkspaceError('Directory is not an independent Git root.')
    urls = git(path, 'config', '--get-all', 'remote.origin.url', optional=True).stdout.splitlines()
    if len(urls) != 1 or github_identity(urls[0]) != github_identity(repo['url']):
        raise WorkspaceError('Repository origin does not match the manifest.')


def local_state(path, repo):
    branch = git(path, 'symbolic-ref', '--quiet', '--short', 'HEAD', optional=True).stdout.strip() or None
    sha = git(path, 'rev-parse', '--short', 'HEAD').stdout.strip()
    dirty = bool(git(path, 'status', '--porcelain=v1', '--untracked-files=all').stdout)
    remote_ref = 'refs/remotes/origin/' + repo['branch']
    exists = git(path, 'show-ref', '--verify', '--quiet', remote_ref, optional=True).returncode == 0
    ahead = behind = None
    if exists:
        counts = git(path, 'rev-list', '--left-right', '--count', 'HEAD...' + remote_ref).stdout.split()
        ahead, behind = map(int, counts)
    return dict(branch=branch, sha=sha, dirty=dirty, ahead=ahead, behind=behind,
                tracking=remote_ref if exists else None)


def bootstrap(root, repo, args):
    path = root / repo['name']
    if os.path.lexists(path):
        validate_repository(path, repo)
        return dict(state='existing', **local_state(path, repo))
    if args.reuse_existing is not None:
        source = Path(args.reuse_existing).expanduser().absolute() / repo['name']
        validate_repository(source, repo)
        if source.resolve() == path.resolve():
            raise WorkspaceError('Reuse source equals missing destination.')
        if not args.dry_run:
            path.symlink_to(source.resolve(), target_is_directory=True)
        return dict(state='planned' if args.dry_run else 'linked', action='symlink')
    if args.dry_run:
        return dict(state='planned', action='clone')
    # Clone into an owned temporary location; failure never leaves a partial child.
    temporary = Path(tempfile.mkdtemp(prefix='.workspace-clone-', dir=root))
    try:
        clone = temporary / 'repository'
        git(root, 'clone', '--origin', 'origin', '--branch', repo['branch'],
            '--', repo['url'], str(clone))
        validate_repository(clone, repo)
        if os.path.lexists(path):
            raise WorkspaceError('Destination appeared during clone; preserved it.')
        clone.rename(path)
    finally:
        shutil.rmtree(temporary)
    return dict(state='cloned', **local_state(path, repo))


def update(root, repo, args):
    path = root / repo['name']
    validate_repository(path, repo)
    state = local_state(path, repo)
    if state['dirty']:
        return dict(state='skipped', reason='dirty', **state)
    if state['branch'] != repo['branch']:
        return dict(state='skipped', reason='detached' if state['branch'] is None else 'non-main', **state)
    if args.dry_run:
        if state['ahead']:
            return dict(state='skipped', reason='diverged' if state['behind'] else 'ahead', **state)
        return dict(state='planned', action='fetch-and-fast-forward', **state)
    branch = repo['branch']
    git(path, 'fetch', '--no-tags', 'origin', '+refs/heads/' + branch + ':refs/remotes/origin/' + branch)
    # Recheck after fetch in case another process or a configured fetch hook changed state.
    state = local_state(path, repo)
    if state['dirty'] or state['branch'] != branch:
        return dict(state='skipped', reason='changed-during-fetch', **state)
    if state['ahead']:
        return dict(state='skipped', reason='diverged' if state['behind'] else 'ahead', **state)
    if state['behind']:
        git(path, 'merge', '--ff-only', '--no-overwrite-ignore', '--no-edit',
            'refs/remotes/origin/' + branch)
        return dict(state='updated', **local_state(path, repo))
    return dict(state='up-to-date', **state)


def idea(root, repos, dry_run, active_repos=None):
    directory = root / '.idea'
    target = directory / 'vcs.xml'
    if directory.is_symlink() or target.is_symlink():
        raise WorkspaceError('IDEA output must be a local regular file.')
    if target.exists():
        try:
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            tree = ET.parse(target, parser=parser)
        except (ET.ParseError, OSError):
            raise WorkspaceError('Existing vcs.xml is invalid; preserved it.') from None
        if tree.getroot().tag != 'project':
            raise WorkspaceError('Existing vcs.xml has an invalid root; preserved it.')
    else:
        tree = ET.ElementTree(ET.Element('project', version='4'))
    project = tree.getroot()
    component = project.find("component[@name='VcsDirectoryMappings']")
    if component is None:
        component = ET.SubElement(project, 'component', name='VcsDirectoryMappings')
    # Retire only exact Git mappings for the formerly managed mobile checkout.
    # Keep the checkout/symlink itself and unrelated custom mappings untouched.
    retired = set()
    retired_physical = set()
    active = {root.resolve()} | {(root / repo['name']).resolve() for repo in
                                (repos if active_repos is None else active_repos)}
    for name in EXCLUDED_REPOSITORIES:
        legacy = root / name
        retired.update(('$PROJECT_DIR$/' + name, str(legacy)))
        physical = legacy.resolve()
        if physical not in active:
            retired_physical.add(physical)
            retired.add(str(physical))
            retired.add('$PROJECT_DIR$/' + os.path.relpath(physical, root))
    removed = []
    for mapping in list(component.findall('mapping')):
        value = mapping.get('directory', '')
        expanded = value.replace('$PROJECT_DIR$', str(root), 1) if value.startswith('$PROJECT_DIR$') else value
        # Normalize known path spelling without following an unrelated custom symlink.
        same_physical_root = bool(expanded) and Path(expanded).is_absolute() and Path(os.path.normpath(expanded)) in retired_physical
        if mapping.get('vcs') == 'Git' and (value in retired or same_physical_root):
            removed.append(value)
            component.remove(mapping)
    existing = {item.get('directory') for item in component.findall('mapping')}
    mappings = ['$PROJECT_DIR$'] + ['$PROJECT_DIR$/' + repo['name'] for repo in repos]
    added = []
    for mapping in mappings:
        if mapping not in existing:
            ET.SubElement(component, 'mapping', directory=mapping, vcs='Git')
            added.append(mapping)
    if not dry_run and (added or removed or not target.exists()):
        directory.mkdir(exist_ok=True)
        ET.indent(tree, space='  ')
        # Write beside destination and replace atomically, preserving malformed input on error.
        fd, filename = tempfile.mkstemp(prefix='.vcs-', suffix='.xml', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as output:
                tree.write(output, encoding='UTF-8', xml_declaration=True)
                output.write(b'\n')
            os.replace(filename, target)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
    return dict(state='planned' if dry_run else 'configured', added=added, removed=removed)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('command', choices=('bootstrap', 'update', 'status', 'idea'))
    parser.add_argument('--only', nargs='+', metavar='NAME')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--json', action='store_true', dest='json_output')
    parser.add_argument('--reuse-existing', metavar='PATH')
    argv = sys.argv[1:] if argv is None else argv
    json_output = '--json' in argv
    result = dict(command=None, dry_run='--dry-run' in argv, repositories=[])
    code = 0
    try:
        args = parser.parse_args(argv)
        result['command'] = args.command
        if args.reuse_existing is not None and args.command != 'bootstrap':
            raise WorkspaceError('--reuse-existing is only valid for bootstrap.')
        repos = load_manifest(ROOT)
        active_repos = repos
        if args.only:
            names = {repo['name'] for repo in repos}
            if set(args.only) - names:
                raise WorkspaceError('Unknown repository in --only.')
            repos = [repo for repo in repos if repo['name'] in args.only]
        if args.command == 'idea':
            result['idea'] = idea(ROOT, repos, args.dry_run, active_repos)
        else:
            for repo in repos:
                row = dict(name=repo['name'])
                try:
                    if args.command == 'bootstrap':
                        row.update(bootstrap(ROOT, repo, args))
                    elif args.command == 'update':
                        row.update(update(ROOT, repo, args))
                    else:
                        path = ROOT / repo['name']
                        validate_repository(path, repo)
                        row.update(state='ok', **local_state(path, repo))
                    if row['state'] == 'skipped' and code == 0:
                        code = 2
                except (WorkspaceError, OSError) as error:
                    row.update(state='error', error=str(error) if isinstance(error, WorkspaceError)
                               else 'Filesystem operation failed.')
                    code = 1
                result['repositories'].append(row)
    except (WorkspaceError, OSError) as error:
        result['error'] = str(error) if isinstance(error, WorkspaceError) else 'Filesystem operation failed.'
        code = 1
    result['exit_code'] = code
    result['summary'] = {key: sum(row.get('state') == key for row in result['repositories'])
                         for key in ('error', 'skipped')}
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if 'error' in result:
            print('ERROR: ' + result['error'])
        for row in result['repositories']:
            details = [row['state']]
            if 'branch' in row:
                details += ['branch=' + (row['branch'] or 'detached'), 'sha=' + row['sha'],
                            'dirty=' + str(row['dirty']).lower(), 'ahead=' + str(row['ahead']),
                            'behind=' + str(row['behind'])]
            details += [row[key] for key in ('reason', 'error') if key in row]
            print(row['name'] + ': ' + ' '.join(details))
        if 'idea' in result:
            print('idea: ' + result['idea']['state'])
        print('exit=' + str(code) + ' errors=' + str(result['summary']['error'])
              + ' skipped=' + str(result['summary']['skipped']))
    return code


if __name__ == '__main__':
    sys.exit(main())
