"""Verify the model and effort that a dispatch actually used.

Codex hosts can expose telemetry in different shapes.  This module accepts a
small, deliberately permissive mapping/attribute protocol and keeps the
resulting record independent of the host implementation.  A mismatch is a
host-routing problem, never a model-quality failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping
import uuid


class VerificationStatus(str, Enum):
    """Lifecycle states for one important agent dispatch."""

    REQUESTED = "REQUESTED"
    SPAWNED = "SPAWNED"
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"


# Module-level names are convenient for JSON-oriented integrations and keep
# status checks readable without requiring callers to import the enum.
REQUESTED = VerificationStatus.REQUESTED
SPAWNED = VerificationStatus.SPAWNED
VERIFIED = VerificationStatus.VERIFIED
MISMATCH = VerificationStatus.MISMATCH
UNVERIFIED = VerificationStatus.UNVERIFIED


_SECRET_KEY = re.compile(
    r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|authorization|credential|cookie)(?:$|[_-])",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe(value: Any, *, key: str | None = None) -> Any:
    """Return JSON-safe metadata while dropping credential-like fields."""

    if key and _SECRET_KEY.search(key):
        return None
    if isinstance(value, Mapping):
        return {
            str(k): cleaned
            for k, v in value.items()
            if (cleaned := _safe(v, key=str(k))) is not None
        }
    if isinstance(value, set):
        return [_safe(v) for v in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _telemetry_value(telemetry: Any, names: Iterable[str]) -> Any:
    """Read the first non-empty value from mappings or telemetry objects."""

    if telemetry is None:
        return None
    for name in names:
        value = telemetry.get(name) if isinstance(telemetry, Mapping) else getattr(telemetry, name, None)
        if value is not None and value != "":
            return value
    # A few hosts nest effective information under runtime/effective/usage.
    for container_name in ("effective", "runtime", "session", "telemetry", "usage"):
        nested = telemetry.get(container_name) if isinstance(telemetry, Mapping) else getattr(telemetry, container_name, None)
        if nested is not None:
            value = _telemetry_value(nested, names)
            if value is not None:
                return value
    return None


@dataclass
class DispatchRecord:
    """Auditable, secret-free description of a dispatch and its verification."""

    requested_model: str
    requested_effort: str
    dispatch_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    spawn_id: str | None = None
    parent_thread: str | None = None
    effective_model: str | None = None
    effective_effort: str | None = None
    agent_path: str | None = None
    multicontroller_version: str | None = None
    status: VerificationStatus = VerificationStatus.REQUESTED
    failure_class: str | None = None
    verified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def galaxy_version(self) -> str | None:
        """Canonical alias retained alongside the V1 field name."""

        return self.multicontroller_version

    @property
    def trusted(self) -> bool:
        return self.status is VerificationStatus.VERIFIED

    @property
    def reputation_eligible(self) -> bool:
        # Unknown telemetry cannot establish attribution either.  In
        # particular, MISMATCH is never fed to model reputation.
        return self.status is VerificationStatus.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        result = {
            "dispatch_id": self.dispatch_id,
            "requested_model": self.requested_model,
            "requested_effort": self.requested_effort,
            "spawn_id": self.spawn_id,
            "parent_thread": self.parent_thread,
            "effective_model": self.effective_model,
            "effective_effort": self.effective_effort,
            "agent_path": self.agent_path,
            "galaxy_version": self.multicontroller_version,
            "status": self.status.value,
            "failure_class": self.failure_class,
            "verified_at": self.verified_at,
            "trusted": self.trusted,
            "reputation_eligible": self.reputation_eligible,
            "metadata": _safe(self.metadata),
        }
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DispatchRecord":
        data = dict(value)
        if "galaxy_version" in data:
            data["multicontroller_version"] = data.pop("galaxy_version")
        data["status"] = VerificationStatus(data.get("status", VerificationStatus.REQUESTED))
        data.pop("trusted", None)
        data.pop("reputation_eligible", None)
        return cls(**{k: data[k] for k in (
            "requested_model", "requested_effort", "dispatch_id", "spawn_id",
            "parent_thread", "effective_model", "effective_effort", "agent_path",
            "multicontroller_version", "status", "failure_class", "verified_at", "metadata"
        ) if k in data})


class RuntimeVerifier:
    """Collect dispatch records and verify effective runtime telemetry.

    ``path`` is optional.  When provided, records are saved atomically as a
    JSON array; no prompts or credential-like metadata are persisted.
    """

    def __init__(self, path: str | Path | None = None, *, version: str | None = None,
                 allow_advertised_fallback: bool = False):
        self.path = Path(path) if path is not None else None
        self.version = version
        self.allow_advertised_fallback = allow_advertised_fallback
        self.records: dict[str, DispatchRecord] = {}
        if self.path is not None:
            self._load()

    def request(self, requested_model: str, requested_effort: str, **kwargs: Any) -> DispatchRecord:
        if not isinstance(requested_model, str) or not requested_model.strip():
            raise ValueError("requested model is required")
        if not isinstance(requested_effort, str) or not requested_effort.strip():
            raise ValueError("requested effort is required")
        metadata = kwargs.pop("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        record = DispatchRecord(
            requested_model=requested_model.strip(), requested_effort=requested_effort.strip(),
            multicontroller_version=kwargs.pop("multicontroller_version", kwargs.pop("version", self.version)),
            metadata=dict(metadata), **kwargs,
        )
        if record.dispatch_id in self.records:
            raise ValueError("duplicate dispatch id")
        self.records[record.dispatch_id] = record
        self._save()
        return record

    # Descriptive aliases make the lifecycle usable by dispatch adapters.
    record_request = request
    record_dispatch = request
    request_dispatch = request

    def spawned(self, dispatch: str | DispatchRecord, spawn_id: str | None = None,
                *, parent_thread: str | None = None) -> DispatchRecord:
        record = self._record(dispatch)
        if record.status not in (VerificationStatus.REQUESTED, VerificationStatus.SPAWNED):
            raise ValueError("dispatch is already terminal")
        record.spawn_id = spawn_id or record.spawn_id
        record.parent_thread = parent_thread or record.parent_thread
        record.status = VerificationStatus.SPAWNED
        self._save()
        return record

    mark_spawned = spawned

    def verify(self, dispatch: str | DispatchRecord, telemetry: Any = None, *,
               effective_model: str | None = None, effective_effort: str | None = None,
               allow_fallback: bool | None = None) -> DispatchRecord:
        record = self._record(dispatch)
        if record.status in (VerificationStatus.VERIFIED, VerificationStatus.MISMATCH):
            raise ValueError("dispatch is already terminal")
        model = effective_model if effective_model is not None else _telemetry_value(
            telemetry, ("effective_model", "actual_model", "runtime_model", "model"))
        effort = effective_effort if effective_effort is not None else _telemetry_value(
            telemetry, ("effective_effort", "actual_effort", "reasoning_effort", "effort"))
        record.effective_model = str(model).strip() if model is not None else None
        record.effective_effort = str(effort).strip() if effort is not None else None
        if model is None or effort is None:
            record.status = VerificationStatus.UNVERIFIED
            record.failure_class = None
        elif (record.effective_model == record.requested_model and
              record.effective_effort == record.requested_effort):
            record.status = VerificationStatus.VERIFIED
            record.failure_class = None
        elif allow_fallback if allow_fallback is not None else self.allow_advertised_fallback:
            record.status = VerificationStatus.VERIFIED
            record.failure_class = "host-routing-fallback-accepted"
        else:
            record.status = VerificationStatus.MISMATCH
            record.failure_class = "host-routing"
        record.verified_at = _utc_now()
        self._save()
        return record

    verify_runtime = verify
    verify_dispatch = verify
    verify_effective = verify

    def get(self, dispatch_id: str) -> DispatchRecord | None:
        return self.records.get(dispatch_id)

    def reputation_input(self) -> list[dict[str, Any]]:
        """Return only dispatches whose requested route was actually verified."""

        return [r.to_dict() for r in self.records.values() if r.reputation_eligible]

    def _record(self, dispatch: str | DispatchRecord) -> DispatchRecord:
        if isinstance(dispatch, DispatchRecord):
            if dispatch.dispatch_id not in self.records:
                self.records[dispatch.dispatch_id] = dispatch
            return dispatch
        if dispatch not in self.records:
            raise KeyError("unknown dispatch id")
        return self.records[dispatch]

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("records", data) if isinstance(data, (dict, list)) else []
            if isinstance(entries, list):
                for item in entries:
                    if isinstance(item, Mapping) and item.get("dispatch_id"):
                        self.records[item["dispatch_id"]] = DispatchRecord.from_dict(item)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            # Corrupt telemetry is non-authoritative; fail closed and leave it
            # untouched for diagnostics rather than destroying evidence.
            return

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": [self.records[key].to_dict() for key in sorted(self.records)]}
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with open(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            Path(temporary).replace(self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


__all__ = [
    "DispatchRecord", "RuntimeVerifier", "VerificationStatus", "REQUESTED", "SPAWNED",
    "VERIFIED", "MISMATCH", "UNVERIFIED",
]
