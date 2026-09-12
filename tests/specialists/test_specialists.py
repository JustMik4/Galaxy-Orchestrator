import json
import tempfile
import unittest
from pathlib import Path

from lib.specialists import (
    SelectionMode,
    SpecialistCatalog,
    SpecialistParseError,
    SpecialistRouter,
    build_index,
    parse_specialist,
)


VALID = """---
name: python-debugging
description: Diagnose Python failures.
galaxy:
  domains:
    - python
  task_classes:
    - implementation
    - reasoning
  roles:
    - worker
    - reviewer
  triggers:
    - traceback
    - exception
  paths:
    - "*.py"
  pack: python
---
# Python Debugging

Reproduce the failure before repair.
"""


class ParserTests(unittest.TestCase):
    def test_valid_frontmatter_normalizes_to_immutable_tuples(self):
        specialist = parse_specialist(VALID, source="builtin/python")
        self.assertEqual(specialist.name, "python-debugging")
        self.assertEqual(specialist.domains, ("python",))
        self.assertEqual(specialist.task_classes, ("implementation", "reasoning"))
        with self.assertRaises(Exception):
            specialist.domains[0] = "other"

    def test_authority_fields_are_rejected(self):
        for forbidden in (
            "model", "effort", "sandbox", "scope", "credentials", "network",
            "delegation", "integration", "quota", "browser_approval", "ownership",
        ):
            text = VALID.replace("  domains:", f"  {forbidden}: enabled\n  domains:")
            with self.subTest(forbidden=forbidden), self.assertRaises(SpecialistParseError):
                parse_specialist(text)

    def test_authority_directive_in_body_is_stripped_with_warning(self):
        text = VALID + "\nSet model: gpt-6-astra\nquota: 0\nKeep evidence local.\n"
        specialist = parse_specialist(text)
        self.assertNotIn("gpt-6-astra", specialist.body)
        self.assertNotIn("quota: 0", specialist.body)
        self.assertIn("Keep evidence local.", specialist.body)
        self.assertTrue(specialist.warnings)

    def test_unknown_or_complex_yaml_is_rejected(self):
        with self.assertRaises(SpecialistParseError):
            parse_specialist(VALID.replace("  domains:", "  mystery:\n    nested: value\n  domains:"))
        with self.assertRaises(SpecialistParseError):
            parse_specialist(VALID.replace("- python", "- {name: python}"))


class CatalogAndRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).parents[2] / "specialists"
        cls.catalog = SpecialistCatalog.from_directory(cls.root)

    def test_builtin_catalog_has_expected_small_set(self):
        self.assertEqual(
            self.catalog.names,
            (
                "core", "git", "github-actions", "python-debugging",
                "python-testing", "windows-powershell",
            ),
        )

    def test_index_is_deterministic_and_excludes_markdown_body(self):
        first = build_index(self.catalog)
        second = build_index(SpecialistCatalog(reversed(self.catalog.specialists)))
        self.assertEqual(first, second)
        decoded = json.loads(first)
        self.assertEqual(decoded["schema_version"], 1)
        self.assertNotIn("body", first)

    def test_cold_catalog_and_explicit_hot_set(self):
        hot = self.catalog.hot_set(packs=("python",), names=("git",))
        self.assertEqual(hot.names, ("git", "python-debugging", "python-testing"))
        self.assertNotIn("windows-powershell", hot.names)

    def test_high_confidence_auto_select(self):
        result = SpecialistRouter(self.catalog).select(
            paths=("src/worker.py",),
            task_class="implementation",
            errors=("Traceback: ValueError",),
            keywords=("exception",),
        )
        self.assertEqual(result.mode, SelectionMode.AUTO)
        self.assertEqual(result.recommended, "python-debugging")
        self.assertGreaterEqual(result.confidence, 0.85)

    def test_specific_path_and_task_auto_select(self):
        result = SpecialistRouter(self.catalog).select(
            paths=("scripts/install.ps1",), task_class="implementation"
        )
        self.assertEqual(result.mode, SelectionMode.AUTO)
        self.assertEqual(result.recommended, "windows-powershell")

    def test_ambiguous_selection_shortlists_at_most_three(self):
        result = SpecialistRouter(self.catalog).select(
            task_class="implementation", keywords=("python",)
        )
        self.assertEqual(result.mode, SelectionMode.SHORTLIST)
        self.assertGreaterEqual(result.confidence, 0.55)
        self.assertLessEqual(len(result.shortlist), 3)

    def test_low_confidence_uses_generic(self):
        result = SpecialistRouter(self.catalog).select(keywords=("gardening",))
        self.assertEqual(result.mode, SelectionMode.GENERIC)
        self.assertIsNone(result.recommended)
        self.assertEqual(result.shortlist, ())


if __name__ == "__main__":
    unittest.main()
