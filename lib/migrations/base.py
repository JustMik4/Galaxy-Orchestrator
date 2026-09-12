"""Shared, conservative primitives for versioned Galaxy project migrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid
from typing import Any, Callable, Mapping


class MigrationError(ValueError):
    """Base error for migrations that cannot safely continue."""


class MigrationDetectionError(MigrationError):
    """The project is not a complete supported source version."""


class MigrationConflictError(MigrationError):
    """User changes or an occupied destination require reconciliation."""

    def __init__(self, migration_plan: "MigrationPlan") -> None:
        self.plan = migration_plan
        details = ", ".join(item["path"] for item in migration_plan.conflicts)
        super().__init__("migration-conflict: " + (details or "unresolved conflict"))


Validator = Callable[[Path], Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def normalize_relative(value: str) -> str:
    value = value.replace("\\", "/")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or value.startswith("/"):
        raise MigrationDetectionError("unsafe manifest path: " + value)
    return "/".join(path.parts)


def checked_root(value: os.PathLike[str] | str, *, must_exist: bool = True) -> Path:
    path = Path(value).resolve(strict=must_exist)
    if must_exist and not path.is_dir():
        raise MigrationError("expected directory: " + str(path))
    _reject_links(path)
    return path


def checked_project_path(root: Path, relative: str) -> Path:
    relative = normalize_relative(relative)
    target = root.joinpath(*relative.split("/"))
    resolved_parent = target.parent.resolve(strict=False)
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise MigrationError("path escapes project: " + relative) from exc
    _reject_links(root, target)
    return target


def _reject_links(root: Path, target: Path | None = None) -> None:
    target = target or root
    current = root
    candidates = [root]
    try:
        relative = target.relative_to(root)
    except ValueError:
        relative = Path()
    for part in relative.parts:
        current = current / part
        candidates.append(current)
    for candidate in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        info = candidate.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if candidate.is_symlink() or (reparse and attributes & reparse):
            raise MigrationError("link/reparse point is not a migration target: " + str(candidate))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_git(project: Path, arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run Git without a shell; all callers pass a literal argument vector."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=project,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise MigrationError("git failed: " + message)
    return result


def git_inventory(project: Path) -> dict[str, Any]:
    probe = run_git(project, ["rev-parse", "--show-toplevel"], check=False)
    if probe.returncode:
        return {"repository": False, "tracked": [], "index_path": None, "index_exists": False}
    top = Path(probe.stdout.decode("utf-8", errors="surrogateescape").strip()).resolve()
    if top != project:
        raise MigrationError("migration project must be the Git worktree root")
    listing = run_git(project, ["ls-files", "-z"]).stdout
    tracked = sorted(
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in listing.split(b"\0") if item
    )
    raw_index = run_git(project, ["rev-parse", "--git-path", "index"]).stdout
    index = Path(raw_index.decode("utf-8", errors="surrogateescape").strip())
    if not index.is_absolute():
        index = (project / index).resolve()
    return {
        "repository": True,
        "tracked": tracked,
        "index_path": str(index),
        "index_exists": index.is_file(),
    }


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    from_version: str
    to_version: str
    status: str
    project_owned: tuple[str, ...] = ()
    managed_unchanged: tuple[str, ...] = ()
    managed_user_modified: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    untrack: tuple[str, ...] = ()
    generated: tuple[str, ...] = ()
    vault_inventory: tuple[str, ...] = ()
    transformations: tuple[dict[str, str], ...] = ()
    conflicts: tuple[dict[str, str], ...] = ()
    _writes: Mapping[str, bytes] = field(default_factory=dict, repr=False, compare=False)
    _deletes: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _tracked: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "already_migrated"} and not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "status": self.status,
            "ready": self.ready,
            "project_owned": list(self.project_owned),
            "managed_unchanged": list(self.managed_unchanged),
            "managed_user_modified": list(self.managed_user_modified),
            "preserved": list(self.preserved),
            "untrack": list(self.untrack),
            "generated": list(self.generated),
            "vault_inventory": list(self.vault_inventory),
            "transformations": list(self.transformations),
            "conflicts": list(self.conflicts),
        }


def create_backup(master: Path, project: Path, migration_plan: MigrationPlan) -> tuple[Path, dict[str, Any]]:
    backup = master / "local" / "migrations" / uuid.uuid4().hex
    backup.mkdir(parents=True, exist_ok=False)
    touched = sorted(set(migration_plan._writes) | set(migration_plan._deletes))
    originals: dict[str, dict[str, Any]] = {}
    for relative in touched:
        source = checked_project_path(project, relative)
        if source.exists():
            if not source.is_file():
                raise MigrationError("expected a regular file: " + relative)
            data = source.read_bytes()
            stored = backup / "files" / Path(relative)
            atomic_write(stored, data)
            originals[relative] = {
                "existed": True,
                "sha256": digest(data),
                "backup": stored.relative_to(backup).as_posix(),
            }
        else:
            originals[relative] = {"existed": False, "sha256": None, "backup": None}

    git = git_inventory(project)
    index_backup = None
    if git["repository"] and git["index_exists"]:
        index_path = Path(git["index_path"])
        index_data = index_path.read_bytes()
        index_backup = "git/index"
        atomic_write(backup / index_backup, index_data)
        git["index_sha256"] = digest(index_data)
    else:
        git["index_sha256"] = None
    git["index_backup"] = index_backup
    state = {
        "schema_version": 1,
        "migration_id": migration_plan.migration_id,
        "project": str(project),
        "from_version": migration_plan.from_version,
        "to_version": migration_plan.to_version,
        "created_at": utc_now(),
        "status": "in_progress",
        "old_file_hashes": {
            name: metadata["sha256"] for name, metadata in originals.items()
        },
        "originals": originals,
        "tracking": git,
        "planned_transformations": list(migration_plan.transformations),
    }
    atomic_write(backup / "backup.json", json_bytes(state))
    return backup, state


def extend_backup(
    backup: Path,
    project: Path,
    state: dict[str, Any],
    relatives: list[str] | tuple[str, ...],
) -> None:
    """Capture newly discovered targets before a later migration phase mutates them."""
    originals = state.setdefault("originals", {})
    old_hashes = state.setdefault("old_file_hashes", {})
    for relative in sorted(set(relatives)):
        relative = normalize_relative(relative)
        if relative in originals:
            continue
        source = checked_project_path(project, relative)
        if source.exists():
            if not source.is_file():
                raise MigrationError("expected a regular file: " + relative)
            data = source.read_bytes()
            stored = backup / "files" / Path(relative)
            atomic_write(stored, data)
            metadata = {
                "existed": True,
                "sha256": digest(data),
                "backup": stored.relative_to(backup).as_posix(),
            }
        else:
            metadata = {"existed": False, "sha256": None, "backup": None}
        originals[relative] = metadata
        old_hashes[relative] = metadata["sha256"]
    atomic_write(backup / "backup.json", json_bytes(state))


def restore_backup(backup: Path, project: Path, state: Mapping[str, Any]) -> None:
    originals = state.get("originals", {})
    for relative, metadata in originals.items():
        target = checked_project_path(project, relative)
        if metadata["existed"]:
            data = (backup / metadata["backup"]).read_bytes()
            if digest(data) != metadata["sha256"]:
                raise MigrationError("backup hash mismatch: " + relative)
            atomic_write(target, data)
        elif target.exists():
            if not target.is_file():
                raise MigrationError("refusing to remove non-file during rollback: " + relative)
            target.unlink()

    tracking = state.get("tracking", {})
    index_value = tracking.get("index_path")
    if tracking.get("repository") and index_value:
        index = Path(index_value)
        if tracking.get("index_exists"):
            data = (backup / tracking["index_backup"]).read_bytes()
            if digest(data) != tracking.get("index_sha256"):
                raise MigrationError("Git index backup hash mismatch")
            atomic_write(index, data)
        elif index.exists():
            index.unlink()

    directories = sorted(
        {checked_project_path(project, name).parent for name in originals},
        key=lambda path: len(path.parts), reverse=True,
    )
    for directory in directories:
        while directory != project and directory.exists():
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent


def normalize_check_result(value: Any, label: str) -> dict[str, Any]:
    if value is False:
        raise MigrationError(label + " failed")
    if value is None or value is True:
        return {"status": "passed"}
    if isinstance(value, Mapping):
        result = dict(value)
        status = str(result.get("status", "passed")).lower()
        if status in {"fail", "failed", "error", "blocked"}:
            raise MigrationError(label + " failed")
        result.setdefault("status", "passed")
        return result
    exit_code = getattr(value, "exit_code", None)
    if isinstance(exit_code, int):
        if exit_code:
            raise MigrationError(label + " failed")
        status = getattr(value, "status", "passed")
        return {"status": str(getattr(status, "value", status)).lower()}
    return {"status": "passed", "result": str(value)}


def write_receipt(backup: Path, receipt: Mapping[str, Any]) -> Path:
    path = backup / "receipt.json"
    atomic_write(path, json_bytes(receipt))
    return path


def copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-copy migration input so semantic edits never mutate caller data."""
    return json.loads(json.dumps(value))
