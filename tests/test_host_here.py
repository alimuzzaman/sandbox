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
            "host_here_argv_invalid     ": _args(["host", "stack", "list", "--here"], request_id="r" * 65),
            "host_here_argv_invalid      ": _args(["host", "stack", "down", "--here", "--project-dir", "."],
                                                  project_dir="."),
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


class SourceSnapshotTests(unittest.TestCase):
    """stack up and apply run on an owner-private snapshot of the bound commit, never in place."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.home)], check=False))
        self.owner = _owner(self.home)
        (self.home / "repo").mkdir()
        self.repo = (self.home / "repo").resolve()
        _git(self.repo, "init", "-q", "-b", "dev")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "a.txt").write_text("one\n")
        _git(self.repo, "add", "a.txt", ".gitignore")
        _git(self.repo, "commit", "-qm", "one")
        self.first = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.txt").write_text("two\n")
        _git(self.repo, "commit", "-qam", "two")
        self.head = _git(self.repo, "rev-parse", "HEAD")
        patcher = mock.patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.submitted: list[list[str]] = []

    def run_here(self, original, **values):
        def submit(argv):
            self.submitted.append(argv)
            staged = Path(argv[argv.index("--project-dir") + 1])
            self.seen = {path.name for path in staged.iterdir()}
            self.staged_head = _git(staged, "rev-parse", "HEAD")
            return "f" * 32
        args = _args(original, **values)
        return here.run(args, submit=submit, view=lambda job_id: {"lifecycle": "succeeded"},
                        output=lambda job_id: '{"ok": false, "code": "test_stop"}\n', owner=self.owner,
                        record=_record(), capability=dict(CAPABILITY), sleep=lambda _s: None)

    def test_apply_runs_on_a_private_snapshot_and_the_job_validator_accepts_it(self):
        original = ["host", "apply", "--confirm", "--project-dir", str(self.repo), "--here"]
        self.run_here(original, project_dir=str(self.repo))
        (job,) = self.submitted
        staged = Path(job[job.index("--project-dir") + 1])
        snapshots = self.home / "sandbox/runtime/hosting/here/src"
        self.assertIn(snapshots, staged.parents)
        self.assertNotEqual(staged, self.repo)
        self.assertEqual(staged.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.staged_head, job[job.index("--source-commit") + 1]), (self.head, self.head))
        inner = tuple(job[job.index("--") + 1:])
        self.assertEqual(inner[inner.index("--project-dir") + 1], str(staged))
        context = SimpleNamespace(argv=inner, source_commit=self.head, project_root=str(staged), owner=self.owner)
        with mock.patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=context), \
                mock.patch("sandbox.application.server_cli.validate_owner_current", return_value=context):
            self.assertEqual(server_cli_dispatch.bound_source_branch(staged), "dev")

    def test_apply_refuses_a_dirty_or_untracked_checkout(self):
        original = ["host", "apply", "--confirm", "--project-dir", str(self.repo), "--here"]
        for change in ("a.txt", "new.txt"):
            with self.subTest(change=change):
                (self.repo / change).write_text("changed\n")
                with self.assertRaises(here.HereRefusal) as caught:
                    self.run_here(original, project_dir=str(self.repo))
                self.assertEqual(caught.exception.code, "host_here_source_dirty")
                _git(self.repo, "checkout", "-q", "--", "a.txt")
                (self.repo / "new.txt").unlink(missing_ok=True)
        self.assertEqual(self.submitted, [])

    def test_stack_up_stages_the_ref_without_untracked_or_ignored_files(self):
        (self.repo / "untracked.txt").write_text("x\n")
        (self.repo / "ignored.txt").write_text("x\n")
        (self.repo / "a.txt").write_text("edited\n")
        original = ["host", "stack", "up", "--project-dir", str(self.repo), "--ref", self.first, "--here"]
        self.run_here(original, project_dir=str(self.repo), ref=self.first)
        (job,) = self.submitted
        self.assertEqual(self.staged_head, self.first)
        self.assertEqual(self.seen, {".git", ".gitignore", "a.txt"})
        inner = job[job.index("--") + 1:]
        packed = next(value for value in inner if value.startswith("--server-cli-stack-ref="))
        self.assertEqual(json.loads(packed.split("=", 1)[1])["commit"], self.first)

    def test_the_staged_tree_is_stable_reset_each_run_and_one_run_at_a_time(self):
        from sandbox.hosting import here_source
        with here_source.snapshot(self.owner.sandbox_home, self.repo, self.first) as staged:
            first_path = staged
            (staged / "left-by-a-job.txt").write_text("x\n")
            with self.assertRaises(here_source.SnapshotError) as caught:
                with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head):
                    pass
            self.assertEqual(caught.exception.code, "host_here_source_busy")
        with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head) as staged:
            self.assertEqual(staged, first_path)
            self.assertEqual(_git(staged, "rev-parse", "HEAD"), self.head)
            self.assertFalse((staged / "left-by-a-job.txt").exists())
        with mock.patch.object(here_source, "_active_jobs_inside", return_value=True), \
                self.assertRaises(here_source.SnapshotError) as caught:
            with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head):
                pass
        self.assertEqual(caught.exception.code, "host_here_source_busy")

    def test_staged_tree_keeps_the_repo_identity_without_the_token(self):
        from sandbox.hosting import here_source
        from sandbox.hosting.lifetime import naming
        _git(self.repo, "remote", "add", "origin", "https://user:secret-token@github.com/WPDevelopers/demo.git")
        with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head) as staged:
            self.assertEqual(naming.repo_identity(staged), naming.repo_identity(self.repo))
            self.assertEqual(naming.repo_identity(staged), "github.com/wpdevelopers/demo")
            self.assertNotIn("secret-token", _git(staged, "config", "--list"))

    def test_a_shallow_source_is_staged(self):
        from sandbox.hosting import here_source
        shallow = self.home / "shallow"
        _git(self.home, "clone", "-q", "--depth", "1", "file://" + str(self.repo), str(shallow))
        head = _git(shallow, "rev-parse", "HEAD")
        with here_source.snapshot(self.owner.sandbox_home, shallow.resolve(), head) as staged:
            self.assertEqual(_git(staged, "rev-parse", "HEAD"), head)

    def test_a_source_another_account_controls_is_refused_before_any_git_read(self):
        from sandbox.hosting import here_source
        with mock.patch.object(here_source, "_owned", return_value=False), \
                mock.patch.object(here_source.subprocess, "run", side_effect=AssertionError("git ran")), \
                mock.patch.object(here, "_source_bindings", side_effect=AssertionError("source read")), \
                self.assertRaises(here.HereRefusal) as caught:
            self.run_here(["host", "apply", "--confirm", "--project-dir", str(self.repo), "--here"],
                          project_dir=str(self.repo))
        self.assertEqual(caught.exception.code, "host_here_source_not_owned")
        self.assertEqual(self.submitted, [])

    def test_template_only_stack_up_carries_the_checkouts_owner_label(self):
        _git(self.repo, "config", "user.name", "Repo Owner")
        (self.repo / "t.yml").write_text("x\n")
        _git(self.repo, "add", "t.yml")
        _git(self.repo, "commit", "-qm", "template")
        head = _git(self.repo, "rev-parse", "HEAD")
        original = ["host", "stack", "up", "--template", str(self.repo / "t.yml"), "--here"]
        self.run_here(original, template=str(self.repo / "t.yml"), owner=None)
        (job,) = self.submitted
        inner = job[job.index("--") + 1:]
        staged = Path(job[job.index("--project-dir") + 1])
        self.assertEqual(inner[inner.index("--template") + 1], str(staged / "t.yml"))
        self.assertIn("--owner=Repo Owner", inner)
        self.submitted.clear()
        self.run_here(original + ["--owner", "miu"], template=str(self.repo / "t.yml"), owner="miu")
        inner = self.submitted[0][self.submitted[0].index("--") + 1:]
        self.assertEqual([t for t in inner if t.startswith("--owner")], ["--owner"])
        self.assertEqual(inner[inner.index("--owner") + 1], "miu")
        self.assertEqual(self.staged_head, head)

    def test_a_tree_that_cannot_be_reset_is_moved_aside_and_the_next_run_works(self):
        from sandbox.hosting import here_source
        with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head) as staged:
            (staged / "ro").mkdir()
            (staged / "ro" / "f").write_text("x\n")
            (staged / "ro").chmod(0o500)
        self.addCleanup(lambda: [p.chmod(0o700) for p in self.home.rglob("ro") if p.is_dir()])
        if os.geteuid() != 0:
            with self.assertRaises(here_source.SnapshotError):
                with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head):
                    pass
        with here_source.snapshot(self.owner.sandbox_home, self.repo, self.head) as staged:
            self.assertFalse((staged / "ro").exists())
        group = staged.parent
        self.assertEqual([p.name for p in group.iterdir() if p.name.startswith(".tree.broken-")], [])

    def test_busy_check_uses_the_registrys_in_use_rule(self):
        import sqlite3
        from sandbox.hosting import here_source
        tree = (self.home / "src" / "tree").resolve()
        registry = Path(self.owner.sandbox_home) / "runtime" / "jobs" / "registry.sqlite3"
        registry.parent.mkdir(parents=True)
        db = sqlite3.connect(registry)
        db.executescript("CREATE TABLE jobs (job_id TEXT, project_root TEXT, lifecycle TEXT);"
                         "CREATE TABLE workspace_leases (job_id TEXT);")
        def state(*rows, leases=()):
            db.execute("DELETE FROM jobs"); db.execute("DELETE FROM workspace_leases")
            db.executemany("INSERT INTO jobs VALUES (?,?,?)", rows)
            db.executemany("INSERT INTO workspace_leases VALUES (?)", [(j,) for j in leases])
            db.commit()
            return here_source._active_jobs_inside(tree, self.owner.sandbox_home)
        self.assertIs(state(("a", str(tree / "sub"), "running")), True)
        self.assertIs(state(("a", str(tree), "succeeded")), False)
        self.assertIs(state(("a", str(tree), "interrupted"), leases=["a"]), True)
        self.assertIs(state(("a", str(tree) + "2", "running"), ("b", str(tree) + "_x", "running")), False)
        self.assertIs(state(*[(f"j{i}", f"/elsewhere/{i}", "running") for i in range(300)]), False)
        db.close()
        registry.write_text("not a database")
        self.assertIsNone(here_source._active_jobs_inside(tree, self.owner.sandbox_home))

    def test_paths_must_stay_inside_the_source_root(self):
        staged = Path("/staged")
        (self.repo / "t.yml").write_text("x\n")
        moved = here.relocate_paths(["host", "stack", "up", "--project-dir", str(self.repo),
                                     "--template=" + str(self.repo / "t.yml")], self.repo, staged)
        self.assertEqual(moved[4:], [str(staged), "--template=" + str(staged / "t.yml")])
        outside = self.home / "elsewhere.yml"
        outside.write_text("x\n")
        for argv in (["--template", str(outside)], ["--project-dir", str(self.home)]):
            with self.subTest(argv=argv), self.assertRaises(here.HereRefusal) as caught:
                here.relocate_paths(["host", "stack", "up", *argv], self.repo, staged)
            self.assertEqual(caught.exception.code, "host_here_path_outside_source")


class WaitTests(unittest.TestCase):
    def test_an_unreadable_registry_keeps_waiting_and_a_missing_outer_job_is_reported(self):
        states = iter([None, None, {"lifecycle": "succeeded"}])
        self.assertEqual(here._wait("e" * 32, 10, view=lambda job_id: next(states), sleep=lambda _s: None,
                                    clock=lambda: 0), {"lifecycle": "succeeded"})
        with self.assertRaises(here.HereRefusal) as caught:
            here._submit_and_wait(SimpleNamespace(action="stack", image_action="list"),
                                  ["sb", "--timeout", "900"], "req", submit=lambda argv: "e" * 32,
                                  view=lambda job_id: here._Missing(), output=lambda job_id: "",
                                  sleep=lambda _s: None, clock=lambda: 0)
        self.assertEqual((caught.exception.code, caught.exception.job_id), ("host_here_job_not_found", "e" * 32))
        self.assertIn("do not resubmit", caught.exception.message)

    def test_job_state_tells_a_missing_row_from_an_unreadable_registry(self):
        from sandbox.jobs.registry import JobNotFound
        runtime = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(runtime)], check=False))
        (runtime / "jobs").mkdir()
        (runtime / "jobs" / "registry.sqlite3").write_text("")

        class Repo:
            def __init__(self, error):
                self.error = error
            def get(self, job_id):
                raise self.error
            def close(self):
                pass
        from sandbox.core import _paths
        with mock.patch.object(_paths, "RUNTIME_DIR", runtime):
            with mock.patch("sandbox.hosting.here_source.open_registry", return_value=Repo(JobNotFound("x"))):
                self.assertIsInstance(here.job_state("e" * 32), here._Missing)
            with mock.patch("sandbox.hosting.here_source.open_registry", return_value=Repo(OSError("busy"))):
                self.assertIsNone(here.job_state("e" * 32))

    def test_a_refused_inner_command_is_not_followed_into_another_job(self):
        result = {"ok": False, "code": "stack_apply_in_progress", "job_id": "1" * 32}
        self.assertIs(here._follow_child(result, "f" * 32, view=lambda job_id: self.fail("followed"),
                                         sleep=lambda _s: None, clock=lambda: 0), result)

    def test_an_unknown_child_stops_and_a_timeout_keeps_the_outer_job_id(self):
        accepted = {"ok": True, "status": "accepted", "job_id": "1" * 32}
        with self.assertRaises(here.HereRefusal) as caught:
            here._follow_child(dict(accepted), "f" * 32, view=lambda job_id: here._Missing(),
                               sleep=lambda _s: None, clock=lambda: 0)
        self.assertEqual((caught.exception.code, caught.exception.extra["outer_job_id"]),
                         ("host_here_job_not_found", "f" * 32))
        clock = iter(range(0, 100_000, 1000))
        with self.assertRaises(here.HereRefusal) as caught:
            here._follow_child(dict(accepted), "f" * 32, view=lambda job_id: {"lifecycle": "running"},
                               sleep=lambda _s: None, clock=lambda: next(clock))
        self.assertEqual((caught.exception.code, caught.exception.job_id, caught.exception.extra["outer_job_id"]),
                         ("host_here_job_unfinished", "1" * 32, "f" * 32))
        refusal = caught.exception
        with mock.patch.object(here, "run", side_effect=refusal), mock.patch("builtins.print") as printed, \
                self.assertRaises(SystemExit):
            here.dispatch(_args(["host", "stack", "up", "--here"]))
        self.assertEqual(json.loads(printed.call_args.args[0])["outer_job_id"], "f" * 32)


class RelayReadsRealJobOutputTests(unittest.TestCase):
    """Feedback 25074608: the relay must read through the real job-output API and its page cap."""

    def setUp(self):
        from sandbox.application.job_service import JobService
        from sandbox.jobs.models import JobSubmission, SourceIdentity
        from sandbox.jobs.registry import JobRepository
        from sandbox.jobs.storage import JobStorage
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.addCleanup(self.repository.close)
        self.job, _ = self.repository.accept(JobSubmission(
            "server-cli", "/project", "project", "local", "default", ("host",), 60, SourceIdentity("s")))
        self.storage = JobStorage(self.temp.name, free_disk_reserve=0)
        self.storage.job_dir(self.job["job_id"], create=True)
        self.service = JobService(self.repository, self.storage, None, launcher=lambda _: None)

    def write(self, *chunks: bytes):
        from sandbox.jobs.output import JobOutputStore
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"])
        for chunk in chunks:
            output.append("stdout", chunk)
        output.finish("stdout")
        self.repository.transition(self.job["job_id"], "running")
        self.repository.transition(self.job["job_id"], "succeeded", exit_code=0)

    def relay(self) -> dict:
        deps = {"job_service": self.service}
        with mock.patch("sandbox.application.context.durable_job_dependencies", return_value=deps):
            return here._submit_and_wait(
                SimpleNamespace(action="stack", image_action="list"), ["sb", "--timeout", "900"], "req",
                submit=lambda argv: self.job["job_id"], view=lambda job_id: {"lifecycle": "succeeded"},
                output=None, sleep=lambda _s: None, clock=lambda: 0)

    def test_a_small_result_is_relayed(self):
        self.write(b"progress\n", json.dumps({"ok": True, "action": "stack-list", "stacks": []}).encode() + b"\n")
        result = self.relay()
        self.assertEqual((result["ok"], result["action"], result["job_id"]), (True, "stack-list", self.job["job_id"]))

    def test_a_result_larger_than_one_page_after_more_than_the_budget_of_noise(self):
        from sandbox.jobs.models import MAX_OUTPUT_PAGE_BYTES
        stacks = [{"name": f"s{i}", "note": "x" * 200} for i in range(1500)]
        line = json.dumps({"ok": True, "action": "stack-list", "stacks": stacks}).encode() + b"\n"
        self.assertGreater(len(line), MAX_OUTPUT_PAGE_BYTES)
        noise = [b"n" * 4095 + b"\n"] * 300  # 1.2 MiB before the result
        self.write(*noise, line)
        result = self.relay()
        self.assertEqual(len(result["stacks"]), 1500)


class RelayRefusalTests(unittest.TestCase):
    def test_output_that_never_ends_or_repeats_its_cursor_or_is_gone_refuses(self):
        import base64

        class Endless:
            def __init__(self, repeat=False):
                self.n, self.repeat = 0, repeat
            def read_output(self, job_id, query):
                self.n += 1
                return {"data": base64.b64encode(b"x").decode(), "has_more": True,
                        "cursor": "c" if self.repeat else f"c{self.n}"}

        class Gone:
            def read_output(self, job_id, query):
                raise RuntimeError("output_unavailable")
        for service, code in ((Endless(), "host_here_result_unreadable"),
                              (Endless(repeat=True), "host_here_result_unreadable"),
                              (Gone(), "host_here_output_unavailable")):
            with self.subTest(code=code, service=type(service).__name__), \
                    self.assertRaises(here.HereRefusal) as caught:
                here._stdout_tail("e" * 32, service=service)
            self.assertEqual((caught.exception.code, caught.exception.job_id), (code, "e" * 32))
        endless = Endless()
        with self.assertRaises(here.HereRefusal):
            here._stdout_tail("e" * 32, service=endless)
        self.assertEqual(endless.n, here.RELAY_MAX_PAGES)


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
