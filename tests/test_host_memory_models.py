from __future__ import annotations
import unittest
from datetime import datetime, timezone
from sandbox.resources.host_memory.models import (
    AggregateMemorySample, HostMemoryStatusProjection, RemoteServiceEvidence,
    SwapPolicy, bounded, canonical_digest,
)
from tests.host_memory_assertions import assert_privacy_bounded
from tests.host_memory_fixtures import MARKER, REVISION, TARGET, sample


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
