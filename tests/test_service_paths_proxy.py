import tempfile
import unittest
from pathlib import Path

from sandbox.services.paths import AllowedRootPathPolicy
from sandbox.services.proxy import CallbackProxyManager


class TestPathsAndProxy(unittest.TestCase):
    def test_paths_fail_closed_outside_root(self):
        with tempfile.TemporaryDirectory() as root:
            policy = AllowedRootPathPolicy((root,))
            self.assertEqual(
                policy.require_allowed(Path(root) / "artifact"),
                (Path(root) / "artifact").resolve(),
            )
            with self.assertRaisesRegex(ValueError, "outside allowed roots"):
                policy.require_allowed("/etc/passwd")

    def test_failed_proxy_apply_rolls_back_exact_hostname(self):
        calls = []
        def fail(hostname, port):
            calls.append(("apply", hostname, port))
            raise RuntimeError("fixture")
        manager = CallbackProxyManager(
            apply_route=fail,
            remove_route=lambda hostname: calls.append(("remove", hostname)),
        )
        plan = manager.plan("fixture.test", 8080)
        with self.assertRaises(RuntimeError):
            manager.apply(plan)
        self.assertEqual(calls, [("apply", "fixture.test", 8080), ("remove", "fixture.test")])


if __name__ == "__main__":
    unittest.main()
