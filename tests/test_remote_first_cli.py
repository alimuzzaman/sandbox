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
                                 workspace_label="default")
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        remote_transport = SimpleNamespace(submit=lambda submission: {
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
            cmd_exec(None, self._args())
        self.assertEqual(json.loads(output.getvalue())["remote"], "vps")

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


if __name__ == "__main__":
    unittest.main()
