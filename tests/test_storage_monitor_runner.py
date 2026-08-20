"""Scheduled storage-monitor orchestration and deletion gates."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sandbox.resources import monitor
from sandbox.resources.context import PlanStore
from sandbox.resources.models import StorageTarget
from sandbox.resources.reclaim_service import ReclaimService
from sandbox.resources.service import result


TARGET = StorageTarget("remote", "fixture", "a" * 24)
POLICY = {
    "warn_ratio": 0.15,
    "critical_ratio": 0.05,
    "auto_ratio": 0.05,
    "auto_enabled": False,
    "auto_tier": "safe",
    "reap_enabled": False,
    "reap_ttl": None,
}


class _Lease:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.reason = "acquired" if acquired else "lock_held"

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Provider:
    def __init__(self, capacity=None, *, fail_inventory=False):
        self.capacity = capacity
        self.fail_inventory = fail_inventory
        self.calls = []

    def target(self):
        return TARGET

    def inventory(self, *, budget_seconds, directory_cache):
        self.calls.append(("inventory", budget_seconds, directory_cache))
        if self.fail_inventory:
            raise RuntimeError("probe failed " + "x" * 1000)
        return {
            "capacity": self.capacity,
            "reclaim": {
                "status": "complete",
                "entries": [],
                "volumes": [],
                "scratch": [],
                "hosted_sites": [],
                "leases": {},
            },
        }

    def reclaim(self, *_args, **_kwargs):
        self.calls.append(("reclaim",))
        raise AssertionError("monitor test must not call provider reclaim directly")


class _Harness(ReclaimService):
    def __init__(self, provider, store, *, cleanup_exc=None, reap_exc=None):
        super().__init__(provider, store, target=TARGET,
                         clock=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.cleanup_calls = []
        self.reap_calls = []
        self.cleanup_exc = cleanup_exc
        self.reap_exc = reap_exc

    def cleanup(self, **kwargs):
        self.cleanup_calls.append(kwargs)
        if self.cleanup_exc is not None:
            raise self.cleanup_exc
        return result(
            True, "cleanup", status="completed", target=TARGET,
            data={"run_id": "b" * 32, "observed_reclaimed_bytes": 123},
        )

    def reap(self, **kwargs):
        self.reap_calls.append(kwargs)
        if self.reap_exc is not None:
            raise self.reap_exc
        return result(
            True, "reap", status="planned" if kwargs["dry_run"] else "completed",
            target=TARGET,
            data={
                "dry_run": kwargs["dry_run"],
                "candidates": [{"locator": "/tmp/candidate"}],
                "estimated_reclaimable_bytes": 456,
                "observed_reclaimed_bytes": 456 if not kwargs["dry_run"] else 0,
            },
        )


class MonitorRunnerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.runtime_patch = mock.patch.object(monitor, "RUNTIME_DIR", self.runtime)
        self.runtime_patch.start()
        self.lock_patch = mock.patch.object(monitor, "monitor_lock", lambda _target: _Lease())
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.addCleanup(self.runtime_patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.store = self.runtime / "plans"

    def service(self, provider, **kwargs):
        # The monitor path never consults the plan store in this test harness,
        # so a path-like sentinel is sufficient and avoids host setup.
        return _Harness(provider, self.store, **kwargs)

    @staticmethod
    def capacity(free, total=100):
        if free is None:
            return None
        return {"total_bytes": total, "available_bytes": free,
                "used_bytes": total - free}

    def run_monitor(self, provider, config=None, **kwargs):
        service = self.service(provider)
        payload = service.monitor(config or POLICY, budget_seconds=20, **kwargs)
        return service, payload

    def test_default_observation_never_deletes_at_any_pressure_level(self):
        for free in (50, 10, 1, None):
            provider = _Provider(self.capacity(free))
            service, payload = self.run_monitor(provider)
            self.assertEqual(service.cleanup_calls, [])
            self.assertEqual(len(service.reap_calls), 1)
            self.assertTrue(service.reap_calls[0]["dry_run"])
            self.assertEqual(service.reap_calls[0]["directory_cache"], "cache_only")
            self.assertEqual(provider.calls[0][2], "cache_only")
            self.assertEqual(payload["data"]["auto"]["ran"], False)

    def test_real_service_default_never_calls_provider_reclaim(self):
        # Exercise the production ``reap``/``plan`` path as well as the
        # monitor harness: a dry reaper may write a review plan, but it cannot
        # cross the provider's deletion seam.
        for free in (50, 10, 1, None):
            provider = _Provider(self.capacity(free))
            service = ReclaimService(
                provider, PlanStore(self.runtime / f"plans-{free}"), target=TARGET,
            )
            with mock.patch.object(monitor, "monitor_lock", lambda _target: _Lease()):
                payload = service.monitor(POLICY, budget_seconds=20)
            self.assertEqual(payload["data"]["reap"]["dry_run"], True)
            self.assertEqual([call[0] for call in provider.calls], ["inventory", "inventory"])
            self.assertTrue(all(call[2] == "cache_only" for call in provider.calls))

    def test_auto_path_is_safe_and_scheduled_auto_only_when_eligible(self):
        config = {**POLICY, "auto_enabled": True}
        provider = _Provider(self.capacity(5))
        service, payload = self.run_monitor(
            provider, config, trigger="scheduled", dry_run=False,
        )
        self.assertEqual(len(service.cleanup_calls), 1)
        self.assertEqual(service.cleanup_calls[0]["tier"], "safe")
        self.assertEqual(service.cleanup_calls[0]["trigger"], "scheduled_auto")
        self.assertTrue(service.cleanup_calls[0]["confirm"])
        self.assertEqual(service.cleanup_calls[0]["budget_seconds"], 20.0)
        self.assertEqual(service.cleanup_calls[0]["directory_cache"], "cache_only")
        self.assertTrue(payload["data"]["auto"]["eligible"])
        self.assertTrue(payload["data"]["auto"]["ran"])
        self.assertEqual(payload["data"]["auto"]["reclaimed_bytes"], 123)

        service, payload = self.run_monitor(
            _Provider(self.capacity(50)), config, dry_run=False,
        )
        self.assertEqual(service.cleanup_calls, [])
        self.assertEqual(payload["data"]["auto"]["reason"], "threshold_not_reached")

    def test_unknown_capacity_never_enters_the_automatic_path(self):
        config = {**POLICY, "auto_enabled": True}
        provider = _Provider(None)
        service, payload = self.run_monitor(provider, config)
        self.assertEqual(payload["data"]["level"], "unknown")
        self.assertFalse(payload["data"]["auto"]["eligible"])
        self.assertEqual(payload["data"]["auto"]["reason"], "capacity_unknown")
        self.assertEqual(service.cleanup_calls, [])

    def test_partial_automatic_cleanup_is_not_reported_as_completed(self):
        class PartialCleanup(_Harness):
            def cleanup(self, **kwargs):
                self.cleanup_calls.append(kwargs)
                return result(
                    True, "cleanup", status="partial", target=TARGET,
                    data={"run_id": "c" * 32, "observed_reclaimed_bytes": 7},
                )

        service = PartialCleanup(_Provider(self.capacity(1)), self.store)
        payload = service.monitor(
            {**POLICY, "auto_enabled": True}, budget_seconds=20,
        )
        self.assertTrue(payload["data"]["auto"]["ran"])
        self.assertEqual(payload["data"]["auto"]["reason"], "partial")

    def test_dry_run_overrides_both_deletion_opt_ins(self):
        config = {**POLICY, "auto_enabled": True, "reap_enabled": True}
        service, payload = self.run_monitor(
            _Provider(self.capacity(1)), config, dry_run=True,
        )
        self.assertTrue(payload["data"]["auto"]["eligible"])
        self.assertFalse(payload["data"]["auto"]["ran"])
        self.assertEqual(payload["data"]["auto"]["reason"], "dry_run")
        self.assertTrue(payload["data"]["reap"]["dry_run"])
        self.assertEqual(service.cleanup_calls, [])
        self.assertTrue(service.reap_calls[0]["dry_run"])
        self.assertFalse(service.reap_calls[0]["confirm"])

    def test_non_safe_auto_tier_refuses_before_provider_contact(self):
        provider = _Provider(self.capacity(1))
        payload = self.service(provider).monitor(
            {**POLICY, "auto_enabled": True, "auto_tier": "all"},
            budget_seconds=20,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_auto_tier")
        self.assertEqual(provider.calls, [])

    def test_reap_is_always_called_dry_by_default_and_real_when_opted_in(self):
        service, payload = self.run_monitor(_Provider(self.capacity(50)))
        self.assertEqual(payload["data"]["reap"]["enabled"], False)
        self.assertTrue(payload["data"]["reap"]["dry_run"])
        self.assertEqual(service.reap_calls[0]["confirm"], False)

        service, payload = self.run_monitor(
            _Provider(self.capacity(50)),
            {**POLICY, "reap_enabled": True},
            dry_run=False,
        )
        self.assertEqual(payload["data"]["reap"]["enabled"], True)
        self.assertFalse(payload["data"]["reap"]["dry_run"])
        self.assertTrue(service.reap_calls[0]["confirm"])
        self.assertEqual(service.reap_calls[0]["budget_seconds"], 20.0)
        self.assertEqual(payload["data"]["reap"]["reclaimed_bytes"], 456)

    def test_full_monitor_record_is_written_and_round_trips(self):
        provider = _Provider(self.capacity(10))
        _service, payload = self.run_monitor(provider, trigger="scheduled")
        self.assertEqual(set(payload["data"]), {
            "schema", "target", "at", "trigger", "level", "free_bytes",
            "total_bytes", "free_ratio", "warn_ratio", "critical_ratio",
            "auto_ratio", "threshold_crossed", "guidance", "auto", "reap",
            "inventory_status", "errors",
        })
        stored = monitor.read_record({"kind": "remote", "name": "fixture"})
        self.assertEqual(stored, payload["data"])
        self.assertEqual(stored["trigger"], "scheduled")
        self.assertEqual(stored["target"], {"kind": "remote", "name": "fixture"})

    def test_lock_held_skips_without_measuring_or_writing(self):
        with mock.patch.object(monitor, "monitor_lock", lambda _target: _Lease(False)):
            provider = _Provider(self.capacity(1))
            payload = self.service(provider).monitor(POLICY, budget_seconds=20)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["data"]["reason"], "lock_held")
        self.assertEqual(provider.calls, [])
        self.assertIsNone(monitor.read_record({"kind": "remote", "name": "fixture"}))

    def test_provider_failure_is_bounded_and_recorded(self):
        provider = _Provider(self.capacity(10), fail_inventory=True)
        _service, payload = self.run_monitor(provider)
        self.assertEqual(payload["status"], "unknown")
        self.assertFalse(payload["ok"])
        errors = payload["data"]["errors"]
        self.assertEqual(errors[0]["code"], "reclaim_inventory_unavailable")
        self.assertLessEqual(len(errors[0]["message"]), 240)
        self.assertIsNotNone(monitor.read_record({"kind": "remote", "name": "fixture"}))

    def test_cleanup_and_reap_failures_are_bounded_and_recorded(self):
        service = self.service(
            _Provider(self.capacity(1)),
            cleanup_exc=RuntimeError("cleanup failed " + "x" * 1000),
            reap_exc=RuntimeError("reap failed " + "y" * 1000),
        )
        payload = service.monitor(
            {**POLICY, "auto_enabled": True, "reap_enabled": True},
            budget_seconds=20,
        )
        self.assertFalse(payload["ok"])
        errors = payload["data"]["errors"]
        self.assertEqual({error["code"] for error in errors},
                         {"cleanup_failed", "reap_failed"})
        self.assertTrue(all(len(error["message"]) <= 240 for error in errors))
        stored = monitor.read_record({"kind": "remote", "name": "fixture"})
        self.assertEqual(stored["errors"], errors)


if __name__ == "__main__":
    unittest.main()
