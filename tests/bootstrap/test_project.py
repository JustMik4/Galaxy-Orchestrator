import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.project import (
    ProjectConfigurationError,
    load_project,
    project_pollution,
)


TRACKED_DECLARATIONS = (
    "AGENTS.md",
    ".galaxy/project.yml",
    ".galaxy/team.yml",
    ".galaxy/checks.json",
)


def update_declaration_hashes(root: Path) -> None:
    lock_path = root / "galaxy.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["declarations"] = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in TRACKED_DECLARATIONS
    }
    lock_path.write_text(json.dumps(lock), encoding="utf-8")


def write_project(root: Path) -> None:
    (root / "AGENTS.md").write_text("# Fixture instructions\n", encoding="utf-8")
    (root / ".galaxy").mkdir()
    (root / ".galaxy/project.yml").write_text(json.dumps({
        "schema_version": 1,
        "name": "fixture",
        "adapter": "codex",
        "specialists": {"packs": ["core"], "names": ["git"]},
        "vault": {"enabled": False, "path": ".galaxy/vault", "mode": "projection"},
    }), encoding="utf-8")
    (root / ".galaxy/team.yml").write_text(json.dumps({
        "schema_version": 1, "mode": "SOLO", "operators": []
    }), encoding="utf-8")
    (root / ".galaxy/checks.json").write_text(json.dumps({
        "schema_version": 1, "commands": []
    }), encoding="utf-8")
    (root / "galaxy.lock").write_text(json.dumps({
        "schema_version": 1,
        "galaxy": {"version": "2.0.0", "source_revision": "abc123"},
        "specialists": {"catalog_revision": "catalog-v1", "packs": ["core"], "names": ["git"]},
        "adapters": {"schema_version": 1, "codex": "codex-v1"},
        "project_schema": 1,
        "migration_schema": 1,
    }), encoding="utf-8")
    update_declaration_hashes(root)


class ProjectBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        write_project(self.root)

    def test_loads_all_canonical_declarations(self):
        project = load_project(self.root)
        self.assertEqual(project.config.name, "fixture")
        self.assertEqual(project.team.mode, "SOLO")
        self.assertEqual(project.lock.galaxy_version, "2.0.0")
        self.assertEqual(project.selected_specialists, ("git",))

    def test_lock_rejects_credentials_and_local_paths(self):
        path = self.root / "galaxy.lock"
        lock = json.loads(path.read_text())
        lock["token"] = "secret"
        path.write_text(json.dumps(lock))
        with self.assertRaises(ProjectConfigurationError):
            load_project(self.root)
        lock.pop("token")
        lock["galaxy"]["source_revision"] = "C:/Users/alice/source"
        path.write_text(json.dumps(lock))
        with self.assertRaises(ProjectConfigurationError):
            load_project(self.root)

    def test_lock_rejects_missing_extra_and_malformed_declaration_hashes(self):
        path = self.root / "galaxy.lock"
        original = json.loads(path.read_text(encoding="utf-8"))
        invalid_declarations = (
            {
                key: value for key, value in original["declarations"].items()
                if key != "AGENTS.md"
            },
            {**original["declarations"], "README.md": "0" * 64},
            {**original["declarations"], "AGENTS.md": "A" * 64},
            {**original["declarations"], "AGENTS.md": "0" * 63},
        )
        for declarations in invalid_declarations:
            with self.subTest(declarations=declarations):
                lock = dict(original)
                lock["declarations"] = declarations
                path.write_text(json.dumps(lock), encoding="utf-8")
                with self.assertRaises(ProjectConfigurationError):
                    load_project(self.root)

    def test_lock_rejects_changed_declaration_bytes(self):
        (self.root / ".galaxy/checks.json").write_text(
            json.dumps({"schema_version": 1, "commands": [["python", "-V"]]}),
            encoding="utf-8",
        )
        with self.assertRaises(ProjectConfigurationError):
            load_project(self.root)

    def test_template_lock_authenticates_exact_canonical_declaration_bytes(self):
        template = Path(__file__).resolve().parents[2] / "template"
        lock = json.loads((template / "galaxy.lock").read_text(encoding="utf-8"))
        self.assertEqual(set(lock["declarations"]), set(TRACKED_DECLARATIONS))
        self.assertEqual(lock["declarations"], {
            relative: hashlib.sha256((template / relative).read_bytes()).hexdigest()
            for relative in TRACKED_DECLARATIONS
        })

    def test_tracked_generated_and_legacy_files_are_reported_only(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git unavailable")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        generated = self.root / ".codex/agents/root.toml"
        generated.parent.mkdir(parents=True)
        generated.write_text("generated")
        legacy = self.root / ".multicontroller/tools/runtime.py"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        result = project_pollution(self.root)
        self.assertIn(".codex/agents/root.toml", result.tracked_generated)
        self.assertIn(".multicontroller/tools/runtime.py", result.tracked_legacy)
        self.assertTrue(generated.exists())
        self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
