from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from sandbox.resources.host_memory.service import HostMemoryService
from tests.host_memory_fixtures import MARKER, NOW, REVISION, status_state
from tests.host_memory_fixtures import sample
from tests.host_memory_assertions import assert_privacy_bounded

class FakeRemote:
    name="r"; marker=MARKER; revision=REVISION; record={"identity":"host"}
    def __init__(self): self.calls=[]; self.apply_status="applied"
    def call(self,action,**fields):
        self.calls.append((action,fields))
        if action=="host_memory_status": return status_state(target_identity="host")
        if action=="host_memory_history": return {"samples":[],"counts":{"returned":0},"complete":True,"truncated":False}
        return {"status":self.apply_status,"data":{"operation_id":fields["operation_id"]},"error":None}

class HostMemoryServiceTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.remote=FakeRemote(); self.service=HostMemoryService(self.remote,now=lambda:NOW)
    def tearDown(self): self.tmp.cleanup()
    def test_status_rejects_known_only_or_sensitive_fake_adapter_results(self):
        for state in ({"evidence_state":"known"},
                      {**status_state(), "source_path":"/proc/meminfo"}):
            self.remote.call=lambda action, **fields: state
            result=self.service.status()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"],"response_invalid")

    def test_status_composes_freshness_warning_and_projection(self):
        state = status_state(target_identity="host", monitor={
            **status_state()["monitor"], "service_state":"active", "timer_state":"active",
            "latest_sample_at":"2026-08-30T11:55:00Z", "age_seconds":300,
            "interval_seconds":300, "freshness":"fresh", "sustained_swap_use":True,
            "pressure_state":"normal", "next_sample_at":"2026-08-30T12:00:00Z"})
        self.remote.call = lambda action, **fields: state
        result = self.service.status()
        self.assertEqual(result["data"]["monitor"]["freshness"], "fresh")
        self.assertTrue(result["data"]["monitor"]["sustained_swap_use"])
        projection = self.service.projection(result["data"])
        self.assertTrue(projection.sustained_swap_use)
        assert_privacy_bounded(self, projection.to_dict(), maximum=64*1024)

    def test_unknown_status_is_partial_and_non_authorizing(self):
        self.remote.call = lambda action, **fields: status_state(evidence_state="unknown",
                                                                 target_identity="host")
        result = self.service.status()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "partial")
