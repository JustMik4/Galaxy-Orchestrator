import json
import os
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib import galaxy as galaxy_cli
from lib import installer
from lib.project import ProjectConfigurationError, load_project
from tests.bootstrap.test_project import TRACKED_DECLARATIONS, update_declaration_hashes
from tests.migrations.test_v1_3_to_v2_0 import MigrationFixture


ROOT = Path(__file__).resolve().parents[1]


class GalaxyV2CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="galaxy cli ")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "existing project"
        self.project.mkdir()

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "galaxy.py"), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_lib_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "lib/galaxy.py"), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def workflow_run_steps(self):
        lines = (ROOT / "template/.github/workflows/galaxy-validate.yml").read_text(
            encoding="utf-8"
        ).splitlines()
        steps = []
        index = 0
        while index < len(lines):
            stripped = lines[index].lstrip()
            indent = len(lines[index]) - len(stripped)
            if stripped in ("- run: |", "run: |"):
                content_indent = indent + (4 if stripped.startswith("-") else 2)
                body = []
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    candidate_indent = len(candidate) - len(candidate.lstrip())
                    if candidate.strip() and candidate_indent <= indent:
                        break
                    body.append(candidate[content_indent:] if candidate.strip() else "")
                    index += 1
                steps.append("\n".join(body) + "\n")
                continue
            index += 1
        return steps

    def test_install_creates_canonical_layout_without_v1_pollution(self):
        result = self.run_cli("install", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "install")
        for relative in (
            "AGENTS.md",
            ".galaxy/project.yml",
            ".galaxy/team.yml",
            ".galaxy/checks.json",
            "galaxy.lock",
            ".github/workflows/galaxy-validate.yml",
            ".codex/config.toml",
            ".codex/agents/worker.toml",
            ".codex/skills/core/SKILL.md",
        ):
            self.assertTrue((self.project / relative).is_file(), relative)
        for relative in (
            "AGENT_TEAM.yml",
            ".multicontroller",
            ".agents/skills/multicontroller",
            ".multicontroller/tools",
        ):
            self.assertFalse((self.project / relative).exists(), relative)
        config = (self.project / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5.6-sol"', config)
        self.assertIn('model_reasoning_effort = "medium"', config)
        self.assertEqual(load_project(self.project).team.mode, "SOLO")

    def test_init_alias_installs_default_solo_project(self):
        result = self.run_cli("init", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "init")
        team = json.loads((self.project / ".galaxy/team.yml").read_text(encoding="utf-8"))
        self.assertEqual(team["mode"], "SOLO")
        self.assertTrue((self.project / ".codex/config.toml").is_file())

    def test_coop_install_renders_canonical_coordination_without_v1_runtime(self):
        result = self.run_cli("install", self.project, "--mode", "CO-OP")
        self.assertEqual(result.returncode, 0, result.stderr)
        team_path = self.project / ".galaxy/team.yml"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        self.assertEqual(team["mode"], "CO-OP")
        self.assertEqual(team["coordination"], {
            "automatic_expiry": False,
            "backend": "github-actions-issue",
            "capability": "github-actions",
            "claim_protocol": "serialized-workflow",
            "control_issue": None,
            "workflow": ".github/workflows/galaxy-control.yml",
        })
        self.assertEqual(team["required_checks"], ["galaxy / validate"])
        control = self.project / ".github/workflows/galaxy-control.yml"
        self.assertTrue(control.is_file())
        workflow = control.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:", "group: galaxy-control-v2",
            "issues: write", "pull-requests: write", "contents: write",
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            "github.event.repository.default_branch", "persist-credentials: false",
            "GH_TOKEN: ${{ github.token }}", "galaxy.lock", "source_revision",
            "https://github.com/JustMik4/Galaxy-Orchestrator",
            "^[0-9a-fA-F]{40}$", "FETCH_HEAD", "rev-parse",
            "-m lib.coordinator",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn(".multicontroller/tools/coordinator.py", workflow)
        self.assertNotIn("'lib/coordinator.py'", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("${{ inputs.request }}", workflow)
        self.assertEqual(load_project(self.project).team.mode, "CO-OP")
        for relative in (
            ".multicontroller", "AGENT_TEAM.yml",
            ".github/workflows/multicontroller.yml",
            ".github/workflows/multicontroller-control.yml",
        ):
            self.assertFalse((self.project / relative).exists(), relative)

        before = team_path.read_bytes()
        mismatch = self.run_cli("install", self.project, "--check")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("incompatible", mismatch.stderr)
        self.assertEqual(team_path.read_bytes(), before)

    def test_solo_install_does_not_include_remote_control_workflow(self):
        result = self.run_cli("install", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.project / ".github/workflows/galaxy-control.yml").exists())

    def test_installed_project_survives_autocrlf_checkout(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "core.autocrlf", "false"], cwd=self.project, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.project, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Galaxy Test"],
            cwd=self.project, check=True,
        )
        installer.install_v2(ROOT, self.project)
        attributes = self.project / ".gitattributes"
        attributes.write_bytes(
            attributes.read_bytes() + b"* text=auto eol=crlf\n"
        )
        installer.install_v2(ROOT, self.project)
        resolved = subprocess.run(
            ["git", "check-attr", "text", "--", ".galaxy/team.yml"],
            cwd=self.project, text=True, capture_output=True, check=True,
        )
        self.assertEqual(resolved.stdout.strip(), ".galaxy/team.yml: text: unset")
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "installed project"],
            cwd=self.project, check=True,
        )
        checkout = Path(self.temporary.name) / "autocrlf checkout"
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
            self.fail(f"autocrlf install checkout must load: {exc}")

        self.assertEqual(loaded.team.mode, "SOLO")
        self.assertEqual(
            (checkout / ".gitattributes").read_bytes(),
            attributes.read_bytes(),
        )
        for relative in TRACKED_DECLARATIONS:
            self.assertEqual(
                (checkout / relative).read_bytes(),
                (self.project / relative).read_bytes(),
                relative,
            )

    def test_install_merges_existing_gitattributes_rules_deterministically(self):
        attributes = self.project / ".gitattributes"
        legacy_rules = (ROOT / "template/.gitattributes").read_bytes()
        project_rules = b"*.png binary\r\n# project attributes\r\n"
        attributes.write_bytes(legacy_rules + project_rules)

        first = installer.install_v2(ROOT, self.project)
        merged = attributes.read_bytes()
        second = installer.install_v2(ROOT, self.project)

        self.assertIn(".gitattributes", first["changed"])
        self.assertEqual(second["changed"], [])
        self.assertEqual(attributes.read_bytes(), merged)
        self.assertTrue(merged.startswith(project_rules))
        self.assertEqual(merged.count(b"# Galaxy declaration integrity\n"), 1)
        for rule in (ROOT / "template/.gitattributes").read_bytes().splitlines():
            self.assertEqual(merged.splitlines().count(rule), 1, rule)

    def test_repeat_install_preserves_user_added_gitattributes_rules(self):
        installer.install_v2(ROOT, self.project)
        attributes = self.project / ".gitattributes"
        user_rules = (
            b"docs/** linguist-documentation\n"
            b"* text=auto eol=crlf\n"
        )
        attributes.write_bytes(attributes.read_bytes() + user_rules)
        before = attributes.read_bytes()

        check = self.run_cli("install", self.project, "--check")
        self.assertEqual(attributes.read_bytes(), before)
        result = installer.install_v2(ROOT, self.project)
        merged = attributes.read_bytes()
        second = installer.install_v2(ROOT, self.project)

        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn(".gitattributes", json.loads(check.stdout)["changed"])
        self.assertIn(".gitattributes", result["changed"])
        self.assertEqual(second["changed"], [])
        self.assertNotEqual(merged, before)
        self.assertEqual(merged.count(user_rules), 1)
        self.assertLess(
            merged.index(user_rules),
            merged.index(b"# Galaxy declaration integrity\n"),
        )
        self.assertTrue(
            merged.endswith(
                b"# Galaxy declaration integrity\n"
                + (ROOT / "template/.gitattributes").read_bytes()
            )
        )

    def test_install_check_reports_attributes_merge_without_mutation(self):
        attributes = self.project / ".gitattributes"
        attributes.write_bytes(b"*.png binary\n")
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }

        result = self.run_cli("install", self.project, "--check")

        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(".gitattributes", json.loads(result.stdout)["changed"])
        self.assertEqual(after, before)

    def test_critical_preset_is_declarative_and_keeps_root_sol_medium(self):
        result = self.run_cli("install", self.project, "--preset", "critical")
        self.assertEqual(result.returncode, 0, result.stderr)
        project_path = self.project / ".galaxy/project.yml"
        declaration = json.loads(project_path.read_text(encoding="utf-8"))
        self.assertEqual(declaration["routing"]["profile"], "critical")
        config = (self.project / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5.6-sol"', config)
        self.assertIn('model_reasoning_effort = "medium"', config)

        before = project_path.read_bytes()
        mismatch = self.run_cli("install", self.project, "--check")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("incompatible", mismatch.stderr)
        self.assertEqual(project_path.read_bytes(), before)

    def test_second_install_and_bootstrap_check_are_deterministic_and_read_only(self):
        first = self.run_cli("install", self.project)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        second = self.run_cli("install", self.project)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["changed"], [])
        check = self.run_cli("bootstrap", self.project, "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        payload = json.loads(check.stdout)
        self.assertEqual(payload["command"], "bootstrap")
        self.assertFalse(payload["applied"])
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_bootstrap_check_missing_generated_artifact_exits_one(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        missing = self.project / ".codex/config.toml"
        missing.unlink()

        result = self.run_cli("bootstrap", self.project, "--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(".codex/config.toml", payload["create"])
        self.assertFalse(missing.exists())

    def test_specialists_sync_check_marker_retaining_human_edit_is_drift(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        config = self.project / ".codex/config.toml"
        config.write_text(config.read_text(encoding="utf-8") + "# stale\n", encoding="utf-8")

        result = self.run_cli("specialists", "sync", self.project, "--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(".codex/config.toml", payload["drift"])
        self.assertTrue(config.read_text(encoding="utf-8").endswith("# stale\n"))

    def test_specialists_sync_check_preserves_planned_removal_and_exits_one(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        project_path = self.project / ".galaxy/project.yml"
        lock_path = self.project / "galaxy.lock"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        project["specialists"]["packs"] = []
        lock["specialists"]["packs"] = []
        project_path.write_text(json.dumps(project), encoding="utf-8")
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        update_declaration_hashes(self.project)
        stale = self.project / ".codex/skills/core/SKILL.md"

        result = self.run_cli("specialists", "sync", self.project, "--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(".codex/skills/core/SKILL.md", payload["removed"])
        self.assertTrue(stale.is_file())

    def test_validate_reports_configuration_and_runs_explicit_gate(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        checks = self.project / ".galaxy/checks.json"
        checks.write_text(json.dumps({
            "schema_version": 1,
            "commands": [[sys.executable, "-c", "pass"]],
        }), encoding="utf-8")
        update_declaration_hashes(self.project)

        result = self.run_cli("validate", self.project, "--gate")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "validate")
        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["product_checks"], "passed")

    def test_doctor_json_uses_stable_report_and_exit_code(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        checks = self.project / ".galaxy/checks.json"
        checks.write_text(json.dumps({
            "schema_version": 1,
            "commands": [[sys.executable, "-c", "pass"]],
        }), encoding="utf-8")
        update_declaration_hashes(self.project)

        result = self.run_cli("doctor", self.project, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn(payload["status"], ("PASS", "WARN"))
        self.assertIsInstance(payload["checks"], list)

    def test_migration_preview_has_no_project_writes(self):
        MigrationFixture(self.project)
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }

        result = self.run_cli("migrate", self.project, "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "migrate")
        self.assertTrue(payload["preview"])
        self.assertTrue(payload["ready"])
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_lib_galaxy_file_execution_runs_v2_command(self):
        MigrationFixture(self.project)
        result = self.run_lib_cli("migrate", self.project, "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "migrate")
        self.assertTrue(payload["preview"])

    def test_cleanup_preview_reports_targets_without_mutation(self):
        subprocess.run(["git", "init", "-b", "main"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Galaxy Test"], cwd=self.project, check=True)
        marker = self.project / "README.md"
        marker.write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.project, check=True, capture_output=True)
        stale = self.project / ".galaxy/runtime/old.tmp"
        stale.parent.mkdir(parents=True)
        stale.write_text("cache", encoding="utf-8")
        old = 1_600_000_000
        os.utime(stale, (old, old))

        result = self.run_cli("cleanup", self.project, "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "cleanup")
        self.assertFalse(payload["applied"])
        self.assertIn(".galaxy/runtime/old.tmp", payload["targets"]["runtime"])
        self.assertTrue(stale.is_file())

    def test_vault_status_is_disabled_and_read_only_by_default(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }

        result = self.run_cli("vault", "status", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "vault status")
        self.assertFalse(payload["enabled"])
        self.assertIsNone(payload["path"])
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_disabled_vault_sync_check_needs_no_snapshot_and_writes_nothing(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }

        result = self.run_cli("vault", "sync", self.project, "--check")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "vault sync")
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["written"], [])
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_vault_sync_uses_explicit_snapshot_and_configured_path(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        config_path = self.project / ".galaxy/project.yml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["vault"].update(enabled=True, path=".galaxy/obsidian")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        update_declaration_hashes(self.project)
        snapshots = Path(self.temporary.name) / "snapshots.json"
        task = {
            "task_id": "T-9",
            "title": "Integrate CLI",
            "status": "complete",
            "revision": 1,
            "source_receipt": "receipt-1",
            "prompt": "must not be exported",
        }
        snapshots.write_text(json.dumps({
            "schema_version": 1,
            "tasks": [task],
            "authority": [{
                "task_id": "T-9", "revision": 1, "source_receipt": "receipt-1",
            }],
        }), encoding="utf-8")

        result = self.run_cli("vault", "sync", self.project, "--snapshot", snapshots)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "vault sync")
        self.assertTrue(payload["enabled"])
        note = self.project / ".galaxy/obsidian/tasks/T-9.md"
        self.assertTrue(note.is_file())
        self.assertNotIn("must not be exported", note.read_text(encoding="utf-8"))

    def test_specialists_list_is_cold_and_does_not_read_bodies(self):
        real_read_text = Path.read_text

        def refuse_specialist_body(path, *args, **kwargs):
            if path.name == "SKILL.md":
                raise AssertionError("cold list loaded a specialist body")
            return real_read_text(path, *args, **kwargs)

        output = io.StringIO()
        with patch.object(Path, "read_text", refuse_specialist_body), redirect_stdout(output):
            code = galaxy_cli.main(["specialists", "list"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command"], "specialists list")
        self.assertEqual(payload["specialists"], [
            "core", "git", "github-actions", "python-debugging",
            "python-testing", "windows-powershell",
        ])

    def test_specialists_sync_materializes_the_declared_hot_set(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        skill = self.project / ".codex/skills/core/SKILL.md"
        skill.unlink()
        skill.parent.rmdir()

        result = self.run_cli("specialists", "sync", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "specialists sync")
        self.assertIn(".codex/skills/core/SKILL.md", payload["written"])
        self.assertTrue(skill.is_file())

    def test_v1_installer_remains_available_through_explicit_compatibility_api(self):
        result = installer.install_v1(ROOT, self.project)
        self.assertEqual(result["mode"], "SOLO")
        self.assertTrue((self.project / "AGENT_TEAM.yml").is_file())
        self.assertTrue((self.project / ".multicontroller/tools/multicontroller.py").is_file())

    def test_v1_compatibility_install_is_not_a_hybrid_v2_project(self):
        installer.install_v1(ROOT, self.project)
        for relative in (
            ".galaxy", "galaxy.lock", ".github/workflows/galaxy-validate.yml",
        ):
            self.assertFalse((self.project / relative).exists(), relative)
        instructions = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Project instructions — Multicontroller V1", instructions)
        self.assertNotIn("Galaxy Orchestrator project instructions", instructions)
        self.assertIn(".multicontroller/policy.json", instructions)

    def test_powershell_install_wrapper_routes_to_canonical_cli(self):
        if subprocess.run(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion"],
                          capture_output=True).returncode:
            self.skipTest("PowerShell unavailable")
        arguments = [
            "pwsh", "-NoProfile", "-File", str(ROOT / "scripts/install.ps1"),
            "-ProjectPath", str(self.project),
        ]
        preview = subprocess.run(arguments + ["-WhatIf"], text=True, capture_output=True, check=False)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(list(self.project.iterdir()), [])
        result = subprocess.run(arguments, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["command"], "install")
        self.assertTrue((self.project / ".galaxy/project.yml").is_file())
        self.assertFalse((self.project / "AGENT_TEAM.yml").exists())

    def test_powershell_install_passes_mode_and_preset_to_canonical_cli(self):
        if subprocess.run(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion"],
                          capture_output=True).returncode:
            self.skipTest("PowerShell unavailable")
        result = subprocess.run([
            "pwsh", "-NoProfile", "-File", str(ROOT / "scripts/install.ps1"),
            "-ProjectPath", str(self.project), "-Mode", "CO-OP",
            "-Preset", "critical",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        team = json.loads((self.project / ".galaxy/team.yml").read_text(encoding="utf-8"))
        project = json.loads((self.project / ".galaxy/project.yml").read_text(encoding="utf-8"))
        self.assertEqual(team["mode"], "CO-OP")
        self.assertEqual(project["routing"]["profile"], "critical")

        update = subprocess.run([
            "pwsh", "-NoProfile", "-File", str(ROOT / "scripts/update.ps1"),
            "-ProjectPath", str(self.project),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(update.returncode, 0, update.stderr)
        self.assertEqual(json.loads(update.stdout)["changed"], [])

    def test_install_preserves_git_exclude_and_adds_only_local_policy(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        exclude = self.project / ".git/info/exclude"
        exclude.write_text("user-local.txt\n", encoding="utf-8")

        result = self.run_cli("install", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = exclude.read_text(encoding="utf-8")
        self.assertIn("user-local.txt\n", policy)
        for relative in (
            ".codex/", ".galaxy/local/", ".galaxy/runtime/",
            ".galaxy/cache/", ".galaxy/install/",
        ):
            self.assertIn(relative, policy)
        self.assertNotIn(".agents/", policy)

    def test_top_level_help_lists_canonical_and_legacy_commands(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "install", "bootstrap", "migrate", "validate", "doctor",
            "specialists", "cleanup", "vault", "dispatch", "decide", "gate", "reclaim",
        ):
            self.assertIn(command, result.stdout)

    def test_dispatch_cli_is_operational_and_installed_instructions_require_it(self):
        install = self.run_cli("install", self.project)
        self.assertEqual(install.returncode, 0, install.stderr)
        request = Path(self.temporary.name) / "dispatch-request.json"
        capabilities = Path(self.temporary.name) / "capabilities.json"
        quota = Path(self.temporary.name) / "quota.json"
        request.write_text(json.dumps({
            "task_class": "implementation", "required_capability": 3,
        }), encoding="utf-8")
        capabilities.write_text(json.dumps({
            "configured": [
                {"model": "luna", "effort": "medium", "capability": 2, "expected_cost": 1},
                {"model": "terra", "effort": "medium", "capability": 3, "expected_cost": 2},
            ],
            "observed": [
                {"model": "luna", "effort": "medium"},
                {"model": "terra", "effort": "medium"},
            ],
        }), encoding="utf-8")
        quota.write_text(json.dumps({
            "five_hour_remaining": 90, "weekly_remaining": 80,
        }), encoding="utf-8")

        result = self.run_cli(
            "dispatch", "authorize", self.project,
            "--request", request, "--capabilities", capabilities, "--quota", quota,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["authorized"])
        self.assertEqual(payload["route"], {"model": "terra", "effort": "medium"})
        instructions = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in (
            "galaxy dispatch authorize", "galaxy dispatch verify",
            "galaxy dispatch review-check", "galaxy dispatch review-record",
        ):
            self.assertIn(phrase, instructions)

    def test_v2_install_refuses_concurrent_file_and_preserves_it(self):
        real_safe_path = installer.safe_path
        target = self.project / "AGENTS.md"
        resolutions = 0

        def interleaved(value):
            nonlocal resolutions
            resolved = real_safe_path(value)
            if resolved == target:
                resolutions += 1
                if resolutions == 3:
                    target.write_text("concurrent user rules\n", encoding="utf-8")
            return resolved

        with patch.object(installer, "safe_path", side_effect=interleaved):
            with self.assertRaisesRegex(ValueError, "changed concurrently"):
                installer.install_v2(ROOT, self.project)
        self.assertEqual(target.read_text(encoding="utf-8"), "concurrent user rules\n")
        self.assertEqual(
            [path.name for path in self.project.iterdir()],
            ["AGENTS.md"],
        )

    def test_v2_attributes_merge_refuses_concurrent_user_edit(self):
        real_safe_path = installer.safe_path
        target = self.project / ".gitattributes"
        target.write_bytes(b"*.png binary\n")
        concurrent = b"*.png binary\n*.zip binary\n"
        resolutions = 0

        def interleaved(value):
            nonlocal resolutions
            resolved = real_safe_path(value)
            if resolved == target:
                resolutions += 1
                if resolutions == 3:
                    target.write_bytes(concurrent)
            return resolved

        with patch.object(installer, "safe_path", side_effect=interleaved):
            with self.assertRaisesRegex(ValueError, "changed concurrently"):
                installer.install_v2(ROOT, self.project)
        self.assertEqual(target.read_bytes(), concurrent)
        self.assertEqual(
            [path.name for path in self.project.iterdir()],
            [".gitattributes"],
        )

    def test_v2_install_rollback_never_follows_replaced_symlink(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        exclude = self.project / ".git/info/exclude"
        original = b"user-local.txt\n"
        exclude.write_bytes(original)
        outside_root = Path(self.temporary.name) / "outside"
        outside_root.mkdir()
        outside = outside_root / "exclude"
        outside_bytes = b"external must remain intact\n"
        outside.write_bytes(outside_bytes)
        use_junction = os.name == "nt"
        if use_junction:
            probe = self.project / "junction-probe"
            created = subprocess.run([
                "pwsh", "-NoProfile", "-Command",
                "New-Item -ItemType Junction -Path $env:GALAXY_LINK "
                "-Target $env:GALAXY_TARGET | Out-Null",
            ], env={**os.environ, "GALAXY_LINK": str(probe),
                    "GALAXY_TARGET": str(outside_root)},
                text=True, capture_output=True, check=False)
            if created.returncode:
                self.skipTest("directory junction creation unavailable")
            probe.rmdir()
        else:
            probe = self.project / "symlink-probe"
            try:
                probe.symlink_to(outside)
                probe.unlink()
            except OSError:
                self.skipTest("file symlink creation unavailable")

        real_replace = installer.os.replace
        attacked = False
        failed = False

        def late_failure(source, destination):
            nonlocal attacked, failed
            target = Path(destination)
            if target == exclude and not attacked:
                real_replace(source, destination)
                target.unlink()
                if use_junction:
                    target.parent.rmdir()
                    created = subprocess.run([
                        "pwsh", "-NoProfile", "-Command",
                        "New-Item -ItemType Junction -Path $env:GALAXY_LINK "
                        "-Target $env:GALAXY_TARGET | Out-Null",
                    ], env={**os.environ, "GALAXY_LINK": str(target.parent),
                            "GALAXY_TARGET": str(outside_root)},
                        text=True, capture_output=True, check=False)
                    if created.returncode:
                        raise OSError(created.stderr)
                else:
                    target.symlink_to(outside)
                attacked = True
                return
            if attacked and not failed and self.project in target.parents:
                failed = True
                raise OSError("simulated late install failure")
            return real_replace(source, destination)

        with patch.object(installer.os, "replace", side_effect=late_failure):
            with self.assertRaisesRegex(OSError, "simulated late install failure"):
                installer.install_v2(ROOT, self.project)

        self.assertEqual(outside.read_bytes(), outside_bytes)
        self.assertFalse(exclude.is_symlink())
        self.assertEqual(exclude.read_bytes(), original)
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_workflow_fetches_and_runs_exact_galaxy_lock_source(self):
        workflow = (ROOT / "template/.github/workflows/galaxy-validate.yml").read_text(
            encoding="utf-8"
        )
        run_steps = self.workflow_run_steps()
        self.assertEqual(len(run_steps), 1)
        script = run_steps[0]
        self.assertNotEqual(script.strip(), "galaxy validate . --gate")
        for required in (
            "https://github.com/JustMik4/Galaxy-Orchestrator",
            "galaxy.lock", "source_revision", "version",
            "^[0-9a-fA-F]{40}$", "FETCH_HEAD", "rev-parse", "galaxy.py",
            "bootstrap", "validate", "--gate",
        ):
            self.assertIn(required, script)
        self.assertLess(script.index("bootstrap"), script.index("validate"))
        self.assertEqual(script.count("Join-Path $toolRoot 'galaxy.py'"), 2)
        self.assertIn("shell: pwsh", workflow)
        self.assertIn("persist-credentials: false", workflow)

        parser = subprocess.run(
            [
                "pwsh", "-NoProfile", "-Command",
                "$tokens=$null;$errors=$null;"
                "[System.Management.Automation.Language.Parser]::ParseInput("
                "$env:GALAXY_WORKFLOW_SCRIPT,[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count){$errors|ForEach-Object Message;exit 1}",
            ],
            env={**os.environ, "GALAXY_WORKFLOW_SCRIPT": script},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(parser.returncode, 0, parser.stdout + parser.stderr)

        (self.project / "galaxy.lock").write_text(json.dumps({
            "galaxy": {"version": "2.0.0", "source_revision": "main; whoami"},
        }), encoding="utf-8")
        executed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            env={**os.environ, "GITHUB_WORKSPACE": str(self.project)},
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(executed.returncode, 0)
        self.assertIn("source_revision", executed.stderr)

        lock = json.loads((ROOT / "template/galaxy.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["galaxy"]["source_revision"], "v2.0.0")


if __name__ == "__main__":
    unittest.main()
