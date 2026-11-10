"""Host-configurable durable-job slots (`jobs.scheduler`) and `sb job-policy`."""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from sandbox.application.job_service import JobService
from sandbox.commands import job_policy
from sandbox.config.job_scheduler import (
    DEFAULT_POLICY, JobSchedulerConfigError, JobSchedulerPolicy, normalize_job_scheduler,
)
from sandbox.config.manifest import apply_machine_config
from sandbox.core import _config
from sandbox.jobs.listing import job_slots
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import (
    JobScheduler, SchedulerPolicyInvalid, WorkspaceBusy, capacity_line, host_scheduler_policy,
)
from sandbox.jobs.storage import JobStorage


def _code(raw):
    try:
        normalize_job_scheduler(raw)
    except JobSchedulerConfigError as exc:
        return exc.code
    raise AssertionError(f"{raw!r} was accepted")


class NormalizationTests(unittest.TestCase):
    def test_absent_block_uses_todays_defaults(self):
        self.assertEqual(normalize_job_scheduler(None), JobSchedulerPolicy(2, 512, 2048))

    def test_partial_block_fills_defaults(self):
        self.assertEqual(normalize_job_scheduler({"max_parallel": 4}),
                         JobSchedulerPolicy(4, 512, 2048))

    def test_refusals_carry_stable_codes(self):
        self.assertEqual(_code([]), "job_policy_invalid")
        self.assertEqual(_code({"max_paralel": 4}), "job_policy_unknown_key")
        for bad in (0, 33, True, "4", 2.0):
            self.assertEqual(_code({"max_parallel": bad}), "job_policy_invalid_max_parallel", bad)
        self.assertEqual(_code({"min_free_memory_mb": 511}), "job_policy_invalid_floor")
        self.assertEqual(_code({"min_free_disk_mb": 2047}), "job_policy_invalid_floor")

    def test_machine_config_is_inert_when_absent_and_normalized_when_present(self):
        self.assertNotIn("jobs", apply_machine_config({"version": "0.1.0"}))
        raw = {"jobs": {"scheduler": {"max_parallel": 4}}}
        resolved = apply_machine_config(raw)
        self.assertEqual(resolved["jobs"]["scheduler"],
                         {"max_parallel": 4, "min_free_memory_mb": 512, "min_free_disk_mb": 2048})
        self.assertEqual(raw, {"jobs": {"scheduler": {"max_parallel": 4}}})
        # An invalid jobs block is left for the scheduler to refuse; it does
        # not refuse unrelated machine consumers such as the storage monitor.
        invalid = apply_machine_config({"jobs": {"scheduler": {"max_parallel": 99}}})
        self.assertEqual(invalid["jobs"], {"scheduler": {"max_parallel": 99}})
        self.assertIn("monitor", invalid["resources"])

    def test_host_policy_reads_the_local_machine_config(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "sandbox.local.yml"
            local.write_text("jobs:\n  scheduler:\n    max_parallel: 5\n")
            with mock.patch.object(_config, "CONFIG_LOCAL", local):
                self.assertEqual(host_scheduler_policy().max_parallel, 5)
                local.write_text("jobs:\n  scheduler:\n    max_parallel: 50\n")
                with self.assertRaises(JobSchedulerConfigError) as raised:
                    host_scheduler_policy()
                self.assertEqual(raised.exception.code, "job_policy_invalid_max_parallel")


class SchedulerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.policy = JobSchedulerPolicy(1, 512, 2048)
        self.scheduler = JobScheduler(self.repo, policy_loader=lambda: self.current())
        # The real floors read this machine; keep admission about slots only.
        self.scheduler._resource_floor_available = lambda: True

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def current(self):
        if isinstance(self.policy, Exception):
            raise self.policy
        return self.policy

    def accept(self, label):
        return self.repo.accept(JobSubmission(
            "test", "/p", "p", "local", label, ("echo", "x"), 60, SourceIdentity("s")))[0]

    def test_a_raised_limit_applies_at_the_next_admission(self):
        first, second = self.accept("a"), self.accept("b")
        self.scheduler.acquire(first)
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(second)
        self.policy = JobSchedulerPolicy(2, 512, 2048)
        self.scheduler.acquire(second)
        self.assertEqual(self.scheduler.capacity()["in_use"], 2)

    def test_a_lowered_limit_counts_slots_still_held(self):
        self.policy = JobSchedulerPolicy(3, 512, 2048)
        jobs = [self.accept(label) for label in ("a", "b", "c", "d")]
        for job in jobs[:3]:
            self.scheduler.acquire(job)
        self.policy = JobSchedulerPolicy(2, 512, 2048)
        self.scheduler.release(jobs[0]["job_id"])  # frees slot 1; two remain held
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(jobs[3])

    def test_an_invalid_host_policy_refuses_admission_with_its_code(self):
        self.policy = JobSchedulerConfigError("bad", "job_policy_invalid_max_parallel")
        with self.assertRaises(SchedulerPolicyInvalid) as raised:
            self.scheduler.acquire(self.accept("a"))
        self.assertEqual(raised.exception.queue_reason, "job_policy_invalid_max_parallel")
        view = self.scheduler.capacity()
        self.assertEqual((view["available"], view["policy_error"]),
                         (0, "job_policy_invalid_max_parallel"))

    def test_a_submission_under_an_invalid_policy_queues_with_the_code(self):
        self.policy = JobSchedulerConfigError("bad", "job_policy_unknown_key")
        service = JobService(self.repo, JobStorage(self.temp.name, free_disk_reserve=0), None,
                             launcher=lambda _path: None, scheduler=self.scheduler)
        row = service.submit(JobSubmission(
            "test", self.temp.name, "p", "local", "a", ("echo", "x"), 60, SourceIdentity("s")))
        stored = self.repo.get(row["job_id"])
        self.assertEqual((stored["lifecycle"], stored["queue_reason"]),
                         ("queued", "job_policy_unknown_key"))

    def test_queue_details_name_no_slot_holders_under_an_invalid_policy(self):
        first, second = self.accept("a"), self.accept("b")
        self.scheduler.acquire(first)
        self.policy = JobSchedulerConfigError("bad", "job_policy_invalid_floor")
        self.assertEqual(self.scheduler.queue_details(second)["blocking_jobs"], [])

    def test_capacity_reports_limit_floors_and_usage(self):
        self.policy = JobSchedulerPolicy(4, 1024, 4096)
        self.scheduler.acquire(self.accept("a"))
        self.accept("b")
        self.assertEqual(self.scheduler.capacity(), {
            "max_parallel": 4, "min_free_memory_mb": 1024, "min_free_disk_mb": 4096,
            "in_use": 1, "available": 3, "queued": 0, "policy_error": None,
        })

    def test_a_scheduler_without_loader_keeps_constructor_values(self):
        scheduler = JobScheduler(self.repo, max_parallel=3)
        self.assertEqual(scheduler.capacity()["max_parallel"], 3)
        self.assertEqual(JobScheduler(self.repo).max_parallel, DEFAULT_POLICY.max_parallel)


class ViewTests(unittest.TestCase):
    def test_job_slots_keeps_only_bounded_known_fields(self):
        view = job_slots({"max_parallel": 4, "in_use": True, "queued": -1,
                          "policy_error": "x" * 99, "extra": "secret"})
        self.assertEqual(view["max_parallel"], 4)
        self.assertIsNone(view["in_use"])
        self.assertIsNone(view["queued"])
        self.assertIsNone(view["policy_error"])
        self.assertNotIn("extra", view)

    def test_capacity_line(self):
        line = capacity_line({"max_parallel": 4, "in_use": 2, "queued": 1,
                              "min_free_memory_mb": 512, "min_free_disk_mb": 2048,
                              "policy_error": "job_policy_invalid_floor"})
        self.assertEqual(line, "job slots: 2/4 in use, 1 queued (floors 512 MB memory, "
                               "2048 MB disk); admission refused: job_policy_invalid_floor")


class SlotViewSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def list_args(self, **values):
        base = {"remote": None, "project_dir": None, "project_identity": None,
                "workspace": None, "active_only": False, "lifecycle": None, "kind": None,
                "cursor_job_id": None, "limit": 50, "json": True}
        base.update(values)
        return argparse.Namespace(**base)

    def run_list(self, dependencies, **values):
        from sandbox.commands import jobs_runtime
        out = io.StringIO()
        with mock.patch.object(jobs_runtime, "durable_job_dependencies",
                               return_value=dependencies), redirect_stdout(out):
            jobs_runtime.cmd_job_list(None, self.list_args(**values))
        return json.loads(out.getvalue())

    def test_local_job_list_reports_slots(self):
        repo = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.addCleanup(repo.close)
        service = mock.Mock(scheduler=JobScheduler(repo, max_parallel=3))
        service.list.return_value = []
        envelope = self.run_list({"job_service": service})
        self.assertEqual((envelope["slots"]["max_parallel"], envelope["slots"]["in_use"]), (3, 0))

    def test_remote_job_list_passes_the_hosts_slots_through(self):
        page = {"ok": True, "jobs": [], "page": {"schema_version": 2, "has_more": False},
                "slots": {"max_parallel": 4, "in_use": 2, "queued": 1}}
        with mock.patch("sandbox.transports.remote_jobs.RemoteJobTransport.list",
                        return_value=page):
            envelope = self.run_list({"job_service": mock.Mock()}, remote="example-remote")
        self.assertEqual((envelope["slots"]["max_parallel"], envelope["slots"]["in_use"]), (4, 2))

    def test_an_older_remote_runtime_reports_no_slots(self):
        page = {"ok": True, "jobs": [], "page": {"schema_version": 2, "has_more": False}}
        with mock.patch("sandbox.transports.remote_jobs.RemoteJobTransport.list",
                        return_value=page):
            envelope = self.run_list({"job_service": mock.Mock()}, remote="example-remote")
        self.assertNotIn("slots", envelope)

    def test_diagnostics_report_the_hosts_slots(self):
        from sandbox.remote_runtime import host_inventory
        home = Path(self.temp.name)
        (home / "runtime" / "jobs").mkdir(parents=True)
        JobRepository(home / "runtime" / "jobs" / "registry.sqlite3").close()
        with mock.patch.dict("os.environ", {"SANDBOX_HOME": str(home)}), \
                mock.patch("sandbox.jobs.scheduler.host_scheduler_policy",
                           return_value=JobSchedulerPolicy(4, 512, 2048)):
            view = host_inventory._job_slots()
        self.assertEqual((view["max_parallel"], view["in_use"], view["available"]), (4, 0, 4))

    def test_diagnostics_without_a_registry_report_nothing(self):
        from sandbox.remote_runtime import host_inventory
        with mock.patch.dict("os.environ", {"SANDBOX_HOME": self.temp.name}), \
                mock.patch("sandbox.jobs.scheduler.host_scheduler_policy",
                           return_value=DEFAULT_POLICY):
            self.assertIsNone(host_inventory._job_slots())


def _args(**values):
    base = {"max_parallel": None, "min_free_memory_mb": None, "min_free_disk_mb": None,
            "confirm": False, "show": False, "remote": None, "json": True}
    base.update(values)
    return argparse.Namespace(**base)


class JobPolicyCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.local = Path(self.temp.name) / "sandbox.local.yml"
        self.local.write_text("remotes:\n  keep: {ssh: user@example.invalid}\n")
        self.repo = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        scheduler = JobScheduler(self.repo, policy_loader=host_scheduler_policy)
        patches = (
            mock.patch.object(_config, "CONFIG_LOCAL", self.local),
            mock.patch("sandbox.application.context.durable_job_dependencies",
                       return_value={"job_service": mock.Mock(scheduler=scheduler)}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def run_command(self, **values):
        out = io.StringIO()
        code = 0
        with redirect_stdout(out):
            try:
                job_policy.cmd_job_policy(None, _args(**values))
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_show_reports_defaults_and_slots(self):
        code, payload = self.run_command()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "observed")
        self.assertEqual(payload["policy"]["max_parallel"], 2)
        self.assertEqual(payload["slots"]["in_use"], 0)

    def test_without_confirm_the_change_is_only_planned(self):
        before = self.local.read_text()
        _code_, payload = self.run_command(max_parallel=4)
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(self.local.read_text(), before)

    def test_confirm_writes_the_full_block_and_keeps_other_config(self):
        _code_, payload = self.run_command(max_parallel=4, confirm=True)
        self.assertEqual((payload["status"], payload["policy"]["max_parallel"]), ("updated", 4))
        written = yaml.safe_load(self.local.read_text())
        self.assertEqual(written["jobs"]["scheduler"],
                         {"max_parallel": 4, "min_free_memory_mb": 512, "min_free_disk_mb": 2048})
        self.assertIn("keep", written["remotes"])
        _code_, again = self.run_command(max_parallel=4, confirm=True)
        self.assertEqual(again["status"], "unchanged")

    def test_a_bad_value_is_refused_before_any_write(self):
        before = self.local.read_text()
        code, payload = self.run_command(max_parallel=64, confirm=True)
        self.assertEqual((code, payload["code"]), (1, "job_policy_invalid_max_parallel"))
        code, payload = self.run_command(min_free_disk_mb=100, confirm=True)
        self.assertEqual(payload["code"], "job_policy_invalid_floor")
        self.assertEqual(self.local.read_text(), before)

    def test_an_invalid_block_can_be_replaced(self):
        self.local.write_text("jobs:\n  scheduler:\n    max_parallel: 0\n")
        code, payload = self.run_command()
        self.assertEqual((code, payload["code"]), (1, "job_policy_invalid_max_parallel"))
        _code_, payload = self.run_command(max_parallel=3, confirm=True)
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(host_scheduler_policy().max_parallel, 3)

    def test_an_invalid_base_block_is_refused_before_any_local_write(self):
        base = Path(self.temp.name) / "sandbox.yml"
        base.write_text(_config.CONFIG.read_text()
                        + "\njobs:\n  scheduler:\n    max_paralel: 4\n")
        before = self.local.read_text()
        with mock.patch.object(_config, "CONFIG", base):
            code, payload = self.run_command(max_parallel=3, confirm=True)
        self.assertEqual((code, payload["code"]), (1, "job_policy_invalid_base_config"))
        self.assertIn("sandbox.yml", payload["error"])
        self.assertEqual(self.local.read_text(), before)

    def test_an_unreadable_config_is_a_structured_refusal(self):
        self.local.write_text("jobs: [unclosed\n")
        before = self.local.read_text()
        code, payload = self.run_command(max_parallel=3, confirm=True)
        self.assertEqual((code, payload["code"]), (1, "job_policy_config_unreadable"))
        self.assertEqual(self.local.read_text(), before)
        with mock.patch.object(_config, "CONFIG", Path(self.temp.name) / "missing.yml"):
            code, payload = self.run_command(max_parallel=3, confirm=True)
        self.assertEqual((code, payload["code"]), (1, "job_policy_config_unreadable"))

    def test_remote_runs_the_same_command_on_the_host(self):
        reply = {"ok": True, "status": "updated", "policy": {"max_parallel": 4},
                 "slots": {"max_parallel": 4, "in_use": 1}}
        with mock.patch("sandbox.transports.remote_jobs.RemoteJobTransport.control",
                        return_value=reply) as control:
            _code_, payload = self.run_command(remote="example-remote", max_parallel=4,
                                               confirm=True)
        control.assert_called_once_with(
            "example-remote", ["job-policy", "--max-parallel", "4", "--confirm"])
        self.assertEqual((payload["remote"], payload["status"]), ("example-remote", "updated"))
        self.assertEqual(payload["policy"], {"max_parallel": 4, "min_free_memory_mb": 512,
                                             "min_free_disk_mb": 2048})
        self.assertEqual(payload["slots"]["in_use"], 1)

    def test_a_malformed_remote_reply_is_refused(self):
        for reply in (["not", "an", "object"], {"ok": True, "policy": "x"},
                      {"ok": True, "policy": {"max_parallel": 0}}):
            with mock.patch("sandbox.transports.remote_jobs.RemoteJobTransport.control",
                            return_value=reply):
                code, payload = self.run_command(remote="example-remote")
            self.assertEqual((code, payload["code"]),
                             (1, "remote_job_policy_invalid_response"), reply)

    def test_remote_bad_value_never_contacts_the_host(self):
        with mock.patch("sandbox.transports.remote_jobs.RemoteJobTransport.control") as control:
            code, payload = self.run_command(remote="example-remote", max_parallel=0,
                                             confirm=True)
        control.assert_not_called()
        self.assertEqual((code, payload["code"]), (1, "job_policy_invalid_max_parallel"))


if __name__ == "__main__":
    unittest.main()
