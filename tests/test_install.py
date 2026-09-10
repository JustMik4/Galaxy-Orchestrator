import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
if importlib.util.find_spec('installer'):
    import installer
else: installer = None


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(installer, 'installer not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='mc espaços ')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'projeto ação'
        self.project.mkdir()

    def install(self, **kw):
        return installer.install(ROOT, self.project, **kw)

    def test_preview_has_no_side_effect_and_solo_has_no_github(self):
        self.install(preview=True)
        self.assertEqual(list(self.project.iterdir()), [])
        self.install()
        self.assertFalse((self.project/'.github').exists())
        self.assertEqual(installer.validate_project(self.project)['mode'], 'SOLO')

    def test_coop_installs_public_team_and_no_local_identity(self):
        self.install(mode='CO-OP')
        self.assertTrue((self.project/'.github/ISSUE_TEMPLATE/agent-task.yml').is_file())
        self.assertFalse((self.project/'local').exists())
        self.assertFalse((self.project/'.codex/auth.json').exists())

    def test_reinstall_is_identical_and_drift_aborts_without_partial_changes(self):
        self.install()
        paths = [p for p in self.project.rglob('*') if p.is_file()]
        before = {p.relative_to(self.project):p.read_bytes() for p in paths}
        self.install()
        self.assertEqual(before, {p.relative_to(self.project):p.read_bytes() for p in paths})
        (self.project/'AGENTS.md').write_text('my rules', encoding='utf-8')
        with self.assertRaises(ValueError): self.install(preset='critical')
        self.assertEqual((self.project/'.codex/config.toml').read_bytes(), before[Path('.codex/config.toml')])

    def test_existing_config_refused_and_existing_gitignore_preserved(self):
        (self.project/'.gitignore').write_text('my-secret\n', encoding='utf-8')
        (self.project/'AGENTS.md').write_text('user work', encoding='utf-8')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ['.gitignore','AGENTS.md'])
        (self.project/'AGENTS.md').unlink()
        self.install()
        self.assertIn('my-secret\n', (self.project/'.gitignore').read_text())

    def test_product_gate_is_red_until_real_command_configured(self):
        self.install()
        with self.assertRaises(ValueError): installer.validate_project(self.project, run_checks=True)
        p = self.project/'.multicontroller/checks.json'
        p.write_text(json.dumps({'commands': [[sys.executable, '-c', 'raise SystemExit(0)']]}))
        self.assertEqual(installer.validate_project(self.project, run_checks=True)['product_checks'], 'passed')
        p.write_text(json.dumps({'commands': [[sys.executable, '-c', 'raise SystemExit(7)']]}))
        with self.assertRaises(ValueError): installer.validate_project(self.project, run_checks=True)

    def test_symlink_destination_refused(self):
        outside = Path(self.temp.name)/'outside'
        outside.mkdir()
        try: (self.project/'.codex').symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt': self.skipTest('symlink creation unavailable')
            env = dict(os.environ, MC_TEST_LINK=str(self.project/'.codex'), MC_TEST_TARGET=str(outside))
            r = subprocess.run(['pwsh','-NoProfile','-Command',
                                'New-Item -ItemType Junction -Path $env:MC_TEST_LINK -Target $env:MC_TEST_TARGET | Out-Null'],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_nested_master_and_project_refused(self):
        with self.assertRaises(ValueError): installer.install(ROOT, ROOT/'nested')

    def test_coop_rejects_case_variant_github_accounts(self):
        self.install(mode='CO-OP')
        team_file = self.project/'AGENT_TEAM.yml'
        team = json.loads(team_file.read_text())
        team.update(integration_lead='one', operators=[{'id':'one','github_login':'Alice'}, {'id':'two','github_login':'alice'}])
        team_file.write_text(json.dumps(team))
        (self.project/'.multicontroller/checks.json').write_text(json.dumps({'commands':[[sys.executable,'-c','pass']]}))
        with self.assertRaises(ValueError): installer.validate_project(self.project, run_checks=True)

    def test_concurrent_change_before_lock_is_not_overwritten(self):
        from unittest.mock import patch
        real_open = installer.os.open
        def interleaved(path, flags, *args, **kw):
            (self.project/'AGENTS.md').write_text('new concurrent user rules')
            return real_open(path, flags, *args, **kw)
        with patch.object(installer.os, 'open', side_effect=interleaved):
            with self.assertRaises(ValueError): self.install()
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'new concurrent user rules')
        self.assertFalse((self.project/'.codex').exists())

    def test_powershell_preview_and_install(self):
        script = ROOT/'scripts/install.ps1'
        args = ['pwsh','-NoProfile','-File',str(script),'-ProjectPath',str(self.project),'-Mode','SOLO']
        r = subprocess.run(args+['-WhatIf'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.project.iterdir()), [])
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.project/'.codex/config.toml').exists())

    def test_upgrade_rolls_back_after_io_failure(self):
        from unittest.mock import patch
        self.install()
        before = {p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        real_replace = installer.os.replace
        calls = 0
        def fail_second(source, dest):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('simulated disk error')
            return real_replace(source, dest)
        with patch.object(installer.os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError): self.install(preset='critical')
        self.assertEqual(before, {p.relative_to(self.project):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_upgrade_preserves_project_team_and_commands(self):
        self.install()
        team_file = self.project/'AGENT_TEAM.yml'
        team = json.loads(team_file.read_text())
        team['integration_branch'] = 'develop'
        team_file.write_text(json.dumps(team))
        self.install(preset='critical')
        self.assertEqual(installer.validate_project(self.project)['preset'], 'critical')
        self.assertEqual(json.loads(team_file.read_text())['integration_branch'], 'develop')


if __name__ == '__main__': unittest.main()
