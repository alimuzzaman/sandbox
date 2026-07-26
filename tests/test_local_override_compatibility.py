"""Explicit local MCP selection remains compatible with remote-first projects."""

from types import SimpleNamespace
import unittest

from sandbox.application.target_service import TargetService
from sandbox.jobs.models import TargetRequest


class LocalOverrideCompatibilityTests(unittest.TestCase):
    def test_explicit_local_bypasses_configured_remote_and_keeps_a_local_namespace(self):
        service = TargetService(
            config_loader=lambda _root: {"root": "/project", "runtime": {
                "default": "remote", "remote": "vps", "workspace": "default",
            }},
            remote_lookup=lambda name: {"provisioned": True} if name == "vps" else None,
        )
        target = service.resolve(TargetRequest("/project", local=True, workspace="compat"))
        self.assertEqual((target.kind, target.remote_name, target.workspace_label), ("local", None, "compat"))
        self.assertTrue(target.namespace.startswith("local:"))

    def test_local_target_does_not_require_remote_capabilities(self):
        service = TargetService(
            config_loader=lambda _root: {"root": "/project", "runtime": {"default": "remote", "remote": "offline"}},
            remote_lookup=lambda _name: None,
        )
        target = service.resolve(TargetRequest("/project", local=True, required_capability="job.exec"))
        self.assertEqual(target.kind, "local")


if __name__ == "__main__":
    unittest.main()
