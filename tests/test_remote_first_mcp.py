"""MCP target-input parity for durable remote-first job operations."""

import importlib.util
import hashlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


def _load_jobs_tool():
    dependencies = types.ModuleType("dependencies")
    dependencies.ToolDependencies = object
    spec = importlib.util.spec_from_file_location(
        "sandbox_test_remote_first_mcp_jobs", ROOT / "mcp" / "wp-server" / "tools" / "jobs.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"dependencies": dependencies}):
        spec.loader.exec_module(module)
    return module


class RemoteFirstMcpTests(unittest.TestCase):
    def test_job_start_uses_shared_configured_remote_target_and_bounded_argv(self):
        module = _load_jobs_tool()
        requests = []
        module._target_service = SimpleNamespace(resolve=lambda request: requests.append(request) or SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps", workspace_label="default",
            sources={"identity": "project:remote"}))
        submissions = []
        transport = SimpleNamespace(submit=lambda submission: submissions.append(submission) or {
            "job_id": "a" * 32, "target": submission.target_kind,
            "remote": submission.remote_name, "workspace": submission.workspace_label,
        })
        with patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport):
            result = module.job_start(["npm", "test"], "/project", timeout_seconds=120)
        self.assertEqual(result["remote"], "vps")
        self.assertEqual(result["workspace"], "default")
        self.assertFalse(requests[0].local)
        self.assertIsNone(requests[0].remote)
        self.assertEqual(submissions[0].project_identity, "project:remote")
        self.assertEqual(
            submissions[0].source.identity,
            "sha256:" + hashlib.sha256("/project".encode()).hexdigest(),
        )

    def test_unknown_target_is_reported_without_submission(self):
        module = _load_jobs_tool()

        class UnknownTarget(Exception):
            code = "unknown_remote"

        module._target_service = SimpleNamespace(resolve=lambda _request: (_ for _ in ()).throw(UnknownTarget("missing")))
        self.assertEqual(module.job_start(["npm", "test"], "/project")["code"], "unknown_remote")

    def test_remote_status_and_output_preserve_cursor_and_bounded_options(self):
        module = _load_jobs_tool()
        calls = []
        module._remote_transport = lambda: SimpleNamespace(
            status=lambda remote, job_id: calls.append(("status", remote, job_id)) or {"job_id": job_id},
            read_output=lambda remote, job_id, **kwargs: calls.append(("output", remote, job_id, kwargs)) or {"ok": True},
        )
        self.assertTrue(module.job_status("b" * 32, remote="vps")["ok"])
        self.assertTrue(module.job_output("b" * 32, remote="vps", cursor="opaque", max_bytes=4096)["ok"])
        self.assertEqual(calls, [
            ("status", "vps", "b" * 32),
            ("output", "vps", "b" * 32, {"stream": "combined", "cursor": "opaque", "offset": None,
                                       "tail_bytes": None, "lines": None, "since": None, "max_bytes": 4096,
                                       "wait_seconds": 0, "encoding": "utf8", "profile": "full"}),
        ])


if __name__ == "__main__":
    unittest.main()
