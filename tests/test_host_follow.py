"""Spec 089: the server-side stack branch follower (`sb host follow ...`).

Every collaborator that would touch the network, systemd, GitHub or a real
stack is a fake: ssh-keygen is emulated, the job contract and stack up are
recorded calls, and each test has its own temporary store and SANDBOX_HOME.
"""
import json
import subprocess
import sys
import tempfile
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
from sandbox.hosting.follow.apply import Attempt  # noqa: E402
from sandbox.hosting.follow.spec import FollowError  # noqa: E402
from sandbox.hosting.follow.store import FollowStore  # noqa: E402
from tests.subprocess_support import run_test_process  # noqa: E402

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
                 ({"owner": None}, "follow_owner_missing"))
        for fields, code in cases:
            with self.subTest(code=code), self.assertRaises(FollowError) as raised:
                self.add(**fields)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.store.names(), [])
        self.assertFalse(self.store.keys.exists() and any(self.store.keys.iterdir()))
        self.assertEqual(self.installs, [])
        self.assertEqual(self.audit_events(), [])

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
                   "checkout": lambda repo, commit: self.checked_out.append((Path(repo).name, commit)),
                   "stack_lookup": lambda _remote, _name: self.stack,
                   "busy": lambda _record, home: self.busy_reason, "home": lambda: str(self.home)}
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

    def stack_up(self, namespace):
        self.calls.append(namespace)
        if namespace.plan:
            return {"slug": "dev", "hostnames": list(self.plan_hosts)}
        return {"job_id": "apply-job-7"}

    def job_view(self, _job_id):
        return {"lifecycle": self.lifecycles.pop(0) if len(self.lifecycles) > 1 else self.lifecycles[0],
                "exit_code": 1}

    def run_attempt(self):
        attempt = Attempt(self.store, context_for=lambda _remote: self.context, stack_up=self.stack_up,
                          job_view=self.job_view,
                          prepare=lambda argv, **kw: self.prepared.append((argv, kw)),
                          head=lambda directory: self.heads[Path(directory).name],
                          environment=lambda _context: {"PATH": "/usr/bin"}, sleep=lambda _s: None)
        return attempt.run("xspeed-dev", "dev", self.attempt_id)

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


if __name__ == "__main__":
    unittest.main()
