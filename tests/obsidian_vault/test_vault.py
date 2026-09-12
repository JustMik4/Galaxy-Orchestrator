import json
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


if __name__ == '__main__':
    unittest.main()
