"""A later exec takes over only the target lease of an ended, stopped exec job.

The lease files and their lock are real. Most tests use a synthetic registry;
``SupervisorWrittenRowTests`` uses rows the real supervisor wrote. Without
Linux /proc the cessation scan is patched.
"""
import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sandbox.application import server_cli, server_cli_exec_recovery as recovery
from sandbox.hosting import host_exec, target_lease
from sandbox.hosting.target_resolution import ResolvedTarget
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler
from sandbox.jobs.storage import JobStorage
from tests.subprocess_support import synthetic_environment

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

    def test_a_pre_placed_new_file_is_replaced_not_written_through(self):
        outside = self.path.parent / "outside"
        outside.write_text("keep\n")
        for link in (os.link, os.symlink):
            with self.subTest(link=link.__name__):
                self.hold(recovery.exec_lease_owner(ENDED))
                temporary = Path(str(self.path) + ".new")
                temporary.unlink(missing_ok=True)
                link(outside, temporary)
                lease, _ = self.take(Registry(job()))
                self.assertEqual(outside.read_text(), "keep\n")
                self.assertEqual(self.path.read_text().split()[:2], [lease["token"], self.taker])
                self.assertFalse(temporary.exists() or temporary.is_symlink())

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


class SupervisorWrittenRowTests(unittest.TestCase):
    """Rows the real supervisor wrote, not hand-built ones.

    Without Linux /proc the cessation scan is patched; on Linux it runs.
    """

    def setUp(self):
        self.runtime = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source = self.runtime / "source"
        source.mkdir(mode=0o700)
        (self.runtime / ".sandbox").mkdir(mode=0o700)
        self.storage = JobStorage(self.runtime, free_disk_reserve=0)
        self.repository = JobRepository(self.storage.root / "registry.sqlite3")
        self.addCleanup(self.repository.close)
        self.owner = server_cli.ServerCliOwner(os.geteuid(), str(self.runtime), str(self.runtime.parent),
            str(self.runtime / "sb"), "c" * 24, "a" * 24, "b" * 24)
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        self.proof = {"server_cli.target_identity": self.owner.target_identity,
            "server_cli.ownership_marker": self.owner.ownership_marker,
            "server_cli.runtime_revision": self.owner.runtime_revision,
            "server_cli.remote_name": "box", "server_cli.exec_service": "wordpress",
            "server_cli.exec_target": "project/dev", "server_cli.target_metadata": '{"front_door":"nginx"}',
            "server_cli.target_selector_digest": "sha256:" + "d" * 64,
            "server_cli.output_contract": "artifact.v1", "server_cli.absolute_deadline_at": deadline,
            "server_cli.target_snapshot": json.dumps({"schema_version": 1, "origin_remote_name": "box",
                "target_identity": self.owner.target_identity, "ownership_marker": self.owner.ownership_marker,
                "runtime_revision": self.owner.runtime_revision, "name": "project", "copy_id": "1234abcd",
                "source_dir": str(source), "compose_files": [str(source / "compose.yml")],
                "native_keys": ["box/project/dev"], "selector_digest": "sha256:" + "d" * 64,
                "source_commit": "1" * 40, "source_origin_commit": "2" * 40,
                "project": "project", "environment": "dev",
                "services": [{"name": "wordpress", "index": 1, "container_id": "f" * 64}]})}
        self.source = source
        self.path = Path(target_lease.lease_path(str(self.runtime), "project", "dev"))
        os.close(os.open(str(self.path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600))
        if not Path("/proc/sys/kernel/random/boot_id").exists():
            self.enterContext(patch.object(recovery, "_ceased", return_value={}))

    def accept(self):
        submission = JobSubmission("server-cli", str(self.source), "e" * 64, "local", "server-exec",
            (self.owner.executable, "host", "exec", "--remote", "box"), 60,
            SourceIdentity("sha256:" + "e" * 64, "1" * 40), request_id="exec-" + secrets.token_hex(4),
            execution_policy_provenance=self.proof)
        row, _ = self.repository.accept(submission)
        JobScheduler(self.repository, min_free_disk_mb=0, min_free_memory_mb=0).acquire(row)
        return row

    def exec_job(self, script, deadline=30):
        """An exec job the real supervisor ran and ended; ``exit 255`` is what
        ``sb host exec`` exits with after its command's own ``--timeout``."""
        argv = ("/bin/sh", "-c", script)
        row = self.accept()
        descriptor = self.storage.job_dir(row["job_id"], create=True) / "descriptor.json"
        descriptor.write_text(json.dumps({
            "job_id": row["job_id"], "registry_path": str(self.repository.path),
            "runtime_dir": str(self.runtime), "argv": list(argv), "cwd": str(self.source),
            "deadline_seconds": deadline, "cancel_grace_seconds": 1, "nonce_hash": "0" * 64,
            "environment": dict(synthetic_environment()), "execution_runtime": "host",
            "authoritative_context": {"job_id": row["job_id"]}}))
        descriptor.chmod(0o600)
        # Its own process, as job_service launches it, so its PID is gone afterwards.
        subprocess.run([sys.executable, "-B", "-m", "sandbox.jobs.supervisor", str(descriptor)],
                       cwd=Path(__file__).resolve().parents[1], check=False, timeout=60,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.repository.snapshot(row["job_id"])

    def hold(self, row):
        since = int(datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")).timestamp())
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(descriptor, f"{secrets.token_hex(16)} {recovery.exec_lease_owner(row['job_id'])} {since}\n".encode())
        os.close(descriptor)
        return self.path.read_text()

    def take(self):
        return recovery.take_over_ended_exec_lease(self.path, owner=recovery.exec_lease_owner("2" * 32),
                                                   target="project/dev", repository=self.repository)

    def test_the_lease_of_an_ended_exec_is_taken_over(self):
        for script, deadline, lifecycle in (("exit 255", 30, "failed"), ("exit 0", 30, "succeeded"),
                                            ("sleep 30", 1, "timed_out")):
            with self.subTest(lifecycle=lifecycle):
                row = self.exec_job(script, deadline)
                self.assertEqual(row["lifecycle"], lifecycle)
                self.hold(row)
                lease, previous = self.take()
                self.assertEqual(previous, row["job_id"])
                self.assertEqual(lease["owner"], recovery.exec_lease_owner("2" * 32))
                token, holder, since = self.path.read_text().split()
                self.assertEqual((token, holder), (lease["token"], lease["owner"]))
                self.assertLessEqual(abs(int(since) - time.time()), 5)
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(Path(str(self.path) + ".new").exists())

    def test_job_reconcile_accepts_an_exec_that_failed_by_itself(self):
        row = self.exec_job("exit 255")
        bound, _, stopped, _, _ = recovery._binding(self.repository, row["job_id"], self.owner)
        self.assertEqual(bound["lifecycle"], "failed")
        self.assertIs(stopped["owned_work_stopped"], True)

    def test_an_exec_that_left_a_process_running_is_refused(self):
        pid_path = self.runtime / "leftover.pid"
        row = self.exec_job(
            f"perl -e 'setpgrp(0,0); open F, \">\", shift; print F $$; close F; sleep 30'"
            f" {pid_path} </dev/null >/dev/null 2>&1 & sleep .3; exit 255")
        for _ in range(100):
            if pid_path.exists() and pid_path.read_text().strip():
                break
            time.sleep(.03)
        pid = int(pid_path.read_text())

        def kill():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)
        self.addCleanup(kill)
        self.assertEqual(row["lifecycle"], "failed")
        self.assertIs(json.loads(row["result_json"])["owned_work_stopped"], False)
        body = self.hold(row)
        self.assertIsNone(self.take())
        self.assertEqual(self.path.read_text(), body)
        with self.assertRaises(ValueError):
            recovery._binding(self.repository, row["job_id"], self.owner)

    def test_an_interrupted_exec_is_refused(self):
        job_id = self.accept()["job_id"]
        self.repository.transition(job_id, "running")
        self.repository.put_process_identity(job_id, host_boot_id="boot", supervisor_pid=4241,
            supervisor_start_identity="supervisor-start", supervisor_nonce_hash="0" * 64,
            child_pid=4242, child_pgid=4242, child_start_identity="child-start")
        # The transition status reconciliation makes (job_service.py) when the supervisor is lost.
        self.repository.transition(job_id, "interrupted", termination_reason="supervisor_missing",
            output_completeness="partial", result_json=json.dumps(
                {"reconciled": True, "evidence": ["supervisor_missing"]}, sort_keys=True))
        body = self.hold(self.repository.snapshot(job_id))
        self.assertIsNone(self.take())
        self.assertEqual(self.path.read_text(), body)


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
