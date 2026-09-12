"""Deterministic resolution of user actions to available integrations."""

from .resolver import (
    ActionResolver,
    Capability,
    CapabilityInventory,
    Decision,
    ResolutionCode,
    github_capabilities,
    resolve_action,
)

__all__ = [
    "ActionResolver", "Capability", "CapabilityInventory", "Decision",
    "ResolutionCode", "github_capabilities", "resolve_action",
]
