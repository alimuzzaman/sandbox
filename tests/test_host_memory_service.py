from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from sandbox.resources.host_memory.repository import HostMemoryRepository
from sandbox.resources.host_memory.service import HostMemoryService
from tests.host_memory_fixtures import MARKER, NOW, REVISION, eligible_state

class FakeRemote:
    name="r"; marker=MARKER; revision=REVISION; record={"identity":"host"}
    def __init__(self): self.calls=[]; self.apply_status="applied"
    def call(self,action,**fields):
        self.calls.append((action,fields))
        if action=="host_memory_status": return {**eligible_state(),"target_identity":"host"}
        if action=="host_memory_history": return {"samples":[],"counts":{"returned":0},"complete":True,"truncated":False}
        return {"status":self.apply_status,"data":{"operation_id":fields["operation_id"]},"error":None}

class HostMemoryServiceTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.remote=FakeRemote(); self.service=HostMemoryService(self.remote,HostMemoryRepository(Path(self.tmp.name)),now=lambda:NOW)
    def tearDown(self): self.tmp.cleanup()
    def test_status_plan_apply_and_replay_identity(self):
        status=self.service.status(); self.assertTrue(status["ok"])
        plan=self.service.plan("enable",size_gib=1); self.assertEqual(plan["status"],"planned")
        refused=self.service.apply(plan["data"]["plan_id"]); self.assertEqual(refused["error"]["code"],"confirmation_required")
        first=self.service.apply(plan["data"]["plan_id"],confirm=True); second=self.service.apply(plan["data"]["plan_id"],confirm=True)
        self.assertEqual(first["status"],"applied"); self.assertEqual(self.remote.calls[-1][1]["operation_id"],self.remote.calls[-2][1]["operation_id"])
    def test_planning_never_sends_remote_plan_action(self):
        self.service.plan("enable"); self.assertEqual([c[0] for c in self.remote.calls],["host_memory_status"])
    def test_history_is_bounded_action(self): self.assertEqual(self.service.history(limit=1000)["action"],"swap-history")
