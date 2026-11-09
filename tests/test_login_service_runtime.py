"""The login service over real loopback sockets, with fake upstreams."""
import base64
import hashlib
import http.client
import http.server
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
        if self.headers.get("Authorization") != expected:
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
        for attribute in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=3600"):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain", cookie)

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


if __name__ == "__main__":
    unittest.main()
