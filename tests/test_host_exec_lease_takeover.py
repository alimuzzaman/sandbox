"""A later exec takes over only the target lease of an ended, stopped exec job.

The lease files and their lock are real; the job registry and the /proc
cessation scan are synthetic.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sandbox.application import server_cli_exec_recovery as recovery
from sandbox.hosting import host_exec, target_lease
from sandbox.hosting.target_resolution import ResolvedTarget

ENDED = "1" * 32
NOW = datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def job(job_id=ENDED, *, lifecycle="timed_out", stopped=True, target="project/dev",
        kind="server-cli"):
    ended = lifecycle in recovery.ENDED_LIFECYCLES
    return {"job_id": job_id, "kind": kind, "target_kind": "local", "lifecycle": lifecycle,
            "started_at": _iso(NOW - timedelta(minutes=10)),
            "finished_at": _iso(NOW - timedelta(minutes=1)) if ended else None,
            "result_json": json.dumps({"owned_work_stopped": stopped, "stop_error": None,
                                       "owned_descendants": []}) if ended else None,
            "execution_policy_provenance_json": json.dumps({"server_cli.exec_target": target}),
            "parent_job_id": None,
            "process": {"child_pid": 4242, "supervisor_pid": 4241, "child_pgid": 4242,
                        "supervisor_start_identity": "supervisor-start",
                        "child_start_identity": "child-start"}}


class Registry:
    def __init__(self, *rows):
        self.rows = {row["job_id"]: row for row in rows}

    def list(self, *, kind, limit, cursor_job_id=None):
        return [dict(row) for row in self.rows.values() if row["kind"] == kind][:limit]

    def children(self, job_id):
        return [dict(row) for row in self.rows.values() if row.get("parent_job_id") == job_id]

    def snapshot(self, job_id):
        return dict(self.rows[job_id])

    def close(self):
        pass


class TakeOverEndedExecLeaseTests(unittest.TestCase):
    def setUp(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (home / ".sandbox").mkdir(mode=0o700)
        self.path = Path(target_lease.lease_path(str(home), "project", "dev"))
        descriptor = os.open(str(self.path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        self.ceased = self.enterContext(patch.object(recovery, "_ceased", return_value={}))
        self.taker = recovery.exec_lease_owner("2" * 32)

    def hold(self, holder, since=None):
        since = int((NOW - timedelta(minutes=5)).timestamp()) if since is None else since
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(descriptor, f"{secrets.token_hex(16)} {holder} {since}\n".encode())
        os.close(descriptor)
        return self.path.read_text()

    def take(self, registry, owner=None):
        return recovery.take_over_ended_exec_lease(self.path, owner=owner or self.taker,
                                                   target="project/dev", repository=registry)

    def assert_refused(self, registry, body):
        self.assertIsNone(self.take(registry))
        self.assertEqual(self.path.read_text(), body)

    def test_an_ended_stopped_exec_jobs_lease_is_taken_over(self):
        for lifecycle in sorted(recovery.ENDED_LIFECYCLES):
            with self.subTest(lifecycle=lifecycle):
                self.hold(recovery.exec_lease_owner(ENDED))
                lease, previous = self.take(Registry(job(lifecycle=lifecycle)))
                self.assertEqual(previous, ENDED)
                self.assertEqual(lease["owner"], self.taker)
                token, holder, since = self.path.read_text().split()
                self.assertEqual((token, holder), (lease["token"], self.taker))
                self.assertLessEqual(abs(int(since) - time.time()), 5)
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(Path(str(self.path) + ".new").exists())

    def test_a_running_jobs_lease_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED))
        for lifecycle in ("running", "cancelling", "queued"):
            with self.subTest(lifecycle=lifecycle):
                self.assert_refused(Registry(job(lifecycle=lifecycle)), body)
        self.ceased.assert_not_called()

    def test_an_ended_job_without_owned_work_stopped_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED))
        for stopped in (False, None):
            with self.subTest(owned_work_stopped=stopped):
                self.assert_refused(Registry(job(stopped=stopped)), body)
        without = job()
        without["result_json"] = None
        self.assert_refused(Registry(without), body)

    def test_an_ended_job_with_a_live_process_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED))
        self.ceased.side_effect = ValueError("original_exec_recovery_unavailable")
        self.assert_refused(Registry(job()), body)

    def test_an_apply_or_controller_lease_is_refused(self):
        controller = secrets.token_hex(16)
        body = self.hold(controller)
        # Even with ended exec jobs on record, a holder that is no exec job's lease stays.
        self.assert_refused(Registry(job(), job("3" * 32)), body)
        self.ceased.assert_not_called()

    def test_an_unknown_exec_job_hash_is_refused(self):
        body = self.hold(recovery.exec_lease_owner("9" * 32))
        self.assert_refused(Registry(job()), body)

    def test_a_lease_of_another_target_or_kind_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED))
        self.assert_refused(Registry(job(target="project/prod")), body)
        other = job()
        other["target_kind"] = "remote"
        self.assert_refused(Registry(other), body)

    def test_a_nested_or_parent_job_or_one_without_start_identities_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED))
        nested = job()
        nested["parent_job_id"] = "8" * 32
        self.assert_refused(Registry(nested), body)
        child = job("7" * 32, lifecycle="running")
        child["parent_job_id"] = ENDED
        self.assert_refused(Registry(job(), child), body)
        for key in ("supervisor_start_identity", "child_start_identity"):
            with self.subTest(missing=key):
                row = job()
                row["process"] = {**row["process"], key: None}
                self.assert_refused(Registry(row), body)
        self.ceased.assert_not_called()

    def test_a_lease_taken_outside_the_jobs_run_is_refused(self):
        body = self.hold(recovery.exec_lease_owner(ENDED), since=int(NOW.timestamp()) + 600)
        self.assert_refused(Registry(job()), body)

    def test_two_concurrent_takers_have_exactly_one_winner(self):
        self.hold(recovery.exec_lease_owner(ENDED))
        registry = Registry(job())
        def slow_cessation(*args):
            time.sleep(0.3)  # both takers reach the lock while one holds it
            return {}
        self.ceased.side_effect = slow_cessation
        owners = [recovery.exec_lease_owner(str(n) * 32) for n in (4, 5)]
        start = threading.Barrier(2)
        results = {}
        def take(owner):
            start.wait()
            results[owner] = self.take(registry, owner)
        threads = [threading.Thread(target=take, args=(owner,)) for owner in owners]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        winners = [owner for owner, result in results.items() if result is not None]
        self.assertEqual(len(results), 2)
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.path.read_text().split()[1], winners[0])
        self.assertEqual(results[winners[0]][1], ENDED)


class LeaseTakeoverAuditTests(unittest.TestCase):
    def test_the_takeover_line_names_both_jobs(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "audit.jsonl"
        target = ResolvedTarget(remote="box", project="project", environment="dev",
                                kind="stack", project_dir="/unused")
        request = host_exec.ExecRequest(target, "web", ("true",))
        with host_exec.AuditLog(path) as audit:
            audit.write_lease_takeover(request, previous_job_id=ENDED, job_id="2" * 32)
        record = json.loads(path.read_text())
        self.assertEqual(record["event"], "lease_takeover")
        self.assertEqual((record["previous_job_id"], record["job_id"]), (ENDED, "2" * 32))
        self.assertEqual((record["remote"], record["target"], record["service"]),
                         ("box", "project/dev", "web"))


if __name__ == "__main__":
    unittest.main()
