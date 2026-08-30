from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from sandbox.resources.host_memory.policy import build_plan
from sandbox.resources.host_memory.repository import HostMemoryRepository, RepositoryError
from tests.host_memory_fixtures import NOW, eligible_state, sample


class HostMemoryRepositoryTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.repo=HostMemoryRepository(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()
    def test_plan_is_owner_only_atomic_and_immutable(self):
        plan=build_plan("enable",{},eligible_state(),now=NOW); self.repo.save_plan(plan)
        self.assertEqual(self.repo.load_plan(plan["plan_id"]),plan)
        self.assertEqual((self.repo.plans/f"{plan['plan_id']}.json").stat().st_mode & 0o777,0o600)
    def test_corrupt_plan_fails_closed(self):
        with self.assertRaises(RepositoryError): self.repo.load_plan("a"*64)
    def test_history_is_bounded_and_malformed_is_visible(self):
        self.repo.append_sample(sample()); self.repo.append_sample(sample("2026-08-30T11:55:00Z"))
        with (Path(self.tmp.name)/"history.2.jsonl").open("w") as stream: stream.write("not-json\n")
        result=self.repo.history_window(limit=1)
        self.assertEqual(result["counts"]["returned"],1); self.assertEqual(result["counts"]["malformed"],1); self.assertFalse(result["complete"])
    def test_operation_replay_evidence(self):
        operation={"schema_version":1,"operation_id":"a"*64,"phase":"accepted","evidence":[]}
        self.repo.save_operation(operation); self.assertEqual(self.repo.load_operation(),operation)
