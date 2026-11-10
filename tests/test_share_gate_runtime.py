"""Spec 081: share links in the gate runtime, over real loopback sockets.

Covers what 075's site tests do not: path scope and traversal refusals, unlimited
links, several shares in one cookie, the link-file recheck, no-index on every gate
answer, robots.txt, and the stack page with its per-view sign-in links.
"""
import hashlib
import hmac
import http.client
import io
import json
import re
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.hosting import share  # noqa: E402
from sandbox.hosting.login_service import runtime  # noqa: E402
from tests.test_login_service_runtime import _Fake, _serve, _Upstream, secrets_token  # noqa: E402

GATE = "s7k2.dev.example.test"
HUB = "hub.dev.example.test"
QA_HOST = "login.dev.example.test"
WP_KEY = "ab" * 32
TAG = "noindex, nofollow, noarchive"


class _Gate(unittest.TestCase):
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
            "enabled_until_epoch": int(self.now) + 40 * 86400,
            "session_ttl_seconds": 43200,
            "capture": None,
            "allowed_host_suffixes": ["dev.example.test"],
            "entries": {
                "share": {"mode": "site_gate", "requires_email": False, "hostname": GATE,
                          "path": "/__sandbox/share/share", "ttl_seconds": 900,
                          "upstream": f"http://127.0.0.1:{self.upstream.server_address[1]}",
                          "landing_path": "/"},
                "qa": {"mode": "magic_link", "requires_email": True, "hostname": QA_HOST,
                       "path": "/__sandbox/login/qa", "ttl_seconds": 600,
                       "magic_link": {"request_url": "http://hub:8080/x", "request_origin": f"https://{HUB}",
                                      "link_origin": f"https://{HUB}", "link_path": "/verify",
                                      "link_pattern": "x", "required_params": {},
                                      "poll_timeout_seconds": 5}},
            },
        }
        self.public, self.health = runtime.create_servers(
            self.config, "", self.state_dir, clock=lambda: self.now, log=self.logs.append,
            public_address=("127.0.0.1", 0), health_address=("127.0.0.1", 0))
        _serve(self.public)
        self.addCleanup(self.public.shutdown)
        self.addCleanup(self.health.server_close)
        self.ids = {}

    def manage(self, *argv, stdin=""):
        out = io.StringIO()
        code = runtime.manage(self.config, list(argv), self.state_dir, stdin=io.StringIO(stdin),
                              stdout=out, clock=lambda: self.now)
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def mint(self, *, scope="/", uses=None, ttl=share.DEFAULT_TTL_SECONDS, kind="site", stack=None):
        request = share.LinkRequest(entry="share", kind=kind, scope=scope, ttl_seconds=ttl, uses=uses)
        minted = share.mint(request, now=self.now, ends_at=self.config["enabled_until_epoch"])
        record = share.link_record(request, minted)
        page = None
        if stack is not None:
            page = {"schema": "sandbox.share-stack.v1", "entry": "share", "link": minted.link_id, **stack}
            if page.get("wordpress"):
                page["wordpress"] = {**page["wordpress"], "kid": minted.link_id[2:]}
        reply = self.manage("mint", "share", stdin=json.dumps({"link": record, "stack": page}))
        self.assertTrue(reply["ok"], reply)
        self.ids[minted.token] = minted.link_id
        return minted.token, minted.link_id

    def request(self, method, path, *, body=None, headers=None, host=GATE):
        connection = http.client.HTTPConnection("127.0.0.1", self.public.server_address[1], timeout=10)
        connection.request(method, path, body=body, headers={"Host": host, **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def open(self, token, *, cookie=None, link_id=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        return self.request("POST", share.LINK_PREFIX + (link_id or self.ids[token]),
                            body=urllib.parse.urlencode({"token": token}), headers=headers)

    def cookie(self, token, cookie=None):
        response, _ = self.open(token, cookie=cookie)
        self.assertEqual(response.status, 303)
        return response.getheader("Set-Cookie").split(";", 1)[0]

    def get(self, path, cookie):
        return self.request("GET", path, headers={"Cookie": cookie})


class LinkModelTests(_Gate):
    def test_url_form_is_random_and_names_no_path(self):
        token, link_id = self.mint(scope="/designs/home/")
        url = share.share_url(GATE, link_id, token)
        self.assertRegex(url, rf"^https://{re.escape(GATE)}/s/l-[0-9a-f]{{32}}#[A-Za-z0-9_-]{{43}}$")
        self.assertNotIn("designs", url)
        self.assertNotEqual(share.new_link_id(), share.new_link_id())

    def test_default_link_lives_seven_days_and_sessions_follow_the_link(self):
        token, _ = self.mint()
        response, _ = self.open(token)
        self.assertEqual(response.getheader("Location"), "/")
        max_age = int(re.search(r"Max-Age=([0-9]+)", response.getheader("Set-Cookie")).group(1))
        self.assertEqual(max_age, 43200)
        self.now += 43201
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        self.assertEqual(self.get("/", cookie)[0].status, 404)
        # The link itself is still live, so the browser can open it again.
        self.assertEqual(self.open(token)[0].status, 303)
        self.now += 7 * 86400
        self.assertEqual(self.open(token)[0].status, 404)

    def test_a_link_opens_in_any_number_of_browsers_without_a_limit(self):
        token, _ = self.mint()
        self.assertEqual({self.open(token)[0].status for _ in range(25)}, {303})

    def test_a_limited_link_stops_at_its_limit(self):
        token, _ = self.mint(uses=2)
        self.assertEqual([self.open(token)[0].status for _ in range(3)], [303, 303, 404])

    def test_opener_page_answers_only_for_a_live_id_and_is_not_indexed(self):
        token, link_id = self.mint()
        response, body = self.request("GET", share.LINK_PREFIX + link_id)
        self.assertEqual(response.status, 200)
        self.assertIn(b'<meta name="robots" content="noindex, nofollow">', body)
        self.assertEqual(response.getheader("X-Robots-Tag"), TAG)
        self.assertNotIn(token.encode(), body)
        self.assertEqual(self.request("GET", share.LINK_PREFIX + share.new_link_id())[0].status, 404)
        self.assertEqual(self.request("GET", share.LINK_PREFIX + "l-short")[0].status, 404)
        self.assertEqual(self.upstream_store["requests"], [])

    def test_a_token_only_opens_its_own_id(self):
        token, _ = self.mint()
        _, other = self.mint()
        self.assertEqual(self.open(token, link_id=other)[0].status, 404)
        self.assertEqual(self.open(token)[0].status, 303)

    def test_one_cookie_keeps_several_shares(self):
        first, _ = self.mint(scope="/a/")
        second, _ = self.mint(scope="/b/")
        cookie = self.cookie(first)
        both = self.cookie(second, cookie)
        self.assertEqual(both.split("=", 1)[1].count("."), 1)
        self.assertEqual(self.get("/a/x", both)[0].status, 200)
        self.assertEqual(self.get("/b/x", both)[0].status, 200)
        self.assertEqual(self.get("/c/x", both)[0].status, 404)

    def test_the_cookie_keeps_at_most_eight_sessions(self):
        cookie = None
        for _ in range(10):
            token, _ = self.mint()
            cookie = self.cookie(token, cookie)
        self.assertEqual(len(cookie.split("=", 1)[1].split(".")), runtime.SHARE_COOKIE_SESSIONS)

    def test_ttl_and_scope_rules(self):
        self.assertEqual(share.parse_ttl(None), 7 * 86400)
        self.assertEqual(share.parse_ttl("30d"), 30 * 86400)
        self.assertEqual(share.parse_ttl("2w"), 14 * 86400)
        self.assertEqual(share.parse_ttl("90m"), 5400)
        for bad in ("31d", "59", "0", "-1d", "1y", True, "1.5d"):
            with self.subTest(ttl=bad), self.assertRaises(share.ShareRefusal):
                share.parse_ttl(bad)
        for good in ("/", "/designs/", "/designs/home.html", "/a%20b/"):
            self.assertEqual(share.validate_scope(good), good)
        for bad in ("designs/", "/a/../b/", "/a/./b", "/a%2fb/", "/a//b", "/a?x=1", "/a#b", "/a\\b",
                    "/a;b", "/%2e%2e/", "/a b/", "/%252e/"):
            with self.subTest(scope=bad), self.assertRaises(share.ShareRefusal):
                share.validate_scope(bad)
        clamped = share.mint(share.LinkRequest("share", "site", "/", 30 * 86400, None), now=1000,
                             ends_at=1000 + 86400)
        self.assertEqual((clamped.expires_at, clamped.clamped), (1000 + 86400, True))

    def test_controller_and_gate_path_rules_agree(self):
        samples = ["/", "/a/", "/a/b.html", "/a/../b", "/a/%2e%2e/b", "/A/%2F", "/a//b", "/a\\b", "/a;x", "/a/#/../b",
                   "/a/%00", "/a/%25", "/./", "/a/.", "/%61/", "x", ""]
        for raw in samples:
            self.assertEqual(share.unsafe_path(raw), runtime.unsafe_path(raw), raw)
        for scope, path in (("/a/", "/a/x"), ("/a/", "/ab"), ("/a.html", "/a.html"), ("/a.html", "/a.htm"),
                            ("/%61/", "/a/x"), ("/", "/anything")):
            self.assertEqual(share.scope_covers(scope, path), runtime.scope_covers(scope, path))


class ScopeTests(_Gate):
    def test_folder_scope_lands_there_and_refuses_everything_else(self):
        token, _ = self.mint(scope="/designs/home/")
        response, _ = self.open(token)
        self.assertEqual(response.getheader("Location"), "/designs/home/")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        self.assertEqual(self.get("/designs/home/", cookie)[0].status, 200)
        self.assertEqual(self.get("/designs/home/img/a.png?v=1", cookie)[0].status, 200)
        before = len(self.upstream_store["requests"])
        refused = ["/", "/designs/", "/designs/homepage", "/designs/other/", "/designs/home/../other/",
                   "/designs/home/%2e%2e/other/", "/designs/home/%2E%2E/other/", "/designs/home/..%2fother",
                   "/designs/home/%2F../other", "/designs/home//../x", "/designs/home/.%2e/x",
                   "/designs/home/a\\..\\..\\x", "/designs/home/;/../x", "/designs/home/%252e%252e/x",
                   "/designs/home/./x", "/designs/home/#/../../other/", "/designs/home/x#y"]
        for path in refused:
            with self.subTest(path=path):
                self.assertEqual(self.get(path, cookie)[0].status, 404)
        self.assertEqual(len(self.upstream_store["requests"]), before)

    def test_page_scope_reaches_exactly_one_path(self):
        token, _ = self.mint(scope="/designs/home.html")
        cookie = self.cookie(token)
        self.assertEqual(self.get("/designs/home.html", cookie)[0].status, 200)
        self.assertEqual(self.get("/designs/home.html?x=1", cookie)[0].status, 200)
        for path in ("/designs/home.htm", "/designs/home.html/x", "/designs/", "/"):
            self.assertEqual(self.get(path, cookie)[0].status, 404, path)

    def test_a_scoped_websocket_outside_scope_is_refused(self):
        token, _ = self.mint(scope="/designs/")
        cookie = self.cookie(token)
        sock = __import__("socket").create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall((f"GET /ws HTTP/1.1\r\nHost: {GATE}\r\nCookie: {cookie}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        self.assertIn(b" 404 ", sock.recv(4096))

    def test_a_declined_upgrade_never_becomes_a_raw_tunnel(self):
        # An upstream that answers the upgrade with 200 and keeps the connection open:
        # a request pipelined behind it must not reach the upstream unchecked.
        token, _ = self.mint(scope="/designs/")
        cookie = self.cookie(token)
        sock = __import__("socket").create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        self.addCleanup(sock.close)
        with patch.object(_Upstream, "do_GET", _declining_handle):
            sock.sendall((f"GET /designs/ws HTTP/1.1\r\nHost: {GATE}\r\nCookie: {cookie}\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
                          f"GET /secret HTTP/1.1\r\nHost: {GATE}\r\n\r\n").encode())
            received = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received += chunk
        self.assertIn(b" 502 ", received.split(b"\r\n", 1)[0])
        self.assertIn(b"upgrade_refused", received)
        self.assertNotIn(b"declined", received)
        self.assertEqual([item["path"] for item in self.upstream_store["requests"]], ["/designs/ws"])

    def test_an_upgrade_with_a_body_or_another_method_is_refused(self):
        token, _ = self.mint()
        cookie = self.cookie(token)
        for head in ("POST /ws HTTP/1.1\r\nContent-Length: 4\r\n", "GET /ws HTTP/1.1\r\nContent-Length: 4\r\n",
                     "GET /ws HTTP/1.1\r\nTransfer-Encoding: chunked\r\n"):
            with self.subTest(head=head.split("\r\n")[1]):
                sock = __import__("socket").create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
                self.addCleanup(sock.close)
                sock.sendall((f"{head}Host: {GATE}\r\nCookie: {cookie}\r\n"
                              "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\nping").encode())
                self.assertRegex(sock.recv(4096).split(b"\r\n", 1)[0], rb" (400|411) ")
        self.assertEqual(self.upstream_store["requests"], [])


def _declining_handle(self):
    self.server.store["requests"].append({"method": self.command, "path": self.path,
                                          "headers": self.headers.items()})
    body = b"declined"
    self.send_response(200)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class RevokeTests(_Gate):
    def test_revoking_one_link_ends_its_sessions_at_the_next_request(self):
        token, link_id = self.mint()
        keep, _ = self.mint()
        cookie = self.cookie(token)
        other = self.cookie(keep)
        self.assertEqual(self.manage("revoke", "share", link_id)["revoked_links"], 1)
        self.assertEqual(self.get("/", cookie)[0].status, 404)
        self.assertEqual(self.get("/", other)[0].status, 200)

    def test_a_session_whose_link_vanished_is_refused_even_if_its_file_survives(self):
        token, link_id = self.mint()
        cookie = self.cookie(token)
        store = self.public.gate.sites["share"]
        (store.links / f"{hashlib.sha256(token.encode()).hexdigest()}.json").unlink()
        self.assertTrue(list(store.sessions.glob("*.json")))
        self.assertEqual(self.get("/", cookie)[0].status, 404)

    def test_revoke_all(self):
        cookies = [self.cookie(self.mint()[0]) for _ in range(3)]
        reply = self.manage("revoke", "share", "all")
        self.assertEqual((reply["revoked_links"], reply["revoked_sessions"]), (3, 3))
        for cookie in cookies:
            self.assertEqual(self.get("/", cookie)[0].status, 404)

    def test_listing_shows_scope_and_kind_but_no_secret(self):
        token, link_id = self.mint(scope="/x/", uses=3)
        cookie = self.cookie(token)
        listing = self.manage("list", "share")
        self.assertEqual(listing["links"][0]["scope"], "/x/")
        self.assertEqual((listing["links"][0]["kind"], listing["links"][0]["used"]), ("site", 1))
        blob = json.dumps(listing)
        for value in (token, cookie.split("=", 1)[1], hashlib.sha256(token.encode()).hexdigest()):
            self.assertNotIn(value, blob)


class NoIndexTests(_Gate):
    def test_every_gate_answer_carries_the_header(self):
        token, link_id = self.mint()
        cookie = self.cookie(token)
        answers = [self.request("GET", "/"), self.request("GET", "/robots.txt"),
                   self.request("GET", share.LINK_PREFIX + link_id), self.open(token),
                   self.get("/page", cookie), self.request("GET", "/", host="unknown.example.test"),
                   self.request("GET", share.LINK_PREFIX + share.new_link_id())]
        for response, _ in answers:
            with self.subTest(status=response.status):
                self.assertEqual(response.getheader("X-Robots-Tag"), TAG)

    def test_errors_http_server_raises_itself_carry_the_header(self):
        for method in ("TRACE", "PROPFIND"):
            with self.subTest(method=method):
                response, _ = self.request(method, "/")
                self.assertEqual(response.status, 501)
                self.assertEqual(response.getheader("X-Robots-Tag"), TAG)
                self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")

    def test_an_accepted_upgrade_carries_the_header(self):
        token, _ = self.mint()
        cookie = self.cookie(token)
        sock = __import__("socket").create_connection(("127.0.0.1", self.public.server_address[1]), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall((f"GET /ws HTTP/1.1\r\nHost: {GATE}\r\nCookie: {cookie}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            self.assertTrue(chunk, head)
            head += chunk
        self.assertIn(b" 101 ", head.split(b"\r\n", 1)[0])
        self.assertIn(f"X-Robots-Tag: {TAG}".encode(), head)
        self.assertIn(b"Upgrade: websocket", head)
        sock.sendall(b"ping")
        self.assertEqual(sock.recv(1024), b"echo:ping")

    def test_the_upstreams_own_robots_header_is_replaced(self):
        token, _ = self.mint()
        cookie = self.cookie(token)
        with patch.object(_Upstream, "do_GET", _robots_allowing_handle):
            response, _ = self.get("/", cookie)
        self.assertEqual(response.msg.get_all("X-Robots-Tag"), [TAG])

    def test_robots_txt_denies_without_a_session(self):
        response, body = self.request("GET", "/robots.txt")
        self.assertEqual((response.status, body), (200, b"User-agent: *\nDisallow: /\n"))
        self.assertEqual(self.upstream_store["requests"], [])


def _robots_allowing_handle(self):
    body = b"ok"
    self.send_response(200)
    self.send_header("X-Robots-Tag", "all")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class StackPageTests(_Gate):
    STACK = {"title": "hub-1", "hosts": [{"hostname": HUB, "role": "hub", "probe": "internal"},
                                         {"hostname": "site.dev.example.test", "role": "site", "probe": "public"},
                                         {"hostname": "elsewhere.example.org", "role": "x", "probe": "public"}],
             "wordpress": {"kid": "", "key": WP_KEY, "url": "https://wp.dev.example.test/"},
             "sign_in": [{"entry": "qa", "email": "qa@dev.example.test"}]}

    def stack_cookie(self):
        token, link_id = self.mint(kind="stack", scope=share.STACK_PAGE_PATH, stack=dict(self.STACK))
        response, _ = self.open(token)
        self.assertEqual(response.getheader("Location"), share.STACK_PAGE_PATH)
        return response.getheader("Set-Cookie").split(";", 1)[0], link_id

    @staticmethod
    def _fake_probe(host, probe, _upstream, suffixes):
        if probe == "internal":
            return "up"
        return "unreachable" if any(host.endswith(suffix) for suffix in suffixes) else "not_checked"

    def view(self, cookie):
        with patch.object(runtime, "_probe", side_effect=self._fake_probe):
            return self.get(share.STACK_PAGE_PATH, cookie)

    def test_page_lists_hosts_with_health_and_fresh_links(self):
        cookie, link_id = self.stack_cookie()
        response, body = self.view(cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("X-Robots-Tag"), TAG)
        self.assertIn("style-src 'sha256-", response.getheader("Content-Security-Policy"))
        text = body.decode()
        self.assertIn('<meta name="robots" content="noindex, nofollow">', text)
        self.assertIn(HUB, text)
        self.assertIn('class="up"', text)
        self.assertIn("not checked", text)
        wp = re.search(r'href="(https://wp\.dev\.example\.test/\?sandbox_share_login=([0-9a-f]{32})#([^"]+))"', text)
        self.assertIsNotNone(wp)
        self.assertEqual(wp.group(2), link_id[2:])
        exp, nonce, mac = wp.group(3).split(".")
        self.assertLessEqual(int(exp) - int(self.now), runtime.SIGN_IN_SECONDS)
        message = f"sandbox-share-login.v1|{link_id[2:]}|{exp}|{nonce}".encode()
        self.assertEqual(mac, hmac.new(bytes.fromhex(WP_KEY), message, hashlib.sha256).hexdigest())
        qa = re.search(rf'href="https://{re.escape(QA_HOST)}/__sandbox/login/qa#sandbox_autologin=([A-Za-z0-9_-]+)"', text)
        self.assertIsNotNone(qa)
        record = json.loads((self.state_dir / "qa.json").read_text())
        self.assertEqual(record["token_sha256"], hashlib.sha256(qa.group(1).encode()).hexdigest())
        self.assertEqual((record["email"], record["expires_at"] - record["issued_at"]),
                         ("qa@dev.example.test", runtime.SIGN_IN_SECONDS))
        self.assertTrue(self.public.gate.is_live("qa"))
        _, again = self.view(cookie)
        self.assertNotEqual(re.search(r"#([0-9]+\.[^\"]+)\"", again.decode()).group(1), wp.group(3))
        joined = " ".join(self.logs)
        for value in (wp.group(3), qa.group(1), WP_KEY):
            self.assertNotIn(value, joined)

    def test_a_stack_session_reaches_nothing_but_the_page(self):
        cookie, _ = self.stack_cookie()
        for path in ("/", "/index.html", "/__sandbox/stackx", share.STACK_PAGE_PATH + "/"):
            self.assertEqual(self.get(path, cookie)[0].status, 404, path)
        self.assertEqual(self.upstream_store["requests"], [])

    def test_a_site_session_cannot_open_the_stack_page(self):
        token, _ = self.mint()
        cookie = self.cookie(token)
        self.assertEqual(self.get(share.STACK_PAGE_PATH, cookie)[0].status, 404)

    def test_revoke_drops_the_stack_page_and_its_key(self):
        cookie, link_id = self.stack_cookie()
        store = self.public.gate.sites["share"]
        self.assertTrue((store.stacks / f"{link_id}.json").exists())
        self.manage("revoke", "share", link_id)
        self.assertFalse((store.stacks / f"{link_id}.json").exists())
        self.assertEqual(self.view(cookie)[0].status, 404)

    def test_revoking_the_stack_link_ends_the_sign_in_links_it_minted(self):
        cookie, link_id = self.stack_cookie()
        self.assertEqual(self.view(cookie)[0].status, 200)
        gate = self.public.gate
        self.assertTrue(gate.is_live("qa"))
        self.manage("revoke", "share", link_id)
        # The token record is still on disk, but it no longer counts.
        self.assertTrue((self.state_dir / "qa.json").exists())
        self.assertFalse(gate.is_live("qa"))
        self.assertIsNone(gate.record("qa"))

    def test_a_sign_in_posted_slowly_across_a_revoke_is_refused(self):
        # Headers first, so the gate loads the token record; then the revoke; then the body.
        cookie, link_id = self.stack_cookie()
        _, body = self.view(cookie)
        token = re.search(rf'href="https://{re.escape(QA_HOST)}/__sandbox/login/qa#sandbox_autologin=([A-Za-z0-9_-]+)"',
                          body.decode()).group(1)
        form = urllib.parse.urlencode({"token": token}).encode()
        sock = __import__("socket").create_connection(("127.0.0.1", self.public.server_address[1]), timeout=10)
        self.addCleanup(sock.close)
        sock.sendall((f"POST /__sandbox/login/qa HTTP/1.1\r\nHost: {QA_HOST}\r\n"
                      "Content-Type: application/x-www-form-urlencoded\r\n"
                      f"Content-Length: {len(form)}\r\n\r\n").encode())
        time.sleep(0.5)
        self.manage("revoke", "share", link_id)
        sock.sendall(form)
        self.assertIn(b" 404 ", sock.recv(4096).split(b"\r\n", 1)[0])
        self.assertFalse((self.state_dir / "used" / hashlib.sha256(token.encode()).hexdigest()).exists())

    def test_a_spent_one_use_stack_link_keeps_its_sign_in_links(self):
        token, link_id = self.mint(kind="stack", scope=share.STACK_PAGE_PATH, stack=dict(self.STACK), uses=1)
        response, _ = self.open(token)
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        self.assertEqual(self.view(cookie)[0].status, 200)
        self.assertTrue(self.public.gate.is_live("qa"))

    def test_a_sign_in_record_naming_an_unknown_stack_link_is_dead(self):
        self.public.gate.mint_sign_in("qa", "qa@dev.example.test", int(self.now) + 300,
                                      ("share", share.new_link_id()))
        self.assertFalse(self.public.gate.is_live("qa"))
        self.public.gate.mint_sign_in("qa", "qa@dev.example.test", int(self.now) + 300, ("nope", "l-x"))
        self.assertFalse(self.public.gate.is_live("qa"))

    def test_stack_records_are_checked(self):
        def mint(**changes):
            request = share.LinkRequest("share", "stack", share.STACK_PAGE_PATH, 3600, None)
            minted = share.mint(request, now=self.now, ends_at=self.config["enabled_until_epoch"])
            page = {"schema": "sandbox.share-stack.v1", "entry": "share", "link": minted.link_id,
                    **self.STACK, "wordpress": {**self.STACK["wordpress"], "kid": minted.link_id[2:]}, **changes}
            return self.manage("mint", "share", stdin=json.dumps({"link": share.link_record(request, minted),
                                                                  "stack": page}))
        self.assertTrue(mint()["ok"])
        for changes in ({"title": "Hub One"}, {"hosts": [{"hostname": "x", "role": "a", "probe": "internal"}]},
                        {"wordpress": {"kid": "0" * 32, "key": WP_KEY, "url": "https://wp.dev.example.test/"}},
                        {"wordpress": {"kid": "", "key": "short", "url": "x"}},
                        {"sign_in": [{"entry": "share"}]}, {"sign_in": [{"entry": "qa"}]},
                        {"extra": True}):
            with self.subTest(changes=list(changes)):
                self.assertEqual(mint(**changes)["code"], "site_record_invalid")
        # A site link may not carry a page, and a stack link must.
        request = share.LinkRequest("share", "site", "/", 3600, None)
        minted = share.mint(request, now=self.now, ends_at=self.config["enabled_until_epoch"])
        self.assertEqual(self.manage("mint", "share", stdin=json.dumps(
            {"link": share.link_record(request, minted), "stack": {}}))["code"], "site_record_invalid")


class HealthTests(_Gate):
    def test_internal_probe_goes_through_the_upstream_with_the_host(self):
        result = runtime._probe(HUB, "internal", ("127.0.0.1", self.upstream.server_address[1]), [])
        self.assertEqual(result, "up")
        self.assertEqual(dict(self.upstream_store["requests"][-1]["headers"])["Host"], HUB)

    def test_public_probe_skips_hosts_off_the_allowed_suffixes(self):
        self.assertEqual(runtime._probe("elsewhere.example.org", "public", None, ["dev.example.test"]),
                         "not_checked")

    def test_classification(self):
        for status, label in ((200, "up"), (302, "redirect"), (401, "up_login"), (403, "up_login"),
                              (404, "client_error"), (502, "server_error"), (None, "unreachable")):
            self.assertEqual(runtime.classify(status), label)


if __name__ == "__main__":
    unittest.main()
