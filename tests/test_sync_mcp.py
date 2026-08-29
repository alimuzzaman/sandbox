import sys
import argparse
import json
import unittest
from pathlib import Path
from unittest.mock import patch


MCP_ROOT = Path(__file__).resolve().parents[1] / "mcp" / "wp-server"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))


class _Server:
    def __init__(self):
        self.names = []

    def tool(self):
        def decorate(function):
            self.names.append(function.__name__)
            return function
        return decorate


class SyncMcpTests(unittest.TestCase):
    def test_group_registers_all_sync_tools_against_one_dependency(self):
        from dependencies import ToolDependencies
        from tools import sync

        service = object()
        server = _Server()
        with patch.object(sync, "_service", None):
            sync.register(server, ToolDependencies({"sync_service": service}))
        self.assertEqual(server.names, [
            "sync_once", "sync_status", "sync_start", "sync_stop", "sync_resolve",
        ])

    def test_sync_status_returns_bounded_failure_on_service_error(self):
        from tools import sync

        class Broken:
            def status(self, **_kwargs):
                raise RuntimeError("private path /secret")

        with patch.object(sync, "_service", Broken()):
            result = sync.sync_status("/project", "remote", "workspace")
        self.assertEqual(result, {
            "ok": False, "status": "failed", "code": "sync_failed",
            "message": "synchronization status is unavailable",
        })

    def test_start_stop_and_resolve_use_the_same_service_operations(self):
        from tools import sync

        class Service:
            def start(self, project_dir, **kwargs):
                return {"operation": "start", "project_dir": project_dir, **kwargs}
            def stop(self, project_dir, **kwargs):
                return {"operation": "stop", "project_dir": project_dir, **kwargs}
            def resolve(self, project_dir, **kwargs):
                return {"operation": "resolve", "project_dir": project_dir, **kwargs}

        with patch.object(sync, "_service", Service()):
            started = sync.sync_start("/project", "remote", "workspace", "live", "p")
            stopped = sync.sync_stop("/project", "remote", "workspace", "p")
            resolved = sync.sync_resolve(
                "/project", "remote", "workspace", "keep-local", True,
            )
        self.assertEqual(started["operation"], "start")
        self.assertEqual(stopped["operation"], "stop")
        self.assertEqual(resolved["operation"], "resolve")

    def test_cli_and_mcp_status_return_the_same_redacted_contract_fields(self):
        from sandbox.commands import sync as cli_sync
        from sandbox.sync.models import SynchronizationRelationship, success_envelope
        from tools import sync as mcp_sync

        envelope = success_envelope(SynchronizationRelationship(
            "rel_fixture", "project_fixture", "remote", "workspace",
            updated_at="2026-08-26T00:00:00Z",
        ), status="stopped")

        class Service:
            def status(self, project_dir, **kwargs):
                return envelope

        with patch.object(mcp_sync, "_service", Service()):
            mcp_result = mcp_sync.sync_status("/private/project", "remote", "workspace")
        args = argparse.Namespace(json=True)
        with patch("builtins.print") as output:
            cli_sync._emit(envelope, args)
        cli_result = json.loads(output.call_args.args[0])
        self.assertEqual(cli_result, mcp_result)
        serialized = json.dumps(mcp_result, sort_keys=True)
        self.assertNotIn("/private/project", serialized)
        self.assertNotIn("argv", serialized.lower())


if __name__ == "__main__":
    unittest.main()
