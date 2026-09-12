import json
import tempfile
import unittest
from pathlib import Path

from lib.evidence import ReviewCache, make_review_fingerprint, normalize_relevant_scope


class ReviewCacheTests(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_scope_normalized(self):
        first = make_review_fingerprint("base", "head", 3, "security", ["src/Auth/", "./README.md", "src/auth"])
        second = make_review_fingerprint("base", "head", "3", "security", ["readme.md", "src/auth/"])
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.relevant_scope, ("readme.md", "src/auth"))
        self.assertEqual(len(first.value), 64)

    def test_unsafe_scope_is_rejected_instead_of_normalized_away(self):
        for value in ("../src", "src//auth", "C:/src", "/src", "src\\auth", "src/*"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    make_review_fingerprint("base", "head", 1, "quality", [value])

    def test_exact_fingerprint_reuses_evidence_and_producer_is_not_called(self):
        cache = ReviewCache()
        fp = cache.fingerprint("base", "head", "r1", "quality", ["src"])
        calls = []
        cache.put(fp, {"result": "passed"})
        result = cache.get_or_reuse(fp, lambda: calls.append(True))
        self.assertEqual(result.evidence, {"result": "passed"})
        self.assertEqual(calls, [])

    def test_head_base_contract_and_reviewer_changes_invalidate_by_key(self):
        cache = ReviewCache()
        common = dict(base_sha="base", head_sha="head", contract_revision="r1", reviewer_class="quality", relevant_scope=["src"])
        original = cache.fingerprint(**common)
        cache.put(original, {"result": "passed"})
        for changed in (
            dict(head_sha="new-head"), dict(base_sha="new-base"),
            dict(contract_revision="r2"), dict(reviewer_class="architecture"),
            dict(relevant_scope=["tests"]),
        ):
            values = dict(common)
            values.update(changed)
            self.assertIsNone(cache.get(cache.fingerprint(**values)))

    def test_cache_persists_atomically_and_drops_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".galaxy" / "cache" / "reviews.json"
            cache = ReviewCache(path)
            fp = cache.fingerprint("base", "head", "r1", "quality", ["src"])
            cache.put(fp, {"result": "passed", "access_token": "secret", "summary": "ok"}, metadata={"api_key": "secret"})
            loaded = ReviewCache(path)
            entry = loaded.get(fp)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.evidence, {"result": "passed", "summary": "ok"})
            self.assertEqual(entry.metadata, {})
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertEqual(json.loads(raw)["version"], 1)

    def test_get_or_reuse_records_producer_result_on_miss(self):
        cache = ReviewCache()
        fp = cache.fingerprint("base", "head", "r1", "quality")
        result = cache.get_or_reuse(fp, lambda: {"result": "passed"})
        self.assertEqual(result.evidence, {"result": "passed"})
        self.assertTrue(cache.has(fp))


if __name__ == "__main__":
    unittest.main()
