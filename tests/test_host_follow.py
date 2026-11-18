"""Spec 089: the server-side stack branch follower (`sb host follow ...`).

Every collaborator that would touch the network, systemd, GitHub or a real
stack is a fake: ssh-keygen is emulated, the job contract and stack up are
recorded calls, and each test has its own temporary store and SANDBOX_HOME.
"""
import contextlib
import io
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sandbox.commands import host_follow  # noqa: E402
from sandbox.commands import host_stack  # noqa: E402
from sandbox.hosting.follow import git, units  # noqa: E402
from sandbox.hosting.follow import tick as follow_tick  # noqa: E402
from sandbox.hosting.follow import apply as follow_apply  # noqa: E402
from sandbox.hosting.follow import spec as follow_spec  # noqa: E402
from sandbox.hosting.follow.apply import Attempt, run_prepare  # noqa: E402
from sandbox.hosting.follow.spec import FollowError  # noqa: E402
from sandbox.hosting.follow.store import FollowStore  # noqa: E402
from tests.subprocess_support import run_test_process, synthetic_environment  # noqa: E402

PRIVATE_MARKER = "PRIVATE KEY"
HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40
CAPABILITY = {"executable": "/srv/sb", "target_identity": "target-1", "runtime_revision": "rev-1",
              "ownership_marker": "marker-1", "target_metadata": {"tailscale_host": "box.ts.net"}}


def fake_ssh_keygen(argv, **_kwargs):
    """Emulate the three ssh-keygen calls git.py makes, writing fake key files."""
    if argv[:1] != ["ssh-keygen"]:
        raise AssertionError(f"unexpected command {argv}")
    path = Path(argv[argv.index("-f") + 1])
    if "-t" in argv:
        comment = argv[argv.index("-C") + 1]
        path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nZmFrZQ==\n-----END OPENSSH PRIVATE KEY-----\n")
        path.chmod(0o600)
        path.with_name(path.name + ".pub").write_text(f"ssh-ed25519 AAAAFAKE{abs(hash(comment)) % 9999} {comment}\n")
        return subprocess.CompletedProcess(argv, 0, "", "")
    if "-l" in argv:
        return subprocess.CompletedProcess(argv, 0, "256 SHA256:fakefingerprint comment (ED25519)\n", "")
    if "-y" in argv:
        return subprocess.CompletedProcess(argv, 0, path.with_name(path.name + ".pub").read_text(), "")
    raise AssertionError(f"unexpected ssh-keygen call {argv}")


def owner_args(action, **fields):
    base = {"image_action": action, "remote": "xspeed-dev", "name": "dev", "json": True, "here": False,
            "follow_repo": None, "stack_template": None, "prepare": None, "prepare_timeout": None,
            "owner": None, "ref": None, "lifetime": None, "environment": None, "generate_secrets": False,
            "attempt": None}
    base.update(fields)
    return Namespace(**base)


def add_args(**fields):
    values = {"follow_repo": ["hub=WPDevelopers/xspeed-hub@dev", "setup=WPDevelopers/xspeed-dev-setup@dev"],
              "stack_template": "{repo:setup}/stack.yml", "owner": "alim",
              "prepare": "{repo:setup}/bin/prepare --commit {commit:hub}"}
    values.update(fields)
    return owner_args("add", **values)


class FollowCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "sandbox-home"
        self.home.mkdir()
        self.store = FollowStore(self.home / "runtime" / "hosting" / "follow")
        self.context = SimpleNamespace(
            job_id="job-owner-1", origin_remote_name="xspeed-dev",
            target_metadata={"front_door": "caddy", "mcp_port": 8765, "ignored": "x"},
            owner=SimpleNamespace(executable="/srv/sandbox/sb", sandbox_home=str(self.home)),
            deadline_at="2099-01-01T00:00:00Z")
        self.installs, self.removals, self.starts = [], [], []
        for patcher in (patch.object(host_follow, "_store", lambda: self.store),
                        patch.object(host_follow, "owner_context", lambda _args: self.context),
                        patch.object(git, "_run", side_effect=fake_ssh_keygen),
                        patch.object(units, "check_host", lambda: None),
                        patch.object(units, "install", side_effect=self._install),
                        patch.object(units, "remove", side_effect=self._remove),
                        patch.object(units, "installed", lambda: bool(self.installs)),
                        patch.object(units, "start_now", side_effect=self._start)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _install(self, **kwargs):
        self.installs.append(kwargs)
        return {"timer": units.TIMER, "changed": len(self.installs) == 1}

    def _remove(self):
        self.removals.append(True)
        return {"timer": units.TIMER, "removed": True}

    def _start(self):
        self.starts.append(True)
        return True

    def audit_events(self):
        if not self.store.audit_path.exists():
            return []
        return [json.loads(line)["event"] for line in self.store.audit_path.read_text().splitlines()]

    def assert_no_private_material(self, *texts):
        for path in [self.store.audit_path, *self.store.follows.glob("*.json")]:
            if path.exists():
                self.assertNotIn(PRIVATE_MARKER, path.read_text(), path)
        for text in texts:
            self.assertNotIn(PRIVATE_MARKER, text)
            self.assertNotIn("ZmFrZQ==", text)

    def add(self, **fields):
        return host_follow._add(add_args(**fields))


class OwnerCommandTests(FollowCase):
    def test_add_stores_the_declaration_keys_and_timer_and_prints_public_keys_only(self):
        payload = self.add()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["replaced"])
        self.assertEqual([row["deploy_key"] for row in payload["repos"]], ["created", "created"])
        for row in payload["repos"]:
            self.assertTrue(row["public_key"].startswith("ssh-ed25519 "))
            self.assertEqual(row["add_at"], f"https://github.com/{row['github']}/settings/keys/new")
        record = self.store.read("dev")
        self.assertEqual(record["remote"], "xspeed-dev")
        self.assertEqual(record["routing"], {"front_door": "caddy", "mcp_port": 8765})
        self.assertEqual(record["prepare"], ["{repo:setup}/bin/prepare", "--commit", "{commit:hub}"])
        self.assertEqual(self.installs, [{"sb_path": "/srv/sandbox/sb", "sandbox_home": str(self.home)}])
        private = git.key_path(self.store.keys, "WPDevelopers/xspeed-hub")
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.keys.stat().st_mode & 0o777, 0o700)
        rendered = json.dumps(payload) + host_follow._render(payload)
        self.assert_no_private_material(rendered)
        self.assertEqual(self.audit_events(), ["add"])

    def test_re_add_replaces_the_declaration_and_keeps_state_and_keys(self):
        self.add()
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["applied"] = {"hub": HEAD_A, "setup": HEAD_A}
            self.store.write(record)
        payload = self.add(owner="someone-else")
        self.assertTrue(payload["replaced"])
        self.assertEqual([row["deploy_key"] for row in payload["repos"]], ["existing", "existing"])
        record = self.store.read("dev")
        self.assertEqual(record["owner"], "someone-else")
        self.assertEqual(record["state"]["applied"], {"hub": HEAD_A, "setup": HEAD_A})
        self.assertEqual(self.audit_events(), ["add", "replace"])

    def test_invalid_declarations_are_refused_before_anything_is_stored(self):
        cases = (({"follow_repo": ["hub=https://gitlab.com/x/y@dev"]}, "follow_repo_invalid"),
                 ({"follow_repo": ["hub=WPDevelopers/xspeed-hub@../dev"]}, "follow_branch_invalid"),
                 ({"stack_template": "/etc/stack.yml"}, "follow_template_invalid"),
                 ({"prepare": "run {repo:missing}"}, "follow_placeholder_invalid"),
                 ({"prepare_timeout": 5}, "follow_prepare_invalid"),
                 ({"prepare_timeout": 7201}, "follow_prepare_invalid"),
                 ({"owner": None}, "follow_owner_missing"))
        for fields, code in cases:
            with self.subTest(code=code), self.assertRaises(FollowError) as raised:
                self.add(**fields)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.store.names(), [])
        self.assertFalse(self.store.keys.exists() and any(self.store.keys.iterdir()))
        self.assertEqual(self.installs, [])
        self.assertEqual(self.audit_events(), [])

    def test_add_refuses_a_name_whose_apply_request_id_would_be_invalid(self):
        # Fable review 9: "follow-<name>-<16 hex>-apply" must be a valid job request id.
        from sandbox.jobs.models import _SAFE_NAME
        longest = "a" * 34
        self.assertRegex(f"follow-{longest}-{'0' * 16}-apply", _SAFE_NAME)
        with self.assertRaises(FollowError) as raised:
            self.add(name="a" * 35)
        self.assertEqual(raised.exception.code, "follow_name_invalid")
        self.assertEqual(self.store.names(), [])
        self.assertTrue(self.add(name=longest)["ok"])

    def test_the_prepare_limit_accepts_the_spec_maximum_of_7200_seconds(self):
        self.assertTrue(self.add(prepare_timeout=7200)["ok"])
        record = self.store.read("dev")
        self.assertEqual(record["prepare_timeout"], 7200)
        self.assertEqual(follow_spec.job_timeout(record), 7200 + follow_spec.APPLY_ALLOWANCE_SECONDS)

    def test_a_failed_timer_install_rolls_back_the_new_declaration_and_keys(self):
        with patch.object(units, "install", side_effect=FollowError("follow_timer_failed", "no")), \
                self.assertRaises(FollowError):
            self.add()
        self.assertEqual(self.store.names(), [])
        self.assertEqual(list(self.store.keys.iterdir()), [])
        self.assertEqual(self.audit_events(), [])

    def test_status_pause_resume_and_run_now(self):
        self.add()
        status = host_follow._status(owner_args("status"))
        self.assertEqual([follow["name"] for follow in status["follows"]], ["dev"])
        view = status["follows"][0]
        self.assertEqual([repo["deploy_key"] for repo in view["repos"]], ["present", "present"])
        self.assertEqual(view["repos"][0]["fingerprint"], "SHA256:fakefingerprint")
        self.assertTrue(status["timer"]["installed"])
        self.assertNotIn("public_key", json.dumps(status))

        paused = host_follow._pause(owner_args("pause"))
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["pause_reason"], "owner")
        with self.assertRaises(FollowError) as raised:
            host_follow._run_now(owner_args("run-now"))
        self.assertEqual(raised.exception.code, "follow_paused")
        self.assertEqual(self.starts, [])

        resumed = host_follow._resume(owner_args("resume"))
        self.assertFalse(resumed["paused"])
        self.assertFalse(resumed["retry"], "resume never retries failed heads")
        ran = host_follow._run_now(owner_args("run-now"))
        self.assertTrue(ran["retry"])
        self.assertTrue(ran["poll_started"])
        self.assertEqual(self.starts, [True])
        self.assertEqual(self.audit_events(), ["add", "status", "pause", "resume", "run_now"])

    def test_key_reprints_public_keys_only(self):
        self.add()
        payload = host_follow._key(owner_args("key"))
        self.assertEqual([row["deploy_key"] for row in payload["repos"]], ["existing", "existing"])
        self.assert_no_private_material(json.dumps(payload), host_follow._render(payload))
        self.assertEqual(self.audit_events(), ["add", "key"])

    def test_remove_refuses_a_running_attempt_then_keeps_keys_and_drops_the_last_timer(self):
        self.add()
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["attempt"] = {"id": "1" * 16, "status": "running"}
            self.store.write(record)
        with self.assertRaises(FollowError) as raised:
            host_follow._remove(owner_args("remove"))
        self.assertEqual(raised.exception.code, "follow_attempt_running")
        self.assertEqual(self.store.names(), ["dev"])
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["attempt"]["status"] = "failed"
            self.store.write(record)
        payload = host_follow._remove(owner_args("remove"))
        self.assertTrue(payload["timer"]["removed"])
        self.assertEqual(self.store.names(), [])
        self.assertTrue(git.key_path(self.store.keys, "WPDevelopers/xspeed-hub").exists())
        self.assertEqual(self.audit_events(), ["add", "remove"])

    def test_remove_keeps_the_timer_while_another_follow_exists(self):
        self.add()
        self.add(name="staging")
        payload = host_follow._remove(owner_args("remove", name="staging"))
        self.assertFalse(payload["timer"]["removed"])
        self.assertEqual(self.removals, [])


