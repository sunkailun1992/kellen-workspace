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
from xml.parsers import expat

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_REPOSITORIES = ('ai-wechat-mini-programs',)
IDEA_XML_MAX_BYTES = 2 * 1024 * 1024
IDEA_XML_MAX_INPUT_BYTES = 8 * 1024 * 1024
IDEA_XML_MAX_INPUT_FILES = 512
IDEA_XML_MAX_DEPTH = 128
IDEA_XML_MAX_NODES = 50000
IDEA_XML_MAX_ATTRIBUTES = 256
IDEA_XML_MAX_NAME_CHARS = 1024
IDEA_XML_MAX_NAMESPACE_CHARS = 512
IDEA_XML_MAX_NAMESPACES = 128
IDEA_XML_MAX_EXPANDED_CHARS = 8 * 1024 * 1024


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


def read_idea_bytes(path):
    """Bound every read of local IDE metadata, including idempotence comparisons."""
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError('IDEA input must be a local regular file.')
    with path.open('rb') as source:
        data = source.read(IDEA_XML_MAX_BYTES + 1)
    if len(data) > IDEA_XML_MAX_BYTES:
        raise WorkspaceError('IDEA XML exceeds the file size limit.')
    return data


class IdeaXmlInputs:
    """Share a bounded, canonical-path parse cache for one IDEA operation."""

    def __init__(self):
        self.documents = {}
        self.raw = {}
        self.total_bytes = 0

    def parse(self, path):
        # Check the original spelling too: cached aliases must not admit symlinks.
        if path.is_symlink() or not path.is_file():
            raise WorkspaceError('IDEA input must be a local regular file.')
        canonical = path.resolve()
        if canonical not in self.documents:
            if len(self.documents) >= IDEA_XML_MAX_INPUT_FILES:
                raise WorkspaceError('IDEA XML exceeds the input file count limit.')
            data = read_idea_bytes(path)
            self.total_bytes += len(data)
            if self.total_bytes > IDEA_XML_MAX_INPUT_BYTES:
                raise WorkspaceError('IDEA XML exceeds the total input size limit.')
            # Count invalid files too when a custom .iml is safely skipped.
            self.documents[canonical] = None
            self.documents[canonical] = parse_idea_xml_bytes(data)
            self.raw[canonical] = data
        document = self.documents[canonical]
        if document is None:
            raise ET.ParseError('Invalid IDEA XML.')
        return document


def parse_idea_xml(path, inputs=None):
    return (inputs if inputs is not None else IdeaXmlInputs()).parse(path)


def parse_idea_xml_bytes(data):
    """Parse bounded XML with declarations denied and namespace expansion budgeted.

    A raw Expat pass checks attribute/namespace bounds before the namespace-aware
    parser can allocate expanded attribute names. Both passes use the same bytes.
    """
    if len(data) > IDEA_XML_MAX_BYTES:
        raise WorkspaceError('IDEA XML exceeds the file size limit.')

    def forbidden(*unused):
        raise WorkspaceError('IDEA XML DTD and entity declarations are forbidden.')

    def parser_for(namespace_separator=None):
        parser = expat.ParserCreate(namespace_separator=namespace_separator)
        parser.StartDoctypeDeclHandler = forbidden
        parser.EntityDeclHandler = forbidden
        parser.ExternalEntityRefHandler = forbidden
        if not parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER):
            raise WorkspaceError('Cannot enforce IDEA XML entity protection.')
        return parser

    depth = nodes = namespaces = 0

    def node():
        nonlocal nodes
        nodes += 1
        if nodes > IDEA_XML_MAX_NODES:
            raise WorkspaceError('IDEA XML exceeds the node limit.')

    def start(name, attributes):
        nonlocal depth, namespaces
        node()
        depth += 1
        if depth > IDEA_XML_MAX_DEPTH or len(attributes) > IDEA_XML_MAX_ATTRIBUTES:
            raise WorkspaceError('IDEA XML exceeds the structural limits.')
        if len(name) > IDEA_XML_MAX_NAME_CHARS or any(
                len(key) > IDEA_XML_MAX_NAME_CHARS for key in attributes):
            raise WorkspaceError('IDEA XML exceeds the name length limit.')
        for key, value in attributes.items():
            if key == 'xmlns' or key.startswith('xmlns:'):
                namespaces += 1
                if (namespaces > IDEA_XML_MAX_NAMESPACES
                        or len(value) > IDEA_XML_MAX_NAMESPACE_CHARS):
                    raise WorkspaceError('IDEA XML exceeds the namespace limits.')

    def end(unused):
        nonlocal depth
        depth -= 1

    preflight = parser_for()
    preflight.StartElementHandler = start
    preflight.EndElementHandler = end
    preflight.CommentHandler = lambda unused: node()
    builder = ET.TreeBuilder(insert_comments=True)
    retained = 0

    def budget(value):
        nonlocal retained
        retained += len(value)
        if retained > IDEA_XML_MAX_EXPANDED_CHARS:
            raise WorkspaceError('IDEA XML exceeds the expanded character limit.')

    def expanded(name):
        return '{' + name if '}' in name else name

    def build_start(name, attributes):
        name = expanded(name)
        budget(name)
        converted = {}
        for key, value in attributes.items():
            key = expanded(key)
            budget(key)
            budget(value)
            converted[key] = value
        builder.start(name, converted)

    def build_data(value):
        budget(value)
        builder.data(value)

    def build_comment(value):
        budget(value)
        builder.comment(value)

    parser = parser_for('}')
    parser.StartElementHandler = build_start
    parser.EndElementHandler = lambda name: builder.end(expanded(name))
    parser.CharacterDataHandler = build_data
    parser.CommentHandler = build_comment
    try:
        preflight.Parse(data, True)
        parser.Parse(data, True)
        return ET.ElementTree(builder.close())
    except (expat.ExpatError, ValueError, LookupError):
        # Never return parser diagnostics containing input values or external IDs.
        raise ET.ParseError('Invalid IDEA XML.') from None


