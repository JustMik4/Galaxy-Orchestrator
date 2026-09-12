"""Safe, deterministic cleanup planning for agent branches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Iterable

from .validation import validate_retention_days


class LifecycleGitError(RuntimeError):
    """Git was unavailable or could not answer a lifecycle query."""


def _git(repo: Path, *args: str, check: bool = False) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=str(repo), text=True,
                                capture_output=True, check=False)
    except OSError as exc:
        raise LifecycleGitError(f"could not execute git: {exc}") from exc
    if check and result.returncode:
        raise LifecycleGitError(result.stderr.strip() or "git command failed")
    return result.stdout


def _utc_timestamp(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            return float(raw)
        except ValueError:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return default


def _branch_state(metadata: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    metadata = metadata or {}
    state: dict[str, Any] = {}
    for key in ("branches", "branch_states", "branch_metadata"):
        values = metadata.get(key)
        if isinstance(values, Mapping) and isinstance(values.get(name), Mapping):
            state.update(values[name])
    if isinstance(metadata.get(name), Mapping):
        state.update(metadata[name])
    return state


def _belongs(value: Any, name: str) -> bool:
    if isinstance(value, str):
        return value == name
    if isinstance(value, Mapping):
        return any(value.get(key) == name for key in ("branch", "name", "ref"))
    return False


def _listed(metadata: Mapping[str, Any] | None, keys: Iterable[str], name: str) -> bool:
    metadata = metadata or {}
    for key in keys:
        values = metadata.get(key, ())
        if isinstance(values, Mapping):
            if name in values and bool(values[name]):
                return True
            values = values.values()
        if isinstance(values, (str, Mapping)):
            values = (values,)
        try:
            if any(_belongs(item, name) for item in values):
                return True
        except TypeError:
            continue
    return False


@dataclass(frozen=True)
class BranchCandidate:
    name: str
    head: str
    last_activity: float
    merged: bool
    reachable: bool
    remote_state: str
    active: bool
    pending_recovery: bool
    dirty_worktree: bool
    retention_elapsed: bool
    safe: bool
    reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "safe" if self.safe else "unsafe"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "head": self.head, "last_activity": self.last_activity,
                "merged": self.merged, "reachable": self.reachable,
                "remote_state": self.remote_state, "active": self.active,
                "pending_recovery": self.pending_recovery,
                "dirty_worktree": self.dirty_worktree,
                "retention_elapsed": self.retention_elapsed, "safe": self.safe,
                "reasons": list(self.reasons)}


@dataclass(frozen=True)
class BranchCleanupPlan:
    repository: str
    integration_branch: str
    retention_days: float
    candidates: tuple[BranchCandidate, ...]
    preview: bool = True

    @property
    def safe_candidates(self) -> tuple[BranchCandidate, ...]:
        return tuple(item for item in self.candidates if item.safe)

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.safe_candidates)

    @property
    def fingerprint(self) -> str:
        payload = [(item.name, item.head) for item in self.safe_candidates]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"repository": self.repository, "integration_branch": self.integration_branch,
                "retention_days": self.retention_days, "preview": self.preview,
                "targets": list(self.targets), "fingerprint": self.fingerprint,
                "candidates": [item.to_dict() for item in self.candidates]}


def _worktree_dirty(repo: Path) -> dict[str, bool]:
    """Return dirty state by branch without touching any worktree."""
    result: dict[str, bool] = {}
    raw = _git(repo, "worktree", "list", "--porcelain")
    block: dict[str, str] = {}
    blocks = []
    for line in raw.splitlines() + [""]:
        if line:
            key, _, value = line.partition(" ")
            block[key] = value
        elif block:
            blocks.append(block)
            block = {}
    for item in blocks:
        branch_ref = item.get("branch", "")
        branch = branch_ref.removeprefix("refs/heads/")
        path = item.get("worktree")
        if branch and path:
            try:
                dirty = bool(_git(Path(path), "status", "--porcelain").strip())
            except LifecycleGitError:
                dirty = True
            result[branch] = dirty
    return result


def _remote_state(repo: Path, name: str, state: Mapping[str, Any]) -> str:
    explicit = state.get("remote_state")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    if state.get("open_pr") is True or state.get("pr_open") is True:
        return "open"
    # A repository with no remote has no PR state to query; local ancestry is
    # authoritative there.  A configured remote, however, is intentionally
    # unknown without an authenticated PR capability and is never deleted.
    remotes = _git(repo, "remote").split()
    return "unknown" if remotes else "none"


def plan_branch_cleanup(repo: str | Path, *, integration_branch: str = "main",
                        retention_days: float = 3, now: float | None = None,
                        metadata: Mapping[str, Any] | None = None,
                        include_all_agent_branches: bool = True) -> BranchCleanupPlan:
    """Inspect ``galaxy/*`` and legacy ``codex/*`` branches without mutation."""
    retention_days = validate_retention_days(retention_days)
    root = Path(repo).resolve()
    metadata = metadata or {}
    current = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    dirty = _worktree_dirty(root)
    integration_ref = f"refs/heads/{integration_branch}"
    integration_exists = bool(_git(root, "show-ref", "--verify", "--quiet", integration_ref,
                                    check=False) or False)
    # show-ref writes no stdout; use a direct return query for the existence bit.
    try:
        probe = subprocess.run(["git", "show-ref", "--verify", "--quiet", integration_ref],
                               cwd=str(root), capture_output=True, check=False)
        integration_exists = probe.returncode == 0
    except OSError as exc:
        raise LifecycleGitError(f"could not execute git: {exc}") from exc
    rows = _git(root, "for-each-ref", "refs/heads/", "--format=%(refname:short)%00%(objectname)%00%(committerdate:unix)")
    candidates: list[BranchCandidate] = []
    for row in rows.splitlines():
        fields = row.split("\x00")
        if len(fields) < 3:
            continue
        name, head, stamp = fields[:3]
        if not (name.startswith("galaxy/") or name.startswith("codex/")):
            continue
        state = _branch_state(metadata, name)
        last = _utc_timestamp(state.get("last_activity", state.get("updated_at", stamp)), float(stamp or 0))
        merged = False
        if integration_exists:
            try:
                merged = subprocess.run(["git", "merge-base", "--is-ancestor", head, integration_ref],
                                        cwd=str(root), capture_output=True, check=False).returncode == 0
            except OSError as exc:
                raise LifecycleGitError(f"could not execute git: {exc}") from exc
        reachable = merged
        if state.get("merged") is True:
            merged = True
        if state.get("reachable") is False:
            reachable = False
        remote = _remote_state(root, name, state)
        active = bool(state.get("active", False)) or _listed(metadata, ("active", "active_tasks", "ownership", "task_ownership"), name)
        pending = bool(state.get("pending_recovery", False)) or _listed(metadata, ("pending_recovery", "pending_recoveries", "recovery"), name)
        dirty_worktree = bool(dirty.get(name, False)) or bool(state.get("dirty_worktree", False))
        retention = last <= current - retention_days * 86400
        reasons: list[str] = []
        if not merged: reasons.append("not-merged")
        if not reachable: reasons.append("head-not-reachable")
        if remote in {"open", "unknown", "pending"}: reasons.append(f"remote-{remote}")
        if active: reasons.append("active-task")
        if pending: reasons.append("pending-recovery")
        if dirty_worktree: reasons.append("dirty-worktree")
        if not retention: reasons.append("retention-not-elapsed")
        safe = not reasons
        candidates.append(BranchCandidate(name, head, last, merged, reachable, remote,
                                          active, pending, dirty_worktree, retention, safe,
                                          tuple(reasons)))
    return BranchCleanupPlan(str(root), integration_branch, retention_days,
                             tuple(sorted(candidates, key=lambda item: item.name)), True)


branch_cleanup_plan = plan_branch_cleanup


def apply_branch_cleanup(plan: BranchCleanupPlan, *, apply: bool = False,
                         targets: Iterable[str] | None = None) -> dict[str, Any]:
    """Delete exactly the safe branches from a previously validated plan."""
    if not apply:
        return {"applied": False, "deleted": [], "targets": list(plan.targets)}
    expected = tuple(plan.targets)
    requested = expected if targets is None else tuple(targets)
    if requested != expected:
        raise ValueError("apply targets must exactly match the validated plan")
    deleted: list[str] = []
    for candidate in plan.safe_candidates:
        current_head = _git(Path(plan.repository), "rev-parse", "--verify",
                            f"refs/heads/{candidate.name}", check=True).strip()
        if current_head != candidate.head:
            raise LifecycleGitError(
                f"branch changed after preview: {candidate.name}"
            )
        # -d (not -D) preserves a branch if the repository changed since plan.
        result = subprocess.run(["git", "branch", "-d", candidate.name], cwd=plan.repository,
                                text=True, capture_output=True, check=False)
        if result.returncode:
            raise LifecycleGitError(result.stderr.strip() or f"could not delete {candidate.name}")
        deleted.append(candidate.name)
    return {"applied": True, "deleted": deleted, "targets": list(expected)}
