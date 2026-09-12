import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliCompatibilityTests(unittest.TestCase):
    def test_canonical_cli_runs_legacy_policy_commands(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "galaxy.py"), "gate", str(ROOT / "docs/examples/gate.json")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DEPRECATED", result.stderr)

    def test_legacy_cli_prints_notice_and_delegates(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "multicontroller.py"), "gate", str(ROOT / "docs/examples/gate.json")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DEPRECATED", result.stderr)


if __name__ == "__main__":
    unittest.main()