class JoinedRemoteTests(unittest.TestCase):
    def test_a_remote_without_a_retained_server_job_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FollowStore(Path(directory) / "follow")
            with patch("sandbox.hosting.server_cli_target.selected_owner_target", return_value=None), \
                    patch.object(host_follow, "_store", lambda: store), \
                    patch.object(units, "install") as install:
                for action in ("add", "status", "pause", "resume", "remove", "run-now", "key"):
                    handler = host_follow.HANDLERS[action]
                    with self.subTest(action=action), self.assertRaises(FollowError) as raised:
                        handler(add_args() if action == "add" else owner_args(action))
                    self.assertEqual(raised.exception.code, "follow_requires_joined_remote")
                install.assert_not_called()
            self.assertFalse(store.root.exists())

    def test_cli_accepts_follow_add_and_remove_and_refuses_a_non_joined_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"SANDBOX_HOME": directory, "SANDBOX_AUTO_UPDATE": "0"}
            for argv in (["add", "--remote", "nowhere", "--name", "dev", "--repo",
                          "hub=WPDevelopers/xspeed-hub@dev", "--stack-template", "{repo:hub}/stack.yml",
                          "--owner", "x", "--json"],
                         ["remove", "--remote", "nowhere", "--name", "dev", "--json"]):
                with self.subTest(action=argv[0]):
                    done = run_test_process([sys.executable, str(ROOT / "sb"), "host", "follow", *argv],
                                            capture_output=True, text=True, cwd=directory, env=env)
                    self.assertEqual(done.returncode, 1, done.stderr)
                    self.assertNotIn("only available with", done.stderr)
                    payload = json.loads(done.stdout.splitlines()[-1])
                    self.assertEqual(payload["code"], "follow_requires_joined_remote")
            self.assertFalse((Path(directory) / "runtime" / "hosting" / "follow" / "follows").exists())

    def test_cli_limits_follow_verbs_to_host_follow(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"SANDBOX_HOME": directory, "SANDBOX_AUTO_UPDATE": "0"}
            for argv, message in ((["image", "pause"], "only available with `host follow`"),
                                  (["stack", "run-now"], "only available with `host follow`"),
                                  (["status", "key"], "only available with `host follow`"),
                                  (["stack", "tick", "--here"], "only available with `host follow`"),
                                  (["follow", "renew"], "only available with `host tailnet-name`")):
                with self.subTest(argv=argv):
                    done = run_test_process([sys.executable, str(ROOT / "sb"), "host", *argv],
                                            capture_output=True, text=True, cwd=directory, env=env)
                    self.assertEqual(done.returncode, 2, done.stderr)
                    self.assertIn(message, done.stderr)

    def test_tick_needs_here(self):
        with self.assertRaises(FollowError) as raised:
            host_follow._tick(owner_args("tick"))
        self.assertEqual(raised.exception.code, "follow_tick_requires_here")


class FetchDetailTests(unittest.TestCase):
    def detail(self, stderr):
        return git._detail(subprocess.CompletedProcess(["git"], 128, "", stderr))

    def test_the_line_naming_the_cause_is_shown_not_git_boilerplate(self):
        tail = ("fatal: Could not read from remote repository.\n\n"
                "Please make sure you have the correct access rights\nand the repository exists.\n")
        self.assertEqual(self.detail("git@github.com: Permission denied (publickey).\n" + tail),
                         "git@github.com: Permission denied (publickey).")
        self.assertEqual(self.detail("No ED25519 host key is known for github.com and you have requested "
                                     "strict checking.\nHost key verification failed.\n" + tail),
                         "Host key verification failed.")
        self.assertEqual(self.detail("fatal: couldn't find remote ref refs/heads/nope\n"),
                         "fatal: couldn't find remote ref refs/heads/nope")
        self.assertEqual(self.detail("something odd\nlast line\n"), "last line")
        self.assertEqual(self.detail(""), "exit 128")


