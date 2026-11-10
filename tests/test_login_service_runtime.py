"""The login service over real loopback sockets, with fake upstreams."""
import base64
import hashlib
import http.client
import io
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.hosting import login_service as login  # noqa: E402
from sandbox.hosting.login_service import runtime  # noqa: E402

CREDENTIAL = "capture-password-sentinel"
GATE = "gate.example.test"
APP_PATH = "/__sandbox/login/app"
INBOX_PATH = "/__sandbox/login/inbox"
VERIFY = "https://app.example.test/login/verify?token=one&return=https%3A%2F%2Fapp.example.test"


class _Fake(http.server.ThreadingHTTPServer):
    daemon_threads = True


class _Capture(http.server.BaseHTTPRequestHandler):
    """A capture API with Basic auth, one UI route, and a websocket echo."""

    def log_message(self, *_args):
        pass

    def _send(self, status, body=b"{}", headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        store = self.server.store
        store["seen_headers"].append(dict(self.headers))
        store["seen_paths"].append(self.path)
        expected = "Basic " + base64.b64encode(f"capture:{CREDENTIAL}".encode()).decode()
        if not store.get("no_login") and self.headers.get("Authorization") != expected:
            return self._send(401, b"login", [("WWW-Authenticate", 'Basic realm="mail"')])
        if self.path.startswith("/mail/api/v1/messages"):
            messages = [{"ID": item["ID"], "Created": item["Created"]} for item in store["messages"]]
            return self._send(200, json.dumps({"messages": messages}).encode())
        if self.path.startswith("/mail/api/v1/message/"):
            wanted = self.path.rsplit("/", 1)[1]
            for item in store["messages"]:
                if item["ID"] == wanted:
                    return self._send(200, json.dumps(item).encode())
            return self._send(404)
        if self.path == "/mail/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            data = self.rfile.read(4)
            self.connection.sendall(b"echo:" + data)
            self.close_connection = True
            return None
        return self._send(200, b"inbox", [("Content-Type", "text/plain")])


class _App(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        store = self.server.store
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        store["requests"].append({"body": body, "host": self.headers.get("Host"),
                                  "proto": self.headers.get("X-Forwarded-Proto")})
        status = store.get("status", 200)
        if status == 200 and store.get("mail") is not None:
            mail = store["mail"]
            mail = mail(body) if callable(mail) else mail
            store["capture"]["messages"].append(mail)
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


def _message(identifier, to="person@example.test", text=VERIFY, created=None):
    return {"ID": identifier, "To": [{"Address": to}], "Text": text, "HTML": "",
            "Created": created or dt_now()}


def dt_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 1))


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class _Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)

        self.capture_store = {"messages": [], "seen_headers": [], "seen_paths": []}
        self.capture = _Fake(("127.0.0.1", 0), _Capture)
        self.capture.store = self.capture_store
        _serve(self.capture)
        self.addCleanup(self.capture.shutdown)

        self.app_store = {"requests": [], "capture": self.capture_store,
                          "mail": lambda body: _message("new", to=body["email"])}
        self.app = _Fake(("127.0.0.1", 0), _App)
        self.app.store = self.app_store
        _serve(self.app)
        self.addCleanup(self.app.shutdown)

        self.logs = []
        self.now = time.time()
        self.config = {
            "enabled_until_epoch": int(self.now) + 86400,
            "session_ttl_seconds": 3600,
            "capture": {"adapter": "mailpit_v1",
                        "api_url": f"http://127.0.0.1:{self.capture.server_address[1]}/mail",
                        "username": "capture"},
            "entries": {
                "app": {
                    "mode": "magic_link", "requires_email": True, "hostname": GATE,
                    "path": APP_PATH,
                    "magic_link": {
                        "request_url": f"http://127.0.0.1:{self.app.server_address[1]}/login/request",
                        "request_origin": "https://app.example.test",
                        "link_origin": "https://app.example.test",
                        "link_path": "/login/verify",
                        "link_pattern": r"https://app\.example\.test/login/verify\?[^\s\"<>]+",
                        "required_params": {"return": "https://app.example.test"},
                        "poll_timeout_seconds": 1,
                    },
                },
                "inbox": {
                    "mode": "inbox_gate", "requires_email": False, "hostname": GATE,
                    "path": INBOX_PATH, "landing_path": "/mail/",
                },
            },
        }
        self._start()

    def _start(self):
        self.public, self.health = runtime.create_servers(
            self.config, CREDENTIAL, self.state_dir,
            clock=lambda: self.now, log=self.logs.append,
            public_address=("127.0.0.1", 0), health_address=("127.0.0.1", 0))
        _serve(self.public)
        _serve(self.health)
        self.addCleanup(self.public.shutdown)
        self.addCleanup(self.health.shutdown)

    def issue(self, name="inbox", token="t" * 43, *, email=None, ttl=600, expires_at=None):
        issued_at = int(self.now)
        record = {"schema": "sandbox.login-token.v1", "entry": name,
                  "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                  "issued_at": issued_at,
                  "expires_at": expires_at if expires_at is not None else issued_at + ttl}
        if email:
            record["email"] = email
        (self.state_dir / f"{name}.json").write_text(json.dumps(record))
        return token

    def request(self, method, path, *, body=None, headers=None, host=GATE, server=None):
        target = server or self.public
        connection = http.client.HTTPConnection("127.0.0.1", target.server_address[1], timeout=5)
        sent = {"Host": host, **(headers or {})}
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def consume(self, path=INBOX_PATH, token="t" * 43, **fields):
        body = urllib.parse.urlencode({"token": token, **fields})
        return self.request("POST", path, body=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})

    def session(self):
        self.issue()
        response, _ = self.consume()
        self.assertEqual(response.status, 303)
        return response.getheader("Set-Cookie").split(";", 1)[0]


