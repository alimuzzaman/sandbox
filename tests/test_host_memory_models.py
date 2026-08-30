from __future__ import annotations
import unittest
from datetime import datetime, timezone
from sandbox.resources.host_memory.models import (
    AggregateMemorySample, HistoryWindow, HostMemoryStatusProjection, MonitorHealth,
    OwnershipReceipt, ProtectedSwapOperation, RemoteServiceEvidence, RemoteSwapState,
    SwapLifecyclePlan, SwapPolicy, bounded, canonical_digest,
)
from tests.host_memory_assertions import assert_privacy_bounded
from tests.host_memory_fixtures import (
    MARKER, NOW, REVISION, TARGET, eligible_state, ownership_receipt, sample, status_state,
)


class HostMemoryModelsTest(unittest.TestCase):
    def test_service_evidence_and_policy_are_strict(self):
        RemoteServiceEvidence("r", TARGET, MARKER, REVISION)
        self.assertEqual(SwapPolicy().size_gib, 4)
        for bad in (0, 9, True, "4"):
            with self.assertRaises(ValueError): SwapPolicy(size_gib=bad)

    def test_sample_allowlist_and_projection_are_bounded(self):
        model=AggregateMemorySample(**{k:v for k,v in sample().items() if k!="schema_version"})
        assert_privacy_bounded(self, model.to_dict())
        projection=HostMemoryStatusProjection(TARGET,"2026-08-30T12:00:00Z","known",1,1,0,0,"absent","missing",False,"unknown",None)
        self.assertNotIn("plan_id", projection.to_dict())

    def test_forbidden_and_oversized_evidence_refuses(self):
        with self.assertRaises(ValueError): bounded({"stdout":"private"})
        with self.assertRaises(ValueError): bounded({"safe":"x"*100}, 10)
        self.assertEqual(len(canonical_digest({"a":1})),64)

    def test_remote_state_validates_bytes_and_derives_digest(self):
        state = RemoteSwapState.from_dict(status_state(), require_digest=True)
        self.assertEqual(len(state.observation_digest), 64)
        malformed = eligible_state(memory={"total_bytes": 1, "available_bytes": 2})
        with self.assertRaises(ValueError):
            RemoteSwapState.from_dict(malformed)

    def test_plan_operation_and_receipt_are_immutable_and_versioned(self):
        plan_payload = {
            "schema_version": 1, "plan_id": "a" * 64, "operation": "enable",
            "target": {"target_identity": TARGET}, "created_at": "2026-08-30T12:00:00Z",
            "expires_at": "2026-08-30T12:15:00Z", "observation": eligible_state(),
            "observation_digest": "b" * 64, "requested_policy": {"size_gib": 4},
            "effective_policy": SwapPolicy().to_dict(), "calculations": (),
            "intended_changes": ("swap_file",), "rollback_scope": ("swap_file",),
            "requires_confirmation": True, "state": "planned",
        }
        plan = SwapLifecyclePlan(**plan_payload)
        self.assertEqual(plan.to_dict()["state"], "planned")
        operation = ProtectedSwapOperation(
            operation_id="c" * 64, plan_id=plan.plan_id, phase="accepted",
            prior_state_digest="d" * 64, last_observation_digest="e" * 64,
            phase_evidence=(), mutation_started=False, rollback=None, outcome=None,
            unrelated_mutation_blocked=True,
        )
        self.assertEqual(operation.to_dict()["schema_version"], 1)
        receipt = OwnershipReceipt.from_dict(ownership_receipt())
        self.assertEqual(receipt.lifecycle_state, "enabled")

    def test_monitor_history_and_sample_reject_unknown_fields(self):
        health = MonitorHealth(
            service_state="active", timer_state="active", interval_seconds=300,
            latest_sample_at="2026-08-30T12:00:00Z", age_seconds=0,
            freshness="fresh", next_sample_at="2026-08-30T12:05:00Z",
            sustained_swap_use=False, pressure_state="normal",
            retention={"current_files": 1, "history_files": 0,
                       "total_bytes": 100, "compliant": True, "truncated": False},
        )
        window = HistoryWindow(
            requested_range={"since": None, "until": None},
            observed_range={"since": "2026-08-30T12:00:00Z", "until": "2026-08-30T12:00:00Z"},
            samples=(sample(),),
            counts={"returned": 1, "valid": 1, "partial": 0, "failed": 0,
                    "malformed": 0, "missing": 0}, freshness="fresh",
            complete=True, truncated=False,
        )
        self.assertEqual(window.to_dict()["counts"]["valid"], 1)
        self.assertEqual(health.to_dict()["freshness"], "fresh")
        bad = sample(); bad["source_path"] = "/proc/meminfo"
        with self.assertRaises((TypeError, ValueError)):
            AggregateMemorySample.from_dict(bad)
