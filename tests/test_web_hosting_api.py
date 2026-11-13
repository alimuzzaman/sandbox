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
from sandbox.hosting import panel_lifetime as pl
from sandbox.hosting import panel_records as pr
from sandbox.hosting import stack_registry
from tests.test_target_lifetime import stack_record

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
        self.fetches = []
        self.lifetime_jobs = []
        self.lifetimes = pl.LifetimeCache(self.fetch, submit=self.lifetime_jobs.append)

    def fetch(self, remote):
        self.fetches.append(remote)
        record = {**stack_record(expires="2099-01-01T00:00:00Z"), "remote_record": "present"}
        record.update(project="xspeed-hub-demo", environment="staging",
                      compose_project="sandbox-host-xspeed-hub-demo-staging")
        return {"ok": True, "remote": remote, "stacks": [record], "targets": [], "unclassified": [], "invalid": []}

    def view(self, *, server=True, registry=None, host_state=None, stacks=None, registered=None,
             registration_error=False):
        core = SimpleNamespace(registry_all=lambda: registry or {})
        with mock.patch.object(ui_dash, "_hosting_lifetimes", return_value=self.lifetimes), \
                mock.patch.object(stack_registry, "read", return_value=stacks or {"version": 1, "stacks": {}}), \
                mock.patch.object(ui_dash, "_hosts_control_service", return_value=server), \
                mock.patch("sandbox.core._remote.list_remotes", return_value=registered or {},
                           side_effect=RuntimeError("unavailable") if registration_error else None), \
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

    def test_server_view_carries_this_servers_lifetimes(self):
        write_summary(self.runtime)
        store = self.runtime / "target-lifetimes"
        store.mkdir()
        record = stack_record(expires="2000-01-01T00:00:00Z", secret_note=SECRET)
        record.update(project="xspeed-hub-demo", environment="staging",
                      compose_project="sandbox-host-xspeed-hub-demo-staging")
        (store / "sandbox-host-xspeed-hub-demo-staging.json").write_text(json.dumps(record))
        (store / "sandbox-host-broken-x.json").write_text("{")
        code, view = self.view()
        self.assertEqual(code, 200)
        lifetime = view["stacks"][0]["lifetime"]
        self.assertEqual((lifetime["lifetime"], lifetime["owner"], lifetime["reap_eligible"], lifetime["reap_reason"]),
                         ("temporary", "alice", True, "expired"))
        self.assertEqual([(row["name"], row["classification"]) for row in view["lifetimes"]],
                         [("demo", "recorded"), ("sandbox-host-broken-x", "unclassified")])
        self.assertFalse(view["lifetimes"][1]["reap_eligible"])
        self.assertIn("lifetime_record_invalid:sandbox-host-broken-x", view["partial_reasons"])
        self.assertFalse((store / ".lock").exists())
        self.assertEqual(self.fetches, [])
        self.assertNotIn(SECRET, json.dumps(view))

    def test_controller_lifetimes_come_from_stack_list_in_the_background(self):
        stacks = {"version": 1, "stacks": {"xspeed-dev/demo": {
            "remote": "xspeed-dev", "slug": "demo", "project": "xspeed-hub-demo", "environment": "staging",
            "ref": "main", "ref_branch": "main", "commit": COMMIT, "hostnames": ["hub-demo.example.test"]}}}
        code, view = self.view(server=False, stacks=stacks)
        self.assertEqual(code, 200)
        self.assertEqual(view["stacks"][0]["lifetime"]["reason"], "loading")  # never waits for the remote
        self.assertEqual(len(self.lifetime_jobs), 1)
        self.lifetime_jobs.pop()()
        self.assertEqual(self.fetches, ["xspeed-dev"])
        code, view = self.view(server=False, stacks=stacks)
        self.assertEqual(view["stacks"][0]["lifetime"]["owner"], "alice")
        self.assertEqual(view["lifetimes"][0]["remote"], "xspeed-dev")

    def controller_stacks(self, *, remote="xspeed-dev", extra=False):
        rows = {f"{remote}/demo": {
            "remote": remote, "slug": "demo", "project": "xspeed-hub-demo", "environment": "staging",
            "ref": "main", "ref_branch": "main", "commit": COMMIT, "hostnames": ["hub-demo.example.test"]}}
        if extra:
            rows[f"{remote}/remote-only"] = {
                **rows[f"{remote}/demo"], "slug": "remote-only", "project": "remote-only",
                "hostnames": ["remote-only.example.test"]}
        return {"version": 1, "stacks": rows}

    def test_owner_mirrors_do_not_fetch_an_unregistered_controller_alias(self):
        write_summary(self.runtime)
        code, view = self.view(stacks=self.controller_stacks())
        self.assertEqual(code, 200)
        self.assertEqual(self.lifetime_jobs, [])
        self.assertEqual(view["partial_reasons"], [])
        self.assertEqual([(s["slug"], s["source"]) for s in view["stacks"]], [("demo", "this-server")])

    def test_unregistered_alias_with_unmatched_controller_target_still_fetches(self):
        write_summary(self.runtime)
        self.lifetimes = pl.LifetimeCache(lambda remote: {"ok": False, "remote": remote},
                                         submit=self.lifetime_jobs.append)
        code, view = self.view(stacks=self.controller_stacks(extra=True))
        self.assertEqual(code, 200)
        self.assertEqual(len(self.lifetime_jobs), 1)
        self.assertEqual({s["slug"]: s["source"] for s in view["stacks"]},
                         {"demo": "this-server", "remote-only": "controller"})
        # The absent alias remains visibly unavailable for the unmatched target.
        self.lifetime_jobs.pop()()
        _, view = self.view(stacks=self.controller_stacks(extra=True))
        self.assertIn("lifetime_unavailable:xspeed-dev", view["partial_reasons"])

    def test_registered_mirror_still_discovers_remote_lifetime_without_a_card(self):
        write_summary(self.runtime)
        record = {**stack_record(expires="2099-01-01T00:00:00Z"), "remote_record": "present"}
        record.update(project="orphan", environment="staging", compose_project="sandbox-host-orphan-staging")
        self.lifetimes = pl.LifetimeCache(lambda remote: {"ok": True, "remote": remote, "stacks": [record]},
                                         submit=self.lifetime_jobs.append)
        self.view(stacks=self.controller_stacks(), registered={"xspeed-dev": {}})
        self.assertEqual(len(self.lifetime_jobs), 1)
        self.lifetime_jobs.pop()()
        _, view = self.view(stacks=self.controller_stacks(), registered={"xspeed-dev": {}})
        self.assertTrue(any(row["compose_project"] == "sandbox-host-orphan-staging" and not row["card"]
                            for row in view["lifetimes"]))

    def test_different_remote_is_not_covered_by_matching_project_and_environment(self):
        write_summary(self.runtime)
        code, view = self.view(stacks=self.controller_stacks(remote="other"))
        self.assertEqual(code, 200)
        self.assertEqual(len(self.lifetime_jobs), 1)
        self.assertEqual({s["source"] for s in view["stacks"]}, {"this-server", "controller"})

    def test_unknown_remote_registration_preserves_fetch(self):
        write_summary(self.runtime)
        code, _ = self.view(stacks=self.controller_stacks(), registration_error=True)
        self.assertEqual(code, 200)
        self.assertEqual(len(self.lifetime_jobs), 1)

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
                    mock.patch.object(stack_registry, "read", return_value={"version": 1, "stacks": {}}), \
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
