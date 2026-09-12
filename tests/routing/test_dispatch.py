import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.dispatch import DispatchCoordinator
from tests.bootstrap.test_project import update_declaration_hashes, write_project


class DispatchCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        write_project(self.project)
        self.capabilities = {
            "configured": [
                {"model": "luna", "effort": "medium", "capability": 2, "expected_cost": 1},
                {"model": "terra", "effort": "medium", "capability": 3, "expected_cost": 2},
                {"model": "astra", "effort": "high", "capability": 6,
                 "expected_cost": 10, "frontier": True},
            ],
            "observed": [
                {"model": "luna", "effort": "medium"},
                {"model": "terra", "effort": "medium"},
                {"model": "astra", "effort": "high"},
            ],
        }
        self.quota = {"five_hour_remaining": 90, "weekly_remaining": 80}

    def test_authorizes_lowest_observed_qualifying_route_and_persists_request(self):
        coordinator = DispatchCoordinator(self.project)
        result = coordinator.authorize(
            {"task_class": "implementation", "required_capability": 3},
            self.capabilities,
            self.quota,
        )
        self.assertTrue(result["authorized"])
        self.assertEqual(result["route"], {"model": "terra", "effort": "medium"})
        dispatch_id = result["dispatch"]["dispatch_id"]
        loaded = DispatchCoordinator(self.project)
        self.assertEqual(loaded.verifier.get(dispatch_id).requested_model, "terra")

    def test_unknown_observed_support_fails_closed_but_bounded_unknown_quota_uses_policy(self):
        coordinator = DispatchCoordinator(self.project)
        no_observation = coordinator.authorize(
            {"required_capability": 2},
            {**self.capabilities, "observed": []},
            self.quota,
        )
        self.assertFalse(no_observation["authorized"])
        self.assertEqual(no_observation["failure_class"], "capability-missing")
        unknown_quota = coordinator.authorize(
            {"required_capability": 2}, self.capabilities,
            {"five_hour_remaining": None, "weekly_remaining": 80},
        )
        self.assertTrue(unknown_quota["authorized"])
        expensive = coordinator.authorize(
            {"required_capability": 6, "frontier_reason": "reviewed need"}, self.capabilities,
            {"five_hour_remaining": None, "weekly_remaining": 80},
        )
        self.assertFalse(expensive["authorized"])
        self.assertEqual(expensive["failure_class"], "quota")

    def test_unknown_quota_honors_local_operator_block_all_policy(self):
        operator = self.project / ".galaxy/local/quota.toml"
        operator.parent.mkdir(parents=True)
        operator.write_text('[quota_guard]\nunknown_telemetry = "block_all"\n', encoding="utf-8")
        coordinator = DispatchCoordinator(self.project, operator_config=operator)
        blocked = coordinator.authorize(
            {"required_capability": 2}, self.capabilities,
            {"five_hour_remaining": None, "weekly_remaining": 80},
        )
        self.assertFalse(blocked["authorized"])
        self.assertEqual(blocked["quota_state"], "unknown")

        operator.write_text('[quota_guard]\nunknown_telemetry = "allow_bounded"\n', encoding="utf-8")
        allowed = DispatchCoordinator(self.project, operator_config=operator).authorize(
            {"required_capability": 6, "frontier_reason": "reviewed need"},
            self.capabilities,
            {"five_hour_remaining": None, "weekly_remaining": 80},
        )
        self.assertTrue(allowed["authorized"])
        self.assertEqual(allowed["route"]["model"], "astra")

    def test_project_profile_cannot_be_overridden_by_dispatch_request(self):
        declaration = self.project / ".galaxy/project.yml"
        project = json.loads(declaration.read_text())
        project["routing"] = {"profile": "quality"}
        declaration.write_text(json.dumps(project), encoding="utf-8")
        update_declaration_hashes(self.project)
        coordinator = DispatchCoordinator(self.project)
        selected = coordinator.authorize(
            {"task_class": "implementation"}, self.capabilities, self.quota,
        )
        self.assertEqual(selected["route"], {"model": "terra", "effort": "medium"})
        with self.assertRaisesRegex(ValueError, "unsupported routing request fields"):
            coordinator.authorize(
                {"required_capability": 2, "profile": "economy"},
                self.capabilities, self.quota,
            )

    def test_quota_floor_blocks_dispatch_before_request_is_recorded(self):
        coordinator = DispatchCoordinator(self.project)
        result = coordinator.authorize(
            {"required_capability": 2}, self.capabilities,
            {"five_hour_remaining": 15, "weekly_remaining": 80},
        )
        self.assertFalse(result["authorized"])
        self.assertEqual(result["action"], "blocked-quota")
        self.assertEqual(coordinator.verifier.records, {})

    def test_verify_records_effective_route_and_classifies_mismatch(self):
        coordinator = DispatchCoordinator(self.project)
        authorized = coordinator.authorize(
            {"required_capability": 2}, self.capabilities, self.quota,
        )
        dispatch_id = authorized["dispatch"]["dispatch_id"]
        verified = coordinator.verify(
            dispatch_id, spawn_id="agent-7",
            effective_model="terra", effective_effort="medium",
        )
        self.assertEqual(verified["status"], "MISMATCH")
        self.assertEqual(verified["failure_class"], "host-routing")
        persisted = json.loads(
            (self.project / ".galaxy/runtime/dispatch-verification.json").read_text()
        )
        self.assertEqual(persisted["records"][0]["effective_model"], "terra")

    def test_emergency_authorization_persists_complete_evidence(self):
        coordinator = DispatchCoordinator(self.project)
        result = coordinator.authorize({
            "task_class": "implementation", "required_capability": 3,
            "emergency": True,
            "previous_route": {"model": "luna", "effort": "medium"},
            "failure_class": "reasoning",
            "failure_evidence": "same obligation failed twice",
            "emergency_reason": "bounded intermediate escalation",
        }, self.capabilities, self.quota)
        self.assertTrue(result["authorized"])
        evidence = result["emergency_record"]
        self.assertEqual(evidence["reason"], "bounded intermediate escalation")
        self.assertEqual(evidence["failure_evidence"], "same obligation failed twice")
        self.assertEqual(evidence["previous_route"], {"model": "luna", "effort": "medium"})
        self.assertEqual(evidence["selected_route"], result["route"])
        self.assertIn("normal_next_route_skipped", evidence)
        stored = DispatchCoordinator(self.project).verifier.get(
            result["dispatch"]["dispatch_id"]
        )
        self.assertEqual(stored.metadata["emergency_record"], evidence)

    def test_review_cache_reuses_only_exact_unchanged_fingerprint(self):
        coordinator = DispatchCoordinator(self.project)
        fingerprint = {
            "base_sha": "base", "head_sha": "head", "contract_revision": "contract-1",
            "reviewer_class": "quality", "relevant_scope": ["src"],
            "policy_revision": "policy-1", "tests_revision": "tests-1",
        }
        self.assertFalse(coordinator.review_lookup(fingerprint)["reusable"])
        coordinator.review_record(fingerprint, {"result": "approved"})
        self.assertTrue(coordinator.review_lookup(fingerprint)["reusable"])
        changed = {**fingerprint, "tests_revision": "tests-2"}
        self.assertFalse(coordinator.review_lookup(changed)["reusable"])

    @unittest.skipUnless(os.name == "nt", "junction regression is Windows-specific")
    def test_runtime_state_junction_cannot_redirect_dispatch_evidence(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        runtime = self.project / ".galaxy/runtime"
        created = subprocess.run([
            "cmd", "/c", "mklink", "/J", str(runtime), str(outside),
        ], text=True, capture_output=True, check=False)
        if created.returncode:
            self.skipTest("directory junction creation unavailable")
        self.addCleanup(lambda: runtime.rmdir() if runtime.exists() else None)

        with self.assertRaisesRegex(ValueError, "symlink/reparse"):
            DispatchCoordinator(self.project)

        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
