import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.migrations import (
    MigrationConflictError,
    MigrationDetectionError,
    MigrationError,
    apply,
    plan,
    rollback,
)
from lib.migrations import v1_3_to_v2_0 as migration_module
from lib.doctor import CheckStatus, run_doctor
from lib.project import ProjectConfigurationError, load_project


def _json(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


ROOT = Path(__file__).resolve().parents[2]


class MigrationFixture:
    def __init__(self, root: Path):
        self.root = root
        self.files = {
            "AGENTS.md": b"# Project-owned V1 instructions\r\nDo not rewrite me.\r\n",
            "AGENT_TEAM.yml": _json({
                "schema_version": 3,
                "mode": "SOLO",
                "integration_branch": "main",
                "operators": [],
                "required_checks": ["multicontroller / validate"],
            }),
            ".multicontroller/checks.json": _json({"commands": []}),
            ".multicontroller/policy.json": _json({
                "schema_version": 1,
                "preset": "balanced",
                "max_subagents": 3,
            }),
            ".codex/config.toml": b'model = "gpt-5.6-sol"\n',
            ".agents/skills/multicontroller/SKILL.md": b"legacy skill\n",
            ".multicontroller/tools/multicontroller.py": b"legacy tool\n",
            ".multicontroller/examples/gate.json": b"{}\n",
            ".multicontroller/messages/HANDOFF.md": b"handoff\n",
            ".github/workflows/multicontroller.yml": b"name: multicontroller\n",
        }
        for name, data in self.files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = {
            "schema_version": 1,
            "version": "1.3.0",
            "mode": "SOLO",
            "preset": "balanced",
            "files": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in self.files.items()
            },
        }
        path = root / ".multicontroller/install-manifest.json"
        path.write_bytes(_json(manifest))
        self.files[".multicontroller/install-manifest.json"] = path.read_bytes()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="galaxy migration ")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.master = base / "master"
        self.project = base / "project"
        self.master.mkdir()
        self.project.mkdir()
        (self.master / "VERSION").write_text("2.0.0\n")
        self.fixture = MigrationFixture(self.project)

    def snapshot(self):
        return {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }

    def configure_coop_with_legacy_control(self, *, managed: bool = True) -> Path:
        team_path = self.project / "AGENT_TEAM.yml"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team.update({
            "mode": "CO-OP",
            "operators": [{"id": "primary", "github_login": "alice"}],
            "integration_operators": ["primary"],
            "coordination": {"control_issue": 17},
        })
        team_path.write_bytes(_json(team))
        workflow = self.project / ".github/workflows/multicontroller-control.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_bytes(
            (ROOT / "coop/.github/workflows/multicontroller-control.yml").read_bytes()
        )
        if managed:
            manifest_path = self.project / ".multicontroller/install-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["mode"] = "CO-OP"
            manifest["files"][workflow.relative_to(self.project).as_posix()] = (
                hashlib.sha256(workflow.read_bytes()).hexdigest()
            )
            manifest_path.write_bytes(_json(manifest))
        return workflow

    def test_clean_preview_has_no_side_effects(self):
        (self.project / ".gitignore").write_text(".env\n", encoding="utf-8")
        before = self.snapshot()
        result = plan(self.master, self.project)
        self.assertTrue(result.ready)
        self.assertIn("AGENT_TEAM.yml", result.project_owned)
        self.assertIn(".codex/config.toml", result.managed_unchanged)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.master / "local").exists())
        receipt = apply(self.master, self.project)
        self.assertIn(b".env", (self.project / ".gitignore").read_bytes())
        self.assertEqual(receipt["status"], "success")
        generated = (self.project / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn("GENERATED BY GALAXY", generated)
        self.assertIn('model_reasoning_effort = "medium"', generated)
        self.assertEqual(receipt["bootstrap"]["status"], "passed")
        self.assertEqual(
            load_project(self.project).config.context_economy.mode.value, "off"
        )

    def test_customized_team_is_semantically_preserved(self):
        team_path = self.project / "AGENT_TEAM.yml"
        team = json.loads(team_path.read_text())
        team["integration_branch"] = "develop"
        team["custom"] = {"owner": "project"}
        team_path.write_bytes(_json(team))
        checks_path = self.project / ".multicontroller/checks.json"
        checks_path.write_bytes(_json({"commands": [["python", "-m", "unittest"]]}))
        receipt = apply(self.master, self.project)
        migrated = json.loads((self.project / ".galaxy/team.yml").read_text())
        self.assertEqual(migrated["integration_branch"], "develop")
        self.assertEqual(migrated["custom"], {"owner": "project"})
        self.assertEqual(migrated["required_checks"], ["galaxy / validate"])
        self.assertFalse(team_path.exists())
        self.assertEqual(
            json.loads((self.project / ".galaxy/checks.json").read_text())["commands"],
            [["python", "-m", "unittest"]],
        )
        self.assertEqual(receipt["status"], "success")

    def test_migration_preserves_agents_bytes_and_authenticates_all_declarations(self):
        before = (self.project / "AGENTS.md").read_bytes()
        receipt = apply(self.master, self.project)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual((self.project / "AGENTS.md").read_bytes(), before)
        lock = json.loads((self.project / "galaxy.lock").read_text(encoding="utf-8"))
        expected_paths = {
            "AGENTS.md", ".galaxy/project.yml", ".galaxy/team.yml",
            ".galaxy/checks.json",
        }
        self.assertEqual(set(lock["declarations"]), expected_paths)
        self.assertEqual(lock["declarations"], {
            relative: hashlib.sha256((self.project / relative).read_bytes()).hexdigest()
            for relative in expected_paths
        })
        self.assertEqual(load_project(self.project).team.mode, "SOLO")

    def test_coop_migration_normalizes_coordination_and_materializes_canonical_workflow(self):
        team_path = self.project / "AGENT_TEAM.yml"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team.update({
            "mode": "CO-OP",
            "operators": [{"id": "primary", "github_login": "alice"}],
            "integration_operators": ["primary"],
            "integration_branch": "develop",
            "rules": {"cross_review": "optional", "direct_main_push": False},
            "review": {"independent_agent_required": True, "partner_review": "optional"},
            "coordination": {
                "backend": "github-issue",
                "claim_protocol": "legacy",
                "control_issue": 17,
            },
        })
        team_path.write_bytes(_json(team))

        receipt = apply(self.master, self.project)

        self.assertEqual(receipt["status"], "success")
        migrated = json.loads((self.project / ".galaxy/team.yml").read_text(encoding="utf-8"))
        self.assertEqual(migrated["operators"], team["operators"])
        self.assertEqual(migrated["integration_operators"], ["primary"])
        self.assertEqual(migrated["integration_branch"], "develop")
        self.assertEqual(migrated["rules"], team["rules"])
        self.assertEqual(migrated["review"], team["review"])
        self.assertEqual(migrated["coordination"], {
            "automatic_expiry": False,
            "backend": "github-actions-issue",
            "capability": "github-actions",
            "claim_protocol": "serialized-workflow",
            "control_issue": 17,
            "workflow": ".github/workflows/galaxy-control.yml",
        })
        self.assertEqual(
            (self.project / ".github/workflows/galaxy-control.yml").read_bytes(),
            (ROOT / "template/.github/workflows/galaxy-control.yml").read_bytes(),
        )
        self.assertEqual(load_project(self.project).team.mode, "CO-OP")

    def test_incomplete_coop_migration_stays_truthful_and_doctor_blocks(self):
        team_path = self.project / "AGENT_TEAM.yml"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team.update({
            "mode": "CO-OP",
            "operators": [],
            "integration_operators": [],
            "coordination": {"control_issue": 0},
        })
        team_path.write_bytes(_json(team))

        apply(self.master, self.project, doctor=lambda _root: {"status": "passed"})

        migrated = json.loads((self.project / ".galaxy/team.yml").read_text(encoding="utf-8"))
        self.assertEqual(migrated["operators"], [])
        self.assertEqual(migrated["integration_operators"], [])
        self.assertIsNone(migrated["coordination"]["control_issue"])
        report = run_doctor(self.project, galaxy_version="2.0.0")
        coop = next(item for item in report.checks if item.id == "coop-coordination")
        self.assertEqual(coop.status, CheckStatus.FAIL)
        self.assertIn("control_issue", coop.details["missing"])
        self.assertIn("operators", coop.details["missing"])
        self.assertIn("integration_operators", coop.details["missing"])

    def test_solo_migration_does_not_create_control_workflow(self):
        apply(self.master, self.project)
        self.assertFalse(
            (self.project / ".github/workflows/galaxy-control.yml").exists()
        )

    def test_migration_preserves_existing_attributes_and_autocrlf_clone_loads(self):
        attributes = self.project / ".gitattributes"
        canonical = (ROOT / "template/.gitattributes").read_bytes()
        custom = (
            b"*.bin binary\r\n"
            b"# project policy\r\n"
            b"* text=auto eol=crlf\r\n"
        )
        attributes.write_bytes(
            b"# Galaxy declaration integrity\n" + canonical + custom
        )

        before_plan = self.snapshot()
        migration_plan = plan(self.master, self.project)
        self.assertEqual(before_plan, self.snapshot())
        receipt = apply(self.master, self.project)
        first = attributes.read_bytes()
        second = apply(self.master, self.project)

        self.assertTrue(migration_plan.ready)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(second["status"], "already_migrated")
        self.assertEqual(attributes.read_bytes(), first)
        self.assertEqual(first.count(custom), 1)
        self.assertLess(
            first.index(custom), first.index(b"# Galaxy declaration integrity\n")
        )
        self.assertTrue(
            first.endswith(b"# Galaxy declaration integrity\n" + canonical)
        )
        self.assertEqual(first.count(b"# Galaxy declaration integrity\n"), 1)
        for rule in canonical.splitlines():
            self.assertEqual(first.splitlines().count(rule), 1, rule)
        _git(self.project, "init", "-q")
        _git(self.project, "config", "core.autocrlf", "false")
        _git(self.project, "config", "user.email", "test@example.invalid")
        _git(self.project, "config", "user.name", "Galaxy Test")
        self.assertEqual(
            _git(self.project, "check-attr", "text", "--", ".galaxy/team.yml"),
            ".galaxy/team.yml: text: unset",
        )
        _git(self.project, "add", ".")
        _git(self.project, "commit", "-qm", "migrated project")
        checkout = Path(self.temporary.name) / "autocrlf migrated checkout"
        subprocess.run(
            [
                "git", "-c", "core.autocrlf=true", "clone", "-q",
                self.project, checkout,
            ],
            check=True,
        )
        try:
            loaded = load_project(checkout)
        except ProjectConfigurationError as exc:
            self.fail(f"autocrlf migrated checkout must load: {exc}")
        self.assertEqual(loaded.team.mode, "SOLO")
        self.assertEqual(
            (checkout / ".gitattributes").read_bytes(), attributes.read_bytes()
        )

    def test_coop_migration_retires_manifest_proven_legacy_control_workflow(self):
        legacy = self.configure_coop_with_legacy_control()

        receipt = apply(self.master, self.project)

        self.assertEqual(receipt["status"], "success")
        self.assertFalse(legacy.exists())
        self.assertEqual(
            (self.project / ".github/workflows/galaxy-control.yml").read_bytes(),
            (ROOT / "template/.github/workflows/galaxy-control.yml").read_bytes(),
        )

    def test_unmanaged_legacy_control_workflow_blocks_migration_without_mutation(self):
        legacy = self.configure_coop_with_legacy_control(managed=False)
        before = self.snapshot()

        migration_plan = plan(self.master, self.project)

        self.assertFalse(migration_plan.ready)
        self.assertIn(
            {
                "path": ".github/workflows/multicontroller-control.yml",
                "reason": "privileged-legacy-workflow-project-owned",
                "expected_sha256": "manifest-entry-required",
                "actual_sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
            },
            migration_plan.conflicts,
        )
        with self.assertRaises(MigrationConflictError):
            apply(self.master, self.project)
        self.assertEqual(before, self.snapshot())

    def test_modified_manifest_legacy_control_workflow_blocks_migration(self):
        legacy = self.configure_coop_with_legacy_control()
        legacy.write_bytes(b"project-modified privileged workflow\n")

        migration_plan = plan(self.master, self.project)

        self.assertFalse(migration_plan.ready)
        conflict = next(
            item for item in migration_plan.conflicts if item["path"] == (
                ".github/workflows/multicontroller-control.yml"
            )
        )
        self.assertEqual(conflict["reason"], "managed-file-user-modified")
        self.assertEqual(legacy.read_bytes(), b"project-modified privileged workflow\n")

    def test_production_defaults_use_complete_project_and_doctor_boundaries(self):
        checks = self.project / ".multicontroller/checks.json"
        checks.write_bytes(_json({"commands": [["python", "-m", "unittest"]]}))
        receipt = apply(self.master, self.project)
        self.assertEqual(receipt["validation"]["validator"], "load_project+bootstrap-check")
        self.assertEqual(receipt["doctor"]["engine"], "lib.doctor.run_doctor")
        self.assertEqual(receipt["doctor"]["exit_code"], 0)
        self.assertEqual(receipt["doctor"]["report_status"], "WARN")

    def test_empty_checks_keep_real_doctor_failure_as_explicit_migration_exception(self):
        receipt = apply(self.master, self.project)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(receipt["doctor"]["status"], "accepted_pending_product_checks")
        self.assertEqual(receipt["doctor"]["exit_code"], 1)
        self.assertEqual(receipt["doctor"]["report_status"], "FAIL")
        self.assertEqual(
            receipt["doctor"]["accepted_failures"], ["required-product-checks"]
        )

    def test_modified_managed_file_blocks_without_project_mutation(self):
        target = self.project / ".codex/config.toml"
        target.write_text("human change\n")
        before = self.snapshot()
        migration_plan = plan(self.master, self.project)
        self.assertIn(".codex/config.toml", migration_plan.managed_user_modified)
        with self.assertRaises(MigrationConflictError):
            apply(self.master, self.project)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.project / "galaxy.lock").exists())

    def test_partial_v2_state_is_not_reported_as_already_migrated(self):
        (self.project / "AGENT_TEAM.yml").unlink()
        (self.project / "galaxy.lock").write_bytes(_json({
            "schema_version": 1,
            "galaxy": {"version": "2.0.0", "source_revision": "manual"},
        }))
        with self.assertRaises(MigrationDetectionError):
            plan(self.master, self.project)

    def test_tracked_codex_is_untracked_without_deleting_working_copy(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        observed = []
        def validate(root):
            observed.extend(_git(root, "ls-files").splitlines())
            return {"status": "passed"}
        receipt = apply(self.master, self.project, validator=validate)
        self.assertTrue((self.project / ".codex/config.toml").exists())
        self.assertIn(".codex/config.toml", observed)
        self.assertNotIn(".codex/config.toml", _git(self.project, "ls-files").splitlines())
        self.assertIn(".codex/config.toml", receipt["untracked"])

    def test_mixed_agents_content_is_preserved_and_not_untracked(self):
        owned = self.project / ".agents/skills/team-owned/SKILL.md"
        owned.parent.mkdir(parents=True)
        owned.write_text("project content\n")
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        receipt = apply(self.master, self.project)
        tracked = _git(self.project, "ls-files").splitlines()
        self.assertTrue(owned.exists())
        self.assertIn(".agents/skills/team-owned/SKILL.md", tracked)
        self.assertNotIn(".agents/skills/multicontroller/SKILL.md", tracked)
        self.assertIn(".agents/skills/team-owned/SKILL.md", receipt["preserved"])

    def test_failed_validation_rolls_back_files_and_git_index_exactly(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(ValueError, "validation failed"):
            apply(
                self.master,
                self.project,
                validator=lambda _root: (_ for _ in ()).throw(ValueError("validation failed")),
            )
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())

    def test_bootstrap_drift_rolls_back_declarations_and_git_index_exactly(self):
        drift = self.project / ".codex/agents/worker.toml"
        drift.parent.mkdir(parents=True, exist_ok=True)
        drift.write_bytes(b"project-owned runtime collision\n")
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(ValueError, "drift"):
            apply(self.master, self.project)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())

    def test_success_receipt_can_restore_exact_files_and_index(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()
        receipt = apply(self.master, self.project)
        for field in (
            "transformed", "preserved", "untracked", "generated", "conflicts",
            "validation", "doctor", "backup_location", "receipt_path",
        ):
            self.assertIn(field, receipt)
        result = rollback(self.master, self.project, receipt["receipt_path"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())

    def test_success_rollback_restores_preexisting_bootstrap_state_exactly(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        state = self.project / ".galaxy/install/bootstrap-state.json"
        state.parent.mkdir(parents=True)
        state.write_bytes(b'{"schema_version":0,"local":"preserve exactly"}\r\n')
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()

        receipt = apply(self.master, self.project)
        rollback(self.master, self.project, receipt["receipt_path"])

        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())

    def test_doctor_failure_rolls_back_with_truthful_phase_statuses(self):
        unmanaged = self.project / ".codex/project-owned.txt"
        unmanaged.write_bytes(b"must remain tracked and unchanged\n")
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(ValueError, "doctor failed"):
            apply(self.master, self.project)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())
        paths = list((self.master / "local/migrations").glob("*/receipt.json"))
        self.assertEqual(len(paths), 1)
        receipt = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["bootstrap"]["status"], "passed")
        self.assertEqual(receipt["validation"]["status"], "passed")
        self.assertEqual(receipt["git"]["status"], "passed")
        self.assertEqual(receipt["doctor"]["status"], "failed")
        self.assertEqual(receipt["doctor"]["engine"], "lib.doctor.run_doctor")
        self.assertEqual(receipt["doctor"]["exit_code"], 1)
        self.assertEqual(receipt["doctor"]["report_status"], "FAIL")
        self.assertEqual(receipt["doctor"]["accepted_failures"], [])
        failed_checks = {
            item["id"] for item in receipt["doctor"]["report"]["checks"]
            if item["status"] == "FAIL"
        }
        self.assertIn("tracked-generated", failed_checks)

    def test_mid_untrack_failure_restores_exact_state_and_does_not_blame_doctor(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()
        real_run_git = migration_module.run_git
        untrack_calls = 0

        def fail_second_untrack(root, arguments, *, check=True):
            nonlocal untrack_calls
            if arguments and arguments[0] == "rm":
                untrack_calls += 1
                if untrack_calls == 2:
                    raise ValueError("simulated mid-untrack failure")
            return real_run_git(root, arguments, check=check)

        with patch.object(migration_module, "run_git", side_effect=fail_second_untrack):
            with self.assertRaisesRegex(ValueError, "mid-untrack"):
                apply(self.master, self.project)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())
        paths = list((self.master / "local/migrations").glob("*/receipt.json"))
        self.assertEqual(len(paths), 1)
        receipt = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["bootstrap"]["status"], "passed")
        self.assertEqual(receipt["validation"]["status"], "passed")
        self.assertEqual(receipt["git"]["status"], "failed")
        self.assertEqual(receipt["doctor"]["status"], "not_run")

    def test_vault_is_inventory_only_and_apply_is_idempotent(self):
        vault = self.project / ".multicontroller/vault/tasks/private.md"
        vault.parent.mkdir(parents=True)
        vault.write_bytes(b"user vault bytes\n")
        migration_plan = plan(self.master, self.project)
        self.assertIn(".multicontroller/vault", migration_plan.vault_inventory)
        apply(self.master, self.project)
        self.assertEqual(vault.read_bytes(), b"user vault bytes\n")
        second = apply(self.master, self.project)
        self.assertEqual(second["status"], "already_migrated")
        self.assertEqual(vault.read_bytes(), b"user vault bytes\n")

    def test_abrupt_interruption_requires_explicit_rollback_before_retry(self):
        _git(self.project, "init", "-q")
        _git(self.project, "add", ".")
        before = self.snapshot()
        before_index = (self.project / ".git/index").read_bytes()

        with patch("lib.bootstrap.bootstrap_plan", side_effect=SystemExit("abrupt stop")):
            with patch.object(
                migration_module, "restore_backup", side_effect=SystemExit("process terminated")
            ):
                with self.assertRaisesRegex(SystemExit, "process terminated"):
                    apply(self.master, self.project)

        receipts = list((self.master / "local/migrations").glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        pending = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(pending["status"], "in_progress")
        self.assertEqual(pending["configuration"]["status"], "passed")
        self.assertEqual(pending["bootstrap"]["status"], "running")

        partial = self.snapshot()
        with self.assertRaisesRegex(MigrationError, "in progress.*rollback"):
            apply(self.master, self.project)
        self.assertEqual(partial, self.snapshot())

        result = rollback(self.master, self.project, receipts[0])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())

    def test_migrated_workflow_is_exact_canonical_template(self):
        expected = (ROOT / "template/.github/workflows/galaxy-validate.yml").read_bytes()
        receipt = apply(self.master, self.project)
        actual = (self.project / ".github/workflows/galaxy-validate.yml").read_bytes()
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(actual, expected)
        self.assertIn(b"galaxy.lock", actual)
        self.assertIn(b"validate $env:GITHUB_WORKSPACE --gate", actual)
        self.assertNotIn(b"validate --project", actual)


if __name__ == "__main__":
    unittest.main()
