import json
import tempfile
import unittest
from pathlib import Path

from lib.runtime import RuntimeVerifier, VerificationStatus


class RuntimeVerifierTests(unittest.TestCase):
    def test_requested_spawned_and_exact_effective_route_is_verified(self):
        verifier = RuntimeVerifier(version="2.0.0")
        record = verifier.request("gpt-5.6-sol", "medium", parent_thread="root")
        self.assertEqual(record.status, VerificationStatus.REQUESTED)
        verifier.spawned(record, "child-123")
        result = verifier.verify(record, {
            "effective_model": "gpt-5.6-sol",
            "effective_effort": "medium",
        })
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertEqual(result.spawn_id, "child-123")
        self.assertTrue(result.trusted)
        self.assertEqual(result.multicontroller_version, "2.0.0")

    def test_mismatch_is_host_routing_and_never_reputation_input(self):
        verifier = RuntimeVerifier()
        record = verifier.request("gpt-6-astra", "high")
        result = verifier.verify(record, {"model": "gpt-5.6-sol", "effort": "high"})
        self.assertEqual(result.status, VerificationStatus.MISMATCH)
        self.assertEqual(result.failure_class, "host-routing")
        self.assertFalse(result.reputation_eligible)
        self.assertEqual(verifier.reputation_input(), [])

    def test_unknown_telemetry_is_unverified(self):
        verifier = RuntimeVerifier()
        result = verifier.verify(verifier.request("gpt-5.6-luna", "low"), {})
        self.assertEqual(result.status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(result.effective_model)
        self.assertFalse(result.trusted)

    def test_nested_telemetry_and_persistence_are_supported_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            verifier = RuntimeVerifier(path, version="2.0.0")
            record = verifier.request("gpt-5.6-luna", "low", metadata={
                "task": "safe-id", "api_token": "must-not-persist", "nested": {"password": "x"}
            })
            verifier.verify(record, {"runtime": {"model": "gpt-5.6-luna", "reasoning_effort": "low"}})
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("must-not-persist", raw)
            self.assertIn('"galaxy_version": "2.0.0"', raw)
            self.assertNotIn('"multicontroller_version"', raw)
            loaded = RuntimeVerifier(path)
            self.assertEqual(loaded.get(record.dispatch_id).status, VerificationStatus.VERIFIED)
            self.assertEqual(loaded.get(record.dispatch_id).metadata, {"task": "safe-id", "nested": {}})
            json.loads(raw)

    def test_terminal_records_cannot_be_verified_twice(self):
        verifier = RuntimeVerifier()
        record = verifier.request("gpt-5.6-sol", "high")
        verifier.verify(record, effective_model="gpt-5.6-sol", effective_effort="high")
        with self.assertRaises(ValueError):
            verifier.verify(record, effective_model="gpt-5.6-luna", effective_effort="low")


if __name__ == "__main__":
    unittest.main()
