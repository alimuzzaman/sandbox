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
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
