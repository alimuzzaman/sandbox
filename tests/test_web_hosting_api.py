"""`GET /api/hosting`: the read-only stacks and hosted sites endpoint (spec 076)."""
from __future__ import annotations

import contextlib
import http.client
import inspect
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import sandbox.core._dash as dash
import sandbox.core._hosting as hosting_core
from sandbox.commands import ui_dash
from sandbox.hosting import panel_health
from sandbox.hosting import panel_records as pr

COMMIT = "c" * 40
SECRET = "planted-secret-value-5555555555"


def write_summary(runtime: Path) -> None:
    summary = pr.parse_target_summary({
        "schema": 1, "remote": "xspeed-dev", "project": "xspeed-hub-demo", "environment": "staging",
        "compose_project": "sandbox-host-xspeed-hub-demo-staging", "loopback_port": 18017,
        "healthcheck_path": "/health",
        "routes": [{"hostname": "hub-demo.example.test", "mode": "serve", "primary": True}],
        "commit": COMMIT,
        "last_apply": {"status": "succeeded", "started_at": 1, "finished_at": 2, "request_id": "req-42"},
        "stack": {"slug": "demo", "ref": "main", "ref_branch": "main", "commit": COMMIT,
                  "template": "sandbox.hosting.yml", "ports": {"mailpit": 18142}, "created_at": 1},
    })
    target = runtime / "hosts" / "xspeed-hub-demo" / "staging"
    target.mkdir(parents=True)
    (target / "summary.json").write_text(pr.encode_record(summary))
    (target / "environment.env").write_text(f"PG_PASSWORD='{SECRET}'\n")


class HostingViewTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        self.runtime.mkdir()
        self.jobs = []
        self.cache = panel_health.HealthCache(submit=self.jobs.append, probe_fn=lambda *a: {})

    def view(self, *, server=True, registry=None, host_state=None):
        core = SimpleNamespace(registry_all=lambda: registry or {})
        with mock.patch.object(ui_dash, "_hosts_control_service", return_value=server), \
                mock.patch("sandbox.core._paths.RUNTIME_DIR", self.runtime), \
                mock.patch.object(hosting_core, "load_host_state", return_value=host_state or {"hosts": {}}), \
                mock.patch.object(ui_dash, "_core", return_value=core), \
                mock.patch.object(ui_dash, "_instance_route_file_names", return_value=[]), \
                mock.patch.object(ui_dash, "_hosting_health", return_value=self.cache):
            return ui_dash._hosting_view()

    def test_server_view(self):
        write_summary(self.runtime)
        code, view = self.view(registry={"k": {"instance": "sandbox-wp-demo",
                                               "url": "https://wp-demo.example.test"}})
        self.assertEqual(code, 200)
        self.assertEqual(view["sources"], ["this-server"])
        (stack,) = view["stacks"]
        self.assertEqual(stack["commit"], COMMIT)  # the redactor keeps commits
        self.assertEqual(stack["last_apply"]["request_id"], "req-42")
        self.assertEqual([row["hostname"] for row in stack["servers"]],
                         ["hub-demo.example.test", "wp-demo.example.test"])
        self.assertEqual(stack["servers"][1]["origin"], "matched_by_name")
        self.assertEqual(stack["servers"][0]["health"]["status"], "checking")
        self.assertEqual(len(self.jobs), 2)  # scheduled, not awaited
        self.assertNotIn(SECRET, json.dumps(view))

    def test_controller_only_where_not_a_server(self):
        write_summary(self.runtime)
        code, view = self.view(server=False, registry={"k": {"instance": "i", "url": "https://wp-demo.example.test"}})
        self.assertEqual(code, 200)
        self.assertEqual(view["sources"], [])
        self.assertEqual(view["stacks"], [])

    def test_unreadable_controller_state_is_partial(self):
        with mock.patch.object(hosting_core, "load_host_state", side_effect=hosting_core.HostingError("x")):
            core = SimpleNamespace(registry_all=lambda: {})
            with mock.patch.object(ui_dash, "_hosts_control_service", return_value=False), \
                    mock.patch("sandbox.core._paths.RUNTIME_DIR", self.runtime), \
                    mock.patch.object(ui_dash, "_core", return_value=core), \
                    mock.patch.object(ui_dash, "_hosting_health", return_value=self.cache):
                code, view = ui_dash._hosting_view()
        self.assertEqual(code, 200)
        self.assertEqual(view["partial_reasons"], ["hosting_state_unavailable"])

    def test_unexpected_error_has_no_detail(self):
        with mock.patch.object(ui_dash, "_hosts_control_service", side_effect=RuntimeError(SECRET)):
            code, view = ui_dash._hosting_view()
        self.assertEqual((code, view), (500, {"ok": False, "error": "hosting view unavailable"}))

    def test_no_hosting_action_exists(self):
        source = inspect.getsource(dash._web_dispatch)
        for word in ("hosting", "host apply", "host_stack", "panel_"):
            self.assertNotIn(word, source)


class HostingRouteTest(unittest.TestCase):
    """Drive the real request handler on a loopback socket."""

    def setUp(self):
        captured = {}

        class Capture:
            def __init__(self, address, handler):
                captured["handler"] = handler

            def serve_forever(self):
                raise KeyboardInterrupt

            def shutdown(self):
                pass

            def server_close(self):
                pass
        args = SimpleNamespace(port=8765, exact_port=True, open=False, host=["127.0.0.1"])
        with mock.patch("http.server.ThreadingHTTPServer", Capture), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ui_dash.cmd_web({}, args)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), captured["handler"])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        connection.request(method, path, headers={"Host": "127.0.0.1"})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, response.getheader("Cache-Control"), body

    def test_get_returns_view_without_cache(self):
        with mock.patch.object(ui_dash, "_hosting_view", return_value=(200, {"ok": True})):
            status, cache, body = self.request("GET", "/api/hosting")
        self.assertEqual((status, cache, json.loads(body)), (200, "no-store", {"ok": True}))

    def test_query_is_refused(self):
        with mock.patch.object(ui_dash, "_hosting_view") as view:
            status, _, _ = self.request("GET", "/api/hosting?host=evil.example.test")
        self.assertEqual(status, 400)
        view.assert_not_called()

    def test_post_is_not_a_route(self):
        status, _, _ = self.request("POST", "/api/hosting")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
