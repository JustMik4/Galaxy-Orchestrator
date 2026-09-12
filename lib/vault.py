"""Deterministic, privacy-preserving Obsidian projection for Galaxy tasks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
import re


class VaultError(ValueError):
    """Invalid vault configuration or unsafe synchronization state."""


_SENSITIVE = {'prompt', 'prompts', 'response', 'responses', 'telemetry', 'secret', 'secrets',
              'token', 'tokens', 'credential', 'credentials', 'env', 'environment', 'account',
              'account_id', 'github_login', 'email'}
_FIELDS = ('task_id', 'title', 'status', 'owner', 'revision', 'source_receipt', 'updated_at',
           'dependencies', 'risk', 'specialist')
_TASK_ID = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$')


def _reject_links(path):
    current = path
    while True:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise VaultError('vault path cannot contain symlink/reparse point: ' + str(current))
        if current.parent == current:
            break
        current = current.parent


def validate_internal_vault_path(project, configured):
    """Return a safe vault path when *configured* is a strict project-relative path."""
    root = Path(os.path.abspath(project))
    value = Path(configured)
    if value.is_absolute() or not str(configured).strip() or any(part == '..' for part in value.parts):
        raise VaultError('internal vault path must be project-relative and contain no traversal')
    target = Path(os.path.abspath(root / value))
    try:
        target.relative_to(root)
    except ValueError:
        raise VaultError('vault path escapes project')
    _reject_links(root)
    _reject_links(target)
    return target


def _operator_vault_override(project):
    """Read only the explicit operator-local external-vault override."""
    path = Path(os.path.abspath(project)) / '.galaxy' / 'local' / 'operator.toml'
    _reject_links(path)
    if not path.is_file():
        return {}
    try:
        decoded = tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise VaultError('invalid operator-local vault configuration: ' + str(exc)) from exc
    vault = decoded.get('vault', {})
    if not isinstance(vault, dict) or set(vault).difference({'path', 'external'}):
        raise VaultError('operator-local vault override accepts only path and external')
    if not vault:
        return {}
    if vault.get('external') is not True or not isinstance(vault.get('path'), str):
        raise VaultError('operator-local vault path requires external = true and an absolute path')
    if not Path(vault['path']).is_absolute():
        raise VaultError('operator-local external vault path must be absolute')
    return {'path': vault['path'], 'external': True}


def _config(project, config):
    config = dict(config or {})
    if not config.get('enabled', False):
        return False, None, config.get('mode', 'projection')
    config.update(_operator_vault_override(project))
    path = config.get('path', '.galaxy/vault')
    if config.get('external', False) or Path(path).is_absolute():
        target = Path(os.path.abspath(Path(path).expanduser()))
        _reject_links(target)
    else:
        target = validate_internal_vault_path(project, path)
    mode = config.get('mode', 'projection')
    if mode not in ('projection', 'project-owned'):
        raise VaultError('invalid vault mode')
    return True, target, mode


def _scalar(value):
    if isinstance(value, bool): return 'true' if value else 'false'
    if value is None: return 'null'
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def render_task_note(snapshot):
    """Render only an allowlisted task snapshot; never include sensitive payloads."""
    if not isinstance(snapshot, dict):
        raise VaultError('task snapshot must be an object')
    task_id = snapshot.get('task_id', snapshot.get('id'))
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise VaultError('task_id must be a safe non-empty identifier')
    fields = {}
    for key in _FIELDS:
        if key in snapshot and snapshot[key] is not None:
            value = snapshot[key]
            if key == 'dependencies':
                value = sorted(str(item) for item in value) if isinstance(value, (list, tuple)) else []
            elif isinstance(value, (dict, list, tuple, set)):
                # Do not serialize arbitrary nested payloads: they can conceal
                # prompts, credentials, telemetry or account identifiers.
                continue
            fields[key] = value
    fields['task_id'] = task_id
    fields['galaxy_schema'] = 1
    fields.setdefault('revision', 0)
    fields.setdefault('source_receipt', '')
    fields.setdefault('updated_at', '')
    fields.setdefault('status', '')
    fields.setdefault('owner', '')
    lines = ['---']
    for key in sorted(fields):
        lines.append(f'{key}: {_scalar(fields[key])}')
    lines += ['---', '', '# ' + str(fields.get('title', task_id)), '',
              'Esta nota é uma projeção Galaxy; o coordenador serializado é a autoridade.', '']
    return '\n'.join(lines)


def _desired(snapshot_list):
    files = {'.obsidian/app.json': b'{}\n'}
    for snapshot in snapshot_list:
        note = render_task_note(snapshot)
        task_id = snapshot.get('task_id', snapshot.get('id'))
        files['tasks/' + task_id + '.md'] = note.encode('utf-8')
    return files


def status(project, config, snapshots=()):
    enabled, vault, mode = _config(project, config)
    if not enabled:
        return {'enabled': False, 'path': None, 'drift': [], 'expected': 0}
    expected = _desired(snapshots)
    drift = []
    for relative, data in expected.items():
        path = vault / relative
        if not path.is_file() or path.read_bytes() != data:
            drift.append(relative)
    tasks = vault / 'tasks'
    if tasks.is_dir():
        for path in tasks.glob('*.md'):
            relative = path.relative_to(vault).as_posix()
            if relative not in expected and '.conflict-' not in path.name:
                drift.append(relative)
    return {'enabled': True, 'path': str(vault), 'mode': mode,
            'drift': sorted(drift), 'expected': len(expected)}


def _backup_path(vault, drift):
    fingerprint = hashlib.sha256()
    for relative in sorted(drift):
        fingerprint.update(relative.encode('utf-8'))
        path = vault / relative
        fingerprint.update(path.read_bytes() if path.is_file() else b'<missing>')
    return vault.parent / (vault.name + '.backup-' + fingerprint.hexdigest()[:12])


def sync(project, config, snapshots, *, check=False, force=False):
    enabled, vault, mode = _config(project, config)
    if not enabled:
        return {'enabled': False, 'written': [], 'drift': [], 'backup': None}
    files = _desired(snapshots)
    drift = [relative for relative, data in files.items() if not (vault / relative).is_file() or (vault / relative).read_bytes() != data]
    stale = []
    tasks = vault / 'tasks'
    if tasks.is_dir():
        stale = sorted(path.relative_to(vault).as_posix() for path in tasks.glob('*.md')
                       if path.relative_to(vault).as_posix() not in files
                       and '.conflict-' not in path.name)
        drift.extend(stale)
    if check:
        return {'enabled': True, 'path': str(vault), 'mode': mode, 'written': [],
                'drift': sorted(drift), 'conflicts': [], 'backup': None, 'check': True}
    if mode == 'project-owned':
        vault.mkdir(parents=True, exist_ok=True)
        written = []
        conflicts = []
        for relative, data in files.items():
            target = vault / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                written.append(relative)
            elif target.is_file() and target.read_bytes() != data and relative.startswith('tasks/'):
                digest = hashlib.sha256(data).hexdigest()[:12]
                conflict = target.with_name(target.stem + '.conflict-' + digest + target.suffix)
                conflict_relative = conflict.relative_to(vault).as_posix()
                if not conflict.exists():
                    conflict.write_bytes(data)
                    written.append(conflict_relative)
                elif not conflict.is_file() or conflict.read_bytes() != data:
                    raise VaultError('vault conflict artifact has unexpected content: ' + conflict_relative)
                conflicts.append(conflict_relative)
        return {'enabled': True, 'path': str(vault), 'mode': mode,
                'written': sorted(written), 'drift': sorted(drift),
                'conflicts': sorted(conflicts), 'backup': None}
    if drift and not force and vault.exists():
        raise VaultError('vault drift detected; use force only after reviewing: ' + ', '.join(sorted(drift)))
    backup = None
    if drift and force and vault.exists():
        backup = _backup_path(vault, drift)
        if not backup.exists():
            shutil.copytree(vault, backup)
    vault.mkdir(parents=True, exist_ok=True)
    for relative, data in files.items():
        target = vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for relative in stale:
        (vault / relative).unlink()
    return {'enabled': True, 'path': str(vault), 'mode': mode,
            'written': sorted(drift), 'drift': sorted(drift), 'conflicts': [],
            'backup': str(backup) if backup else None}
