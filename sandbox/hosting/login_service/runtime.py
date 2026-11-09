"""One-time login links for a hosted Compose project.

Standard library only: Sandbox injects this file into the service container
through the generated Compose override. Nothing here names an application.
The contract is docs/login-service.md.

Security properties worth keeping in mind when changing this file:

* A link is confirmed by POST, never by GET, so previews cannot use it up.
* The token travels in the URL fragment and the POST body, never a request line.
* Logs are fixed codes. A token, email address, credential, or URL never reaches one.
* With no live token and no session, every public route answers 404.
"""
import base64
import calendar
import hashlib
import hmac
import html
import http.client
import http.server
import json
import os
import re
import secrets
import select
import socket
import sys
import time
import urllib.parse
from pathlib import Path

PUBLIC_ADDRESS = ("0.0.0.0", 8080)
HEALTH_ADDRESS = ("127.0.0.1", 8081)
STATE_DIRECTORY = "/run/sandbox-login"
RECORD_SCHEMA = "sandbox.login-token.v1"
MAX_CONFIRM_BODY = 64 * 1024
MAX_PROXY_BODY = 16 * 1024 * 1024
MIN_LINK_SECONDS = 60
MAX_LINK_SECONDS = 3600
TUNNEL_IDLE_SECONDS = 3600
POLL_INTERVAL_SECONDS = 0.25
MAIL_CLOCK_TOLERANCE_SECONDS = 2
UPSTREAM_TIMEOUT_SECONDS = 10

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_HASH = re.compile(r"[0-9a-f]{64}")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SESSION_EXPIRY = re.compile(r"[0-9]{1,12}")
_AUTHORITY = re.compile(r"([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?")
_MAIL_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})")
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
}
_CONFIRM_SCRIPT = (
    "(function(){var m=location.hash.match(/^#sandbox_autologin=([A-Za-z0-9_-]+)$/);"
    "var f=document.forms[0];history.replaceState(null,'',location.pathname);"
    "if(m){f.elements.token.value=m[1];}else{f.hidden=true;"
    "document.getElementById('incomplete').hidden=false;}})();"
)
_CONFIRM_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>Confirm sign-in</title></head><body><main><h1>Confirm sign-in</h1>"
    "<p>Continue uses this one-time link. It cannot be used again.</p>"
    '<form method="post"><input type="hidden" name="token">'
    "<button>Continue</button></form>"
    '<p id="incomplete" hidden>This link is incomplete. Open the full link you were sent.</p>'
    f"<script>{_CONFIRM_SCRIPT}</script></main></body></html>"
).encode()
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_CONFIRM_SCRIPT.encode()).digest()).decode()


class CaptureUnavailable(RuntimeError):
    """The declared capture service cannot be reached."""


class SignInFailure(Exception):
    """A named, fail-closed outcome of the magic-link exchange."""

    def __init__(self, code, status):
        super().__init__(code)
        self.code = code
        self.status = status


