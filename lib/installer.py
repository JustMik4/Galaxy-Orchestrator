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
    for folder in [master/'template'] + ([master/'coop'] if mode == 'CO-OP' else []):
        for f in folder.rglob('*'):
            safe_path(f)
            if f.is_file(): sources[f.relative_to(folder).as_posix()] = f.read_bytes()
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
