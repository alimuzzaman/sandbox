from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
import os

from sandbox.resources.adapters import ProviderSnapshot
from sandbox.resources.models import (
    CleanupItemOutcome,
    ResourceObservation,
)
from sandbox.resources.plans import PlanStore
from sandbox.resources.service import ResourceService
from tests.resource_fixtures import NOW, observation, target


class FakeAdapter:
    def __init__(self, resources=(), *, partial=False):
        self._target = target()
        self.resources = tuple(resources)
        self.partial = partial
        self.observe_calls = []
        self.revalidate_map = {item.resource_id: item for item in self.resources}
        self.removed = []

    def target(self):
        return self._target

    def observe(self, *, thorough, budget_seconds, progress=None):
        self.observe_calls.append((thorough, budget_seconds))
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
        self.assertEqual(payload["error"]["code"], "measurement_unavailable")


if __name__ == "__main__":
    unittest.main()
