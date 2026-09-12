import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_project_config_rejects_root_vault_path(self):
        path = self.root / '.galaxy/project.yml'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['vault']['enabled'] = True
        config['vault']['path'] = '.'
        path.write_text(json.dumps(config), encoding='utf-8')
        update_declaration_hashes(self.root)

        with self.assertRaisesRegex(ProjectConfigurationError, 'project-relative'):
            load_project(self.root)

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

    def test_load_project_parses_exact_authenticated_snapshot_bytes(self):
        checks_path = self.root / ".galaxy/checks.json"
        real_read_text = Path.read_text

        def swap_after_snapshot(path, *args, **kwargs):
            if path == checks_path:
                return json.dumps({
                    "schema_version": 1,
                    "commands": [["untrusted-after-snapshot"]],
                })
            return real_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", swap_after_snapshot):
            project = load_project(self.root)

        self.assertEqual(project.checks.commands, ())

    def test_load_project_rejects_symlinked_declaration(self):
        declaration = self.root / '.galaxy/project.yml'
        target = self.root.parent / 'project-declaration-target.yml'
        target.write_bytes(declaration.read_bytes())
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        try:
            declaration.unlink()
            declaration.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'declaration symlink unavailable: {exc}')
        self.addCleanup(lambda: declaration.unlink(missing_ok=True))

        with self.assertRaisesRegex(ProjectConfigurationError, 'link/reparse'):
            load_project(self.root)

    def test_load_project_rejects_symlinked_declaration_ancestor(self):
        galaxy = self.root / '.galaxy'
        redirected = self.root.parent / 'redirected-galaxy'
        redirected.mkdir()
        for name in ('project.yml', 'team.yml', 'checks.json'):
            shutil.copy2(galaxy / name, redirected / name)
        self.addCleanup(lambda: shutil.rmtree(redirected, ignore_errors=True))
        try:
            galaxy.rename(self.root / '.galaxy-original')
            if os.name == 'nt':
                created = subprocess.run(
                    ['cmd', '/c', 'mklink', '/J', str(galaxy), str(redirected)],
                    text=True, capture_output=True, check=False,
                )
                if created.returncode:
                    raise OSError(created.stderr.strip() or 'junction creation failed')
            else:
                galaxy.symlink_to(redirected, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            original = self.root / '.galaxy-original'
            if original.exists() and not galaxy.exists():
                original.rename(galaxy)
            self.skipTest(f'declaration ancestor symlink unavailable: {exc}')
        original = self.root / '.galaxy-original'

        def restore_galaxy_directory():
            if galaxy.is_symlink():
                galaxy.unlink()
            elif getattr(galaxy, 'is_junction', lambda: False)():
                galaxy.rmdir()
            if original.exists():
                original.rename(galaxy)

        self.addCleanup(restore_galaxy_directory)

        with self.assertRaisesRegex(ProjectConfigurationError, 'link/reparse'):
            load_project(self.root)

    def test_load_project_rejects_redirected_project_root(self):
        alias = self.root.parent / 'project-root-alias'
        try:
            if os.name == 'nt':
                created = subprocess.run(
                    ['cmd', '/c', 'mklink', '/J', str(alias), str(self.root)],
                    text=True, capture_output=True, check=False,
                )
                if created.returncode:
                    raise OSError(created.stderr.strip() or 'junction creation failed')
            else:
                alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'project root redirection unavailable: {exc}')

        def remove_alias():
            if getattr(alias, 'is_junction', lambda: False)():
                alias.rmdir()
            else:
                alias.unlink(missing_ok=True)

        self.addCleanup(remove_alias)
        with self.assertRaisesRegex(ProjectConfigurationError, 'link/reparse'):
            load_project(alias)

    def test_template_lock_authenticates_exact_canonical_declaration_bytes(self):
        template = Path(__file__).resolve().parents[2] / "template"
        lock = json.loads((template / "galaxy.lock").read_text(encoding="utf-8"))
        self.assertEqual(set(lock["declarations"]), set(TRACKED_DECLARATIONS))
        self.assertEqual(lock["declarations"], {
            relative: hashlib.sha256((template / relative).read_bytes()).hexdigest()
            for relative in TRACKED_DECLARATIONS
        })

    def test_template_declaration_bytes_survive_autocrlf_checkout(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git unavailable")
        repository = Path(__file__).resolve().parents[2]
        template = repository / "template"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            checkout = Path(temporary) / "checkout"
            source.mkdir()
            for relative in (*TRACKED_DECLARATIONS, "galaxy.lock"):
                target = source / "template" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template / relative, target)
            attributes = repository / ".gitattributes"
            if attributes.is_file():
                shutil.copy2(attributes, source / ".gitattributes")
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(
                ["git", "config", "core.autocrlf", "false"], cwd=source, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=source, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Galaxy Test"], cwd=source, check=True
            )
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "fixture"], cwd=source, check=True
            )
            subprocess.run(
                ["git", "-c", "core.autocrlf=true", "clone", "-q", source, checkout],
                check=True,
            )

            try:
                loaded = load_project(checkout / "template")
            except ProjectConfigurationError as exc:
                self.fail(f"autocrlf checkout must preserve declaration bytes: {exc}")

            self.assertEqual(loaded.team.mode, "SOLO")
            for relative in TRACKED_DECLARATIONS:
                self.assertEqual(
                    (checkout / "template" / relative).read_bytes(),
                    (template / relative).read_bytes(),
                    relative,
                )

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
