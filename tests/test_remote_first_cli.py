"""CLI regression coverage for remote-first target selection."""

import json
import hashlib
import subprocess
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.runtime import cmd_exec, configure_exec_parser
from sandbox.runtimes.base import OperationResult


class RemoteFirstCliTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "command": ["--", "npm", "test"], "local": False, "remote": None,
            "workspace": None, "timeout": None, "detach": False,
            "output_profile": "smart", "json": True, "in_instance": False,
            "request_id": None,
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
            cmd_exec(None, self._args(output_profile="agent", request_id="exec-remote-1"))
        self.assertEqual(json.loads(output.getvalue())["remote"], "vps")
        self.assertEqual(submissions[0].output_profile_definition, {"mode": "errors"})
        self.assertEqual(submissions[0].project_identity, "project:remote")
        self.assertEqual(submissions[0].request_id, "exec-remote-1")
        self.assertEqual(
            submissions[0].source.identity,
            "sha256:" + hashlib.sha256("/project".encode()).hexdigest(),
        )

    def test_explicit_local_does_not_resolve_the_configured_remote(self):
        accepted = {"job_id": "b" * 32}
        submissions = []
        local_service = SimpleNamespace(
            submit=lambda submission: submissions.append(submission) or accepted,
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
            cmd_exec(None, self._args(local=True, detach=True, request_id="exec-local-1"))
        self.assertEqual(json.loads(output.getvalue()), accepted)
        self.assertEqual(submissions[0].request_id, "exec-local-1")

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

    def test_in_instance_failed_exec_preserves_streams_and_child_exit_code(self):
        requests, invocations = [], []
        target = SimpleNamespace(kind="local", project_root="/remote/project", remote_name=None,
                                 workspace_label="qa", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(
            resolve=lambda request: requests.append(request) or target)}
        registry = SimpleNamespace(registry_find_instance=lambda _instance: {
            "root": "/remote/project", "label": "qa",
        })
        runtime = SimpleNamespace(invoke=lambda request: invocations.append(request) or OperationResult(
            False, "exec", "/remote/project", "compose", {
                "stdout": "jest output\n",
                "stderr": "compose: container exited\n",
                "exit_code": 23,
                "reason": {"code": "compose_exec_failed"},
            }))
        output, errors = StringIO(), StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.commands.runtime.preflight_instance_capability", return_value=None), \
             patch("sandbox.commands.runtime._core", return_value=registry), \
             patch("sandbox.commands.runtime.runtime_service", return_value=runtime), \
             patch("sys.stdout", output), patch("sys.stderr", errors), \
             self.assertRaises(SystemExit) as raised:
            cmd_exec(None, self._args(local=True, workspace="qa", timeout=120,
                                      in_instance=True, json=False))
        self.assertEqual(raised.exception.code, 23)
        self.assertEqual(output.getvalue(), "jest output\n")
        self.assertEqual(errors.getvalue(), "compose: container exited\n")
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(invocations), 1)

    def test_in_instance_failed_exec_json_is_one_envelope_with_child_exit_code(self):
        target = SimpleNamespace(kind="local", project_root="/remote/project", remote_name=None,
                                 workspace_label="qa", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        registry = SimpleNamespace(registry_find_instance=lambda _instance: {
            "root": "/remote/project", "label": "qa",
        })
        runtime = SimpleNamespace(invoke=lambda _request: OperationResult(
            False, "exec", "/remote/project", "compose", {
                "stdout": "jest output\n",
                "stderr": "compose: container exited\n",
                "exit_code": 23,
                "reason": {"code": "compose_exec_failed"},
            }))
        output, errors = StringIO(), StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.commands.runtime.preflight_instance_capability", return_value=None), \
             patch("sandbox.commands.runtime._core", return_value=registry), \
             patch("sandbox.commands.runtime.runtime_service", return_value=runtime), \
             patch("sys.stdout", output), patch("sys.stderr", errors), \
             self.assertRaises(SystemExit) as raised:
            cmd_exec(None, self._args(local=True, workspace="qa", timeout=120,
                                      in_instance=True, json=True))
        self.assertEqual(raised.exception.code, 23)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stdout"], "jest output\n")
        self.assertEqual(payload["stderr"], "compose: container exited\n")
        self.assertEqual(payload["exit_code"], 23)
        self.assertEqual(errors.getvalue(), "")

    def test_request_id_refuses_bare_direct_local_before_runtime_invocation(self):
        target = SimpleNamespace(kind="local", project_root="/project", remote_name=None,
                                 workspace_label="default", runtime_policy={},
                                 sources={"identity": "project:local"})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        runtime = SimpleNamespace(invoke=lambda _request: self.fail("direct runtime must not run"))
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.commands.runtime.runtime_service", return_value=runtime), \
             patch("sandbox.commands.runtime.die", side_effect=RuntimeError) as die:
            with self.assertRaises(RuntimeError):
                cmd_exec(None, self._args(request_id="exec-direct-local-1"))
        die.assert_called_once_with(
            "--request-id requires durable execution; add --detach or select --local/--remote")

    def test_request_id_refuses_hidden_in_instance_before_target_or_runtime(self):
        dependencies = {"target_service": self.fail}
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sandbox.commands.runtime.die", side_effect=RuntimeError) as die:
            with self.assertRaises(RuntimeError):
                cmd_exec(None, self._args(local=True, in_instance=True,
                                          request_id="exec-in-instance-1"))
        die.assert_called_once_with(
            "--request-id requires durable execution; add --detach or select --local/--remote")

    def test_exec_parser_exposes_request_id_without_changing_target_defaults(self):
        parser = __import__("argparse").ArgumentParser()
        configure_exec_parser(parser)
        args = parser.parse_args(["--request-id", "exec-parser-1", "--", "echo", "ok"])
        self.assertEqual(args.request_id, "exec-parser-1")
        self.assertFalse(args.local)
        self.assertIsNone(args.remote)
        self.assertFalse(args.detach)
        self.assertIn("--request-id", parser.format_help())

    def test_exec_help_exposes_target_and_finite_deadline_controls(self):
        result = subprocess.run([str(__import__("pathlib").Path(__file__).parent.parent / "sb"),
                                 "exec", "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--local", "--remote", "--workspace", "--timeout", "--request-id"):
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
