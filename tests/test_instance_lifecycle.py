import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class TestInstanceLifecycleConfig(unittest.TestCase):
    def test_wordpress_power_uses_the_core_instance_owner(self):
        import inspect
        from sandbox.application import context

        source = inspect.getsource(context.runtime_service)
        self.assertIn("core.resolve_instances(cfg)", source)
        self.assertNotIn("sc.resolve_instances(cfg)", source)

    def test_omission_resolves_to_idle_stop_and_request_wake(self):
        from sandbox.config.instance_lifecycle import normalize_instance_lifecycle

        self.assertEqual(normalize_instance_lifecycle(), {
            "mode": "idle_stop",
            "wakeOnRequest": True,
            "idleAfterSeconds": 1800,
            "wakeTimeoutSeconds": 60,
            "stopGraceSeconds": 30,
            "maxPendingRequests": 32,
        })

    def test_common_manifest_materializes_default_and_allows_explicit_opt_out(self):
        from sandbox.config.manifest import apply_common_config

        resolved = apply_common_config({})
        self.assertEqual(resolved["instanceLifecycle"]["mode"], "idle_stop")
        self.assertIs(resolved["instanceLifecycle"]["wakeOnRequest"], True)
        self.assertEqual(resolved["instanceLifecycle"]["idleAfterSeconds"], 1800)
        opted_out = apply_common_config({"instanceLifecycle": {"mode": "always_on"}})
        self.assertEqual(opted_out["instanceLifecycle"]["mode"], "always_on")

    def test_idle_stop_is_strict_and_detached(self):
        from sandbox.config.instance_lifecycle import normalize_instance_lifecycle

        raw = {"mode": "idle_stop", "idleAfterSeconds": 300}
        normalized = normalize_instance_lifecycle(raw)
        raw["idleAfterSeconds"] = 60
        self.assertEqual(normalized["mode"], "idle_stop")
        self.assertEqual(normalized["idleAfterSeconds"], 300)

    def test_invalid_values_fail_closed(self):
        from sandbox.config.instance_lifecycle import InstanceLifecycleConfigError, normalize_instance_lifecycle

        for raw in (
            {"mode": "provider_native"},
            {"mode": "idle_stop", "idleAfterSeconds": 1},
            {"maxPendingRequests": True},
            {"unknown": 1},
            {"wakeOnRequest": 1},
        ):
            with self.subTest(raw=raw), self.assertRaises(InstanceLifecycleConfigError):
                normalize_instance_lifecycle(raw)

    def test_explicit_null_is_not_treated_as_omission(self):
        from sandbox.config.manifest import apply_common_config

        with self.assertRaisesRegex(ValueError, "instanceLifecycle must be an object"):
            apply_common_config({"instanceLifecycle": None})

    def test_compose_descriptor_carries_policy_through_common_manifest(self):
        from sandbox.config.compose import ComposeSchemaProvider
        from sandbox.config.manifest import apply_common_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "instanceLifecycle": {"mode": "idle_stop", "idleAfterSeconds": 300},
                "compose": {
                    "file": "compose.yaml", "service": "web", "internal_port": 80,
                    "health_path": "/healthz",
                },
            }))
            descriptor = ComposeSchemaProvider().resolve(root)
            resolved = apply_common_config(descriptor)
            self.assertEqual(resolved["instanceLifecycle"]["mode"], "idle_stop")
            self.assertEqual(resolved["instanceLifecycle"]["idleAfterSeconds"], 300)

    def test_wordpress_instance_resolution_preserves_normalized_policy(self):
        import sandbox.core._instances as instances

        cfg = {"instances": {"site-a": {
            "instance_lifecycle": {"mode": "idle_stop", "idleAfterSeconds": 300},
            "activation_route": {"id": "ar_1234567890abcdef", "token": "t" * 32},
        }}}
        registry = {"fixture": {
            "instance": "site-a", "root": "/tmp/site-a", "kind": "wordpress",
        }}
        with mock.patch.object(
                instances, "_core",
                return_value=SimpleNamespace(registry_all=lambda: registry)):
            resolved = instances.resolve_instances(cfg)["site-a"]
        self.assertEqual(resolved["instance_lifecycle"]["mode"], "idle_stop")
        self.assertEqual(resolved["instance_lifecycle"]["idleAfterSeconds"], 300)
        self.assertEqual(resolved["activation_route"]["id"], "ar_1234567890abcdef")
        self.assertEqual(resolved["activation_route"]["token"], "t" * 32)


class TestActivationCoordinator(unittest.TestCase):
    def test_default_activation_policy_waits_thirty_minutes(self):
        from sandbox.activation.coordinator import ActivationPolicy

        policy = ActivationPolicy()
        self.assertEqual(policy.idle_after_seconds, 1800)

    def test_single_flight_pending_bound_and_idle_due(self):
        from sandbox.activation.coordinator import ActivationCoordinator, ActivationPolicy, ActivationState

        now = [1000.0]
        coordinator = ActivationCoordinator(clock=lambda: now[0])
        coordinator.register("site-a", ActivationPolicy(
            mode="idle_stop", idle_after_seconds=60, max_pending_requests=1,
        ))
        self.assertTrue(coordinator.begin_request("site-a"))
        self.assertFalse(coordinator.begin_request("site-a"))
        coordinator.mark_ready("site-a")
        coordinator.end_request("site-a")
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 61
        self.assertEqual(coordinator.due_for_suspend(), ("site-a",))
        self.assertEqual(coordinator.snapshot("site-a")["state"], ActivationState.DRAINING.value)

    def test_lease_prevents_suspend_until_expiry(self):
        from sandbox.activation.coordinator import ActivationCoordinator, ActivationPolicy

        now = [100.0]
        coordinator = ActivationCoordinator(clock=lambda: now[0])
        coordinator.register("site-b", ActivationPolicy(mode="idle_stop", idle_after_seconds=60))
        coordinator.mark_ready("site-b")
        lease = coordinator.acquire_lease("site-b", "job", ttl_seconds=100)
        now[0] += 61
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 40
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 60
        self.assertEqual(coordinator.due_for_suspend(), ("site-b",))
        self.assertFalse(coordinator.release_lease(lease.lease_id))

    def test_activation_service_starts_once_for_concurrent_requests(self):
        from sandbox.activation import ActivationPolicy, ActivationService
        import threading

        service = ActivationService()
        service.register("site-c", ActivationPolicy(mode="idle_stop"))
        starts = []
        ready = threading.Barrier(3)
        resumed = threading.Event()
        results = []

        def resume(route_id, timeout):
            starts.append((route_id, timeout))
            resumed.set()
            return True

        def invoke():
            ready.wait(timeout=2)
            results.append(service.activate("site-c", resume=resume))

        workers = [threading.Thread(target=invoke) for _ in range(2)]
        for worker in workers:
            worker.start()
        ready.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
        self.assertTrue(resumed.is_set())
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.ok for item in results))


if __name__ == "__main__":
    unittest.main()
