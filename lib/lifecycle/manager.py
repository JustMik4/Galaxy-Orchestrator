"""Facade combining branch, worktree, and runtime lifecycle plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .branches import BranchCleanupPlan, apply_branch_cleanup, plan_branch_cleanup
from .worktrees import WorktreeCleanupPlan, apply_worktree_cleanup, plan_worktree_cleanup
from .runtime import RuntimeCleanupPlan, apply_runtime_cleanup, plan_runtime_cleanup
from .validation import validate_retention_days


@dataclass(frozen=True)
class LifecyclePlan:
    branches: BranchCleanupPlan
    worktrees: WorktreeCleanupPlan
    runtime: RuntimeCleanupPlan
    preview: bool = True

    @property
    def targets(self) -> dict[str, tuple[str, ...]]:
        return {"branches": self.branches.targets, "worktrees": self.worktrees.targets,
                "runtime": self.runtime.targets}

    def to_dict(self) -> dict[str, Any]:
        return {"preview": self.preview, "targets": {key: list(value) for key, value in self.targets.items()},
                "branches": self.branches.to_dict(), "worktrees": self.worktrees.to_dict(),
                "runtime": self.runtime.to_dict()}


def plan_lifecycle(repo: str, *, integration_branch: str = "main", retention_days: float = 3,
                   now: float | None = None, metadata: Mapping[str, Any] | None = None,
                   runtime_paths: Iterable[str] | None = None,
                   current_audit_paths: Iterable[str] = ()) -> LifecyclePlan:
    """Build a completely read-only lifecycle plan (preview by default)."""
    retention_days = validate_retention_days(retention_days)
    return LifecyclePlan(
        plan_branch_cleanup(repo, integration_branch=integration_branch,
                            retention_days=retention_days, now=now, metadata=metadata),
        plan_worktree_cleanup(repo, metadata=metadata),
        plan_runtime_cleanup(repo, paths=runtime_paths, retention_days=retention_days,
                             now=now, current_audit_paths=current_audit_paths),
        True,
    )


def apply_lifecycle(plan: LifecyclePlan, *, apply: bool = False,
                    targets: Mapping[str, Iterable[str]] | None = None) -> dict[str, Any]:
    """Apply exactly the targets in a validated plan when explicitly enabled."""
    if not apply:
        return {"applied": False, "targets": {key: list(value) for key, value in plan.targets.items()}}
    targets = targets or {}
    result = {}
    # Worktrees must be detached before deleting branches they still reference.
    for key, fn, child in (("worktrees", apply_worktree_cleanup, plan.worktrees),
                           ("branches", apply_branch_cleanup, plan.branches),
                           ("runtime", apply_runtime_cleanup, plan.runtime)):
        result[key] = fn(child, apply=True, targets=targets.get(key, child.targets))
    return {"applied": True, **result}


class LifecycleManager:
    """Small object-oriented facade for integrations that keep a manager.

    It intentionally has no background thread or implicit mutation; ``plan``
    is always a preview and callers must pass ``apply=True`` to ``apply``.
    """

    def __init__(self, repository: str, **defaults: Any):
        self.repository = repository
        self.defaults = dict(defaults)

    def plan(self, **overrides: Any) -> LifecyclePlan:
        options = dict(self.defaults)
        options.update(overrides)
        return plan_lifecycle(self.repository, **options)

    def apply(self, plan: LifecyclePlan, *, apply: bool = False,
              targets: Mapping[str, Iterable[str]] | None = None) -> dict[str, Any]:
        return apply_lifecycle(plan, apply=apply, targets=targets)
