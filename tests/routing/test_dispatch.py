import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_task_contract_can_override_project_profile_with_allowed_value(self):
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
        overridden = coordinator.authorize(
            {"required_capability": 2, "profile": "economy"},
            self.capabilities, self.quota,
        )
        self.assertEqual(overridden["route"], {"model": "luna", "effort": "medium"})
        self.assertEqual(overridden["profile"], "economy")
        with self.assertRaisesRegex(ValueError, "task routing profile"):
            coordinator.authorize(
                {"required_capability": 2, "profile": "untrusted"},
                self.capabilities, self.quota,
            )

    def test_authenticated_task_profile_override_is_applied_only_to_bound_task(self):
        declaration = self.project / ".galaxy/project.yml"
        project = json.loads(declaration.read_text())
        project["routing"] = {
            "profile": "economy",
            "task_profiles": {"security-review": "quality"},
        }
        declaration.write_text(json.dumps(project), encoding="utf-8")
        update_declaration_hashes(self.project)

        coordinator = DispatchCoordinator(self.project)
        ordinary = coordinator.authorize(
            {"task_id": "ordinary", "task_class": "implementation"},
            self.capabilities, self.quota,
        )
        planned = coordinator.authorize(
            {"task_id": "security-review", "task_class": "implementation"},
            self.capabilities, self.quota,
        )
        self.assertEqual(ordinary["route"], {"model": "luna", "effort": "medium"})
        self.assertEqual(planned["route"], {"model": "terra", "effort": "medium"})
        self.assertEqual(ordinary["profile"], "economy")
        self.assertEqual(planned["profile"], "quality")
        self.assertEqual(planned["task_id"], "security-review")
        explicit = coordinator.authorize(
            {"task_id": "ordinary", "profile": "quality"},
            self.capabilities, self.quota,
        )
        self.assertEqual(explicit["profile"], "quality")
        self.assertEqual(explicit["route"], {"model": "terra", "effort": "medium"})

    def test_invalid_authenticated_task_profile_is_rejected(self):
        declaration = self.project / ".galaxy/project.yml"
        project = json.loads(declaration.read_text())
        project["routing"] = {
            "profile": "balanced",
            "task_profiles": {"security-review": "untrusted"},
        }
        declaration.write_text(json.dumps(project), encoding="utf-8")
        update_declaration_hashes(self.project)
        with self.assertRaisesRegex(ValueError, "task profile"):
            DispatchCoordinator(self.project).authorize(
                {"task_id": "security-review"}, self.capabilities, self.quota,
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
            "task_id": "task-emergency-1",
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
        self.assertEqual(stored.metadata["task_id"], "task-emergency-1")

    def test_emergency_limit_uses_persisted_task_identity_not_request_counter(self):
        request = {
            "task_id": "task-emergency-1",
            "task_class": "implementation", "required_capability": 3,
            "emergency": True,
            "previous_route": {"model": "luna", "effort": "medium"},
            "failure_class": "reasoning",
            "failure_evidence": "same obligation failed twice",
            "emergency_reason": "bounded intermediate escalation",
        }
        first = DispatchCoordinator(self.project).authorize(
            request, self.capabilities, self.quota,
        )
        second = DispatchCoordinator(self.project).authorize(
            request, self.capabilities, self.quota,
        )
        self.assertTrue(first["authorized"])
        self.assertFalse(second["authorized"])
        self.assertEqual(second["reason"], "emergency dispatch limit reached")
        state = json.loads(
            (self.project / ".galaxy/runtime/emergency-dispatches.json").read_text()
        )
        self.assertEqual(sum(item["dispatches"] for item in state["tasks"].values()), 1)

        other = DispatchCoordinator(self.project).authorize(
            {**request, "task_id": "task-emergency-2"},
            self.capabilities, self.quota,
        )
        self.assertTrue(other["authorized"])

    def test_malformed_emergency_state_fails_closed_without_being_replaced(self):
        state_path = self.project / ".galaxy/runtime/emergency-dispatches.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text('{"version":1,"tasks":"forged"}', encoding="utf-8")
        result = DispatchCoordinator(self.project).authorize({
            "task_id": "task-emergency-1", "emergency": True,
            "failure_evidence": "evidence", "emergency_reason": "reason",
        }, self.capabilities, self.quota)
        self.assertFalse(result["authorized"])
        self.assertEqual(result["reason"], "emergency dispatch limit reached")
        self.assertEqual(
            state_path.read_text(encoding="utf-8"),
            '{"version":1,"tasks":"forged"}',
        )

    def test_emergency_limit_claim_is_atomic_for_concurrent_coordinators(self):
        request = {
            "task_id": "concurrent-task", "task_class": "implementation",
            "required_capability": 3, "emergency": True,
            "previous_route": {"model": "luna", "effort": "medium"},
            "failure_class": "reasoning", "failure_evidence": "evidence",
            "emergency_reason": "reason",
        }

        def authorize():
            return DispatchCoordinator(self.project).authorize(
                request, self.capabilities, self.quota,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: authorize(), range(2)))
        self.assertEqual(sum(result["authorized"] for result in results), 1)

    def test_emergency_requires_task_identity_and_rejects_request_counter(self):
        common = {
            "emergency": True, "failure_evidence": "evidence",
            "emergency_reason": "reason",
        }
        coordinator = DispatchCoordinator(self.project)
        with self.assertRaisesRegex(ValueError, "task_id"):
            coordinator.authorize(common, self.capabilities, self.quota)
        with self.assertRaisesRegex(ValueError, "unsupported routing request fields"):
            coordinator.authorize(
                {**common, "task_id": "task-1", "emergency_dispatches": 0},
                self.capabilities, self.quota,
            )

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
