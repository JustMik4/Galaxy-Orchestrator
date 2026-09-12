import json
import unittest

from lib.actions import ActionResolver, Capability, ResolutionCode


class ActionResolverTests(unittest.TestCase):
    def test_plugin_is_preferred_over_cli_and_api(self):
        resolver = ActionResolver([
            Capability("github", "cli", "gh", authenticated=True),
            Capability("github", "api", "github-rest", authenticated=True),
            Capability("github", "plugin", "github-mcp", authenticated=True),
        ])
        decision = resolver.resolve("github.create_issue")
        self.assertEqual(decision.code, ResolutionCode.SELECTED)
        self.assertEqual(decision.capability.id, "github-mcp")

    def test_cli_then_api_fallback(self):
        resolver = ActionResolver([
            Capability("github", "cli", "gh", authenticated=True),
            Capability("github", "api", "github-rest", authenticated=True),
        ])
        self.assertEqual(resolver.resolve("github.create_issue").capability.id, "gh")
        resolver = ActionResolver([Capability("github", "api", "github-rest", authenticated=True)])
        self.assertEqual(resolver.resolve("github.create_issue").capability.id, "github-rest")

    def test_unavailable_or_unauthenticated_are_ignored(self):
        resolver = ActionResolver([
            Capability("github", "plugin", "bad", available=False, authenticated=True),
            Capability("github", "cli", "logged-out", authenticated=False),
        ])
        decision = resolver.resolve("github.create_issue")
        self.assertEqual(decision.code, ResolutionCode.BLOCKED_MISSING_CAPABILITY)

    def test_browser_requires_approval(self):
        resolver = ActionResolver([Capability("github", "browser", "github-web")])
        decision = resolver.resolve("github.create_issue")
        self.assertEqual(decision.code, ResolutionCode.BLOCKED_MISSING_CAPABILITY)
        self.assertEqual(decision.reason, "approval-required")

    def test_explicit_approval_selects_browser(self):
        resolver = ActionResolver([Capability("github", "browser", "github-web")])
        decision = resolver.resolve("github.create_issue", approval=True)
        self.assertEqual(decision.code, ResolutionCode.SELECTED)
        self.assertEqual(decision.capability.kind, "browser")

    def test_preference_override(self):
        resolver = ActionResolver([
            Capability("github", "plugin", "mcp"),
            Capability("github", "cli", "gh", authenticated=True),
        ], preferences={"github.create_issue": ["cli", "plugin"]})
        self.assertEqual(resolver.resolve("github.create_issue").capability.id, "gh")

    def test_decision_is_stable_json(self):
        decision = ActionResolver([Capability("github", "api", "rest", authenticated=True)]).resolve("github.create_issue")
        encoded = json.dumps(decision.to_dict(), sort_keys=True)
        self.assertEqual(encoded, json.dumps(decision.to_dict(), sort_keys=True))

    def test_invalid_capabilities_and_secret_metadata_are_rejected_or_sanitized(self):
        with self.assertRaises(ValueError):
            Capability("github", "telepathy", "provider")
        with self.assertRaises(ValueError):
            Capability("", "api", "provider")
        capability = Capability("github", "api", "provider", metadata={"region": "us", "api_token": "hidden"})
        self.assertEqual(capability.to_dict()["metadata"], {"region": "us"})


if __name__ == "__main__":
    unittest.main()
