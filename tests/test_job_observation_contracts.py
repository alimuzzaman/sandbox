"""Bounded CLI/MCP observation and explicit-mutation job contracts."""

from __future__ import annotations

import sys
import unittest
from argparse import ArgumentParser
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.jobs_runtime import cmd_job_cleanup, configure_cleanup_parser


MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


class JobObservationContractTests(unittest.TestCase):
    def test_cli_cleanup_requires_documented_confirmation(self):
        parser = ArgumentParser()
        configure_cleanup_parser(parser)
        missing = parser.parse_args(["a" * 32])
        with patch("sandbox.commands.jobs_runtime._die", side_effect=RuntimeError("confirmation required")):
            with self.assertRaisesRegex(RuntimeError, "confirmation required"):
                cmd_job_cleanup(None, missing)
        confirmed = parser.parse_args(["a" * 32, "--logs", "--yes", "--json"])
        service = SimpleNamespace(cleanup=lambda *args, **kwargs: {
            "ok": True, "job_id": args[0], "cleanup_state": "retained", **kwargs})
        output = StringIO()
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies",
                   return_value={"job_service": service}), patch("sys.stdout", output):
            cmd_job_cleanup(None, confirmed)
        self.assertIn('"cleanup_state": "retained"', output.getvalue())

    def test_mcp_observation_wrappers_are_bounded_and_cleanup_is_confirmed(self):
        from tools import jobs

        job_id = "a" * 32
        artifact = {"artifact_id": "report", "size_bytes": 3, "sha256": "0" * 64}
        service = SimpleNamespace(
            get=lambda identifier: {"job_id": identifier, "lifecycle": "running", "health": "active"},
            read_metrics=lambda identifier, limit: {"ok": True, "job_id": identifier, "samples": [], "limit": limit},
            list_artifacts=lambda identifier: [artifact],
            get_artifact=lambda *_args, **_kwargs: b"abc",
            cancel=lambda identifier, force=False: {"job_id": identifier, "force": force},
            retry=lambda identifier, request_id=None: {"job_id": identifier, "request_id": request_id},
            cleanup=lambda identifier, **kwargs: {"ok": True, "job_id": identifier, **kwargs},
        )
        with patch.object(jobs, "_job_service", service):
            self.assertTrue(jobs.job_status(job_id)["ok"])
            self.assertEqual(jobs.job_metrics(job_id, limit=7)["limit"], 7)
            self.assertEqual(jobs.job_artifacts(job_id)["artifacts"], [artifact])
            page = jobs.job_artifact_get(job_id, "report", offset=1, max_bytes=2)
            self.assertEqual(page["bytes_read"], 3)
            self.assertTrue(jobs.job_cancel(job_id, force=True)["ok"])
            self.assertEqual(jobs.job_retry(job_id, request_id="retry")["request_id"], "retry")
            self.assertEqual(jobs.job_cleanup(job_id)["code"], "confirmation_required")
            self.assertTrue(jobs.job_cleanup(job_id, logs=True, artifacts=False,
                                              metrics=False, confirm=True)["ok"])

