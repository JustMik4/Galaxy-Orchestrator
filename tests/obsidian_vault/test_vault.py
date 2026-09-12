import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.vault import (
    VaultError, validate_internal_vault_path, render_task_note, status, sync,
)


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'project'
        self.project.mkdir()
        self.snapshot = {'task_id': 'T-1', 'title': 'Build feature', 'status': 'claimed',
                         'owner': 'alice', 'revision': 3, 'source_receipt': 'r-3',
                         'updated_at': '2026-09-12T12:00:00Z',
                         'prompt': 'do not export', 'response': 'do not export',
                         'telemetry': {'tokens': 9}, 'secret': 'x', 'account_id': 'private'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_internal_path_is_project_relative_and_safe(self):
        self.assertEqual(validate_internal_vault_path(self.project, '.galaxy/vault'),
                         self.project / '.galaxy' / 'vault')
        for value in ('../vault', '/outside', 'C:/outside', '.galaxy/../vault'):
            with self.subTest(value=value):
                with self.assertRaises(VaultError):
                    validate_internal_vault_path(self.project, value)

    def test_render_is_deterministic_and_has_frontmatter(self):
        first = render_task_note(self.snapshot)
        second = render_task_note(self.snapshot)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('---\n'))
        self.assertIn('task_id: "T-1"', first)
        self.assertIn('source_receipt: "r-3"', first)
        self.assertIn('revision: 3', first)
        self.assertIn('galaxy_schema: 1', first)
        self.assertIn('status: "claimed"', first)
        self.assertIn('owner: "alice"', first)
        for word in ('do not export', 'tokens', 'account_id', 'secret'):
            self.assertNotIn(word, first)

    def test_dependencies_must_be_scalar_task_identifiers_and_never_leak_nested_secrets(self):
        nested = {**self.snapshot, 'dependencies': [{'task_id': 'T-0', 'token': 'example-secret'}]}
        with self.assertRaisesRegex(VaultError, 'dependencies'):
            render_task_note(nested)
        self.assertNotIn('example-secret', render_task_note({
            **self.snapshot, 'dependencies': ['T-0', 'T-2'],
        }))

    def test_include_and_exclude_policy_is_applied_before_rendering(self):
        without_tasks = sync(self.project, {
            'enabled': True, 'path': '.galaxy/vault', 'include': ['summaries'],
        }, [self.snapshot])
        self.assertNotIn('tasks/T-1.md', without_tasks['written'])
        self.assertFalse((self.project / '.galaxy/vault/tasks/T-1.md').exists())

        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        result = sync(other, {
            'enabled': True, 'path': '.galaxy/vault', 'include': ['tasks'],
            'exclude': ['dependencies', 'risk'],
        }, [{**self.snapshot, 'dependencies': ['T-0'], 'risk': 'critical'}])
        note = other / '.galaxy/vault/tasks/T-1.md'
        self.assertIn('tasks/T-1.md', result['written'])
        self.assertNotIn('dependencies:', note.read_text(encoding='utf-8'))
        self.assertNotIn('risk:', note.read_text(encoding='utf-8'))

    def test_exclude_cannot_remove_authoritative_frontmatter(self):
        with self.assertRaisesRegex(VaultError, 'required vault fields'):
            sync(self.project, {
                'enabled': True, 'path': '.galaxy/vault',
                'exclude': ['source_receipt'],
            }, [self.snapshot])

    def test_sync_creates_obsidian_marker_and_notes(self):
        result = sync(self.project, {'enabled': True, 'path': '.galaxy/vault'}, [self.snapshot])
        vault = self.project / '.galaxy' / 'vault'
        self.assertEqual(result['written'], ['.obsidian/app.json', 'tasks/T-1.md'])
        self.assertTrue((vault / '.obsidian/app.json').is_file())
        self.assertTrue((vault / 'tasks/T-1.md').is_file())
        self.assertEqual(json.loads((vault / '.obsidian/app.json').read_text()), {})

    def test_check_and_disabled_do_not_write(self):
        self.assertEqual(sync(self.project, {'enabled': False}, [self.snapshot])['written'], [])
        result = sync(self.project, {'enabled': True, 'path': '.galaxy/vault'}, [self.snapshot], check=True)
        self.assertEqual(result['written'], [])
        self.assertFalse((self.project / '.galaxy').exists())

    def test_drift_refuses_and_force_backs_up(self):
        config = {'enabled': True, 'path': '.galaxy/vault'}
        sync(self.project, config, [self.snapshot])
        note = self.project / '.galaxy/vault/tasks/T-1.md'
        note.write_text('human edit', encoding='utf-8')
        with self.assertRaises(VaultError):
            sync(self.project, config, [self.snapshot])
        result = sync(self.project, config, [self.snapshot], force=True)
        self.assertTrue(result['backup'])
        self.assertEqual(note.read_text(encoding='utf-8'), render_task_note(self.snapshot))
        self.assertTrue(Path(result['backup'], 'tasks/T-1.md').is_file())

    def test_stale_projection_requires_force_and_is_backed_up(self):
        config = {'enabled': True, 'path': '.galaxy/vault'}
        sync(self.project, config, [self.snapshot])
        with self.assertRaises(VaultError):
            sync(self.project, config, [])
        result = sync(self.project, config, [], force=True)
        self.assertTrue(Path(result['backup'], 'tasks/T-1.md').is_file())
        self.assertFalse((self.project / '.galaxy/vault/tasks/T-1.md').exists())

    def test_force_never_allows_revision_downgrade_or_equal_receipt_mismatch(self):
        config = {'enabled': True, 'path': '.galaxy/vault'}
        current = {**self.snapshot, 'revision': 7, 'source_receipt': 'receipt-7'}
        sync(self.project, config, [current])
        note = self.project / '.galaxy/vault/tasks/T-1.md'
        before = note.read_bytes()
        for stale in (
            {**self.snapshot, 'revision': 6, 'source_receipt': 'receipt-6'},
            {**self.snapshot, 'revision': 7, 'source_receipt': 'different-receipt'},
        ):
            with self.subTest(stale=stale):
                with self.assertRaisesRegex(VaultError, 'revision|receipt'):
                    sync(self.project, config, [stale], force=True)
                self.assertEqual(note.read_bytes(), before)

    def test_sync_rejects_missing_authoritative_receipt(self):
        with self.assertRaisesRegex(VaultError, 'source_receipt'):
            sync(self.project, {'enabled': True, 'path': '.galaxy/vault'}, [
                {**self.snapshot, 'source_receipt': ''},
            ])

    @unittest.skipUnless(os.name == 'nt', 'junction regression is Windows-specific')
    def test_descendant_tasks_junction_cannot_write_outside_vault(self):
        vault = self.project / '.galaxy/vault'
        vault.mkdir(parents=True)
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        junction = vault / 'tasks'
        created = subprocess.run([
            'cmd', '/c', 'mklink', '/J', str(junction), str(outside),
        ], text=True, capture_output=True, check=False)
        if created.returncode:
            self.skipTest('directory junction creation unavailable')
        self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)

        with self.assertRaisesRegex(VaultError, 'symlink/reparse'):
            sync(self.project, {'enabled': True, 'path': '.galaxy/vault'}, [self.snapshot], force=True)

        self.assertFalse((outside / 'T-1.md').exists())

    @unittest.skipUnless(os.name == 'nt', 'junction regression is Windows-specific')
    def test_status_rejects_deep_descendant_junction_even_when_not_expected(self):
        vault = self.project / '.galaxy/vault'
        archive = vault / 'tasks/archive'
        archive.parent.mkdir(parents=True)
        outside = Path(self.tmp.name) / 'outside-status'
        outside.mkdir()
        created = subprocess.run([
            'cmd', '/c', 'mklink', '/J', str(archive), str(outside),
        ], text=True, capture_output=True, check=False)
        if created.returncode:
            self.skipTest('directory junction creation unavailable')
        self.addCleanup(lambda: archive.rmdir() if archive.exists() else None)

        with self.assertRaisesRegex(VaultError, 'symlink/reparse'):
            status(self.project, {'enabled': True, 'path': '.galaxy/vault'}, [self.snapshot])

    def test_project_owned_conflict_preserves_human_note_and_incoming_snapshot(self):
        config = {'enabled': True, 'path': '.galaxy/vault', 'mode': 'project-owned'}
        sync(self.project, config, [self.snapshot])
        note = self.project / '.galaxy/vault/tasks/T-1.md'
        human = note.read_text(encoding='utf-8') + '\nHuman decision: keep this paragraph.\n'
        note.write_text(human, encoding='utf-8')
        incoming = {**self.snapshot, 'revision': 4, 'source_receipt': 'r-4'}

        result = sync(self.project, config, [incoming], force=True)

        self.assertEqual(note.read_text(encoding='utf-8'), human)
        self.assertEqual(len(result['conflicts']), 1)
        conflict = self.project / '.galaxy/vault' / result['conflicts'][0]
        self.assertTrue(conflict.is_file())
        self.assertEqual(conflict.read_text(encoding='utf-8'), render_task_note(incoming))
        again = sync(self.project, config, [incoming])
        self.assertEqual(again['conflicts'], result['conflicts'])
        self.assertEqual(note.read_text(encoding='utf-8'), human)

    def test_project_owned_stale_note_is_never_deleted(self):
        config = {'enabled': True, 'path': '.galaxy/vault', 'mode': 'project-owned'}
        sync(self.project, config, [self.snapshot])
        note = self.project / '.galaxy/vault/tasks/T-1.md'
        result = sync(self.project, config, [], force=True)
        self.assertTrue(note.is_file())
        self.assertIn('tasks/T-1.md', result['drift'])

    def test_operator_local_override_selects_external_vault(self):
        external = Path(self.tmp.name) / 'personal-vault'
        local = self.project / '.galaxy/local'
        local.mkdir(parents=True)
        quoted = json.dumps(str(external))
        (local / 'operator.toml').write_text(
            '[vault]\nexternal = true\npath = ' + quoted + '\n', encoding='utf-8'
        )
        config = {'enabled': True, 'path': '.galaxy/vault', 'mode': 'projection'}
        result = sync(self.project, config, [self.snapshot])
        self.assertEqual(Path(result['path']), external)
        self.assertTrue((external / 'tasks/T-1.md').is_file())

    def _write_external_override(self, target):
        local = self.project / '.galaxy/local'
        local.mkdir(parents=True, exist_ok=True)
        (local / 'operator.toml').write_text(
            '[vault]\nexternal = true\npath = ' + json.dumps(str(target)) + '\n',
            encoding='utf-8',
        )

    def _assert_external_boundary_rejected(self, target):
        self._write_external_override(target)
        config = {'enabled': True, 'path': '.galaxy/vault', 'mode': 'projection'}
        for operation in (
            lambda: status(self.project, config, [self.snapshot]),
            lambda: sync(self.project, config, [self.snapshot], force=True),
        ):
            with self.subTest(operation=operation.__code__.co_firstlineno):
                with self.assertRaisesRegex(
                    VaultError, 'external vault path must be disjoint from project'
                ):
                    operation()
        self.assertFalse((target / '.obsidian/app.json').exists())

    def test_external_override_cannot_equal_project(self):
        self._assert_external_boundary_rejected(self.project)

    def test_external_override_cannot_be_parent_of_project(self):
        self._assert_external_boundary_rejected(self.project.parent)

    def test_external_override_cannot_be_child_of_project(self):
        self._assert_external_boundary_rejected(self.project / 'personal-vault')


if __name__ == '__main__':
    unittest.main()