class Gate:
    """Token, session, and sign-in state shared by the request handlers."""

    def __init__(self, config, credential, state_dir, *, clock, log):
        self.config = config
        self.credential = credential
        self.state_dir = Path(state_dir)
        self.clock = clock
        self.log = log
        # Process-local on purpose: restarting the service ends every session.
        self.session_key = secrets.token_bytes(32)
        (self.state_dir / "used").mkdir(mode=0o700, parents=True, exist_ok=True)

    def enabled(self):
        return self.clock() < self.config["enabled_until_epoch"]

    def record(self, name):
        """Return a well-formed record for this entry, or None."""
        try:
            raw = json.loads((self.state_dir / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("schema") != RECORD_SCHEMA or raw.get("entry") != name:
            return None
        digest, issued, expires = raw.get("token_sha256"), raw.get("issued_at"), raw.get("expires_at")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            return None
        if type(issued) is not int or type(expires) is not int:
            return None
        if not MIN_LINK_SECONDS <= expires - issued <= MAX_LINK_SECONDS:
            return None
        return raw

    def _used_marker(self, digest):
        return self.state_dir / "used" / digest

    def is_live(self, name):
        record = self.record(name)
        return (record is not None and self.clock() < record["expires_at"]
                and not self._used_marker(record["token_sha256"]).exists())

    def claim(self, digest):
        """Atomically consume a token; exactly one caller ever gets True."""
        try:
            os.close(os.open(self._used_marker(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            return False
        return True

    @staticmethod
    def cookie_name(name):
        return f"__Host-sandbox_login_{name}"

    def _signature(self, name, expires):
        return hmac.new(self.session_key, f"{name}:{expires}".encode(), hashlib.sha256).hexdigest()

    def new_session(self, name):
        expires = int(self.clock()) + self.config["session_ttl_seconds"]
        return f"{expires}.{self._signature(name, expires)}"

    def session_valid(self, name, value):
        expires, _, signature = (value or "").partition(".")
        if not _SESSION_EXPIRY.fullmatch(expires):
            return False
        # Bytes, because compare_digest raises on a non-ASCII str from a client.
        if not hmac.compare_digest(self._signature(name, int(expires)).encode(),
                                   signature.encode("utf-8", "replace")):
            return False
        return self.clock() < int(expires)

    def _capture_json(self, path):
        capture = self.config["capture"]
        target = urllib.parse.urlsplit(capture["api_url"])
        connection = http.client.HTTPConnection(
            target.hostname, target.port, timeout=UPSTREAM_TIMEOUT_SECONDS)
        try:
            connection.request("GET", f"{target.path.rstrip('/')}{path}",
                               headers=capture_headers(capture, self.credential))
            response = connection.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException):
            raise SignInFailure("capture_unavailable", 502) from None
        finally:
            connection.close()
        if response.status != 200:
            raise SignInFailure("capture_unavailable", 502)
        try:
            return json.loads(body)
        except ValueError:
            raise SignInFailure("capture_unavailable", 502) from None

    def _message_ids(self):
        listing = self._capture_json("/api/v1/messages?limit=200")
        messages = listing.get("messages") if isinstance(listing, dict) else None
        if not isinstance(messages, list):
            raise SignInFailure("capture_unavailable", 502)
        return [item["ID"] for item in messages if isinstance(item, dict) and isinstance(item.get("ID"), str)]

    def _request_mail(self, settings, email):
        target = urllib.parse.urlsplit(settings["request_url"])
        origin = urllib.parse.urlsplit(settings["request_origin"])
        connection = http.client.HTTPConnection(
            target.hostname, target.port, timeout=UPSTREAM_TIMEOUT_SECONDS)
        try:
            # The origin is fixed by the declaration; nothing about the browser
            # request can choose where the resulting session lands.
            connection.request("POST", target.path, body=json.dumps({"email": email}), headers={
                "Content-Type": "application/json", "Host": origin.netloc,
                "X-Forwarded-Proto": origin.scheme})
            response = connection.getresponse()
            response.read()
        except (OSError, http.client.HTTPException):
            raise SignInFailure("request_failed", 502) from None
        finally:
            connection.close()
        if response.status == 429:
            raise SignInFailure("rate_limited", 429)
        if not 200 <= response.status < 300:
            raise SignInFailure("request_failed", 502)

    @staticmethod
    def _addressed_only_to(message, email):
        recipients = message.get("To")
        return (isinstance(recipients, list) and len(recipients) == 1
                and isinstance(recipients[0], dict)
                and str(recipients[0].get("Address", "")).casefold() == email.casefold())

    @staticmethod
    def _arrived_since(message, started):
        match = _MAIL_TIME.fullmatch(str(message.get("Created", "")))
        if match is None:
            return False
        stamp, zone = match.groups()
        try:
            epoch = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return False
        if zone != "Z":
            offset = int(zone[1:3]) * 3600 + int(zone[4:6]) * 60
            epoch -= offset if zone[0] == "+" else -offset
        return epoch >= int(started) - MAIL_CLOCK_TOLERANCE_SECONDS

    @staticmethod
    def _accepted_link(message, settings):
        """Return the one declared link in a message, or fail closed."""
        body = f"{message.get('Text') or ''}\n{html.unescape(str(message.get('HTML') or ''))}"
        found = set(re.findall(settings["link_pattern"], body))
        if len(found) != 1:
            raise SignInFailure("template_mismatch", 502)
        link = found.pop()
        parsed = urllib.parse.urlsplit(link)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if (f"{parsed.scheme}://{parsed.netloc}" != settings["link_origin"]
                or parsed.path != settings["link_path"]
                # A repeated name is refused: the receiver may read a different copy.
                or any([item for name, item in params if name == key] != [value]
                       for key, value in settings.get("required_params", {}).items())):
            raise SignInFailure("template_mismatch", 502)
        return link

    def sign_in(self, settings, email):
        """Ask the application to mail a sign-in link and return that link."""
        existing = set(self._message_ids())
        started = time.time()
        self._request_mail(settings, email)
        deadline = time.monotonic() + settings.get("poll_timeout_seconds", 10)
        while True:
            for identifier in self._message_ids():
                if identifier in existing:
                    continue
                existing.add(identifier)
                message = self._capture_json(f"/api/v1/message/{urllib.parse.quote(identifier, safe='')}")
                if (isinstance(message, dict) and self._addressed_only_to(message, email)
                        and self._arrived_since(message, started)):
                    return self._accepted_link(message, settings)
            if time.monotonic() >= deadline:
                raise SignInFailure("mail_timeout", 504)
            time.sleep(POLL_INTERVAL_SECONDS)


def capture_headers(capture, credential):
    secret = base64.b64encode(f"{capture['username']}:{credential}".encode()).decode()
    return {"Authorization": f"Basic {secret}"}


class _PublicHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "sandbox-login"
    sys_version = ""

    def log_message(self, *_args):
        """Request lines are never logged."""

    @property
    def gate(self):
        return self.server.gate

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self):
        self._drain()
        self._reply(404, b"Not found\n", [("Content-Type", "text/plain; charset=utf-8")])

    def _fail(self, status, code):
        self._reply(status, f"{code}\n".encode(), [("Content-Type", "text/plain; charset=utf-8")])

    def _drain(self):
        """Discard a small unread body so the connection stays well framed."""
        length = self._content_length()
        if length is None or length > MAX_CONFIRM_BODY:
            self.close_connection = True
        elif length:
            self.rfile.read(length)

    def _content_length(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        return int(raw) if raw.isdigit() else None

    def _host(self):
        match = _AUTHORITY.fullmatch(self.headers.get("Host", "").strip().lower())
        return match.group(1) if match else ""

    def _dispatch(self):
        gate = self.gate
        # Only origin-form targets: self.path is forwarded to the capture service.
        if not gate.enabled() or not self.path.startswith("/") or self.path.startswith("//"):
            return self._not_found()
        host = self._host()
        path = urllib.parse.urlsplit(self.path).path
        entries = gate.config["entries"]
        for name, entry in entries.items():
            if entry["hostname"] == host and entry["path"] == path:
                return self._handle_entry(name, entry)
        for name, entry in entries.items():
            if entry["hostname"] == host and entry["mode"] == "inbox_gate":
                if gate.session_valid(name, self._cookie(gate.cookie_name(name))):
                    return self._proxy(authenticated=True)
                if gate.is_live(name):
                    # Anyone without a link still meets the service's own login,
                    # but only while a link is outstanding (no standing access).
                    return self._proxy(authenticated=False)
        return self._not_found()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _dispatch

    def _cookie(self, wanted):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == wanted:
                return value
        return None

    def _handle_entry(self, name, entry):
        gate = self.gate
        if self.command in ("GET", "HEAD"):
            if not gate.is_live(name):
                return self._not_found()
            return self._reply(200, _CONFIRM_PAGE, self._page_headers(entry))
        if self.command != "POST":
            return self._fail(405, "method_not_allowed") if gate.is_live(name) else self._not_found()
        record = gate.record(name)
        if record is None:
            return self._not_found()
        return self._consume(name, entry, record)

    @staticmethod
    def _page_headers(entry):
        targets = "'self'"
        if entry["mode"] == "magic_link":
            # Browsers apply form-action to the redirect that follows the POST.
            targets += f" {entry['magic_link']['link_origin']}"
        policy = ("default-src 'none'; "
                  f"script-src 'sha256-{_SCRIPT_HASH}'; "
                  f"form-action {targets}; base-uri 'none'; frame-ancestors 'none'")
        return [("Content-Type", "text/html; charset=utf-8"), ("Content-Security-Policy", policy),
                ("X-Content-Type-Options", "nosniff")]

    def _read_token(self):
        """Return the submitted token, an empty string if absent, or None if oversized."""
        length = self._content_length()
        if length is None or length > MAX_CONFIRM_BODY:
            self.close_connection = True
            return None
        fields = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        return (fields.get("token") or [""])[0]

    def _consume(self, name, entry, record):
        gate = self.gate
        token = self._read_token()
        if token is None:
            return self._fail(413, "too_large")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"https://{entry['hostname']}":
            return self._fail(403, "invalid_link")
        if not _TOKEN.fullmatch(token):
            return self._fail(403, "invalid_link")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(digest, record["token_sha256"]):
            return self._fail(403, "invalid_link")
        if gate.clock() >= record["expires_at"]:
            return self._fail(410, "link_expired")
        email = record.get("email")
        if entry["mode"] == "magic_link" and not (isinstance(email, str) and _ADDRESS.fullmatch(email)):
            return self._fail(403, "invalid_link")
        if not gate.claim(digest):
            return self._fail(410, "link_used")
        gate.log(f"consumed entry={name}")
        if entry["mode"] == "inbox_gate":
            return self._start_session(name, entry)
        return self._exchange_magic_link(name, entry, email)

    def _start_session(self, name, entry):
        # No Max-Age or Expires: the cookie ends with the browser session. The
        # server-side limit is carried, signed, inside the value.
        value = self.gate.new_session(name)
        cookie = (f"{self.gate.cookie_name(name)}={value}; Path=/; "
                  "Secure; HttpOnly; SameSite=Strict")
        self._reply(303, b"", [("Location", entry.get("landing_path") or "/"), ("Set-Cookie", cookie)])

    def _exchange_magic_link(self, name, entry, email):
        try:
            link = self.gate.sign_in(entry["magic_link"], email)
        except SignInFailure as failure:
            self.gate.log(f"signin_failed entry={name} code={failure.code}")
            return self._fail(failure.status, failure.code)
        self._reply(303, b"", [("Location", link)])

    def _upstream_headers(self, authenticated):
        gate = self.gate
        headers = []
        for name, value in self.headers.items():
            lowered = name.lower()
            if lowered in _HOP_BY_HOP or "\r" in value or "\n" in value:
                continue
            if lowered == "cookie":
                kept = [part.strip() for part in value.split(";")
                        if not part.strip().startswith("__Host-sandbox_login_")]
                if kept:
                    headers.append((name, "; ".join(kept)))
                continue
            if lowered == "authorization" and authenticated:
                continue
            headers.append((name, value))
        if authenticated:
            headers.extend(capture_headers(gate.config["capture"], gate.credential).items())
        return headers

    def _proxy(self, authenticated):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self._tunnel(authenticated)
        length = self._content_length()
        if length is None or "Transfer-Encoding" in self.headers:
            self.close_connection = True
            return self._fail(411, "length_required")
        if length > MAX_PROXY_BODY:
            self.close_connection = True
            return self._fail(413, "too_large")
        target = urllib.parse.urlsplit(self.gate.config["capture"]["api_url"])
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=30)
        try:
            body = self.rfile.read(length) if length else None
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for name, value in self._upstream_headers(authenticated):
                connection.putheader(name, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            self._relay(response)
        except (OSError, http.client.HTTPException):
            self.close_connection = True
            return self._fail(502, "upstream_unavailable")
        finally:
            connection.close()
        return None

    def _relay(self, response):
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in _HOP_BY_HOP and name.lower() != "referrer-policy":
                self.send_header(name, value)
        self.send_header("Referrer-Policy", "no-referrer")
        length = response.getheader("Content-Length")
        if length is not None:
            self.send_header("Content-Length", length)
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command == "HEAD":
            return
        while True:
            chunk = response.read(65536)
            if not chunk:
                return
            self.wfile.write(chunk)

    def _buffered_client_bytes(self):
        """Take bytes already read past the headers, without blocking for more."""
        self.connection.setblocking(False)
        try:
            pending = self.rfile.peek(65536)
        except OSError:
            pending = b""
        finally:
            self.connection.setblocking(True)
        return self.rfile.read(len(pending)) if pending else b""

    def _tunnel(self, authenticated):
        target = urllib.parse.urlsplit(self.gate.config["capture"]["api_url"])
        try:
            upstream = socket.create_connection((target.hostname, target.port), timeout=10)
        except OSError:
            self.close_connection = True
            return self._fail(502, "upstream_unavailable")
        self.close_connection = True
        try:
            lines = [f"{self.command} {self.path} HTTP/1.1"]
            lines += [f"{name}: {value}" for name, value in self._upstream_headers(authenticated)]
            lines += ["Upgrade: websocket", "Connection: Upgrade", "", ""]
            upstream.sendall("\r\n".join(lines).encode("latin-1", "replace"))
            upstream.sendall(self._buffered_client_bytes())
            self.wfile.flush()
            self._pump(self.connection, upstream)
        except OSError:
            pass
        finally:
            upstream.close()
        return None

    @staticmethod
    def _pump(client, upstream):
        """Copy bytes both ways until either side closes or the link idles out."""
        peers = {client: upstream, upstream: client}
        while True:
            readable, _, _ = select.select(list(peers), [], [], TUNNEL_IDLE_SECONDS)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                peers[source].sendall(data)


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        """Health probes are not logged."""

    def do_GET(self):
        status = 200 if self.path == "/healthz" else 404
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # A busy restart must rebind without waiting on TIME_WAIT sockets.
    allow_reuse_address = True
    # The default backlog of 5 resets connections during a burst of confirmations.
    request_queue_size = 128

    def handle_error(self, _request, _client_address):
        # The default prints a traceback, which can carry request values.
        self.gate.log(f"handler_error type={sys.exc_info()[0].__name__}")


def create_servers(config, credential, state_dir, *, clock=time.time, log=None,
                   public_address=PUBLIC_ADDRESS, health_address=HEALTH_ADDRESS):
    """Bind the public and internal listeners; serving is the caller's choice."""
    gate = Gate(config, credential, state_dir, clock=clock, log=log or _log)
    public = _Server(public_address, _PublicHandler)
    public.gate = gate
    health = _Server(health_address, _HealthHandler)
    health.gate = gate
    return public, health


def wait_for_capture(config, *, attempts, delay):
    """Refuse to start unless the declared capture service answers."""
    target = urllib.parse.urlsplit(config["capture"]["api_url"])
    for attempt in range(attempts):
        if attempt:
            time.sleep(delay)
        try:
            socket.create_connection((target.hostname, target.port), timeout=3).close()
            return
        except OSError:
            continue
    raise CaptureUnavailable("capture_unreachable")


def _log(line):
    print(line, file=sys.stderr, flush=True)


def main():
    config = json.loads(base64.b64decode(os.environ["SANDBOX_LOGIN_CONFIG"]))
    credential = os.environ.get("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", "")
    if not credential:
        _log("refused code=capture_credential_missing")
        raise SystemExit(1)
    try:
        wait_for_capture(config, attempts=30, delay=2)
    except CaptureUnavailable:
        _log("refused code=capture_unreachable")
        raise SystemExit(1) from None
    public, health = create_servers(config, credential, STATE_DIRECTORY)
    import threading
    threading.Thread(target=health.serve_forever, daemon=True).start()
    _log("started")
    public.serve_forever()


if __name__ == "__main__":
    main()
