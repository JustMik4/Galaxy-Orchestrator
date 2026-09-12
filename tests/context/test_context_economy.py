import tempfile
import unittest
from pathlib import Path

from lib.context import (
    ContextBuilder,
    ContextReferenceRegistry,
    EvidenceStore,
    HandoffCompactor,
    ToolOutputCompressor,
    parse_context_economy,
    BenchmarkSample,
    compare,
)


class ContextEconomyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".galaxy").mkdir()
        self.store = EvidenceStore(self.root)

    def test_off_is_true_baseline(self):
        policy = parse_context_economy()
        self.assertEqual(policy.mode.value, "off")
        output = "\n".join(f"line {i}" for i in range(200))
        result = ToolOutputCompressor(policy, self.store).compress(
            "task-1", output, exit_code=0,
        )
        self.assertFalse(result.compacted)
        self.assertEqual(result.inline, output)
        self.assertFalse((self.root / ".galaxy/evidence").exists())

    def test_balanced_handoff_is_structured_and_overflow_is_referenced(self):
        policy = parse_context_economy({
            "mode": "balanced", "handoff": {"max_summary_tokens": 50},
        })
        report = {
            "status": "complete", "changed": ["a.py"] * 100,
            "tests": ["all passed"] * 100, "decisions": ["kept API"] * 100,
            "blockers": "none", "risks": "low", "evidence": "tests.json", "head": "abc123",
        }
        result = HandoffCompactor(policy, self.store).compact("task-2", report)
        self.assertTrue(result.compacted)
        self.assertIn("STATUS: complete", result.text)
        self.assertIn("BLOCKERS: none", result.text)
        self.assertIn("HEAD: abc123", result.text)
        self.assertIsNotNone(result.overflow)
        stored = self.store.read(result.overflow)
        self.assertIn("CHANGED:", stored)

    def test_aggressive_has_smaller_budgets_than_balanced(self):
        balanced = parse_context_economy({"mode": "balanced"})
        aggressive = parse_context_economy({"mode": "aggressive"})
        self.assertLess(aggressive.handoff_max_summary_tokens, balanced.handoff_max_summary_tokens)
        self.assertLess(aggressive.logs_inline_max_lines, balanced.logs_inline_max_lines)

    def test_failure_output_preserves_identity_location_and_full_sanitized_evidence(self):
        policy = parse_context_economy({"mode": "aggressive", "logs": {"inline_max_lines": 10}})
        lines = [f"progress {i}" for i in range(80)]
        lines[42] = "FAILED tests/test_api.py:17 AssertionError password=hunter2"
        result = ToolOutputCompressor(policy, self.store).compress(
            "task-3", "\n".join(lines), exit_code=1, command="pytest",
        )
        self.assertIn("tests/test_api.py:17", result.inline)
        self.assertIn("AssertionError", result.inline)
        self.assertNotIn("hunter2", result.inline)
        stored = self.store.read(result.reference)
        self.assertNotIn("hunter2", stored)
        self.assertIn("password=[REDACTED]", stored)

    def test_small_failure_remains_inline_and_still_has_full_evidence(self):
        policy = parse_context_economy({"mode": "balanced"})
        result = ToolOutputCompressor(policy, self.store).compress(
            "task-small", "FAILED tests/test_small.py:9", exit_code=1,
        )
        self.assertFalse(result.compacted)
        self.assertIn("test_small.py:9", result.inline)
        self.assertEqual(self.store.read(result.reference), result.inline)

    def test_evidence_is_deduplicated_and_references_are_lazy(self):
        first = self.store.persist("task-4", "same", label="log")
        second = self.store.persist("task-4", "same", label="log")
        self.assertEqual(first, second)
        registry = ContextReferenceRegistry()
        builder = ContextBuilder(parse_context_economy({"mode": "balanced"}), registry)
        initial = builder.build([("stable policy", first.path)])
        repeated = builder.build([("stable policy", first.path)])
        self.assertEqual(initial.inline, ("stable policy",))
        self.assertEqual(repeated.inline, ())
        self.assertEqual(repeated.references, (first.path,))

    def test_progressive_disclosure_selects_only_hot_specialist_metadata(self):
        catalog = (
            {"name": "git", "description": "Git"},
            {"name": "python", "description": "Python"},
        )
        result = ContextBuilder(parse_context_economy({"mode": "balanced"})).build(
            [], specialist_catalog=catalog, selected_specialists=["git"],
        )
        self.assertEqual([item["name"] for item in result.specialist_index], ["git"])

    def test_invalid_or_unsafe_configuration_fails_closed(self):
        invalid = (
            {"mode": "magic"},
            {"mode": "balanced", "tool_output": {"preserve_failures": False}},
            {"mode": "off", "logs": {"inline_max_lines": 10}},
            {"mode": "balanced", "unknown": True},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_context_economy(value)

    def test_evidence_rejects_traversal_and_redacts_bearer_tokens(self):
        with self.assertRaises(ValueError):
            self.store.persist("../escape", "data")
        reference = self.store.persist("secure", "Authorization: Bearer abc.def")
        self.assertNotIn("abc.def", self.store.read(reference))

    def test_ab_benchmark_requires_matched_accepted_baseline(self):
        samples = [
            BenchmarkSample("build", "off", 100, 20, True),
            BenchmarkSample("build", "balanced", 70, 20, True),
            BenchmarkSample("unmatched", "aggressive", 10, 10, True),
            BenchmarkSample("rejected", "off", 50, 10, False),
        ]
        result = compare(samples)
        self.assertEqual(result["matched_pairs"], 1)
        self.assertEqual(result["mean_delta_tokens"], -30)
        self.assertTrue(result["comparable"])


if __name__ == "__main__":
    unittest.main()
