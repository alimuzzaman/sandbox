from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
import os
from types import SimpleNamespace

from sandbox.resources.adapters import ProviderSnapshot
from sandbox.resources.attribution import (
    DeepAttribution,
    FilesystemObservation,
    reconcile_attribution,
)
from sandbox.resources.models import (
    CleanupItemOutcome,
    ResourceObservation,
)
from sandbox.resources.plans import PlanStore
from sandbox.resources.service import ResourceService
from tests.resource_fixtures import NOW, observation, target


class FakeAdapter:
    def __init__(
        self, resources=(), *, partial=False, deep_attribution=None,
        capacity_scope_id=None,
    ):
        self._target = target()
        self.resources = tuple(resources)
        self.partial = partial
        self.observe_calls = []
        self.revalidate_map = {item.resource_id: item for item in self.resources}
        self.removed = []
        self.deep_attribution = deep_attribution
        self.capacity_scope_id = capacity_scope_id

    def target(self):
        return self._target

    def observe(
        self, *, thorough, budget_seconds, progress=None, focus=None,
        deep=False,
    ):
        self.observe_calls.append((thorough, budget_seconds, focus))
        if progress:
            progress("fixture")
        return ProviderSnapshot(
            self._target,
            {
                "total_bytes": 100_000,
                "used_bytes": 80_000,
                "available_bytes": 19_000,
                "reserved_bytes": 1_000,
            },
            self.resources,
            ({"category": "slow", "status": "timed_out"},) if self.partial else (),
            None,
            self.deep_attribution,
            self.capacity_scope_id,
        )

    def revalidate(self, candidate):
        return self.revalidate_map.get(candidate.resource_id)

    def remove(self, candidate):
        self.removed.append(candidate.resource_id)
        item = self.revalidate_map.pop(candidate.resource_id, None)
        return CleanupItemOutcome(
            candidate.resource_id,
            "removed" if item else "already_absent",
            "removed" if item else "already_absent",
            candidate.expected_size_bytes,
            False,
            NOW,
        )


