"""Compose up serialises on its own lock, not on the server's edge locks (Fable review 2).

Holding the Caddy and nginx locks for a whole recreate made every route install,
certificate reload and other target's apply wait 30 s and then fail.
"""
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sandbox.commands.hosting as hosting_cmd
from sandbox.hosting import ports
from sandbox.hosting.front_door import nginx

VALIDATED = {"compose": {"service": "web", "container_port": 8080, "build_timeout_seconds": 900},
             "login_service": None}
RUNTIME = {"compose_project": "example-site-production", "loopback_port": 18000,
           "port_identity": {"target": "machine-1", "uid": 1000, "home": "/home/sandbox"},
           "port_engine_id": "engine-1"}


def _locked_argv(command: str) -> list:
    """The flock argv the sender hands to sudo (its fourth shell word, JSON)."""
    import json
    return json.loads(shlex.split(command)[5])


class ComposePublishLockTests(unittest.TestCase):
    def test_compose_up_takes_only_the_publish_lock_with_a_budget_wait(self):
        argv = _locked_argv(hosting_cmd._locked_host_compose_up(VALIDATED, RUNTIME, "docker compose up -d"))
        self.assertNotIn(hosting_cmd._CADDY_LOCK_PATH, argv)
        self.assertNotIn(nginx.LOCK_PATH, argv)
        self.assertEqual(argv[:6], ["flock", "-w", "300", "-E", str(ports.LOCK_BUSY_EXIT),
                                    hosting_cmd._COMPOSE_PUBLISH_LOCK_PATH])
        self.assertEqual(argv.count("flock"), 1)

    def test_the_wait_fits_the_stack_job_slack(self):
        # Re-check S2: the wait is added to the up's SSH timeout, so it is capped.
        long = {**VALIDATED, "compose": {**VALIDATED["compose"], "build_timeout_seconds": 3600}}
        self.assertEqual(hosting_cmd._compose_publish_lock_wait(long), 300)

    def test_a_short_budget_still_waits_thirty_seconds(self):
        short = {**VALIDATED, "compose": {**VALIDATED["compose"], "build_timeout_seconds": 60}}
        argv = _locked_argv(hosting_cmd._locked_host_compose_up(short, RUNTIME, "true"))
        self.assertEqual(argv[2], "30")

    def test_a_busy_lock_is_a_named_pre_effect_refusal(self):
        failed = hosting_cmd.HostRemoteCommandFailed("", phase="compose_recreate",
                                                     exit_code=ports.LOCK_BUSY_EXIT)
        with mock.patch.object(hosting_cmd, "_remote_checked", side_effect=failed), \
                self.assertRaises(hosting_cmd.HostComposePublishBusy) as raised:
            hosting_cmd._run_locked_compose_up({}, "cmd", timeout=10, log_phase="compose_recreate")
        self.assertEqual(raised.exception.code, "hosting_compose_publish_busy")
        self.assertIn(raised.exception.phase, hosting_cmd._PRE_EFFECT_PHASES)

    def test_another_compose_failure_is_unchanged(self):
        failed = hosting_cmd.HostRemoteCommandFailed("boom", phase="compose_recreate", exit_code=1)
        with mock.patch.object(hosting_cmd, "_remote_checked", side_effect=failed), \
                self.assertRaises(hosting_cmd.HostRemoteCommandFailed) as raised:
            hosting_cmd._run_locked_compose_up({}, "cmd", timeout=10, log_phase="compose_recreate")
        self.assertNotIsInstance(raised.exception, hosting_cmd.HostComposePublishBusy)

    def test_lock_context_rejects_bad_waits_and_lock_counts(self):
        for kwargs in ({"locks": ()}, {"locks": ("/run/lock/a", "/run/lock/b", "/run/lock/c")},
                       {"locks": ("/run/lock/a",), "wait": 0}, {"locks": ("/run/lock/a",), "wait": "30"}):
            with self.assertRaises(ValueError):
                ports.lock_context_command("true", uid=1000, **kwargs)

    @unittest.skipUnless(shutil.which("flock"), "util-linux flock")
    def test_flock_reports_a_held_lock_with_the_busy_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "publish.lock")
            holder = subprocess.Popen(["flock", path, "sleep", "5"])
            try:
                for _ in range(50):
                    if subprocess.run(["flock", "-n", path, "true"]).returncode != 0:
                        break
                    subprocess.run(["sleep", "0.1"])
                done = subprocess.run(["flock", "-w", "1", "-E", str(ports.LOCK_BUSY_EXIT), path, "true"])
            finally:
                holder.kill()
                holder.wait()
        self.assertEqual(done.returncode, ports.LOCK_BUSY_EXIT)


if __name__ == "__main__":
    unittest.main()
