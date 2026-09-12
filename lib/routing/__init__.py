"""Capability-aware routing primitives for Galaxy Orchestrator."""

from .catalog import CapabilityCatalog, ModelCapability, Route
from .router import (
    CapabilityRouter,
    EmergencyRecord,
    EmergencyPolicy,
    FailureClass,
    PROFILE_POLICIES,
    ProfilePolicy,
    RouteProfile,
    RoutingAction,
    RoutingDecision,
    RoutingRequest,
)

__all__ = [
    "CapabilityCatalog",
    "CapabilityRouter",
    "EmergencyRecord",
    "EmergencyPolicy",
    "FailureClass",
    "ModelCapability",
    "PROFILE_POLICIES",
    "ProfilePolicy",
    "Route",
    "RouteProfile",
    "RoutingAction",
    "RoutingDecision",
    "RoutingRequest",
]
