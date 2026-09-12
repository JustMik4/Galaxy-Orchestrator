"""Operational V2 boundary for routed, verified Galaxy dispatches and reviews."""

from __future__ import annotations

from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .evidence import ReviewCache
from .operator_config import load_operator_quota_guard
from .project import load_project
from .quota import QuotaSnapshot
from .routing import (
    CapabilityCatalog, CapabilityRouter, FailureClass, ModelCapability, Route,
    RouteProfile, RoutingAction, RoutingRequest,
)
from .routing.emergency import EmergencyDispatchState
from .runtime import RuntimeVerifier


_REQUEST_FIELDS = frozenset({
    "role", "task_class", "risk", "required_capability", "specialist",
    "failure_class", "previous_route", "failure_evidence", "emergency_reason",
    "emergency", "frontier_reason", "task_id", "profile",
})

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_CAPABILITY_FIELDS = frozenset({
    "model", "effort", "capability", "expected_cost", "frontier", "family",
})
_FINGERPRINT_FIELDS = frozenset({
    "base_sha", "head_sha", "contract_revision", "reviewer_class",
    "relevant_scope", "policy_revision", "tests_revision",
})


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
        or bool(junction and junction())
    )


def _safe_local_path(root: Path, relative: str) -> Path:
    target = root / relative
    for candidate in (*reversed(target.parents), target):
        if _is_link_or_reparse(candidate):
            raise ValueError("dispatch state path contains a symlink/reparse point: " + str(candidate))
    return target


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(label + " must be an object")
    return dict(value)


def _catalog(value: Any) -> CapabilityCatalog:
    raw = _mapping(value, "capabilities")
    unknown = set(raw) - {"configured", "observed"}
    if unknown:
        raise ValueError("unsupported capabilities fields: " + ", ".join(sorted(unknown)))
    configured = raw.get("configured")
    observed = raw.get("observed")
    if not isinstance(configured, list) or not isinstance(observed, list):
        raise ValueError("capabilities require configured and observed arrays")
    entries = []
    for item in configured:
        entry = _mapping(item, "configured capability")
        unknown_entry = set(entry) - _CAPABILITY_FIELDS
        if unknown_entry:
            raise ValueError("unsupported configured capability fields: " + ", ".join(sorted(unknown_entry)))
        try:
            entries.append(ModelCapability(
                Route(entry["model"], entry["effort"]), entry["capability"],
                entry["expected_cost"], entry.get("frontier", False), entry.get("family"),
            ))
        except KeyError as exc:
            raise ValueError("configured capability is incomplete") from exc
    pairs = []
    for item in observed:
        entry = _mapping(item, "observed capability")
        if set(entry) != {"model", "effort"}:
            raise ValueError("observed capability requires only model and effort")
        pairs.append((entry["model"], entry["effort"]))
    return CapabilityCatalog(entries, pairs)


def _quota(value: Any) -> QuotaSnapshot:
    raw = _mapping(value, "quota snapshot")
    if set(raw) != {"five_hour_remaining", "weekly_remaining"}:
        raise ValueError("quota snapshot requires exact five_hour_remaining and weekly_remaining fields")
    snapshot = QuotaSnapshot(raw["five_hour_remaining"], raw["weekly_remaining"])
    return snapshot


