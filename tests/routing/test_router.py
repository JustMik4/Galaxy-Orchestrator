import unittest
from pathlib import Path
import tempfile

from lib.quota import QuotaGuard, QuotaSnapshot
from lib.routing import (
    CapabilityCatalog,
    CapabilityRouter,
    EmergencyPolicy,
    FailureClass,
    ModelCapability,
    Route,
    RouteProfile,
    RoutingAction,
    RoutingRequest,
)


def capability(model, effort, capability, cost, *, frontier=False):
    return ModelCapability(
        route=Route(model, effort),
        capability=capability,
        expected_cost=cost,
        frontier=frontier,
    )


class CatalogTests(unittest.TestCase):
    def test_missing_observations_leave_configured_pairs_unavailable(self):
        route = capability("astra", "medium", 6, 10, frontier=True)
        catalog = CapabilityCatalog([route])
        self.assertEqual(catalog.observed_pairs, frozenset())
        self.assertEqual(catalog.available(), ())
        self.assertFalse(catalog.supports(route.route))

    def test_only_configured_and_observed_pairs_are_dispatchable(self):
        entries = [
            capability("luna", "medium", 2, 1),
            capability("luna", "ultra", 4, 2),
            capability("sol", "medium", 4, 3),
        ]
        catalog = CapabilityCatalog(
            configured=entries,
            observed_pairs={("luna", "medium"), ("sol", "medium")},
        )
        self.assertEqual(
            [item.route for item in catalog.available()],
            [Route("luna", "medium"), Route("sol", "medium")],
        )
        self.assertFalse(catalog.supports(Route("luna", "ultra")))

    def test_duplicate_pair_is_rejected(self):
        item = capability("luna", "medium", 2, 1)
        with self.assertRaises(ValueError):
            CapabilityCatalog([item, item], {("luna", "medium")})


