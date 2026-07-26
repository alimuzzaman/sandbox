"""CLI regression coverage for remote-first target selection."""

import json
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.runtime import cmd_exec


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

    def test_explicit_local_does_not_resolve_the_configured_remote(self):
        accepted = {"job_id": "b" * 32}
        local_service = SimpleNamespace(
            submit=lambda _submission: accepted,
            get=lambda _job_id: {"lifecycle": "succeeded"},
            read_output=lambda _job_id: {"data": "ok\n"},
        )
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: SimpleNamespace(
            kind="local", project_root="/project", remote_name=None, workspace_label="default")),
            "job_service": local_service}
        output = StringIO()
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch("sys.stdout", output):
            cmd_exec(None, self._args(local=True, detach=True))
        self.assertEqual(json.loads(output.getvalue()), accepted)

    def test_detached_human_output_includes_target_workspace_and_deadline_source(self):
        accepted = {"job_id": "c" * 32, "target": {"kind": "local", "remote": None},
                    "workspace": "unit", "deadline": {"seconds": 120, "source": "explicit"}}
        target = SimpleNamespace(kind="local", project_root="/project", remote_name=None,
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