class CancellationAwareAdapter(FakeAdapter):
    def __init__(self, *args, category_outcomes=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.cancelled_calls = []
        self.category_outcomes = tuple(category_outcomes)

    def observe(
        self, *, thorough, budget_seconds, progress=None, focus=None,
        deep=False, cancelled=False,
    ):
        self.cancelled_calls.append(cancelled)
        snapshot = super().observe(
            thorough=thorough,
            budget_seconds=budget_seconds,
            progress=progress,
            focus=focus,
            deep=deep,
        )
        return ProviderSnapshot(
            snapshot.target,
            snapshot.capacity,
            snapshot.resources,
            self.category_outcomes or snapshot.category_outcomes,
            snapshot.drift,
            snapshot.deep_attribution,
            snapshot.capacity_scope_id,
        )


class NetworkPressureAdapter(FakeAdapter):
    def __init__(self, count, **kwargs):
        networks = tuple(
            observation(
                f"network-{index}",
                kind="network",
                classification="active",
                size_bytes=0,
                owner_kind="project",
                owner_id=f"sandbox-project-{index}",
                locator=f"network-{index}",
                capacity_accounted=False,
            )
            for index in range(count)
        )
        super().__init__(networks, **kwargs)

    def observe(self, *, thorough, budget_seconds, progress=None, focus=None,
                deep=False):
        snapshot = super().observe(
            thorough=thorough,
            budget_seconds=budget_seconds,
            progress=progress,
            focus=focus,
            deep=deep,
        )
        return ProviderSnapshot(
            snapshot.target,
            snapshot.capacity,
            snapshot.resources,
            ({"category": "docker_networks", "status": "complete"},),
            snapshot.drift,
            snapshot.deep_attribution,
            snapshot.capacity_scope_id,
        )


class TestResourceService(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = NOW

    def tearDown(self):
        self.temp.cleanup()

    def service(self, adapter):
        return ResourceService(
            adapter,
            PlanStore(Path(self.temp.name), clock=lambda: self.now),
            clock=lambda: self.now,
        )

    def test_fast_status_reconciles_capacity_ranks_resources_and_redacts(self):
        resources = (
            observation("small", size_bytes=100),
            observation(
                "large", size_bytes=20_000,
                evidence=("credential=hunter2", "managed_root"),
            ),
            observation(
                "unknown", classification="unmanaged", size_bytes=10_000,
                owner_kind="unmanaged", owner_id=None,
            ),
        )
        payload = self.service(FakeAdapter(resources)).status(
            thorough=False, budget_seconds=15,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "complete")
        data = payload["data"]
        self.assertEqual(data["resources"][0]["resource_id"], "large")
        self.assertEqual(data["summary"]["reclaimable_bytes"], 20_100)
        self.assertEqual(data["summary"]["unknown_bytes"], 49_900)
        self.assertEqual(
            data["summary"]["categories"][0]["id"],
            "download_cache",
        )
        self.assertEqual(
            data["summary"]["owners"][0]["id"],
            "sandbox:sandbox",
        )
        self.assertNotIn("hunter2", str(payload))

    def test_capacity_pressure_is_additive_and_thresholded(self):
        for count, level, code in (
            (23, "low", None),
            (24, "medium", "network_capacity_pressure"),
            (28, "high", "network_pool_exhausted"),
        ):
            with self.subTest(count=count):
                payload = self.service(NetworkPressureAdapter(count)).status(
                    budget_seconds=15,
                )
                self.assertTrue(payload["ok"])
                pressure = payload["data"]["capacity_pressure"]
                self.assertEqual(pressure["level"], level)
                self.assertEqual(
                    pressure["managed_user_defined_network_count"], count,
                )
                self.assertEqual(pressure["threshold"], 28)
                self.assertEqual(pressure["recovery"]["code"], code)
                self.assertFalse(pressure["recovery"]["automatic_cleanup"])
                self.assertNotIn("network_pool_exhausted", str(payload["error"]))

    def test_pressure_guidance_never_implies_active_network_cleanup(self):
        payload = self.service(NetworkPressureAdapter(31)).status(
            budget_seconds=15,
        )
        guidance = payload["data"]["capacity_pressure"]["recovery"]["guidance"]
        self.assertIn("workspace destroy", guidance)
        self.assertIn("rescan", guidance)
        self.assertIn("Do not delete active", guidance)

    def test_secret_corpus_is_redacted_from_status(self):
        item = observation(
            "secret-corpus",
            evidence=(
                "password=alpha",
                "authorization:Bearer-beta",
                "cookie=session-gamma",
                "credential=delta",
            ),
        )
        payload = self.service(FakeAdapter((item,))).status()
        rendered = str(payload)
        for secret in ("alpha", "beta", "gamma", "delta"):
            self.assertNotIn(secret, rendered)

    def test_partial_status_never_converts_timeout_to_zero(self):
        item = observation(
            "slow", classification="unverified", size_bytes=None,
            owner_kind="unknown", owner_id=None,
        )
        payload = self.service(FakeAdapter((item,), partial=True)).status(
            thorough=True, budget_seconds=2,
        )
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["resources"][0]["size_bytes"], None)
        self.assertEqual(payload["data"]["confidence"], "low")

    def test_capacity_attribution_excludes_nested_detail_observations(self):
        accounting_root = observation(
            "host-root", kind="host_root", classification="retained",
            size_bytes=60_000, capacity_accounted=True,
        )
        nested_volume = observation(
            "nested-volume", kind="volume", classification="active",
            size_bytes=40_000, capacity_accounted=False,
        )
        payload = self.service(FakeAdapter((
            accounting_root, nested_volume,
        ))).status(thorough=True, budget_seconds=15)
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 60_000)
        self.assertEqual(payload["data"]["summary"]["unknown_bytes"], 20_000)

    def test_deep_reconciliation_replaces_managed_only_capacity_gap(self):
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000,
                directory_allocated_bytes=60_000,
                deleted_open_bytes=10_000,
                overlapping_logical_bytes=50_000,
            ),
        )
        payload = self.service(FakeAdapter(
            (observation("managed", size_bytes=20_000),),
            deep_attribution=deep,
        )).status(deep=True, budget_seconds=15)
        self.assertEqual(payload["data"]["mode"], "deep")
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 70_000)
        self.assertEqual(payload["data"]["summary"]["unknown_bytes"], 10_000)
        self.assertEqual(
            payload["data"]["deep_attribution"]["reconciliation"][
                "overlapping_logical_bytes"
            ],
            50_000,
        )

    def test_cancelled_deep_request_forwards_signal_and_retains_evidence(self):
        deep = DeepAttribution(
            status="partial",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000,
                directory_allocated_bytes=60_000,
            ),
        )
        adapter = CancellationAwareAdapter(deep_attribution=deep)
        payload = self.service(adapter).status(deep=True, cancelled=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["data"]["completeness"], "cancelled")
        self.assertEqual(payload["data"]["deep_attribution"]["status"], "partial")
        self.assertEqual(adapter.cancelled_calls, [True])

    def test_cancelled_request_does_not_call_legacy_provider(self):
        adapter = FakeAdapter()
        payload = self.service(adapter).status(cancelled=True)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["error"]["code"], "request_cancelled")
        self.assertEqual(adapter.observe_calls, [])

    def test_capable_provider_pre_cancellation_returns_structured_evidence(self):
        adapter = CancellationAwareAdapter((
            observation("completed-before-cancel", size_bytes=10_000),
        ))

        def cancelled_snapshot(**_kwargs):
            return ProviderSnapshot(
                adapter.target(),
                None,
                adapter.resources,
                ({"category": "deep_directory", "status": "cancelled"},),
            )

        adapter.observe = cancelled_snapshot
        payload = self.service(adapter).status(deep=True, cancelled=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["data"]["completeness"], "cancelled")
        self.assertIsNone(payload["data"]["capacity"])
        self.assertEqual(
            payload["data"]["resources"][0]["resource_id"],
            "completed-before-cancel",
        )
        self.assertIsNone(payload["error"])

    def test_disconnected_category_retains_completed_evidence(self):
        adapter = CancellationAwareAdapter(
            (observation("completed", size_bytes=10_000),),
            category_outcomes=({
                "category": "remote_probe",
                "status": "disconnected",
                "reason": "ssh_disconnected_after_payload",
            },),
        )
        payload = self.service(adapter).status(thorough=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "disconnected")
        self.assertEqual(payload["data"]["resources"][0]["resource_id"], "completed")

    def test_deep_capacity_scope_mismatch_is_partial_and_not_combined(self):
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=70_000,
                directory_allocated_bytes=60_000,
            ),
        )
        payload = self.service(FakeAdapter(
            (observation("managed", size_bytes=20_000),),
            deep_attribution=deep,
        )).status(deep=True)

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 0)
        self.assertEqual(payload["data"]["summary"]["unknown_bytes"], 80_000)
        self.assertEqual(payload["data"]["deep_attribution"]["status"], "partial")
        self.assertIn({
            "category": "reconciliation",
            "status": "partial",
            "reason": "capacity_scope_mismatch",
        }, payload["data"]["category_outcomes"])

    def test_matching_used_bytes_with_distinct_scope_ids_is_not_combined(self):
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000,
                directory_allocated_bytes=60_000,
            ),
        )
        object.__setattr__(deep, "capacity_scope_id", "filesystem-deep")
        adapter = FakeAdapter(deep_attribution=deep)
        original_observe = adapter.observe

        def scoped_observe(
            *, thorough, budget_seconds, progress=None, focus=None, deep=False,
        ):
            snapshot = original_observe(
                thorough=thorough,
                budget_seconds=budget_seconds,
                progress=progress,
                focus=focus,
                deep=deep,
            )
            return SimpleNamespace(
                target=snapshot.target,
                capacity=snapshot.capacity,
                resources=snapshot.resources,
                category_outcomes=snapshot.category_outcomes,
                drift=snapshot.drift,
                deep_attribution=snapshot.deep_attribution,
                capacity_scope_id="filesystem-capacity",
            )

        adapter.observe = scoped_observe
        payload = self.service(adapter).status(deep=True)

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["capacity_scope_id"], "filesystem-capacity")
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 0)

    def test_matching_used_bytes_with_one_missing_scope_id_is_not_combined(self):
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000,
                directory_allocated_bytes=60_000,
            ),
        )
        payload = self.service(FakeAdapter(
            deep_attribution=deep,
            capacity_scope_id="filesystem-capacity",
        )).status(deep=True)

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 0)

    def test_legacy_multi_filesystem_deep_scope_fails_closed(self):
        def filesystem(identity):
            return FilesystemObservation(
                filesystem_id=identity,
                display_name=identity,
                filesystem_type="unknown",
                total_bytes=100_000,
                used_bytes=40_000,
                available_bytes=60_000,
                writable=True,
                selected=True,
                selection_reason="managed_root",
                status="complete",
                observed_allocated_bytes=30_000,
                hardlink_deduplication="confirmed",
            )

        deep = DeepAttribution(
            status="complete",
            filesystems=(filesystem("filesystem-a"), filesystem("filesystem-b")),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000,
                directory_allocated_bytes=60_000,
            ),
        )
        payload = self.service(FakeAdapter(
            deep_attribution=deep,
        )).status(deep=True)

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["summary"]["attributed_bytes"], 0)

    def test_deep_capacity_drift_is_reported_with_materiality(self):
        capacity_drift = 5 * 1024 * 1024
        attributed_drift = 65 * 1024 * 1024
        deep = DeepAttribution(
            status="complete",
            filesystems=(),
            findings=(),
            capabilities=(),
            coverage=(),
            reconciliation=reconcile_attribution(
                used_bytes=80_000_000,
                directory_allocated_bytes=60_000_000,
                capacity_drift_bytes=capacity_drift,
                attributed_drift_bytes=attributed_drift,
            ),
        )
        payload = self.service(FakeAdapter(deep_attribution=deep)).status(deep=True)

        reported = payload["data"]["drift"]
        self.assertEqual(reported["capacity_drift_bytes"], capacity_drift)
        self.assertEqual(reported["attributed_drift_bytes"], attributed_drift)
        self.assertFalse(reported["capacity_drift_material"])
        self.assertTrue(reported["attributed_drift_material"])

    def test_cache_plan_is_read_only_and_excludes_named_volumes(self):
        cache = observation("cache", size_bytes=500)
        volume = observation(
            "volume", kind="volume", classification="stale_candidate",
            size_bytes=900,
        )
        adapter = FakeAdapter((cache, volume))
        payload = self.service(adapter).plan(
            "cache", thorough=True, budget_seconds=15,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(
            [item["resource_id"] for item in payload["data"]["candidates"]],
            ["cache"],
        )
        self.assertEqual(adapter.removed, [])
        self.assertTrue(payload["data"]["requires_confirmation"])
        self.assertEqual(adapter.observe_calls[-1], (True, 15.0, "cache"))

    def test_cleanup_refuses_confirmation_before_adapter_access(self):
        adapter = FakeAdapter()
        service = self.service(adapter)
        payload = service.cleanup("0" * 32, confirm=False)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")
        self.assertEqual(adapter.observe_calls, [])

    def test_cleanup_revalidates_and_skips_changed_candidate(self):
        cache = observation("cache", size_bytes=500)
        adapter = FakeAdapter((cache,))
        service = self.service(adapter)
        planned = service.plan("cache", thorough=True, budget_seconds=15)
        plan_id = planned["data"]["plan_id"]
        adapter.revalidate_map["cache"] = observation(
            "cache", classification="active", size_bytes=500,
            references=("container:active",),
        )
        result = service.cleanup(plan_id, confirm=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["outcomes"][0]["status"], "skipped")
        self.assertEqual(result["data"]["outcomes"][0]["reason"], "evidence_changed")
        self.assertEqual(adapter.removed, [])

    def test_cleanup_removes_exact_candidate_and_refuses_replay(self):
        cache = observation("cache", size_bytes=500)
        adapter = FakeAdapter((cache,))
        service = self.service(adapter)
        plan_id = service.plan("cache", thorough=True, budget_seconds=15)["data"]["plan_id"]
        result = service.cleanup(plan_id, confirm=True)
        self.assertTrue(result["ok"])
        self.assertEqual(adapter.removed, ["cache"])
        replay = service.cleanup(plan_id, confirm=True)
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["error"]["code"], "plan_already_used")
        receipts = list(Path(self.temp.name).glob("*.run.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(os.stat(receipts[0]).st_mode & 0o777, 0o600)
        self.assertEqual(result["data"]["drift"]["reason"],
                         "concurrent_or_shared_storage_change")

    def test_remote_or_local_measurement_without_capacity_fails_safely(self):
        adapter = FakeAdapter()
        adapter.observe = lambda **_kwargs: ProviderSnapshot(
            adapter.target(), None, (),
            ({"category": "remote_probe", "status": "unavailable"},),
        )
        payload = self.service(adapter).status()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error"]["code"], "measurement_unavailable")


if __name__ == "__main__":
    unittest.main()
