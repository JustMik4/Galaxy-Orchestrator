"""Read-only worktree inspection and conservative cleanup application."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

from .branches import LifecycleGitError, _git, _listed


def _state(metadata: Mapping[str, Any] | None, branch: str, path: str) -> dict[str, Any]:
    metadata = metadata or {}
    result: dict[str, Any] = {}
    for key in ("worktrees", "worktree_states"):
        values = metadata.get(key)
        if isinstance(values, Mapping):
            for lookup in (branch, path):
                if isinstance(values.get(lookup), Mapping):
                    result.update(values[lookup])
    return result


def _parse_worktrees(raw: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines() + [""]:
        if line:
            key, _, value = line.partition(" ")
            current[key] = value
        elif current:
            blocks.append(current)
            current = {}
    return blocks


@dataclass(frozen=True)
class WorktreeCandidate:
    path: str
    branch: str | None
    head: str | None
    exists: bool
    dirty: bool
    branch_exists: bool
    completed: bool
    stale: bool
    safe: bool
    reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.dirty:
            return "dirty"
        if self.stale:
            return "stale"
        if not self.branch_exists:
            return "missing-branch"
        if self.completed:
            return "completed"
        return "active"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "branch": self.branch, "head": self.head,
                "exists": self.exists, "dirty": self.dirty,
                "branch_exists": self.branch_exists, "completed": self.completed,
                "stale": self.stale, "safe": self.safe, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class WorktreeCleanupPlan:
    repository: str
    candidates: tuple[WorktreeCandidate, ...]
    preview: bool = True

    @property
    def safe_candidates(self) -> tuple[WorktreeCandidate, ...]:
        return tuple(item for item in self.candidates if item.safe)

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.safe_candidates)

    @property
    def fingerprint(self) -> str:
        data = [(item.path, item.branch, item.head) for item in self.safe_candidates]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"repository": self.repository, "preview": self.preview,
                "targets": list(self.targets), "fingerprint": self.fingerprint,
                "candidates": [item.to_dict() for item in self.candidates]}


def plan_worktree_cleanup(repo: str | Path, *, metadata: Mapping[str, Any] | None = None,
                          include_completed: bool = True) -> WorktreeCleanupPlan:
    """Identify stale, missing-branch, and completed agent worktrees.

    Dirty worktrees are always retained.  A path which no longer exists is
    reported as stale but is not considered safe: its final dirty state cannot
    be established from the current host.
    """
    root = Path(repo).resolve()
    metadata = metadata or {}
    local_branches = set(_git(root, "for-each-ref", "refs/heads/", "--format=%(refname:short)").splitlines())
    candidates: list[WorktreeCandidate] = []
    for item in _parse_worktrees(_git(root, "worktree", "list", "--porcelain")):
        path_value = item.get("worktree")
        if not path_value:
            continue
        path = Path(path_value).resolve()
        branch_ref = item.get("branch", "")
        branch = branch_ref.removeprefix("refs/heads/") or None
        # Only agent-created branches are lifecycle-owned.  The main checkout
        # and user worktrees are intentionally omitted from cleanup plans.
        agent_branch = bool(branch and (branch.startswith("galaxy/") or branch.startswith("codex/")))
        if not agent_branch:
            continue
        exists = path.exists()
        state = _state(metadata, branch or "", str(path))
        dirty = bool(state.get("dirty", False))
        if exists:
            try:
                dirty = bool(_git(path, "status", "--porcelain").strip()) or dirty
            except LifecycleGitError:
                dirty = True
        branch_exists = bool(branch and branch in local_branches)
        completed = bool(state.get("completed", False) or state.get("status") in ("completed", "complete", "merged"))
        completed = completed or _listed(metadata, ("completed_tasks", "completed_worktrees"), branch or "")
        stale = not exists
        reasons: list[str] = []
        if stale: reasons.append("missing-worktree")
        if not branch_exists: reasons.append("missing-branch")
        if include_completed and completed: reasons.append("completed-task")
        if dirty: reasons.append("dirty-worktree")
        # Missing paths are unsafe because dirtiness cannot be proven.  Missing
        # branches with a present, clean worktree can be removed safely.
        safe = bool((not dirty) and exists and (not branch_exists or completed))
        if not exists and "missing-worktree-state-unknown" not in reasons:
            reasons.append("missing-worktree-state-unknown")
        candidates.append(WorktreeCandidate(str(path), branch, item.get("HEAD"), exists, dirty,
                                             branch_exists, completed, stale, safe, tuple(reasons)))
    return WorktreeCleanupPlan(str(root), tuple(sorted(candidates, key=lambda item: item.path)), True)


worktree_cleanup_plan = plan_worktree_cleanup


def apply_worktree_cleanup(plan: WorktreeCleanupPlan, *, apply: bool = False,
                           targets: Iterable[str] | None = None) -> dict[str, Any]:
    if not apply:
        return {"applied": False, "removed": [], "targets": list(plan.targets)}
    expected = tuple(plan.targets)
    requested = expected if targets is None else tuple(targets)
    if requested != expected:
        raise ValueError("apply targets must exactly match the validated plan")
    removed: list[str] = []
    for candidate in plan.safe_candidates:
        result = subprocess.run(["git", "worktree", "remove", candidate.path], cwd=plan.repository,
                                text=True, capture_output=True, check=False)
        if result.returncode:
            raise LifecycleGitError(result.stderr.strip() or f"could not remove {candidate.path}")
        removed.append(candidate.path)
    return {"applied": True, "removed": removed, "targets": list(expected)}

