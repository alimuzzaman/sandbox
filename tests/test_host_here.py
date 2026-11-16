"""Fix 5: `sb host ... --here`, the installed owner driving its own host without SSH.

The owner origin record a confirmed migrate writes, the local server CLI job
`--here` submits from it, and the refusals before any submission. Nothing here
starts a job: submission, job view and output are stubbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
from sandbox.application.server_cli import ServerCliOwner  # noqa: E402
from sandbox.hosting import owner_origin, server_cli_dispatch  # noqa: E402
from sandbox.hosting import server_cli_here as here  # noqa: E402
from tests.test_controller_update_remote import SOURCE, _completed, _staged_source  # noqa: E402

MARKER, REVISION, TARGET = "b" * 24, "c" * 24, "a" * 24


def _owner(home: Path) -> ServerCliOwner:
    return ServerCliOwner(uid=os.geteuid(), home=str(home), sandbox_home=str(home / "sandbox"),
                          executable="/opt/sb-src/sb", target_identity=TARGET,
                          ownership_marker=MARKER, runtime_revision=REVISION)


def _record(**changes) -> dict:
    return {**owner_origin.payload("xspeed-dev", "nginx", 9174, MARKER, REVISION), **changes}


def _write(home: Path, record, mode=0o600) -> Path:
    path = owner_origin.path_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record if isinstance(record, str) else json.dumps(record))
    path.chmod(mode)
    return path


CAPABILITY = {"target_identity": TARGET, "runtime_revision": REVISION, "ownership_marker": MARKER,
              "executable": "/opt/sb-src/sb",
              "target_metadata": {"origin_ipv4": "212.95.33.238", "tailscale_host": "100.64.181.113"}}


def _args(argv: list[str], **values) -> SimpleNamespace:
    """A parsed `sb host ...` namespace; ``argv`` is what follows `sb`."""
    base = dict(cmd="host", action=argv[1], image_action=argv[2] if len(argv) > 2 and not argv[2].startswith("-") else None,
                here="--here" in argv, json="--json" in argv, remote=None, confirm="--confirm" in argv,
                project_dir=None, template=None, request_id=None, timeout=None, ref=None,
                server_cli_stack_ref=None, _server_cli_argv=list(argv))
    base.update(values)
    return SimpleNamespace(**base)


class OwnerOriginRecordTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.home)], check=False))
        self.owner = _owner(self.home)

    def test_record_for_the_installed_unit_is_read(self):
        _write(self.home, _record())
        self.assertEqual(owner_origin.read_for_owner(self.owner)["remote_name"], "xspeed-dev")
        self.assertEqual(owner_origin.remote_label(self.home), "xspeed-dev")

    def test_missing_shared_foreign_or_invalid_records_refuse(self):
        with self.assertRaises(owner_origin.OwnerOriginUnavailable):
            owner_origin.read_for_owner(self.owner)
        for record, mode in ((_record(), 0o644), (_record(ownership_marker="d" * 24), 0o600),
                             (_record(runtime_revision="d" * 24), 0o600),
                             (_record(front_door="apache"), 0o600), (_record(extra=1), 0o600),
                             ("not json", 0o600)):
            with self.subTest(record=record, mode=oct(mode)):
                _write(self.home, record, mode)
                with self.assertRaises(owner_origin.OwnerOriginUnavailable):
                    owner_origin.read_for_owner(self.owner)
        self.assertIsNone(owner_origin.remote_label(self.home))

    def test_a_symlinked_record_is_refused(self):
        real = self.home / "elsewhere.json"
        real.write_text(json.dumps(_record()))
        real.chmod(0o600)
        path = owner_origin.path_for(self.home)
        path.parent.mkdir(parents=True)
        path.symlink_to(real)
        with self.assertRaises(owner_origin.OwnerOriginUnavailable):
            owner_origin.read_for_owner(self.owner)


class MigrateWritesTheRecordTests(unittest.TestCase):
    @mock.patch("sandbox.core._remote.ssh_run")
    def test_confirmed_install_writes_the_record_after_the_rollback_trap(self, ssh_run):
        ssh_run.return_value = _completed(returncode=0)
        with mock.patch.object(sr, "RUNTIME_DIR", Path(tempfile.mkdtemp()) / "runtime"):
            sr.migrate_remote_mcp_service(
                {"ssh": "ubuntu@203.0.113.10", "_remote_name": "xspeed-dev", "front_door": "nginx"},
                "127.0.0.1", 9174, "c" * 64, "https://sandbox.example.test",
                confirm=True, source_revision=SOURCE, staged_source=_staged_source())
        command = ssh_run.call_args.args[1]
        self.assertIn("$HOME/sandbox/runtime/owner-origin.json", command)
        self.assertLess(command.rindex("trap - EXIT"), command.index("owner-origin.json"))
        self.assertIn('"remote_name":"xspeed-dev"', command)
        self.assertIn('"front_door":"nginx"', command)
        self.assertIn('"mcp_port":9174', command)

    def test_the_written_record_is_what_here_reads(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(home)], check=False))
        record = sr.remote_mcp_service_record("127.0.0.1", 9174, None)
        command = sr._owner_origin_command({"_remote_name": "xspeed-dev", "front_door": "nginx"}, record)
        subprocess.run(["sh", "-c", "set -eu; " + command], env={**os.environ, "HOME": str(home)}, check=True)
        from dataclasses import replace
        owner = replace(_owner(home), ownership_marker=record["ownership_marker"],
                        runtime_revision=record["runtime_revision"])
        read = owner_origin.read_for_owner(owner)
        self.assertEqual((read["remote_name"], read["front_door"], read["mcp_port"]), ("xspeed-dev", "nginx", 9174))
        self.assertEqual(owner_origin.path_for(home).stat().st_mode & 0o777, 0o600)

    def test_unproven_front_door_removes_a_stale_record_and_writes_none(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(home)], check=False))
        _write(home, _record())
        record = sr.remote_mcp_service_record("127.0.0.1", 9174, None)
        joined_unproven = {"_remote_name": "xspeed-dev", "joined": True, "control_access": "tailnet"}
        command = sr._owner_origin_command(joined_unproven, record)
        subprocess.run(["sh", "-c", "set -eu; " + command], env={**os.environ, "HOME": str(home)}, check=True)
        self.assertFalse(owner_origin.path_for(home).exists())


class HereSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.home)], check=False))
        self.owner = _owner(self.home)
        patcher = mock.patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.submitted: list[list[str]] = []

    def run_here(self, args, *, record=None, output='{"ok": true, "action": "follow-status"}\n',
                 lifecycle="succeeded"):
        def submit(argv):
            self.submitted.append(argv)
            return "f" * 32
        return here.run(args, submit=submit, view=lambda job_id: {"lifecycle": lifecycle},
                        output=lambda job_id: output, owner=self.owner,
                        record=record if record is not None else _record(), capability=dict(CAPABILITY),
                        sleep=lambda _s: None)

    def test_miu_follow_call_becomes_the_controllers_server_cli_job(self):
        """`sb host follow add ... --here --json` (Miu's call shape) without --remote."""
        argv = ["host", "follow", "add", "--name", "pr-free-1", "--owner", "miu", "--here", "--json"]
        result = self.run_here(_args(argv))
        self.assertEqual(result, {"ok": True, "action": "follow-status", "job_id": "f" * 32})
        (job,) = self.submitted
        self.assertEqual(job[:4], ["/opt/sb-src/sb", "job-start", "--local", "--server-cli"])
        flags = dict(zip(job[4:job.index("--"):2], job[5:job.index("--"):2]))
        self.assertEqual((flags["--server-cli-target"], flags["--server-cli-revision"],
                          flags["--server-cli-marker"], flags["--server-cli-origin"]),
                         (TARGET, REVISION, MARKER, "xspeed-dev"))
        self.assertEqual(json.loads(flags["--server-cli-target-metadata"]), {
            "front_door": "nginx", "mcp_port": 9174, "origin_ipv4": "212.95.33.238",
            "tailscale_host": "100.64.181.113"})
        self.assertEqual(flags["--project-dir"], str((self.home / "sandbox/runtime/hosting/here").resolve()))
        inner = job[job.index("--") + 1:]
        self.assertEqual(inner, ["host", "follow", "add", "--name", "pr-free-1", "--owner", "miu",
                                 "--json", "--remote", "xspeed-dev"])

    def test_stack_down_relays_the_commands_refusal_and_exit(self):
        argv = ["host", "stack", "down", "--name", "pr-free-1", "--owner", "miu", "--confirm", "--here", "--json"]
        result = self.run_here(_args(argv), lifecycle="failed",
                               output='{"ok": false, "code": "stack_not_found"}\n')
        self.assertEqual((result["ok"], result["code"]), (False, "stack_not_found"))
        with mock.patch.object(here, "run", return_value=result), \
                mock.patch("builtins.print") as printed, self.assertRaises(SystemExit):
            here.dispatch(_args(argv))
        self.assertIn("stack_not_found", printed.call_args.args[0])

    def test_matching_remote_is_replaced_and_a_different_one_refused(self):
        argv = ["host", "stack", "list", "--remote", "xspeed-dev", "--here", "--json"]
        self.run_here(_args(argv, remote="xspeed-dev"))
        inner = self.submitted[0][self.submitted[0].index("--") + 1:]
        self.assertEqual(inner.count("--remote"), 1)
        with self.assertRaises(here.HereRefusal) as caught:
            self.run_here(_args(["host", "stack", "list", "--remote=other", "--here"], remote="other"))
        self.assertEqual(caught.exception.code, "host_here_remote_mismatch")
        self.assertEqual(len(self.submitted), 1)

    def test_refusals_happen_before_any_submission(self):
        cases = {
            "host_here_unsupported": _args(["host", "destroy", "--here"]),
            "host_here_unsupported ": _args(["host", "apply", "--here"]),  # no --confirm
            "host_here_argv_invalid": _args(["host", "apply", "--confirm", "--here", "--templ", "x"]),
            "host_here_argv_invalid ": _args(["host", "follow", "status", "--her"], _server_cli_argv=[
                "host", "follow", "status", "--her"]),
            "host_here_argv_invalid  ": _args(["host", "stack", "list", "--here"], credential_scope="x"),
            "host_here_argv_invalid   ": _args(["host", "stack", "list", "--here", "--js"]),
            "host_here_argv_invalid    ": _args(["host", "apply", "--confirm", "--here",
                                                 "--server-cli-source-branch={}"],
                                                server_cli_source_branch="{}"),
        }
        for code, args in cases.items():
            with self.subTest(code=code.strip()), self.assertRaises(here.HereRefusal) as caught:
                self.run_here(args)
            self.assertEqual(caught.exception.code, code.strip())
        self.assertEqual(self.submitted, [])
        # Refused before anything is created on the host.
        self.assertFalse((self.home / "sandbox").exists())

    def test_inner_command_always_asks_for_json_and_keeps_project(self):
        inner = here.inner_argv(["host", "stack", "list", "--project", "x", "--here"], "xspeed-dev")
        self.assertEqual(inner, ["host", "stack", "list", "--project", "x", "--json", "--remote", "xspeed-dev"])

    def test_sudo_caller_keeping_its_own_home_is_refused(self):
        with mock.patch.dict(os.environ, {"HOME": "/tmp"}), self.assertRaises(here.HereRefusal) as caught:
            here._require_owner_home(self.owner)
        self.assertEqual(caught.exception.code, "host_here_owner_unavailable")
        (self.home / "sandbox").mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            here._require_owner_home(self.owner)

    def test_apply_job_from_stack_up_or_apply_is_followed_to_its_end(self):
        accepted = json.dumps({"ok": True, "status": "accepted", "job_id": "1" * 32, "completion": "pending"})
        views = {"f" * 32: "succeeded", "1" * 32: "failed"}
        result = here._follow_child(json.loads(accepted), "f" * 32,
                                    view=lambda job_id: {"lifecycle": views[job_id], "exit_code": 1},
                                    sleep=lambda _s: None, clock=lambda: 0)
        self.assertEqual((result["ok"], result["job_id"], result["outer_job_id"], result["completion"]),
                         (False, "1" * 32, "f" * 32, "failed"))
        self.assertEqual(result["code"], "host_here_apply_failed")

    def test_no_record_or_foreign_owner_refuses(self):
        with self.assertRaises(here.HereRefusal) as caught:
            here.run(_args(["host", "stack", "list", "--here"]), owner=self.owner,
                     submit=lambda argv: self.fail("submitted"))
        self.assertEqual(caught.exception.code, "host_here_origin_unavailable")
        from sandbox.application.server_cli import ServerCLIUnavailable
        with mock.patch("sandbox.application.server_cli.read_current_server_owner",
                        side_effect=ServerCLIUnavailable("x")), self.assertRaises(here.HereRefusal) as caught:
            here.run(_args(["host", "stack", "list", "--here"]), submit=lambda argv: self.fail("submitted"))
        self.assertEqual(caught.exception.code, "host_here_owner_unavailable")

    def test_job_that_never_finishes_reports_its_id_not_a_result(self):
        clock = iter(range(0, 10_000, 500))
        with self.assertRaises(here.HereRefusal) as caught:
            here.run(_args(["host", "stack", "list", "--here"]), submit=lambda argv: "e" * 32,
                     view=lambda job_id: {"lifecycle": "running"}, output=lambda job_id: "",
                     owner=self.owner, record=_record(), capability=dict(CAPABILITY),
                     sleep=lambda _s: None, clock=lambda: next(clock))
        self.assertEqual((caught.exception.code, caught.exception.job_id), ("host_here_job_unfinished", "e" * 32))


def _git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}).stdout.strip()