class StandingAccessTests(_Base):
    def test_no_live_token_means_every_public_route_is_not_found(self):
        for method, path in (("GET", "/"), ("GET", "/mail/"), ("GET", INBOX_PATH), ("GET", APP_PATH),
                             ("POST", INBOX_PATH), ("GET", "/healthz")):
            with self.subTest(method=method, path=path):
                response, _ = self.request(method, path, body="" if method == "POST" else None)
                self.assertEqual(response.status, 404)

    def test_unknown_host_is_not_found_even_with_a_live_token(self):
        self.issue()
        response, _ = self.request("GET", INBOX_PATH, host="other.example.test")
        self.assertEqual(response.status, 404)

    def test_health_answers_only_on_the_internal_listener(self):
        response, _ = self.request("GET", "/healthz", server=self.health)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.request("GET", "/healthz")[0].status, 404)

    def test_expired_service_is_off_even_with_a_valid_session(self):
        cookie = self.session()
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 200)
        self.now = self.config["enabled_until_epoch"] + 1
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 404)
        self.issue(expires_at=int(self.now) + 300)
        self.assertEqual(self.request("GET", INBOX_PATH)[0].status, 404)

    def test_expired_token_is_not_live(self):
        self.issue(expires_at=int(self.now) - 1)
        self.assertEqual(self.request("GET", INBOX_PATH)[0].status, 404)

    def test_malformed_record_is_not_live(self):
        (self.state_dir / "inbox.json").write_text("{not json")
        self.assertEqual(self.request("GET", INBOX_PATH)[0].status, 404)

    def test_record_for_another_entry_is_not_live(self):
        token = self.issue("app", email="person@example.test")
        (self.state_dir / "inbox.json").write_text((self.state_dir / "app.json").read_text())
        self.assertEqual(self.consume(token=token)[0].status, 404)