def idea_xml(path, tag, inputs=None, **attributes):
    """Read local IDE metadata without following output links or replacing invalid input."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise WorkspaceError('IDEA output must be a local regular file.')
    if not path.exists():
        return ET.ElementTree(ET.Element(tag, attributes))
    try:
        tree = parse_idea_xml(path, inputs)
    except (ET.ParseError, OSError):
        raise WorkspaceError('Existing IDEA module metadata is invalid; preserved it.') from None
    if tree.getroot().tag != tag:
        raise WorkspaceError('Existing IDEA module metadata has an invalid root; preserved it.')
    return tree


def idea_content_path(root, module_path, value):
    """Resolve a module content URL in its own file context, including IDEA macros."""
    if not value.startswith('file://'):
        return None
    expanded = (value.removeprefix('file://').replace('$PROJECT_DIR$', str(root.resolve()))
                .replace('$MODULE_DIR$', str(module_path.resolve().parent)))
    if '$' in expanded or not Path(expanded).is_absolute():
        return None
    return Path(expanded).resolve()


def web_module_plan(root, repos, inputs=None):
    """Plan standalone web modules; Gradle remains responsible only for Java builds."""
    web = [repo for repo in repos if repo['kind'] == 'web']
    if not web:
        return [], []
    inputs = inputs if inputs is not None else IdeaXmlInputs()
    directory = root / '.idea' / 'workspace-web'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise WorkspaceError('IDEA web module directory must be a local directory.')
    registry = root / '.idea' / 'modules.xml'
    project = idea_xml(registry, 'project', inputs=inputs, version='4')
    component = project.getroot().find("component[@name='ProjectModuleManager']")
    if component is None:
        component = ET.SubElement(project.getroot(), 'component', name='ProjectModuleManager')
    modules = component.find('modules')
    if modules is None:
        modules = ET.SubElement(component, 'modules')
    outputs, configured = [], []
    for repo in web:
        source = root / repo['name']
        validate_repository(source, repo)
        module_path = directory / (repo['name'] + '.iml')
        # Respect an already attached custom web module for the same real checkout.
        # Never rewrite that user's .iml or claim ownership of unrelated modules.
        existing = None
        inspected = set()
        for item in modules.findall('module'):
            value = item.get('filepath', item.get('fileurl', '').removeprefix('file://'))
            path = Path(value.replace('$PROJECT_DIR$', str(root), 1))
            if (not path.is_absolute() or path.suffix != '.iml' or path.is_symlink()
                    or not path.is_file() or path.resolve() == module_path.resolve()):
                continue
            canonical = path.resolve()
            if canonical in inspected:
                continue
            inspected.add(canonical)
            try:
                custom = parse_idea_xml(path, inputs).getroot()
            except (ET.ParseError, OSError):
                continue
            if custom.tag != 'module' or custom.get('type') != 'WEB_MODULE':
                continue
            for content in custom.findall('.//content'):
                if idea_content_path(root, path, content.get('url', '')) == source.resolve():
                    existing = item
                    break
            if existing is not None:
                break
        if existing is not None:
            for item in list(modules.findall('module')):
                value = item.get('filepath', item.get('fileurl', '').removeprefix('file://'))
                path = Path(value.replace('$PROJECT_DIR$', str(root), 1))
                if path.is_absolute() and path.resolve() == module_path.resolve():
                    modules.remove(item)
            configured.append(dict(name=repo['name'], module=existing.get('filepath'),
                                   state='existing-custom-module'))
            continue
        module = idea_xml(module_path, 'module', inputs=inputs, type='WEB_MODULE', version='4')
        if module.getroot().get('type') != 'WEB_MODULE':
            raise WorkspaceError('Existing frontend module type conflicts; preserved it.')
        manager = module.getroot().find("component[@name='NewModuleRootManager']")
        if manager is None:
            manager = ET.SubElement(module.getroot(), 'component', name='NewModuleRootManager')
        # Module files do not reliably expand PROJECT_DIR in content/watch roots.
        # Only ignored local metadata contains this dynamically resolved path.
        content_url = 'file://' + str(source.resolve())
        contents = manager.findall('content')
        content = next((item for item in contents if idea_content_path(
            root, module_path, item.get('url', '')) == source.resolve()), None)
        if content is None:
            if contents:
                raise WorkspaceError('Existing frontend content root conflicts; preserved it.')
            content = ET.SubElement(manager, 'content', url=content_url)
        else:
            previous = content.get('url')
            if '$PROJECT_DIR$' in previous:
                # Repair only our owned module and URLs under its verified root.
                for item in content.iter():
                    value = item.get('url', '')
                    if value == previous or value.startswith(previous + '/'):
                        item.set('url', content_url + value[len(previous):])
            else:
                # IDEA may serialize an equivalent MODULE_DIR or absolute URL.
                content_url = previous
        for name in ('node_modules', 'dist', 'coverage'):
            value = content_url + '/' + name
            if not any(item.get('url') == value for item in content.findall('excludeFolder')):
                ET.SubElement(content, 'excludeFolder', url=value)
        if manager.find("orderEntry[@type='sourceFolder']") is None:
            ET.SubElement(manager, 'orderEntry', type='sourceFolder', forTests='false')
        filepath = '$PROJECT_DIR$/.idea/workspace-web/' + repo['name'] + '.iml'
        matching = []
        for item in modules.findall('module'):
            value = item.get('filepath', item.get('fileurl', '').removeprefix('file://'))
            expanded = value.replace('$PROJECT_DIR$', str(root), 1)
            if Path(expanded).is_absolute() and Path(expanded).resolve() == module_path.resolve():
                matching.append(item)
        entry = matching[0] if matching else ET.SubElement(modules, 'module')
        entry.set('filepath', filepath)
        entry.set('fileurl', 'file://' + filepath)
        for duplicate in matching[1:]:
            modules.remove(duplicate)
        outputs.append((module_path, module))
        configured.append(dict(name=repo['name'], module=filepath, content_root=content_url))
    outputs.append((registry, project))
    return outputs, configured


def idea_xml_bytes(tree):
    """Budget formatted output before any of the planned files are written."""
    ET.indent(tree, space='  ')
    data = ET.tostring(tree.getroot(), encoding='UTF-8', xml_declaration=True) + b'\n'
    if len(data) > IDEA_XML_MAX_BYTES:
        raise WorkspaceError('Generated IDEA XML exceeds the file size limit.')
    # Added mappings/modules must remain acceptable to the next invocation too.
    parse_idea_xml_bytes(data)
    return data


def write_idea_xml(path, data):
    """Atomically update generated metadata only when its bounded bytes changed."""
    if path.exists() and read_idea_bytes(path) == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.idea-', suffix='.xml', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def idea_build_plan(root, inputs):
    """Keep native IDEA compilation for this linked root without changing test runners."""
    path = root / '.idea' / 'gradle.xml'
    if path.parent.is_symlink():
        raise WorkspaceError('IDEA output must be a local directory.')
    tree = idea_xml(path, 'project', inputs=inputs, version='4')
    before = inputs.raw.get(path.resolve())
    components = tree.getroot().findall("component[@name='GradleSettings']")
    if len(components) > 1:
        raise WorkspaceError('Ambiguous Gradle settings; preserved the original metadata.')
    component = components[0] if components else ET.SubElement(tree.getroot(), 'component', name='GradleSettings')
    options = component.findall("option[@name='linkedExternalProjectsSettings']")
    if len(options) > 1:
        raise WorkspaceError('Ambiguous linked Gradle projects; preserved the original metadata.')
    linked = options[0] if options else ET.SubElement(component, 'option', name='linkedExternalProjectsSettings')
    sets = linked.findall('set')
    if len(sets) > 1 or (not sets and len(linked)):
        raise WorkspaceError('Unknown linked Gradle project metadata; preserved it.')
    projects = sets[0] if sets else ET.SubElement(linked, 'set')
    matches = []
    for settings in projects.findall('GradleProjectSettings'):
        locations = settings.findall("option[@name='externalProjectPath']")
        if len(locations) > 1:
            raise WorkspaceError('Ambiguous Gradle project path; preserved the original metadata.')
        location = locations[0] if locations else None
        value = location.get('value', '') if location is not None else ''
        expanded = value.replace('$PROJECT_DIR$', str(root.resolve()))
        if '$' not in expanded and Path(expanded).is_absolute() and Path(expanded).resolve() == root.resolve():
            matches.append(settings)
    if len(matches) > 1:
        raise WorkspaceError('Duplicate workspace Gradle links; preserved the original metadata.')
    settings = matches[0] if matches else ET.SubElement(projects, 'GradleProjectSettings')
    if not matches:
        ET.SubElement(settings, 'option', name='externalProjectPath', value='$PROJECT_DIR$')
    delegated = settings.findall("option[@name='delegatedBuild']")
    if len(delegated) > 1:
        raise WorkspaceError('Ambiguous delegated build setting; preserved the original metadata.')
    option = delegated[0] if delegated else ET.SubElement(settings, 'option', name='delegatedBuild')
    option.set('value', 'false')
    return dict(path=path, before=before, after=idea_xml_bytes(tree))


def assert_idea_build_plan_current(plan):
    path = plan['path']
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise WorkspaceError('IDEA Gradle settings changed during planning; preserved the current file.')
    current = read_idea_bytes(path) if path.exists() else None
    if current != plan['before']:
        raise WorkspaceError('IDEA Gradle settings changed during planning; preserved the current file.')


def idea(root, repos, dry_run, active_repos=None):
    inputs = IdeaXmlInputs()
    directory = root / '.idea'
    target = directory / 'vcs.xml'
    if directory.is_symlink() or target.is_symlink():
        raise WorkspaceError('IDEA output must be a local regular file.')
    if target.exists():
        try:
            tree = parse_idea_xml(target, inputs)
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
    # Gradle imports real paths. Map each managed Git root once, so IDEA does
    # not treat a reuse symlink and its target as two independent repositories.
    managed = dict.fromkeys([root.resolve()] + [(root / repo['name']).resolve() for repo in repos])

    def mapping_path(value):
        if value == '$PROJECT_DIR$':
            return root
        if value.startswith('$PROJECT_DIR$/'):
            return Path(os.path.normpath(root / value[len('$PROJECT_DIR$/'):]))
        if Path(value).is_absolute():
            return Path(os.path.normpath(value))
        return None

    preferred = {}
    for item in component.findall('mapping'):
        path = mapping_path(item.get('directory', ''))
        if item.get('vcs') == 'Git' and path in managed:
            preferred.setdefault(path, item.get('directory'))
    for path in managed:
        relative = os.path.relpath(path, root.resolve())
        preferred.setdefault(path, '$PROJECT_DIR$' if relative == '.' else '$PROJECT_DIR$/' + relative)

    seen = set()
    for item in list(component.findall('mapping')):
        value = item.get('directory', '')
        path = mapping_path(value)
        physical = path.resolve() if path is not None else None
        if item.get('vcs') == 'Git' and physical in managed:
            if value != preferred[physical] or physical in seen:
                removed.append(value)
                component.remove(item)
            else:
                seen.add(physical)
    existing = {item.get('directory') for item in component.findall('mapping')}
    mappings = list(preferred.values())
    added = []
    for mapping in mappings:
        if mapping not in existing:
            ET.SubElement(component, 'mapping', directory=mapping, vcs='Git')
            added.append(mapping)
    # Plan the one workspace Gradle link; preserve testRunner and all other links.
    build_plan = idea_build_plan(root, inputs)
    # Validate all module metadata before writing vcs.xml or any module output.
    web_outputs, web_modules = web_module_plan(root, repos, inputs)
    outputs = [(path, idea_xml_bytes(document)) for path, document in web_outputs]
    if added or removed or not target.exists():
        outputs.append((target, idea_xml_bytes(tree)))
    if not dry_run:
        assert_idea_build_plan_current(build_plan)
        write_idea_xml(build_plan['path'], build_plan['after'])
        for path, data in outputs:
            write_idea_xml(path, data)
    return dict(state='planned' if dry_run else 'configured', added=added, removed=removed,
                web_modules=web_modules, delegated_build=False,
                gradle_settings_changed=build_plan['before'] != build_plan['after'])


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
