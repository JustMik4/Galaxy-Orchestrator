import json
import os
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

                def change_then_write(path, data, unchanged, *args, changed=target, **kwargs):
                    changed.write_bytes(changed.read_bytes() + b" ")
                    return original_atomic_write(path, data, unchanged, *args, **kwargs)

                with patch.object(declaration_lock, "_atomic_write", change_then_write):
                    with self.assertRaisesRegex(
                        ProjectConfigurationError, "changed concurrently"
                    ):
                        declaration_lock.sync(self.root)

                expected = before + (b" " if target == self.lock_path else b"")
                self.assertEqual(self.lock_path.read_bytes(), expected)

    def test_root_identity_change_to_identical_clone_is_rejected(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        original_atomic_write = declaration_lock._atomic_write
        moved = self.root.with_name("moved-project")
        replacement_lock = None

        def redirect_then_write(path, data, unchanged, *args, **kwargs):
            nonlocal replacement_lock
            self.root.rename(moved)
            shutil.copytree(moved, self.root)
            replacement_lock = (self.root / "galaxy.lock").read_bytes()
            return original_atomic_write(path, data, unchanged, *args, **kwargs)

        try:
            with patch.object(declaration_lock, "_atomic_write", redirect_then_write):
                with self.assertRaises((OSError, ProjectConfigurationError)):
                    declaration_lock.sync(self.root)
            if replacement_lock is not None:
                self.assertEqual((self.root / "galaxy.lock").read_bytes(), replacement_lock)
        finally:
            if moved.exists():
                if self.root.exists():
                    shutil.rmtree(self.root)
                moved.rename(self.root)

    def test_ancestor_identity_change_to_identical_clone_is_rejected(self):
        ancestor = self.root.parent / "nested-boundary"
        project = ancestor / "project"
        project.mkdir(parents=True)
        write_project(project)
        checks_path = project / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        moved = ancestor.with_name("moved-boundary")
        original_atomic_write = declaration_lock._atomic_write
        replacement_before = None

        def redirect_then_write(path, data, unchanged, *args, **kwargs):
            nonlocal replacement_before
            ancestor.rename(moved)
            shutil.copytree(moved, ancestor)
            replacement_before = (project / "galaxy.lock").read_bytes()
            return original_atomic_write(path, data, unchanged, *args, **kwargs)

        try:
            with patch.object(declaration_lock, "_atomic_write", redirect_then_write):
                with self.assertRaises((OSError, ProjectConfigurationError)):
                    declaration_lock.sync(project)
            if replacement_before is not None:
                self.assertEqual(
                    (project / "galaxy.lock").read_bytes(), replacement_before
                )
        finally:
            if moved.exists():
                if ancestor.exists():
                    shutil.rmtree(ancestor)
                moved.rename(ancestor)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_redirection_never_writes_outside_lock(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        outside = self.root.parent / "outside-project"
        shutil.copytree(self.root, outside)
        outside_lock = outside / "galaxy.lock"
        outside_before = outside_lock.read_bytes()
        moved = self.root.with_name("moved-project")
        original_atomic_write = declaration_lock._atomic_write

        def junction_then_write(path, data, unchanged, *args, **kwargs):
            self.root.rename(moved)
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(self.root), str(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            if created.returncode:
                raise OSError(created.stderr or created.stdout)
            return original_atomic_write(path, data, unchanged, *args, **kwargs)

        try:
            with patch.object(declaration_lock, "_atomic_write", junction_then_write):
                with self.assertRaises((OSError, ProjectConfigurationError)):
                    declaration_lock.sync(self.root)
            self.assertEqual(outside_lock.read_bytes(), outside_before)
        finally:
            if self.root.is_junction():
                self.root.rmdir()
            if moved.exists():
                moved.rename(self.root)

    @unittest.skipUnless(os.name == "nt", "Windows handle regression")
    def test_windows_guard_holds_every_ancestor_and_root_identity(self):
        root = declaration_lock._checked_root(self.root)
        with declaration_lock._BoundaryGuard.acquire(root) as boundary:
            self.assertEqual(
                len(boundary.windows_handles),
                len(boundary.identities),
            )
            self.assertEqual(
                [declaration_lock._windows_handle_inode(handle)
                 for handle in boundary.windows_handles],
                [identity.inode for identity in boundary.identities],
            )

    @unittest.skipUnless(os.name == "nt", "Windows handle regression")
    def test_windows_promotes_the_exact_verified_staged_handle(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        real_rename = declaration_lock._rename_windows_stage

        with patch.object(
            declaration_lock, "_rename_windows_stage", wraps=real_rename
        ) as rename:
            result = declaration_lock.sync(self.root)

        self.assertTrue(result["applied"])
        rename.assert_called_once()
        descriptor, root_handle = rename.call_args.args
        self.assertIsInstance(descriptor, int)
        self.assertIsInstance(root_handle, int)
        self.assertEqual(load_project(self.root).checks.commands, (("python", "-V"),))

    @unittest.skipUnless(os.name == "nt", "Windows handle regression")
    def test_stage_setup_failures_close_handle_and_remove_exact_temp(self):
        real_open = declaration_lock._open_windows_stage
        real_fstat = declaration_lock.os.fstat
        for failure in ("fstat",):
            with self.subTest(failure=failure):
                self.reset_project()
                checks_path = self.root / ".galaxy/checks.json"
                checks_path.write_text(json.dumps({
                    "schema_version": 1, "commands": [["python", "-V"]]
                }), encoding="utf-8")
                before = self.lock_path.read_bytes()
                descriptors = []

                def tracked_open(path):
                    descriptor = real_open(path)
                    descriptors.append(descriptor)
                    return descriptor

                def injected_fstat(descriptor):
                    if failure == "fstat" and descriptor in descriptors:
                        raise OSError("injected stage fstat failure")
                    return real_fstat(descriptor)

                try:
                    with patch.object(
                        declaration_lock, "_open_windows_stage", tracked_open
                    ), patch.object(
                        declaration_lock.os, "fstat", side_effect=injected_fstat
                    ):
                        with self.assertRaises(OSError):
                            declaration_lock.sync(self.root)

                    self.assertEqual(list(self.root.glob(".galaxy.lock.*.tmp")), [])
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            real_fstat(descriptor)
                    moved = self.root.with_name("post-failure-move")
                    self.root.rename(moved)
                    moved.rename(self.root)
                    self.assertEqual(self.lock_path.read_bytes(), before)
                finally:
                    for descriptor in descriptors:
                        try:
                            real_fstat(descriptor)
                        except OSError:
                            continue
                        os.close(descriptor)
                    for temporary in self.root.glob(".galaxy.lock.*.tmp"):
                        temporary.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows handle regression")
    def test_windows_stage_does_not_require_fchmod(self):
        self.reset_project()
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")

        with patch.object(
            declaration_lock.os,
            "fchmod",
            create=True,
            side_effect=AssertionError("Windows staging must not call os.fchmod"),
        ):
            result = declaration_lock.sync(self.root)

        self.assertTrue(result["applied"])
        self.assertEqual(list(self.root.glob(".galaxy.lock.*.tmp")), [])

    def test_staged_temp_byte_tamper_is_rejected_without_install(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        before = self.lock_path.read_bytes()
        original_atomic_write = declaration_lock._atomic_write

        def tamper_then_write(path, data, unchanged, *args, **kwargs):
            def tamper_after_guard():
                unchanged()
                staged = list(path.parent.glob(".galaxy.lock.*.tmp"))
                self.assertEqual(len(staged), 1)
                staged[0].write_bytes(b'{"tampered":true}\n')

            return original_atomic_write(path, data, tamper_after_guard, *args, **kwargs)

        with patch.object(declaration_lock, "_atomic_write", tamper_then_write):
            with self.assertRaisesRegex(ProjectConfigurationError, "staged galaxy.lock"):
                declaration_lock.sync(self.root)

        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".galaxy.lock.*.tmp")), [])

    def test_exact_staged_bytes_are_verified_before_install(self):
        checks_path = self.root / ".galaxy/checks.json"
        checks_path.write_text(json.dumps({
            "schema_version": 1, "commands": [["python", "-V"]]
        }), encoding="utf-8")
        before = self.lock_path.read_bytes()
        real_read = os.read
        tampered = False

        def tamper_staged_descriptor(descriptor, size):
            nonlocal tampered
            if not tampered:
                tampered = True
                invalid = b'{"tampered":true}\n'
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, invalid)
                os.ftruncate(descriptor, len(invalid))
                os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
            return real_read(descriptor, size)

        with patch.object(declaration_lock.os, "read", tamper_staged_descriptor):
            with self.assertRaisesRegex(
                ProjectConfigurationError, "staged galaxy.lock bytes"
            ):
                declaration_lock.sync(self.root)

        self.assertTrue(tampered)
        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".galaxy.lock.*.tmp")), [])

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
