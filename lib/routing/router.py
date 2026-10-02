"""Deterministic normal and emergency capability routing."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from .catalog import CapabilityCatalog, ModelCapability, Route
from lib.quota import QuotaGuard, QuotaSnapshot
from lib.operator_config import load_operator_quota_guard


class FailureClass(str, Enum):
    IMPLEMENTATION = "implementation"
    REASONING = "reasoning"
    COMPLEXITY = "complexity"
    ARCHITECTURE = "architecture"
    AMBIGUITY = "ambiguity"
    SCOPE = "scope"
    REGRESSION = "regression"
    INFRA = "infra"
    FLAKY = "flaky"
    GIT = "git"
    HOST_ROUTING = "host-routing"
    QUOTA = "quota"
    CAPABILITY_MISSING = "capability-missing"
    MIGRATION_CONFLICT = "migration-conflict"
    CONTEXT_INSUFFICIENT = "context-insufficient"


class RouteProfile(str, Enum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    QUALITY = "quality"
    CRITICAL = "critical"


class RoutingAction(str, Enum):
    DISPATCH = "dispatch"
    EMERGENCY_DISPATCH = "emergency-dispatch"
    BLOCKED = "blocked"
    BLOCKED_QUOTA = "blocked-quota"
    RETURN_TO_ROOT = "return-to-root"
    RETURN_TO_ARCHITECT = "return-to-architect"


@dataclass(frozen=True)
class ProfilePolicy:
    capability_adjustment: int
    frontier_allowed: bool
    prefer_quality: bool = False
    independent_review: bool = False
    architectural_review_if_relevant: bool = False
    frontier_requires_reason: bool = False


PROFILE_POLICIES = MappingProxyType({
    RouteProfile.ECONOMY: ProfilePolicy(0, False),
    RouteProfile.BALANCED: ProfilePolicy(0, True, frontier_requires_reason=True),
    RouteProfile.QUALITY: ProfilePolicy(1, True, prefer_quality=True),
    RouteProfile.CRITICAL: ProfilePolicy(
        2,
        True,
        prefer_quality=True,
        independent_review=True,
        architectural_review_if_relevant=True,
    ),
})


@dataclass(frozen=True)
class EmergencyPolicy:
    enabled: bool = True
    max_emergency_dispatches_per_task: int = 1
    require_failure_evidence: bool = True
    allow_frontier: bool = True
    ignore_quota_guard: bool = False

    def __post_init__(self) -> None:
        if self.max_emergency_dispatches_per_task < 0:
            raise ValueError("emergency dispatch limit cannot be negative")
        if self.ignore_quota_guard:
            raise ValueError("emergency routing cannot ignore the quota guard")


@dataclass(frozen=True)
class RoutingRequest:
    role: str = "worker"
    task_class: str = "implementation"
    risk: str = "low"
    profile: RouteProfile = RouteProfile.BALANCED
    required_capability: int | None = None
    specialist: str | None = None
    failure_class: FailureClass | None = None
    previous_route: Route | None = None
    failure_evidence: str | None = None
    emergency_reason: str | None = None
    emergency: bool = False
    frontier_reason: str | None = None
    quota_snapshot: "QuotaSnapshot | None" = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", RouteProfile(self.profile))
        if self.failure_class is not None:
            object.__setattr__(self, "failure_class", FailureClass(self.failure_class))
        if self.required_capability is not None and self.required_capability < 0:
            raise ValueError("required_capability cannot be negative")
        if self.task_id is not None and (not isinstance(self.task_id, str) or not self.task_id):
            raise ValueError("task_id must be non-empty")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role must be non-empty")
        if not isinstance(self.task_class, str) or not self.task_class.strip():
            raise ValueError("task_class must be non-empty")


@dataclass(frozen=True)
class RoutingDecision:
    action: RoutingAction
    route: Route | None
    reason: str
    failure_class: FailureClass | None = None
    fresh_context: bool = False
    quota_state: str | None = None
    emergency_record: "EmergencyRecord | None" = None


@dataclass(frozen=True)
class EmergencyRecord:
    reason: str
    failure_evidence: str
    previous_route: Route | None
    selected_route: Route
    normal_next_route_skipped: str
    quota_snapshot: "QuotaSnapshot | None"


class CapabilityRouter:
    """Choose the cheapest verified route that satisfies a computed requirement."""

    _TASK_REQUIREMENTS = {
        "exploration": 1,
        "implementation": 2,
        "reasoning": 3,
        "review": 4,
        "architecture": 5,
    }
    _RISK_ADJUSTMENTS = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    _NO_PROMOTION = frozenset(
        {FailureClass.INFRA, FailureClass.FLAKY, FailureClass.GIT, FailureClass.HOST_ROUTING}
    )
    _ROOT_FAILURES = frozenset(
        {FailureClass.AMBIGUITY, FailureClass.SCOPE, FailureClass.REGRESSION, FailureClass.MIGRATION_CONFLICT}
    )
    _MODEL_GENERATION_PREFERENCE = {
        "gpt-6.1-sol": 0,
        "gpt-6-astra": 1,
        "gpt-6-sol": 1,
        "gpt-6-luna": 1,
    }

    def __init__(
        self,
        catalog: CapabilityCatalog,
        emergency_policy: EmergencyPolicy | None = None,
        quota_guard: QuotaGuard | None = None,
        operator_config: str | Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.emergency_policy = emergency_policy or EmergencyPolicy()
        if quota_guard is not None and operator_config is not None:
            raise ValueError("provide quota_guard or operator_config, not both")
        self.quota_guard = (
            quota_guard
            if quota_guard is not None
            else load_operator_quota_guard(operator_config)
        )

    def route(
        self,
        request: RoutingRequest,
        *,
        quota_guard: "QuotaGuard | None" = None,
    ) -> RoutingDecision:
        effective_guard = quota_guard or self.quota_guard
        if request.failure_class is FailureClass.CONTEXT_INSUFFICIENT:
            if request.previous_route is None:
                return self._normal(request, effective_guard)
            quota_block = self._check_quota(
                request, self.catalog.get(request.previous_route), effective_guard, "retry",
            )
            if quota_block:
                return quota_block
            return RoutingDecision(
                RoutingAction.DISPATCH,
                request.previous_route,
                "expand referenced context and retry the same route before model promotion",
                request.failure_class,
                fresh_context=True,
            )
        if request.failure_class in self._NO_PROMOTION:
            return RoutingDecision(
                RoutingAction.BLOCKED,
                request.previous_route,
                "repair environment, Git, or host routing without promoting the model",
                request.failure_class,
            )
        if request.failure_class is FailureClass.ARCHITECTURE and request.role not in ("root", "architect"):
            return RoutingDecision(
                RoutingAction.RETURN_TO_ARCHITECT,
                None,
                "architecture must return to root/architect authority",
                request.failure_class,
                fresh_context=True,
            )
        if request.failure_class in self._ROOT_FAILURES:
            return RoutingDecision(
                RoutingAction.RETURN_TO_ROOT,
                None,
                "contract or migration conflict requires root authority",
                request.failure_class,
            )
        if request.failure_class in (FailureClass.CAPABILITY_MISSING, FailureClass.QUOTA):
            return RoutingDecision(
                RoutingAction.BLOCKED,
                None,
                "explicit blocker must be resolved before dispatch",
                request.failure_class,
            )
        if request.emergency:
            return self._emergency(request, effective_guard)
        return self._normal(request, effective_guard)

    def _requirement(self, request: RoutingRequest) -> int:
        if request.required_capability is not None:
            return request.required_capability
        if request.task_class not in self._TASK_REQUIREMENTS:
            raise ValueError(f"unknown task class: {request.task_class}")
        if request.risk not in self._RISK_ADJUSTMENTS:
            raise ValueError(f"unknown risk: {request.risk}")
        base = self._TASK_REQUIREMENTS[request.task_class]
        risk = self._RISK_ADJUSTMENTS[request.risk]
        profile = PROFILE_POLICIES[request.profile].capability_adjustment
        return max(0, base + risk + profile)

    def _qualifying(
        self,
        requirement: int,
        *,
        allow_frontier: bool,
        above: ModelCapability | None = None,
    ) -> list[ModelCapability]:
        result = [
            item
            for item in self.catalog.available(allow_frontier=allow_frontier)
            if item.capability >= requirement
            and (above is None or item.capability > above.capability or item.expected_cost > above.expected_cost)
        ]
        return sorted(
            result,
            key=lambda item: (
                item.expected_cost,
                item.capability,
                self._MODEL_GENERATION_PREFERENCE.get(item.route.model, 2),
                item.route,
            ),
        )

    def _check_quota(
        self,
        request: RoutingRequest,
        candidate: ModelCapability,
        quota_guard: "QuotaGuard | None",
        operation: str,
    ) -> RoutingDecision | None:
        snapshot = request.quota_snapshot or QuotaSnapshot(None, None)
        quota = quota_guard.evaluate(
            snapshot,
            operation,
            expensive=candidate.expected_cost >= 7,
            frontier=candidate.frontier,
        )
        if quota.allowed:
            return None
        return RoutingDecision(
            RoutingAction.BLOCKED_QUOTA,
            None,
            quota.reason,
            FailureClass.QUOTA,
            quota_state=quota.state.value,
        )

    def _normal(
        self, request: RoutingRequest, quota_guard: "QuotaGuard | None"
    ) -> RoutingDecision:
        requirement = self._requirement(request)
        previous = self.catalog.get(request.previous_route) if request.previous_route else None
        if request.failure_class in (
            FailureClass.REASONING,
            FailureClass.COMPLEXITY,
            FailureClass.IMPLEMENTATION,
        ) and previous is not None:
            requirement = max(requirement, previous.capability + 1)
        policy = PROFILE_POLICIES[request.profile]
        allow_frontier = policy.frontier_allowed and (
            not policy.frontier_requires_reason or bool((request.frontier_reason or "").strip())
        )
        candidates = self._qualifying(requirement, allow_frontier=allow_frontier)
        if not candidates:
            return RoutingDecision(
                RoutingAction.BLOCKED,
                None,
                "no configured and host-observed model/effort pair qualifies",
                FailureClass.CAPABILITY_MISSING,
            )
        candidate = candidates[0]
        operation = "escalation" if request.previous_route else "dispatch"
        quota_block = self._check_quota(request, candidate, quota_guard, operation)
        if quota_block:
            return quota_block
        return RoutingDecision(
            RoutingAction.DISPATCH,
            candidate.route,
            "lowest expected-cost verified route satisfying task capability",
            fresh_context=request.previous_route is not None,
        )

    def _emergency(
        self, request: RoutingRequest, quota_guard: "QuotaGuard | None"
    ) -> RoutingDecision:
        policy = self.emergency_policy
        if not policy.enabled:
            return RoutingDecision(RoutingAction.BLOCKED, None, "emergency routing disabled")
        if policy.require_failure_evidence and not (request.failure_evidence or "").strip():
            raise ValueError("emergency routing requires failure evidence")
        if not (request.emergency_reason or "").strip():
            raise ValueError("emergency routing requires a reason")
        requirement = self._requirement(request)
        previous = self.catalog.get(request.previous_route) if request.previous_route else None
        if previous is not None:
            requirement = max(requirement, previous.capability + 1)
        candidates = self._qualifying(
            requirement,
            allow_frontier=policy.allow_frontier and PROFILE_POLICIES[request.profile].frontier_allowed,
            above=previous,
        )
        if not candidates:
            return RoutingDecision(
                RoutingAction.BLOCKED,
                None,
                "no justified host-observed emergency route exists",
                FailureClass.CAPABILITY_MISSING,
            )
        candidate = candidates[0]
        quota_block = self._check_quota(request, candidate, quota_guard, "emergency")
        if quota_block:
            return quota_block
        return RoutingDecision(
            RoutingAction.EMERGENCY_DISPATCH,
            candidate.route,
            "evidence-based emergency route selected outside normal ordering",
            request.failure_class,
            fresh_context=True,
            emergency_record=EmergencyRecord(
                reason=request.emergency_reason.strip(),
                failure_evidence=(request.failure_evidence or "").strip(),
                previous_route=request.previous_route,
                selected_route=candidate.route,
                normal_next_route_skipped="emergency policy selected an evidence-based intermediate route",
                quota_snapshot=request.quota_snapshot or QuotaSnapshot(None, None),
            ),
        )
