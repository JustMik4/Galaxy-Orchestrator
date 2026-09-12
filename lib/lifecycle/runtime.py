"""Planning and safe application of old generated runtime files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


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
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    if resolved in protected:
        return True
    return any(part.casefold() in _AUDIT_WORDS or any(word in part.casefold() for word in _AUDIT_WORDS)
               for part in resolved.parts)


@dataclass(frozen=True)
class RuntimeCandidate:
    path: str
    category: str
    modified_at: float
    retention_elapsed: bool
    protected_audit: bool
    safe: bool
    reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "safe" if self.safe else "protected"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "category": self.category,
                "modified_at": self.modified_at, "retention_elapsed": self.retention_elapsed,
                "protected_audit": self.protected_audit, "safe": self.safe,
                "reasons": list(self.reasons)}


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
        data = [(item.path, item.modified_at) for item in self.safe_candidates]
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
    project = Path(root).resolve()
    current = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    protected = {Path(value).resolve() for value in current_audit_paths}
    roots = [project / relative for relative in _DEFAULT_ROOTS] if paths is None else [Path(value) if Path(value).is_absolute() else project / value for value in paths]
    candidates: list[RuntimeCandidate] = []
    for scan_root in roots:
        if not scan_root.exists():
            continue
        if scan_root.is_file():
            entries = [scan_root]
        else:
            try:
                entries = [item for item in scan_root.rglob("*") if item.is_file()]
            except OSError:
                entries = []
        for path in entries:
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
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
            candidates.append(RuntimeCandidate(relative, category, modified, old,
                                               protected_audit, not reasons, tuple(reasons)))
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
    deleted: list[str] = []
    for candidate in plan.safe_candidates:
        target = Path(plan.root) / candidate.path
        # The plan stores project-relative paths.  Re-check containment and
        # file type to make stale plans fail closed.
        resolved = target.resolve(strict=False)
        project = Path(plan.root).resolve()
        try:
            resolved.relative_to(project)
        except ValueError as exc:
            raise ValueError("runtime target escapes project") from exc
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        try:
            current_modified = resolved.stat().st_mtime
        except OSError as exc:
            raise FileNotFoundError(str(resolved)) from exc
        if current_modified != candidate.modified_at:
            raise RuntimeError(
                f"runtime file changed after preview: {candidate.path}"
            )
        resolved.unlink()
        deleted.append(candidate.path)
    return {"applied": True, "deleted": deleted, "targets": list(expected)}
