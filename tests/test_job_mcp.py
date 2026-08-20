import sys
import hashlib
import importlib.util
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.resources.network_capacity import evaluate_network_capacity
from sandbox.application.target_service import TargetResolutionError
from sandbox.transports.remote_jobs import RemoteJobAdmissionError


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
    @staticmethod
    def _admission_error():
        decision = evaluate_network_capacity({"status": "partial"}, remote_name="vps")
        secret = "mcp-admission-fixture-private-value"
        decision["evidence"]["ssh_output"] = secret
        decision["recovery"]["next_command"] = secret
        return RemoteJobAdmissionError(decision), secret

    def test_remote_runner_exception_is_not_serialized_by_job_start(self):
        from tools import jobs

        fixture = "runner-private-value"
        target = SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps",
            workspace_label="unit", runtime_policy={}, sources={"identity": "project:remote"},
        )
        transport = SimpleNamespace(submit=Mock(side_effect=RuntimeError(fixture)))
        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport):
            result = jobs.job_start(["tool", "safe"], "/project", remote="vps")

        self.assertEqual(result, {
            "ok": False, "code": "supervisor_launch_failed", "error": "job submission failed",
        })
        self.assertFalse(fixture in str(result))

    def test_network_capacity_admission_preserves_public_job_start_envelope(self):
        from tools import jobs

        admission, secret = self._admission_error()
        target = SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps",
            workspace_label="unit", runtime_policy={}, sources={"identity": "project:remote"},
        )
        transport = SimpleNamespace(submit=Mock(side_effect=admission))
        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport):
            result = jobs.job_start(["tool", "safe"], "/project", remote="vps")

        self.assertEqual(result, admission.to_payload())
        self.assertFalse("job_id" in result)
        self.assertNotEqual(result.get("status"), "accepted")
        self.assertNotIn(secret, str(result))

    def test_network_capacity_admission_preserves_public_job_matrix_envelope(self):
        from tools import jobs

        admission, secret = self._admission_error()
        target = SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps",
            workspace_label="unit", runtime_policy={}, sources={"identity": "project:remote"},
        )
        transport = SimpleNamespace(submit_many=Mock(side_effect=admission))
        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", return_value=transport):
            result = jobs.job_matrix(["tool", "safe"], ["one", "two"], "/project", remote="vps")

        self.assertEqual(result, admission.to_payload())
        self.assertFalse("job_id" in result)
        self.assertNotEqual(result.get("status"), "accepted")
        self.assertNotIn(secret, str(result))

    def test_job_matrix_preserves_target_resolution_codes_before_policy_validation(self):
        from tools import jobs

        for code in ("unknown_remote", "remote_not_provisioned"):
            with self.subTest(code=code), patch.object(
                    jobs, "_target_service", SimpleNamespace(resolve=Mock(side_effect=TargetResolutionError(
                        code, "target is unavailable")))):
                result = jobs.job_matrix(["tool", "safe"], ["one"], "/project", remote="vps")

            self.assertEqual(result, {
                "ok": False, "code": code, "error": "target is unavailable",
            })

    def test_job_list_forwards_project_workspace_and_pages_filtered_results(self):
        from tools import jobs

        target = SimpleNamespace(
            project_root="/project", kind="local", remote_name=None,
            workspace_label="unit", sources={"identity": "project:one"},
        )
        rows = [
            {"job_id": "a" * 32, "lifecycle": "running", "kind": "test"},
            {"job_id": "b" * 32, "lifecycle": "running", "kind": "test"},
            {"job_id": "c" * 32, "lifecycle": "succeeded", "kind": "test"},
        ]
        service = SimpleNamespace(list=Mock(
            side_effect=lambda query: rows[1:2] if query.get("cursor_job_id") else rows))
        with patch.object(jobs, "_target_service", SimpleNamespace(
                resolve=lambda _request: target)), \
                patch.object(jobs, "_job_service", service):
            first = jobs.job_list(
                "/project", workspace="unit", lifecycle="running",
                kind="test", limit=1,
            )
            second = jobs.job_list(
                "/project", workspace="unit", lifecycle="running",
                kind="test", limit=1, cursor=first["next_cursor"],
            )
        self.assertEqual(service.list.call_args_list[0].args[0], {
            "limit": 2, "project_identity": "project:one",
            "workspace_label": "unit", "lifecycle": "running", "kind": "test",
        })
        self.assertEqual(service.list.call_args_list[1].args[0]["cursor_job_id"], "a" * 32)
        self.assertEqual([item["job_id"] for item in first["jobs"]], ["a" * 32])
        self.assertTrue(first["has_more"])
        self.assertEqual([item["job_id"] for item in second["jobs"]], ["b" * 32])
        self.assertFalse(second["has_more"])

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
                                 workspace_label="unit", runtime_policy={},
                                 sources={"identity": "project:remote"})
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
        self.assertEqual(calls[0][1].project_identity, "project:remote")
        self.assertEqual(
            calls[0][1].source.identity,
            "sha256:" + hashlib.sha256("/project".encode()).hexdigest(),
        )
        self.assertEqual(calls[-1], ("output", "vps", "a" * 32, {
            "stream": "combined", "cursor": "opaque", "offset": None, "tail_bytes": None,
            "lines": None, "since": None, "max_bytes": 4096, "wait_seconds": 0,
            "encoding": "utf8", "profile": "full",
        }))

    def test_mcp_start_uses_the_same_workspace_execution_policy_as_cli(self):
        from tools import jobs

        target = SimpleNamespace(
            kind="local", project_root="/project", remote_name=None, workspace_label="qa",
            runtime_policy={
                "executionProfile": "exec",
                "executionProfiles": {
                    "exec": {"timeoutSeconds": 90, "stallSeconds": 9,
                             "cancelGraceSeconds": 10, "cancelOnStall": True,
                             "cleanup": "always"},
                    "custom": {"timeoutSeconds": 120, "stallSeconds": 12,
                               "cancelGraceSeconds": 13, "cancelOnStall": False,
                               "cleanup": "ephemeral"},
                },
                "workspaces": {"qa": {"executionProfile": "custom"}},
            }, sources={"identity": "project:local"},
        )
        captured = []
        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
                patch.object(jobs, "_job_service", SimpleNamespace(
                    submit=lambda submission: captured.append(submission) or {"ok": True})):
            result = jobs.job_start(["npm", "test"], "/project", workspace="qa")
            invalid = jobs.job_start(["npm", "test"], "/project", workspace="qa", timeout_seconds=0)

        self.assertTrue(result["ok"])
        self.assertEqual((captured[0].deadline_seconds, captured[0].stall_seconds,
                          captured[0].cancel_grace_seconds, captured[0].cleanup_policy),
                         (120, 12, 13, "ephemeral"))
        self.assertEqual(invalid, {"ok": False, "code": "invalid_execution_policy",
                                   "error": "execution policy is invalid"})
        self.assertEqual(len(captured), 1)

    def test_remote_run_tests_preserves_result_keys_and_defers_completion(self):
        wp = _load_wp_tool()

        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="php", runtime_policy={},
                                 sources={"identity": "project:remote"})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        submissions = []
        transport = SimpleNamespace(submit=lambda submission: submissions.append(submission) or {
            "ok": True, "job_id": "b" * 32})
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
                patch.object(wp, "_remote_job_transport", return_value=transport):
            result = wp.run_tests("/project", mode="unit", remote="vps", workspace="php",
                                  timeout_seconds=120)

        self.assertEqual(result, {"ok": True, "passed": None, "summary": "remote test job accepted",
                                  "output": "", "mode": "unit", "job_id": "b" * 32,
                                  "lifecycle": "accepted", "workspace": "php", "remote": "vps"})
        self.assertEqual(submissions[0].project_identity, "project:remote")
        self.assertEqual(
            submissions[0].source.identity,
            "sha256:" + hashlib.sha256("/project".encode()).hexdigest(),
        )

    def test_remote_run_tests_resolves_full_workspace_execution_policy_before_submit(self):
        wp = _load_wp_tool()
        target = SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps", workspace_label="php",
            runtime_policy={
                "executionProfiles": {"verify": {
                    "timeoutSeconds": 123, "stallSeconds": 12, "cancelGraceSeconds": 13,
                    "cancelOnStall": True, "cleanup": "ephemeral",
                }},
                "workspaces": {"php": {"executionProfile": "verify"}},
            }, sources={"identity": "project:remote"},
        )
        submissions = []
        with patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(resolve=lambda _request: target)}), \
                patch.object(wp, "_remote_job_transport", return_value=SimpleNamespace(
                    submit=lambda submission: submissions.append(submission) or {"ok": True, "job_id": "b" * 32})):
            result = wp.run_tests("/project", mode="unit", remote="vps", workspace="php")
            rejected = wp.run_tests("/project", mode="unit", remote="vps", workspace="php",
                                    timeout_seconds=0)

        self.assertTrue(result["ok"])
        self.assertEqual((submissions[0].deadline_seconds, submissions[0].stall_seconds,
                          submissions[0].cancel_grace_seconds, submissions[0].cancel_on_stall,
                          submissions[0].cleanup_policy), (123, 12, 13, True, "ephemeral"))
        self.assertEqual(submissions[0].execution_policy_provenance["execution_profile"], "workspace")
        self.assertFalse(rejected["ok"])
        self.assertEqual(len(submissions), 1)

    def test_network_capacity_admission_preserves_public_run_tests_envelope(self):
        wp = _load_wp_tool()
        admission, secret = self._admission_error()

        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="php", runtime_policy={},
                                 sources={"identity": "project:remote"})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target)}
        transport = SimpleNamespace(submit=Mock(side_effect=admission))
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
                patch.object(wp, "_remote_job_transport", return_value=transport):
            result = wp.run_tests("/project", mode="unit", remote="vps", workspace="php",
                                  timeout_seconds=120)

        expected = {**admission.to_payload(), "passed": False, "summary": None,
                    "output": "", "mode": "unit"}
        self.assertEqual(result, expected)
        self.assertNotIn("job_id", result)
        self.assertNotEqual(result.get("status"), "accepted")
        self.assertNotIn(secret, str(result))

    def test_remote_run_tests_reports_config_resolved_mode_not_auto(self):
        wp = _load_wp_tool()
        target = SimpleNamespace(kind="remote", project_root="/project", remote_name="vps",
                                 workspace_label="php", runtime_policy={},
                                 sources={"identity": "project:remote"})
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

    def test_instance_exec_propagates_tri_state_policy_without_defaulting_zero(self):
        from tools import jobs, runtime

        accepted = {"ok": True, "job_id": "d" * 32}
        with patch.object(jobs, "_submit_explicit_job", return_value=accepted) as submit:
            result = runtime.instance_exec(
                ["node", "--version"], "/project", local=True, timeout_seconds=0,
                execution_profile="custom", stall_seconds=12, cancel_grace_seconds=13,
                cancel_on_stall=False, cleanup_policy="ephemeral",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(submit.call_args.kwargs["timeout_seconds"], 0)
        self.assertEqual(submit.call_args.kwargs["cancel_on_stall"], False)
        self.assertEqual(submit.call_args.kwargs["cleanup_policy"], "ephemeral")