class SourceBindingTests(unittest.TestCase):
    """stack up and apply run on the checkout in place, with the controller's bindings."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.home)], check=False))
        self.owner = _owner(self.home)
        (self.home / "repo").mkdir()
        self.repo = (self.home / "repo").resolve()
        _git(self.repo, "init", "-q", "-b", "dev")
        (self.repo / "a.txt").write_text("one\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-qm", "one")
        self.first = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.txt").write_text("two\n")
        _git(self.repo, "commit", "-qam", "two")
        self.head = _git(self.repo, "rev-parse", "HEAD")

    def argv(self, original, **values):
        args = _args(original, project_dir=str(self.repo), **values)
        return here.job_argv(args, original, owner=self.owner, record=_record(), capability=dict(CAPABILITY),
                             request_id="host-here-test", root=self.repo)

    def test_apply_binds_the_checkouts_branch_and_the_job_validator_accepts_it(self):
        original = ["host", "apply", "--confirm", "--project-dir", str(self.repo), "--here"]
        job = self.argv(original)
        self.assertEqual(job[job.index("--source-commit") + 1], self.head)
        inner = tuple(job[job.index("--") + 1:])
        context = SimpleNamespace(argv=inner, source_commit=self.head, project_root=str(self.repo), owner=self.owner)
        with mock.patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=context), \
                mock.patch("sandbox.application.server_cli.validate_owner_current", return_value=context):
            self.assertEqual(server_cli_dispatch.bound_source_branch(self.repo), "dev")

    def test_stack_up_binds_the_ref_only_when_it_is_checked_out_and_clean(self):
        original = ["host", "stack", "up", "--project-dir", str(self.repo), "--here"]
        job = self.argv(original)
        inner = job[job.index("--") + 1:]
        packed = next(value for value in inner if value.startswith("--server-cli-stack-ref="))
        self.assertEqual(json.loads(packed.split("=", 1)[1]),
                         {"ref": "HEAD", "commit": self.head, "ref_branch": "dev"})
        self.assertEqual(job[job.index("--source-commit") + 1], self.head)
        with self.assertRaises(here.HereRefusal) as caught:
            self.argv(original + ["--ref", self.first], ref=self.first)
        self.assertEqual(caught.exception.code, "host_here_ref_not_checked_out")
        (self.repo / "a.txt").write_text("dirty\n")
        with self.assertRaises(here.HereRefusal) as caught:
            self.argv(original)
        self.assertEqual(caught.exception.code, "host_here_source_dirty")

    def test_relative_paths_become_the_callers_absolute_paths(self):
        cwd = os.getcwd()
        os.chdir(self.home)
        self.addCleanup(os.chdir, cwd)
        argv = here._absolute_paths(["host", "apply", "--project-dir", "repo", "--template=repo/a.txt"])
        self.assertEqual(argv[3], str(self.repo))
        self.assertEqual(argv[4], "--template=" + str(self.repo / "a.txt"))


class DispatchRoutingTests(unittest.TestCase):
    def test_here_owner_commands_never_reach_the_controller_path(self):
        args = _args(["host", "follow", "status", "--here", "--json"])
        with mock.patch.object(here, "dispatch") as dispatch, \
                mock.patch.object(server_cli_dispatch, "_dispatch_host_if_selected") as controller:
            self.assertTrue(server_cli_dispatch.dispatch_host_if_selected(args))
        dispatch.assert_called_once_with(args)
        controller.assert_not_called()

    def test_older_meanings_of_here_are_left_alone(self):
        for argv in (["host", "stack", "reap", "--here", "--plan"], ["host", "follow", "tick", "--here"],
                     ["host", "follow", "apply", "--here"]):
            with self.subTest(argv=argv):
                self.assertFalse(here.claims(_args(argv)))
        self.assertFalse(here.claims(_args(["host", "login-url", "--here"], image_action=None)))
        self.assertFalse(here.claims(_args(["host", "stack", "list", "--json"])))


if __name__ == "__main__":
    unittest.main()
