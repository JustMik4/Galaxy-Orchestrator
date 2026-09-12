import unittest
from pathlib import Path
import tempfile

from lib.quota import (
    QuotaGuard,
    QuotaOperation,
    QuotaPolicy,
    QuotaSnapshot,
    QuotaState,
    UnknownTelemetryPolicy,
)
from lib.operator_config import load_operator_policy


class QuotaGuardTests(unittest.TestCase):
    def test_states_follow_default_thresholds(self):
        guard = QuotaGuard()
        cases = [
            (QuotaSnapshot(31, 9), QuotaState.NORMAL),
            (QuotaSnapshot(30, 8), QuotaState.CONSERVATIVE),
            (QuotaSnapshot(20, 5), QuotaState.ECONOMY),
            (QuotaSnapshot(15, 50), QuotaState.HARD_STOP),
            (QuotaSnapshot(90, 2), QuotaState.HARD_STOP),
        ]
        for snapshot, state in cases:
            with self.subTest(snapshot=snapshot):
                self.assertEqual(guard.state(snapshot), state)

    def test_operator_can_customize_thresholds(self):
        guard = QuotaGuard(
            QuotaPolicy(five_hour_stop=10, weekly_stop=1)
        )
        self.assertTrue(guard.evaluate(QuotaSnapshot(12, 1.1)).allowed)
        self.assertFalse(guard.evaluate(QuotaSnapshot(10, 80)).allowed)

    def test_hard_stop_blocks_all_new_work_including_emergency(self):
        guard = QuotaGuard()
        snapshot = QuotaSnapshot(10, 50)
        for operation in (
            QuotaOperation.DISPATCH,
            QuotaOperation.RETRY,
            QuotaOperation.ESCALATION,
            QuotaOperation.REVIEW,
            QuotaOperation.EMERGENCY,
        ):
            with self.subTest(operation=operation):
                self.assertFalse(guard.evaluate(snapshot, operation).allowed)

    def test_hard_stop_allows_cleanup_and_handoff(self):
        guard = QuotaGuard()
        snapshot = QuotaSnapshot(10, 1)
        self.assertTrue(guard.evaluate(snapshot, QuotaOperation.CLEANUP).allowed)
        self.assertTrue(guard.evaluate(snapshot, QuotaOperation.HANDOFF).allowed)

    def test_default_unknown_policy_allows_bounded_but_blocks_expensive(self):
        guard = QuotaGuard()
        snapshot = QuotaSnapshot(None, None)
        self.assertTrue(guard.evaluate(snapshot).allowed)
        self.assertFalse(guard.evaluate(snapshot, expensive=True).allowed)
        self.assertFalse(guard.evaluate(snapshot, frontier=True).allowed)

    def test_partial_telemetry_still_enforces_a_known_hard_floor(self):
        guard = QuotaGuard()
        self.assertFalse(guard.evaluate(QuotaSnapshot(14, None)).allowed)
        self.assertFalse(guard.evaluate(QuotaSnapshot(None, 2)).allowed)

    def test_economy_state_blocks_expensive_routes(self):
        decision = QuotaGuard().evaluate(QuotaSnapshot(19, 50), expensive=True)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, QuotaState.ECONOMY)

    def test_unknown_policy_is_configurable(self):
        blocked = QuotaGuard(
            QuotaPolicy(unknown_telemetry=UnknownTelemetryPolicy.BLOCK_ALL)
        )
        allowed = QuotaGuard(
            QuotaPolicy(unknown_telemetry=UnknownTelemetryPolicy.ALLOW_BOUNDED)
        )
        snapshot = QuotaSnapshot(None, None)
        self.assertFalse(blocked.evaluate(snapshot).allowed)
        self.assertTrue(allowed.evaluate(snapshot, expensive=True).allowed)

    def test_invalid_snapshot_and_threshold_order_are_rejected(self):
        with self.assertRaises(ValueError):
            QuotaSnapshot(101, 50)
        with self.assertRaises(ValueError):
            QuotaPolicy(five_hour_warn=10, five_hour_economy=20)


class OperatorPolicyTests(unittest.TestCase):
    def test_local_operator_policy_is_read_only_and_customizable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operator.toml"
            path.write_text(
                "[quota_guard]\n"
                "five_hour_stop = 10\n"
                "weekly_stop = 1\n"
                'unknown_telemetry = "block_all"\n',
                encoding="utf-8",
            )
            before = path.read_bytes()
            policy = load_operator_policy(path)
            self.assertEqual(policy.five_hour_stop, 10)
            self.assertEqual(policy.weekly_stop, 1)
            self.assertEqual(policy.unknown_telemetry, UnknownTelemetryPolicy.BLOCK_ALL)
            self.assertEqual(path.read_bytes(), before)

    def test_malformed_operator_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operator.toml"
            path.write_text(
                "[quota_guard]\n"
                "five_hour_stop = 101\n"
                "token = \"must not be accepted\"\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_operator_policy(path)


if __name__ == "__main__":
    unittest.main()
