"""Deterministic, local cache for review evidence.

The cache key is content identity, not wall-clock freshness.  Any change to
the base/head, contract revision, reviewer class, relevant scope, policy, or
test evidence produces a new key and therefore cannot accidentally reuse an
old expensive review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping


def normalize_relevant_scope(scope: Any) -> tuple[str, ...]:
    """Normalize a scope into a sorted, duplicate-free tuple.

    Scope accepts one path, or any iterable of paths.  Normalization is
    intentionally conservative: it removes formatting-only differences but
    does not resolve paths on disk or follow symlinks.
    """

    if scope is None:
        values: list[Any] = []
    elif isinstance(scope, str):
        values = [scope]
    else:
        try:
            values = list(scope)
        except TypeError as exc:
            raise ValueError("relevant scope must be a path or iterable of paths") from exc
    normalized: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError("relevant scope entries must be strings")
        value = item.strip()
        if (not value or value.startswith(("/", "\\")) or "\\" in value
                or re.search(r"[:*?\[\]<>|\x00-\x1f]", value)):
            raise ValueError("relevant scope entries must be literal relative paths")
        if value.startswith("./"):
            value = value[2:]
        parts = value.rstrip("/").split("/")
        if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in parts):
            raise ValueError("unsafe relevant scope entry")
        value = "/".join(parts)
        normalized.add(value.casefold())
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class ReviewFingerprint:
    base_sha: str
    head_sha: str
    contract_revision: str
    reviewer_class: str
    relevant_scope: tuple[str, ...]
    policy_revision: str
    tests_revision: str

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "contract_revision": self.contract_revision,
            "reviewer_class": self.reviewer_class,
            "relevant_scope": list(self.relevant_scope),
            "policy_revision": self.policy_revision,
            "tests_revision": self.tests_revision,
        }

    @property
    def value(self) -> str:
        encoded = json.dumps(self.payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def key(self) -> str:
        return self.value

    @property
    def digest(self) -> str:
        """Alias used by evidence/telemetry serializers."""

        return self.value

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload, fingerprint=self.value)


def make_review_fingerprint(base_sha: str, head_sha: str, contract_revision: Any,
                            reviewer_class: str, relevant_scope: Any = (), *,
                            policy_revision: Any, tests_revision: Any,
                            **aliases: Any) -> ReviewFingerprint:
    # ``scope`` and ``normalized_scope`` are accepted as migration-friendly
    # aliases; the canonical field remains ``relevant_scope``.
    if "scope" in aliases:
        if relevant_scope not in (None, ()):
            raise TypeError("provide only relevant_scope or scope")
        relevant_scope = aliases.pop("scope")
    if "normalized_scope" in aliases:
        if relevant_scope not in (None, ()):
            raise TypeError("provide only relevant_scope or normalized_scope")
        relevant_scope = aliases.pop("normalized_scope")
    if aliases:
        raise TypeError("unknown fingerprint fields: " + ", ".join(sorted(aliases)))
    fields = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "contract_revision": contract_revision,
        "reviewer_class": reviewer_class,
        "policy_revision": policy_revision,
        "tests_revision": tests_revision,
    }
    for name, value in fields.items():
        if not isinstance(value, (str, int)) or not str(value).strip():
            raise ValueError(f"{name} is required")
    return ReviewFingerprint(
        base_sha=str(base_sha).strip(),
        head_sha=str(head_sha).strip(),
        contract_revision=str(contract_revision).strip(),
        reviewer_class=str(reviewer_class).strip(),
        relevant_scope=normalize_relevant_scope(relevant_scope),
        policy_revision=str(policy_revision).strip(),
        tests_revision=str(tests_revision).strip(),
    )


# Friendly aliases for callers that use noun/verb variants.
review_fingerprint = make_review_fingerprint
fingerprint_review = make_review_fingerprint


_SECRET_KEY = re.compile(
    r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|authorization|credential|cookie|prompt)(?:$|[_-])",
    re.IGNORECASE,
)


def _safe(value: Any, *, key: str | None = None) -> Any:
    """Drop sensitive evidence fields before it reaches local JSON."""

    if key and _SECRET_KEY.search(key):
        return None
    if isinstance(value, Mapping):
        result = {}
        for k, v in value.items():
            clean = _safe(v, key=str(k))
            if clean is not None:
                result[str(k)] = clean
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class ReviewEntry:
    fingerprint: ReviewFingerprint
    evidence: Any
    created_at: str
    metadata: dict[str, Any]

    @property
    def reused(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "evidence": _safe(self.evidence),
            "created_at": self.created_at,
            "metadata": _safe(self.metadata),
        }

    def __getitem__(self, key: Any) -> Any:
        """Allow ``cache.get(fp)["result"]`` for mapping evidence."""

        if isinstance(self.evidence, Mapping):
            return self.evidence[key]
        raise TypeError("cached evidence is not a mapping")

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(self.evidence, Mapping):
            return self.evidence.get(key, default)
        return default

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewEntry":
        fp = value.get("fingerprint", {})
        if not isinstance(fp, Mapping):
            raise ValueError("invalid review fingerprint")
        fingerprint = make_review_fingerprint(
            fp.get("base_sha"), fp.get("head_sha"), fp.get("contract_revision"),
            fp.get("reviewer_class"), fp.get("relevant_scope", ()),
            policy_revision=fp.get("policy_revision"),
            tests_revision=fp.get("tests_revision"),
        )
        # A wrong persisted hash indicates tampering/corruption.  Ignore the
        # entry instead of trusting content under an unrelated key.
        if fp.get("fingerprint") not in (None, fingerprint.value):
            raise ValueError("review fingerprint checksum mismatch")
        created = value.get("created_at")
        if not isinstance(created, str) or not created:
            created = "unknown"
        metadata = value.get("metadata", {})
        return cls(fingerprint, value.get("evidence"), created,
                   dict(metadata) if isinstance(metadata, Mapping) else {})


class ReviewCache:
    """In-memory review cache with optional atomic local JSON persistence."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, ReviewEntry] = {}
        if self.path is not None:
            self._load()

    @staticmethod
    def fingerprint(base_sha: str, head_sha: str, contract_revision: Any,
                    reviewer_class: str, relevant_scope: Any = (), *,
                    policy_revision: Any, tests_revision: Any,
                    **aliases: Any) -> ReviewFingerprint:
        return make_review_fingerprint(base_sha, head_sha, contract_revision,
                                       reviewer_class, relevant_scope,
                                       policy_revision=policy_revision,
                                       tests_revision=tests_revision, **aliases)

    make_fingerprint = fingerprint

    def get(self, fingerprint: ReviewFingerprint | str) -> ReviewEntry | None:
        key = fingerprint.value if isinstance(fingerprint, ReviewFingerprint) else str(fingerprint)
        return self._entries.get(key)

    lookup = get

    def has(self, fingerprint: ReviewFingerprint | str) -> bool:
        return self.get(fingerprint) is not None

    def put(self, fingerprint: ReviewFingerprint, evidence: Any, *,
            metadata: Mapping[str, Any] | None = None,
            created_at: str | None = None) -> ReviewEntry:
        if not isinstance(fingerprint, ReviewFingerprint):
            raise TypeError("put expects a ReviewFingerprint")
        entry = ReviewEntry(
            fingerprint=fingerprint,
            evidence=_safe(evidence),
            created_at=created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata=dict(_safe(metadata or {})),
        )
        self._entries[fingerprint.value] = entry
        self._save()
        return entry

    store = put
    add = put

    def record(self, *, base_sha: str, head_sha: str, contract_revision: Any,
               reviewer_class: str, relevant_scope: Any = (),
               policy_revision: Any, tests_revision: Any, evidence: Any = None,
               metadata: Mapping[str, Any] | None = None,
               created_at: str | None = None) -> ReviewEntry:
        return self.put(self.fingerprint(base_sha, head_sha, contract_revision,
                                         reviewer_class, relevant_scope,
                                         policy_revision=policy_revision,
                                         tests_revision=tests_revision), evidence,
                        metadata=metadata, created_at=created_at)

    def get_or_reuse(self, fingerprint: ReviewFingerprint, producer: Any = None) -> ReviewEntry | Any:
        """Return existing evidence, calling ``producer`` only on a miss."""

        existing = self.get(fingerprint)
        if existing is not None:
            return existing
        if producer is None:
            return None
        result = producer() if callable(producer) else producer
        return self.put(fingerprint, result)

    get_or_create = get_or_reuse

    def invalidate(self, fingerprint: ReviewFingerprint | str) -> bool:
        key = fingerprint.value if isinstance(fingerprint, ReviewFingerprint) else str(fingerprint)
        removed = self._entries.pop(key, None) is not None
        if removed:
            self._save()
        return removed

    def clear(self) -> None:
        self._entries.clear()
        self._save()

    def entries(self) -> tuple[ReviewEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("entries", []) if isinstance(data, Mapping) else []
            if not isinstance(items, list):
                return
            for item in items:
                try:
                    entry = ReviewEntry.from_dict(item)
                except (TypeError, ValueError, KeyError):
                    continue
                self._entries[entry.fingerprint.value] = entry
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Leave malformed data untouched and fail closed (cache miss).
            return

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "entries": [entry.to_dict() for entry in self.entries()]}
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with open(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            Path(temporary).replace(self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


__all__ = [
    "ReviewCache", "ReviewEntry", "ReviewFingerprint", "make_review_fingerprint",
    "review_fingerprint", "fingerprint_review", "normalize_relevant_scope",
]
