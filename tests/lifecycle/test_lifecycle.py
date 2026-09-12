import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from lib.lifecycle import (
    apply_branch_cleanup, apply_runtime_cleanup, branch_cleanup_plan,
    plan_runtime_cleanup, plan_worktree_cleanup,
)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Lifecycle Test")
        (self.repo / "README").write_text("base\n")
        git(self.repo, "add", "README")
        git(self.repo, "commit", "-m", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def branch(self, name, message="change"):
        git(self.repo, "branch", name)
        return git(self.repo, "rev-parse", name)

    def test_merged_old_branch_is_safe_and_preview_does_not_mutate(self):
        head = self.branch("galaxy/old")
        # Reachability is enough for a local repository with no remote.
        plan = branch_cleanup_plan(self.repo, retention_days=0, now=time.time())
        candidate = next(item for item in plan.candidates if item.name == "galaxy/old")
        self.assertTrue(candidate.safe)
        self.assertEqual(plan.targets, ("galaxy/old",))
        result = apply_branch_cleanup(plan)
        self.assertFalse(result["applied"])
        self.assertIn("galaxy/old", git(self.repo, "branch", "--list"))
        self.assertEqual(candidate.head, head)

    def test_retention_active_pending_and_dirty_are_not_safe(self):
        self.branch("galaxy/new")
        self.branch("codex/legacy")
        self.branch("galaxy/active")
        plan = branch_cleanup_plan(self.repo, retention_days=3, now=time.time(), metadata={
            "active_tasks": ["galaxy/active"],
            "pending_recovery": ["codex/legacy"],
        })
        by_name = {item.name: item for item in plan.candidates}
        self.assertIn("retention-not-elapsed", by_name["galaxy/new"].reasons)
        self.assertIn("active-task", by_name["galaxy/active"].reasons)
        self.assertIn("pending-recovery", by_name["codex/legacy"].reasons)

    def test_unknown_remote_is_unsafe(self):
        self.branch("galaxy/remote")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        plan = branch_cleanup_plan(self.repo, retention_days=0, now=time.time(),
                                   metadata={"branches": {"galaxy/remote": {"merged": True}}})
        candidate = next(item for item in plan.candidates if item.name == "galaxy/remote")
        self.assertFalse(candidate.safe)
        self.assertIn("remote-unknown", candidate.reasons)

    def test_dirty_worktree_is_refused(self):
        branch = self.branch("galaxy/dirty")
        worktree = self.repo.parent / (self.repo.name + "-dirty-worktree")
        git(self.repo, "worktree", "add", str(worktree), "galaxy/dirty")
        (worktree / "untracked").write_text("do not remove\n")
        plan = plan_worktree_cleanup(self.repo, metadata={"completed_tasks": ["galaxy/dirty"]})
        item = next(item for item in plan.candidates if item.branch == "galaxy/dirty")
        self.assertEqual(item.head, branch)
        self.assertTrue(item.dirty)
        self.assertFalse(item.safe)
        self.assertIn("dirty-worktree", item.reasons)

    def test_runtime_preview_and_audit_protection(self):
        runtime = self.repo / ".galaxy" / "runtime"
        runtime.mkdir(parents=True)
        old = runtime / "old.tmp"
        old.write_text("cache")
        audit = runtime / "audit-evidence.json"
        audit.write_text("keep")
        old_stamp = time.time() - 10 * 86400
        os.utime(old, (old_stamp, old_stamp))
        os.utime(audit, (old_stamp, old_stamp))
        plan = plan_runtime_cleanup(self.repo, retention_days=3, now=time.time())
        self.assertEqual(plan.targets, (".galaxy/runtime/old.tmp",))
        self.assertTrue(audit.exists())
        self.assertFalse(apply_runtime_cleanup(plan)["applied"])
        self.assertTrue(old.exists())
        apply_runtime_cleanup(plan, apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(audit.exists())

    def test_apply_rejects_unvalidated_targets(self):
        self.branch("galaxy/old")
        plan = branch_cleanup_plan(self.repo, retention_days=0, now=time.time())
        with self.assertRaises(ValueError):
            apply_branch_cleanup(plan, apply=True, targets=("galaxy/not-the-plan",))

    def test_branch_changed_after_preview_is_refused(self):
        self.branch("galaxy/old")
        plan = branch_cleanup_plan(self.repo, retention_days=0, now=time.time())
        git(self.repo, "switch", "galaxy/old")
        (self.repo / "later").write_text("new work\n")
        git(self.repo, "add", "later")
        git(self.repo, "commit", "-m", "later")
        git(self.repo, "switch", "main")
        with self.assertRaisesRegex(RuntimeError, "changed after preview"):
            apply_branch_cleanup(plan, apply=True)

    def test_runtime_changed_after_preview_is_refused(self):
        runtime = self.repo / ".galaxy" / "runtime"
        runtime.mkdir(parents=True)
        target = runtime / "old.tmp"
        target.write_text("old")
        old_stamp = time.time() - 10 * 86400
        os.utime(target, (old_stamp, old_stamp))
        plan = plan_runtime_cleanup(self.repo, retention_days=3, now=time.time())
        target.write_text("recreated")
        with self.assertRaisesRegex(RuntimeError, "changed after preview"):
            apply_runtime_cleanup(plan, apply=True)
        self.assertTrue(target.exists())

    def test_runtime_replacement_with_same_mtime_and_size_is_refused_by_identity(self):
        runtime = self.repo / ".galaxy" / "runtime"
        runtime.mkdir(parents=True)
        target = runtime / "old.tmp"
        target.write_text("first")
        old_stamp = time.time() - 10 * 86400
        os.utime(target, (old_stamp, old_stamp))
        plan = plan_runtime_cleanup(self.repo, retention_days=3, now=time.time())
        replacement = runtime / "replacement.tmp"
        replacement.write_text("other")
        os.utime(replacement, (old_stamp, old_stamp))
        os.replace(replacement, target)

        with self.assertRaisesRegex(RuntimeError, "changed after preview"):
            apply_runtime_cleanup(plan, apply=True)
        self.assertEqual(target.read_text(), "other")

    @unittest.skipUnless(os.name == "nt", "junction regression is Windows-specific")
    def test_runtime_junction_to_product_source_is_rejected_without_deletion(self):
        source = self.repo / "src"
        source.mkdir()
        keep = source / "keep.py"
        keep.write_text("keep = True\n")
        old_stamp = time.time() - 10 * 86400
        os.utime(keep, (old_stamp, old_stamp))
        runtime = self.repo / ".galaxy/runtime"
        runtime.parent.mkdir(parents=True)
        created = subprocess.run([
            "cmd", "/c", "mklink", "/J", str(runtime), str(source),
        ], text=True, capture_output=True, check=False)
        if created.returncode:
            self.skipTest("directory junction creation unavailable")
        self.addCleanup(lambda: runtime.rmdir() if runtime.exists() else None)

        with self.assertRaisesRegex(ValueError, "link/reparse"):
            plan_runtime_cleanup(self.repo, retention_days=3, now=time.time())

        self.assertTrue(keep.is_file())


if __name__ == "__main__":
    unittest.main()
