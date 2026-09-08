"""Regressions derived from the September delivery audit and actual CLI runs."""
import contextlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sandbox.application.job_service import JobService
from sandbox.core import _instances, _remote
from sandbox.commands import instances_cmd
from sandbox.jobs.listing import job_page, MAX_JOB_PAGE_BYTES
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class JobHistoryRegressions(unittest.TestCase):
    def test_large_history_pages_are_bounded_complete_and_continuable(self):
        rows = [{"job_id": f"{index:032x}", "lifecycle": "succeeded",
                 "project_identity": "界" * 300, "request_id": "x" * 1000,
                 "source_identity": "y" * 1000, "command_json": {"secret": "DO-NOT-LIST"},
                 "submission_json": "DO-NOT-LIST", "result_json": "DO-NOT-LIST"}
                for index in range(200)]
        remaining = rows
        seen = []
        while remaining:
            page = job_page(remaining, limit=200, has_more=False)
            encoded = json.dumps(page).encode()
            self.assertLessEqual(len(encoded), MAX_JOB_PAGE_BYTES)
            self.assertNotIn(b"DO-NOT-LIST", encoded)
            ids = [row["job_id"] for row in page["jobs"]]
            self.assertTrue(ids)
            seen.extend(ids)
            if len(ids) < len(remaining):
                self.assertTrue(page["page"]["has_more"])
                self.assertEqual(page["page"]["next_cursor"], ids[-1])
            else:
                self.assertIsNone(page["page"]["next_cursor"])
            remaining = remaining[len(ids):]
        self.assertEqual(seen, [row["job_id"] for row in rows])

    def test_old_controller_completeness_stays_unknown_and_large_fields_use_detail(self):
        page = job_page([{"job_id": "a" * 32, "request_id": "x" * 2000}], limit=10, has_more=None)
        self.assertEqual(page["page"]["completeness"], "unknown")
        self.assertIsNone(page["page"]["has_more"])
        self.assertEqual(page["page"]["next_cursor"], "a" * 32)
        self.assertNotIn("request_id", page["jobs"][0])
        self.assertIn("request_id", page["jobs"][0]["detail_required"])

    def test_cancel_then_force_preserves_identity_and_escalates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = JobRepository(Path(tmp) / "jobs.sqlite")
            self.addCleanup(repository.close)
            service = JobService(repository, JobStorage(tmp, free_disk_reserve=0), None,
                                 launcher=lambda _: None)
            row, _ = repository.accept(JobSubmission("test", tmp, "p", "local", "cancel",
                ("echo", "synthetic"), 30, SourceIdentity("s")))
            job_id = row["job_id"]
            repository.transition(job_id, "running")
            repository.put_process_identity(job_id, host_boot_id="boot", supervisor_pid=100,
                supervisor_start_identity="sup", supervisor_nonce_hash="nonce", child_pid=101,
                child_pgid=101, child_start_identity="child")
            with patch("sandbox.application.job_service.verify_owned_process_identity", return_value=True), \
                 patch("sandbox.application.job_service.signal_owned_process_group", return_value=True) as signal:
                self.assertEqual(service.cancel(job_id)["lifecycle"], "cancelling")
                self.assertEqual(service.cancel(job_id, force=True)["lifecycle"], "cancelling")
                self.assertEqual([call.args[1] for call in signal.call_args_list], [15, 9])
                signal.side_effect = ProcessLookupError
                self.assertEqual(service.cancel(job_id, force=True)["signal_delivery"], "process_unavailable")


