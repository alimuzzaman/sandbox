from __future__ import annotations
import tempfile, unittest
from pathlib import Path
import os
from unittest import mock
from sandbox.resources.host_memory.policy import build_plan
from sandbox.resources.host_memory.repository import HostMemoryRepository, RepositoryError
from tests.host_memory_fixtures import NOW, eligible_state, ownership_receipt, sample, service_evidence


class HostMemoryRepositoryTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.repo=HostMemoryRepository(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()
    def test_plan_is_owner_only_atomic_and_immutable(self):
        plan=build_plan("enable",service_evidence(),eligible_state(),now=NOW); self.repo.save_plan(plan)
        self.assertEqual(self.repo.load_plan(plan["plan_id"]),plan)
        self.assertEqual((self.repo.plans/f"{plan['plan_id']}.json").stat().st_mode & 0o777,0o600)
    def test_corrupt_plan_fails_closed(self):
        with self.assertRaises(RepositoryError): self.repo.load_plan("a"*64)
        for limit in (True, "1", 1.5):
            with self.assertRaises(RepositoryError): self.repo.history_window(limit=limit)
    def test_history_is_bounded_and_malformed_is_visible(self):
        self.repo.append_sample(sample()); self.repo.append_sample(sample("2026-08-30T11:55:00Z"))
        malformed=Path(str(self.repo.history_path)+".2")
        with malformed.open("w") as stream: stream.write("not-json\n")
        malformed.chmod(0o600)
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
        files = [path for path in self.repo._history_paths() if path.exists()]
        self.assertLessEqual(len(files), 9)
        self.assertLessEqual(sum(path.stat().st_size for path in files), 700)

    def test_status_monitor_evidence_uses_real_history(self):
        for minute in (40, 45, 50, 55):
            self.repo.append_sample(sample(
                f"2026-08-30T11:{minute}:00Z", 512 * 1024 ** 2,
            ))
        monitor = self.repo.status_monitor_evidence(now=NOW)
        self.assertEqual(monitor["latest_sample_at"], "2026-08-30T11:55:00Z")
        self.assertEqual(monitor["next_sample_at"], "2026-08-30T12:00:00Z")
        self.assertEqual(monitor["freshness"], "fresh")
        self.assertTrue(monitor["sustained_swap_use"])
        self.assertEqual(monitor["retention"]["current_files"], 1)
        self.assertFalse(monitor["retention"]["truncated"])
        self.assertTrue(monitor["history_complete"])

    def test_fixed_history_path_is_bounded_attested_and_budgeted(self):
        history=Path(self.tmp.name)/"var/log/sandbox/host-memory.jsonl"
        repo=HostMemoryRepository(Path(self.tmp.name)/"state",history_path=history)
        for minute in (45,50,55):
            repo.append_sample(sample(f"2026-08-30T11:{minute}:00Z"))
        self.assertTrue(history.exists())
        self.assertFalse((repo.root/"history.jsonl").exists())
        self.assertFalse(repo.history_window(limit=3)["truncated"])
        history.chmod(0o644)
        with self.assertRaises(RepositoryError):
            repo.history_window(limit=3)
        with self.assertRaises(RepositoryError):
            repo.history_window(limit=3,budget_seconds=0)

    def test_absent_fixed_history_does_not_require_shared_ancestor_ownership(self):
        shared = Path(self.tmp.name) / "shared"
        shared.mkdir(mode=0o777)
        history = shared / "sandbox" / "host-memory.jsonl"
        repo = HostMemoryRepository(
            Path(self.tmp.name) / "state",
            history_path=history,
            history_ancestor_root=history.parent,
        )
        monitor = repo.status_monitor_evidence(now=NOW)
        self.assertIsNone(monitor["latest_sample_at"])
        self.assertEqual(monitor["retention"]["current_files"], 0)
        self.assertTrue(monitor["history_complete"])

    def test_history_open_rejects_replacement_with_symlink_or_oversized_file(self):
        history=Path(self.tmp.name)/"safe/history.jsonl"
        repo=HostMemoryRepository(Path(self.tmp.name)/"state",history_path=history,
                                  history_ancestor_root=Path(self.tmp.name))
        repo.append_sample(sample())
        history.parent.chmod(0o777)
        with self.assertRaises(RepositoryError): repo.history_window(limit=3)
        history.parent.chmod(0o700)
        replacement=Path(self.tmp.name)/"replacement.jsonl"
        replacement.write_text("{}\n"); replacement.chmod(0o600)
        original_open=os.open

        def race(replacement_kind):
            replaced=False
            def open_with_replacement(path,flags,*args,**kwargs):
                nonlocal replaced
                if not replaced and path==history.name and not flags & getattr(os,"O_DIRECTORY",0):
                    replaced=True
                    history.unlink()
                    if replacement_kind=="symlink": history.symlink_to(replacement)
                    else:
                        history.write_bytes(b"x"*(32*1024*1024+1)); history.chmod(0o600)
                return original_open(path,flags,*args,**kwargs)
            with mock.patch("sandbox.resources.host_memory.repository.os.open",
                            side_effect=open_with_replacement):
                with self.assertRaises(RepositoryError): repo.history_window(limit=3)

        race("symlink")
        if history.is_symlink(): history.unlink()
        repo.append_sample(sample())
        race("oversized")

    def test_history_disappearance_after_stat_is_not_clean_missing(self):
        history=Path(self.tmp.name)/"safe/history.jsonl"
        repo=HostMemoryRepository(Path(self.tmp.name)/"state",history_path=history,
                                  history_ancestor_root=Path(self.tmp.name))
        original_open=os.open

        def run_race(kind):
            history.parent.mkdir(mode=0o700,exist_ok=True)
            history.write_text("{}"); history.chmod(0o600)
            moved=Path(self.tmp.name)/"moved"
            if moved.exists(): moved.rename(Path(self.tmp.name)/"old-moved")
            fired=False
            def racing_open(path,flags,*args,**kwargs):
                nonlocal fired
                if not fired and ((kind=="leaf" and path==history.name)
                                  or (kind=="ancestor" and path==history.parent.name)):
                    fired=True
                    if kind=="leaf": history.unlink()
                    else: history.parent.rename(moved)
                return original_open(path,flags,*args,**kwargs)
            with mock.patch("sandbox.resources.host_memory.repository.os.open",
                            side_effect=racing_open):
                with self.assertRaises(RepositoryError): repo.history_window(limit=3)
            if moved.exists(): moved.rename(history.parent)

        run_race("leaf")
        run_race("ancestor")

    def test_rejected_unsafe_ancestor_calls_do_not_leak_descriptors(self):
        history=Path(self.tmp.name)/"unsafe/history.jsonl"
        repo=HostMemoryRepository(Path(self.tmp.name)/"state",history_path=history,
                                  history_ancestor_root=Path(self.tmp.name))
        history.parent.mkdir(mode=0o700); history.write_text("{}\n"); history.chmod(0o600)
        history.parent.chmod(0o777)
        before=len(os.listdir("/dev/fd"))
        for _index in range(40):
            with self.assertRaises(RepositoryError): repo.history_window(limit=3)
        after=len(os.listdir("/dev/fd"))
        self.assertLessEqual(after,before+1)
