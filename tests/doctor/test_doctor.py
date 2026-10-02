import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from lib.actions import Capability
from lib.doctor import CheckStatus, REMEDIATIONS, run_doctor
from lib.quota import QuotaSnapshot


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "AGENTS.md").write_text("# Fixture instructions\n")
        (self.root / ".galaxy").mkdir()
        (self.root / ".galaxy" / "project.yml").write_text(json.dumps({
            "schema_version": 1, "name": "fixture", "adapter": "codex",
            "specialists": {"packs": [], "names": []},
            "vault": {"enabled": False, "path": ".galaxy/vault"},
        }))
        (self.root / ".galaxy" / "team.yml").write_text(json.dumps({
            "schema_version": 1, "mode": "SOLO", "operators": [],
        }))
        (self.root / ".galaxy" / "checks.json").write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-m", "unittest"]],
        }))
        (self.root / "galaxy.lock").write_text(json.dumps({
            "schema_version": 1,
            "galaxy": {"version": "2.0.0", "source_revision": "fixture"},
            "specialists": {"catalog_revision": "fixture", "packs": [], "names": []},
            "adapters": {"schema_version": 1}, "project_schema": 1,
            "migration_schema": 1,
        }))
        self._refresh_declaration_hashes()

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=True)

    def _refresh_declaration_hashes(self):
        lock_path = self.root / "galaxy.lock"
        lock = json.loads(lock_path.read_text())
        lock["declarations"] = {
            relative: hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
            for relative in (
                "AGENTS.md", ".galaxy/project.yml", ".galaxy/team.yml",
                ".galaxy/checks.json",
            )
        }
        lock_path.write_text(json.dumps(lock))

    def test_clean_project_has_no_failures(self):
        (self.root / ".gitignore").write_text(".env\n.codex/\n")
        report = run_doctor(
            self.root, galaxy_version="2.0.0",
            runtime={"effective_model": "gpt-6.1-sol", "effective_effort": "medium"},
            quota_snapshot=QuotaSnapshot(90, 90),
            capabilities=[Capability("github.repository.create", "native", "github-native")],
        )
        self.assertEqual(report.exit_code, 0)
        self.assertFalse(report.failed)
        context = next(item for item in report.checks if item.id == "context-economy")
        self.assertEqual(context.status, CheckStatus.PASS)
        self.assertEqual(context.details["mode"], "off")

    def test_doctor_reports_enabled_context_economy(self):
        path = self.root / ".galaxy/project.yml"
        config = json.loads(path.read_text())
        config["context_economy"] = {"mode": "balanced"}
        path.write_text(json.dumps(config))
        self._refresh_declaration_hashes()
        report = run_doctor(self.root, galaxy_version="2.0.0")
        context = next(item for item in report.checks if item.id == "context-economy")
        self.assertEqual(context.status, CheckStatus.PASS)
        self.assertEqual(context.details["mode"], "balanced")
        self.assertTrue(context.details["progressive_disclosure"])

    def test_remediation_commands_include_required_project_path(self):
        self.assertEqual(
            REMEDIATIONS["telemetry"],
            "enable host runtime telemetry and rerun `galaxy doctor PATH`",
        )
        self.assertEqual(
            REMEDIATIONS["vault"],
            "run `galaxy vault sync PATH --check` and resolve drift before `--force`",
        )
        self.assertEqual(
            REMEDIATIONS["lifecycle"],
            "review candidates with `galaxy cleanup PATH --preview`; apply only after validation",
        )

    def test_tracked_generated_is_fail_by_default_and_warn_policy_is_supported(self):
        generated = self.root / ".codex" / "config.toml"
        generated.parent.mkdir()
        generated.write_text('model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n')
        self._git("init")
        self._git("add", ".")
        strict = run_doctor(self.root)
        self.assertEqual(next(c for c in strict.checks if c.id == "tracked-generated").status, CheckStatus.FAIL)
        permissive = run_doctor(self.root, generated_policy="warn")
        self.assertEqual(next(c for c in permissive.checks if c.id == "tracked-generated").status, CheckStatus.WARN)

    def test_legacy_and_duplicate_environment_are_warnings(self):
        (self.root / "AGENT_TEAM.yml").write_text("legacy")
        (self.root / ".env.example").write_text("A=1\n")
        (self.root / "env.example").write_text("A=2\n")
        report = run_doctor(self.root)
        self.assertEqual(next(c for c in report.checks if c.id == "legacy-v1").status, CheckStatus.WARN)
        self.assertEqual(next(c for c in report.checks if c.id == "duplicate-env-templates").status, CheckStatus.WARN)

    def test_missing_declarations_fail(self):
        (self.root / ".galaxy" / "team.yml").unlink()
        report = run_doctor(self.root)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(next(c for c in report.checks if c.id == "team-schema").status, CheckStatus.FAIL)

    def test_unknown_telemetry_is_not_invented(self):
        report = run_doctor(self.root)
        self.assertEqual(next(c for c in report.checks if c.id == "runtime-verification").status, CheckStatus.UNKNOWN)
        self.assertEqual(next(c for c in report.checks if c.id == "quota-telemetry").status, CheckStatus.UNKNOWN)

    def test_default_version_check_uses_installed_version_not_lock_as_evidence(self):
        lock_path = self.root / "galaxy.lock"
        lock = json.loads(lock_path.read_text())
        lock["galaxy"]["version"] = "999.0.0"
        lock_path.write_text(json.dumps(lock))
        report = run_doctor(self.root)
        check = next(item for item in report.checks if item.id == "version-lock")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("999.0.0", check.message)
        self.assertEqual(next(c for c in report.checks if c.id == "lifecycle-candidates").status, CheckStatus.UNKNOWN)

    def test_browser_only_action_requires_approval(self):
        capability = Capability("github.repository.create", "browser", "browser")
        report = run_doctor(self.root, capabilities=[capability])
        self.assertEqual(next(c for c in report.checks if c.id == "action-capability").status, CheckStatus.WARN)
        approved = run_doctor(self.root, capabilities=[capability], action_approval=True)
        self.assertEqual(next(c for c in approved.checks if c.id == "action-capability").status, CheckStatus.PASS)

    def test_valid_codex_toml_is_reported_explicitly(self):
        config = self.root / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n')
        report = run_doctor(self.root)
        self.assertEqual(next(c for c in report.checks if c.id == "codex-config").status, CheckStatus.PASS)

    def test_incomplete_coop_coordination_is_a_failure(self):
        team = {
            "schema_version": 1, "mode": "CO-OP", "operators": [],
            "integration_branch": "main", "integration_operators": [],
            "required_checks": ["galaxy / validate"],
            "coordination": {
                "backend": "github-actions-issue",
                "claim_protocol": "serialized-workflow",
                "control_issue": None,
            },
        }
        (self.root / ".galaxy/team.yml").write_text(json.dumps(team))
        self._refresh_declaration_hashes()

        report = run_doctor(self.root)
        check = next(c for c in report.checks if c.id == "coop-coordination")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("control_issue", check.details["missing"])
        self.assertIn("workflow", check.details["missing"])
        self.assertIn("capability", check.details["missing"])

    def test_complete_coop_coordination_requires_coherent_workflow_capability(self):
        team = {
            "schema_version": 1, "mode": "CO-OP",
            "operators": [{"id": "one", "github_login": "alice"}],
            "integration_operators": ["one"], "integration_branch": "main",
            "required_checks": ["galaxy / validate"],
            "coordination": {
                "backend": "github-actions-issue",
                "claim_protocol": "serialized-workflow", "control_issue": 17,
                "capability": "github-actions",
                "workflow": ".github/workflows/galaxy-control.yml",
            },
        }
        (self.root / ".galaxy/team.yml").write_text(json.dumps(team))
        self._refresh_declaration_hashes()
        workflow = self.root / ".github/workflows/galaxy-control.yml"
        workflow.parent.mkdir(parents=True)
        source = Path(__file__).resolve().parents[2] / "template/.github/workflows/galaxy-control.yml"
        workflow.write_bytes(source.read_bytes())

        report = run_doctor(self.root)
        check = next(c for c in report.checks if c.id == "coop-coordination")
        self.assertEqual(check.status, CheckStatus.PASS, check.message)
        self.assertEqual(check.details["workflow"], ".github/workflows/galaxy-control.yml")

    def test_malformed_coop_operator_fails_closed_without_crashing_doctor(self):
        team = {
            "schema_version": 1, "mode": "CO-OP",
            "operators": [{"id": "one", "github_login": {"bad": "shape"}}],
            "integration_operators": ["one"], "integration_branch": "main",
            "required_checks": ["galaxy / validate"],
            "coordination": {
                "backend": "github-actions-issue",
                "claim_protocol": "serialized-workflow", "control_issue": 17,
                "capability": "github-actions",
                "workflow": ".github/workflows/galaxy-control.yml",
            },
        }
        (self.root / ".galaxy/team.yml").write_text(json.dumps(team))
        self._refresh_declaration_hashes()

        report = run_doctor(self.root)
        check = next(c for c in report.checks if c.id == "coop-coordination")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("operators", check.details["missing"])


if __name__ == "__main__":
    unittest.main()
