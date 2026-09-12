"""Deterministic, privacy-preserving Obsidian projection for Galaxy tasks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
import re
from pathlib import PurePosixPath


class VaultError(ValueError):
    """Invalid vault configuration or unsafe synchronization state."""


_SENSITIVE = {'prompt', 'prompts', 'response', 'responses', 'telemetry', 'secret', 'secrets',
              'token', 'tokens', 'credential', 'credentials', 'env', 'environment', 'account',
              'account_id', 'github_login', 'email'}
_FIELDS = ('task_id', 'title', 'status', 'owner', 'revision', 'source_receipt', 'updated_at',
           'dependencies', 'risk', 'specialist')
_TASK_ID = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$')
_DEFAULT_INCLUDE = ('tasks', 'milestones', 'summaries')
_DEFAULT_EXCLUDE = ('prompts', 'responses', 'telemetry', 'secrets')
_REQUIRED_FIELDS = frozenset(
    {'task_id', 'revision', 'source_receipt', 'status', 'owner', 'updated_at'}
)
_EXCLUDE_ALIASES = {
    **{name: name for name in _SENSITIVE | set(_FIELDS)},
    **{
        name + 's': name
        for name in _SENSITIVE | set(_FIELDS)
        if not name.endswith('s')
    },
    'dependency': 'dependencies',
}


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


def _validate_external_vault_boundary(project, target):
    """Require an external vault to be disjoint from the project tree."""
    root = Path(os.path.abspath(project))
    try:
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise VaultError('external vault path must be disjoint from project')
    try:
        root.relative_to(target)
    except ValueError:
        pass
    else:
        raise VaultError('external vault path must be disjoint from project')


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
        _validate_external_vault_boundary(project, target)
    else:
        target = validate_internal_vault_path(project, path)
    mode = config.get('mode', 'projection')
    if mode not in ('projection', 'project-owned'):
        raise VaultError('invalid vault mode')
    return True, target, mode


def _policy(config):
    include = config.get('include', _DEFAULT_INCLUDE)
    exclude = config.get('exclude', _DEFAULT_EXCLUDE)
    if (
        not isinstance(include, (list, tuple))
        or any(not isinstance(item, str) or not item.strip() for item in include)
    ):
        raise VaultError('vault include must be an array of names')
    normalized_exclude = _normalize_exclude(exclude)
    return frozenset(item.casefold() for item in include), normalized_exclude


def _normalize_exclude(exclude):
    if (
        not isinstance(exclude, (list, tuple, set, frozenset))
        or any(not isinstance(item, str) or not item.strip() for item in exclude)
    ):
        raise VaultError('vault exclude must be an array of names')
    try:
        normalized_exclude = frozenset(
            _EXCLUDE_ALIASES[item.casefold()] for item in exclude
        )
    except KeyError as exc:
        raise VaultError('unsupported vault exclude field: ' + str(exc.args[0])) from exc
    if _REQUIRED_FIELDS.intersection(normalized_exclude):
        raise VaultError('required vault fields cannot be excluded')
    return normalized_exclude


def _vault_path(vault, relative):
    pure = PurePosixPath(str(relative).replace('\\', '/'))
    if pure.is_absolute() or not pure.parts or '..' in pure.parts:
        raise VaultError('vault descendant path is unsafe: ' + str(relative))
    target = vault.joinpath(*pure.parts)
    _reject_links(target)
    return target


def _safe_mkdir(path):
    _reject_links(path)
    path.mkdir(parents=True, exist_ok=True)
    _reject_links(path)


def _validate_tree(path):
    """Reject every link/reparse entry before a whole-tree operation."""
    _reject_links(path)
    if not path.exists():
        return
    stack = [path]
    while stack:
        current = stack.pop()
        _reject_links(current)
        if not current.is_dir():
            continue
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise VaultError('cannot inspect vault tree: ' + str(exc)) from exc
        for entry in entries:
            child = Path(entry.path)
            _reject_links(child)
            if entry.is_dir(follow_symlinks=False):
                stack.append(child)


def _scalar(value):
    if isinstance(value, bool): return 'true' if value else 'false'
    if value is None: return 'null'
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def render_task_note(snapshot, *, exclude=()):
    """Render only an allowlisted task snapshot; never include sensitive payloads."""
    if not isinstance(snapshot, dict):
        raise VaultError('task snapshot must be an object')
    task_id = snapshot.get('task_id', snapshot.get('id'))
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise VaultError('task_id must be a safe non-empty identifier')
    revision = snapshot.get('revision')
    receipt = snapshot.get('source_receipt')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise VaultError('revision must be a non-negative integer')
    if not isinstance(receipt, str) or not receipt.strip():
        raise VaultError('source_receipt must be a non-empty string')
    excluded = _normalize_exclude(exclude)
    fields = {}
    for key in _FIELDS:
        if key.casefold() in excluded:
            continue
        if key in snapshot and snapshot[key] is not None:
            value = snapshot[key]
            if key == 'dependencies':
                if not isinstance(value, (list, tuple)) or any(
                    not isinstance(item, str) or not _TASK_ID.fullmatch(item)
                    for item in value
                ):
                    raise VaultError('dependencies must contain only scalar task identifiers')
                value = sorted(set(value))
            elif isinstance(value, (dict, list, tuple, set)):
                # Do not serialize arbitrary nested payloads: they can conceal
                # prompts, credentials, telemetry or account identifiers.
                continue
            fields[key] = value
    fields['task_id'] = task_id
    fields['galaxy_schema'] = 1
    fields.setdefault('updated_at', '')
    fields.setdefault('status', '')
    fields.setdefault('owner', '')
    lines = ['---']
    for key in sorted(fields):
        lines.append(f'{key}: {_scalar(fields[key])}')
    lines += ['---', '', '# ' + str(fields.get('title', task_id)), '',
              'Esta nota é uma projeção Galaxy; o coordenador serializado é a autoridade.', '']
    return '\n'.join(lines)


def _desired(snapshot_list, config):
    include, exclude = _policy(config)
    files = {'.obsidian/app.json': b'{}\n'}
    if 'tasks' not in include:
        return files
    seen = set()
    for snapshot in snapshot_list:
        task_id = snapshot.get('task_id', snapshot.get('id'))
        if task_id in seen:
            raise VaultError('duplicate task snapshot: ' + str(task_id))
        seen.add(task_id)
        note = render_task_note(snapshot, exclude=exclude)
        files['tasks/' + task_id + '.md'] = note.encode('utf-8')
    return files


def _authority_state(value, label):
    if not isinstance(value, dict) or set(value) != {
        'task_id', 'revision', 'source_receipt'
    }:
        raise VaultError(label + ' must contain exact task_id, revision, and source_receipt fields')
    task_id = value.get('task_id')
    revision = value.get('revision')
    receipt = value.get('source_receipt')
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise VaultError(label + ' task_id must be a safe identifier')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise VaultError(label + ' revision must be a non-negative integer')
    if not isinstance(receipt, str) or not receipt.strip():
        raise VaultError(label + ' source_receipt must be non-empty')
    return task_id, revision, receipt


def _snapshot_input(value, *, require_authority):
    if isinstance(value, (list, tuple)):
        if require_authority:
            raise VaultError('sync requires a versioned authoritative snapshot envelope')
        return list(value), None
    if not isinstance(value, dict) or set(value) != {
        'schema_version', 'tasks', 'authority'
    }:
        raise VaultError('snapshot envelope must contain schema_version, tasks, and authority')
    if value.get('schema_version') != 1:
        raise VaultError('unsupported authoritative snapshot schema_version')
    tasks = value.get('tasks')
    authority = value.get('authority')
    if not isinstance(tasks, list) or not isinstance(authority, list):
        raise VaultError('authoritative snapshot tasks and authority must be arrays')
    authority_by_id = {}
    for item in authority:
        state = _authority_state(item, 'authoritative receipt')
        if state[0] in authority_by_id:
            raise VaultError('duplicate authoritative receipt: ' + state[0])
        authority_by_id[state[0]] = state
    task_ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise VaultError('task snapshot must be an object')
        task_id = task.get('task_id', task.get('id'))
        state = _authority_state({
            'task_id': task_id,
            'revision': task.get('revision'),
            'source_receipt': task.get('source_receipt'),
        }, 'task snapshot authority')
        if task_id in task_ids:
            raise VaultError('duplicate task snapshot: ' + str(task_id))
        task_ids.add(task_id)
        if authority_by_id.get(task_id) != state:
            raise VaultError('task snapshot does not match authoritative receipt: ' + str(task_id))
    if task_ids != set(authority_by_id):
        raise VaultError('authoritative receipt set must exactly match task snapshots')
    return tasks, authority_by_id


def _note_state(data):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return None
    text = text.replace('\r\n', '\n')
    if '\r' in text:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end < 0:
        return None
    values = {}
    for line in text[4:end].splitlines():
        key, separator, raw = line.partition(':')
        if not separator:
            return None
        try:
            values[key.strip()] = json.loads(raw.strip())
        except json.JSONDecodeError:
            return None
    revision = values.get('revision')
    receipt = values.get('source_receipt')
    task_id = values.get('task_id')
    if (
        isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
        or not isinstance(receipt, str) or not receipt
        or not isinstance(task_id, str)
    ):
        return None
    return task_id, revision, receipt


def _validate_monotonic(vault, files):
    for relative, incoming in files.items():
        if not relative.startswith('tasks/'):
            continue
        target = _vault_path(vault, relative)
        if not target.is_file():
            continue
        _reject_links(target)
        current = _note_state(target.read_bytes())
        desired = _note_state(incoming)
        if current is None:
            raise VaultError('existing vault task frontmatter is malformed: ' + relative)
        if desired is None:
            raise VaultError('generated vault task frontmatter is malformed: ' + relative)
        current_id, current_revision, current_receipt = current
        desired_id, desired_revision, desired_receipt = desired
        if current_id != desired_id:
            raise VaultError('task identity mismatch in existing vault note')
        if desired_revision < current_revision:
            raise VaultError('vault revision downgrade is forbidden')
        if desired_revision == current_revision and desired_receipt != current_receipt:
            raise VaultError('source_receipt mismatch at equal revision')


def status(project, config, snapshots=()):
    enabled, vault, mode = _config(project, config)
    if not enabled:
        return {'enabled': False, 'path': None, 'drift': [], 'expected': 0}
    snapshot_list, _authority = _snapshot_input(snapshots, require_authority=False)
    expected = _desired(snapshot_list, config)
    _validate_tree(vault)
    _validate_monotonic(vault, expected)
    drift = []
    for relative, data in expected.items():
        path = _vault_path(vault, relative)
        if not path.is_file() or path.read_bytes() != data:
            drift.append(relative)
    tasks = _vault_path(vault, 'tasks')
    if tasks.is_dir():
        for path in tasks.glob('*.md'):
            _reject_links(path)
            relative = path.relative_to(vault).as_posix()
            if relative not in expected and '.conflict-' not in path.name:
                drift.append(relative)
    return {'enabled': True, 'path': str(vault), 'mode': mode,
            'drift': sorted(drift), 'expected': len(expected)}


def _backup_path(vault, drift):
    fingerprint = hashlib.sha256()
    for relative in sorted(drift):
        fingerprint.update(relative.encode('utf-8'))
        path = _vault_path(vault, relative)
        fingerprint.update(path.read_bytes() if path.is_file() else b'<missing>')
    backup = vault.parent / (vault.name + '.backup-' + fingerprint.hexdigest()[:12])
    _reject_links(backup)
    return backup


def sync(project, config, snapshots, *, check=False, force=False):
    enabled, vault, mode = _config(project, config)
    if not enabled:
        return {'enabled': False, 'written': [], 'drift': [], 'backup': None}
    snapshot_list, _authority = _snapshot_input(snapshots, require_authority=True)
    files = _desired(snapshot_list, config)
    _validate_tree(vault)
    _validate_monotonic(vault, files)
    drift = []
    for relative, data in files.items():
        target = _vault_path(vault, relative)
        if not target.is_file() or target.read_bytes() != data:
            drift.append(relative)
    stale = []
    tasks = _vault_path(vault, 'tasks')
    if tasks.is_dir():
        for path in tasks.glob('*.md'):
            _reject_links(path)
            relative = path.relative_to(vault).as_posix()
            if '.conflict-' not in path.name and _note_state(path.read_bytes()) is None:
                raise VaultError('existing vault task frontmatter is malformed: ' + relative)
            if relative not in files and '.conflict-' not in path.name:
                stale.append(relative)
        stale.sort()
        drift.extend(stale)
    if check:
        return {'enabled': True, 'path': str(vault), 'mode': mode, 'written': [],
                'drift': sorted(drift), 'conflicts': [], 'backup': None, 'check': True}
    if mode == 'project-owned':
        _safe_mkdir(vault)
        written = []
        conflicts = []
        for relative, data in files.items():
            target = _vault_path(vault, relative)
            if not target.exists():
                _safe_mkdir(target.parent)
                _reject_links(target)
                target.write_bytes(data)
                written.append(relative)
            elif target.is_file() and target.read_bytes() != data and relative.startswith('tasks/'):
                digest = hashlib.sha256(data).hexdigest()[:12]
                conflict = target.with_name(target.stem + '.conflict-' + digest + target.suffix)
                _reject_links(conflict)
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
            _validate_tree(vault)
            _reject_links(backup)
            shutil.copytree(vault, backup)
            _validate_tree(backup)
    _safe_mkdir(vault)
    for relative, data in files.items():
        target = _vault_path(vault, relative)
        _safe_mkdir(target.parent)
        _reject_links(target)
        target.write_bytes(data)
    for relative in stale:
        target = _vault_path(vault, relative)
        target.unlink()
    return {'enabled': True, 'path': str(vault), 'mode': mode,
            'written': sorted(drift), 'drift': sorted(drift), 'conflicts': [],
            'backup': str(backup) if backup else None}
