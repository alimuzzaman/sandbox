"""`POST /api/hosting/share`: the panel's "Share this stack" route (spec 081 follow-up).

The route mints login-bypass links, so these tests drive the real request handler on a
loopback socket and check the gate, the body rules, the result mapping, and that a
minted link reaches only the one response that carries it.
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import re
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import host_share
from sandbox.commands import ui_dash
from sandbox.hosting.share import ShareRefusal

ROOT = Path(__file__).resolve().parents[1]
# A short-form link (spec 081 Story 7): no id in the URL, one 22-character token.
LINK_ID = "l-" + "a" * 12
LEGACY_LINK_ID = "l-" + "a" * 32  # LEGACY(081 short links)
SESSION_ID = "s-" + "b" * 32
CANARY = "c4naryT0ken7c1d9e0b5a4"
LINK = f"https://s.dev.example.test/s/#{CANARY}"
SECRET = "planted-secret-value-6666666666"
CREATED = {"ok": True, "link": LINK, "link_id": LINK_ID, "target": "demo-hub/dev", "entry": "share",
           "kind": "stack", "scope": "/__sandbox/stack", "expires_at": 1_800_000_000,
           "expires_clamped": False, "uses": None, "hostname": "s.dev.example.test"}


class ShareRouteTest(unittest.TestCase):
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
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.create = self.patch("create_share", return_value=dict(CREATED))
        self.list = self.patch("list_shares", return_value={
            "ok": True, "target": "demo-hub/dev", "entry": "share",
            "links": [{"id": LINK_ID, "kind": "stack", "scope": "/__sandbox/stack", "issued_at": 1,
                       "expires_at": 2, "uses": None, "used": 0, "token_sha256": "f" * 64}],
            "sessions": [{"id": SESSION_ID, "link": LINK_ID, "created_at": 1, "expires_at": 2,
                          "secret_sha256": "e" * 64}]})
        self.revoke = self.patch("revoke_share", return_value={
            "ok": True, "target": "demo-hub/dev", "entry": "share", "revoked_links": 1,
            "revoked_sessions": 0, "revoked_keys": 1})

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(host_share, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def headers(self, **overrides):
        headers = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json",
                   "X-Sandbox-Panel": "share"}
        for key, value in overrides.items():
            key = key.replace("_", "-")
            if value is None:
                headers.pop(key, None)
            else:
                headers[key] = value
        return headers

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        connection.request(method, path, body=data, headers=headers or self.headers())
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, response.getheader("Cache-Control"), raw

    def post(self, body, **overrides):
        status, cache, raw = self.request("POST", "/api/hosting/share", body, self.headers(**overrides))
        return status, cache, json.loads(raw), raw

    def assert_no_share_call(self):
        self.create.assert_not_called()
        self.list.assert_not_called()
        self.revoke.assert_not_called()

    # ---- the gate ----

    def test_create_returns_the_link_once_without_cache(self):
        status, cache, body, _ = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache), (200, "no-store"))
        self.assertEqual(body, CREATED)
        self.create.assert_called_once_with("xspeed-dev", stack="demo", caller="panel")

    def test_requests_not_from_the_panel_page_are_refused_before_any_share_call(self):
        cases = {
            "no origin": {"Origin": None},
            "foreign origin": {"Origin": "https://evil.example.test"},
            "another loopback port": {"Origin": "http://127.0.0.1:1"},
            "localhost for an IP host": {"Origin": f"http://localhost:{self.server.server_address[1]}"},
            "null origin": {"Origin": "null"},
            "origin with a path": {"Origin": f"http://{self.host}/x"},
            "form post": {"Content-Type": "application/x-www-form-urlencoded"},
            "plain text": {"Content-Type": "text/plain"},
            "no content type": {"Content-Type": None},
            "no panel header": {"X-Sandbox-Panel": None},
            "wrong panel header": {"X-Sandbox-Panel": "1"},
            "cross-site fetch": {"Sec-Fetch-Site": "cross-site"},
            "same-site fetch": {"Sec-Fetch-Site": "same-site"},
            "foreign host": {"Host": "evil.example.test", "Origin": "http://evil.example.test"},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                status, cache, body, _ = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"},
                                                   **{k.replace("-", "_"): v for k, v in overrides.items()})
                self.assertEqual(status, 403)
                self.assertEqual(cache, "no-store")
                self.assertEqual(body["ok"], False)
        self.assert_no_share_call()

    def test_same_origin_fetch_metadata_and_charset_pass(self):
        status, _, _, _ = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"},
                                    Sec_Fetch_Site="same-origin", Content_Type="application/json; charset=utf-8")
        self.assertEqual(status, 200)

    def test_an_oversized_or_empty_body_is_refused_unread(self):
        for body in (b"", json.dumps({"action": "create", "remote": "xspeed-dev", "stack": "demo",
                                      "pad": "x" * 5000}).encode()):
            with self.subTest(len(body)):
                status, _, _, _ = self.post(body)
                self.assertEqual(status, 403)
        self.assert_no_share_call()

    def test_other_methods_and_paths_are_not_routes(self):
        status, _, _ = self.request("GET", "/api/hosting/share")
        self.assertEqual(status, 404)
        status, _, _ = self.request("POST", "/api/hosting/share?action=create",
                                    json.dumps({"action": "create", "remote": "xspeed-dev", "stack": "demo"}).encode())
        self.assertEqual(status, 404)
        self.assert_no_share_call()

    # ---- the body ----

    def test_bad_bodies_are_refused(self):
        cases = [
            b"not json",
            [],
            {"action": "mint", "remote": "xspeed-dev", "stack": "demo"},
            {"remote": "xspeed-dev", "stack": "demo"},
            {"action": "create", "remote": "xspeed-dev"},
            {"action": "create", "remote": "xspeed-dev", "stack": "demo", "ttl": "30d"},
            {"action": "create", "remote": "xspeed-dev", "stack": "demo", "id": LINK_ID},
            {"action": "create", "remote": "Bad Name", "stack": "demo"},
            {"action": "create", "remote": " xspeed-dev", "stack": "demo"},
            {"action": "create", "remote": "xspeed-dev", "stack": "../demo"},
            {"action": "create", "remote": "xspeed-dev", "stack": "Demo"},
            {"action": "create", "remote": "xspeed-dev", "stack": 7},
            {"action": "revoke", "remote": "xspeed-dev", "stack": "demo"},
            {"action": "revoke", "remote": "xspeed-dev", "stack": "demo", "id": "all"},
            {"action": "revoke", "remote": "xspeed-dev", "stack": "demo", "id": "l-short"},
            {"action": "revoke", "remote": "xspeed-dev", "stack": "demo", "id": "l-" + "a" * 13},
            {"action": "revoke", "remote": "xspeed-dev", "stack": "demo", "id": "s-" + "a" * 12},
        ]
        for body in cases:
            with self.subTest(body):
                status, cache, payload, _ = self.post(body)
                self.assertEqual((status, cache), (400, "no-store"))
                self.assertEqual((payload["ok"], payload["code"]), (False, "panel_request_invalid"))
        self.assert_no_share_call()

    def test_deeply_nested_json_is_a_bad_body(self):
        body = b"[" * 2000 + b"]" * 2000
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, cache, payload, _ = self.post(body)
        self.assertEqual((status, cache, payload["code"]), (400, "no-store", "panel_request_invalid"))
        self.assertNotIn("Traceback", err.getvalue())
        self.assert_no_share_call()

    def test_no_page_may_frame_the_panel(self):
        for method, path, body in (("GET", "/hosting", None),
                                   ("POST", "/api/hosting/share",
                                    {"action": "list", "remote": "xspeed-dev", "stack": "demo"})):
            with self.subTest(path):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
                connection.request(method, path, body=json.dumps(body).encode() if body else None,
                                   headers=self.headers() if body else {"Host": self.host})
                response = connection.getresponse()
                response.read()
                connection.close()
                self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
                self.assertEqual(response.getheader("Content-Security-Policy"), "frame-ancestors 'none'")

    # ---- results ----

    def test_list_passes_only_contract_fields(self):
        status, _, body, _ = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual(status, 200)
        self.list.assert_called_once_with("xspeed-dev", stack="demo")
        self.assertEqual(set(body["links"][0]), {"id", "kind", "scope", "issued_at", "expires_at", "uses", "used"})
        self.assertEqual(set(body["sessions"][0]), {"id", "link", "created_at", "expires_at"})

    def test_revoke_takes_one_id(self):
        for link_id in (LINK_ID, LEGACY_LINK_ID, SESSION_ID):
            with self.subTest(link_id):
                status, _, body, _ = self.post({"action": "revoke", "remote": "xspeed-dev", "stack": "demo",
                                                "id": link_id})
                self.assertEqual(status, 200)
                self.assertEqual(body["revoked_links"], 1)
                self.revoke.assert_called_with("xspeed-dev", link_id, stack="demo")

    def test_a_refusal_shows_its_code_and_message(self):
        self.create.side_effect = ShareRefusal("agent_mint_not_enabled", "set `share.agent_mint: true`")
        status, cache, body, _ = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache), (409, "no-store"))
        self.assertEqual(body, {"ok": False, "code": "agent_mint_not_enabled",
                                "message": "set `share.agent_mint: true`"})

    def test_an_unexpected_error_has_no_detail(self):
        for error in (RuntimeError(SECRET), SystemExit(SECRET)):
            with self.subTest(type(error).__name__):
                self.create.side_effect = error
                status, cache, body, raw = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
                self.assertEqual((status, cache, body["code"]), (500, "no-store", "share_failed"))
                self.assertNotIn(SECRET.encode(), raw)

    def test_create_preserves_safe_attempt_metadata_and_discards_private_extensions(self):
        self.create.return_value = {**CREATED, "attempt_id": "a-" + "c" * 32, "phase": "active",
                                    "private_body": {"token": SECRET}, "notes": [SECRET]}
        status, cache, body, raw = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache), (200, "no-store"))
        self.assertEqual(body, {**CREATED, "attempt_id": "a-" + "c" * 32, "phase": "active"})
        self.assertNotIn(SECRET.encode(), raw)

    def test_partial_create_and_revoke_preserve_cleanup_and_never_leak_extensions(self):
        grant = {"target": "r/demo/dev", "entry": "share", "id": LINK_ID,
                 "expires_at": 1800000000, "status": "unknown", "reason_code": "cleanup_unconfirmed"}
        result = {"ok": False, "attempt_id": "a-" + "c" * 32, "phase": "cleanup_pending",
                  "still_live": [{**grant, "token": SECRET, "wordpress": {"key": SECRET}}],
                  "link": LINK, "message": SECRET, "private_body": SECRET}
        for action, handler in (("create", self.create), ("revoke", self.revoke)):
            with self.subTest(action):
                handler.return_value = result
                payload = {"action": action, "remote": "xspeed-dev", "stack": "demo"}
                if action == "revoke":
                    payload["id"] = LINK_ID
                status, cache, body, raw = self.post(payload)
                self.assertEqual((status, cache, body["ok"]), (409, "no-store", False))
                self.assertEqual(body["still_live"], [grant])
                self.assertEqual(body["attempt_id"], result["attempt_id"])
                self.assertNotIn(SECRET.encode(), raw)
                self.assertNotIn(CANARY.encode(), raw)

    def test_list_preserves_durable_attempts_when_parent_is_missing(self):
        attempt = {"attempt_id": "a-" + "c" * 32, "phase": "cleanup_pending",
                   "link_id": LINK_ID, "expires_at": 1800000000}
        self.list.return_value = {"ok": False, "attempts": [{**attempt, "token": SECRET}],
                                  "links": [], "sessions": [], "partial_reasons": ["parent_unavailable", SECRET],
                                  "private_body": SECRET, "message": SECRET}
        status, cache, body, raw = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache, body["ok"]), (409, "no-store", False))
        self.assertEqual(body["attempts"], [attempt])
        self.assertNotIn(SECRET.encode(), raw)

    def test_coverage_exception_projects_only_safe_fields_and_fixed_guidance(self):
        from sandbox.hosting.share.fanout import CoverageRefusal
        error = CoverageRefusal([{"stack": "demo", "target": "r/demo/dev", "hostname": "demo.test",
                                  "piece": "access", "code": "linked_access_unknown", "fix": SECRET,
                                  "token": SECRET, "private_body": {"wordpress": SECRET}}])
        error.message = SECRET
        self.create.side_effect = error
        status, cache, body, raw = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache, body["code"]), (409, "no-store", "stack_share_coverage_incomplete"))
        self.assertEqual(set(body["gaps"][0]), {"stack", "target", "hostname", "piece", "code", "fix"})
        self.assertIn("linked-site", body["gaps"][0]["fix"])
        self.assertNotIn(SECRET.encode(), raw)

    def test_controller_grant_summaries_project_pending_rows_without_private_descriptors(self):
        grant = {"target": "r/demo/dev", "entry": "share", "id": LINK_ID,
                 "expires_at": 1800000000, "status": "unknown"}
        self.list.return_value = {"ok": False, "links": [], "sessions": [], "attempts": [{
            "attempt_id": "a-" + "c" * 32, "phase": "cleanup_pending", "link_id": LINK_ID,
            "expires_at": 1800000000, "grants": [{**grant, "cleanup": {"wordpress_key": SECRET},
                                                    "token": SECRET}], "private_body": SECRET}]}
        status, cache, body, raw = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual((status, cache), (409, "no-store"))
        self.assertEqual(body["attempts"][0]["still_live"], [{**grant, "reason_code": "cleanup_unconfirmed"}])
        self.assertNotIn(SECRET.encode(), raw)

    def test_every_share_route_response_disables_cache(self):
        for method in ("GET", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method):
                status, cache, _ = self.request(method, "/api/hosting/share")
                self.assertEqual((status, cache), (404 if method in ("GET", "DELETE") else 501, "no-store"))

    def test_allowed_fields_also_reject_nested_or_secret_values(self):
        self.list.return_value = {"ok": True, "target": "https://private.test/" + SECRET,
            "entry": {"secret": SECRET}, "links": [{"id": LINK_ID, "kind": SECRET,
            "scope": "https://private.test/" + SECRET, "expires_at": {"key": SECRET}}],
            "sessions": [{"id": SESSION_ID, "link": SECRET}],
            "attempts": [{"attempt_id": SECRET, "phase": SECRET, "link_id": LINK_ID,
                          "expires_at": SECRET}], "partial_reasons": [SECRET]}
        _, cache, body, raw = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"})
        self.assertEqual(cache, "no-store")
        self.assertNotIn(SECRET.encode(), raw)
        self.assertNotIn(b"private.test", raw)
        self.assertFalse(body["ok"])

    def test_the_link_reaches_only_the_minting_response(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            _, _, _, created = self.post({"action": "create", "remote": "xspeed-dev", "stack": "demo"})
            _, _, _, listed = self.post({"action": "list", "remote": "xspeed-dev", "stack": "demo"})
            with mock.patch.object(ui_dash, "_hosting_view", return_value=(200, {"ok": True})):
                _, _, hosting = self.request("GET", "/api/hosting", headers={"Host": self.host})
        self.assertIn(CANARY.encode(), created)
        for name, text in (("list", listed), ("hosting", hosting), ("stdout", out.getvalue().encode()),
                           ("stderr", err.getvalue().encode())):
            with self.subTest(name):
                self.assertNotIn(CANARY.encode(), text)


class ShareRecoveryPureTests(unittest.TestCase):
    def test_node_recovery_suite(self):
        import shutil
        from tests.subprocess_support import run_test_process
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        version = run_test_process([node, "--version"], capture_output=True, text=True, timeout=10)
        match = re.match(r"v(\d+)", version.stdout)
        if not match or int(match.group(1)) < 24:
            self.skipTest("node 24 or newer runs TypeScript tests directly")
        result = run_test_process([node, "--test", "src/web/tests/share-recovery.test.ts"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-1000:])


class ShareDialogSourceTests(unittest.TestCase):
    """The dialog shows the link once: as an input's value, never in markup or storage."""

    def test_the_link_is_set_as_a_property_only(self):
        source = (ROOT / "src/web/src/pages/share.ts").read_text(encoding="utf-8")
        uses = [line.strip() for line in source.splitlines() if re.search(r"\.link\b", line)
                and "row.link" not in line and "holdsLink" not in line]
        self.assertEqual(uses, ["input.value = result.link;"])
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn("setAttribute(\"value\"", source)

    def test_the_bundle_carries_the_id_rule_for_short_and_old_ids(self):
        labels = (ROOT / "src/web/src/ui/sharelabels.ts").read_text(encoding="utf-8")
        rule = re.search(r"return (/\^.+\$/)\.test\(id\)", labels).group(1)
        self.assertIn("{12}", rule)
        self.assertIn(rule, (ROOT / "config/sandbox-web.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
