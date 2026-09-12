"""Pure quota policy evaluation.

Percentages in this module are account telemetry supplied by the host.  Token
counts are intentionally not accepted as a proxy for subscription quota.
"""

from dataclasses import dataclass
from enum import Enum


class QuotaState(str, Enum):
    NORMAL = "normal"
    CONSERVATIVE = "conservative"
    ECONOMY = "economy"
    HARD_STOP = "hard-stop"
    UNKNOWN = "unknown"


class QuotaOperation(str, Enum):
    DISPATCH = "dispatch"
    RETRY = "retry"
    ESCALATION = "escalation"
    REVIEW = "review"
    EMERGENCY = "emergency"
    CLEANUP = "cleanup"
    HANDOFF = "handoff"


class UnknownTelemetryPolicy(str, Enum):
    BLOCK_EXPENSIVE = "block_expensive"
    ALLOW_BOUNDED = "allow_bounded"
    BLOCK_ALL = "block_all"


@dataclass(frozen=True)
class QuotaSnapshot:
    five_hour_remaining: float | None
    weekly_remaining: float | None

    def __post_init__(self) -> None:
        for name, value in (
            ("five_hour_remaining", self.five_hour_remaining),
            ("weekly_remaining", self.weekly_remaining),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100
            ):
                raise ValueError(f"{name} must be a percentage from 0 to 100 or None")

    @property
    def complete(self) -> bool:
        return self.five_hour_remaining is not None and self.weekly_remaining is not None


@dataclass(frozen=True)
class QuotaPolicy:
    enabled: bool = True
    five_hour_warn: float = 30
    five_hour_economy: float = 20
    five_hour_stop: float = 15
    weekly_warn: float = 8
    weekly_economy: float = 5
    weekly_stop: float = 2
    check_before_dispatch: bool = True
    check_before_retry: bool = True
    check_before_escalation: bool = True
    check_before_expensive_review: bool = True
    interrupt_running_agent: bool = True
    unknown_telemetry: UnknownTelemetryPolicy = UnknownTelemetryPolicy.BLOCK_EXPENSIVE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unknown_telemetry",
            UnknownTelemetryPolicy(self.unknown_telemetry),
        )
        for prefix in ("five_hour", "weekly"):
            warn = getattr(self, f"{prefix}_warn")
            economy = getattr(self, f"{prefix}_economy")
            stop = getattr(self, f"{prefix}_stop")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 100
                for value in (warn, economy, stop)
            ):
                raise ValueError("quota thresholds must be percentages from 0 to 100")
            if not warn >= economy >= stop:
                raise ValueError(f"{prefix} thresholds must satisfy warn >= economy >= stop")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    state: QuotaState
    reason: str
    prefer_economy: bool = False


class QuotaGuard:
    """Classify account quota and authorize an orchestration operation."""

    _CLEANUP_OPERATIONS = frozenset({QuotaOperation.CLEANUP, QuotaOperation.HANDOFF})

    def __init__(self, policy: QuotaPolicy | None = None) -> None:
        self.policy = policy or QuotaPolicy()

    def state(self, snapshot: QuotaSnapshot) -> QuotaState:
        if not self.policy.enabled:
            return QuotaState.NORMAL
        if (
            snapshot.five_hour_remaining is not None
            and snapshot.five_hour_remaining <= self.policy.five_hour_stop
        ) or (
            snapshot.weekly_remaining is not None
            and snapshot.weekly_remaining <= self.policy.weekly_stop
        ):
            return QuotaState.HARD_STOP
        if not snapshot.complete:
            return QuotaState.UNKNOWN
        assert snapshot.five_hour_remaining is not None
        assert snapshot.weekly_remaining is not None
        if (
            snapshot.five_hour_remaining <= self.policy.five_hour_economy
            or snapshot.weekly_remaining <= self.policy.weekly_economy
        ):
            return QuotaState.ECONOMY
        if (
            snapshot.five_hour_remaining <= self.policy.five_hour_warn
            or snapshot.weekly_remaining <= self.policy.weekly_warn
        ):
            return QuotaState.CONSERVATIVE
        return QuotaState.NORMAL

    def evaluate(
        self,
        snapshot: QuotaSnapshot,
        operation: QuotaOperation | str = QuotaOperation.DISPATCH,
        *,
        expensive: bool = False,
        frontier: bool = False,
    ) -> QuotaDecision:
        operation = QuotaOperation(operation)
        state = self.state(snapshot)
        if operation in self._CLEANUP_OPERATIONS:
            return QuotaDecision(True, state, "cleanup and handoff remain available")
        if state is QuotaState.HARD_STOP:
            return QuotaDecision(
                False,
                state,
                "quota reserve reached; no dispatch, retry, escalation, review, or emergency",
                prefer_economy=True,
            )
        if state is QuotaState.UNKNOWN:
            policy = self.policy.unknown_telemetry
            if policy is UnknownTelemetryPolicy.BLOCK_ALL:
                return QuotaDecision(False, state, "quota telemetry unknown; policy blocks new work")
            if policy is UnknownTelemetryPolicy.BLOCK_EXPENSIVE and (expensive or frontier):
                return QuotaDecision(
                    False,
                    state,
                    "quota telemetry unknown; expensive/frontier work is blocked",
                    prefer_economy=True,
                )
            return QuotaDecision(True, state, "quota telemetry unknown; bounded work allowed")
        if state is QuotaState.ECONOMY and (frontier or expensive):
            return QuotaDecision(
                False,
                state,
                "economy quota state permits cheap qualifying routes only",
                True,
            )
        return QuotaDecision(
            True,
            state,
            "quota reserve available",
            prefer_economy=state in (QuotaState.CONSERVATIVE, QuotaState.ECONOMY),
        )

    def require(
        self,
        snapshot: QuotaSnapshot,
        operation: QuotaOperation | str = QuotaOperation.DISPATCH,
        *,
        expensive: bool = False,
        frontier: bool = False,
    ) -> QuotaDecision:
        decision = self.evaluate(
            snapshot, operation, expensive=expensive, frontier=frontier
        )
        if not decision.allowed:
            raise QuotaBlockedError(decision.reason)
        return decision


class QuotaBlockedError(RuntimeError):
    """Raised when callers opt into exception-based quota enforcement."""