class TickTests(FollowCase):
    def setUp(self):
        super().setUp()
        self.add()
        self.heads = {"hub": HEAD_A, "setup": HEAD_A}
        self.submitted, self.checked_out = [], []
        self.stack = None
        self.busy_reason = (None, None)
        self.lifetime_value = None
        self.jobs = {}

    def fetch(self, repo, github, branch, *, key, known_hosts):
        self.assertEqual(key, git.key_path(self.store.keys, github))
        self.assertEqual(known_hosts, self.store.known_hosts)
        name = Path(repo).name
        value = self.heads[name]
        if isinstance(value, Exception):
            raise value
        return value

    def submit(self, argv):
        self.submitted.append(list(argv))
        return f"job-{len(self.submitted)}"

    def tick(self, **overrides):
        options = {"submit": self.submit, "capability": lambda: CAPABILITY,
                   "job_view": lambda job_id: self.jobs.get(job_id), "fetch": self.fetch,
                   "checkout": lambda repo, commit, branch=None: self.checked_out.append((Path(repo).name, commit)),
                   "stack_lookup": lambda _remote, _name: self.stack,
                   "busy": lambda _record, home: self.busy_reason,
                   "lifetime": lambda _record, home: self.lifetime_value, "home": lambda: str(self.home)}
        options.update(overrides)
        return follow_tick.Tick(self.store, **options).run()["follows"][0]

    def state(self):
        return self.store.read("dev")["state"]

    def finish_attempt(self, status):
        with self.store.lock("dev"):
            record = self.store.read("dev")
            state = record["state"]
            state["attempt"]["status"] = status
            if status == "succeeded":
                state["applied"] = state["attempt"]["heads"]
            else:
                state["failed_heads"] = state["attempt"]["heads"]
            self.store.write(record)

    def test_first_poll_applies_current_heads_once_then_unchanged_heads_submit_nothing(self):
        row = self.tick()
        self.assertEqual((row["action"], row["reason"]), ("start", "submitted"))
        self.assertEqual(len(self.submitted), 1)
        argv = self.submitted[0]
        attempt = self.state()["attempt"]
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["job_id"], "job-1")
        self.assertEqual(attempt["argv"], argv)
        self.assertEqual(argv[argv.index("--request-id") + 1], f"follow-dev-{attempt['id']}")
        self.assertEqual(argv[argv.index("--source-commit") + 1], HEAD_A)
        self.assertEqual(argv[argv.index("--server-cli-origin") + 1], "xspeed-dev")
        self.assertEqual(json.loads(argv[argv.index("--server-cli-target-metadata") + 1]),
                         {"front_door": "caddy", "mcp_port": 8765, "tailscale_host": "box.ts.net"})
        self.assertEqual(argv[argv.index("--") + 1:], ["host", "follow", "apply", "--remote", "xspeed-dev",
                                                       "--name", "dev", "--attempt", attempt["id"], "--json"])
        self.assertEqual(sorted(self.checked_out), [("hub", HEAD_A), ("setup", HEAD_A)])

        self.jobs["job-1"] = {"lifecycle": "running"}
        with patch.object(self.store, "apply_running", return_value=False):
            row = self.tick()
        self.assertEqual(row["reason"], "attempt_running")
        self.finish_attempt("succeeded")
        self.stack = {"project": "p", "environment": "e"}
        for _ in range(10):
            row = self.tick()
            self.assertEqual((row["action"], row["reason"]), ("skip", "up_to_date"))
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.state()["last_check"]["reason"], "up_to_date")

    def test_a_changed_head_starts_exactly_one_new_attempt(self):
        self.tick()
        self.finish_attempt("succeeded")
        self.heads["hub"] = HEAD_B
        row = self.tick()
        self.assertEqual(row["reason"], "submitted")
        self.assertEqual(row["heads"], {"hub": HEAD_B, "setup": HEAD_A})
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.state()["attempt"]["trigger"], "change")

    def test_a_busy_target_is_skipped_and_never_waited_on(self):
        self.busy_reason = ("target_lease_held", 42)
        row = self.tick()
        self.assertEqual((row["action"], row["reason"], row["busy_seconds"]), ("skip", "target_lease_held", 42))
        self.assertEqual(self.submitted, [])
        self.assertIsNone(self.state()["attempt"])
        self.assertEqual(self.state()["last_check"]["reason"], "target_lease_held")

    def test_target_busy_reports_a_lease_file_and_an_active_stack_apply(self):
        lease = Path(self.home) / ".sandbox" / "host-apply-p-e.lease"
        lease.parent.mkdir()
        lease.write_text(f"token owner {int(time.time()) - 30} apply\n")
        reason, age = follow_tick.target_busy({"project": "p", "environment": "e"}, home=str(self.home))
        self.assertEqual(reason, "target_lease_held")
        self.assertGreaterEqual(age, 30)
        self.assertTrue(lease.exists(), "a lease is never broken")
        lease.unlink()
        record = {"project": "p", "environment": "e", "slug": "dev", "last_apply": {"job_id": "j9"}}
        with patch.object(host_stack, "job_view", return_value={"lifecycle": "running"}):
            self.assertEqual(follow_tick.target_busy(record, home=str(self.home)), ("stack_apply_active", None))
        with patch.object(host_stack, "job_view", return_value={"lifecycle": "succeeded"}):
            self.assertEqual(follow_tick.target_busy(record, home=str(self.home)), (None, None))
        self.assertEqual(follow_tick.target_busy(None, home=str(self.home)), (None, None))

    def test_failed_heads_back_off_until_a_new_commit_or_run_now(self):
        self.tick()
        self.finish_attempt("failed")
        self.stack = {"project": "p", "environment": "e"}
        for _ in range(10):
            row = self.tick()
            self.assertEqual((row["action"], row["reason"]), ("skip", "waiting_after_failure"))
        self.assertEqual(len(self.submitted), 1)

        host_follow._run_now(owner_args("run-now"))
        row = self.tick()
        self.assertEqual(row["reason"], "submitted")
        self.assertEqual(self.state()["attempt"]["trigger"], "retry")
        self.assertFalse(self.state()["retry"], "run-now is one-shot")
        self.finish_attempt("failed")
        self.assertEqual(self.tick()["reason"], "waiting_after_failure")

        self.heads["setup"] = HEAD_C
        self.assertEqual(self.tick()["reason"], "submitted")
        self.assertEqual(len(self.submitted), 3)

    def test_a_fetch_failure_records_the_redacted_cause_and_starts_nothing(self):
        self.heads["setup"] = FollowError("follow_fetch_failed",
                                          "fetch of WPDevelopers/xspeed-dev-setup dev failed: Permission denied")
        row = self.tick()
        self.assertEqual((row["action"], row["reason"]), ("skip", "fetch_failed"))
        self.assertIn("Permission denied", row["error"])
        self.assertEqual(self.submitted, [])

    def test_paused_and_removed_stacks(self):
        host_follow._pause(owner_args("pause"))
        self.assertEqual(self.tick()["reason"], "paused")
        host_follow._resume(owner_args("resume"))
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["stack_seen"] = True
            self.store.write(record)
        row = self.tick()
        self.assertEqual((row["action"], row["reason"]), ("pause", "stack_removed"))
        self.assertTrue(self.state()["paused"])
        self.assertEqual(self.submitted, [])
        self.assertIn("auto_pause", self.audit_events())

    def test_an_unknown_submission_is_replayed_with_the_identical_request_only(self):
        def unknown(argv):
            self.submitted.append(list(argv))
            raise host_stack.StackCommandError(host_stack.SUBMISSION_UNKNOWN, "no receipt")
        row = self.tick(submit=unknown)
        self.assertEqual(row["reason"], "submission_unknown")
        attempt = self.state()["attempt"]
        self.assertEqual(attempt["status"], "submitting")
        self.heads["hub"] = HEAD_B  # a later commit never replaces the in-flight identity
        row = self.tick()
        self.assertTrue(row["replayed"])
        self.assertEqual(self.submitted[0], self.submitted[1])
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertEqual(self.state()["attempt"]["job_id"], "job-2")

    def test_an_attempt_job_that_ended_without_an_outcome_is_settled_as_failed(self):
        self.tick()
        self.jobs["job-1"] = {"lifecycle": "interrupted"}
        row = self.tick()
        self.assertEqual(row["action"], "settled")
        self.assertEqual(self.state()["attempt"]["status"], "failed")
        self.assertEqual(self.state()["failed_heads"], {"hub": HEAD_A, "setup": HEAD_A})

    def test_a_second_poll_while_one_runs_exits_busy(self):
        with self.store.host_lock() as acquired:
            self.assertTrue(acquired)
            result = follow_tick.Tick(self.store, submit=self.submit).run()
        self.assertEqual(result["status"], "skipped_busy")
        self.assertEqual(self.submitted, [])

    def test_attempt_audit_lines(self):
        self.tick()
        rows = [json.loads(line) for line in self.store.audit_path.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["add", "attempt_started", "attempt_submitted"])
        started, submitted = rows[1], rows[2]
        self.assertEqual(started["actor"], "follower")
        self.assertEqual(started["heads"], {"hub": HEAD_A, "setup": HEAD_A})
        self.assertEqual(started["request_id"], f"follow-dev-{started['attempt']}")
        self.assertEqual(submitted["job_id"], "job-1")
        self.assertEqual(rows[0]["actor"], "job:job-owner-1")
        self.assertEqual(rows[0]["repos"], ["hub=WPDevelopers/xspeed-hub@dev",
                                            "setup=WPDevelopers/xspeed-dev-setup@dev"])
        for row in rows:
            self.assertTrue(row["at"].endswith("Z"))
            self.assertEqual(row["follow"], "dev")
        self.assertEqual(self.store.audit_path.stat().st_mode & 0o777, 0o600)
        self.assert_no_private_material(self.store.audit_path.read_text())

    # Review fixes S2(b), S5 and M1 in the poll.

    def test_a_reaped_stack_pauses_its_follow_and_a_reaping_one_is_busy(self):
        self.stack = {"project": "p", "environment": "e"}
        self.lifetime_value = "reaping"
        row = self.tick()
        self.assertEqual((row["action"], row["reason"]), ("skip", "stack_reaping"))
        self.lifetime_value = "reaped"
        row = self.tick()
        self.assertEqual((row["action"], row["reason"]), ("pause", "stack_reaped"))
        self.assertEqual((self.state()["paused"], self.state()["pause_reason"]), (True, "stack_reaped"))
        self.assertEqual(self.submitted, [])

    def test_lifetime_state_reads_this_hosts_lifetime_record(self):
        from sandbox.hosting.lifetime import hoststore
        listing = {"records": [{"compose_project": "sandbox-host-other-e", "state": "active"},
                               {"compose_project": "sandbox-host-p-e", "state": "reaped"}]}
        with patch.object(hoststore.Store, "peek", return_value=listing) as peek:
            self.assertEqual(follow_tick.lifetime_state({"project": "p", "environment": "e"},
                                                        home=str(self.home)), "reaped")
            self.assertIsNone(follow_tick.lifetime_state({"project": "q", "environment": "e"},
                                                         home=str(self.home)))
        self.assertEqual(peek.call_count, 2)
        with patch.object(hoststore.Store, "peek", side_effect=OSError("unreadable")):
            self.assertIsNone(follow_tick.lifetime_state({"project": "p", "environment": "e"},
                                                         home=str(self.home)))
        self.assertIsNone(follow_tick.lifetime_state(None, home=str(self.home)))

    def test_an_unexpected_error_in_one_follow_is_recorded_and_the_others_still_poll(self):
        self.add(name="staging")

        def lookup(_remote, name):
            if name == "dev":
                raise OSError("stack registry unreadable")
            return None
        options = {"submit": self.submit, "capability": lambda: CAPABILITY, "job_view": lambda _j: None,
                   "fetch": self.fetch, "checkout": lambda *a, **k: None, "stack_lookup": lookup,
                   "busy": lambda _r, home: (None, None), "lifetime": lambda _r, home: None,
                   "home": lambda: str(self.home)}
        result = follow_tick.Tick(self.store, **options).run()
        rows = {row["name"]: row for row in result["follows"]}
        self.assertEqual((rows["dev"]["action"], rows["dev"]["reason"]), ("error", "follow_poll_error"))
        self.assertIn("OSError", rows["dev"]["error"])
        self.assertEqual(self.store.read("dev")["state"]["last_check"]["reason"], "follow_poll_error")
        self.assertEqual(rows["staging"]["reason"], "submitted")
        self.assertEqual(len(self.submitted), 1)

    def test_an_unanswered_job_view_keeps_the_attempt_then_settles_after_the_limit(self):
        self.tick()
        for _ in range(follow_tick.UNKNOWN_POLL_LIMIT):
            row = self.tick()  # self.jobs has no answer for job-1
            self.assertEqual((row["action"], row["reason"]), ("skip", "attempt_state_unknown"))
            self.assertEqual(self.state()["attempt"]["status"], "running")
            self.assertIsNone(self.state()["failed_heads"])
        row = self.tick()
        self.assertEqual(row["action"], "settled")
        self.assertEqual(self.state()["attempt"]["reason"], "attempt_ended_without_outcome:unknown")
        self.assertEqual(len(self.submitted), 1)


