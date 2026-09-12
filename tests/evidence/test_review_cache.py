import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.evidence import ReviewCache, make_review_fingerprint, normalize_relevant_scope


class ReviewCacheTests(unittest.TestCase):
    def fp(self, *args, **kwargs):
        kwargs.setdefault("policy_revision", "policy-1")
        kwargs.setdefault("tests_revision", "tests-1")
        return make_review_fingerprint(*args, **kwargs)

    def test_fingerprint_is_deterministic_and_scope_normalized(self):
        with patch("lib.evidence.review_cache._paths_are_case_sensitive", return_value=False):
            first = self.fp("base", "head", 3, "security", ["src/Auth/", "./README.md", "src/auth"])
            second = self.fp("base", "head", "3", "security", ["readme.md", "src/auth/"])
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.relevant_scope, ("readme.md", "src/auth"))
        self.assertEqual(len(first.value), 64)

    def test_case_sensitive_scope_keeps_distinct_posix_paths(self):
        with patch("lib.evidence.review_cache._paths_are_case_sensitive", return_value=True):
            upper = self.fp("base", "head", 3, "security", ["src/Foo.py"])
            lower = self.fp("base", "head", 3, "security", ["src/foo.py"])
        self.assertNotEqual(upper.value, lower.value)
        self.assertEqual(upper.relevant_scope, ("src/Foo.py",))
        self.assertEqual(lower.relevant_scope, ("src/foo.py",))

    def test_case_insensitive_scope_folds_case_without_relaxing_path_safety(self):
        with patch("lib.evidence.review_cache._paths_are_case_sensitive", return_value=False):
            upper = self.fp("base", "head", 3, "security", ["src/Foo.py"])
            lower = self.fp("base", "head", 3, "security", ["src/foo.py"])
            with self.assertRaises(ValueError):
                self.fp("base", "head", 3, "security", ["src/../Foo.py"])
        self.assertEqual(upper.value, lower.value)
        self.assertEqual(upper.relevant_scope, ("src/foo.py",))

    def test_unsafe_scope_is_rejected_instead_of_normalized_away(self):
        for value in ("../src", "src//auth", "C:/src", "/src", "src\\auth", "src/*"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.fp("base", "head", 1, "quality", [value])

    def test_exact_fingerprint_reuses_evidence_and_producer_is_not_called(self):
        cache = ReviewCache()
        fp = cache.fingerprint("base", "head", "r1", "quality", ["src"], policy_revision="p1", tests_revision="t1")
        calls = []
        cache.put(fp, {"result": "passed"})
        result = cache.get_or_reuse(fp, lambda: calls.append(True))
        self.assertEqual(result.evidence, {"result": "passed"})
        self.assertEqual(calls, [])

    def test_head_base_contract_and_reviewer_changes_invalidate_by_key(self):
        cache = ReviewCache()
        common = dict(base_sha="base", head_sha="head", contract_revision="r1", reviewer_class="quality", relevant_scope=["src"], policy_revision="p1", tests_revision="t1")
        original = cache.fingerprint(**common)
        cache.put(original, {"result": "passed"})
        for changed in (
            dict(head_sha="new-head"), dict(base_sha="new-base"),
            dict(contract_revision="r2"), dict(reviewer_class="architecture"),
            dict(relevant_scope=["tests"]), dict(policy_revision="p2"),
            dict(tests_revision="t2"),
        ):
            values = dict(common)
            values.update(changed)
            self.assertIsNone(cache.get(cache.fingerprint(**values)))

    def test_cache_persists_atomically_and_drops_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".galaxy" / "cache" / "reviews.json"
            cache = ReviewCache(path)
            fp = cache.fingerprint("base", "head", "r1", "quality", ["src"], policy_revision="p1", tests_revision="t1")
            cache.put(fp, {"result": "passed", "access_token": "secret", "summary": "ok"}, metadata={"api_key": "secret"})
            loaded = ReviewCache(path)
            entry = loaded.get(fp)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.evidence, {"result": "passed", "summary": "ok"})
            self.assertEqual(entry.metadata, {})
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertEqual(json.loads(raw)["version"], 2)

    def test_get_or_reuse_records_producer_result_on_miss(self):
        cache = ReviewCache()
        fp = cache.fingerprint("base", "head", "r1", "quality", policy_revision="p1", tests_revision="t1")
        result = cache.get_or_reuse(fp, lambda: {"result": "passed"})
        self.assertEqual(result.evidence, {"result": "passed"})
        self.assertTrue(cache.has(fp))


if __name__ == "__main__":
    unittest.main()
