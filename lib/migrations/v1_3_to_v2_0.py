"""Deterministic V1.3 -> Galaxy Orchestrator V2.0 project migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import (
    MigrationConflictError,
    MigrationDetectionError,
    MigrationError,
    MigrationPlan,
    Validator,
    atomic_write,
    checked_project_path,
    checked_root,
    copy_mapping,
    create_backup,
    digest,
    extend_backup,
    git_inventory,
    json_bytes,
    normalize_check_result,
    normalize_relative,
    restore_backup,
    run_git,
    utc_now,
    write_receipt,
)


MIGRATION_ID = "v1_3_to_v2_0"
FROM_VERSION = "1.3.0"
TO_VERSION = "2.0.0"

TEAM = "AGENT_TEAM.yml"
CHECKS = ".multicontroller/checks.json"
POLICY = ".multicontroller/policy.json"
MANIFEST = ".multicontroller/install-manifest.json"
OLD_WORKFLOW = ".github/workflows/multicontroller.yml"

PROJECT_OWNED = {TEAM, CHECKS}
GENERATED_PREFIXES = (
    ".codex/",
    ".agents/skills/multicontroller/",
    ".multicontroller/tools/",
    ".multicontroller/examples/",
    ".multicontroller/messages/",
    ".multicontroller/runtime/",
    ".multicontroller/cache/",
    ".multicontroller/install/",
    ".multicontroller/local/",
)


def _read_json(project: Path, relative: str) -> dict[str, Any]:
    path = checked_project_path(project, relative)
    if not path.is_file():
        raise MigrationDetectionError("missing V1.3 file: " + relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationDetectionError("invalid JSON-compatible V1 file: " + relative) from exc
    if not isinstance(value, dict):
        raise MigrationDetectionError("expected object in V1 file: " + relative)
    return value


def _master_revision(master: Path) -> str:
    result = run_git(master, ["rev-parse", "HEAD"], check=False)
    if result.returncode == 0:
        return result.stdout.decode("ascii", errors="replace").strip()
    release = master / "RELEASE-MANIFEST.json"
    if release.is_file():
        return "sha256:" + digest(release.read_bytes())
    return "migration-v1.3"


def _catalog_revision(master: Path) -> str:
    """Use the same canonical catalog fingerprint consumed by bootstrap."""
    catalog_root = master / "specialists"
    if not catalog_root.is_dir():
        catalog_root = Path(__file__).resolve().parents[2] / "specialists"
    if not catalog_root.is_dir():
        return "builtin-v1"
    try:
        from lib.specialists import SpecialistCatalog, build_index

        catalog = SpecialistCatalog.from_directory(catalog_root)
        return digest(build_index(catalog).encode("utf-8"))
    except (ImportError, OSError, ValueError):
        # A migration plan remains inspectable on a minimal/offline master. The
        # injectable validation boundary may enforce a stronger catalog check.
        return "builtin-v1"


def _team_bytes(team: Mapping[str, Any]) -> bytes:
    migrated = copy_mapping(team)
    checks = migrated.get("required_checks")
    if isinstance(checks, list):
        migrated["required_checks"] = [
            "galaxy / validate" if item == "multicontroller / validate" else item
            for item in checks
        ]
    migration = migrated.setdefault("migration", {})
    if isinstance(migration, dict):
        migration.setdefault("source", "AGENT_TEAM.yml")
        migration.setdefault("source_schema_version", team.get("schema_version"))
    return json_bytes(migrated)


def _checks_bytes(checks: Mapping[str, Any]) -> bytes:
    migrated = copy_mapping(checks)
    migrated.setdefault("schema_version", 1)
    return json_bytes(migrated)


def _project_bytes(project: Path, policy: Mapping[str, Any]) -> bytes:
    migrated_policy = copy_mapping(policy)
    return json_bytes({
        "schema_version": 1,
        "name": project.name,
        "adapter": "codex",
        "specialists": {"packs": ["core"], "names": []},
        "policy": migrated_policy,
        "vault": {
            "enabled": False,
            "path": ".galaxy/vault",
            "mode": "projection",
            "sync": "manual",
            "include": ["tasks", "milestones", "summaries"],
            "exclude": ["prompts", "responses", "telemetry", "secrets"],
        },
    })


def _lock_bytes(master: Path, declarations: Mapping[str, bytes]) -> bytes:
    return json_bytes({
        "schema_version": 1,
        "galaxy": {"version": TO_VERSION, "source_revision": _master_revision(master)},
        "specialists": {"catalog_revision": _catalog_revision(master), "packs": ["core"], "names": []},
        "adapters": {"schema_version": 1, "codex": "codex-v1"},
        "project_schema": 1,
        "migration_schema": 1,
        "declarations": {
            name: digest(content) for name, content in sorted(declarations.items())
        },
    })


def _workflow_bytes() -> bytes:
    # This is a format-aware replacement for the one known validation workflow,
    # not a repository-wide product-name substitution.
    return (
        "name: galaxy\n"
        "on:\n"
        "  pull_request:\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: windows-latest\n"
        "    timeout-minutes: 15\n"
        "    steps:\n"
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
        "        with:\n"
        "          persist-credentials: false\n"
        "      - name: Validate Galaxy declarations and product checks\n"
        "        shell: pwsh\n"
        "        run: galaxy validate --project .\n"
    ).encode("utf-8")


def _ignore_bytes(project: Path) -> bytes:
    path = project / ".gitignore"
    original = path.read_bytes() if path.is_file() else b""
    marker = b"# Galaxy generated/local artifacts"
    if marker in original:
        return original
    rules = (
        marker + b"\n"
        b".env\n"
        b".codex/\n"
        b".agents/skills/multicontroller/\n"
        b".multicontroller/tools/\n"
        b".multicontroller/examples/\n"
        b".multicontroller/messages/\n"
        b".multicontroller/install-manifest.json\n"
        b".galaxy/local/\n"
        b".galaxy/runtime/\n"
        b".galaxy/cache/\n"
        b".galaxy/install/\n"
    )
    separator = b"" if not original or original.endswith(b"\n") else b"\n"
    return original + separator + rules


def _vault_inventory(project: Path) -> tuple[str, ...]:
    found: set[str] = set()
    for relative in (".obsidian", ".multicontroller/vault", ".galaxy/vault"):
        target = checked_project_path(project, relative)
        if target.exists():
            found.add(relative)
    return tuple(sorted(found))


def _is_known_generated(relative: str, manifest_files: set[str]) -> bool:
    if relative == MANIFEST:
        return True
    return relative in manifest_files and relative.startswith(GENERATED_PREFIXES)


def plan(master: Path | str, project: Path | str) -> MigrationPlan:
    """Build a complete side-effect-free migration plan."""
    master = checked_root(master)
    project = checked_root(project)
    if master == project or master in project.parents or project in master.parents:
        raise MigrationError("master and project must be separate directory trees")

    if (project / "galaxy.lock").is_file() and not (project / TEAM).exists():
        try:
            from lib.project import load_project

            loaded = load_project(project)
        except (OSError, ValueError) as exc:
            raise MigrationDetectionError(
                "galaxy.lock exists but the V2 declaration set is incomplete or invalid"
            ) from exc
        if loaded.lock.galaxy_version != TO_VERSION:
            raise MigrationDetectionError(
                "galaxy.lock is not a complete Galaxy V2.0 migration target"
            )
        return MigrationPlan(MIGRATION_ID, FROM_VERSION, TO_VERSION, "already_migrated")

    manifest = _read_json(project, MANIFEST)
    version = str(manifest.get("version", ""))
    if not (version == FROM_VERSION or version.startswith("1.3.")):
        raise MigrationDetectionError("unsupported source version: " + (version or "unknown"))
    entries = manifest.get("files")
    if not isinstance(entries, dict) or not entries:
        raise MigrationDetectionError("V1 install manifest has no file inventory")
    manifest_files = {normalize_relative(str(name)) for name in entries}
    team = _read_json(project, TEAM)
    checks = _read_json(project, CHECKS)
    policy = _read_json(project, POLICY)
    if team.get("schema_version") not in {1, 2, 3}:
        raise MigrationDetectionError("unsupported AGENT_TEAM schema")
    if not isinstance(checks.get("commands"), list):
        raise MigrationDetectionError("V1 checks commands must be an array")
    if not policy.get("preset"):
        raise MigrationDetectionError("V1 policy has no preset")

    project_owned: set[str] = set(PROJECT_OWNED)
    unchanged: set[str] = {MANIFEST}
    modified: set[str] = set()
    preserved: set[str] = set()
    conflicts: list[dict[str, str]] = []
    for name, expected in entries.items():
        relative = normalize_relative(str(name))
        target = checked_project_path(project, relative)
        if relative in PROJECT_OWNED:
            if target.is_file():
                project_owned.add(relative)
            continue
        if not target.is_file():
            modified.add(relative)
            conflicts.append({
                "path": relative,
                "reason": "managed-file-missing",
                "expected_sha256": str(expected),
                "actual_sha256": "missing",
            })
            continue
        actual = digest(target.read_bytes())
        if actual == expected:
            unchanged.add(relative)
        else:
            modified.add(relative)
            conflicts.append({
                "path": relative,
                "reason": "managed-file-user-modified",
                "expected_sha256": str(expected),
                "actual_sha256": actual,
            })

    # A policy outside the old manifest is an explicit project declaration.
    if POLICY not in manifest_files:
        project_owned.add(POLICY)
        modified.discard(POLICY)
        conflicts = [item for item in conflicts if item["path"] != POLICY]

    team_data = _team_bytes(team)
    checks_data = _checks_bytes(checks)
    project_data = _project_bytes(project, policy)
    declarations = {
        ".galaxy/team.yml": team_data,
        ".galaxy/checks.json": checks_data,
        ".galaxy/project.yml": project_data,
    }
    writes: dict[str, bytes] = dict(declarations)
    writes["galaxy.lock"] = _lock_bytes(master, declarations)
    writes[".gitignore"] = _ignore_bytes(project)
    transformations: list[dict[str, str]] = [
        {"source": TEAM, "destination": ".galaxy/team.yml", "rule": "team-json-schema"},
        {"source": CHECKS, "destination": ".galaxy/checks.json", "rule": "checks-json-schema"},
        {"source": POLICY, "destination": ".galaxy/project.yml", "rule": "policy-to-project"},
        {"source": MANIFEST, "destination": "galaxy.lock", "rule": "manifest-to-lock"},
    ]
    deletes = [TEAM, CHECKS, POLICY]
    deletes.extend(
        name for name in unchanged if name.startswith(".codex/")
    )
    if (project / OLD_WORKFLOW).is_file():
        workflow_expected = entries.get(OLD_WORKFLOW)
        workflow_current = digest((project / OLD_WORKFLOW).read_bytes())
        if workflow_expected is None:
            project_owned.add(OLD_WORKFLOW)
            preserved.add(OLD_WORKFLOW)
        elif workflow_current == workflow_expected:
            writes[".github/workflows/galaxy-validate.yml"] = _workflow_bytes()
            deletes.append(OLD_WORKFLOW)
            transformations.append({
                "source": OLD_WORKFLOW,
                "destination": ".github/workflows/galaxy-validate.yml",
                "rule": "validation-workflow-v1-to-v2",
            })

    for destination, content in writes.items():
        target = checked_project_path(project, destination)
        if destination == ".gitignore":
            # _ignore_bytes is a semantic append derived from the exact current
            # bytes, so an existing project-owned ignore file is expected.
            continue
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            conflicts.append({
                "path": destination,
                "reason": "destination-exists",
                "expected_sha256": digest(content),
                "actual_sha256": digest(target.read_bytes()) if target.is_file() else "not-a-file",
            })

    git = git_inventory(project)
    untrack = sorted(
        name for name in git["tracked"] if _is_known_generated(name, manifest_files)
    )
    all_files = {
        path.relative_to(project).as_posix()
        for path in project.rglob("*") if path.is_file() and ".git" not in path.relative_to(project).parts
    }
    transformed_sources = {item["source"] for item in transformations}
    preserved.update(all_files - set(writes) - transformed_sources - set(untrack))
    status = "conflict" if conflicts else "ready"
    return MigrationPlan(
        MIGRATION_ID,
        version,
        TO_VERSION,
        status,
        tuple(sorted(project_owned)),
        tuple(sorted(unchanged)),
        tuple(sorted(modified)),
        tuple(sorted(preserved)),
        tuple(untrack),
        tuple(sorted(writes)),
        _vault_inventory(project),
        tuple(transformations),
        tuple(sorted(conflicts, key=lambda item: (item["path"], item["reason"]))),
        writes,
        tuple(sorted(set(deletes))),
        tuple(git["tracked"]),
    )


def _default_validator(project: Path, catalog_root: Path) -> dict[str, Any]:
    from lib.bootstrap import bootstrap_plan
    from lib.project import load_project

    loaded = load_project(project)
    generated = bootstrap_plan(project, catalog_root=catalog_root)
    if not generated.clean:
        details = sorted(
            set(generated.create) | set(generated.update) |
            set(generated.remove) | set(generated.drift)
        )
        raise MigrationError("validation failed: bootstrap is not reproducible: " + ", ".join(details))
    return {
        "status": "passed",
        "validator": "load_project+bootstrap-check",
        "mode": loaded.team.mode,
        "galaxy_version": loaded.lock.galaxy_version,
    }


def _default_doctor(project: Path) -> dict[str, Any]:
    from lib.doctor import run_doctor
    from lib.project import load_project

    report = run_doctor(project, galaxy_version=TO_VERSION)
    report_data = report.to_dict()
    failures = [item.id for item in report.failed]
    configured_checks = load_project(project).checks.commands
    accepted = failures == ["required-product-checks"] and not configured_checks
    return {
        "status": (
            "passed" if report.exit_code == 0
            else "accepted_pending_product_checks" if accepted
            else "failed"
        ),
        "engine": "lib.doctor.run_doctor",
        "exit_code": report.exit_code,
        "report_status": report_data["status"],
        "accepted_failures": failures if accepted else [],
        "report": report_data,
    }


def apply(
    master: Path | str,
    project: Path | str,
    *,
    validator: Validator | None = None,
    doctor: Validator | None = None,
) -> dict[str, Any]:
    """Apply a planned migration, rolling back every project/index mutation on failure."""
    master = checked_root(master)
    project = checked_root(project)
    migration_plan = plan(master, project)
    if migration_plan.status == "already_migrated":
        return {**migration_plan.to_dict(), "status": "already_migrated", "changed": []}
    if not migration_plan.ready:
        raise MigrationConflictError(migration_plan)

    started = utc_now()
    backup, state = create_backup(master, project, migration_plan)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "migration_id": MIGRATION_ID,
        "from_version": migration_plan.from_version,
        "to_version": TO_VERSION,
        "project": str(project),
        "started_at": started,
        "ended_at": None,
        "backup_location": str(backup),
        "preserved": list(migration_plan.preserved),
        "transformed": list(migration_plan.transformations),
        "untracked": [],
        "generated": list(migration_plan.generated),
        "conflicts": list(migration_plan.conflicts),
        "configuration": {"status": "not_run"},
        "bootstrap": {"status": "not_run"},
        "validation": {"status": "not_run"},
        "git": {"status": "not_run", "untracked": []},
        "doctor": {"status": "not_run"},
        "vault_inventory": list(migration_plan.vault_inventory),
        "status": "in_progress",
    }
    active_phase = "configuration"
    try:
        receipt[active_phase] = {"status": "running"}
        for relative, data in sorted(migration_plan._writes.items()):
            target = checked_project_path(project, relative)
            atomic_write(target, data)
        for relative in migration_plan._deletes:
            target = checked_project_path(project, relative)
            if target.exists():
                if not target.is_file():
                    raise MigrationError("refusing to delete non-file: " + relative)
                target.unlink()
        receipt[active_phase] = {"status": "passed"}

        from lib.bootstrap import bootstrap, bootstrap_plan

        active_phase = "bootstrap"
        receipt[active_phase] = {"status": "running"}
        catalog_root = master / "specialists"
        if not catalog_root.is_dir():
            catalog_root = Path(__file__).resolve().parents[2] / "specialists"
        bootstrap_preview = bootstrap_plan(project, catalog_root=catalog_root)
        bootstrap_targets = [
            relative for relative, _content in bootstrap_preview.artifacts
        ] + list(bootstrap_preview.remove)
        extend_backup(backup, project, state, bootstrap_targets)
        bootstrap_result = bootstrap(project, catalog_root=catalog_root)
        receipt["bootstrap"] = {
            "status": "passed",
            "written": list(bootstrap_result.written),
            "removed": list(bootstrap_result.removed),
        }
        receipt["generated"] = sorted(
            set(receipt["generated"]) | set(bootstrap_result.written)
        )

        active_phase = "validation"
        receipt[active_phase] = {"status": "running"}
        validation_result = (
            validator(project) if validator is not None
            else _default_validator(project, catalog_root)
        )
        receipt["validation"] = normalize_check_result(validation_result, "validation")

        # The index boundary is deliberately after bootstrap/validation. Working
        # copies are never removed by these literal, one-path Git commands.
        active_phase = "git"
        receipt[active_phase] = {"status": "running", "untracked": []}
        for relative in migration_plan.untrack:
            run_git(project, ["rm", "--cached", "--force", "--ignore-unmatch", "--", relative])
            receipt["untracked"].append(relative)
            receipt[active_phase]["untracked"].append(relative)
        receipt[active_phase]["status"] = "passed"

        active_phase = "doctor"
        receipt[active_phase] = {"status": "running"}
        doctor_result = doctor(project) if doctor is not None else _default_doctor(project)
        if isinstance(doctor_result, Mapping):
            receipt[active_phase] = dict(doctor_result)
        receipt["doctor"] = normalize_check_result(doctor_result, "doctor")
        receipt["status"] = "success"
        receipt["ended_at"] = utc_now()
        state["status"] = "complete"
        atomic_write(backup / "backup.json", json_bytes(state))
        receipt_path = write_receipt(backup, receipt)
        receipt["receipt_path"] = str(receipt_path)
        # Include the path in the durable receipt as well.
        write_receipt(backup, receipt)
        return receipt
    except BaseException as exc:
        restore_backup(backup, project, state)
        receipt["status"] = "rolled_back_after_failure"
        receipt["ended_at"] = utc_now()
        receipt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        phase_result = dict(receipt[active_phase])
        phase_result.update(status="failed", error=str(exc))
        receipt[active_phase] = phase_result
        receipt_path = write_receipt(backup, receipt)
        receipt["receipt_path"] = str(receipt_path)
        write_receipt(backup, receipt)
        raise


def _resolve_receipt(master: Path, receipt: str | Path | Mapping[str, Any]) -> Path:
    if isinstance(receipt, Mapping):
        value = receipt.get("receipt_path")
        if not value:
            raise MigrationError("receipt mapping has no receipt_path")
        candidate = Path(str(value))
    else:
        value = str(receipt)
        candidate = Path(value)
        if not candidate.is_absolute() and len(candidate.parts) == 1:
            candidate = master / "local" / "migrations" / value / "receipt.json"
        elif not candidate.is_absolute():
            candidate = master / candidate
    candidate = candidate.resolve(strict=True)
    receipts_root = (master / "local" / "migrations").resolve(strict=True)
    try:
        candidate.relative_to(receipts_root)
    except ValueError as exc:
        raise MigrationError("receipt is outside the migration store") from exc
    if candidate.name != "receipt.json":
        raise MigrationError("expected a migration receipt.json")
    return candidate


def rollback(
    master: Path | str,
    project: Path | str,
    receipt: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Restore file bytes and the saved Git index from one local receipt."""
    master = checked_root(master)
    project = checked_root(project)
    receipt_path = _resolve_receipt(master, receipt)
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
        state = json.loads((receipt_path.parent / "backup.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("invalid migration receipt or backup state") from exc
    if Path(data.get("project", "")).resolve() != project or Path(state.get("project", "")).resolve() != project:
        raise MigrationError("receipt belongs to a different project")
    if data.get("migration_id") != MIGRATION_ID or state.get("migration_id") != MIGRATION_ID:
        raise MigrationError("receipt belongs to a different migration")
    restore_backup(receipt_path.parent, project, state)
    data["status"] = "rolled_back"
    data["rolled_back_at"] = utc_now()
    write_receipt(receipt_path.parent, data)
    return data


preview = plan


class V1_3ToV2_0Migration:
    """Bound master/project API convenient for CLI and installer integration."""

    def __init__(
        self,
        master: Path | str,
        project: Path | str,
        *,
        validator: Validator | None = None,
        doctor: Validator | None = None,
    ) -> None:
        self.master = master
        self.project = project
        self.validator = validator
        self.doctor = doctor

    def plan(self) -> MigrationPlan:
        return plan(self.master, self.project)

    preview = plan

    def apply(self) -> dict[str, Any]:
        return apply(
            self.master, self.project, validator=self.validator, doctor=self.doctor
        )

    def rollback(self, receipt: str | Path | Mapping[str, Any]) -> dict[str, Any]:
        return rollback(self.master, self.project, receipt)


MigrationEngine = V1_3ToV2_0Migration