def _task_id(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise ValueError("task_id must be a safe non-empty identifier")
    if any(part in ("", ".", "..") for part in value.replace(":", "/").split("/")):
        raise ValueError("task_id must be a safe non-empty identifier")
    return value


def _profile_for_task(
    policy: Mapping[str, Any], task_id: str | None, requested: Any = None,
) -> RouteProfile:
    try:
        default = RouteProfile(policy.get("profile", "balanced"))
    except (TypeError, ValueError) as exc:
        raise ValueError("project routing profile is not allowed") from exc
    overrides = policy.get("task_profiles", {})
    if not isinstance(overrides, Mapping):
        raise ValueError("project routing task_profiles must be an object")
    parsed: dict[str, RouteProfile] = {}
    for identity, value in overrides.items():
        normalized = _task_id(identity, required=True)
        try:
            parsed[normalized] = RouteProfile(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("project routing task profile is not allowed") from exc
    selected = parsed.get(task_id, default)
    if requested is None:
        return selected
    try:
        return RouteProfile(requested)
    except (TypeError, ValueError) as exc:
        raise ValueError("task routing profile is not allowed") from exc


def _request(value: Any, *, profile: RouteProfile, quota: QuotaSnapshot) -> RoutingRequest:
    raw = _mapping(value, "routing request")
    unknown = set(raw) - _REQUEST_FIELDS
    if unknown:
        raise ValueError("unsupported routing request fields: " + ", ".join(sorted(unknown)))
    identity = _task_id(raw.get("task_id"), required=bool(raw.get("emergency")))
    previous = raw.get("previous_route")
    if previous is not None:
        previous = _mapping(previous, "previous_route")
        if set(previous) != {"model", "effort"}:
            raise ValueError("previous_route requires only model and effort")
        previous = Route(previous["model"], previous["effort"])
    values = dict(raw)
    values["task_id"] = identity
    values["profile"] = profile
    values["previous_route"] = previous
    values["quota_snapshot"] = quota
    if values.get("failure_class") is not None:
        values["failure_class"] = FailureClass(values["failure_class"])
    return RoutingRequest(**values)


class DispatchCoordinator:
    """Single boundary for route authorization, runtime proof, and review reuse."""

    def __init__(self, project_root: str | Path, *, operator_config: str | Path | None = None):
        self.project = load_project(project_root)
        self.operator_config = operator_config
        verifier_path = _safe_local_path(
            self.project.root, ".galaxy/runtime/dispatch-verification.json",
        )
        review_path = _safe_local_path(
            self.project.root, ".galaxy/cache/reviews.json",
        )
        emergency_path = _safe_local_path(
            self.project.root, ".galaxy/runtime/emergency-dispatches.json",
        )
        _safe_local_path(
            self.project.root, ".galaxy/runtime/emergency-dispatches.json.lock",
        )
        self.verifier = RuntimeVerifier(verifier_path, version=self.project.lock.galaxy_version)
        self.reviews = ReviewCache(review_path)
        # Validate both fixed paths before the accounting helper opens either.
        self.emergency_state = EmergencyDispatchState(emergency_path)

    def authorize(self, request: Any, capabilities: Any, quota: Any) -> dict[str, Any]:
        snapshot = _quota(quota)
        policy = self.project.config.raw.get("routing", {})
        if not isinstance(policy, Mapping):
            raise ValueError("project routing policy must be an object")
        raw_request = _mapping(request, "routing request")
        identity = _task_id(
            raw_request.get("task_id"), required=bool(raw_request.get("emergency")),
        )
        profile = _profile_for_task(policy, identity, raw_request.get("profile"))
        routing_request = _request(raw_request, profile=profile, quota=snapshot)
        router = CapabilityRouter(
            _catalog(capabilities), quota_guard=load_operator_quota_guard(self.operator_config),
        )
        decision = router.route(routing_request)
        if decision.action is RoutingAction.EMERGENCY_DISPATCH:
            _safe_local_path(
                self.project.root, ".galaxy/runtime/emergency-dispatches.json",
            )
            _safe_local_path(
                self.project.root, ".galaxy/runtime/emergency-dispatches.json.lock",
            )
            assert routing_request.task_id is not None
            if not self.emergency_state.try_claim(
                routing_request.task_id,
                router.emergency_policy.max_emergency_dispatches_per_task,
            ):
                decision = type(decision)(
                    RoutingAction.BLOCKED, None, "emergency dispatch limit reached",
                )
        route = (
            {"model": decision.route.model, "effort": decision.route.effort}
            if decision.route is not None else None
        )
        authorized = decision.action in {
            RoutingAction.DISPATCH, RoutingAction.EMERGENCY_DISPATCH,
        }
        emergency_record = None
        if decision.emergency_record is not None:
            emergency = decision.emergency_record
            emergency_record = {
                "reason": emergency.reason,
                "failure_evidence": emergency.failure_evidence,
                "previous_route": (
                    {"model": emergency.previous_route.model,
                     "effort": emergency.previous_route.effort}
                    if emergency.previous_route is not None else None
                ),
                "selected_route": {
                    "model": emergency.selected_route.model,
                    "effort": emergency.selected_route.effort,
                },
                "normal_next_route_skipped": emergency.normal_next_route_skipped,
                "quota_snapshot": {
                    "five_hour_remaining": emergency.quota_snapshot.five_hour_remaining,
                    "weekly_remaining": emergency.quota_snapshot.weekly_remaining,
                },
            }
        record = None
        if authorized:
            assert decision.route is not None
            _safe_local_path(
                self.project.root, ".galaxy/runtime/dispatch-verification.json",
            )
            requested = self.verifier.request(
                decision.route.model, decision.route.effort,
                metadata={
                    "action": decision.action.value,
                    "task_id": routing_request.task_id,
                    "profile": routing_request.profile.value,
                    "role": routing_request.role,
                    "task_class": routing_request.task_class,
                    "risk": routing_request.risk,
                    "specialist": routing_request.specialist,
                    "emergency_record": emergency_record,
                },
            )
            record = requested.to_dict()
        return {
            "authorized": authorized,
            "action": decision.action.value,
            "task_id": routing_request.task_id,
            "profile": routing_request.profile.value,
            "route": route,
            "reason": decision.reason,
            "failure_class": decision.failure_class.value if decision.failure_class else None,
            "quota_state": decision.quota_state,
            "dispatch": record,
            "emergency_record": emergency_record,
        }

    def verify(
        self, dispatch_id: str, *, spawn_id: str | None,
        effective_model: str | None, effective_effort: str | None,
        parent_thread: str | None = None,
    ) -> dict[str, Any]:
        _safe_local_path(self.project.root, ".galaxy/runtime/dispatch-verification.json")
        record = self.verifier.spawned(
            dispatch_id, spawn_id=spawn_id, parent_thread=parent_thread,
        )
        return self.verifier.verify(
            record, effective_model=effective_model, effective_effort=effective_effort,
        ).to_dict()

    @staticmethod
    def _fingerprint(value: Any):
        raw = _mapping(value, "review fingerprint")
        if set(raw) != _FINGERPRINT_FIELDS:
            raise ValueError("review fingerprint fields must be exact and complete")
        return ReviewCache.fingerprint(**raw)

    def review_lookup(self, fingerprint: Any) -> dict[str, Any]:
        _safe_local_path(self.project.root, ".galaxy/cache/reviews.json")
        value = self._fingerprint(fingerprint)
        entry = self.reviews.get(value)
        return {
            "fingerprint": value.value,
            "reusable": entry is not None,
            "entry": entry.to_dict() if entry is not None else None,
        }

    def review_record(self, fingerprint: Any, evidence: Any) -> dict[str, Any]:
        _safe_local_path(self.project.root, ".galaxy/cache/reviews.json")
        value = self._fingerprint(fingerprint)
        entry = self.reviews.put(value, evidence)
        return {
            "fingerprint": value.value,
            "reusable": True,
            "entry": entry.to_dict(),
        }


__all__ = ["DispatchCoordinator"]
