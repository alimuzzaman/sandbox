"""migrate --plan lists stacks whose running sandbox-login a migrate leaves stale.

The remote program runs for real against a fake `docker` on PATH; no SSH.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from sandbox.hosting import login_reapply_plan as plan_module
from sandbox.hosting import login_service
from tests.subprocess_support import run_test_process


class LoginReapplyPlanTest(unittest.TestCase):
    def setUp(self):
        self.bin = Path(self.enterContext(tempfile.TemporaryDirectory()))
        current = ["python3", "-c", login_service._LOADER, login_service._runtime_source()]
        stale = ["python3", "-c", login_service._LOADER, "b2xk"]
        inspect = [
            {"Config": {"Labels": {"com.docker.compose.project": "sandbox-host-a"}, "Cmd": current}},
            {"Config": {"Labels": {"com.docker.compose.project": "sandbox-host-b"}, "Cmd": stale}},
        ]
        (self.bin / "inspect.json").write_text(json.dumps(inspect))
        docker = self.bin / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  ps) echo aaa; echo bbb ;;\n'
            f'  inspect) cat {shlex.quote(str(self.bin / "inspect.json"))} ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n")
        docker.chmod(0o755)

    def ssh_run(self, remote, command, *, timeout):
        argv = shlex.split(command)
        self.assertEqual(argv[:2], ["python3", "-c"])
        return run_test_process([sys.executable, "-c", argv[2]], text=True,
                                env={"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"})

    def test_a_stack_running_another_login_runtime_needs_a_reapply(self):
        result = plan_module.plan({}, self.ssh_run)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["needs_reapply"], ["sandbox-host-b"])
        self.assertEqual(result["current"], ["sandbox-host-a"])
        self.assertIn("owner_share_upgrade_required", result["note"])

    def test_no_running_login_service_needs_nothing(self):
        (self.bin / "docker").write_text("#!/bin/sh\nexit 0\n")
        result = plan_module.plan({}, self.ssh_run)
        self.assertEqual((result["state"], result["needs_reapply"]), ("complete", []))

    def test_an_unreadable_docker_is_unavailable_not_an_error(self):
        (self.bin / "docker").write_text("#!/bin/sh\nexit 1\n")
        self.assertEqual(plan_module.plan({}, self.ssh_run)["state"], "unavailable")

        def lost(remote, command, *, timeout):
            raise subprocess.TimeoutExpired(command, timeout)
        self.assertEqual(plan_module.plan({}, lost)["state"], "unavailable")

    def test_an_unexpected_project_label_is_unavailable(self):
        def odd(remote, command, *, timeout):
            return subprocess.CompletedProcess(command, 0, json.dumps([{"project": "../x"}]), "")
        self.assertEqual(plan_module.plan({}, odd)["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
