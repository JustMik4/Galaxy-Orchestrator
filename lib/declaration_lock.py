"""Safely inspect or refresh the authenticated Galaxy declaration hashes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Callable

from .project import (
    LOCKED_DECLARATION_PATHS,
    ProjectConfigurationError,
    _mapping_bytes,
    _parse_checks,
    _parse_lock,
    _parse_project_config,
    _parse_team,
)


_LOCK_PATH = "galaxy.lock"
_SNAPSHOT_PATHS = (*LOCKED_DECLARATION_PATHS, _LOCK_PATH)


@dataclass(frozen=True)
class _Snapshot:
    root: Path
    contents: tuple[tuple[str, bytes], ...]

    @property
    def bytes_by_path(self) -> dict[str, bytes]:
        return dict(self.contents)


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(reparse and getattr(info, "st_file_attributes", 0) & reparse)
        or bool(junction and junction())
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _checked_root(value: Path | str) -> Path:
    """Return an absolute lexical root without resolving away unsafe links."""
    root = Path(os.path.abspath(os.fspath(value)))
    candidates = [*reversed(root.parents), root]
    for candidate in candidates:
        info = _lstat(candidate)
        if info is not None and _is_link_or_reparse(candidate, info):
            raise ProjectConfigurationError(
                "link/reparse point is not a project boundary: " + str(candidate)
            )
    info = _lstat(root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise ProjectConfigurationError("project root is not a directory: " + str(root))
    return root


def _declaration_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ProjectConfigurationError("unsafe project declaration path: " + relative)
    target = root.joinpath(*pure.parts)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProjectConfigurationError(
            "project declaration escapes project: " + relative
        ) from exc
    current = root
    for part in pure.parts:
        current /= part
        info = _lstat(current)
        if info is not None and _is_link_or_reparse(current, info):
            raise ProjectConfigurationError(
                "link/reparse point is not a declaration path: " + relative
            )
    return target


def _read_regular_file(root: Path, relative: str) -> bytes:
    path = _declaration_path(root, relative)
    before = _lstat(path)
    if before is None:
        raise ProjectConfigurationError("missing project declaration: " + relative)
    if not stat.S_ISREG(before.st_mode):
        raise ProjectConfigurationError(
            "project declaration is not a regular file: " + relative
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ProjectConfigurationError(
            f"cannot read project declaration {relative}: {exc}"
        ) from exc
    after = _lstat(path)
    if (
        after is None
        or _is_link_or_reparse(path, after)
        or not stat.S_ISREG(after.st_mode)
        or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
    ):
        raise ProjectConfigurationError(
            "project declaration changed concurrently: " + relative
        )
    return data


def _capture(root: Path) -> _Snapshot:
    return _Snapshot(
        root,
        tuple(
            (relative, _read_regular_file(root, relative))
            for relative in _SNAPSHOT_PATHS
        ),
    )


def _validate(snapshot: _Snapshot):
    values = snapshot.bytes_by_path
    paths = {
        relative: _declaration_path(snapshot.root, relative)
        for relative in _SNAPSHOT_PATHS
    }
    try:
        values["AGENTS.md"].decode("utf-8")
    except UnicodeError as exc:
        raise ProjectConfigurationError("AGENTS.md must be UTF-8 text") from exc
    project = _parse_project_config(
        _mapping_bytes(
            values[".galaxy/project.yml"], paths[".galaxy/project.yml"]
        )
    )
    team = _parse_team(
        _mapping_bytes(values[".galaxy/team.yml"], paths[".galaxy/team.yml"])
    )
    checks = _parse_checks(
        _mapping_bytes(
            values[".galaxy/checks.json"], paths[".galaxy/checks.json"]
        )
    )
    lock = _parse_lock(_mapping_bytes(values[_LOCK_PATH], paths[_LOCK_PATH]))
    if project.schema_version != lock.project_schema:
        raise ProjectConfigurationError("project schema does not match galaxy.lock")
    if (
        project.specialist_packs != lock.specialist_packs
        or project.specialist_names != lock.specialist_names
    ):
        raise ProjectConfigurationError("selected specialists do not match galaxy.lock")
    return project, team, checks, lock


def _assert_unchanged(snapshot: _Snapshot) -> None:
    for relative, expected in snapshot.contents:
        try:
            actual = _read_regular_file(snapshot.root, relative)
        except ProjectConfigurationError as exc:
            raise ProjectConfigurationError(
                "project declaration changed concurrently: " + relative
            ) from exc
        if actual != expected:
            raise ProjectConfigurationError(
                "project declaration changed concurrently: " + relative
            )


def _atomic_write(
    path: Path,
    data: bytes,
    unchanged: Callable[[], None],
) -> None:
    """Stage deterministic bytes and replace only after the final snapshot guard."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".galaxy.lock.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        current = path.lstat()
        staged = os.fstat(descriptor)
        os.chmod(temporary, stat.S_IMODE(current.st_mode))
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        unchanged()
        temporary_info = temporary.lstat()
        if (
            _is_link_or_reparse(temporary, temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or (staged.st_dev, staged.st_ino)
            != (temporary_info.st_dev, temporary_info.st_ino)
        ):
            raise ProjectConfigurationError("staged galaxy.lock changed concurrently")
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        info = _lstat(temporary)
        if info is not None:
            if _is_link_or_reparse(temporary, info) or stat.S_ISREG(info.st_mode):
                temporary.unlink()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
    ).encode("utf-8")


def sync(project_root: Path | str, *, check: bool = False) -> dict[str, object]:
    """Check or atomically refresh exactly the four declaration digests."""
    root = _checked_root(project_root)
    snapshot = _capture(root)
    values = snapshot.bytes_by_path
    _project, _team, _checks, lock = _validate(snapshot)
    hashes = {
        relative: hashlib.sha256(values[relative]).hexdigest()
        for relative in LOCKED_DECLARATION_PATHS
    }
    stale = sorted(
        relative
        for relative in LOCKED_DECLARATION_PATHS
        if hashes[relative] != lock.declaration_hashes[relative]
    )
    result: dict[str, object] = {
        "applied": False,
        "check": check,
        "declarations": hashes,
        "stale": stale,
        "status": "stale" if stale else "current",
        "written": [],
    }
    if check or not stale:
        return result

    updated = dict(lock.raw)
    updated["declarations"] = hashes
    output = _json_bytes(updated)
    lock_path = _declaration_path(root, _LOCK_PATH)
    _atomic_write(lock_path, output, lambda: _assert_unchanged(snapshot))
    result.update(applied=True, status="synced", written=[_LOCK_PATH])
    return result


__all__ = ["sync"]
