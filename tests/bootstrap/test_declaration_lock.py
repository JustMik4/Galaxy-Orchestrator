import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import declaration_lock
from lib.project import (
    LOCKED_DECLARATION_PATHS,
    ProjectConfigurationError,
    load_project,
)
from tests.bootstrap.test_project import write_project


ROOT = Path(__file__).resolve().parents[2]


class DeclarationLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="galaxy lock ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        write_project(self.root)
        self.lock_path = self.root / "galaxy.lock"

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "galaxy.py"), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def reset_project(self):
        shutil.rmtree(self.root)
        self.root.mkdir()
        write_project(self.root)

    def test_legitimate_team_and_checks_edits_can_be_checked_and_synced(self):
        team_path = self.root / ".galaxy/team.yml"
        checks_path = self.root / ".galaxy/checks.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team["mode"] = "CO-OP"
        team_path.write_text(json.dumps(team), encoding="utf-8")
        checks_path.write_text(json.dumps({
            "schema_version": 1,
            "commands": [[sys.executable, "-m", "unittest", "discover"]],
        }), encoding="utf-8")

        before = self.lock_path.read_bytes()
        checked = declaration_lock.sync(self.root, check=True)
        self.assertFalse(checked["applied"])
        self.assertEqual(
            checked["stale"],
            [".galaxy/checks.json", ".galaxy/team.yml"],
        )
        self.assertEqual(self.lock_path.read_bytes(), before)

        applied = declaration_lock.sync(self.root)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["written"], ["galaxy.lock"])
        self.assertEqual(
            set(json.loads(self.lock_path.read_text())["declarations"]),
            set(LOCKED_DECLARATION_PATHS),
        )
        project = load_project(self.root)
        self.assertEqual(project.team.mode, "CO-OP")
        self.assertEqual(project.checks.commands[0][1:3], ("-m", "unittest"))

    def test_every_invalid_declaration_is_rejected_without_writing(self):
        invalid = {
            "AGENTS.md": b"\xff",
            ".galaxy/project.yml": json.dumps({
                "schema_version": 1, "adapter": "unsafe"
            }).encode(),
            ".galaxy/team.yml": json.dumps({
                "schema_version": 1, "mode": "SHARED", "operators": []
            }).encode(),
            ".galaxy/checks.json": json.dumps({
                "schema_version": 1, "commands": ["python -m unittest"]
            }).encode(),
        }
        for relative, content in invalid.items():
            with self.subTest(relative=relative):
                self.reset_project()
                self.root.joinpath(*relative.split("/")).write_bytes(content)
                before = self.lock_path.read_bytes()

                with self.assertRaises(ProjectConfigurationError):
                    declaration_lock.sync(self.root)

                self.assertEqual(self.lock_path.read_bytes(), before)

    def test_invalid_lock_is_not_repaired_and_unrelated_fields_are_preserved(self):
        lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
        lock["release_channel"] = "stable"
        self.lock_path.write_text(json.dumps(lock), encoding="utf-8")
        (self.root / "AGENTS.md").write_text("updated rules\n", encoding="utf-8")
        declaration_lock.sync(self.root)
        self.assertEqual(
            json.loads(self.lock_path.read_text(encoding="utf-8"))["release_channel"],
            "stable",
        )

        invalid = json.loads(self.lock_path.read_text(encoding="utf-8"))
        invalid["declarations"]["AGENTS.md"] = "A" * 64
        self.lock_path.write_text(json.dumps(invalid), encoding="utf-8")
        before = self.lock_path.read_bytes()
        with self.assertRaisesRegex(ProjectConfigurationError, "lowercase SHA-256"):
            declaration_lock.sync(self.root)
        self.assertEqual(self.lock_path.read_bytes(), before)

    def test_check_is_read_only_and_apply_is_idempotent(self):
        declaration = self.root / "AGENTS.md"
        declaration.write_bytes(declaration.read_bytes() + b"\nProject rule.\n")
        before = self.lock_path.read_bytes()
        before_stat = self.lock_path.stat()

        checked = declaration_lock.sync(self.root, check=True)
        self.assertEqual(checked["status"], "stale")
        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(self.lock_path.stat().st_mtime_ns, before_stat.st_mtime_ns)

        first = declaration_lock.sync(self.root)
        after = self.lock_path.read_bytes()
        after_stat = self.lock_path.stat()
        second = declaration_lock.sync(self.root)
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["status"], "current")
        self.assertEqual(self.lock_path.read_bytes(), after)
        self.assertEqual(self.lock_path.stat().st_mtime_ns, after_stat.st_mtime_ns)

    def test_concurrent_declaration_or_lock_change_prevents_replace(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        original_atomic_write = declaration_lock._atomic_write

        for target in (checks_path, self.lock_path):
            with self.subTest(target=target.name):
                self.reset_project()
                checks_path.write_text(json.dumps({
                    "schema_version": 1, "commands": [["python", "-V"]]
                }), encoding="utf-8")
                before = self.lock_path.read_bytes()

                def change_then_write(path, data, unchanged, *, changed=target):
                    changed.write_bytes(changed.read_bytes() + b" ")
                    return original_atomic_write(path, data, unchanged)

                with patch.object(declaration_lock, "_atomic_write", change_then_write):
                    with self.assertRaisesRegex(
                        ProjectConfigurationError, "changed concurrently"
                    ):
                        declaration_lock.sync(self.root)

                expected = before + (b" " if target == self.lock_path else b"")
                self.assertEqual(self.lock_path.read_bytes(), expected)

    def test_project_relation_mismatch_and_missing_declaration_are_rejected(self):
        project_path = self.root / ".galaxy/project.yml"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["specialists"]["names"] = ["reviewer"]
        project_path.write_text(json.dumps(project), encoding="utf-8")
        before = self.lock_path.read_bytes()
        with self.assertRaisesRegex(ProjectConfigurationError, "specialists"):
            declaration_lock.sync(self.root)
        self.assertEqual(self.lock_path.read_bytes(), before)

        self.reset_project()
        (self.root / "AGENTS.md").unlink()
        before = self.lock_path.read_bytes()
        with self.assertRaisesRegex(ProjectConfigurationError, "missing"):
            declaration_lock.sync(self.root)
        self.assertEqual(self.lock_path.read_bytes(), before)

    def test_declaration_symlink_is_rejected_without_following_it(self):
        target = self.root.parent / "outside-checks.json"
        target.write_text(json.dumps({"schema_version": 1, "commands": []}))
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.unlink()
        try:
            checks_path.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        before = self.lock_path.read_bytes()

        with self.assertRaisesRegex(ProjectConfigurationError, "link/reparse"):
            declaration_lock.sync(self.root)

        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8")),
            {"schema_version": 1, "commands": []},
        )

    def test_cli_help_argument_order_check_exit_and_idempotence(self):
        help_result = self.run_cli("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("lock sync PROJECT [--check]", help_result.stdout)

        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        before = self.lock_path.read_bytes()
        checked = self.run_cli("lock", "sync", "--check", self.root)
        self.assertEqual(checked.returncode, 1)
        self.assertEqual(json.loads(checked.stdout)["status"], "stale")
        self.assertEqual(self.lock_path.read_bytes(), before)

        applied = self.run_cli("lock", "sync", self.root)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertTrue(json.loads(applied.stdout)["applied"])
        after = self.lock_path.read_bytes()
        repeated = self.run_cli("lock", "sync", self.root, "--check")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["status"], "current")
        self.assertEqual(self.lock_path.read_bytes(), after)


if __name__ == "__main__":
    unittest.main()
