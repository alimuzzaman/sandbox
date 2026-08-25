from __future__ import annotations

import contextlib
import subprocess
import unittest
from unittest import mock

from sandbox.core import _docker, _instances


class _Result:
    def __init__(self, returncode=0):
        self.returncode = returncode


class TestDockerDaemonPreflight(unittest.TestCase):
    def test_ready_probe_is_bounded_and_read_only(self):
        with mock.patch.object(_docker.shutil, "which", return_value="/usr/local/bin/docker"), \
             mock.patch.object(_docker, "run", return_value=_Result(0)) as run:
            result = _docker.docker_daemon_preflight(timeout=3)

        self.assertEqual(result, {"ok": True, "code": "docker_daemon_ready"})
        run.assert_called_once_with(
            ["docker", "info"], check=False, capture=True, timeout=3.0,
        )

    def test_timeout_and_unavailable_results_are_safe_and_actionable(self):
        with mock.patch.object(_docker.shutil, "which", return_value="docker"), \
             mock.patch.object(_docker, "run",
                               side_effect=subprocess.TimeoutExpired("docker info", 5)):
            timeout = _docker.docker_daemon_preflight()
        self.assertEqual(timeout["code"], "docker_daemon_timeout")
        self.assertIn("retry `sb ensure`", timeout["message"])
        self.assertNotIn("socket", timeout["message"])

        with mock.patch.object(_docker.shutil, "which", return_value=None):
            missing = _docker.docker_daemon_preflight()
        self.assertEqual(missing["code"], "docker_cli_unavailable")
        self.assertIn("install Docker", missing["message"])


class TestEnsureDockerGate(unittest.TestCase):
    def test_unavailable_daemon_refuses_before_port_or_state_writes(self):
        class State:
            ConfigError = RuntimeError

            @staticmethod
            def load_project_config(_project, label=None):
                return {"root": "/project", "server": "nginx"}

            @staticmethod
            @contextlib.contextmanager
            def project_lock(_root):
                yield

            @staticmethod
            def registry_get(_root, label=None):
                return None

        with mock.patch.object(_instances, "_core", return_value=State()), \
             mock.patch.object(_instances, "docker_daemon_preflight", return_value={
                 "ok": False,
                 "code": "docker_daemon_timeout",
                 "message": "Docker daemon did not respond within 5s; retry `sb ensure`.",
             }) as preflight, \
             mock.patch.object(_instances, "_resolve_port_conflicts",
                               side_effect=AssertionError("port allocation must not run")), \
             mock.patch.object(_instances, "_write_local_yaml",
                               side_effect=AssertionError("local state must not be written")):
            with self.assertRaisesRegex(RuntimeError, "retry `sb ensure`") as raised:
                _instances.ensure_instance({}, "/project")

        preflight.assert_called_once_with()
        self.assertEqual(raised.exception.code, "docker_daemon_timeout")


if __name__ == "__main__":
    unittest.main()
