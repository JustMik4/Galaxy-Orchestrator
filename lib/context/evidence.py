"""Local evidence persistence with traversal protection and mandatory redaction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile


_TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|authorization|cookie)\b\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive(content: str) -> str:
    result = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", str(content))
    result = _BEARER.sub("Bearer [REDACTED]", result)
    return _SECRET.sub(lambda match: match.group(1) + "[REDACTED]", result)


@dataclass(frozen=True)
class EvidenceReference:
    path: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


def _unsafe(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _assert_no_links(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("evidence path escaped project root") from exc
    current = root
    if _unsafe(current):
        raise ValueError("evidence path contains a link or reparse point")
    for part in relative.parts:
        current = current / part
        if _unsafe(current):
            raise ValueError("evidence path contains a link or reparse point")


class EvidenceStore:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".galaxy" / "evidence"

    def _directory(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not _TASK.fullmatch(task_id):
            raise ValueError("task_id must contain only letters, numbers, dot, underscore, or hyphen")
        current = self.project_root
        for part in (".galaxy", "evidence", task_id):
            current = current / part
        _assert_no_links(self.project_root, current)
        return current

    def persist(self, task_id: str, content: str, *, label: str = "evidence") -> EvidenceReference:
        safe = redact_sensitive(content).encode("utf-8")
        digest = hashlib.sha256(safe).hexdigest()
        safe_label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-") or "evidence"
        directory = self._directory(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        _assert_no_links(self.project_root, directory)
        target = directory / f"{safe_label}-{digest[:16]}.txt"
        relative = target.relative_to(self.project_root).as_posix()
        if _unsafe(target):
            raise ValueError("evidence target is a link or reparse point")
        if target.is_file():
            current = target.read_bytes()
            if hashlib.sha256(current).hexdigest() != digest:
                raise ValueError("existing evidence digest mismatch")
            return EvidenceReference(relative, digest, len(safe))
        descriptor, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(safe)
                stream.flush()
                os.fsync(stream.fileno())
            if _unsafe(target):
                raise ValueError("evidence target is a link or reparse point")
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return EvidenceReference(relative, digest, len(safe))

    def read(self, reference: EvidenceReference) -> str:
        relative = Path(reference.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("evidence reference must be project-relative")
        target = self.project_root / relative
        if self.root not in target.parents:
            raise ValueError("evidence reference escaped its store")
        _assert_no_links(self.project_root, target)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError("evidence reference digest mismatch")
        if len(data) != reference.bytes:
            raise ValueError("evidence reference size mismatch")
        return data.decode("utf-8")
