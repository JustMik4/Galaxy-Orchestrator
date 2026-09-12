import tempfile
import unittest
from pathlib import Path

from lib.adapters import CodexAdapter
from lib.specialists import SpecialistCatalog


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parents[2] / "specialists"
        self.catalog = SpecialistCatalog.from_directory(root)
        self.hot = self.catalog.hot_set(names=("python-testing", "git"))

    def test_render_is_deterministic_root_is_sol_medium_and_only_hot_is_present(self):
        adapter = CodexAdapter()
        first = adapter.render(self.hot)
        second = adapter.render(self.hot)
        self.assertEqual(first, second)
        self.assertIn('model = "gpt-5.6-sol"', first[".codex/config.toml"])
        self.assertIn('model_reasoning_effort = "medium"', first[".codex/config.toml"])
        self.assertIn("[agents.architect]", first[".codex/config.toml"])
        self.assertNotIn('model = ', first[".codex/agents/architect.toml"])
        self.assertNotIn('model_reasoning_effort = ', first[".codex/agents/architect.toml"])
        self.assertIn('explicit model and effort authorized by galaxy dispatch',
                      first[".codex/agents/architect.toml"])
        self.assertIn(".codex/skills/git/SKILL.md", first)
        self.assertIn(".codex/skills/python-testing/SKILL.md", first)
        self.assertNotIn(".codex/skills/python-debugging/SKILL.md", first)

    def test_materialize_matches_render_and_contains_no_secrets_or_local_paths(self):
        adapter = CodexAdapter()
        expected = adapter.render(self.hot)
        with tempfile.TemporaryDirectory() as temporary:
            written = adapter.materialize(Path(temporary), self.hot)
            self.assertEqual(tuple(sorted(expected)), written)
            for relative, content in expected.items():
                self.assertEqual((Path(temporary) / relative).read_text(encoding="utf-8"), content)
                lowered = content.lower()
                self.assertNotIn("api_key", lowered)
                self.assertNotIn("c:\\users\\", lowered)
                self.assertNotIn("/home/", lowered)

    def test_rematerialize_removes_only_stale_galaxy_skill(self):
        adapter = CodexAdapter()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter.materialize(root, self.catalog.hot_set(names=("git", "python-testing")))
            unrelated = root / ".codex" / "skills" / "user-owned" / "notes.md"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("keep", encoding="utf-8")
            adapter.materialize(root, self.catalog.hot_set(names=("git",)))
            self.assertFalse((root / ".codex/skills/python-testing/SKILL.md").exists())
            self.assertTrue((root / ".codex/skills/git/SKILL.md").exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
