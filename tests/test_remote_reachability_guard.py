from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import lifecycle


class RemoteEnsureReachabilityGuardTests(unittest.TestCase):
    def test_unreachable_probe_returns_bounded_retry_guidance(self):
        with patch("sandbox.core._remote.check_reachable_diagnostic", return_value={
            "reachable": False, "state": "timeout", "latency_ms": 5000,
        }) as probe:
            refusal = lifecycle._remote_ensure_reachability(
                "scaleway-sandbox", {"ssh": "untrusted-target"},
            )

        probe.assert_called_once()
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["error"]["code"], "remote_unreachable")
        self.assertIn("--local", refusal["error"]["message"])
        self.assertEqual(refusal["reachability"]["state"], "timeout")
        self.assertNotIn("untrusted-target", str(refusal))

    def test_reachable_probe_allows_deploy_path(self):
        with patch("sandbox.core._remote.check_reachable_diagnostic", return_value={
            "reachable": True, "state": "reachable", "latency_ms": 10,
        }) as probe:
            refusal = lifecycle._remote_ensure_reachability(
                "scaleway-sandbox", {"ssh": "untrusted-target"},
            )

        probe.assert_called_once()
        self.assertIsNone(refusal)

    def test_ensure_refusal_happens_before_deploy_or_workspace_staging(self):
        target = SimpleNamespace(
            kind="remote", project_root="/project", remote_name="vps",
            workspace_label="default", remote={
                "ssh": "untrusted-target", "provisioned": True,
                "capabilities": ["job.exec"],
            },
        )
        dependencies = {"target_service": SimpleNamespace(
            resolve=lambda _request: target,
        )}
        args = SimpleNamespace(
            project_dir="/project", remote=None, local=False, workspace=None,
            label=None, reveal_login=False,
        )
        refusal = {
            "ok": False,
            "error": {"code": "remote_unreachable", "message": "use --local"},
        }
        with patch("sandbox.application.context.durable_job_dependencies", return_value=dependencies), \
             patch.object(lifecycle, "_remote_ensure_reachability", return_value=refusal), \
             patch("sandbox.core._remote.deploy_exact_working_tree") as deploy, \
             patch("sandbox.core._remote.prepare_remote_workspace") as workspace:
            result = lifecycle._remote_lifecycle({}, args, "ensure")

        self.assertEqual(result, refusal)
        deploy.assert_not_called()
        workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
