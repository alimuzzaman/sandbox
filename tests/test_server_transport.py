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
        self.assertTrue(any(getattr(route, "path", None) == "/resources" for route in app.routes))
        self.assertTrue(any(getattr(route, "path", None) == "/inventory" for route in app.routes))
        self.assertTrue(any(getattr(route, "path", None) == "/wp-cli" for route in app.routes))

    def test_resource_contract_rejects_arbitrary_actions(self):
        with self.assertRaisesRegex(ValueError, "unsupported resource action"):
            server._resource_contract({"action": "shell", "command": "id"})

    @patch("server.subprocess.run")
    @patch.dict(os.environ, {
        "SANDBOX_REMOTE_MCP_RUNTIME_REVISION": "a" * 24,
        "SANDBOX_REMOTE_MCP_MARKER": "b" * 24,
        "SANDBOX_HOME": "/srv/sandbox",
    }, clear=False)
    def test_wp_contract_selects_existing_instance_without_workspace_or_shell(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, "raw stdout\n", "raw stderr\n",
        )
        registry = types.SimpleNamespace(
            load_project_config=lambda path, label=None: {
                "root": path, "kind": "wordpress", "slug": "project",
            },
        )
        with patch("server._core", return_value=registry), \
                patch("sandbox.core.resolve_registered_instance", return_value={
                    "root": "/srv/sandbox/deploy-src/project",
                    "label": "default", "instance": "project-default",
                }):
            result = server._remote_wp_contract({
                "schema_version": 1,
                "action": "wp_cli",
                "project_slug": "project",
                "label": "default",
                "argv": ["option", "get", "siteurl"],
                "timeout_seconds": 7,
                "expected_runtime_revision": "a" * 24,
                "expected_ownership_marker": "b" * 24,
            })
        self.assertTrue(result["ok"])
        self.assertEqual(result["instance"], "project-default")
        self.assertEqual(result["stdout"], "raw stdout\n")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], [str(server.SANDBOX_ROOT / "sb"), "--instance",
                                    "project-default", "wp"])
        self.assertIn("--local", argv)
        self.assertNotIn("exec", argv)
        self.assertFalse(any("workspace" in value for value in argv))
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("server.subprocess.run")
    def test_wp_contract_rejects_revision_ownership_and_generic_before_dispatch(self, run):
        base = {
            "schema_version": 1, "action": "wp_cli", "project_slug": "project",
            "label": "default", "argv": ["core", "version"], "timeout_seconds": 7,
            "expected_runtime_revision": "a" * 24,
            "expected_ownership_marker": "b" * 24,
        }
        with patch.dict(os.environ, {
            "SANDBOX_REMOTE_MCP_RUNTIME_REVISION": "c" * 24,
            "SANDBOX_REMOTE_MCP_MARKER": "b" * 24,
        }, clear=False):
            mismatch = server._remote_wp_contract(base)
        self.assertEqual(mismatch["error"]["code"], "runtime_revision_mismatch")
        with patch.dict(os.environ, {
            "SANDBOX_REMOTE_MCP_RUNTIME_REVISION": "a" * 24,
            "SANDBOX_REMOTE_MCP_MARKER": "c" * 24,
        }, clear=False):
            mismatch = server._remote_wp_contract(base)
        self.assertEqual(mismatch["error"]["code"], "remote_service_ownership_unknown")
        run.assert_not_called()

    @patch("server.subprocess.run")
    @patch.dict(os.environ, {
        "SANDBOX_REMOTE_MCP_RUNTIME_REVISION": "a" * 24,
        "SANDBOX_REMOTE_MCP_MARKER": "b" * 24,
        "SANDBOX_HOME": "/srv/sandbox",
    }, clear=False)
    def test_wp_contract_refuses_generic_and_credential_like_argv(self, run):
        base = {
            "schema_version": 1, "action": "wp_cli", "project_slug": "project",
            "label": "default", "argv": ["core", "version"], "timeout_seconds": 7,
            "expected_runtime_revision": "a" * 24,
            "expected_ownership_marker": "b" * 24,
        }
        unsafe = server._remote_wp_contract({**base, "argv": ["option", "get", "--password=secret"]})
        self.assertEqual(unsafe["error"]["code"], "unsafe_argv")
        registry = types.SimpleNamespace(load_project_config=lambda path, label=None: {
            "root": path, "kind": "compose", "slug": "project",
        })
        with patch("server._core", return_value=registry):
            generic = server._remote_wp_contract(base)
        self.assertEqual(generic["error"]["code"], "unsupported_project_kind")
        self.assertNotIn("secret", json.dumps(unsafe))
        run.assert_not_called()

    def test_wp_contract_refuses_non_argv_and_non_boolean_options(self):
        base = {
            "schema_version": 1, "action": "wp_cli", "project_slug": "project",
            "label": "default", "argv": "core version", "timeout_seconds": 7,
            "expected_runtime_revision": "a" * 24,
            "expected_ownership_marker": "b" * 24,
        }
        with self.assertRaisesRegex(ValueError, "explicit argv"):
            server._remote_wp_contract(base)
        with self.assertRaisesRegex(ValueError, "allow_missing"):
            server._remote_wp_contract({**base, "argv": ["core", "version"],
                                        "allow_missing": "yes"})

    @patch("server.subprocess.run")
    @patch.dict(os.environ, {
        "SANDBOX_REMOTE_MCP_RUNTIME_REVISION": "a" * 24,
        "SANDBOX_REMOTE_MCP_MARKER": "b" * 24,
        "SANDBOX_HOME": "/srv/sandbox",
    }, clear=False)
    def test_wp_contract_timeout_preserves_partial_streams_and_unknown_state(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            ["sb", "wp"], 12, output=b"partial out\n", stderr=b"partial err\n",
        )
        registry = types.SimpleNamespace(load_project_config=lambda path, label=None: {
            "root": path, "kind": "wordpress", "slug": "project",
        })
        with patch("server._core", return_value=registry), \
                patch("sandbox.core.resolve_registered_instance", return_value={
                    "root": "/srv/sandbox/deploy-src/project",
                    "label": "default", "instance": "project-default",
                }):
            result = server._remote_wp_contract({
                "schema_version": 1, "action": "wp_cli", "project_slug": "project",
                "label": "default", "argv": ["option", "update", "flag", "1"],
                "timeout_seconds": 7, "expected_runtime_revision": "a" * 24,
                "expected_ownership_marker": "b" * 24,
            })
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["stdout"], "partial out\n")
        self.assertIn("partial err\n", result["stderr"])
        self.assertFalse(result["retried"])
        run.assert_called_once()

    def test_inventory_route_accepts_fast_and_explicit_deep_modes(self):
        with patch("uvicorn.run") as run:
            server._run_streamable_http("127.0.0.1", 9174, "sekrit")
        app = run.call_args.args[0]
        route = next(item for item in app.routes if getattr(item, "path", None) == "/inventory")

        class Query:
            def __init__(self, items):
                self._items = items

            def multi_items(self):
                return self._items

        class Request:
            def __init__(self, items):
                self.query_params = Query(items)

        with patch("server._hosted_inventory_snapshot", return_value={
            "ok": True, "inventory_schema": 1, "transport": "control",
            "scan_mode": "fast",
        }) as snapshot:
            response = asyncio.run(route.endpoint(Request([])))
        self.assertEqual(response.status_code, 200)
        snapshot.assert_called_once_with(deep=False)

        with patch("server._hosted_inventory_snapshot", return_value={
            "ok": True, "inventory_schema": 1, "transport": "control",
            "scan_mode": "deep",
        }) as snapshot:
            response = asyncio.run(route.endpoint(Request([("deep", "1")])))
        self.assertEqual(response.status_code, 200)
        snapshot.assert_called_once_with(deep=True)

        invalid = asyncio.run(route.endpoint(Request([("deep", "true")])))
        self.assertEqual(invalid.status_code, 400)

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