class AttemptTests(FollowCase):
    def setUp(self):
        super().setUp()
        self.add()
        self.heads = {"hub": HEAD_A, "setup": HEAD_A}
        self.attempt_id = "0123456789abcdef"
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["attempt"] = {"id": self.attempt_id, "heads": self.heads, "status": "running",
                                          "job_id": "job-1", "argv": ["x"]}
            self.store.write(record)
        template = self.store.repo_dir("dev", "setup") / "stack.yml"
        template.parent.mkdir(parents=True)
        template.write_text("stack: {}\n")
        self.calls, self.prepared = [], []
        self.plan_hosts = ["dev.xspeedcdn.online"]
        self.lifecycles = ["running", "succeeded"]
        self.stack = {"project": "p", "environment": "e"}
        self.busy_answers = []  # popped per target check; (None, None) once empty
        self.lifetime_value = None
        self.predecessor_value = None  # what the target's open earlier delivery looks like
        self.retired, self.retire_error = [], None

    def stack_up(self, namespace):
        self.calls.append(namespace)
        if namespace.plan:
            return {"slug": "dev", "hostnames": list(self.plan_hosts)}
        return {"job_id": "apply-job-7"}

    def job_view(self, _job_id):
        return {"lifecycle": self.lifecycles.pop(0) if len(self.lifecycles) > 1 else self.lifecycles[0],
                "exit_code": getattr(self, "exit_code", 1)}

    def busy(self, _record, home):
        return self.busy_answers.pop(0) if self.busy_answers else (None, None)

    def run_attempt(self):
        attempt = Attempt(self.store, context_for=lambda _remote: self.context, stack_up=self.stack_up,
                          job_view=self.job_view,
                          prepare=lambda argv, **kw: self.prepared.append((argv, kw)),
                          head=lambda directory: self.heads[Path(directory).name],
                          environment=lambda _context: {"PATH": "/usr/bin"}, sleep=lambda _s: None,
                          stack_lookup=lambda _remote, _name: self.stack, busy=self.busy,
                          lifetime=lambda _record, home: self.lifetime_value, home=lambda: str(self.home),
                          predecessor=lambda _record, _stack: self.predecessor_value, retire=self.retire)
        return attempt.run("xspeed-dev", "dev", self.attempt_id)

    def retire(self, record, predecessor):
        if self.retire_error is not None:
            raise self.retire_error
        self.retired.append(predecessor["request_id"])
        return {"request_id": predecessor["request_id"], "execution_state": "interrupted"}

    def own_interrupted_request(self, attempt_id="fedcba9876543210", job_id="job-earlier"):
        """An earlier attempt of this follow whose nested apply was interrupted."""
        from sandbox.application.server_cli import nested_request_id
        self.store.audit("attempt_submitted", "dev", actor="follower", attempt=attempt_id, job_id=job_id)
        return nested_request_id(job_id, f"follow-dev-{attempt_id}-apply")

    def test_a_successful_attempt_prepares_plans_confirms_waits_and_records_applied_heads(self):
        result = self.run_attempt()
        self.assertTrue(result["ok"])
        self.assertEqual(result["apply_job_id"], "apply-job-7")
        [(argv, options)] = self.prepared
        setup = str(self.store.repo_dir("dev", "setup"))
        self.assertEqual(argv, [f"{setup}/bin/prepare", "--commit", HEAD_A])
        self.assertEqual(options["env"]["SANDBOX_FOLLOW_COMMIT_HUB"], HEAD_A)
        self.assertEqual(options["timeout"], 1200)
        self.assertEqual([call.plan for call in self.calls], [True, False])
        self.assertTrue(self.calls[1].confirm)
        self.assertEqual(self.calls[1].request_id, f"follow-dev-{self.attempt_id}-apply")
        state = self.store.read("dev")["state"]
        self.assertEqual(state["applied"], self.heads)
        self.assertIsNone(state["failed_heads"])
        self.assertTrue(state["stack_seen"])
        self.assertEqual(state["attempt"]["status"], "succeeded")
        self.assertEqual(self.audit_events()[-1], "attempt_finished")

    def test_an_apex_hostname_is_refused_before_confirm(self):
        self.plan_hosts = ["dev.xspeedcdn.online", "xspeedcdn.online"]
        result = self.run_attempt()
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"].startswith("follow_apex_hostname_refused"), result["reason"])
        self.assertEqual([call.plan for call in self.calls], [True])
        state = self.store.read("dev")["state"]
        self.assertEqual(state["failed_heads"], self.heads)
        self.assertIsNone(state["applied"])

    def test_a_failed_stack_apply_records_failed_heads(self):
        self.lifecycles = ["failed"]
        result = self.run_attempt()
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"].startswith("stack_apply_failed"))
        self.assertEqual(self.store.read("dev")["state"]["failed_heads"], self.heads)

    def test_a_busy_compose_publish_lock_is_retried_not_failed(self):
        from sandbox.hosting.ports import LOCK_BUSY_EXIT
        self.lifecycles, self.exit_code = ["failed"], LOCK_BUSY_EXIT
        result = self.run_attempt()
        self.assertEqual(result["status"], "busy")
        self.assertTrue(result["reason"].startswith("hosting_compose_publish_busy"), result["reason"])
        state = self.store.read("dev")["state"]
        self.assertIsNone(state["failed_heads"])
        self.assertIsNone(state["applied"])

    def test_a_moved_checkout_is_refused(self):
        self.heads = {"hub": HEAD_A, "setup": HEAD_B}
        result = self.run_attempt()
        self.assertTrue(result["reason"].startswith("follow_source_changed"))
        self.assertEqual(self.calls, [])

    def test_apply_outside_its_server_job_and_with_another_attempt_is_refused(self):
        with patch("sandbox.hosting.server_cli_target.selected_owner_target", return_value=None), \
                self.assertRaises(FollowError) as raised:
            Attempt(self.store).run("xspeed-dev", "dev", self.attempt_id)
        self.assertEqual(raised.exception.code, "follow_apply_requires_server_job")
        attempt = Attempt(self.store, context_for=lambda _remote: self.context)
        with self.assertRaises(FollowError) as raised:
            attempt.run("xspeed-dev", "dev", "f" * 16)
        self.assertEqual(raised.exception.code, "follow_attempt_mismatch")

    # Review fixes S1, S2(a) and S3 inside the attempt job.

    def test_a_target_that_turns_busy_before_confirm_is_a_skip_retried_next_poll(self):
        self.busy_answers = [(None, None), (None, None), ("stack_apply_active", None)]
        result = self.run_attempt()
        self.assertEqual((result["status"], result["reason"]), ("busy", "stack_apply_active"))
        self.assertEqual([call.plan for call in self.calls], [True], "confirm never ran")
        state = self.store.read("dev")["state"]
        self.assertIsNone(state["failed_heads"])
        self.assertIsNone(state["applied"])
        self.assertEqual(state["attempt"]["status"], "busy")
        row = follow_tick.Tick(self.store, submit=lambda argv: "job-2", capability=lambda: CAPABILITY,
                               job_view=lambda _job: None, fetch=lambda *a, **k: HEAD_A,
                               checkout=lambda *a, **k: None, stack_lookup=lambda *_a: self.stack,
                               busy=lambda _r, home: (None, None), lifetime=lambda _r, home: None,
                               home=lambda: str(self.home)).run()["follows"][0]
        self.assertEqual((row["action"], row["reason"]), ("start", "submitted"))

    def test_a_busy_target_before_prepare_skips_prepare(self):
        self.busy_answers = [("target_lease_held", 12)]
        result = self.run_attempt()
        self.assertEqual(result["status"], "busy")
        self.assertEqual((self.prepared, self.calls), ([], []))

    def test_a_busy_refusal_from_stack_up_is_a_skip_and_keeps_a_run_now_retry(self):
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["attempt"]["trigger"] = "retry"
            self.store.write(record)

        def refusing(namespace):
            self.calls.append(namespace)
            if namespace.plan:
                return {"slug": "dev", "hostnames": ["dev.xspeedcdn.online"]}
            raise host_stack.StackCommandError(host_stack.APPLY_IN_PROGRESS, "apply job j is running")
        self.stack_up = refusing
        result = self.run_attempt()
        self.assertEqual(result["status"], "busy")
        self.assertTrue(result["reason"].startswith("stack_apply_in_progress"))
        state = self.store.read("dev")["state"]
        self.assertIsNone(state["failed_heads"])
        self.assertTrue(state["retry"], "the owner's run-now is still owed")

    def test_a_nested_apply_that_fails_while_the_target_is_busy_is_a_skip(self):
        self.lifecycles = ["failed"]
        self.busy_answers = [(None, None), (None, None), (None, None), ("target_lease_held", 3)]
        result = self.run_attempt()
        self.assertEqual(result["status"], "busy")
        self.assertIn("stack_apply_failed", result["reason"])
        self.assertIsNone(self.store.read("dev")["state"]["failed_heads"])

    def test_a_stack_removed_during_the_attempt_pauses_the_follow_and_is_never_recreated(self):
        with self.store.lock("dev"):
            record = self.store.read("dev")
            record["state"]["stack_seen"] = True
            self.store.write(record)

        def removed_after_plan(namespace):
            self.calls.append(namespace)
            if namespace.plan:
                self.stack = None  # `host stack down` while the plan ran
                return {"slug": "dev", "hostnames": ["dev.xspeedcdn.online"]}
            raise AssertionError("confirm must not run for a removed stack")
        self.stack_up = removed_after_plan
        result = self.run_attempt()
        self.assertEqual((result["status"], result["reason"]), ("removed", "stack_removed"))
        state = self.store.read("dev")["state"]
        self.assertEqual((state["paused"], state["pause_reason"]), (True, "stack_removed"))
        self.assertIsNone(state["failed_heads"])
        self.assertEqual(self.audit_events()[-2:], ["attempt_finished", "auto_pause"])

    def test_a_stack_reaped_or_reaping_during_the_attempt(self):
        self.lifetime_value = "reaped"
        result = self.run_attempt()
        self.assertEqual((result["status"], result["reason"]), ("removed", "stack_reaped"))
        self.assertTrue(self.store.read("dev")["state"]["paused"])
        self.assertEqual(self.calls, [])

    def test_the_attempt_passes_the_template_repositorys_tracked_branch_as_its_source(self):
        self.run_attempt()
        self.assertEqual([call.follow_source for call in self.calls],
                         [{"branch": "dev", "commit": HEAD_A}] * 2)
        record = self.store.read("dev")
        self.assertIsNone(follow_apply.follow_source({**record, "template": "{dir}/stack.yml"}, self.heads))


