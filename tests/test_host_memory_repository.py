from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from sandbox.resources.host_memory.policy import build_plan
from sandbox.resources.host_memory.repository import HostMemoryRepository, RepositoryError
from tests.host_memory_fixtures import NOW, eligible_state, ownership_receipt, sample


class HostMemoryRepositoryTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.repo=HostMemoryRepository(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()
    def test_plan_is_owner_only_atomic_and_immutable(self):
        plan=build_plan("enable",{},eligible_state(),now=NOW); self.repo.save_plan(plan)
        self.assertEqual(self.repo.load_plan(plan["plan_id"]),plan)
        self.assertEqual((self.repo.plans/f"{plan['plan_id']}.json").stat().st_mode & 0o777,0o600)
    def test_corrupt_plan_fails_closed(self):
        with self.assertRaises(RepositoryError): self.repo.load_plan("a"*64)
        for limit in (True, "1", 1.5):
            with self.assertRaises(RepositoryError): self.repo.history_window(limit=limit)
    def test_history_is_bounded_and_malformed_is_visible(self):
        self.repo.append_sample(sample()); self.repo.append_sample(sample("2026-08-30T11:55:00Z"))
        with (Path(self.tmp.name)/"history.2.jsonl").open("w") as stream: stream.write("not-json\n")
        result=self.repo.history_window(limit=1)
        self.assertEqual(result["counts"]["returned"],1); self.assertEqual(result["counts"]["malformed"],1); self.assertFalse(result["complete"])
    def test_operation_replay_evidence(self):
        operation={"schema_version":1,"operation_id":"a"*64,"phase":"accepted","evidence":[]}
        self.repo.save_operation(operation); self.assertEqual(self.repo.load_operation(),operation)

    def test_receipt_schema_identity_and_corruption_fail_closed(self):
        receipt = ownership_receipt()
        self.repo.save_receipt(receipt)
        self.assertEqual(self.repo.load_receipt()["target_identity"], receipt["target_identity"])
        receipt_path = Path(self.tmp.name) / "receipt.json"
        receipt_path.write_text('{"schema_version":2}')
        with self.assertRaises(RepositoryError):
            self.repo.load_receipt()

    def test_operation_identity_is_immutable(self):
        first={"schema_version":1,"operation_id":"a"*64,"plan_id":"b"*64,
               "phase":"accepted","phase_evidence":[]}
        self.repo.save_operation(first)
        changed={**first,"operation_id":"c"*64}
        with self.assertRaises(RepositoryError):
            self.repo.save_operation(changed)

    def test_history_rotation_keeps_current_plus_eight_and_total_bound(self):
        for index in range(12):
            self.repo.append_sample(sample(f"2026-08-30T{index:02d}:00:00Z"), maximum_bytes=700)
        files = list(Path(self.tmp.name).glob("history*.jsonl"))
        self.assertLessEqual(len(files), 9)
        self.assertLessEqual(sum(path.stat().st_size for path in files), 700)

    def test_status_monitor_evidence_uses_real_history(self):
        for minute in (45, 50, 55):
            self.repo.append_sample(sample(
                f"2026-08-30T11:{minute}:00Z", 512 * 1024 ** 2,
            ))
        monitor = self.repo.status_monitor_evidence(now=NOW)
        self.assertEqual(monitor["latest_sample_at"], "2026-08-30T11:55:00Z")
        self.assertEqual(monitor["next_sample_at"], "2026-08-30T12:00:00Z")
        self.assertEqual(monitor["freshness"], "fresh")
        self.assertTrue(monitor["sustained_swap_use"])
        self.assertEqual(monitor["retention"]["current_files"], 1)
