import sys
import importlib.util
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


def _load_wp_tool():
    """Load the test tool without requiring the optional MCP server venv."""
    app = types.ModuleType("app")
    app.SANDBOX_ROOT = Path("/tmp/sandbox")
    app._compose = app._herd_host_env = app._host_run = app._project_instance = lambda *_args, **_kwargs: None
    app._is_herd = lambda *_args, **_kwargs: False
    app._require_project_capability = lambda *_args, **_kwargs: None
    app._resolve_instance = app._safe_json = app._wp_root = app._wpcli = lambda *_args, **_kwargs: {}
    app.mcp = types.SimpleNamespace(tool=lambda: (lambda function: function))
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    httpx.Client = object
    mcp = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = object
    spec = importlib.util.spec_from_file_location("sandbox_test_job_mcp_wp", MCP_ROOT / "tools" / "wp.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app": app, "httpx": httpx, "mcp": mcp,
                                  "mcp.server": server, "mcp.server.fastmcp": fastmcp}):
        spec.loader.exec_module(module)
    return module


class JobMcpTests(unittest.TestCase):
    def test_follow_returns_bounded_monotonic_request_progress(self):
        from tools import jobs

        page = {"ok": True, "cursor": "next", "events_read": 3, "has_more": False}
        with patch.object(jobs, "job_output", return_value=page) as output:
            result = jobs.job_follow("a" * 32, cursor="start", max_updates=3,
                                     max_duration_seconds=2, progress_token="request-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["cursor"], "next")
        self.assertEqual(len(result["updates"]), 1)
        self.assertEqual(result["progress"], [{"token": "request-1", "current": 1,
                                                "total": 3, "events_observed": 3}])
        self.assertEqual(output.call_args.kwargs["wait_seconds"], 1)

    def test_follow_rejects_unbounded_or_invalid_request_progress_inputs(self):
        from tools import jobs

        for kwargs in (
            {"max_updates": 0}, {"max_updates": 21}, {"max_duration_seconds": 0},
            {"max_duration_seconds": 21}, {"progress_token": ""},
        ):
            with self.subTest(kwargs=kwargs):
                result = jobs.job_follow("a" * 32, **kwargs)
                self.assertEqual(result["code"], "invalid_follow_query")

    def test_remote_start_status_and_output_preserve_job_contracts(self):
        from tools import jobs

        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="unit", runtime_policy={})
        started = {"ok": True, "job_id": "a" * 32, "workspace": "unit", "remote": "vps"}
        calls = []
        transport = SimpleNamespace(
            submit=lambda submission: calls.append(("submit", submission)) or started,
            status=lambda remote, job_id: calls.append(("status", remote, job_id)) or
                {"job_id": job_id, "lifecycle": "running"},
            read_output=lambda remote, job_id, **kwargs: calls.append(("output", remote, job_id, kwargs)) or
                {"ok": True, "job_id": job_id, "bounded": True, "cursor": "next"},
        )
        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport), \
                patch.object(jobs, "_remote_transport", return_value=transport):
            accepted = jobs.job_start(["npm", "test"], "/project", remote="vps",
                                      workspace="unit", timeout_seconds=120)
            status = jobs.job_status("a" * 32, remote="vps")
            output = jobs.job_output("a" * 32, remote="vps", cursor="opaque", max_bytes=4096)

        self.assertEqual(accepted, started)
        self.assertEqual(status, {"ok": True, "job_id": "a" * 32, "lifecycle": "running"})
        self.assertTrue(output["bounded"])
        self.assertEqual(calls[0][1].deadline_seconds, 120)
        self.assertEqual(calls[-1], ("output", "vps", "a" * 32, {
            "stream": "combined", "cursor": "opaque", "offset": None, "tail_bytes": None,
            "lines": None, "since": None, "max_bytes": 4096, "wait_seconds": 0,
            "encoding": "utf8", "profile": "full",
        }))

    def test_remote_run_tests_preserves_result_keys_and_defers_completion(self):
        wp = _load_wp_tool()

        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="php", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        transport = SimpleNamespace(submit=lambda _submission: {"ok": True, "job_id": "b" * 32})
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
                patch.object(wp, "_remote_job_transport", return_value=transport):
            result = wp.run_tests("/project", mode="unit", remote="vps", workspace="php",
                                  timeout_seconds=120)

        self.assertEqual(result, {"ok": True, "passed": None, "summary": "remote test job accepted",
                                  "output": "", "mode": "unit", "job_id": "b" * 32,
                                  "lifecycle": "accepted", "workspace": "php", "remote": "vps"})

    def test_remote_run_tests_reports_config_resolved_mode_not_auto(self):
        wp = _load_wp_tool()
        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="php", runtime_policy={})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        submissions = []
        transport = SimpleNamespace(submit=lambda submission: submissions.append(submission) or {
            "ok": True, "job_id": "c" * 32,
        })
        config = SimpleNamespace(load_project_config=lambda _path, label=None: {
            "tests": {"suite": "unit"},
        })
        with patch.object(wp, "_core", return_value=config), \
                patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
                patch.object(wp, "_remote_job_transport", return_value=transport):
            result = wp.run_tests("/project", remote="vps", workspace="php", timeout_seconds=120)

        self.assertEqual(result["mode"], "unit")
        self.assertEqual(submissions[0].argv[:3], ("sb", "test", "unit"))

    def test_wp_cli_job_rejects_malformed_id_before_instance_resolution(self):
        wp = _load_wp_tool()
        instance = Mock()
        wp._project_instance = instance

        result = wp.wp_cli_job("../not-a-job", project_dir="/project")

        self.assertEqual(result, {"ok": False, "error": "invalid job id"})
        instance.assert_not_called()

    def test_remote_instance_exec_uses_durable_runtime_exec_job(self):
        from tools import jobs, runtime

        accepted = {"ok": True, "job_id": "c" * 32}
        with patch.object(jobs, "_submit_explicit_job", return_value=accepted) as submit:
            result = runtime.instance_exec(["node", "--version"], "/project", remote="vps",
                                           workspace="node", timeout_seconds=120)

        self.assertEqual(result, {"ok": True, "operation": "exec", **accepted})
        self.assertEqual(submit.call_args.kwargs["kind"], "runtime-exec")
        self.assertEqual(submit.call_args.kwargs["remote"], "vps")
        self.assertEqual(submit.call_args.kwargs["timeout_seconds"], 120)