class ConfirmationTests(_Base):
    def test_confirm_page_does_not_consume(self):
        self.issue()
        for _ in range(3):
            response, body = self.request("GET", INBOX_PATH)
            self.assertEqual(response.status, 200)
            self.assertIn(b"<form", body)
        self.assertEqual(self.consume()[0].status, 303)

    def test_head_does_not_consume(self):
        self.issue()
        self.assertEqual(self.request("HEAD", INBOX_PATH)[0].status, 200)
        self.assertEqual(self.consume()[0].status, 303)

    def test_confirm_page_is_locked_down(self):
        self.issue()
        response, _ = self.request("GET", INBOX_PATH)
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", response.getheader("Cache-Control"))
        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("form-action 'self'", policy)

    def test_confirm_page_never_embeds_the_token(self):
        token = self.issue()
        self.assertNotIn(token.encode(), self.request("GET", INBOX_PATH)[1])

    def test_wrong_token_does_not_consume(self):
        self.issue()
        self.assertEqual(self.consume(token="w" * 43)[0].status, 403)
        self.assertEqual(self.consume()[0].status, 303)

    def test_malformed_token_is_refused_without_consuming(self):
        self.issue()
        self.assertEqual(self.consume(token="../../etc")[0].status, 403)
        self.assertEqual(self.consume(token="")[0].status, 403)
        self.assertEqual(self.consume()[0].status, 303)

    def test_second_use_is_gone(self):
        self.issue()
        self.assertEqual(self.consume()[0].status, 303)
        self.assertEqual(self.consume()[0].status, 410)

    def test_used_token_stops_being_live(self):
        self.issue()
        self.consume()
        self.assertEqual(self.request("GET", INBOX_PATH)[0].status, 404)

    def test_concurrent_confirmations_yield_one_session(self):
        self.issue()
        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = list(pool.map(lambda _: self.consume()[0].status, range(16)))
        self.assertEqual(statuses.count(303), 1, statuses)
        self.assertEqual(statuses.count(410), 15, statuses)

    def test_cross_origin_confirmation_is_refused_without_consuming(self):
        self.issue()
        body = urllib.parse.urlencode({"token": "t" * 43})
        response, _ = self.request("POST", INBOX_PATH, body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "Origin": "https://evil.example.test"})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.consume()[0].status, 303)

    def test_oversized_body_is_refused(self):
        self.issue()
        response, _ = self.request("POST", INBOX_PATH, body="x" * 70000, headers={
            "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status, 413)

    def test_other_methods_are_refused(self):
        self.issue()
        self.assertEqual(self.request("DELETE", INBOX_PATH)[0].status, 405)


class InboxGateTests(_Base):
    def test_redirect_has_no_token_and_no_referrer(self):
        self.issue()
        response, _ = self.consume()
        self.assertEqual(response.getheader("Location"), "/mail/")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")

    def test_cookie_is_host_only_strict_and_secure(self):
        self.issue()
        cookie = self.consume()[0].getheader("Set-Cookie")
        self.assertTrue(cookie.startswith("__Host-sandbox_login_inbox="))
        for attribute in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain", cookie)

    def test_cookie_ends_with_the_browser_session(self):
        self.issue()
        cookie = self.consume()[0].getheader("Set-Cookie")
        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("Expires", cookie)

    def test_server_side_limit_still_applies_to_a_browser_session_cookie(self):
        self.config["session_ttl_seconds"] = 172800
        self.config["enabled_until_epoch"] = int(self.now) + 5 * 86400
        cookie = self.session()
        self.now += 172799
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 200)
        self.now += 2
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 404)

    def test_session_reaches_the_inbox_with_the_gate_credential(self):
        cookie = self.session()
        response, body = self.request("GET", "/mail/", headers={"Cookie": cookie})
        self.assertEqual((response.status, body), (200, b"inbox"))
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")

    def test_gate_cookie_and_client_credential_never_reach_the_inbox(self):
        cookie = self.session()
        self.request("GET", "/mail/", headers={"Cookie": cookie + "; other=1",
                                               "Authorization": "Basic Zm9vOmJhcg=="})
        seen = self.capture_store["seen_headers"][-1]
        self.assertNotIn("__Host-sandbox_login", seen.get("Cookie", ""))
        self.assertIn("other=1", seen.get("Cookie", ""))
        self.assertNotEqual(seen.get("Authorization"), "Basic Zm9vOmJhcg==")

    def test_forged_cookie_is_refused(self):
        cookie = self.session()
        name, value = cookie.split("=", 1)
        forged = f"{name}=x{value[1:]}"
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": forged})[0].status, 404)

    def test_expired_session_is_refused(self):
        cookie = self.session()
        self.now += self.config["session_ttl_seconds"] + 1
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 404)

    def test_session_for_one_entry_does_not_open_another(self):
        cookie = self.session()
        forged = cookie.replace("sandbox_login_inbox", "sandbox_login_app")
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": forged})[0].status, 404)

    def test_visitor_without_session_gets_the_normal_login_while_a_token_is_live(self):
        self.issue()
        response, _ = self.request("GET", "/mail/")
        self.assertEqual(response.status, 401)
        self.assertNotIn("Authorization", self.capture_store["seen_headers"][-1])

    def test_websocket_is_tunnelled_only_with_a_session(self):
        cookie = self.session()
        sock = socket.create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        sock.sendall((f"GET /mail/ws HTTP/1.1\r\nHost: {GATE}\r\nCookie: {cookie}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        head = sock.recv(1024)
        self.assertIn(b"101", head)
        sock.sendall(b"ping")
        self.assertEqual(sock.recv(1024), b"echo:ping")
        sock.close()

    def test_websocket_without_a_session_gets_nothing(self):
        sock = socket.create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        sock.sendall((f"GET /mail/ws HTTP/1.1\r\nHost: {GATE}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        self.assertIn(b"404", sock.recv(1024))
        sock.close()


class RequestHardeningTests(_Base):
    def test_oversized_cookie_expiry_is_refused_cleanly(self):
        cookie = "__Host-sandbox_login_inbox=" + "9" * 5000 + ".x"
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 404)

    def test_non_ascii_digits_in_a_cookie_are_refused(self):
        cookie = "__Host-sandbox_login_inbox=" + "\u0663" * 3 + ".x"
        self.assertEqual(self.request(
            "GET", "/mail/", headers={"Cookie": cookie.encode("utf-8").decode("latin-1")})[0].status, 404)

    def test_malformed_host_is_not_found(self):
        self.issue()
        for host in ("gate.example.test:evil", "x:gate.example.test", "gate.example.test:80:80"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", INBOX_PATH, host=host)[0].status, 404)

    def test_host_with_a_port_still_matches(self):
        self.issue()
        self.assertEqual(self.request("GET", INBOX_PATH, host=f"{GATE}:443")[0].status, 200)

    def test_absolute_form_targets_are_not_served(self):
        cookie = self.session()
        response, _ = self.request(
            "GET", "http://capture.example.test/mail/", headers={"Cookie": cookie})
        self.assertEqual(response.status, 404)

    def test_protocol_relative_target_never_reaches_the_upstream_as_an_authority(self):
        cookie = self.session()
        self.request("GET", "//evil.example.test/mail/", headers={"Cookie": cookie})
        self.assertFalse([path for path in self.capture_store["seen_paths"] if path.startswith("//")])

    def test_bytes_sent_with_the_upgrade_reach_the_upstream(self):
        cookie = self.session()
        sock = socket.create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        sock.sendall((f"GET /mail/ws HTTP/1.1\r\nHost: {GATE}\r\nCookie: {cookie}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\nping").encode())
        received = b""
        while b"echo:ping" not in received:
            chunk = sock.recv(1024)
            self.assertTrue(chunk, received)
            received += chunk
        sock.close()


class ServiceDeadlineTests(_Base):
    def _post_after_cutoff(self, path, token):
        """Dispatch while enabled, then cross the cutoff before the body arrives."""
        gate = self.public.gate
        gate.config["enabled_until_epoch"] = int(self.now) + 10
        dispatched = threading.Event()
        original = gate.enabled
        def enabled():
            result = original()
            dispatched.set()
            return result
        gate.enabled = enabled
        body = urllib.parse.urlencode({"token": token}).encode()
        sock = socket.create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall((f"POST {path} HTTP/1.1\r\nHost: {GATE}\r\n"
                      "Content-Type: application/x-www-form-urlencoded\r\n"
                      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode())
        self.assertTrue(dispatched.wait(5))
        self.now += 20
        sock.sendall(body)
        return sock.recv(4096)

    def test_cutoff_after_dispatch_leaves_the_token_unclaimed(self):
        token = self.issue(ttl=600)
        reply = self._post_after_cutoff(INBOX_PATH, token)
        self.assertIn(b" 404 ", reply.split(b"\r\n", 1)[0] + b" ")
        self.assertNotIn(b"Set-Cookie", reply)
        self.assertEqual(list((self.state_dir / "used").iterdir()), [])

    def test_cutoff_during_the_exchange_returns_no_link(self):
        self.issue("app", "m" * 43, email="person@example.test")
        gate = self.public.gate
        gate.config["enabled_until_epoch"] = int(self.now) + 10
        def late_sign_in(_settings, _email):
            self.now += 20
            return VERIFY
        gate.sign_in = late_sign_in
        response, _ = self.consume(APP_PATH, "m" * 43)
        self.assertEqual(response.status, 404)
        self.assertIsNone(response.getheader("Location"))

    def _pump(self, session_expires=None):
        client, client_peer = socket.socketpair()
        upstream, upstream_peer = socket.socketpair()
        for item in (client, client_peer, upstream, upstream_peer):
            self.addCleanup(item.close)
        fake = SimpleNamespace(gate=self.public.gate)
        thread = threading.Thread(
            target=runtime._PublicHandler._pump, args=(fake, client, upstream, session_expires),
            daemon=True)
        thread.start()
        client_peer.sendall(b"one")
        upstream_peer.settimeout(5)
        self.assertEqual(upstream_peer.recv(16), b"one")
        return thread, client_peer, upstream_peer

    def _assert_ends_under_traffic(self, thread, client_peer, upstream_peer):
        self.now += 20
        client_peer.sendall(b"two")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        upstream_peer.setblocking(False)
        with self.assertRaises(BlockingIOError):
            upstream_peer.recv(16)

    def test_tunnel_ends_at_the_service_deadline_under_traffic(self):
        self.public.gate.config["enabled_until_epoch"] = int(self.now) + 10
        self._assert_ends_under_traffic(*self._pump())

    def test_authenticated_tunnel_ends_when_its_session_does(self):
        self._assert_ends_under_traffic(*self._pump(session_expires=int(self.now) + 10))


class MagicLinkTests(_Base):
    token = "m" * 43

    def issue_app(self):
        return self.issue("app", self.token, email="person@example.test")

    def go(self, **fields):
        return self.consume(APP_PATH, self.token, **fields)

    def test_success_redirects_to_the_validated_link(self):
        self.issue_app()
        response, _ = self.go()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), VERIFY)
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")

    def test_email_comes_only_from_the_record(self):
        self.issue_app()
        self.go(email="attacker@example.test")
        self.assertEqual(self.app_store["requests"][-1]["body"], {"email": "person@example.test"})

    def test_request_uses_only_the_declared_origin(self):
        self.issue_app()
        self.go()
        request = self.app_store["requests"][-1]
        self.assertEqual(request["host"], "app.example.test")
        self.assertEqual(request["proto"], "https")

    def test_client_cannot_steer_the_origin(self):
        self.issue_app()
        self.request("POST", APP_PATH, body=urllib.parse.urlencode({"token": self.token}), headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-Host": "admin.example.test", "X-Forwarded-Proto": "http"})
        request = self.app_store["requests"][-1]
        self.assertEqual((request["host"], request["proto"]), ("app.example.test", "https"))

    def test_confirm_page_allows_the_redirect_to_the_declared_origin(self):
        # Browsers apply form-action to the redirect that follows the POST.
        self.issue_app()
        policy = self.request("GET", APP_PATH)[0].getheader("Content-Security-Policy")
        self.assertIn("form-action 'self' https://app.example.test", policy)

    def test_no_session_cookie_is_issued_for_a_magic_link(self):
        self.issue_app()
        self.assertIsNone(self.go()[0].getheader("Set-Cookie"))

    def assertFailure(self, status, code):
        self.issue_app()
        response, body = self.go()
        self.assertEqual(response.status, status)
        self.assertIn(code.encode(), body)
        self.assertIsNone(response.getheader("Location"))

    def test_rate_limit_is_its_own_error(self):
        self.app_store["status"] = 429
        self.assertFailure(429, "rate_limited")

    def test_failed_request_is_its_own_error(self):
        self.app_store["status"] = 500
        self.assertFailure(502, "request_failed")

    def test_no_mail_times_out(self):
        self.app_store["mail"] = None
        self.assertFailure(504, "mail_timeout")

    def test_mail_to_another_address_is_ignored(self):
        self.app_store["mail"] = _message("other", to="someone@example.test")
        self.assertFailure(504, "mail_timeout")

    def test_mail_with_extra_recipients_is_ignored(self):
        mail = _message("multi")
        mail["To"].append({"Address": "someone@example.test"})
        self.app_store["mail"] = mail
        self.assertFailure(504, "mail_timeout")

    def test_mail_that_predates_the_request_is_ignored(self):
        self.capture_store["messages"].append(_message("old"))
        self.app_store["mail"] = None
        self.assertFailure(504, "mail_timeout")

    def test_link_on_another_origin_is_a_template_mismatch(self):
        self.app_store["mail"] = _message("x", text="https://evil.example.test/login/verify?token=1")
        self.assertFailure(502, "template_mismatch")

    def test_pattern_match_with_a_lookalike_host_is_a_template_mismatch(self):
        self.config["entries"]["app"]["magic_link"]["link_pattern"] = r"https://[^\s]+"
        self.app_store["mail"] = _message("x", text="https://app.example.test.evil.test/login/verify?a=1")
        self.assertFailure(502, "template_mismatch")

    def test_link_on_another_path_is_a_template_mismatch(self):
        self.app_store["mail"] = _message(
            "x", text="https://app.example.test/login/other?return=https%3A%2F%2Fapp.example.test")
        self.config["entries"]["app"]["magic_link"]["link_pattern"] = r"https://app\.example\.test/\S+"
        self.assertFailure(502, "template_mismatch")

    def test_link_returning_to_another_origin_is_a_template_mismatch(self):
        self.app_store["mail"] = _message(
            "x", text=("https://app.example.test/login/verify?token=1"
                       "&return=https%3A%2F%2Fadmin.example.test"))
        self.assertFailure(502, "template_mismatch")

    def test_ambiguous_mail_is_a_template_mismatch(self):
        self.app_store["mail"] = _message("x", text=VERIFY + " " + VERIFY.replace("one", "two"))
        self.assertFailure(502, "template_mismatch")

    def test_duplicate_required_param_is_a_template_mismatch(self):
        self.app_store["mail"] = _message(
            "x", text=("https://app.example.test/login/verify?token=1"
                       "&return=https%3A%2F%2Fadmin.example.test"
                       "&return=https%3A%2F%2Fapp.example.test"))
        self.assertFailure(502, "template_mismatch")

    def test_timezone_offset_on_a_mail_is_honoured(self):
        # Reads as "now" if the offset is dropped; really six hours old.
        wall_clock = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 1))
        self.app_store["mail"] = _message("x", created=f"{wall_clock}.000+06:00")
        self.assertFailure(504, "mail_timeout")

    def test_mail_timestamp_without_a_zone_is_ignored(self):
        wall_clock = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 1))
        self.app_store["mail"] = _message("x", created=wall_clock)
        self.assertFailure(504, "mail_timeout")

    def test_nanosecond_mail_timestamp_is_accepted(self):
        wall_clock = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 1))
        self.app_store["mail"] = _message("x", created=f"{wall_clock}.123456789Z")
        self.issue_app()
        self.assertEqual(self.go()[0].status, 303)

    def test_html_part_is_decoded(self):
        mail = _message("x", text="")
        mail["HTML"] = f'<a href="{VERIFY.replace("&", "&amp;")}">Sign in</a>'
        self.app_store["mail"] = mail
        self.issue_app()
        self.assertEqual(self.go()[0].getheader("Location"), VERIFY)

    def test_consumed_token_stays_consumed_when_the_mail_step_fails(self):
        self.app_store["status"] = 429
        self.issue_app()
        self.assertEqual(self.go()[0].status, 429)
        self.app_store["status"] = 200
        self.assertEqual(self.go()[0].status, 410)
        self.assertEqual(len(self.app_store["requests"]), 1)

    def test_logs_never_hold_token_email_or_credential(self):
        self.issue_app()
        self.go()
        joined = " ".join(self.logs)
        for secret in (self.token, "person@example.test", CREDENTIAL, "login/verify?token"):
            self.assertNotIn(secret, joined)
        self.assertIn("consumed", joined)

    def test_a_record_without_an_email_is_refused(self):
        self.issue("app", self.token)
        self.assertEqual(self.go()[0].status, 403)
        self.assertEqual(self.app_store["requests"], [])


