from __future__ import annotations

import unittest
from datetime import timedelta

from sandbox.resources.models import (
    CleanupCandidate,
    CleanupPlan,
    ResourceObservation,
    ResourceCancellationSignal,
    ResourceRequest,
    StorageTarget,
)
from tests.resource_fixtures import NOW, observation, target


class TestResourceModels(unittest.TestCase):
    def test_request_owns_exact_first_terminal_cancellation_signal(self):
        signal = ResourceCancellationSignal()
        request = ResourceRequest(15, signal)
        self.assertIs(request.cancellation, signal)
        self.assertTrue(signal.disconnect())
        self.assertFalse(signal.cancel())
        self.assertEqual(request.terminal_status(), "disconnected")
        with self.assertRaises(ValueError):
            ResourceRequest(15, lambda: True)

    def test_target_identity_is_required_and_kind_is_bounded(self):
        self.assertEqual(target().to_dict()["identity"], "host-fixture")
        with self.assertRaises(ValueError):
            StorageTarget("remote", "remote-a", "")
        with self.assertRaises(ValueError):
            StorageTarget("other", "other", "id")

    def test_unmeasured_and_protected_resources_cannot_claim_reclaimable_bytes(self):
        with self.assertRaises(ValueError):
            ResourceObservation(
                resource_id="bad", kind="volume", locator="volume",
                display_name="bad", owner_kind="unknown", owner_id=None,
                classification="unverified", size_state="unavailable",
                size_bytes=None, reclaimable_bytes=1,
            )
        with self.assertRaises(ValueError):
            ResourceObservation(
                resource_id="bad", kind="volume", locator="volume",
                display_name="bad", owner_kind="sandbox", owner_id="x",
                classification="active", size_state="measured",
                size_bytes=10, reclaimable_bytes=10,
            )

    def test_safe_output_omits_internal_locator_and_redacts_secret_like_evidence(self):
        item = observation(
            evidence=("token=top-secret", "managed_root"),
            locator="/private/internal/path",
        )
        payload = item.to_dict()
        self.assertNotIn("locator", payload)
        self.assertNotIn("top-secret", str(payload))
        self.assertIn("[redacted]", str(payload).lower())
        self.assertTrue(payload["capacity_accounted"])

    def test_capacity_accounting_marker_is_boolean(self):
        self.assertFalse(observation(capacity_accounted=False).capacity_accounted)
        with self.assertRaises(ValueError):
            observation(capacity_accounted="no")

    def test_cache_plan_rejects_named_volumes(self):
        candidate = CleanupCandidate.from_observation(
            observation(kind="volume", classification="stale_candidate"),
        )
        with self.assertRaises(ValueError):
            CleanupPlan.create(
                target(), "cache", (candidate,), (), now=NOW,
            )

    def test_plan_defaults_to_fifteen_minutes_and_round_trips(self):
        candidate = CleanupCandidate.from_observation(observation())
        plan = CleanupPlan.create(target(), "cache", (candidate,), (), now=NOW)
        self.assertEqual(plan.expires_at - plan.created_at, timedelta(minutes=15))
        self.assertEqual(
            plan.estimated_reclaimable_bytes,
            candidate.expected_reclaimable_bytes,
        )
        self.assertEqual(CleanupPlan.from_dict(plan.to_dict()), plan)

    def test_private_plan_preserves_exact_secret_like_locator_but_public_omits_it(self):
        item = observation(locator="/managed/token=opaque/cache")
        plan = CleanupPlan.create(
            target(),
            "cache",
            (CleanupCandidate.from_observation(item),),
            (),
            now=NOW,
        )
        internal = plan.to_dict()
        self.assertEqual(
            internal["candidates"][0]["locator"],
            "/managed/token=opaque/cache",
        )
        self.assertNotIn("locator", plan.to_dict(public=True)["candidates"][0])


if __name__ == "__main__":
    unittest.main()
