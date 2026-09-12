"""Install a versioned project snapshot without changing global Codex configuration."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            s = part.lstat()
            if part.is_symlink() or getattr(s, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Link/reparse point not allowed: ' + str(part))
    return path


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False)+'\n').encode('utf-8')


def _path_info(path):
    """Return lstat data without ever resolving a missing/link target."""
    try:
        return Path(path).lstat()
    except FileNotFoundError:
        return None


def _is_link_or_reparse(path, info=None):
    info = info or _path_info(path)
    if info is None:
        return False
    junction = getattr(Path(path), 'is_junction', None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
        or bool(junction and junction())
    )


def _unlink_link_only(path, info):
    """Remove a link/reparse entry itself, never anything below its target."""
    path = Path(path)
    if stat.S_ISDIR(info.st_mode):
        os.rmdir(path)
    else:
        path.unlink()


def _remove_rollback_links(project, target):
    """Remove attacker-substituted links on a rollback path without following them."""
    project, target = Path(project), Path(target)
    try:
        relative = target.relative_to(project)
    except ValueError as exc:
        raise ValueError('Rollback target escaped project: ' + str(target)) from exc
    project_info = _path_info(project)
    if project_info is None or _is_link_or_reparse(project, project_info):
        raise ValueError('Project root changed during rollback: ' + str(project))
    current = project
    for part in relative.parts:
        current /= part
        info = _path_info(current)
        if info is not None and _is_link_or_reparse(current, info):
            _unlink_link_only(current, info)
            # Removing an ancestor makes all remaining components absent.
            break


def _rollback_write(project, target, content):
    """Restore bytes atomically after revalidating the destination path."""
    project, target = Path(project), Path(target)
    rollback_temp = project / ('.galaxy-rollback-' + uuid.uuid4().hex + '.tmp')
    try:
        safe_path(rollback_temp)
        with rollback_temp.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _remove_rollback_links(project, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_path(target.parent)
        safe_path(target)
        os.replace(rollback_temp, target)
    finally:
        info = _path_info(rollback_temp)
        if info is not None:
            if _is_link_or_reparse(rollback_temp, info):
                _unlink_link_only(rollback_temp, info)
            elif stat.S_ISREG(info.st_mode):
                rollback_temp.unlink()


def _rollback_remove(project, target):
    """Remove a newly-created file or substituted link, but never its referent."""
    project, target = Path(project), Path(target)
    _remove_rollback_links(project, target)
    safe_path(target)
    info = _path_info(target)
    if info is None:
        return
    if _is_link_or_reparse(target, info):
        _unlink_link_only(target, info)
    elif stat.S_ISREG(info.st_mode):
        target.unlink()
    else:
        raise ValueError('Rollback expected a file: ' + str(target))


V2_TRACKED_FILES = (
    'AGENTS.md',
    '.galaxy/project.yml',
    '.galaxy/team.yml',
    '.galaxy/checks.json',
    'galaxy.lock',
    '.github/workflows/galaxy-validate.yml',
)

V2_LOCKED_DECLARATIONS = (
    'AGENTS.md',
    '.galaxy/project.yml',
    '.galaxy/team.yml',
    '.galaxy/checks.json',
)

V2_COOP_FILES = (
    '.github/workflows/galaxy-control.yml',
)

V1_TEMPLATE_FILES = (
    'AGENT_TEAM.yml',
    'AGENTS.md',
    '.agents/skills/multicontroller/SKILL.md',
    '.agents/skills/multicontroller/references/contract.md',
    '.agents/skills/multicontroller/references/gates.md',
    '.agents/skills/multicontroller/references/learning.md',
    '.agents/skills/multicontroller/references/remote.md',
    '.codex/config.toml',
    '.codex/agents/explorer.toml',
    '.codex/agents/hard-worker.toml',
    '.codex/agents/researcher.toml',
    '.codex/agents/reviewer.toml',
    '.codex/agents/tester.toml',
    '.codex/agents/worker.toml',
    '.multicontroller/checks.json',
    '.multicontroller/examples/control-claim.json',
    '.multicontroller/examples/control-state.json',
    '.multicontroller/examples/gate.json',
    '.multicontroller/examples/grant.json',
    '.multicontroller/examples/history.json',
    '.multicontroller/examples/reclaim.json',
    '.multicontroller/examples/usage.json',
    '.multicontroller/messages/BLOCKER.md',
    '.multicontroller/messages/CLAIM-ACK.md',
    '.multicontroller/messages/CLAIM-GRANT.md',
    '.multicontroller/messages/CLAIM-REQUEST.md',
    '.multicontroller/messages/EXECUTION-SUMMARY.md',
    '.multicontroller/messages/HANDOFF.md',
    '.multicontroller/messages/INTERFACE-CHANGE.md',
    '.multicontroller/messages/RELEASE.md',
    '.multicontroller/messages/REVOKE.md',
)

V1_COOP_FILES = (
    '.github/CODEOWNERS',
    '.github/pull_request_template.md',
    '.github/ISSUE_TEMPLATE/agent-task.yml',
    '.github/workflows/multicontroller-control.yml',
    '.github/workflows/multicontroller.yml',
)


def install_v2(master, project, check=False, mode='SOLO', preset='balanced'):
    """Install the canonical V2 project snapshot and local Codex projection.

    All bytes are rendered in an isolated staging directory first.  Occupied
    destinations must match exactly, and any write failure restores the prior
    project bytes before returning an error.
    """
    master, project = safe_path(master), safe_path(project)
    if master == project or master in project.parents or project in master.parents:
        raise ValueError('Master and project must be separate directory trees')
    if not project.is_dir():
        raise ValueError('Project directory must already exist')
    if mode not in ('SOLO', 'CO-OP'):
        raise ValueError('invalid V2 mode')
    if preset not in ('balanced', 'critical'):
        raise ValueError('invalid V2 preset')

    with tempfile.TemporaryDirectory(prefix='galaxy-install-') as temporary:
        stage = Path(temporary)
        exclude_original = None
        tracked_files = V2_TRACKED_FILES + (V2_COOP_FILES if mode == 'CO-OP' else ())
        for name in tracked_files:
            source = safe_path(master / 'template' / name)
            if not source.is_file():
                raise ValueError('Missing canonical template file: ' + name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        team_path = stage / '.galaxy/team.yml'
        team = json.loads(team_path.read_text(encoding='utf-8'))
        team['mode'] = mode
        if mode == 'CO-OP':
            team['coordination'] = {
                'automatic_expiry': False,
                'backend': 'github-actions-issue',
                'capability': 'github-actions',
                'claim_protocol': 'serialized-workflow',
                'control_issue': None,
                'workflow': '.github/workflows/galaxy-control.yml',
            }
            team['required_checks'] = ['galaxy / validate']
        team_path.write_bytes(encoded(team))
        project_path = stage / '.galaxy/project.yml'
        project_config = json.loads(project_path.read_text(encoding='utf-8'))
        project_config['routing'] = {'profile': preset}
        project_path.write_bytes(encoded(project_config))
        lock_path = stage / 'galaxy.lock'
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        lock['declarations'] = {
            name: digest((stage / name).read_bytes())
            for name in V2_LOCKED_DECLARATIONS
        }
        lock_path.write_bytes(encoded(lock))
        if (project / '.git').is_dir():
            exclude = project / '.git/info/exclude'
            staged_exclude = stage / '.git/info/exclude'
            staged_exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude_original = exclude.read_bytes() if exclude.is_file() else b''
            staged_exclude.write_bytes(exclude_original)

        try:
            from .bootstrap import bootstrap
        except ImportError:
            from bootstrap import bootstrap  # type: ignore
        bootstrap(stage, catalog_root=master / 'specialists')
        sources = {
            path.relative_to(stage).as_posix(): path.read_bytes()
            for path in stage.rglob('*') if path.is_file()
        }
        changes = {}
        expected_current = {}
        for name, content in sorted(sources.items()):
            target = safe_path(project / name)
            if target.exists():
                if not target.is_file():
                    raise ValueError('Expected a file: ' + name)
                current = target.read_bytes()
                if current != content and name == '.git/info/exclude' and current == exclude_original:
                    changes[name] = content
                    expected_current[name] = current
                elif current != content:
                    raise ValueError('Existing incompatible managed file; reconcile first: ' + name)
            else:
                changes[name] = content
                expected_current[name] = None

        result = {
            'command': 'install',
            'schema_version': 1,
            'check': bool(check),
            'mode': mode,
            'preset': preset,
            'changed': sorted(changes),
            'configuration': 'valid',
        }
        if check or not changes:
            return result

        originals, created_dirs, written_names = {}, [], []
        try:
            for name in changes:
                target = safe_path(project / name)
                originals[name] = target.read_bytes() if target.exists() else None
            for name, content in changes.items():
                target = safe_path(project / name)
                expected = expected_current[name]
                if expected is None:
                    if target.exists():
                        raise ValueError('Install target changed concurrently: ' + name)
                elif not target.is_file() or target.read_bytes() != expected:
                    raise ValueError('Install target changed concurrently: ' + name)
                missing = []
                parent = target.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for folder in reversed(missing):
                    folder.mkdir()
                    created_dirs.append(folder)
                temporary_target = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    with temporary_target.open('xb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary_target, target)
                    written_names.append(name)
                finally:
                    if temporary_target.exists():
                        temporary_target.unlink()
        except BaseException as install_error:
            rollback_errors = []
            for name in reversed(written_names):
                content = originals[name]
                target = project / name
                try:
                    if content is None:
                        _rollback_remove(project, target)
                    else:
                        _rollback_write(project, target, content)
                except BaseException as rollback_error:
                    rollback_errors.append(name + ': ' + str(rollback_error))
            for directory in reversed(created_dirs):
                try:
                    _remove_rollback_links(project, directory)
                    safe_path(directory)
                    if _path_info(directory) is not None:
                        directory.rmdir()
                except BaseException as rollback_error:
                    rollback_errors.append(
                        str(directory.relative_to(project)) + ': ' + str(rollback_error)
                    )
            if rollback_errors:
                raise RuntimeError(
                    'Install failed and rollback could not safely restore all targets: '
                    + '; '.join(rollback_errors)
                ) from install_error
            raise
        return result


def install(master, project, mode='SOLO', preset='balanced', preview=False):
    master, project = safe_path(master), safe_path(project)
    if master == project or master in project.parents or project in master.parents:
        raise ValueError('Master and project must be separate directory trees')
    if not project.is_dir(): raise ValueError('Project directory must already exist')
    if preview: return _install(master, project, mode, preset, True)
    lock = safe_path(project/'.multicontroller-install.lock')
    try: fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError: raise ValueError('Installation already in progress; inspect stale lock before retry')
    os.close(fd)
    try:
        return _install(master, project, mode, preset, False)
    finally:
        lock.unlink()


def _install(master, project, mode, preset, preview):
    if mode not in ('SOLO','CO-OP') or preset not in ('balanced','critical'):
        raise ValueError('invalid mode/preset')
    master, project = safe_path(master), safe_path(project)
    if master == project or master in project.parents or project in master.parents:
        raise ValueError('Master and project must be separate directory trees')
    if not project.is_dir(): raise ValueError('Project directory must already exist')
    sources = {}
    source_groups = [(master / 'legacy-template', V1_TEMPLATE_FILES)]
    if mode == 'CO-OP':
        source_groups.append((master / 'coop', V1_COOP_FILES))
    for folder, names in source_groups:
        for relative in names:
            source = safe_path(folder / relative)
            if not source.is_file():
                raise ValueError('Missing V1 compatibility template file: ' + relative)
            sources[relative] = source.read_bytes()
    sources['.codex/config.toml'] = (master/f'presets/{preset}/config.toml').read_bytes()
    sources['.multicontroller/policy.json'] = (master/f'presets/{preset}/policy.json').read_bytes()
    team = json.loads(sources['AGENT_TEAM.yml'])
    team['mode'] = mode
    sources['AGENT_TEAM.yml'] = encoded(team)
    for name in ('multicontroller.py', 'installer.py', 'coordinator.py'):
        sources['.multicontroller/tools/'+name] = (master/'lib'/name).read_bytes()
    manifest_path = safe_path(project/'.multicontroller/install-manifest.json')
    old = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if old and (old.get('mode') != mode): raise ValueError('Mode migration needs a reviewed fresh installation; no implicit deletion')
    # Custom project settings remain project-owned after the first installation.
    customizable = {'AGENT_TEAM.yml', '.multicontroller/checks.json'}
    changes = {}
    for name, content in sources.items():
        target = safe_path(project/name)
        if target.exists():
            if not target.is_file(): raise ValueError('Expected a file: '+name)
            current = target.read_bytes()
            previous = old.get('files', {}).get(name)
            if old and name in customizable:
                if name == 'AGENT_TEAM.yml':
                    existing_team = json.loads(current)
                    if existing_team.get('schema_version') in (1,2):
                        existing_team['schema_version'] = 3
                        existing_team.setdefault('rules',{})['cross_review'] = 'optional'
                        existing_team['review'] = {'independent_agent_required': True, 'partner_review': 'optional'}
                        existing_team['integration_operators']=[o['id'] for o in existing_team.get('operators',[])]
                        existing_team['coordination']={'backend':'github-actions-issue','claim_protocol':'serialized-workflow',
                                                       'control_issue':None,'automatic_expiry':False}
                        current = encoded(existing_team)
                        if current != target.read_bytes(): changes[name] = current
                sources[name] = current
                continue
            if current == content: continue
            if not previous or digest(current) != previous:
                raise ValueError('Existing or locally modified managed file; reconcile first: '+name)
        changes[name] = content
    # Removed files need explicit reconciliation; never silently leave unknown old code active.
    if set(old.get('files', {})) - set(sources): raise ValueError('Release removes managed files; manual reviewed migration required')
    ignore_path = safe_path(project/'.gitignore')
    ignore = ignore_path.read_bytes() if ignore_path.exists() else b''
    marker = b'# Multicontroller local artifacts'
    if marker not in ignore:
        changes['.gitignore'] = ignore + (b'\n' if ignore and not ignore.endswith(b'\n') else b'') + marker + b'\n.multicontroller/local/\n**/__pycache__/\n'
    manifest = dict(schema_version=1,version=(master/'VERSION').read_text().strip(),mode=mode,preset=preset,
                    files={name:digest(data) for name,data in sorted(sources.items())})
    manifest_data = encoded(manifest)
    if not manifest_path.exists() or manifest_path.read_bytes() != manifest_data:
        changes['.multicontroller/install-manifest.json'] = manifest_data
    result = dict(mode=mode,preset=preset,preview=preview,changed=sorted(changes))
    if preview or not changes: return result
    originals, created_dirs = {}, []
    try:
        # Preserve rollback bytes in memory and disk before the first managed file write.
        for name in changes:
            target = safe_path(project/name)
            originals[name] = target.read_bytes() if target.exists() else None
        if any(v is not None for v in originals.values()):
            backup = safe_path(master/'local'/'backups'/uuid.uuid4().hex)
            backup.mkdir(parents=True)
            for name, data in originals.items():
                if data is not None:
                    target = backup/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            result['backup'] = str(backup)
        for name, data in changes.items():
            target = safe_path(project/name)
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for folder in reversed(missing):
                folder.mkdir()
                created_dirs.append(folder)
            tmp = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                with tmp.open('xb') as stream: stream.write(data)
                os.replace(tmp, target)
            finally:
                if tmp.exists(): tmp.unlink()
    except BaseException:
        for name, data in originals.items():
            target = project/name
            if data is None:
                if target.is_file(): target.unlink()
            elif target.parent.exists(): target.write_bytes(data)
        for directory in reversed(created_dirs):
            if directory.exists() and not any(directory.iterdir()): directory.rmdir()
        raise
    return result


def validate_project(project, run_checks=False):
    project = safe_path(project)
    team = json.loads((project/'AGENT_TEAM.yml').read_text(encoding='utf-8-sig'))
    mode = team.get('mode')
    if mode not in ('SOLO', 'CO-OP'): raise ValueError('invalid project mode')
    config = tomllib.loads((project/'.codex/config.toml').read_text(encoding='utf-8-sig'))
    policy = json.loads((project/'.multicontroller/policy.json').read_text())
    agents = config.get('agents', {})
    if agents.get('max_concurrent_threads_per_session') != policy.get('max_subagents'):
        raise ValueError('Concurrency config/policy mismatch')
    for role in ('explorer','researcher','worker','hard-worker','tester','reviewer'):
        ref = agents.get(role, {}).get('config_file')
        if not isinstance(ref, str): raise ValueError('Missing role: '+role)
        from multicontroller import normalized_path
        normalized_path(ref)
        f = safe_path(project/'.codex'/ref)
        data = tomllib.loads(f.read_text(encoding='utf-8-sig'))
        if not data.get('model') or data.get('model_reasoning_effort') not in ('low','medium','high'):
            raise ValueError('Invalid role model/effort: '+role)
    if not (project/'.agents/skills/multicontroller/SKILL.md').is_file(): raise ValueError('Missing skill')
    checks = json.loads((project/'.multicontroller/checks.json').read_text(encoding='utf-8-sig')).get('commands')
    if not isinstance(checks, list): raise ValueError('commands must be an array')
    if run_checks:
        if mode == 'CO-OP':
            operators = team.get('operators', [])
            ids = [o.get('id') for o in operators]
            logins = [str(o.get('github_login') or '').casefold() for o in operators]
            if len(ids)<1 or len(set(ids)) != len(ids) or any(not x for x in ids+logins) or len(set(logins)) != len(logins):
                raise ValueError('CO-OP needs at least the primary operator; logins must be distinct')
            peers=team.get('integration_operators')
            if not isinstance(peers,list) or not peers or any(p not in ids for p in peers):
                raise ValueError('Configure integration_operators with authorized peer IDs')
            if team.get('rules',{}).get('cross_review') != 'optional' or team.get('review') != {'independent_agent_required': True, 'partner_review': 'optional'}:
                raise ValueError('Upgrade cooperative review policy: independent agent required, partner optional')
        if not checks: raise ValueError('Product gate blocked: configure real product commands')
        for command in checks:
            if not isinstance(command, list) or not command or any(not isinstance(x,str) or not x for x in command):
                raise ValueError('Each product command must be a nonempty string argument array')
            try: result = subprocess.run(command, cwd=project, timeout=600, check=False)
            except subprocess.TimeoutExpired: raise ValueError('Product check timed out')
            if result.returncode: raise ValueError('Product check failed with exit '+str(result.returncode))
    return dict(mode=mode,preset=policy['preset'],configuration='valid',product_checks='passed' if run_checks else 'not_run',
                host_models='requires live smoke test',github_rules='requires remote verification')


# Explicit compatibility surface for callers that still need a V1 snapshot.
install_v1 = install


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project')
    p.add_argument('--master', default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--mode', choices=['SOLO','CO-OP'], default='SOLO')
    p.add_argument('--preset', choices=['balanced','critical'], default='balanced')
    p.add_argument('--preview', action='store_true')
    a = p.parse_args()
    try:
        print(json.dumps(install(Path(a.master), Path(a.project), a.mode, a.preset, a.preview),indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('ERROR: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
