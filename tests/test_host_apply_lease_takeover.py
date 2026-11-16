"""A server-owner apply records its target lease; a later one takes over a dead job's.

The lease files and their lock are real; the registry is synthetic. Without Linux
/proc the cessation scan is patched.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sandbox.application import server_cli_apply_lease as leases
from sandbox.application import server_cli_exec_recovery as recovery
from sandbox.hosting import target_lease

DEAD, TAKER = "1" * 32, "2" * 32
NOW = datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def job(job_id=DEAD, *, lifecycle="failed", stopped=True, parent=None, kind="server-cli"):
    ended = lifecycle in recovery.ENDED_LIFECYCLES
    return {"job_id": job_id, "kind": kind, "target_kind": "local", "lifecycle": lifecycle,
            "started_at": _iso(NOW - timedelta(minutes=10)),
            "finished_at": _iso(NOW - timedelta(minutes=1)) if ended else None,
            "result_json": json.dumps({"owned_work_stopped": stopped, "stop_error": None,
                                       "owned_descendants": []}) if ended else None,
            "parent_job_id": parent,
            "process": {"child_pid": 4242, "supervisor_pid": 4241, "child_pgid": 4242,
                        "supervisor_start_identity": "supervisor-start",
                        "child_start_identity": "child-start"}}


class _Connection:
    def __init__(self, registry):
        self.registry = registry

    def execute(self, sql, params):
        job_id, kind = params
        rows = [(json.dumps(payload),) for (owner, event, payload) in self.registry.events
                if owner == job_id and event == kind]
        return type("Rows", (), {"fetchall": lambda self: rows})()


class Registry:
    def __init__(self, *rows):
        self.rows = {row["job_id"]: row for row in rows}
        self.events = []
        self.connection = _Connection(self)

    def list(self, *, kind, limit, cursor_job_id=None):
        return [dict(row) for row in self.rows.values() if row["kind"] == kind][:limit]

    def children(self, job_id):
        return [dict(row) for row in self.rows.values() if row.get("parent_job_id") == job_id]

    def snapshot(self, job_id):
        return dict(self.rows[job_id])

    def append_event(self, job_id, kind, payload):
        self.events.append((job_id, kind, payload))
        return len(self.events)

    def close(self):
        pass


class TakeOverEndedApplyLeaseTests(unittest.TestCase):
    def setUp(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (home / ".sandbox").mkdir(mode=0o700)
        self.path = Path(target_lease.lease_path(str(home), "project", "dev"))
        descriptor = os.open(str(self.path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        self.ceased = self.enterContext(patch.object(recovery, "_ceased", return_value={}))
        self.taker = leases.apply_lease_owner(TAKER)

    def hold(self, holder=None, *, since=None, token=None):
        holder = holder or leases.apply_lease_owner(DEAD)
        token = token or secrets.token_hex(16)
        since = int((NOW - timedelta(minutes=5)).timestamp()) if since is None else since
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(descriptor, f"{token} {holder} {since}\n".encode())
        os.close(descriptor)
        return token, self.path.read_text()

    def recorded(self, registry, token, *, job_id=DEAD, target="project/dev", path=None):
        registry.events.append((job_id, leases.ACQUIRED, {
            "holder": leases.apply_lease_owner(job_id), "token": token,
            "path": str(path or self.path), "target": target}))

    def take(self, registry):
        return leases.take_over_ended_apply_lease(self.path, owner=self.taker, target="project/dev",
                                                  job_id=TAKER, repository=registry)

    def assert_refused(self, registry, body):
        self.assertIsNone(self.take(registry))
        self.assertEqual(self.path.read_text(), body)

    def test_the_recorded_lease_of_an_ended_stopped_apply_is_taken_over(self):
        token, _ = self.hold()
        registry = Registry(job())
        self.recorded(registry, token)
        lease, previous = self.take(registry)
        self.assertEqual(previous, DEAD)
        self.assertEqual(self.path.read_text().split()[:2], [lease["token"], self.taker])
        self.assertEqual(registry.events[-1][:2], (DEAD, leases.TAKEN_OVER))
        self.assertEqual(registry.events[-1][2]["by_job_id"], TAKER)

    def test_a_withheld_apply_that_ended_stopped_no_longer_holds_the_target(self):
        # An effect-unknown apply skips its own release; its recorded lease is taken.
        token, _ = self.hold()
        registry = Registry(job(lifecycle="failed"))
        self.recorded(registry, token)
        self.assertEqual(self.take(registry)[1], DEAD)

    def test_an_unrecorded_lease_is_refused(self):
        # The ocp-trial case: the holder hash matched only by timing.
        _, body = self.hold()
        self.assert_refused(Registry(job()), body)

    def test_a_record_for_another_token_path_or_target_is_refused(self):
        token, body = self.hold()
        for change in ({"token": secrets.token_hex(16)}, {"path": "/elsewhere.lease"},
                       {"target": "project/prod"}):
            with self.subTest(change=change):
                registry = Registry(job())
                self.recorded(registry, change.get("token", token), path=change.get("path"),
                              target=change.get("target", "project/dev"))
                self.assert_refused(registry, body)

    def test_a_lease_whose_token_was_released_is_refused(self):
        token, body = self.hold()
        registry = Registry(job())
        self.recorded(registry, token)
        registry.events.append((DEAD, leases.RELEASED, {"token": token}))
        self.assert_refused(registry, body)

    def test_a_running_interrupted_or_unstopped_job_is_refused(self):
        token, body = self.hold()
        for row in (job(lifecycle="running"), job(lifecycle="interrupted"), job(stopped=False),
                    job(stopped=None)):
            with self.subTest(lifecycle=row["lifecycle"], result=row["result_json"]):
                registry = Registry(row)
                self.recorded(registry, token)
                self.assert_refused(registry, body)

    def test_a_live_process_or_unfinished_child_is_refused(self):
        token, body = self.hold()
        registry = Registry(job())
        self.recorded(registry, token)
        self.ceased.side_effect = ValueError("original_exec_recovery_unavailable")
        self.assert_refused(registry, body)
        self.ceased.side_effect = None
        registry = Registry(job(), job("3" * 32, lifecycle="running", parent=DEAD))
        self.recorded(registry, token)
        self.assert_refused(registry, body)

    def test_a_nested_apply_whose_parent_still_runs_is_taken_over(self):
        # A stack-up follower keeps running; only the nested apply holds the lease.
        token, _ = self.hold()
        registry = Registry(job(parent="4" * 32), job("4" * 32, lifecycle="running"))
        self.recorded(registry, token)
        self.assertEqual(self.take(registry)[1], DEAD)

    def test_a_lease_taken_outside_the_jobs_run_is_refused(self):
        token, body = self.hold(since=int((NOW + timedelta(minutes=5)).timestamp()))
        registry = Registry(job())
        self.recorded(registry, token)
        self.assert_refused(registry, body)

    def test_an_exec_or_controller_holder_is_never_taken_by_this_rule(self):
        for holder in (recovery.exec_lease_owner(DEAD), secrets.token_hex(16)):
            with self.subTest(holder=holder):
                token, body = self.hold(holder)
                registry = Registry(job())
                self.recorded(registry, token)
                self.assert_refused(registry, body)
        self.ceased.assert_not_called()


class AcquireAndReleaseRecordTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.enterContext(patch.object(leases, "_repository", return_value=self.registry))
        self.entry = type("Entry", (), {"require_current": lambda self: type("C", (), {"job_id": DEAD})()})()

    def run_lease(self, code=0):
        def run(command):
            return subprocess.CompletedProcess(command, code, "", "")
        return run

    def test_acquisition_and_confirmed_release_are_recorded_in_the_job(self):
        lease = leases.acquire_owner_lease(self.entry, "/h/.sandbox/x.lease", "project", "dev", self.run_lease())
        self.assertEqual(lease["owner"], leases.apply_lease_owner(DEAD))
        leases.release_owner_lease(lease, self.run_lease())
        kinds = [(job_id, kind, payload["token"], payload["target"]) for job_id, kind, payload in self.registry.events]
        self.assertEqual(kinds, [(DEAD, leases.ACQUIRED, lease["token"], "project/dev"),
                                 (DEAD, leases.RELEASED, lease["token"], "project/dev")])

    def test_a_failed_release_is_not_recorded(self):
        lease = leases.acquire_owner_lease(self.entry, "/h/.sandbox/x.lease", "project", "dev", self.run_lease())
        leases.release_owner_lease(lease, self.run_lease(code=1))
        self.assertEqual([kind for _, kind, _ in self.registry.events], [leases.ACQUIRED])

    def test_a_held_lease_without_proof_still_refuses(self):
        with patch.object(leases, "take_over_ended_apply_lease", return_value=None):
            with self.assertRaises(target_lease.LeaseHeld):
                leases.acquire_owner_lease(self.entry, "/h/.sandbox/x.lease", "project", "dev",
                                           self.run_lease(code=target_lease.HELD_EXIT))
        self.assertEqual(self.registry.events, [])


if __name__ == "__main__":
    unittest.main()
