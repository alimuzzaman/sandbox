"""Unit tests for mcp/wp-server/server.py's transport-selection branch
(specs/014-remote-vps-hosting/, spec FR-014/FR-015).

`mcp/wp-server` isn't a normal importable package (hyphenated dir name, and
server.py does `from app import mcp` assuming it's run with that directory as
cwd/sys.path[0]) -- so this test inserts it onto sys.path directly, same as
how `sb mcp` itself invokes it.

This needs the MCP venv's own dependencies (httpx, mcp, starlette, uvicorn),
NOT the CLI venv the rest of this repo's tests run under -- run it via:

    mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v

The whole-suite `.cli-venv/bin/python -m unittest discover -s tests` run
(this repo's main convention) does NOT have those deps, so this module skips
itself cleanly there instead of erroring out the whole discovery run.
"""
import asyncio
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_DIR))
os.environ.setdefault("SANDBOX_ROOT", str(ROOT))

try:
    import server  # noqa: E402
except ImportError as e:
    raise unittest.SkipTest(
        f"mcp/wp-server's own dependencies aren't importable here ({e}) -- "
        "run this file with mcp/wp-server/.venv/bin/python instead"
    )


class TestParseTransportArgs(unittest.TestCase):
    def test_no_flags_defaults_to_stdio(self):
        # This is the FR-015 release gate: an existing `sb mcp` invocation
        # (no flags at all) must resolve to exactly the same stdio default
        # as before this feature existed.
        opts = server._parse_transport_args([])
        self.assertEqual(opts["transport"], "stdio")
        self.assertIsNone(opts["bind"])
        self.assertIsNone(opts["port"])
        self.assertIsNone(opts["token"])

    def test_streamable_http_flags_parsed(self):
        opts = server._parse_transport_args(
            ["--transport", "streamable-http", "--bind", "100.64.1.2",
             "--port", "9174", "--token", "sekrit"])
        self.assertEqual(opts["transport"], "streamable-http")
        self.assertEqual(opts["bind"], "100.64.1.2")
        self.assertEqual(opts["port"], 9174)
        self.assertEqual(opts["token"], "sekrit")

    def test_remote_service_uses_environment_token_without_argv(self):
        old = os.environ.get("SANDBOX_REMOTE_MCP_TOKEN")
        os.environ["SANDBOX_REMOTE_MCP_TOKEN"] = "environment-secret"
        try:
            opts = server._parse_transport_args(
                ["--transport", "streamable-http", "--bind", "127.0.0.1", "--port", "9174"])
        finally:
            if old is None:
                os.environ.pop("SANDBOX_REMOTE_MCP_TOKEN", None)
            else:
                os.environ["SANDBOX_REMOTE_MCP_TOKEN"] = old
        self.assertEqual(opts["token"], "environment-secret")

    def test_mismatched_token_sources_are_rejected(self):
        old = os.environ.get("SANDBOX_REMOTE_MCP_TOKEN")
        os.environ["SANDBOX_REMOTE_MCP_TOKEN"] = "environment-secret"
        try:
            with self.assertRaises(SystemExit):
                server._parse_transport_args(["--token", "argv-secret"])
        finally:
            if old is None:
                os.environ.pop("SANDBOX_REMOTE_MCP_TOKEN", None)
            else:
                os.environ["SANDBOX_REMOTE_MCP_TOKEN"] = old


class TestStreamableHttpSafetyGates(unittest.TestCase):
    def test_memory_snapshot_reports_total_used_available_and_percent(self):
        class Meminfo:
            def read_text(self):
                return "MemTotal:       4194304 kB\nMemAvailable:   440320 kB\n"

        snapshot = server._memory_snapshot(Meminfo())
        self.assertEqual(snapshot["memory_total_mb"], 4096)
        self.assertEqual(snapshot["memory_available_mb"], 430)
        self.assertEqual(snapshot["memory_used_mb"], 3666)
        self.assertEqual(snapshot["memory_used_percent"], 89.5)

    def test_refuses_0_0_0_0_bind(self):
        with self.assertRaises(SystemExit):
            server._run_streamable_http("0.0.0.0", 9174, "sekrit")

    def test_pinned_fastmcp_builds_the_streamable_http_app(self):
        """Exercise the v1 API used by the remote service without opening a port."""
        with patch("uvicorn.run") as run:
            server._run_streamable_http("127.0.0.1", 9174, "sekrit", "https://control.example.test")
        app = run.call_args.args[0]
        self.assertEqual(run.call_args.kwargs, {"host": "127.0.0.1", "port": 9174})
        self.assertTrue(any(getattr(route, "path", None) == "/diagnostics" for route in app.routes))

    @patch("server.subprocess.run")
    def test_process_snapshot_uses_only_fixed_argv_probes(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "10 1 2.5 1.0 20 worker\n", ""),
            subprocess.CompletedProcess([], 1, "", "unavailable"),
        ]
        snapshot = server._diagnostic_process_snapshot()
        self.assertEqual(snapshot["diagnostics_schema"], 2)
        self.assertEqual(snapshot["transport"], "control")
        self.assertEqual(snapshot["process_view"]["processes"][0]["name"], "worker")
        self.assertEqual(run.call_args_list[0].args[0], [
            "ps", "-eo", "pid=,ppid=,pcpu=,pmem=,rss=,comm=",
        ])
        self.assertEqual(run.call_args_list[1].args[0], [
            "docker", "stats", "--no-stream", "--format", "{{json .}}",
        ])
        for call in run.call_args_list:
            self.assertNotIn("shell", call.kwargs)

    @patch("server.subprocess.run")
    def test_optional_docker_view_survives_unavailable_ps(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 1, "", "unavailable"),
            subprocess.CompletedProcess(
                [], 0,
                '{"Name":"web","CPUPerc":"1%","MemUsage":"2MiB / 4MiB",'
                '"MemPerc":"50%","PIDs":"3"}\n',
                "",
            ),
        ]
        snapshot = server._diagnostic_process_snapshot()
        self.assertEqual(snapshot["process_view"]["status"], "unavailable")
        self.assertEqual(snapshot["containers"]["status"], "complete")

    def test_diagnostics_route_validates_query_and_preserves_default(self):
        with patch("uvicorn.run") as run:
            server._run_streamable_http("127.0.0.1", 9174, "sekrit")
        app = run.call_args.args[0]
        route = next(item for item in app.routes if getattr(item, "path", None) == "/diagnostics")

        class Query:
            def __init__(self, items):
                self._items = items

            def multi_items(self):
                return self._items

        default = asyncio.run(route.endpoint(types.SimpleNamespace(query_params=Query([]))))
        self.assertEqual(default.status_code, 200)
        self.assertNotIn("diagnostics_schema", json.loads(default.body))

        invalid = asyncio.run(route.endpoint(types.SimpleNamespace(
            query_params=Query([("processes", "true")])
        )))
        self.assertEqual(invalid.status_code, 400)

        process_snapshot = {
            "diagnostics_schema": 2, "transport": "control",
            "capabilities": ["process_view", "container_view"],
            "process_view": {"status": "complete"},
            "containers": {"status": "unavailable"},
        }
        with patch("server._diagnostic_process_snapshot", return_value=process_snapshot):
            response = asyncio.run(route.endpoint(types.SimpleNamespace(
                query_params=Query([("processes", "1")])
            )))
        self.assertEqual(json.loads(response.body)["diagnostics_schema"], 2)


if __name__ == "__main__":
    unittest.main()