class InstanceReadinessRegressions(unittest.TestCase):
    def test_port_trio_reserves_each_choice_when_ranges_converge(self):
        def next_free(base, used):
            return next(port for port in range(base, base + 10) if port not in used)

        with patch.object(_instances, "resolve_instances", return_value={}), \
             patch.object(_instances, "_next_free_port", side_effect=next_free):
            ports = _instances._pick_instance_ports({"runtime": {
                "wordpress_port": 8251, "db_port": 8251, "mailpit_port": 8251}})
        self.assertEqual(set(ports.values()), {8251, 8252, 8253})

    def test_duplicate_repair_is_scoped_and_retains_other_reservations(self):
        other = {"wordpress_port": 8251, "db_port": 3381, "mailpit_port": 8253}
        selected = {"wordpress_port": 8251, "db_port": 3381, "mailpit_port": 8251}
        config = {"other": other, "selected": selected}
        local = {}
        with patch.object(_instances, "resolve_instances", return_value=config), \
             patch.object(_instances, "_local_yaml", return_value=local), \
             patch.object(_instances, "_write_local_yaml") as write, \
             patch.object(_instances, "write_compose_files"), \
             patch.object(_instances, "load_config", return_value={}), \
             patch.object(_instances, "_port_busy_by_other", side_effect=AssertionError("unrelated port probe")), \
             patch.object(_instances, "_next_free_port", side_effect=lambda base, used:
                          next(port for port in range(base, base + 10) if port not in used)):
            _instances._resolve_port_conflicts({}, instance_names={"selected"})
        self.assertEqual(config["other"], other)
        self.assertEqual(set(local["instances"]), {"selected"})
        repaired = local["instances"]["selected"]
        self.assertEqual(len(set(repaired.values())), 3)
        self.assertFalse(set(repaired.values()) & set(other.values()))
        write.assert_called_once()

    def test_final_route_rejects_errors_and_foreign_redirects(self):
        for code, location, expected in [(200, None, True), (404, None, False),
                                        (503, None, False), (301, "/wp-admin/", True),
                                        (302, "https://foreign.test/", False), (302, None, False)]:
            with self.subTest(code=code, location=location):
                response = types.SimpleNamespace(status=code, headers={"Location": location}, close=lambda: None)
                opener = Mock()
                opener.open.return_value = response
                with patch("urllib.request.build_opener", return_value=opener), \
                     patch.object(_instances, "site_url", return_value="https://owned.test"), \
                     patch("time.sleep"):
                    actual = _instances._wait_reachable({}, timeout=1, require_application_success=True)
                self.assertEqual(actual, expected)

    def test_backend_probe_does_not_enter_canonical_route(self):
        with patch.object(_instances, "_wait_reachable", return_value=True) as reachable:
            self.assertTrue(_instances._wait_http(8252, timeout=1))
        reachable.assert_called_once_with({"wordpress_port": 8252}, timeout=1, backend_only=True)

    def test_durable_explicit_reveal_is_still_redacted(self):
        output = io.StringIO()
        with patch("sandbox.commands.instances_cmd.os.environ", {"SANDBOX_DURABLE_JOB_ID": "a" * 32}), \
             contextlib.redirect_stdout(output):
            instances_cmd._print_ensure_json({"login_url": "https://owned.test/?sandbox_autologin=NONSECRET-SENTINEL"},
                                             reveal_login=True)
        self.assertNotIn("NONSECRET-SENTINEL", output.getvalue())

    def test_url_update_and_readback_use_exact_instance_for_all_four_commands(self):
        with patch.object(_remote, "list_remote_instances", return_value=[
                {"name": "primary", "label": "default"}, {"name": "preview", "label": "preview"}]), \
             patch.object(_remote, "remote_sb_path", return_value="/remote/sb"), \
             patch.object(_remote, "ssh_run", return_value=types.SimpleNamespace(returncode=0)) as run:
            _remote.set_remote_instance_url({}, "/remote/project", "preview", "https://preview.test")
        command = run.call_args.args[1]
        self.assertEqual(command.count("--instance preview wp --local --project-dir /remote/project"), 4)
        self.assertIn("unset SANDBOX_INSTANCE SANDBOX_LABEL", command)
        self.assertNotIn("--instance primary", command)
        with patch.object(_remote, "list_remote_instances", return_value=[]), \
             patch.object(_remote, "ssh_run") as run:
            with self.assertRaisesRegex(RuntimeError, "not uniquely registered"):
                _remote.set_remote_instance_url({}, "/remote/project", "preview", "https://preview.test")
            run.assert_not_called()
