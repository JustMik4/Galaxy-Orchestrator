"""Deterministic resolution of user actions to available integrations."""

from .resolver import (
    ActionResolver,
    ApprovalGrant,
    Capability,
    CapabilityInventory,
    Decision,
    ResolutionCode,
    github_capabilities,
    resolve_action,
)

__all__ = [
    "ActionResolver", "ApprovalGrant", "Capability", "CapabilityInventory", "Decision",
    "ResolutionCode", "github_capabilities", "resolve_action",
]
