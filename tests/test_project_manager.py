import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
if importlib.util.find_spec('project_manager'):
    import project_manager as pm
else:
    pm = None


class ProjectManagerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pm, 'project manager not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='mc menu ')
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name)/'AI'/'Projetos'

    def test_creates_named_project_and_environment(self):
        target = pm.create_project(ROOT, self.projects, 'Aplicação nova')
        self.assertEqual(target.name, 'Aplicação nova')
        self.assertTrue((target/'.codex/config.toml').is_file())

    def test_import_folder_preserves_original_and_skips_local_artifacts(self):
        source = Path(self.temp.name)/'origem'
        source.mkdir()
        (source/'app.py').write_text('print(42)')
        (source/'.env').write_text('LOCAL=example')
        (source/'.env.example').write_text('LOCAL=')
        (source/'.git').mkdir()
        (source/'.git/config').write_text('local git')
        target = pm.create_project(ROOT, self.projects, 'Importado', source=source)
        self.assertEqual((target/'app.py').read_text(), 'print(42)')
        self.assertTrue((source/'.env').exists())
        self.assertFalse((target/'.env').exists())
        self.assertFalse((target/'.git').exists())
        self.assertTrue((target/'.env.example').exists())

    def test_import_single_file(self):
        source = Path(self.temp.name)/'brief.txt'
        source.write_text('Brief do projeto')
        target = pm.create_project(ROOT, self.projects, 'Brief', source=source)
        self.assertEqual((target/'brief.txt').read_text(), 'Brief do projeto')

    def test_existing_destination_is_never_replaced(self):
        self.projects.mkdir(parents=True)
        target = self.projects/'Existente'
        target.mkdir()
        (target/'mine.txt').write_text('keep')
        with self.assertRaises(ValueError): pm.create_project(ROOT, self.projects, 'Existente')
        self.assertEqual((target/'mine.txt').read_text(), 'keep')

    def test_invalid_names_and_missing_source_leave_no_project(self):
        for name in ('../bad', 'a/b', 'a\\b', 'CON', '.', 'bad.'):
            with self.assertRaises(ValueError): pm.create_project(ROOT, self.projects, name)
        with self.assertRaises(ValueError): pm.create_project(ROOT, self.projects, 'Missing', source=Path(self.temp.name)/'absent')
        self.assertFalse((self.projects/'Missing').exists())

    def test_config_conflict_rolls_back_new_project(self):
        source = Path(self.temp.name)/'existing code'
        source.mkdir()
        (source/'AGENTS.md').write_text('user instructions')
        with self.assertRaises(ValueError): pm.create_project(ROOT, self.projects, 'Conflict', source=source)
        self.assertFalse((self.projects/'Conflict').exists())
        self.assertEqual((source/'AGENTS.md').read_text(), 'user instructions')
        self.assertFalse(list(self.projects.glob('.mc-import-*')))

    def test_empty_directories_are_preserved(self):
        source = Path(self.temp.name)/'source'
        (source/'assets/empty').mkdir(parents=True)
        target = pm.create_project(ROOT, self.projects, 'Empty', source=source)
        self.assertTrue((target/'assets/empty').is_dir())

    def test_unreadable_source_aborts_import(self):
        from unittest.mock import patch
        source = Path(self.temp.name)/'source'
        source.mkdir()
        real_scan = pm.os.scandir
        def fail_source(path):
            if Path(path) == source: raise PermissionError('unreadable source')
            return real_scan(path)
        with patch.object(pm.os, 'scandir', side_effect=fail_source):
            with self.assertRaises(PermissionError): pm.create_project(ROOT, self.projects, 'Unreadable', source=source)
        self.assertFalse((self.projects/'Unreadable').exists())


if __name__ == '__main__': unittest.main()
