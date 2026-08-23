from __future__ import annotations

import argparse
import json
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class DetachedResourceTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "budget": 120.0,
            "request_id": "storage-refresh-1",
            "remote": None,
            "thorough": True,
            "deep": True,
            "fast": False,
            "refresh": True,
            "cancelled": False,
            "json": True,
            "detach": True,
            "worker": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_start_uses_canonical_target_identity_and_replay_safe_submission(self):
        from sandbox.resources import detached

        target = SimpleNamespace(
            project_root=str(Path.cwd()), project_identity="project:canonical-1",
            kind="local", remote_name=None,
        )
        seen = {}

        class TargetService:
            def resolve(self, request):
                seen["request"] = request
                return target

        class JobService:
            def submit(self, submission):
                seen["submission"] = submission
                return {
                    "ok": True, "job_id": "a" * 32, "status": "accepted",
                    "kind": submission.kind,
                }

        dependencies = {
            "target_service": TargetService(), "job_service": JobService(),
        }
        with patch("sandbox.application.context.durable_job_dependencies",
                   return_value=dependencies):
            payload = detached.start(self._args())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "status")
        self.assertEqual(payload["target"], {"kind": "local", "name": "local"})
        self.assertEqual(payload["poll"]["status"],
                         "./sb job-status " + "a" * 32 + " --json")
        submission = seen["submission"]
        self.assertEqual(submission.project_identity, "project:canonical-1")
        self.assertEqual(submission.request_id, "storage-refresh-1")
        self.assertEqual(submission.kind, "resource-scan")
        self.assertEqual(submission.deadline_seconds, 240)
        self.assertEqual(set(submission.execution_policy_provenance), {
            "execution_profile", "deadline", "stall", "cancel_grace",
            "cancel_on_stall", "cleanup",
        })
        self.assertEqual(submission.argv[:4], (
            sys.executable, "-m", "sandbox.cli", "resources",
        ))
        self.assertIn("--worker", submission.argv)

    def test_worker_emits_progress_and_one_final_result(self):
        from sandbox.commands.resources import configure_parser
        from sandbox.resources import detached

        class Service:
            def status(self, **kwargs):
                kwargs["progress"]("host_filesystem")
                return {
                    "schema_version": 1, "ok": True, "action": "status",
                    "status": "partial", "target": {"kind": "local", "name": "local"},
                    "data": {"summary": {"unknown_bytes": 7}}, "error": None,
                }

        parser = argparse.ArgumentParser()
        configure_parser(parser)
        args = parser.parse_args(["status", "--worker", "--deep", "--budget", "120"])
        output = io.StringIO()
        with patch("sandbox.resources.context.resource_service", return_value=Service()), \
                redirect_stdout(output):
            self.assertEqual(detached.run_worker(args), 0)

        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["event"] for event in events], ["progress", "result"])
        self.assertEqual(events[-1]["payload"]["status"], "partial")

    def test_worker_rejects_remote_recursion(self):
        from sandbox.resources import detached

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                detached.run_worker(self._args(remote="scaleway-sandbox", worker=True)), 1,
            )
        event = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(event["payload"]["error"]["code"], "invalid_worker_target")


if __name__ == "__main__":
    unittest.main()
