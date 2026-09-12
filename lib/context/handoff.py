"""Structured handoff compaction that preserves critical state."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from .evidence import EvidenceReference, EvidenceStore, redact_sensitive
from .policy import ContextEconomyPolicy


FIELDS = ("STATUS", "CHANGED", "TESTS", "DECISIONS", "BLOCKERS", "RISKS", "EVIDENCE", "HEAD")
CRITICAL = frozenset({"STATUS", "BLOCKERS", "RISKS", "HEAD"})


def _text(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return value.strip() or "none"
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item).strip() for item in value if str(item).strip()) or "none"
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _render(report: Mapping[str, Any]) -> str:
    normalized = {str(key).upper(): value for key, value in report.items()}
    return "\n".join(f"{field}: {_text(normalized.get(field))}" for field in FIELDS) + "\n"


def _tokens(text: str) -> int:
    # Deterministic conservative approximation used only for internal budgets.
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class HandoffResult:
    text: str
    estimated_tokens: int
    compacted: bool
    overflow: EvidenceReference | None = None


class HandoffCompactor:
    def __init__(self, policy: ContextEconomyPolicy, evidence: EvidenceStore):
        self.policy = policy
        self.evidence = evidence

    def compact(self, task_id: str, report: Mapping[str, Any]) -> HandoffResult:
        full = _render(report)
        if not self.policy.enabled:
            return HandoffResult(full, _tokens(full), False)
        full = redact_sensitive(full)
        limit = self.policy.handoff_max_summary_tokens
        if limit is None or _tokens(full) <= limit:
            return HandoffResult(full, _tokens(full), False)
        reference = self.evidence.persist(task_id, full, label="handoff")
        normalized = {str(key).upper(): value for key, value in report.items()}
        compact: dict[str, Any] = {}
        for field in FIELDS:
            value = redact_sensitive(_text(normalized.get(field)))
            if field in CRITICAL:
                compact[field] = value
            elif field == "EVIDENCE":
                existing = "" if value == "none" else value + "; "
                compact[field] = existing + f"{reference.path} sha256={reference.sha256}"
            else:
                items = [item.strip() for item in value.split(";") if item.strip()]
                compact[field] = "; ".join(items[:3]) if items else "none"
        rendered = _render(compact)
        # Critical data is never truncated. If it alone exceeds the budget, the
        # result may exceed the target and the complete sanitized report remains referenced.
        return HandoffResult(rendered, _tokens(rendered), True, reference)
