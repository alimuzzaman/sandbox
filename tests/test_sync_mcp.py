import sys
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
        self.assertEqual(server.names, ["sync_once", "sync_status", "sync_start", "sync_stop"])

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


if __name__ == "__main__":
    unittest.main()
