import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    def test_artifact_path_rejects_traversal_and_stays_under_root(self):
        with tempfile.TemporaryDirectory() as root:
            policy = AllowedRootPathPolicy((root,))
            self.assertEqual(
                policy.artifact_path(root, "archives", "snapshot.tar.gz"),
                (Path(root) / "archives" / "snapshot.tar.gz").resolve(),
            )
            with self.assertRaisesRegex(ValueError, "outside allowed roots"):
                policy.artifact_path(root, "..", "outside.tar.gz")

    def test_proxy_plan_apply_and_remove_use_exact_route(self):
        calls = []
        manager = CallbackProxyManager(
            apply_route=lambda hostname, port: calls.append(("apply", hostname, port)),
            remove_route=lambda hostname: calls.append(("remove", hostname)),
        )
        plan = manager.plan("fixture.test", 8080)
        self.assertEqual(plan, {"hostname": "fixture.test", "port": 8080})
        manager.apply(plan)
        manager.remove("fixture.test")
        self.assertEqual(calls, [("apply", "fixture.test", 8080), ("remove", "fixture.test")])

    def test_proxy_rejects_malformed_hosts_ports_and_plans(self):
        manager = CallbackProxyManager(apply_route=lambda *_: None, remove_route=lambda *_: None)
        for hostname, port in (
            ("", 8080), ("bad host", 8080), ("bad/host", 8080),
            ("-bad.test", 8080), ("fixture.test", True), ("fixture.test", 0),
        ):
            with self.subTest(hostname=hostname, port=port), self.assertRaises(ValueError):
                manager.plan(hostname, port)
        for plan in (
            {"hostname": "fixture.test", "port": 8080, "extra": True},
            {"hostname": "bad host", "port": 8080},
            {"hostname": "fixture.test", "port": True},
        ):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                manager.apply(plan)
        with self.assertRaises(ValueError):
            manager.remove("bad\nhost")

    def test_rollback_failure_does_not_mask_apply_failure(self):
        def fail_apply(hostname, port):
            raise RuntimeError("apply failed")

        def fail_remove(hostname):
            raise RuntimeError("cleanup failed")

        manager = CallbackProxyManager(apply_route=fail_apply, remove_route=fail_remove)
        with self.assertRaisesRegex(RuntimeError, "apply failed"):
            manager.apply(manager.plan("fixture.test", 8080))

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

    def test_wordpress_proxy_facade_delegates_existing_policy_without_reimplementing_it(self):
        from sandbox.application.context import wordpress_proxy_facade

        cfg = {"instances": {"demo": {"domain": "demo.tst", "wordpress_port": 8188}}}
        calls = []
        core = SimpleNamespace(
            resolve_instances=lambda value: value["instances"],
            _ensure_proxy_up=lambda value: calls.append(("ensure", value)),
            load_config=lambda: {"instances": {}},
            regen_caddyfile=lambda value: calls.append(("regen", value)),
            reload_proxy=lambda: calls.append(("reload",)) or True,
        )
        manager = wordpress_proxy_facade(cfg, core=core)
        plan = manager.plan("demo.tst", 8188)
        manager.apply(plan)
        manager.remove("demo.tst")
        self.assertEqual(calls, [
            ("ensure", cfg), ("regen", {"instances": {}}), ("reload",),
        ])

    def test_wordpress_proxy_facade_rejects_undeclared_apply_and_declared_remove(self):
        from sandbox.application.context import wordpress_proxy_facade

        cfg = {"instances": {"demo": {"domain": "demo.tst", "wordpress_port": 8188}}}
        core = SimpleNamespace(
            resolve_instances=lambda value: value["instances"],
            _ensure_proxy_up=lambda value: None,
            load_config=lambda: cfg,
            regen_caddyfile=lambda value: None,
            reload_proxy=lambda: True,
        )
        manager = wordpress_proxy_facade(cfg, core=core)
        with self.assertRaisesRegex(ValueError, "declared WordPress route"):
            manager.plan("other.tst", 8188)
        with self.assertRaisesRegex(ValueError, "declared WordPress route"):
            manager.apply({"hostname": "other.tst", "port": 8188})
        with self.assertRaisesRegex(ValueError, "still declared"):
            manager.remove("demo.tst")


if __name__ == "__main__":
    unittest.main()
