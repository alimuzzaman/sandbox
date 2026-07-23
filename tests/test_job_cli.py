import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.jobs_runtime import _download_artifact_file, cmd_job_status


ROOT = Path(__file__).parent.parent


def _load_mcp_jobs_tool():
    fake_dependencies = types.ModuleType("dependencies")
    fake_dependencies.ToolDependencies = object
    path = ROOT / "mcp" / "wp-server" / "tools" / "jobs.py"
    spec = importlib.util.spec_from_file_location("sandbox_test_mcp_jobs", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
        spec.loader.exec_module(module)
    return module


class JobCliTests(unittest.TestCase):
    def test_test_matrix_accepts_flags_after_mode_and_returns_isolated_children(self):
        result = subprocess.run([
            str(ROOT / "sb"), "test", "matrix", "--local", "--workspace", "cli-cell-a",
            "--workspace", "cli-cell-b", "--timeout", "60", "--json", "--",
            sys.executable, "-c", "print('cli-matrix')",
        ], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"]["submitted"], 2)
        self.assertEqual({child["workspace"] for child in payload["children"]}, {"cli-cell-a", "cli-cell-b"})

    def test_successful_json_status_adds_ok_without_changing_human_rendering(self):
        state = {"job_id": "a" * 32, "lifecycle": "succeeded", "health": "terminal"}
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies",
                   return_value={"job_service": SimpleNamespace(get=lambda _job_id: dict(state))}):
            output = StringIO()
            with redirect_stdout(output):
                cmd_job_status(None, SimpleNamespace(remote=None, job_id=state["job_id"], json=True))
            self.assertTrue(json.loads(output.getvalue())["ok"])
            output = StringIO()
            with redirect_stdout(output):
                cmd_job_status(None, SimpleNamespace(remote=None, job_id=state["job_id"], json=False))
            self.assertEqual(output.getvalue().strip(), f"{state['job_id']} succeeded (terminal)")

    def test_cli_artifact_get_rejects_invalid_bounds_before_transport(self):
        from sandbox.commands.jobs_runtime import cmd_job_artifact_get
        args = SimpleNamespace(remote=None, job_id="a" * 32, artifact_id="report",
                               offset=-1, max_bytes=1, output_file=None, json=True)
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies") as dependencies:
            with self.assertRaisesRegex(ValueError, "artifact offset"):
                cmd_job_artifact_get(None, args)
            dependencies.assert_not_called()

    def test_mcp_artifact_get_rejects_invalid_bounds_without_reading_a_chunk(self):
        module = _load_mcp_jobs_tool()
        service = SimpleNamespace(list_artifacts=lambda _job_id: (_ for _ in ()).throw(
            AssertionError("service must not be called")))
        module._job_service = service
        for offset, maximum in ((-1, 1), (0, 0), (0, 1_048_577)):
            result = module.job_artifact_get("a" * 32, "report", offset=offset, max_bytes=maximum)
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "invalid_artifact_query")

    def test_cli_output_reports_stable_unavailable_error(self):
        from sandbox.commands.jobs_runtime import cmd_job_output
        service = SimpleNamespace(read_output=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("output_unavailable")))
        args = SimpleNamespace(remote=None, job_id="a" * 32, stream="combined", cursor=None,
            tail_bytes=None, max_bytes=1024, wait_seconds=0, follow=False,
            encoding="utf8", json=True)
        output = StringIO()
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies",
                   return_value={"job_service": service}), redirect_stdout(output):
            cmd_job_output(None, args)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "output_unavailable")

    def test_mcp_output_and_metrics_preserve_unavailable_errors(self):
        module = _load_mcp_jobs_tool()
        module._job_service = SimpleNamespace(
            read_output=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("output_unavailable")),
            read_metrics=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("metrics_unavailable")),
        )
        output = module.job_output("a" * 32)
        metrics = module.job_metrics("a" * 32)
        self.assertEqual(output["code"], "output_unavailable")
        self.assertEqual(metrics["code"], "metrics_unavailable")

    def test_output_file_downloads_all_chunks_and_validates_size_and_sha(self):
        payload = b"complete-artifact-payload"
        metadata = {"artifact_id": "report", "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(), "status": "available"}
        offsets = []

        def fetch(offset):
            offsets.append(offset)
            chunk = payload[offset:offset + 5]
            return {"ok": True, "offset": offset, "data": __import__("base64").b64encode(chunk).decode(),
                    "bytes_read": len(chunk), "encoding": "base64"}

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.tar"
            result = _download_artifact_file(destination, metadata, fetch)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(result["sha256"], metadata["sha256"])
            self.assertGreater(len(offsets), 1)

    def test_output_file_failure_removes_temp_and_preserves_existing_destination(self):
        payload = b"new-payload"
        metadata = {"artifact_id": "report", "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(), "status": "available"}

        def fetch(offset):
            chunk = payload[offset:offset + 4]
            if offset >= 4:
                chunk = b"corrupt"
            return {"ok": True, "offset": offset, "data": __import__("base64").b64encode(chunk).decode(),
                    "bytes_read": len(chunk), "encoding": "base64"}

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.tar"
            destination.write_bytes(b"old")
            with self.assertRaisesRegex(RuntimeError, "size|sha256"):
                _download_artifact_file(destination, metadata, fetch)
            self.assertEqual(destination.read_bytes(), b"old")
            self.assertEqual(list(destination.parent.glob(".report.tar.*")), [])