class CaptureWithoutLoginTests(_Base):
    token = "m" * 43

    def setUp(self):
        super().setUp()
        self.capture_store["no_login"] = True
        self.config["capture"]["username"] = None

    def test_magic_link_works_and_sends_no_authorization(self):
        self.issue("app", self.token, email="person@example.test")
        response, _ = self.consume(APP_PATH, self.token)
        self.assertEqual((response.status, response.getheader("Location")), (303, VERIFY))
        self.assertTrue(self.capture_store["seen_headers"])
        for seen in self.capture_store["seen_headers"]:
            self.assertNotIn("Authorization", seen)

    def _main(self, config):
        servers = (MagicMock(), MagicMock())
        environment = {"SANDBOX_LOGIN_CONFIG": base64.b64encode(json.dumps(config).encode()).decode()}
        with patch.dict(os.environ, environment), \
             patch.object(runtime, "wait_for_capture"), \
             patch.object(runtime, "create_servers", return_value=servers) as created, \
             patch.object(runtime, "_log"):
            os.environ.pop("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", None)
            runtime.main()
        return created

    def test_startup_needs_no_credential_without_a_login(self):
        created = self._main(self.config)
        self.assertEqual(created.call_args.args[1], "")

    def test_startup_still_needs_the_credential_with_a_login(self):
        self.config["capture"]["username"] = "capture"
        with self.assertRaises(SystemExit) as stopped:
            self._main(self.config)
        self.assertEqual(stopped.exception.code, 1)


