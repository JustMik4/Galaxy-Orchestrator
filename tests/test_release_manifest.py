import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "RELEASE-MANIFEST.json"


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_matches_every_tracked_release_blob(self):
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        self.assertEqual(manifest["algorithm"], "sha256")

        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
        ).stdout.decode("utf-8").rstrip("\0").split("\0")
        expected_paths = sorted(path for path in tracked if path != MANIFEST)
        self.assertEqual(sorted(manifest["files"]), expected_paths)

        for path in expected_paths:
            content = subprocess.run(
                ["git", "show", ":" + path], cwd=ROOT, check=True, capture_output=True
            ).stdout
            self.assertEqual(
                manifest["files"][path],
                hashlib.sha256(content).hexdigest(),
                path,
            )


if __name__ == "__main__":
    unittest.main()
