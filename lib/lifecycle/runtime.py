"""Planning and safe application of old generated runtime files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
from typing import Any, Iterable
import uuid


_DEFAULT_ROOTS = (".galaxy/runtime", ".galaxy/cache", ".galaxy/tmp",
                  ".galaxy/telemetry", ".multicontroller/runtime")
_AUDIT_WORDS = ("audit", "evidence", "receipt", "review", "proof")
_GENERATED_EXTENSIONS = {".tmp", ".temp", ".cache", ".pyc", ".log", ".wal", ".shm"}


def _stamp(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return default


def _is_audit(path: Path, protected: set[Path]) -> bool:
    absolute = Path(os.path.abspath(path))
    if absolute in protected:
        return True
    return any(part.casefold() in _AUDIT_WORDS or any(word in part.casefold() for word in _AUDIT_WORDS)
               for part in absolute.parts)


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
        or bool(junction and junction())
    )


def _assert_no_links(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if _is_link_or_reparse(candidate, info):
            raise ValueError("runtime path contains link/reparse point: " + str(candidate))


def _walk_regular_files(root: Path):
    _assert_no_links(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(root_info.st_mode):
        yield root, root_info
        return
    if not stat.S_ISDIR(root_info.st_mode):
        return
    stack = [root]
    while stack:
        directory = stack.pop()
        _assert_no_links(directory)
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name, reverse=True)
        except OSError as exc:
            raise ValueError("cannot safely inspect runtime directory: " + str(directory)) from exc
        for entry in entries:
            path = Path(entry.path)
            info = path.lstat()
            if _is_link_or_reparse(path, info):
                raise ValueError("runtime path contains link/reparse point: " + str(path))
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
            elif stat.S_ISREG(info.st_mode):
                yield path, info


@dataclass(frozen=True)
class RuntimeCandidate:
    path: str
    category: str
    modified_at: float
    retention_elapsed: bool
    protected_audit: bool
    safe: bool
    reasons: tuple[str, ...] = ()
    identity: tuple[int, int, int, int, int] = ()

    @property
    def status(self) -> str:
        return "safe" if self.safe else "protected"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "category": self.category,
                "modified_at": self.modified_at, "retention_elapsed": self.retention_elapsed,
                "protected_audit": self.protected_audit, "safe": self.safe,
                "reasons": list(self.reasons), "identity": list(self.identity)}


@dataclass(frozen=True)
class RuntimeCleanupPlan:
    root: str
    retention_days: float
    candidates: tuple[RuntimeCandidate, ...]
    preview: bool = True

    @property
    def safe_candidates(self) -> tuple[RuntimeCandidate, ...]:
        return tuple(item for item in self.candidates if item.safe)

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.safe_candidates)

    @property
    def fingerprint(self) -> str:
        data = [(item.path, item.modified_at, item.identity) for item in self.safe_candidates]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"root": self.root, "retention_days": self.retention_days,
                "preview": self.preview, "targets": list(self.targets),
                "fingerprint": self.fingerprint,
                "candidates": [item.to_dict() for item in self.candidates]}


def plan_runtime_cleanup(root: str | Path, *, paths: Iterable[str | Path] | None = None,
                         retention_days: float = 3, now: float | None = None,
                         current_audit_paths: Iterable[str | Path] = ()) -> RuntimeCleanupPlan:
    """Plan deletion of old generated runtime files under explicit roots.

    The default roots are intentionally narrow.  Callers can pass ``paths``
    to add specific generated directories/files; arbitrary project files are
    never scanned.
    """
    project = Path(os.path.abspath(root))
    _assert_no_links(project)
    current = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    protected = {Path(os.path.abspath(value)) for value in current_audit_paths}
    roots = [project / relative for relative in _DEFAULT_ROOTS] if paths is None else [Path(value) if Path(value).is_absolute() else project / value for value in paths]
    candidates: list[RuntimeCandidate] = []
    for scan_root in roots:
        scan_root = Path(os.path.abspath(scan_root))
        try:
            scan_root.relative_to(project)
        except ValueError as exc:
            raise ValueError("runtime cleanup root escapes project") from exc
        _assert_no_links(scan_root)
        if not scan_root.exists():
            continue
        for path, info in _walk_regular_files(scan_root):
            modified = info.st_mtime
            protected_audit = _is_audit(path, protected)
            relative = path.relative_to(project).as_posix() if path.is_relative_to(project) else str(path)
            category = scan_root.name
            old = modified <= current - float(retention_days) * 86400
            reasons: list[str] = []
            if not old: reasons.append("retention-not-elapsed")
            if protected_audit: reasons.append("current-audit-evidence")
            # Explicit paths are still conservative: only generated-looking
            # files qualify, unless their root is one of the known generated
            # runtime directories.
            known_root = any(scan_root == project / item for item in _DEFAULT_ROOTS)
            generated = known_root or path.suffix.casefold() in _GENERATED_EXTENSIONS
            if not generated: reasons.append("not-generated-runtime-file")
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
            candidates.append(RuntimeCandidate(
                relative, category, modified, old, protected_audit,
                not reasons, tuple(reasons), identity,
            ))
    unique = {item.path: item for item in candidates}
    return RuntimeCleanupPlan(str(project), float(retention_days),
                              tuple(sorted(unique.values(), key=lambda item: item.path)), True)


runtime_cleanup_plan = plan_runtime_cleanup


def apply_runtime_cleanup(plan: RuntimeCleanupPlan, *, apply: bool = False,
                          targets: Iterable[str] | None = None) -> dict[str, Any]:
    if not apply:
        return {"applied": False, "deleted": [], "targets": list(plan.targets)}
    expected = tuple(plan.targets)
    requested = expected if targets is None else tuple(targets)
    if requested != expected:
        raise ValueError("apply targets must exactly match the validated plan")
    project = Path(os.path.abspath(plan.root))
    _assert_no_links(project)
    deleted: list[str] = []
    for candidate in plan.safe_candidates:
        relative = PurePosixPath(candidate.path)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError("runtime target escapes project")
        target = project.joinpath(*relative.parts)
        _assert_no_links(target)
        try:
            info = target.lstat()
        except OSError as exc:
            raise FileNotFoundError(str(target)) from exc
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
        if not stat.S_ISREG(info.st_mode) or identity != candidate.identity:
            raise RuntimeError(
                f"runtime file changed after preview: {candidate.path}"
            )
        if os.name == 'nt':
            _delete_windows_file(target, candidate.identity)
        else:
            _delete_posix_file(project, relative, candidate.identity)
        deleted.append(candidate.path)
    return {"applied": True, "deleted": deleted, "targets": list(expected)}


def _delete_windows_file(path: Path, expected: tuple[int, int, int, int, int]) -> None:
    """Delete the exact no-delete-share handle whose identity was previewed."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    handle = create(
        str(path), 0x00010000 | 0x80000000, 0x00000001 | 0x00000002,
        None, 3, 0x00200000, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = -1
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev, opened.st_ino, opened.st_size,
            opened.st_mtime_ns, opened.st_mode,
        )
        if identity != expected:
            raise RuntimeError("runtime file changed after preview: " + str(path))

        class FileDispositionInformation(ctypes.Structure):
            _fields_ = [('delete_file', wintypes.BOOLEAN)]

        disposition = FileDispositionInformation(True)
        set_information = kernel.SetFileInformationByHandle
        set_information.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        set_information.restype = wintypes.BOOL
        if not set_information(
            handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _delete_posix_file(
    project: Path, relative: PurePosixPath,
    expected: tuple[int, int, int, int, int],
) -> None:
    """Move into a private anchored directory, then verify and delete that inode."""
    directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    directory_flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    file_flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    descriptors = []
    stage_fd = None
    candidate_fd = None
    stage_name = '.galaxy-cleanup-' + uuid.uuid4().hex
    staged_name = 'candidate'
    moved = False
    try:
        parent_fd = os.open(project, directory_flags)
        descriptors.append(parent_fd)
        for part in relative.parts[:-1]:
            parent_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            descriptors.append(parent_fd)
        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        stage_fd = os.open(stage_name, directory_flags, dir_fd=parent_fd)
        os.rename(
            relative.name, staged_name,
            src_dir_fd=parent_fd, dst_dir_fd=stage_fd,
        )
        moved = True
        _after_posix_stage(parent_fd, stage_fd, relative.name, staged_name)
        candidate_fd = os.open(staged_name, file_flags, dir_fd=stage_fd)
        opened = os.fstat(candidate_fd)
        opened_identity = (
            opened.st_dev, opened.st_ino, opened.st_size,
            opened.st_mtime_ns, opened.st_mode,
        )
        named = os.stat(staged_name, dir_fd=stage_fd, follow_symlinks=False)
        named_identity = (
            named.st_dev, named.st_ino, named.st_size,
            named.st_mtime_ns, named.st_mode,
        )
        actual = opened_identity if opened_identity == named_identity else None

        def delete_staged():
            nonlocal moved
            os.unlink(staged_name, dir_fd=stage_fd)
            moved = False

        def recover_staged():
            nonlocal moved
            recovered = _recover_posix_stage(
                stage_fd, parent_fd, staged_name, relative.name,
            )
            moved = False
            return recovered

        _finish_posix_stage(
            expected, actual, delete=delete_staged, recover=recover_staged,
            label=relative.as_posix(),
        )
    except BaseException:
        if moved and stage_fd is not None:
            try:
                _recover_posix_stage(stage_fd, parent_fd, staged_name, relative.name)
                moved = False
            except OSError:
                pass
        raise
    finally:
        if candidate_fd is not None:
            os.close(candidate_fd)
        if stage_fd is not None:
            os.close(stage_fd)
        if not moved and 'parent_fd' in locals():
            try:
                os.rmdir(stage_name, dir_fd=parent_fd)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _after_posix_stage(
    _parent_fd: int, _stage_fd: int, _original_name: str, _staged_name: str,
) -> None:
    """Test seam after the atomic move; production intentionally does nothing."""


def _recover_posix_stage(
    stage_fd: int, parent_fd: int, staged_name: str, original_name: str,
) -> str:
    candidates = [
        original_name,
        '.' + original_name + '.galaxy-recovered-' + uuid.uuid4().hex,
    ]
    for destination in candidates:
        try:
            os.link(
                staged_name, destination,
                src_dir_fd=stage_fd, dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            continue
        os.unlink(staged_name, dir_fd=stage_fd)
        return destination
    raise FileExistsError('cannot recover staged runtime candidate')


def _finish_posix_stage(
    expected: tuple[int, int, int, int, int],
    actual: tuple[int, int, int, int, int] | None,
    *, delete, recover, label: str,
) -> None:
    if actual != expected:
        recovered = recover()
        raise RuntimeError(
            'runtime file changed after preview: '
            + label + '; preserved as ' + recovered
        )
    delete()