class RouterTests(unittest.TestCase):
    def setUp(self):
        entries = [
            capability("luna", "low", 1, 1),
            capability("luna", "medium", 2, 2),
            capability("terra", "medium", 3, 3),
            capability("sol", "medium", 4, 5),
            capability("sol", "high", 5, 7),
            capability("astra", "medium", 6, 10, frontier=True),
        ]
        self.router = CapabilityRouter(
            CapabilityCatalog(entries, {entry.route.pair for entry in entries})
        )

    def test_selects_lowest_cost_qualifying_route(self):
        decision = self.router.route(
            RoutingRequest(task_class="implementation", required_capability=3)
        )
        self.assertEqual(decision.route, Route("terra", "medium"))
        self.assertEqual(decision.action, RoutingAction.DISPATCH)

    def test_missing_quota_snapshot_blocks_expensive_observed_route(self):
        astra = capability("astra", "medium", 6, 10, frontier=True)
        router = CapabilityRouter(CapabilityCatalog([astra], [astra.route]))
        decision = router.route(
            RoutingRequest(required_capability=6, frontier_reason="verified need")
        )
        self.assertEqual(decision.action, RoutingAction.BLOCKED_QUOTA)
        self.assertEqual(decision.quota_state, "unknown")

    def test_missing_observation_blocks_even_when_route_is_configured(self):
        astra = capability("astra", "medium", 6, 10, frontier=True)
        router = CapabilityRouter(CapabilityCatalog([astra]))
        decision = router.route(
            RoutingRequest(required_capability=6, frontier_reason="verified need")
        )
        self.assertEqual(decision.action, RoutingAction.BLOCKED)
        self.assertEqual(decision.failure_class, FailureClass.CAPABILITY_MISSING)

    def test_router_uses_custom_local_operator_quota_policy(self):
        bounded = capability("luna", "medium", 2, 2)
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "operator.toml"
            config.write_text(
                "[quota_guard]\nunknown_telemetry = \"block_all\"\n",
                encoding="utf-8",
            )
            router = CapabilityRouter(
                CapabilityCatalog([bounded], [bounded.route]),
                operator_config=config,
            )
            decision = router.route(RoutingRequest(required_capability=1))
        self.assertEqual(decision.action, RoutingAction.BLOCKED_QUOTA)
        self.assertEqual(decision.quota_state, "unknown")

    def test_profiles_change_policy_without_using_unsupported_pairs(self):
        economy = self.router.route(
            RoutingRequest(task_class="implementation", profile=RouteProfile.ECONOMY)
        )
        critical = self.router.route(
            RoutingRequest(task_class="implementation", profile=RouteProfile.CRITICAL)
        )
        self.assertEqual(economy.route, Route("luna", "medium"))
        self.assertEqual(critical.route, Route("sol", "medium"))

    def test_infra_git_and_host_routing_never_promote_model(self):
        previous = Route("luna", "medium")
        for failure in (
            FailureClass.INFRA,
            FailureClass.GIT,
            FailureClass.HOST_ROUTING,
        ):
            with self.subTest(failure=failure):
                result = self.router.route(
                    RoutingRequest(
                        task_class="implementation",
                        failure_class=failure,
                        previous_route=previous,
                    )
                )
                self.assertEqual(result.route, previous)
                self.assertEqual(result.action, RoutingAction.BLOCKED)

    def test_context_insufficient_expands_and_retries_same_route(self):
        previous = Route("luna", "medium")
        result = self.router.route(RoutingRequest(
            task_class="implementation",
            failure_class=FailureClass.CONTEXT_INSUFFICIENT,
            previous_route=previous,
        ))
        self.assertEqual(result.action, RoutingAction.DISPATCH)
        self.assertEqual(result.route, previous)
        self.assertTrue(result.fresh_context)
        self.assertIn("expand referenced context", result.reason)

    def test_architecture_failure_returns_to_architect(self):
        result = self.router.route(
            RoutingRequest(
                role="worker",
                task_class="implementation",
                failure_class=FailureClass.ARCHITECTURE,
                previous_route=Route("sol", "medium"),
            )
        )
        self.assertEqual(result.action, RoutingAction.RETURN_TO_ARCHITECT)
        self.assertIsNone(result.route)

    def test_emergency_uses_intermediate_route_with_evidence(self):
        result = self.router.route(
            RoutingRequest(
                task_class="reasoning",
                emergency=True,
                previous_route=Route("luna", "medium"),
                failure_class=FailureClass.REASONING,
                failure_evidence="same proof obligation failed twice",
                emergency_reason="normal profile is not granular enough",
            )
        )
        self.assertEqual(result.route, Route("terra", "medium"))
        self.assertTrue(result.fresh_context)
        self.assertEqual(result.action, RoutingAction.EMERGENCY_DISPATCH)
        self.assertEqual(result.emergency_record.failure_evidence, "same proof obligation failed twice")
        self.assertEqual(result.emergency_record.previous_route, Route("luna", "medium"))
        self.assertEqual(result.emergency_record.selected_route, Route("terra", "medium"))

    def test_emergency_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.router.route(RoutingRequest(task_class="reasoning", emergency=True))

    def test_capability_missing_is_explicit(self):
        result = self.router.route(
            RoutingRequest(task_class="architecture", required_capability=99)
        )
        self.assertEqual(result.action, RoutingAction.BLOCKED)
        self.assertEqual(result.failure_class, FailureClass.CAPABILITY_MISSING)

    def test_emergency_cannot_bypass_quota_floor(self):
        result = self.router.route(
            RoutingRequest(
                task_class="reasoning",
                emergency=True,
                previous_route=Route("luna", "medium"),
                failure_class=FailureClass.REASONING,
                failure_evidence="evidence",
                emergency_reason="reason",
                quota_snapshot=QuotaSnapshot(five_hour_remaining=15, weekly_remaining=90),
            ),
            quota_guard=QuotaGuard(),
        )
        self.assertEqual(result.action, RoutingAction.BLOCKED_QUOTA)
        self.assertIsNone(result.route)


if __name__ == "__main__":
    unittest.main()
