"""CLI regression coverage for remote-first target selection."""

import json
import hashlib
import subprocess
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.runtime import cmd_exec
from sandbox.runtimes.base import OperationResult


class RemoteFirstCliTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "command": ["--", "npm", "test"], "local": False, "remote": None,
            "workspace": None, "timeout": None, "detach": False,
            "output_profile": "smart", "json": True, "in_instance": False,
            "resolved_instance": "default",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_configured_remote_uses_durable_transport_without_explicit_flag(self):
        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 sources={"identity": "project:remote"},
                                 workspace_label="default", runtime_policy={
                                     "outputProfiles": {"agent": {"mode": "errors"}},
                                 })
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        submissions = []
        remote_transport = SimpleNamespace(submit=lambda submission: submissions.append(submission) or {
            "job_id": "a" * 32, "target": submission.target_kind,
            "remote": submission.remote_name,
        })
        output = StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=remote_transport), \
             patch("sandbox.core._remote.deploy_exact_working_tree"), \
             patch("sandbox.core._remote.ssh_run"), \
             patch("sandbox.core._remote.get_remote"), \
             patch("sandbox.core._remote.remote_sb_path"), \
             patch("sys.stdout", output):
            cmd_exec(None, self._args(output_profile="agent"))
        self.assertEqual(json.loads(output.getvalue())["remote"], "vps")
        self.assertEqual(submissions[0].output_profile_definition, {"mode": "errors"})
        self.assertEqual(submissions[0].project_identity, "project:remote")
        self.assertEqual(
            submissions[0].source.identity,
            "sha256:" + hashlib.sha256("/project".encode()).hexdigest(),
        )

    def test_explicit_local_does_not_resolve_the_configured_remote(self):
        accepted = {"job_id": "b" * 32}
        local_service = SimpleNamespace(
            submit=lambda _submission: accepted,
            get=lambda _job_id: {"lifecycle": "succeeded"},
            read_output=lambda _job_id: {"data": "ok\n"},
        )
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: SimpleNamespace(
            kind="local", project_root="/project", remote_name=None, workspace_label="default",
            sources={"identity": "project:local"})),
            "job_service": local_service}
        output = StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sys.stdout", output):
            cmd_exec(None, self._args(local=True, detach=True))
        self.assertEqual(json.loads(output.getvalue()), accepted)

    def test_explicit_named_remote_is_forwarded_to_the_shared_target_resolver(self):
        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="named-vps",
                                 sources={"identity": "project:named"},
                                 workspace_label="qa", runtime_policy={})
        requests = []
        dependencies = {"target_service": SimpleNamespace(
            resolve=lambda request: requests.append(request) or target)}
        transport = SimpleNamespace(submit=lambda _submission: {"job_id": "d" * 32})
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport), \
                patch("sandbox.core._remote.deploy_exact_working_tree"), \
                patch("sandbox.core._remote.ssh_run"), \
                patch("sandbox.core._remote.get_remote"), \
                patch("sandbox.core._remote.remote_sb_path"), \
                patch("sys.stdout", StringIO()):
            cmd_exec(None, self._args(remote="named-vps", workspace="qa", detach=True))
        self.assertEqual(requests[0].remote, "named-vps")
        self.assertEqual(requests[0].workspace, "qa")
        self.assertEqual(requests[0].required_capability, "job.exec")

    def test_in_instance_exec_bypasses_the_local_job_host_and_uses_compose_service(self):
        requests, invocations = [], []
        target = SimpleNamespace(kind="local", project_root="/remote/project", remote_name=None,
                                 workspace_label="qa", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(
            resolve=lambda request: requests.append(request) or target)}
        registry = SimpleNamespace(registry_find_instance=lambda _instance: {
            "root": "/remote/project", "label": "qa",
        })
        runtime = SimpleNamespace(invoke=lambda request: invocations.append(request) or OperationResult(
            True, "exec", "/remote/project", "compose", {"output": "v22.18.0\n"}))
        output = StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.commands.runtime.preflight_instance_capability", return_value=None), \
             patch("sandbox.commands.runtime._core", return_value=registry), \
             patch("sandbox.commands.runtime.runtime_service", return_value=runtime), \
             patch("sys.stdout", output):
            cmd_exec(None, self._args(local=True, workspace="qa", timeout=120,
                                      in_instance=True))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].local)
        self.assertEqual(invocations[0].project_root, "/remote/project")
        self.assertEqual(invocations[0].operation, "exec")
        self.assertEqual(invocations[0].arguments, {"argv": ["npm", "test"], "timeout": 120})
        self.assertEqual(json.loads(output.getvalue())["output"], "v22.18.0\n")

    def test_exec_help_exposes_target_and_finite_deadline_controls(self):
        result = subprocess.run([str(__import__("pathlib").Path(__file__).parent.parent / "sb"),
                                 "exec", "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--local", "--remote", "--workspace", "--timeout"):
            self.assertIn(option, result.stdout)

    def test_detached_human_output_includes_target_workspace_and_deadline_source(self):
        accepted = {"job_id": "c" * 32, "target": {"kind": "local", "remote": None},
                    "workspace": "unit", "deadline": {"seconds": 120, "source": "explicit"}}
        target = SimpleNamespace(kind="local", project_root="/project", remote_name=None,
                                 sources={"identity": "project:local"},
                                 workspace_label="unit", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target),
                        "job_service": SimpleNamespace(submit=lambda _submission: accepted)}
        output = StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sys.stdout", output):
            cmd_exec(None, self._args(local=True, workspace="unit", timeout=120,
                                      detach=True, json=False))
        self.assertEqual(output.getvalue().strip(),
                         f"{'c' * 32} target=local workspace=unit deadline=120s source=explicit")


if __name__ == "__main__":
    unittest.main()