class InterruptedPredecessorTests(AttemptTests):
    """feedback 4c09145b: an attempt interrupted mid-apply fenced every later one."""

    def test_the_follows_own_interrupted_delivery_is_retired_and_the_apply_goes_on(self):
        request = self.own_interrupted_request()
        self.predecessor_value = {"request_id": request, "validated": {}}
        result = self.run_attempt()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.retired, [request])
        self.assertEqual([call.plan for call in self.calls], [True, False])
        self.assertIn("predecessor_retired", self.audit_events())

    def test_another_owners_interrupted_delivery_is_kept_and_status_names_the_command(self):
        self.own_interrupted_request()
        self.predecessor_value = {"request_id": "nested-" + "0" * 32, "validated": {}}
        result = self.run_attempt()
        self.assertFalse(result["ok"])
        self.assertEqual((self.retired, self.calls, self.prepared), ([], [], []))
        command = ("sb host retire-delivery --name dev --remote xspeed-dev "
                   "--original-request-id nested-" + "0" * 32 + " --confirm")
        self.assertTrue(result["reason"].startswith("follow_predecessor_not_owned"), result["reason"])
        self.assertIn(command, result["reason"])
        state = self.store.read("dev")["state"]
        self.assertEqual(state["failed_heads"], self.heads)
        self.assertEqual(state["last_check"]["next_command"], command)
        self.assertIn(command, state["last_check"]["reason"])
        # The next poll waits on the same heads and keeps the way out in its last check.
        row = follow_tick.Tick(self.store, submit=lambda argv: "job-2", capability=lambda: CAPABILITY,
                               job_view=lambda _job: None, fetch=lambda *a, **k: HEAD_A,
                               checkout=lambda *a, **k: None, stack_lookup=lambda *_a: self.stack,
                               busy=lambda _r, home: (None, None), lifetime=lambda _r, home: None,
                               home=lambda: str(self.home)).run()["follows"][0]
        self.assertEqual(row["action"], "skip")
        self.assertIn(command, row["reason"])
        self.assertEqual(self.store.read("dev")["state"]["last_check"]["next_command"], command)

    def test_the_current_attempts_own_job_counts_as_its_own(self):
        from sandbox.application.server_cli import nested_request_id
        # The attempt records its own job id as it starts.
        request = nested_request_id(self.context.job_id, f"follow-dev-{self.attempt_id}-apply")
        self.predecessor_value = {"request_id": request, "validated": {}}
        self.assertTrue(self.run_attempt()["ok"])
        self.assertEqual(self.retired, [request])

    def test_a_failed_retirement_names_the_command_and_applies_nothing(self):
        from sandbox.delivery.models import DeliveryError
        request = self.own_interrupted_request()
        self.predecessor_value = {"request_id": request, "validated": {}}
        self.retire_error = DeliveryError("binding_mismatch")
        result = self.run_attempt()
        self.assertTrue(result["reason"].startswith("follow_predecessor_retire_failed"), result["reason"])
        self.assertIn("binding_mismatch", result["reason"])
        self.assertIn(f"--original-request-id {request} --confirm", result["reason"])
        self.assertEqual(self.calls, [])

    def test_a_retirement_whose_host_state_cleanup_timed_out_says_what_to_inspect(self):
        from sandbox.commands.hosting import RetireCleanupPending
        request = self.own_interrupted_request()
        self.predecessor_value = {"request_id": request, "validated": {}}
        self.retire_error = RetireCleanupPending({"request_id": request})
        result = self.run_attempt()
        self.assertTrue(result["reason"].startswith("follow_predecessor_cleanup_pending"), result["reason"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.read("dev")["state"]["last_check"]["next_command"],
                         "sb host status --name dev --remote xspeed-dev")

    def test_a_predecessor_whose_job_still_runs_is_busy_not_failed(self):
        self.predecessor_value = {"busy": "predecessor_delivery_running"}
        result = self.run_attempt()
        self.assertEqual((result["status"], result["reason"]), ("busy", "predecessor_delivery_running"))
        self.assertIsNone(self.store.read("dev")["state"]["failed_heads"])

    def test_the_derived_id_is_the_one_the_nested_apply_is_admitted_under(self):
        from sandbox.application import server_cli
        owner = SimpleNamespace(executable="/srv/sb", target_identity="t", runtime_revision="r",
                                ownership_marker="m")
        context = SimpleNamespace(owner=owner, job_id="job-earlier", deadline_at="2099-01-01T00:00:00+00:00",
                                  origin_remote_name="xspeed-dev", target_metadata={})
        with patch.object(server_cli, "validate_owner_current", return_value=context), \
                patch.object(server_cli, "installed_cli", return_value=Path("/srv/sb")):
            argv = server_cli.local_server_cli_submission_argv(
                owner, ("host", "apply"), project_dir=str(self.home), source_commit=HEAD_A,
                request_id=follow_apply.apply_request_id("dev", "fedcba9876543210"),
                timeout_seconds=60, parent_job_id="job-earlier")
        admitted = argv[argv.index("--request-id") + 1]
        self.assertEqual(admitted, self.own_interrupted_request())
        self.assertIn(admitted, follow_apply.owned_requests(self.store, self.store.read("dev")))


class ReviewCommandTests(FollowCase):
    """Review fixes S4, M4 and M5 in the owner commands."""

    def run_command(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            host_follow.cmd_host_follow(args)
        self.assertEqual(raised.exception.code, 1)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertNotIn("Traceback", out.getvalue())
        return json.loads(lines[0])

    def test_an_unexpected_error_is_one_json_refusal(self):
        self.add()
        with patch.object(self.store, "audit_tail", side_effect=OSError(13, "Permission denied")):
            payload = self.run_command(owner_args("status"))
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["code"], "follow_internal_error")
        self.assertIn("PermissionError", payload["error"])

    def test_a_refused_owner_command_is_audited_with_its_code(self):
        self.add()
        host_follow._pause(owner_args("pause"))
        payload = self.run_command(owner_args("run-now"))
        self.assertEqual(payload["code"], "follow_paused")
        line = json.loads(self.store.audit_path.read_text().splitlines()[-1])
        self.assertEqual((line["event"], line["command"], line["code"], line["actor"]),
                         ("refused", "run-now", "follow_paused", "job:job-owner-1"))

    def test_the_timer_changes_under_the_units_lock(self):
        held = []

        def probe(*_args, **_kwargs):
            with self.store._flock("units", blocking=False) as acquired:
                held.append(not acquired)
            return {"timer": units.TIMER, "changed": True, "removed": True}
        with patch.object(units, "install", side_effect=probe), patch.object(units, "remove", side_effect=probe):
            self.add()
            host_follow._remove(owner_args("remove"))
        self.assertEqual(held, [True, True])


class OwnerTargetTests(unittest.TestCase):
    def test_a_changed_retained_context_is_a_json_refusal_not_a_traceback(self):
        from sandbox.hosting.server_cli_target import OwnerTargetUnavailable
        from sandbox.application.server_cli import ServerCLIEffectUnknown
        for error, code in ((OwnerTargetUnavailable("retained context changed"),
                             "server_cli_owner_target_unavailable"),
                            (ServerCLIEffectUnknown("effect unknown"), "server_cli_effect_unknown")):
            def require_current(error=error):
                raise error
            target = SimpleNamespace(require_current=require_current)
            with self.subTest(code=code), \
                    patch("sandbox.hosting.server_cli_target.selected_owner_target", return_value=target):
                with self.assertRaises(FollowError) as raised:
                    follow_apply._owner_context("xspeed-dev")
                self.assertEqual(raised.exception.code, code)
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    host_follow.cmd_host_follow(owner_args("status"))
                payload = json.loads(out.getvalue().splitlines()[-1])
                self.assertEqual((payload["ok"], payload["code"]), (False, code))


class GitCommandShapeTests(unittest.TestCase):
    """The real fetch and checkout argv, with every subprocess faked (no network)."""

    def test_fetch_uses_the_follow_key_the_pinned_host_key_and_one_refspec(self):
        calls = []

        def run(argv, *, timeout=60, env=None, cwd=None):
            calls.append((list(argv), dict(env or {})))
            stdout = HEAD_A + "\n" if "rev-parse" in argv else ""
            return subprocess.CompletedProcess(argv, 1 if argv[-2:] == ["get-url", "origin"] else 0, stdout, "")
        with tempfile.TemporaryDirectory() as directory, patch.object(git, "_run", side_effect=run):
            repo, key, known = Path(directory) / "repo", Path(directory) / "keys" / "k", Path(directory) / "kh"
            self.assertEqual(git.fetch(repo, "WPDevelopers/xspeed-hub", "dev", key=key, known_hosts=known), HEAD_A)
        prefix = ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo)]
        argvs = [argv for argv, _env in calls]
        self.assertIn(prefix + ["remote", "add", "origin", "git@github.com:WPDevelopers/xspeed-hub.git"], argvs)
        [(fetch, env)] = [(argv, env) for argv, env in calls if "fetch" in argv]
        self.assertEqual(fetch, prefix + ["fetch", "--no-tags", "--quiet", "origin",
                                          "+refs/heads/dev:refs/remotes/origin/dev"])
        ssh = shlex.split(env["GIT_SSH_COMMAND"])
        self.assertEqual(ssh[:3], ["ssh", "-i", str(key)])
        options = {ssh[index + 1] for index, item in enumerate(ssh) if item == "-o"}
        self.assertTrue({"IdentitiesOnly=yes", "IdentityAgent=none", "PasswordAuthentication=no",
                         f"UserKnownHostsFile={known}", "GlobalKnownHostsFile=/dev/null",
                         "StrictHostKeyChecking=yes", "BatchMode=yes"} <= options, options)
        self.assertEqual(ssh[-2:], ["-F", "/dev/null"])
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_checkout_names_the_tracked_branch(self):
        self.assertEqual(git.checkout_argv(HEAD_A, "dev"), ["checkout", "--quiet", "--force", "-B", "dev", HEAD_A])
        self.assertEqual(git.checkout_argv(HEAD_A, None), ["checkout", "--quiet", "--detach", "--force", HEAD_A])
        with self.assertRaises(FollowError):
            git.checkout_argv(HEAD_A, "-f")

    def test_a_named_checkout_makes_head_resolve_to_the_tracked_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            env = {"HOME": directory, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
            for argv in (["init", "--quiet", "--initial-branch=main"],
                         ["commit", "--quiet", "--allow-empty", "-m", "one"]):
                run_test_process(["git", "-C", str(repo), *argv], env=env, capture_output=True, check=True)
            commit = run_test_process(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env,
                                      capture_output=True, text=True, check=True).stdout.strip()
            git.checkout(repo, commit, "dev")
            self.assertEqual(git.head(repo), commit)
            self.assertEqual(host_stack.resolve_ref(repo, "HEAD").ref_branch, "dev")

    def test_key_files_of_different_repositories_never_collide(self):
        keys = Path("/srv/keys")
        first, second = git.key_path(keys, "o/x"), git.key_path(keys, "o/x.pub")
        self.assertNotEqual(first, second)
        self.assertNotIn(second, (first, first.with_name(first.name + ".pub")))
        self.assertNotIn(first, (second, second.with_name(second.name + ".pub")))
        self.assertEqual(git.key_path(keys, "O/X"), first)
        self.assertRegex(first.name, r"^github-[0-9a-f]{32}$")


class UnitTextTests(unittest.TestCase):
    def test_the_service_and_timer_text(self):
        rendered = units.render(sb_path="/srv/sandbox/sb", sandbox_home="/srv/home")
        service, timer = rendered[units.SERVICE].splitlines(), rendered[units.TIMER].splitlines()
        for line in ("Type=oneshot", "UMask=0077", f"TimeoutStartSec={units.TICK_BUDGET_SECONDS + 120}s",
                     "Environment=SANDBOX_HOME=/srv/home",
                     "ExecStart=/srv/sandbox/sb host follow tick --here --json"):
            self.assertIn(line, service)
        for line in ("OnBootSec=2min", "OnUnitActiveSec=5min", f"Unit={units.SERVICE}",
                     "WantedBy=timers.target"):
            self.assertIn(line, timer)
        for unsafe in ("relative/sb", "/srv/sb; rm -rf /", "/srv/s b"):
            with self.subTest(unsafe=unsafe), self.assertRaises(FollowError):
                units.render(sb_path=unsafe, sandbox_home="/srv/home")


class PrepareRunnerTests(unittest.TestCase):
    """run_prepare against real fake scripts: no shell, exit codes, timeout and cancel."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.env = dict(synthetic_environment())

    def script(self, body):
        path = self.work / "prepare.py"
        path.write_text("import os, signal, sys, time\n" + body)
        return [sys.executable, "-I", str(path)]

    def test_arguments_reach_the_program_without_a_shell(self):
        argv = self.script("open('out.txt', 'w').write('\\n'.join(sys.argv[1:]) + '\\n' + os.getcwd())\n")
        run_prepare(argv + ["$HOME", "a b;c", "*"], cwd=self.work, env=self.env, timeout=60)
        lines = (self.work / "out.txt").read_text().splitlines()
        self.assertEqual(lines[:3], ["$HOME", "a b;c", "*"])
        self.assertEqual(Path(lines[3]).resolve(), self.work.resolve())

    def test_a_non_zero_exit_fails_with_its_code(self):
        with self.assertRaises(FollowError) as raised:
            run_prepare(self.script("sys.exit(3)\n"), cwd=self.work, env=self.env, timeout=60)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("follow_prepare_failed", "prepare exited 3"))

    def test_a_prepare_past_its_limit_is_stopped(self):
        started = time.monotonic()
        with self.assertRaises(FollowError) as raised:
            run_prepare(self.script("time.sleep(60)\n"), cwd=self.work, env=self.env, timeout=1, grace=2)
        self.assertEqual(raised.exception.code, "follow_prepare_timeout")
        self.assertLess(time.monotonic() - started, 30)

    @unittest.skipUnless(threading.current_thread() is threading.main_thread(),
                         "the prepare cancel handler is installed only on the main thread")
    def test_a_cancelled_attempt_job_stops_prepare_first(self):
        # The fake prepare plays the job supervisor: it sends SIGTERM to the
        # attempt process (its parent), then would run for a minute.
        argv = self.script("time.sleep(0.5)\nopen('pid', 'w').write(str(os.getpid()))\n"
                           "os.kill(os.getppid(), signal.SIGTERM)\ntime.sleep(60)\n")
        before = signal.getsignal(signal.SIGTERM)
        started = time.monotonic()
        with self.assertRaises(FollowError) as raised:
            run_prepare(argv, cwd=self.work, env=self.env, timeout=120, cancel_grace=2)
        self.assertEqual(raised.exception.code, "follow_prepare_cancelled")
        self.assertLess(time.monotonic() - started, 30)
        self.assertIs(signal.getsignal(signal.SIGTERM), before, "the handler is restored")
        pid = int((self.work / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def assert_gone(self, pid):
        """The process no longer runs (a zombie waiting for its reaper counts as gone)."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            stat = Path(f"/proc/{pid}/stat")
            with contextlib.suppress(OSError):
                if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    return
            time.sleep(0.1)
        self.fail(f"process {pid} still runs")

    @unittest.skipUnless(threading.current_thread() is threading.main_thread(),
                         "the prepare cancel handler is installed only on the main thread")
    def test_a_cancel_during_the_timeout_stop_still_kills_the_prepare_group(self):
        # Prepare ignores the group SIGTERM and, on receiving it, plays the job
        # supervisor by stopping the attempt process mid-cleanup.
        argv = self.script("open('pid', 'w').write(str(os.getpid()))\n"
                           "signal.signal(signal.SIGTERM, lambda *_: os.kill(os.getppid(), signal.SIGTERM))\n"
                           "time.sleep(60)\ntime.sleep(60)\n")
        before = signal.getsignal(signal.SIGTERM)
        started = time.monotonic()
        with self.assertRaises(FollowError) as raised:
            run_prepare(argv, cwd=self.work, env=self.env, timeout=1, grace=2, cancel_grace=2)
        self.assertEqual(raised.exception.code, "follow_prepare_timeout")
        self.assertLess(time.monotonic() - started, 30)
        self.assertIs(signal.getsignal(signal.SIGTERM), before, "the handler is restored")
        self.assert_gone(int((self.work / "pid").read_text()))

    @unittest.skipUnless(threading.current_thread() is threading.main_thread(),
                         "the prepare cancel handler is installed only on the main thread")
    def test_a_cancel_just_after_prepare_exits_is_recorded_and_kills_its_group(self):
        # Prepare leaves a child in its group that ignores SIGTERM, then exits 0.
        # The stop signal lands right as its exit is seen.
        child = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open('ready', 'w'); time.sleep(60)"
        argv = self.script("import subprocess\n"
                           f"kid = subprocess.Popen([sys.executable, '-I', '-c', {child!r}])\n"
                           "while not os.path.exists('ready'):\n    time.sleep(0.05)\n"
                           "open('pid', 'w').write(str(kid.pid))\n")
        real_exited = follow_apply._exited

        def exited_then_stopped(process):
            done = real_exited(process)
            if done:
                os.kill(os.getpid(), signal.SIGTERM)
            return done

        before = signal.getsignal(signal.SIGTERM)
        started = time.monotonic()
        with patch.object(follow_apply, "_exited", side_effect=exited_then_stopped), \
                self.assertRaises(FollowError) as raised:
            run_prepare(argv, cwd=self.work, env=self.env, timeout=60, grace=2, cancel_grace=2)
        self.assertEqual(raised.exception.code, "follow_prepare_cancelled")
        self.assertLess(time.monotonic() - started, 30)
        self.assertIs(signal.getsignal(signal.SIGTERM), before, "the handler is restored")
        self.assert_gone(int((self.work / "pid").read_text()))


class ApexTests(unittest.TestCase):
    def test_a_registrable_domain_is_an_apex(self):
        from sandbox.hosting.follow.spec import is_apex
        for name in ("xspeedcdn.online", "example.com", "example.co.uk", "example.com.bd", "EXAMPLE.CO.UK."):
            with self.subTest(name=name):
                self.assertTrue(is_apex(name))
        for name in ("dev.xspeedcdn.online", "dev.example.co.uk", "a.b.example.com", "co.example.io"):
            with self.subTest(name=name):
                self.assertFalse(is_apex(name))


class AttemptScopeTests(unittest.TestCase):
    """feedback 642bc505: the attempt job must not live in the poll unit's cgroup."""

    ARGV = ["/home/u/sandbox/sb-src/sb", "job-start", "--local", "--server-cli", "--json", "--",
            "host", "follow", "apply", "--remote", "vps", "--name", "pr-1",
            "--attempt", "0123456789abcdef", "--json"]

    def test_submission_runs_job_start_in_its_own_user_scope(self):
        seen = {}

        def submit(argv, *, environment=None):
            seen.update(argv=argv, environment=environment)
            return "a" * 32

        with patch.object(follow_tick.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(follow_tick, "scope_available", return_value=True), \
                patch.object(host_stack, "_submit", submit), \
                patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1001"}):
            self.assertEqual(follow_tick.default_submit(list(self.ARGV)), "a" * 32)
        argv = seen["argv"]
        self.assertEqual(argv[:5], ["systemd-run", "--user", "--scope", "--quiet", "--collect"])
        self.assertRegex(argv[5], r"^--unit=sandbox-follow-0123456789abcdef-[0-9a-f]{8}$")
        # The manager variables reach systemd-run only, never the attempt.
        self.assertEqual(argv[argv.index("--") + 1:],
                         ["sh", "-c", follow_tick.ATTEMPT_UMASK_SH, "sandbox-follow-attempt",
                          "env", "-u", "XDG_RUNTIME_DIR", "-u", "DBUS_SESSION_BUS_ADDRESS", *self.ARGV])
        self.assertEqual(seen["environment"]["XDG_RUNTIME_DIR"], "/run/user/1001")
        # Each submission gets its own scope, so a replay never collides with a live one.
        self.assertNotEqual(follow_tick.scoped_argv(self.ARGV)[5], follow_tick.scoped_argv(self.ARGV)[5])

    def test_without_systemd_run_it_refuses_instead_of_staying_in_the_poll(self):
        with patch.object(follow_tick.shutil, "which", return_value=None), \
                patch.object(host_stack, "_submit") as submit:
            with self.assertRaises(host_stack.StackCommandError) as raised:
                follow_tick.default_submit(list(self.ARGV))
        self.assertEqual(raised.exception.code, host_stack.SUBMISSION_FAILED)
        submit.assert_not_called()

    def test_a_scope_that_cannot_start_is_a_refusal_not_an_endless_replay(self):
        with patch.object(follow_tick.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(follow_tick.subprocess if hasattr(follow_tick, "subprocess") else subprocess,
                             "run", return_value=SimpleNamespace(returncode=1)), \
                patch.object(host_stack, "_submit") as submit:
            with self.assertRaises(host_stack.StackCommandError) as raised:
                follow_tick.default_submit(list(self.ARGV))
        self.assertEqual(raised.exception.code, host_stack.SUBMISSION_FAILED)
        submit.assert_not_called()

    def test_the_attempt_survives_its_oneshot_poll_unit(self):
        """A real oneshot user unit (like sandbox-stack-follow.service) starts a
        detached process the way job-start starts its supervisor, then exits."""
        environment = synthetic_environment(follow_tick.user_manager_environment())
        if shutil.which("systemd-run") is None or subprocess.run(
                ["systemctl", "--user", "show-environment"], capture_output=True,
                env=environment, timeout=10).returncode != 0:
            self.skipTest("no systemd user manager")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spawn = root / "spawn.py"
            # What job-start does: launch a detached supervisor, print, exit.
            spawn.write_text(
                "import subprocess, sys\n"
                "p = subprocess.Popen(['sleep', '120'], start_new_session=True,\n"
                "                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n"
                "open(sys.argv[1], 'w').write(str(p.pid))\n")
            poll = root / "poll.py"
            poll.write_text(
                "import json, subprocess, sys\n"
                "argv = json.loads(sys.argv[1])\n"
                "raise SystemExit(subprocess.run(argv).returncode)\n")
            results = {}
            for variant in ("scoped", "unscoped"):
                pidfile = root / f"{variant}.pid"
                inner = [sys.executable, str(spawn), str(pidfile), "--attempt", "0123456789abcdef"]
                argv = follow_tick.scoped_argv(inner) if variant == "scoped" else inner
                unit = f"sandbox-test-follow-poll-{secrets.token_hex(4)}"
                completed = subprocess.run(
                    ["systemd-run", "--user", "--wait", "--quiet", "--collect", f"--unit={unit}",
                     "-p", "Type=oneshot", "-p", "KillMode=control-group",
                     "--setenv=XDG_RUNTIME_DIR=" + environment["XDG_RUNTIME_DIR"],
                     "--", sys.executable, str(poll), json.dumps(argv)],
                    capture_output=True, text=True, env=environment, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                pid = int(pidfile.read_text())
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                if alive:
                    def stop(pid=pid):
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(pid, signal.SIGKILL)
                    self.addCleanup(stop)
                results[variant] = alive
        # The unscoped control reproduces the bug; the scoped attempt survives.
        self.assertEqual(results, {"scoped": True, "unscoped": False})


class AttemptModeTests(unittest.TestCase):
    """feedback d96eaf4c: under the poll's UMask=0077 a follow must build from the
    same file modes as a controller apply (umask 0022)."""

    @staticmethod
    def _modes(root: Path) -> dict:
        out = {}
        for path in sorted(root.rglob("*")):
            if ".git" in path.relative_to(root).parts:
                continue
            out[str(path.relative_to(root))] = "link" if path.is_symlink() else oct(os.lstat(path).st_mode & 0o7777)
        return out

    def _git(self, *argv, cwd=None, umask=0o022):
        old = os.umask(umask)
        try:
            return subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True, text=True,
                                  env=synthetic_environment({"GIT_CONFIG_NOSYSTEM": "1",
                                                             "HOME": str(self.root)}))
        finally:
            os.umask(old)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        origin = self.root / "origin"
        (origin / "stacks" / "build").mkdir(parents=True)
        (origin / "stacks" / "build" / "init.sh").write_text("#!/bin/sh\necho hi\n")
        os.chmod(origin / "stacks" / "build" / "init.sh", 0o755)
        (origin / "stacks" / "sandbox.hosting.yml").write_text("name: x\n")
        (origin / "README").write_text("r\n")
        os.symlink("README", origin / "LINK")
        self._git("init", "-q", "-b", "main", cwd=origin)
        self._git("add", "-A", cwd=origin)
        self._git("-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "c",
                  cwd=origin)
        self.commit = self._git("rev-parse", "HEAD", cwd=origin).stdout.strip()
        self.origin = origin

    def test_a_checkout_under_0077_gets_the_modes_a_controller_checkout_gets(self):
        controller = self.root / "controller"
        self._git("clone", "-q", str(self.origin), str(controller), umask=0o022)
        follow = self.root / "follow"
        self._git("clone", "-q", "--no-checkout", str(self.origin), str(follow), umask=0o077)
        old = os.umask(0o077)
        try:
            git.checkout(follow, self.commit, "main")
        finally:
            os.umask(old)
        expected = self._modes(controller)
        self.assertEqual(expected["stacks/build/init.sh"], "0o755")
        self.assertEqual(expected["stacks/sandbox.hosting.yml"], "0o644")
        self.assertEqual(self._modes(follow), expected)

    def test_an_older_clone_checked_out_under_0077_is_repaired(self):
        follow = self.root / "follow"
        self._git("clone", "-q", str(self.origin), str(follow), umask=0o077)
        self.assertEqual(self._modes(follow)["stacks/build/init.sh"], "0o700")
        # What an earlier 0077 attempt's prepare left behind, untracked.
        old = os.umask(0o077)
        try:
            (follow / "vendor" / "pkg").mkdir(parents=True)
            (follow / "vendor" / "pkg" / "lib.php").write_text("<?php\n")
            (follow / "build.zip").write_text("zip")
            (follow / "tool").write_text("#!/bin/sh\n")
            os.chmod(follow / "tool", 0o700)
            (follow / "links").mkdir()
            os.symlink("../README", follow / "links" / "readme")
            outside = self.root / "outside.txt"
            outside.write_text("x")
            os.chmod(outside, 0o600)
            os.symlink(str(outside), follow / "escape")
            git.checkout(follow, self.commit, "main")  # nothing to rewrite: same commit
        finally:
            os.umask(old)
        modes = self._modes(follow)
        self.assertEqual((modes["stacks/build/init.sh"], modes["README"], modes["stacks"]),
                         ("0o755", "0o644", "0o755"))
        self.assertEqual((modes["vendor"], modes["vendor/pkg/lib.php"], modes["build.zip"],
                          modes["tool"], modes["links"]),
                         ("0o755", "0o644", "0o644", "0o755", "0o755"))
        self.assertEqual(oct(os.stat(follow).st_mode & 0o777), "0o755")
        self.assertEqual(oct(os.stat(follow / ".git").st_mode & 0o777), "0o700")
        # Symlinks are never followed out of the checkout.
        self.assertEqual(oct(os.stat(outside).st_mode & 0o777), "0o600")

    def test_the_stack_worktree_an_attempt_adds_gets_controller_modes(self):
        """The image source is a worktree the attempt adds (host_stack._add_worktree)."""
        controller = self.root / "controller"
        self._git("clone", "-q", str(self.origin), str(controller), umask=0o022)
        follow = self.root / "follow"
        self._git("clone", "-q", str(self.origin), str(follow), umask=0o077)
        worktree = self.root / "source"
        old = os.umask(0o077)
        try:
            subprocess.run(["sh", "-c", follow_tick.ATTEMPT_UMASK_SH, "attempt",
                            "git", "-C", str(follow), "worktree", "add", "-q", "--detach",
                            str(worktree), self.commit],
                           check=True, capture_output=True, timeout=60,
                           env=synthetic_environment({"HOME": str(self.root)}))
        finally:
            os.umask(old)
        self.assertEqual(self._modes(worktree), self._modes(controller))

    def test_personal_secrets_are_never_written_under_the_attempts_umask(self):
        from sandbox.core import _secrets
        target = self.root / "secrets.env"
        created = []
        real_open = os.open

        def spy(path, flags, mode=0o777, *args, **kwargs):
            if str(path).endswith(".tmp"):
                created.append(mode)
            return real_open(path, flags, mode, *args, **kwargs)

        old = os.umask(0o022)
        try:
            with patch.object(_secrets.os, "open", spy):
                _secrets.write_secret("SANDBOX_TEST_KEY", "value-1", path=target)
        finally:
            os.umask(old)
        self.assertEqual(created, [0o600])
        self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o600")

    def test_the_attempt_command_runs_with_umask_0022_under_a_0077_parent(self):
        argv = follow_tick.scoped_argv([sys.executable, "-c", "import os; print(oct(os.umask(0)))",
                                        "--attempt", "0123456789abcdef"])
        inner = argv[argv.index("--") + 1:]
        old = os.umask(0o077)
        try:
            result = subprocess.run(inner, capture_output=True, text=True, timeout=30)
        finally:
            os.umask(old)
        self.assertEqual(result.stdout.strip(), "0o22", result.stderr)

    def test_files_an_attempt_writes_from_a_umask_0077_poll_unit(self):
        """A real oneshot user unit with UMask=0077, like sandbox-stack-follow.service."""
        environment = synthetic_environment(follow_tick.user_manager_environment())
        if shutil.which("systemd-run") is None or subprocess.run(
                ["systemctl", "--user", "show-environment"], capture_output=True,
                env=environment, timeout=10).returncode != 0:
            self.skipTest("no systemd user manager")
        poll = self.root / "poll.py"
        poll.write_text("import json, subprocess, sys\n"
                        "raise SystemExit(subprocess.run(json.loads(sys.argv[1])).returncode)\n")
        results = {}
        for variant in ("scoped", "unscoped"):
            target = self.root / f"{variant}.out"
            inner = [sys.executable, "-c", f"open({str(target)!r}, 'w').write('x')",
                     "--attempt", "0123456789abcdef"]
            argv = follow_tick.scoped_argv(inner) if variant == "scoped" else inner
            completed = subprocess.run(
                ["systemd-run", "--user", "--wait", "--quiet", "--collect",
                 f"--unit=sandbox-test-follow-umask-{secrets.token_hex(4)}",
                 "-p", "Type=oneshot", "-p", "UMask=0077",
                 "--setenv=XDG_RUNTIME_DIR=" + environment["XDG_RUNTIME_DIR"],
                 "--", sys.executable, str(poll), json.dumps(argv)],
                capture_output=True, text=True, env=environment, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            results[variant] = oct(os.stat(target).st_mode & 0o777)
        self.assertEqual(results, {"scoped": "0o644", "unscoped": "0o600"})


if __name__ == "__main__":
    unittest.main()