class StartupTests(_Base):
    def test_reachable_capture_passes(self):
        runtime.wait_for_capture(self.config, attempts=1, delay=0)

    def test_unreachable_capture_refuses_to_start(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        self.config["capture"]["api_url"] = f"http://127.0.0.1:{free_port}/mail"
        with self.assertRaises(runtime.CaptureUnavailable):
            runtime.wait_for_capture(self.config, attempts=2, delay=0)

    def test_session_key_is_process_local(self):
        cookie = self.session()
        self.public.shutdown()
        self.health.shutdown()
        self._start()
        self.assertEqual(self.request("GET", "/mail/", headers={"Cookie": cookie})[0].status, 404)



SITE = "share.example.test"
SITE_PATH = "/__sandbox/share/mockup"


class _Upstream(http.server.BaseHTTPRequestHandler):
    """A static site that records every request reaching it, plus a websocket echo."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _handle(self):
        store = self.server.store
        store["requests"].append({"method": self.command, "path": self.path,
                                  "headers": self.headers.items()})
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            while True:
                data = self.connection.recv(1024)
                if not data:
                    return
                self.connection.sendall(b"echo:" + data)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = f"site:{self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = do_PATCH = _handle


class _SiteBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)
        self.upstream_store = {"requests": []}
        self.upstream = _Fake(("127.0.0.1", 0), _Upstream)
        self.upstream.store = self.upstream_store
        _serve(self.upstream)
        self.addCleanup(self.upstream.shutdown)
        self.logs = []
        self.now = time.time()
        self.config = {
            "enabled_until_epoch": int(self.now) + 86400,
            "session_ttl_seconds": 3600,
            "capture": None,
            "entries": {"mockup": {
                "mode": "site_gate", "requires_email": False, "hostname": SITE, "path": SITE_PATH,
                "ttl_seconds": 900, "upstream": f"http://127.0.0.1:{self.upstream.server_address[1]}",
                "landing_path": "/"}},
        }
        self.public, self.health = runtime.create_servers(
            self.config, "", self.state_dir, clock=lambda: self.now, log=self.logs.append,
            public_address=("127.0.0.1", 0), health_address=("127.0.0.1", 0))
        _serve(self.public)
        self.addCleanup(self.public.shutdown)
        self.addCleanup(self.health.server_close)

    def manage(self, *argv, stdin=""):
        out = io.StringIO()
        code = runtime.manage(self.config, list(argv), self.state_dir, stdin=io.StringIO(stdin),
                              stdout=out, clock=lambda: self.now)
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def mint(self, token=None, *, uses=1, ttl=600, link_id=None):
        token = token or secrets_token()
        issued = int(self.now)
        record = login.render_site_link_record(
            "mockup", link_id or f"l-{os.urandom(6).hex()}", hashlib.sha256(token.encode()).hexdigest(),
            issued, issued + ttl, uses)
        reply = self.manage("mint", "mockup", stdin=record)
        self.assertTrue(reply["ok"], reply)
        return token, reply["id"]

    def request(self, method, path, *, body=None, headers=None, host=SITE):
        connection = http.client.HTTPConnection("127.0.0.1", self.public.server_address[1], timeout=5)
        connection.request(method, path, body=body, headers={"Host": host, **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def open_link(self, token, **headers):
        body = urllib.parse.urlencode({"token": token})
        return self.request("POST", SITE_PATH, body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", **headers})

    def session(self, token=None):
        if token is None:
            token, _ = self.mint()
        response, _ = self.open_link(token)
        self.assertEqual(response.status, 303)
        return response.getheader("Set-Cookie").split(";", 1)[0]

    def raw(self, data):
        sock = socket.create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(data)
        return sock, sock.recv(4096)


def secrets_token():
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


class SiteLinkTests(_SiteBase):
    def test_link_page_needs_a_live_link_and_never_holds_the_token(self):
        self.assertEqual(self.request("GET", SITE_PATH)[0].status, 404)
        token, _ = self.mint()
        response, body = self.request("GET", SITE_PATH)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn("script-src 'sha256-", response.getheader("Content-Security-Policy"))
        self.assertIn("form-action 'self'", response.getheader("Content-Security-Policy"))
        self.assertNotIn(token.encode(), body)
        self.assertEqual(self.upstream_store["requests"], [])

    def test_link_starts_a_scoped_session_and_redirects_clean(self):
        token, _ = self.mint()
        response, _ = self.open_link(token)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        cookie = response.getheader("Set-Cookie")
        name, _, rest = cookie.partition("=")
        self.assertEqual(name, "__Host-sandbox_site_mockup")
        attributes = [part.strip() for part in rest.split(";")[1:]]
        self.assertEqual({item.split("=")[0] for item in attributes},
                         {"Path", "Max-Age", "Secure", "HttpOnly", "SameSite"})
        self.assertIn("Path=/", attributes)
        self.assertIn("SameSite=Lax", attributes)
        self.assertEqual(int(next(item for item in attributes if item.startswith("Max-Age=")).split("=")[1]),
                         3600)
        self.assertNotIn(token, cookie)

    def test_session_reaches_only_the_upstream_with_a_fixed_host(self):
        cookie = self.session()
        response, body = self.request("GET", "/index.html", headers={"Cookie": f"theme=dark; {cookie}"})
        self.assertEqual((response.status, body), (200, b"site:/index.html"))
        seen = dict(self.upstream_store["requests"][-1]["headers"])
        self.assertEqual(seen["Host"], SITE)
        self.assertEqual(seen["Cookie"], "theme=dark")
        posted, _ = self.request("POST", "/form", body=b"a=1", headers={
            "Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(posted.status, 200)

    def test_websocket_is_tunnelled_with_a_session(self):
        cookie = self.session()
        sock, head = self.raw((f"GET /ws HTTP/1.1\r\nHost: {SITE}\r\nCookie: {cookie}\r\n"
                               "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        self.assertIn(b" 101 ", head)
        sock.sendall(b"ping")
        self.assertEqual(sock.recv(1024), b"echo:ping")

    def test_multi_use_link_starts_that_many_sessions(self):
        token, _ = self.mint(uses=3)
        statuses = [self.open_link(token)[0].status for _ in range(4)]
        self.assertEqual(statuses, [303, 303, 303, 404])
        self.assertEqual(len(self.manage("list", "mockup")["sessions"]), 3)

    def test_concurrent_openers_of_a_single_use_link_get_one_session(self):
        token, _ = self.mint()
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: self.open_link(token)[0].status, range(8)))
        self.assertEqual(sorted(statuses), [303] + [404] * 7)


class SiteNoAccessTests(_SiteBase):
    def assert_upstream_untouched(self):
        self.assertEqual(self.upstream_store["requests"], [])

    def test_without_a_session_everything_is_404(self):
        for method in ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
            for path in ("/", "/index.html", "/__sandbox/share/other"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(method, path)[0].status, 404)
        for cookie in ("__Host-sandbox_site_mockup=" + "x" * 43, "__Host-sandbox_site_mockup=",
                       "__Host-sandbox_site_mockup=../../links/x"):
            self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)
        self.assert_upstream_untouched()

    def test_websocket_without_a_session_gets_nothing(self):
        self.mint()
        _, head = self.raw((f"GET /ws HTTP/1.1\r\nHost: {SITE}\r\n"
                            "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        self.assertIn(b" 404 ", head)
        self.assert_upstream_untouched()

    def _answer(self, token):
        response, body = self.open_link(token)
        headers = sorted((name, value) for name, value in response.getheaders() if name != "Date")
        return response.status, headers, body

    def test_every_failed_link_gets_the_same_404(self):
        unknown = self._answer(secrets_token())
        self.assertEqual(unknown[0], 404)
        used, _ = self.mint()
        self.session(used)
        expired, _ = self.mint(ttl=60)
        revoked, revoked_id = self.mint()
        self.manage("revoke", "mockup", revoked_id)
        self.now += 61
        for token in ("short", used, expired, revoked, "x" * 200):
            with self.subTest(token=token[:8]):
                self.assertEqual(self._answer(token), unknown)
        self.assert_upstream_untouched()

    def test_foreign_origin_is_refused_without_spending(self):
        token, _ = self.mint()
        self.assertEqual(self.open_link(token, Origin="https://evil.example.test")[0].status, 404)
        self.assertEqual(self.open_link(token, Origin=f"https://{SITE}")[0].status, 303)

    def test_the_null_origin_of_a_no_referrer_page_is_accepted(self):
        token, _ = self.mint()
        self.assertEqual(self.open_link(token, Origin="null")[0].status, 303)

    def test_host_and_target_tricks_never_reach_another_server(self):
        other_store = {"requests": []}
        other = _Fake(("127.0.0.1", 0), _Upstream)
        other.store = other_store
        _serve(other)
        self.addCleanup(other.shutdown)
        cookie = self.session()
        port = other.server_address[1]
        refused = (
            f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: {SITE}\r\nCookie: {cookie}\r\n\r\n",
            f"GET / HTTP/1.1\r\nHost: {SITE}\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\n\r\n",
            f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\n\r\n",
            f"GET / HTTP/1.1\r\nCookie: {cookie}\r\n\r\n",
        )
        for trick in refused:
            with self.subTest(trick=trick.split("\r\n", 2)[:2]):
                _, head = self.raw(trick.encode())
                self.assertIn(b" 404 ", head.split(b"\r\n", 1)[0] + b" ")
        self.assert_upstream_untouched()
        # http.server folds a leading "//" into "/", so this is an ordinary path on
        # the declared upstream, never an authority.
        self.raw(f"GET //127.0.0.1:{port}/x HTTP/1.1\r\nHost: {SITE}\r\nCookie: {cookie}\r\n\r\n".encode())
        self.assertTrue(all(not item["path"].startswith("//") for item in self.upstream_store["requests"]))
        self.assertEqual(other_store["requests"], [])

    def test_after_enabled_until_everything_is_404(self):
        cookie = self.session()
        token, _ = self.mint()
        self.now = self.config["enabled_until_epoch"] + 1
        self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)
        self.assertEqual(self.open_link(token)[0].status, 404)
        self.assertEqual(self.request("GET", SITE_PATH)[0].status, 404)
        self.assert_upstream_untouched()

    def test_session_expires_on_time(self):
        cookie = self.session()
        self.now += 3600
        self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)

    def test_link_attempts_are_rate_limited(self):
        statuses = [self.open_link(secrets_token())[0] for _ in range(31)]
        self.assertEqual({item.status for item in statuses[:30]}, {404})
        self.assertEqual(statuses[30].status, 429)
        self.assertEqual(statuses[30].getheader("Referrer-Policy"), "no-referrer")
        self.now += 61
        self.assertEqual(self.open_link(secrets_token())[0].status, 404)


class SiteOperatorTests(_SiteBase):
    def test_listing_shows_ids_and_counts_only(self):
        first, first_id = self.mint(uses=2)
        second, _ = self.mint()
        cookie = self.session(first)
        listing = self.manage("list", "mockup")
        self.assertEqual(len(listing["links"]), 2)
        by_id = {item["id"]: item for item in listing["links"]}
        self.assertEqual((by_id[first_id]["used"], by_id[first_id]["uses"]), (1, 2))
        self.assertEqual(len(listing["sessions"]), 1)
        self.assertEqual(listing["sessions"][0]["link"], first_id)
        blob = json.dumps(listing)
        secret = cookie.split("=", 1)[1]
        for value in (first, second, secret, hashlib.sha256(first.encode()).hexdigest(),
                      hashlib.sha256(secret.encode()).hexdigest()):
            self.assertNotIn(value, blob)

    def test_revoking_a_session(self):
        cookie = self.session()
        session_id = self.manage("list", "mockup")["sessions"][0]["id"]
        self.assertEqual(self.manage("revoke", "mockup", session_id),
                         {"ok": True, "revoked_links": 0, "revoked_sessions": 1})
        self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)

    def test_revoking_a_link_ends_its_sessions(self):
        token, link_id = self.mint(uses=2)
        other = self.session()
        cookie = self.session(token)
        self.assertEqual(self.manage("revoke", "mockup", link_id),
                         {"ok": True, "revoked_links": 1, "revoked_sessions": 1})
        self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)
        self.assertEqual(self.open_link(token)[0].status, 404)
        self.assertEqual(self.request("GET", "/", headers={"Cookie": other})[0].status, 200)

    def test_revoking_all(self):
        cookie = self.session()
        token, _ = self.mint()
        reply = self.manage("revoke", "mockup", "all")
        self.assertEqual((reply["revoked_links"], reply["revoked_sessions"]), (2, 1))
        self.assertEqual(self.request("GET", "/", headers={"Cookie": cookie})[0].status, 404)
        self.assertEqual(self.open_link(token)[0].status, 404)
        self.assertEqual(self.manage("list", "mockup"), {"ok": True, "links": [], "sessions": []})

    def test_unknown_ids_and_entries_are_refused(self):
        self.assertEqual(self.manage("revoke", "mockup", "l-000000000000")["code"], "site_link_unknown")
        self.assertEqual(self.manage("revoke", "mockup", "../x")["code"], "site_link_unknown")
        self.assertEqual(self.manage("list", "other")["code"], "site_entry_unknown")
        self.assertEqual(self.manage("drop", "mockup")["code"], "site_usage")

    def test_a_revoke_racing_a_use_leaves_no_session(self):
        token, _ = self.mint()
        store = self.public.gate.sites["mockup"]
        record = store.link(hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(store.claim(record))
        (store.links / f"{record['token_sha256']}.json").unlink()
        self.assertIsNone(store.start_session(record, 3600, self.config["enabled_until_epoch"]))
        self.assertEqual(list(store.sessions.glob("*.json")), [])

    def test_mint_refusals(self):
        def record(**fields):
            base = json.loads(login.render_site_link_record(
                "mockup", "l-0123456789ab", "a" * 64, int(self.now), int(self.now) + 600, 1))
            return json.dumps({**base, **fields})
        for fields in ({"uses": 21}, {"uses": 0}, {"expires_at": int(self.now) + 7200},
                       {"entry": "other"}, {"id": "l-x"}, {"extra": 1}, {"token_sha256": "zz"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.manage("mint", "mockup", stdin=record(**fields))["code"],
                                 "site_record_invalid")
        self.assertEqual(self.manage("mint", "mockup", stdin="not json")["code"], "site_record_invalid")
        for _ in range(runtime.SITE_MAX_LINKS):
            self.mint()
        self.assertEqual(self.manage("mint", "mockup", stdin=record())["code"], "site_link_limit")
        self.now = self.config["enabled_until_epoch"]
        self.assertEqual(self.manage("mint", "mockup", stdin=record())["code"], "login_service_expired")

    def test_an_open_tunnel_closes_after_a_revoke(self):
        cookie = self.session()
        with patch.object(runtime, "SITE_RECHECK_SECONDS", 0.2):
            sock, head = self.raw((f"GET /ws HTTP/1.1\r\nHost: {SITE}\r\nCookie: {cookie}\r\n"
                                   "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
            self.assertIn(b" 101 ", head)
            self.manage("revoke", "mockup", "all")
            sock.settimeout(5)
            self.assertEqual(sock.recv(1024), b"")

    def test_records_are_private_files(self):
        self.session()
        store = self.public.gate.sites["mockup"]
        for path in [*store.links.glob("*.json"), *store.sessions.glob("*.json")]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_logs_never_hold_a_token_secret_or_hash(self):
        token, _ = self.mint()
        cookie = self.session(token)
        secret = cookie.split("=", 1)[1]
        joined = " ".join(self.logs)
        for value in (token, secret, hashlib.sha256(token.encode()).hexdigest(),
                      hashlib.sha256(secret.encode()).hexdigest()):
            self.assertNotIn(value, joined)
        self.assertIn("site_link_used entry=mockup", joined)

    def test_main_runs_operator_verbs_inside_the_service(self):
        config = base64.b64encode(json.dumps(self.config).encode()).decode()
        out = io.StringIO()
        with patch.dict(os.environ, {"SANDBOX_LOGIN_CONFIG": config}), \
             patch.object(runtime, "STATE_DIRECTORY", str(self.state_dir)), \
             patch.object(runtime.sys, "argv", ["-c", "source", "site", "list", "mockup"]), \
             patch.object(runtime.sys, "stdout", out):
            with self.assertRaises(SystemExit) as stopped:
                runtime.main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "links": [], "sessions": []})


if __name__ == "__main__":
    unittest.main()
