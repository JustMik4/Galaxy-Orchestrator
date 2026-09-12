"""Conservative cleanup planning for Galaxy-created lifecycle artifacts.

Planning is read-only.  Applying a plan always requires ``apply=True`` and
only the targets validated by that plan may be changed.
"""

from .branches import (
    BranchCandidate, BranchCleanupPlan, LifecycleGitError,
    apply_branch_cleanup, branch_cleanup_plan, plan_branch_cleanup,
)
from .worktrees import (
    WorktreeCandidate, WorktreeCleanupPlan, apply_worktree_cleanup,
    plan_worktree_cleanup, worktree_cleanup_plan,
)
from .runtime import (
    RuntimeCandidate, RuntimeCleanupPlan, apply_runtime_cleanup,
    plan_runtime_cleanup, runtime_cleanup_plan,
)
from .manager import LifecycleManager, LifecyclePlan, apply_lifecycle, plan_lifecycle

__all__ = [
    "BranchCandidate", "BranchCleanupPlan", "LifecycleGitError",
    "branch_cleanup_plan", "plan_branch_cleanup", "apply_branch_cleanup",
    "WorktreeCandidate", "WorktreeCleanupPlan", "worktree_cleanup_plan",
    "plan_worktree_cleanup", "apply_worktree_cleanup",
    "RuntimeCandidate", "RuntimeCleanupPlan", "runtime_cleanup_plan",
    "plan_runtime_cleanup", "apply_runtime_cleanup", "LifecyclePlan",
    "LifecycleManager", "plan_lifecycle", "apply_lifecycle",
]
