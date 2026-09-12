"""Safely inspect or refresh the authenticated Galaxy declaration hashes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable
import uuid

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


@dataclass(frozen=True)
class _PathIdentity:
    path: Path
    device: int
    inode: int


class _BoundaryGuard:
    """Bind the operation to the original project hierarchy for its lifetime."""

    def __init__(
        self,
        root: Path,
        identities: tuple[_PathIdentity, ...],
        *,
        root_fd: int | None = None,
        windows_handles: tuple[int, ...] = (),
    ) -> None:
        self.root = root
        self.identities = identities
        self.root_fd = root_fd
        self.windows_handles = windows_handles

    @classmethod
    def acquire(cls, root: Path) -> "_BoundaryGuard":
        identities = _capture_boundary_identities(root)
        if os.name == "nt":
            handles = []
            try:
                for identity in identities:
                    handle, inode = _open_windows_directory_guard(identity.path)
                    handles.append(handle)
                    if inode != identity.inode:
                        raise ProjectConfigurationError(
                            "project boundary changed while acquiring guard: "
                            + str(identity.path)
                        )
                guard = cls(root, identities, windows_handles=tuple(handles))
                guard.assert_unchanged()
                return guard
            except BaseException:
                for handle in reversed(handles):
                    _close_windows_handle(handle)
                raise

        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            root_fd = os.open(root, flags)
        except OSError as exc:
            raise ProjectConfigurationError(
                "cannot guard project root: " + str(root)
            ) from exc
        guard = cls(root, identities, root_fd=root_fd)
        try:
            opened = os.fstat(root_fd)
            expected_root = identities[-1]
            if (opened.st_dev, opened.st_ino) != (
                expected_root.device,
                expected_root.inode,
            ):
                raise ProjectConfigurationError(
                    "project root changed while acquiring boundary guard"
                )
            guard.assert_unchanged()
            return guard
        except BaseException:
            guard.close()
            raise

    def assert_unchanged(self) -> None:
        current = _capture_boundary_identities(self.root)
        if current != self.identities:
            raise ProjectConfigurationError(
                "project root or ancestor changed concurrently"
            )
        if self.root_fd is not None:
            opened = os.fstat(self.root_fd)
            expected = self.identities[-1]
            if (opened.st_dev, opened.st_ino) != (expected.device, expected.inode):
                raise ProjectConfigurationError("guarded project root changed")
        elif self.windows_handles:
            opened = tuple(
                _windows_handle_inode(handle) for handle in self.windows_handles
            )
            expected = tuple(identity.inode for identity in self.identities)
            if opened != expected:
                raise ProjectConfigurationError("guarded project boundary changed")

    def lstat_name(self, name: str) -> os.stat_result:
        if self.root_fd is not None:
            return os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
        return (self.root / name).lstat()

    def create_stage(self, name: str, mode: int) -> int:
        descriptor = -1
        try:
            if self.root_fd is not None:
                flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
                flags |= getattr(os, "O_NOFOLLOW", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                descriptor = os.open(name, flags, mode, dir_fd=self.root_fd)
            else:
                descriptor = _open_windows_stage(self.root / name)
            os.fchmod(descriptor, mode)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                try:
                    self.discard_stage(name, descriptor)
                finally:
                    os.close(descriptor)
            raise

    def replace_stage(self, name: str, descriptor: int) -> None:
        if self.root_fd is not None:
            os.replace(
                name,
                _LOCK_PATH,
                src_dir_fd=self.root_fd,
                dst_dir_fd=self.root_fd,
            )
        else:
            _rename_windows_stage(descriptor, self.windows_handles[-1])

    def discard_stage(self, name: str, descriptor: int) -> None:
        """Delete only the inode/handle created for this operation."""
        if self.root_fd is None:
            _delete_windows_stage(descriptor)
            return
        try:
            opened = os.stat(descriptor)
            named = self.lstat_name(name)
        except OSError:
            return
        if (
            stat.S_ISREG(named.st_mode)
            and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
        ):
            try:
                os.unlink(name, dir_fd=self.root_fd)
            except FileNotFoundError:
                pass

    def close(self) -> None:
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None
        for handle in reversed(self.windows_handles):
            _close_windows_handle(handle)
        self.windows_handles = ()

    def __enter__(self) -> "_BoundaryGuard":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


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


def _capture_boundary_identities(root: Path) -> tuple[_PathIdentity, ...]:
    identities = []
    for candidate in (*reversed(root.parents), root):
        info = _lstat(candidate)
        if (
            info is None
            or _is_link_or_reparse(candidate, info)
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise ProjectConfigurationError(
                "project root or ancestor is missing, non-directory, or link/reparse: "
                + str(candidate)
            )
        identities.append(_PathIdentity(candidate, info.st_dev, info.st_ino))
    return tuple(identities)


def _windows_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return ctypes, wintypes, create, close


def _open_windows_directory_guard(path: Path) -> tuple[int, int]:
    ctypes, wintypes, create, close = _windows_api()
    generic_read = 0x80000000
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse = 0x00200000
    handle = create(
        str(path),
        generic_read,
        share_read_write,
        None,
        open_existing,
        backup_semantics | open_reparse,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return int(handle), _windows_handle_inode(int(handle))
    except BaseException:
        close(handle)
        raise


def _windows_handle_inode(handle: int) -> int:
    ctypes, wintypes, _create, _close = _windows_api()

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("access_time", wintypes.FILETIME),
            ("write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    get_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_info.restype = wintypes.BOOL
    info = FileInformation()
    if not get_info(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (info.index_high << 32) | info.index_low


def _open_windows_stage(path: Path) -> int:
    import msvcrt

    ctypes, wintypes, create, close = _windows_api()
    generic_read_write_delete = 0x80000000 | 0x40000000 | 0x00010000
    share_read = 0x00000001
    create_new = 1
    normal = 0x00000080
    handle = create(
        str(path),
        generic_read_write_delete,
        share_read,
        None,
        create_new,
        normal,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


def _set_windows_file_information(
    descriptor: int,
    information_class: int,
    information: object,
    size: int,
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    set_information = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    if not set_information(handle, information_class, information, size):
        raise ctypes.WinError(ctypes.get_last_error())


def _rename_windows_stage(descriptor: int, root_handle: int) -> None:
    """Atomically promote the exact verified staged handle to galaxy.lock."""
    import ctypes
    from ctypes import wintypes

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    destination = _windows_handle_path(root_handle).rstrip("\\/") + "\\" + _LOCK_PATH
    encoded_name = destination.encode("utf-16-le")
    name_offset = FileRenameInformation.file_name.offset
    size = ctypes.sizeof(FileRenameInformation) + len(encoded_name)
    buffer = ctypes.create_string_buffer(size)
    information = ctypes.cast(
        buffer, ctypes.POINTER(FileRenameInformation)
    ).contents
    information.replace_if_exists = 1
    information.root_directory = wintypes.HANDLE()
    information.file_name_length = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(buffer) + name_offset,
        encoded_name,
        len(encoded_name),
    )
    _set_windows_file_information(descriptor, 3, buffer, size)


def _windows_handle_path(handle: int) -> str:
    """Return the stable normalized DOS path represented by a directory handle."""
    import ctypes
    from ctypes import wintypes

    get_path = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFinalPathNameByHandleW
    get_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_path.restype = wintypes.DWORD
    required = get_path(wintypes.HANDLE(handle), None, 0, 0)
    if not required:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_path(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _delete_windows_stage(descriptor: int) -> None:
    """Mark the exact staged handle for deletion without resolving its name."""
    import ctypes

    class FileDispositionInformation(ctypes.Structure):
        _fields_ = [("delete_file", ctypes.c_ubyte)]

    information = FileDispositionInformation(1)
    _set_windows_file_information(
        descriptor,
        4,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )


def _close_windows_handle(handle: int) -> None:
    _ctypes, wintypes, _create, close = _windows_api()
    close(wintypes.HANDLE(handle))


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
    boundary: _BoundaryGuard,
) -> None:
    """Stage deterministic bytes and replace only after the final snapshot guard."""
    boundary.assert_unchanged()
    current = boundary.lstat_name(_LOCK_PATH)
    if _is_link_or_reparse(path, current) or not stat.S_ISREG(current.st_mode):
        raise ProjectConfigurationError("galaxy.lock is not a safe regular file")
    temporary_name = ".galaxy.lock." + uuid.uuid4().hex + ".tmp"
    descriptor = -1
    installed = False
    try:
        descriptor = boundary.create_stage(
            temporary_name, stat.S_IMODE(current.st_mode)
        )
        staged = os.fstat(descriptor)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        try:
            unchanged()
        except OSError as exc:
            raise ProjectConfigurationError(
                "staged galaxy.lock tamper or concurrent filesystem change"
            ) from exc
        boundary.assert_unchanged()
        temporary_info = boundary.lstat_name(temporary_name)
        if (
            _is_link_or_reparse(boundary.root / temporary_name, temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or (staged.st_dev, staged.st_ino)
            != (temporary_info.st_dev, temporary_info.st_ino)
        ):
            raise ProjectConfigurationError("staged galaxy.lock changed concurrently")
        os.lseek(descriptor, 0, os.SEEK_SET)
        staged_bytes = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            staged_bytes += chunk
        if staged_bytes != data:
            raise ProjectConfigurationError("staged galaxy.lock bytes changed concurrently")
        boundary.assert_unchanged()
        boundary.replace_stage(temporary_name, descriptor)
        installed = True
    finally:
        if descriptor >= 0:
            try:
                if not installed:
                    boundary.discard_stage(temporary_name, descriptor)
            finally:
                os.close(descriptor)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
    ).encode("utf-8")


def sync(project_root: Path | str, *, check: bool = False) -> dict[str, object]:
    """Check or atomically refresh exactly the four declaration digests."""
    root = _checked_root(project_root)
    with _BoundaryGuard.acquire(root) as boundary:
        boundary.assert_unchanged()
        snapshot = _capture(root)
        boundary.assert_unchanged()
        values = snapshot.bytes_by_path
        _project, _team, _checks, lock = _validate(snapshot)
        boundary.assert_unchanged()
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
        _atomic_write(
            lock_path,
            output,
            lambda: _assert_unchanged(snapshot),
            boundary,
        )
        result.update(applied=True, status="synced", written=[_LOCK_PATH])
        return result


__all__ = ["sync"]
