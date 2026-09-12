"""Read-only health checks for Galaxy projects.

The doctor deliberately has no repair side effects.  It produces a stable list
of check records so the CLI and CI can render the same result as a human.
Unknown host telemetry is represented as ``UNKNOWN``; it is never converted to
an invented zero, one hundred, or successful capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
from typing import Any, Iterable, Mapping


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


PASS = CheckStatus.PASS
WARN = CheckStatus.WARN
FAIL = CheckStatus.FAIL
UNKNOWN = CheckStatus.UNKNOWN


REMEDIATIONS = {
    "declarations": "run `galaxy init PATH` or `galaxy migrate PATH --preview`",
    "generated": "run `galaxy migrate PATH --preview` and review generated-file untracking",
    "legacy": "run `galaxy migrate PATH --preview`",
    "bootstrap": "run `galaxy bootstrap PATH --check` and resolve drift",
    "checks": "configure project checks in `.galaxy/checks.json`",
    "telemetry": "enable host runtime telemetry and rerun `galaxy doctor`",
    "quota": "refresh host quota telemetry; do not infer quota from token counts",
    "action": "approve browser fallback explicitly or configure connector/plugin/API/CLI",
    "coop": "configure `.galaxy/team.yml` coordination for serialized CO-OP state",
    "vault": "run `galaxy vault sync --check` and resolve drift before `--force`",
    "lifecycle": "review candidates with `galaxy cleanup --preview`; apply only after validation",
    "env": "review and consolidate `.env.example`/`env.example`; do not auto-delete",
}


@dataclass(frozen=True)
class CheckRecord:
    """One deterministic Doctor observation."""

    id: str
    status: CheckStatus
    message: str
    remediation: str | None = None
    details: Mapping[str, Any] = ()

    @property
    def name(self) -> str:
        return self.id

    @property
    def check_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        details = dict(self.details) if isinstance(self.details, Mapping) else {}
        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message,
            "remediation": self.remediation,
            "details": details,
        }


@dataclass(frozen=True)
class DoctorReport:
    project_root: str
    checks: tuple[CheckRecord, ...]
    galaxy_version: str | None = None

    @property
    def failed(self) -> tuple[CheckRecord, ...]:
        return tuple(item for item in self.checks if item.status is CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[CheckRecord, ...]:
        return tuple(item for item in self.checks if item.status is CheckStatus.WARN)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    @property
    def status(self) -> CheckStatus:
        if self.failed:
            return CheckStatus.FAIL
        if self.warnings:
            return CheckStatus.WARN
        return CheckStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        counts = {status.value: sum(item.status is status for item in self.checks) for status in CheckStatus}
        return {
            "project_root": self.project_root,
            "galaxy_version": self.galaxy_version,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "counts": counts,
            "checks": [item.to_dict() for item in self.checks],
        }

    def json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def render(self) -> str:
        lines = ["Galaxy Doctor"]
        if self.galaxy_version:
            lines.append(f"{_symbol(CheckStatus.PASS)} Galaxy {self.galaxy_version}")
        for item in self.checks:
            lines.append(f"{_symbol(item.status)} {item.message}")
            if item.remediation:
                lines.append(f"  fix: {item.remediation}")
        return "\n".join(lines) + "\n"

    # Common CLI spelling.
    text = render


def _symbol(status: CheckStatus) -> str:
    return {CheckStatus.PASS: "✓", CheckStatus.WARN: "⚠", CheckStatus.FAIL: "✗", CheckStatus.UNKNOWN: "?"}[status]


def _record(checks: list[CheckRecord], ident: str, status: CheckStatus, message: str,
            remediation: str | None = None, details: Mapping[str, Any] | None = None) -> None:
    checks.append(CheckRecord(ident, CheckStatus(status), message, remediation,
                              dict(details or {})))


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git"], 127, b"", str(exc).encode())


def _git_ignored(root: Path, relative: str) -> bool | None:
    result = _run_git(root, "check-ignore", "-q", "--", relative)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _value(source: Any, names: Iterable[str]) -> Any:
    if source is None:
        return None
    for name in names:
        value = source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)
        if value is not None and value != "":
            return value
    for nested_name in ("quota", "usage", "telemetry", "effective", "runtime"):
        nested = source.get(nested_name) if isinstance(source, Mapping) else getattr(source, nested_name, None)
        if nested is not None:
            value = _value(nested, names)
            if value is not None:
                return value
    return None


def _percent(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("remaining quota must be a percentage from 0 to 100")
    if not 0 <= result <= 100:
        raise ValueError("remaining quota must be a percentage from 0 to 100")
    return result


def _check_declarations(root: Path, checks: list[CheckRecord]):
    """Load declarations independently so missing files have useful IDs."""
    from lib.project import (load_checks, load_lock, load_project_config, load_team,
                             load_project, ProjectConfigurationError)

    paths = {
        "project-schema": (root / ".galaxy" / "project.yml", load_project_config),
        "team-schema": (root / ".galaxy" / "team.yml", load_team),
        "checks-schema": (root / ".galaxy" / "checks.json", load_checks),
        "lock-schema": (root / "galaxy.lock", load_lock),
    }
    loaded: dict[str, Any] = {}
    for ident, (path, loader) in paths.items():
        try:
            loaded[ident] = loader(path)
            _record(checks, ident, PASS, f"{path.name} schema valid")
        except (OSError, ValueError, ProjectConfigurationError) as exc:
            _record(checks, ident, FAIL, f"{path.name} schema invalid: {exc}", REMEDIATIONS["declarations"])
    project = None
    if len(loaded) == 4:
        try:
            project = load_project(root)
            _record(checks, "declarations-consistency", PASS, "project declarations and galaxy.lock consistent")
        except (OSError, ValueError, ProjectConfigurationError) as exc:
            _record(checks, "declarations-consistency", FAIL, f"project declarations inconsistent: {exc}", REMEDIATIONS["declarations"])
    return project, loaded


def _check_version(root: Path, project: Any, loaded: Mapping[str, Any], checks: list[CheckRecord], expected: str | None) -> str | None:
    lock = loaded.get("lock-schema")
    version = getattr(lock, "galaxy_version", None)
    if expected is None:
        try:
            expected = (Path(__file__).resolve().parents[1] / "VERSION").read_text(
                encoding="utf-8"
            ).strip()
        except (OSError, UnicodeError):
            _record(checks, "version-lock", UNKNOWN,
                    "installed Galaxy version cannot be verified", REMEDIATIONS["telemetry"])
            return version
        if not expected:
            _record(checks, "version-lock", UNKNOWN,
                    "installed Galaxy VERSION is empty", REMEDIATIONS["telemetry"])
            return version
    if version == expected:
        _record(checks, "version-lock", PASS, f"Galaxy {expected} matches galaxy.lock")
    else:
        _record(checks, "version-lock", FAIL, f"Galaxy {expected} differs from galaxy.lock ({version or 'missing'})", REMEDIATIONS["bootstrap"])
    return expected


def _check_generated(root: Path, project: Any, checks: list[CheckRecord]) -> None:
    config = root / ".codex" / "config.toml"
    if not config.exists():
        _record(checks, "codex-config", UNKNOWN, "generated .codex/config.toml is not present", REMEDIATIONS["bootstrap"])
        _record(checks, "root-policy", UNKNOWN, "default root policy cannot be checked without generated Codex config", REMEDIATIONS["bootstrap"])
        _record(checks, "generated-freshness", UNKNOWN, "generated .codex freshness cannot be checked because artifacts are absent", REMEDIATIONS["bootstrap"])
        _record(checks, "specialist-index", UNKNOWN, "generated specialist index is not present", REMEDIATIONS["bootstrap"])
        return
    try:
        import tomllib
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        _record(checks, "codex-config", FAIL, f"generated .codex/config.toml is invalid: {exc}", REMEDIATIONS["bootstrap"])
        return
    _record(checks, "codex-config", PASS, "generated .codex/config.toml is valid")
    model = data.get("model")
    effort = data.get("model_reasoning_effort", data.get("reasoning_effort"))
    if model != "gpt-5.6-sol" or effort != "medium":
        _record(checks, "root-policy", FAIL, f"default root must be Sol Medium (found {model or 'missing'}/{effort or 'missing'})", REMEDIATIONS["bootstrap"])
    else:
        _record(checks, "root-policy", PASS, "default root = Sol Medium")
    try:
        from lib.bootstrap import bootstrap_plan
        plan = bootstrap_plan(root)
        pending = tuple(sorted((*plan.create, *plan.update, *plan.remove, *plan.drift)))
    except (OSError, ValueError, RuntimeError) as exc:
        _record(checks, "generated-freshness", FAIL,
                f"generated artifacts cannot be verified against galaxy.lock: {exc}",
                REMEDIATIONS["bootstrap"])
    else:
        _record(
            checks,
            "generated-freshness",
            PASS if not pending else WARN,
            "generated .codex artifacts match galaxy.lock"
            if not pending else "generated artifacts differ from galaxy.lock",
            None if not pending else REMEDIATIONS["bootstrap"],
            {"paths": list(pending)},
        )

    index = root / ".codex" / "galaxy-specialists.json"
    if not index.exists():
        _record(checks, "specialist-index", UNKNOWN, "generated specialist index is not present", REMEDIATIONS["bootstrap"])
    else:
        try:
            value = json.loads(index.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("specialists"), list):
                raise ValueError("specialists must be a list")
            names = [item.get("name") for item in value["specialists"] if isinstance(item, dict)]
            if len(names) != len(value["specialists"]) or len(names) != len(set(names)):
                raise ValueError("specialist entries must have unique names")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            _record(checks, "specialist-index", FAIL, f"specialist index invalid: {exc}", REMEDIATIONS["bootstrap"])
        else:
            _record(checks, "specialist-index", PASS, "specialist index valid", details={"count": len(names)})


def _check_git_pollution(root: Path, checks: list[CheckRecord], generated_policy: str) -> None:
    from lib.project import project_pollution
    pollution = project_pollution(root)
    if not pollution.git_available:
        _record(checks, "git-status", UNKNOWN, "Git tracked-file status is unavailable", REMEDIATIONS["generated"], {"error": pollution.error or "unknown"})
        return
    _record(checks, "git-status", PASS, "Git tracked-file status available")
    if pollution.tracked_generated:
        status = FAIL if generated_policy == "fail" else WARN
        _record(checks, "tracked-generated", status,
                f"{len(pollution.tracked_generated)} generated Galaxy artifact(s) tracked by Git",
                REMEDIATIONS["generated"], {"paths": list(pollution.tracked_generated)})
    else:
        _record(checks, "tracked-generated", PASS, "generated Galaxy artifacts are not tracked")


def _check_legacy(root: Path, checks: list[CheckRecord]) -> None:
    candidates = []
    for relative in ("AGENT_TEAM.yml", ".multicontroller", ".agents/skills/multicontroller"):
        if (root / PurePosixPath(relative)).exists():
            candidates.append(relative)
    if candidates:
        _record(checks, "legacy-v1", WARN, "legacy V1 artifacts detected: " + ", ".join(candidates), REMEDIATIONS["legacy"], {"paths": candidates})
    else:
        _record(checks, "legacy-v1", PASS, "no legacy V1 project artifacts detected")


def _check_hygiene(root: Path, checks: list[CheckRecord]) -> None:
    left, right = root / ".env.example", root / "env.example"
    if left.is_file() and right.is_file() and left.read_bytes() != right.read_bytes():
        _record(checks, "duplicate-env-templates", WARN, "multiple environment templates may be divergent sources of truth", REMEDIATIONS["env"], {"paths": [".env.example", "env.example"]})
    else:
        _record(checks, "duplicate-env-templates", PASS, "no divergent duplicate environment templates detected")
    ignored = _git_ignored(root, ".env")
    if ignored is True:
        _record(checks, "env-ignored", PASS, ".env is ignored by Git")
    elif ignored is False:
        _record(checks, "env-ignored", FAIL, ".env is not ignored by Git", "add `.env` to `.gitignore` and remove any tracked secret file")
    else:
        _record(checks, "env-ignored", UNKNOWN, "cannot verify .env Git ignore rule", "configure Git ignore rules before storing local credentials")


def _check_runtime(runtime: Any, verifier: Any, checks: list[CheckRecord]) -> None:
    if verifier is None and runtime is None:
        _record(checks, "runtime-verification", UNKNOWN, "runtime model/effort verification telemetry unavailable", REMEDIATIONS["telemetry"])
        return
    if runtime is not None:
        model = _value(runtime, ("effective_model", "actual_model", "runtime_model", "model"))
        effort = _value(runtime, ("effective_effort", "actual_effort", "reasoning_effort", "effort"))
        if model is None or effort is None:
            _record(checks, "runtime-verification", UNKNOWN, "runtime telemetry does not include effective model and effort", REMEDIATIONS["telemetry"])
        else:
            _record(checks, "runtime-verification", PASS, "runtime model/effort verification available")
        return
    _record(checks, "runtime-verification", PASS, "runtime model/effort verifier available")


def _check_quota(telemetry: Any, snapshot: Any, guard: Any, checks: list[CheckRecord]) -> None:
    from lib.quota import QuotaGuard, QuotaSnapshot, QuotaState
    if snapshot is None and telemetry is None:
        _record(checks, "quota-telemetry", UNKNOWN, "quota telemetry unavailable", REMEDIATIONS["quota"])
        return
    try:
        if snapshot is None:
            snapshot = QuotaSnapshot(
                _percent(_value(telemetry, ("five_hour_remaining", "five_hour", "fiveHourRemaining"))),
                _percent(_value(telemetry, ("weekly_remaining", "weekly", "weeklyRemaining"))),
            )
        if not isinstance(snapshot, QuotaSnapshot):
            raise ValueError("quota snapshot must be a QuotaSnapshot")
        guard = guard or QuotaGuard()
        state = guard.state(snapshot)
    except (TypeError, ValueError) as exc:
        _record(checks, "quota-telemetry", FAIL, f"quota telemetry invalid: {exc}", REMEDIATIONS["quota"])
        return
    details = {"five_hour_remaining": snapshot.five_hour_remaining, "weekly_remaining": snapshot.weekly_remaining, "state": state.value}
    if state is QuotaState.HARD_STOP:
        _record(checks, "quota-state", FAIL, "quota hard stop reached; only cleanup/handoff allowed", REMEDIATIONS["quota"], details)
    elif state is QuotaState.UNKNOWN:
        _record(checks, "quota-state", UNKNOWN, "quota telemetry incomplete; state is unknown", REMEDIATIONS["quota"], details)
    elif state in (QuotaState.CONSERVATIVE, QuotaState.ECONOMY):
        _record(checks, "quota-state", WARN, f"quota state is {state.value}", REMEDIATIONS["quota"], details)
    else:
        _record(checks, "quota-state", PASS, "quota thresholds and state are healthy", details=details)


def _check_action(resolver: Any, capabilities: Any, approval: bool, checks: list[CheckRecord]) -> None:
    from lib.actions.resolver import ActionResolver, ResolutionCode
    if resolver is None and capabilities is None:
        _record(checks, "action-capability", UNKNOWN, "GitHub repository action capability was not supplied", REMEDIATIONS["action"])
        return
    try:
        resolver = resolver or ActionResolver(capabilities or ())
        decision = resolver.resolve("github.repository.create", approval=approval)
    except (TypeError, ValueError, AttributeError) as exc:
        _record(checks, "action-capability", FAIL, f"Action Resolver unavailable: {exc}", REMEDIATIONS["action"])
        return
    if decision.code == ResolutionCode.SELECTED:
        kind = getattr(decision.capability, "kind", "unknown")
        if kind == "browser":
            _record(checks, "action-capability", PASS, "GitHub repository creation uses explicitly approved browser fallback", details={"kind": kind})
        else:
            _record(checks, "action-capability", PASS, f"Action Resolver available for github.repository.create via {kind}", details={"kind": kind})
    elif decision.reason == "approval-required":
        _record(checks, "action-capability", WARN, "browser-only GitHub repository creation requires explicit approval", REMEDIATIONS["action"])
    else:
        _record(checks, "action-capability", FAIL, "no usable capability for github.repository.create", REMEDIATIONS["action"])


def _read_coop_workflow(root: Path, relative: str) -> str:
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("coordination workflow must be project-relative")
    current = root
    for part in pure.parts:
        current /= part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if current.is_symlink() or bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ValueError("coordination workflow path contains a link/reparse point")
    return current.read_text(encoding="utf-8")


def _check_coop(root: Path, project: Any, checks: list[CheckRecord]) -> None:
    if project is None:
        return
    if project.team.mode == "SOLO":
        _record(checks, "coop-coordination", PASS, "SOLO coordination does not require a serialized remote coordinator")
        return
    raw = project.team.raw
    coordination = raw.get("coordination") or raw.get("coordinator")
    missing = []
    if not isinstance(coordination, Mapping):
        coordination = {}
        missing.append("coordination")
    if coordination.get("backend") != "github-actions-issue":
        missing.append("backend")
    if coordination.get("claim_protocol") != "serialized-workflow":
        missing.append("claim_protocol")
    issue = coordination.get("control_issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        missing.append("control_issue")
    if coordination.get("capability") != "github-actions":
        missing.append("capability")
    workflow_relative = coordination.get("workflow")
    if workflow_relative != ".github/workflows/galaxy-control.yml":
        missing.append("workflow")
    branch = raw.get("integration_branch")
    if not isinstance(branch, str) or not branch.strip():
        missing.append("integration_branch")
    required_checks = raw.get("required_checks")
    if not isinstance(required_checks, list) or not required_checks or any(
        not isinstance(item, str) or not item.strip() for item in required_checks
    ) or "galaxy / validate" not in required_checks:
        missing.append("required_checks")
    operators = raw.get("operators")
    operator_ids = []
    logins = []
    if isinstance(operators, list):
        for item in operators:
            if isinstance(item, Mapping):
                operator_ids.append(item.get("id"))
                login = item.get("github_login")
                logins.append(login.casefold() if isinstance(login, str) else login)
    if (
        not operator_ids or len(operator_ids) != len(operators)
        or any(not isinstance(item, str) or not item for item in operator_ids + logins)
        or len(set(operator_ids)) != len(operator_ids) or len(set(logins)) != len(logins)
    ):
        missing.append("operators")
    integrators = raw.get("integration_operators")
    if (
        not isinstance(integrators, list) or not integrators
        or any(item not in operator_ids for item in integrators)
        or len(set(integrators)) != len(integrators)
    ):
        missing.append("integration_operators")
    if workflow_relative == ".github/workflows/galaxy-control.yml":
        try:
            workflow = _read_coop_workflow(root, workflow_relative)
            required_workflow = (
                "workflow_dispatch:", "group: galaxy-control-v2",
                "contents: write", "issues: write", "pull-requests: write",
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                "github.event.repository.default_branch", "persist-credentials: false",
                "GH_TOKEN: ${{ github.token }}", "galaxy.lock", "source_revision",
                "https://github.com/JustMik4/Galaxy-Orchestrator",
                "^[0-9a-fA-F]{40}$", "FETCH_HEAD", "rev-parse",
                "-m lib.coordinator",
            )
            if any(marker not in workflow for marker in required_workflow):
                missing.append("workflow-capability")
        except (OSError, UnicodeError, ValueError):
            missing.append("workflow")
    details = {
        "backend": coordination.get("backend"),
        "capability": coordination.get("capability"),
        "control_issue": issue,
        "workflow": workflow_relative,
        "missing": sorted(set(missing)),
    }
    if missing:
        _record(checks, "coop-coordination", FAIL,
                "CO-OP serialized coordination is incomplete or incoherent",
                REMEDIATIONS["coop"], details)
    else:
        _record(checks, "coop-coordination", PASS,
                "CO-OP serialized coordination workflow and capability are configured",
                details=details)


def _check_vault(project: Any, root: Path, snapshots: Iterable[Mapping[str, Any]], checks: list[CheckRecord]) -> None:
    if project is None:
        return
    config = dict(project.config.vault)
    if not config.get("enabled", False):
        _record(checks, "vault", PASS, "Obsidian vault disabled (optional)")
        return
    try:
        from lib.vault import status
        state = status(root, config, snapshots)
    except (OSError, ValueError) as exc:
        _record(checks, "vault", FAIL, f"vault configuration/status invalid: {exc}", REMEDIATIONS["vault"])
        return
    if state.get("drift"):
        _record(checks, "vault", WARN, "vault configuration is enabled and has drift", REMEDIATIONS["vault"], {"path": state.get("path"), "drift": sorted(state["drift"])})
    else:
        _record(checks, "vault", PASS, "vault configuration and projection are in sync", details={"path": state.get("path")})
    vault_path = Path(state["path"])
    suspicious = []
    if vault_path.is_dir():
        for item in sorted(vault_path.rglob("*"), key=lambda p: p.as_posix().casefold()):
            if item.name.casefold() in {".env", "env", "secrets", "secret", "credentials", "credential"} or any(word in item.name.casefold() for word in ("token", "password", "apikey", "api-key")):
                suspicious.append(item.relative_to(vault_path).as_posix())
    if suspicious:
        _record(checks, "vault-secret-names", WARN, "vault contains suspicious secret-like filenames", "remove or exclude secret material from the vault", {"paths": suspicious})
    else:
        _record(checks, "vault-secret-names", PASS, "no suspicious secret-like vault filenames detected")


def _check_lifecycle(root: Path, metadata: Mapping[str, Any] | None, now: float | None, checks: list[CheckRecord]) -> None:
    probe = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if probe.returncode or probe.stdout.strip() != b"true":
        _record(checks, "lifecycle-candidates", UNKNOWN,
                "lifecycle candidates cannot be evaluated outside a Git work tree",
                REMEDIATIONS["lifecycle"])
        return
    try:
        from lib.lifecycle import plan_lifecycle
        plan = plan_lifecycle(str(root), metadata=metadata or {}, now=now)
        branches = [item.name for item in plan.branches.safe_candidates if item.name.startswith(("galaxy/", "codex/"))]
        runtime = [item.path for item in plan.runtime.safe_candidates]
        if branches or runtime:
            _record(checks, "lifecycle-candidates", WARN, f"{len(branches)} stale agent branch candidate(s) and {len(runtime)} runtime candidate(s) found", REMEDIATIONS["lifecycle"], {"branches": branches, "runtime": runtime})
        else:
            _record(checks, "lifecycle-candidates", PASS, "no stale lifecycle cleanup candidates detected")
    except (OSError, ValueError, RuntimeError) as exc:
        _record(checks, "lifecycle-candidates", UNKNOWN, f"lifecycle candidates cannot be evaluated: {exc}", REMEDIATIONS["lifecycle"])


def run_doctor(project_root: str | Path, *, galaxy_version: str | None = None,
               runtime: Any = None, runtime_telemetry: Any = None,
               runtime_verifier: Any = None, quota: Any = None,
               quota_telemetry: Any = None, quota_snapshot: Any = None,
               quota_guard: Any = None, action_resolver: Any = None,
               capabilities: Any = None, action_approval: bool = False,
               lifecycle_metadata: Mapping[str, Any] | None = None,
               now: float | None = None, snapshots: Iterable[Mapping[str, Any]] = (),
               generated_policy: str = "fail", **options: Any) -> DoctorReport:
    """Run all safe Doctor checks for *project_root*.

    Aliases in ``options`` (``telemetry``, ``usage``, ``actions``, ``approval``
    and ``metadata``) are accepted for small CLI integrations.  They do not
    change the deterministic check order.
    """
    root = Path(project_root).resolve()
    if runtime is None:
        runtime = runtime_telemetry if runtime_telemetry is not None else options.get("telemetry")
    if quota is None:
        quota = quota_telemetry if quota_telemetry is not None else options.get("usage")
    if action_resolver is None:
        action_resolver = options.get("actions")
    if lifecycle_metadata is None:
        lifecycle_metadata = options.get("metadata")
    if "approval" in options:
        action_approval = bool(options["approval"])
    generated_policy = "warn" if options.get("strict_generated") is False else generated_policy
    if generated_policy not in ("fail", "warn"):
        raise ValueError("generated_policy must be fail or warn")
    checks: list[CheckRecord] = []
    project, loaded = _check_declarations(root, checks)
    version = _check_version(root, project, loaded, checks, galaxy_version)
    if project is not None:
        _record(checks, "required-product-checks", PASS if project.checks.commands else FAIL,
                "required product checks configured" if project.checks.commands else "product checks not configured",
                None if project.checks.commands else REMEDIATIONS["checks"], {"count": len(project.checks.commands)})
    _check_generated(root, project, checks)
    _check_legacy(root, checks)
    _check_git_pollution(root, checks, generated_policy)
    _check_hygiene(root, checks)
    _check_runtime(runtime, runtime_verifier, checks)
    _check_quota(quota, quota_snapshot, quota_guard, checks)
    _check_action(action_resolver, capabilities, action_approval, checks)
    _check_coop(root, project, checks)
    _check_vault(project, root, snapshots, checks)
    _check_lifecycle(root, lifecycle_metadata, now, checks)
    return DoctorReport(str(root), tuple(checks), version)


doctor = run_doctor
doctor_report = run_doctor


class Doctor:
    """Reusable object facade for CLI integrations."""

    def __init__(self, project_root: str | Path, **defaults: Any):
        self.project_root = project_root
        self.defaults = dict(defaults)

    def run(self, **overrides: Any) -> DoctorReport:
        options = dict(self.defaults)
        options.update(overrides)
        return run_doctor(self.project_root, **options)

    check = run
    report = run


def exit_code(report: DoctorReport) -> int:
    return report.exit_code


__all__ = [
    "CheckRecord", "CheckStatus", "Doctor", "DoctorReport", "FAIL", "PASS", "UNKNOWN", "WARN",
    "doctor", "doctor_report", "exit_code", "run_doctor",
]
