import unittest

import sandbox.hermes.routing as routing
from sandbox.hermes.routing import recommended_route


class TestHermesRouting(unittest.TestCase):
    def test_recommended_boundaries(self):
        self.assertEqual(recommended_route("inventory").profile, "luna")
        self.assertEqual(recommended_route("implementation").profile, "terra")
        self.assertEqual(recommended_route("architecture").profile, "sol")
        self.assertEqual(recommended_route("implementation", failures=2).profile, "sol")

    def test_policy_evaluation_does_not_import_or_invoke_side_effect_adapters(self):
        before = set(routing.__dict__)
        decision = recommended_route(" implementation ", security_sensitive=False)

        self.assertEqual(decision.profile, "terra")
        self.assertEqual(before, set(routing.__dict__))
        self.assertFalse(any(name in routing.__dict__ for name in ("subprocess", "requests", "socket")))

    def test_target_resolution_is_a_pure_explicit_seam(self):
        """US7 seam: transport-independent target resolution belongs outside _hermes."""
        resolver = getattr(routing, "resolve_target", None)
        self.assertTrue(callable(resolver), "routing must expose resolve_target(target, remotes)")
        remotes = {"prod": {"host": "example.test", "provisioned": True}}
        before = repr(remotes)

        target = resolver("prod", remotes) if callable(resolver) else None

        self.assertEqual(repr(remotes), before)
        self.assertEqual(target.name, "prod")
        self.assertEqual(target.host, "example.test")


if __name__ == "__main__": unittest.main()
