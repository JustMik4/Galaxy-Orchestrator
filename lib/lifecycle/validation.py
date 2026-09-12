"""Shared validation for lifecycle policy values."""

from __future__ import annotations

import math
from typing import Any


def validate_retention_days(value: Any) -> float:
    """Return a finite, non-negative retention period.

    Retention is used by destructive cleanup decisions, so values which would
    make every artifact immediately eligible (negative) or make comparisons
    meaningless (NaN/infinity) are rejected before inspecting the repository.
    """

    if isinstance(value, bool):
        raise ValueError("retention_days must be finite and >= 0")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("retention_days must be finite and >= 0") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("retention_days must be finite and >= 0")
    return normalized


__all__ = ["validate_retention_days"]
