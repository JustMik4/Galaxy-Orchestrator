"""Read-only local operator preferences without shadowing Python's operator module.

Operator preferences are account-local and must never become project state.
Only the quota guard section is interpreted here; unknown fields fail closed
so credentials or accidental project policy cannot be silently accepted.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

from .quota import QuotaGuard, QuotaPolicy


_TOP_LEVEL_FIELDS = frozenset({"operator_id", "machine_id", "quota_guard"})
_IDENTITY_FIELDS = frozenset({"operator_id", "machine_id"})
_QUOTA_FIELDS = frozenset(
    {
        "enabled",
        "five_hour_warn",
        "five_hour_economy",
        "five_hour_stop",
        "weekly_warn",
        "weekly_economy",
        "weekly_stop",
        "check_before_dispatch",
        "check_before_retry",
        "check_before_escalation",
        "check_before_expensive_review",
        "interrupt_running_agent",
        "unknown_telemetry",
    }
)


def default_operator_config_path() -> Path:
    """Return the ignored operator configuration beside the master package."""

    return Path(__file__).resolve().parents[1] / "local" / "operator.toml"


def _validate_identity(data: dict[str, object]) -> None:
    for field in _IDENTITY_FIELDS:
        if field in data and (
            not isinstance(data[field], str)
            or not data[field].strip()
            or len(data[field]) > 120
        ):
            raise ValueError(f"{field} must be a non-empty string")


def load_operator_policy(path: str | Path | None = None) -> QuotaPolicy:
    """Load the local quota policy, defaulting safely when it is absent.

    This function only reads the selected file. It rejects unknown fields and
    malformed values instead of falling back to defaults after an operator
    configuration error.
    """

    source = Path(path) if path is not None else default_operator_config_path()
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return QuotaPolicy()
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("cannot load operator configuration: " + str(exc)) from exc
    if not isinstance(raw, dict):
        raise ValueError("operator configuration must be a TOML table")
    unknown = set(raw) - _TOP_LEVEL_FIELDS
    if unknown:
        raise ValueError("unsupported operator configuration fields: " + ", ".join(sorted(unknown)))
    _validate_identity(raw)
    quota = raw.get("quota_guard", {})
    if not isinstance(quota, dict):
        raise ValueError("quota_guard must be a TOML table")
    unknown_quota = set(quota) - _QUOTA_FIELDS
    if unknown_quota:
        raise ValueError("unsupported quota_guard fields: " + ", ".join(sorted(unknown_quota)))
    try:
        return QuotaPolicy(**quota)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid quota_guard policy: " + str(exc)) from exc


def load_operator_quota_guard(path: str | Path | None = None) -> QuotaGuard:
    """Construct a quota guard from local operator preferences."""

    return QuotaGuard(load_operator_policy(path))


__all__ = [
    "default_operator_config_path",
    "load_operator_policy",
    "load_operator_quota_guard",
]
