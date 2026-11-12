"""One-time login links for a hosted Compose project.

Standard library only: Sandbox injects this file into the service container
through the generated Compose override. Nothing here names an application.
The contract is docs/login-service.md.

Security properties worth keeping in mind when changing this file:

* A link is confirmed by POST, never by GET, so previews cannot use it up.
* The token travels in the URL fragment and the POST body, never a request line.
* Logs are fixed codes. A token, email address, credential, or URL never reaches one.
* With no live token and no session, every public route answers 404.
* A site_gate hostname forwards only to its declared upstream, and only with a session
  held as a hash on the tmpfs, so the operator can list and revoke it.
"""
import base64
import calendar
import collections
import contextlib
import fcntl
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
import ssl
import sys
import threading
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
SITE_LINK_SCHEMA = "sandbox.share-link.v2"
SITE_SESSION_SCHEMA = "sandbox.share-session.v2"
STACK_SCHEMA = "sandbox.share-stack.v1"
STACK_SCHEMA_V2 = "sandbox.share-stack.v2"
MAX_STACK_PAGE_BYTES = 32768
MAX_COMPANIONS = 4
STACK_ACTION_PATH = "/__sandbox/stack/sign-in"
WP_CLAIM_PATH = "/__sandbox/stack/wordpress-claim"
COMPANION_STATUS_PATH = "/__sandbox/stack/companion-status"
# One cookie per gate hostname; it holds up to SHARE_COOKIE_SESSIONS session secrets.
SHARE_COOKIE = "__Host-sandbox_share"
# Every gate answer carries it, so apply can tell the gate's 404 from a front
# door's (sandbox.commands.hosting._GATE_HEADER names the same header).
GATE_HEADER = "X-Sandbox-Gate"
# Reserved before any site, entry or upstream: the gate itself answers it 404, so apply
# verification can tell the gate from an application (sandbox.commands.hosting._GATE_CHECK_PATH).
GATE_CHECK_PATH = "/__sandbox/gate-check"
SHARE_COOKIE_SESSIONS = 8
SHARE_PREFIX = "/s/"
STACK_PAGE_PATH = "/__sandbox/stack"
ROBOTS_TAG = "noindex, nofollow, noarchive"
ROBOTS_DENY_BODY = b"User-agent: *\nDisallow: /\n"
# A share link lives up to 30 days; a session ends sooner (session_ttl_seconds).
MAX_SHARE_SECONDS = 30 * 86400
SIGN_IN_SECONDS = 600
STACK_VIEWS = 60
MAX_STACK_HOSTS = 16
HEALTH_TIMEOUT_SECONDS = 3
HEALTH_CACHE_SECONDS = 30
SITE_MAX_USES = 20
SITE_MAX_LINKS = 32
# Link records kept, spent ones included (their sessions need them) until they expire or
# are revoked. Every POST /s/ scans them all, so this bounds that unauthenticated work.
SITE_MAX_RECORDS = 128
SITE_MAX_SESSIONS = 512
SITE_ATTEMPTS = 30
SITE_ATTEMPT_WINDOW_SECONDS = 60
# How often an open tunnel re-reads its session, so a revoke ends it.
SITE_RECHECK_SECONDS = 30
MAX_RECORD_BYTES = 16384

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_HASH = re.compile(r"[0-9a-f]{64}")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SESSION_EXPIRY = re.compile(r"[0-9]{1,12}")
# A share link's public id: `l-` and the first 12 hex of its token hash (spec 081 Story 7).
# LEGACY(081 short links): links minted before then carry 32 random hex and open at
# /s/<id>; they last 30 days at most, then this form and its path go (tasks.md T049).
_SHORT_LINK_ID = re.compile(r"l-[0-9a-f]{12}")
_LEGACY_LINK_ID = re.compile(r"l-[0-9a-f]{32}")
_LINK_ID = re.compile(r"l-[0-9a-f]{12}|l-[0-9a-f]{32}")
# A short link's token: 128 bits, 22 URL-safe characters.
_SHORT_TOKEN = re.compile(r"[A-Za-z0-9_-]{22}")
_SITE_SESSION_ID = re.compile(r"s-[0-9a-f]{32}")
_SCOPE_CHARS = re.compile(r"/[A-Za-z0-9._~!$&'()*+,=:@%/-]*")
_UNSAFE_RAW = ("%2f", "%5c", "%2e", "%00", "%25", "\\", ";", "//", "#")
_STACK_TITLE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_HOSTNAME = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_ROLE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_WORDPRESS_URL = re.compile(r"https://[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._~/-]*")
_SITE_SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
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
    '<meta name="robots" content="noindex, nofollow">'
    "<title>Confirm sign-in</title></head><body><main><h1>Confirm sign-in</h1>"
    "<p>Continue uses this one-time link. It cannot be used again.</p>"
    '<form method="post"><input type="hidden" name="token">'
    "<button>Continue</button></form>"
    '<p id="incomplete" hidden>This link is incomplete. Open the full link you were sent.</p>'
    f"<script>{_CONFIRM_SCRIPT}</script></main></body></html>"
).encode()
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_CONFIRM_SCRIPT.encode()).digest()).decode()
# A share link (/s/#<token>, or an old-form /s/<id>#<token>) submits itself: link
# previews never run it, and the token stays in the fragment, so no request line or
# access log ever holds it.
_SITE_SCRIPT = (
    "(function(){var m=location.hash.match(/^#([A-Za-z0-9_-]{22}|[A-Za-z0-9_-]{43})$/);"
    "var f=document.forms[0];history.replaceState(null,'',location.pathname);"
    "if(m){f.elements.token.value=m[1];f.submit();}else{f.hidden=true;"
    "document.getElementById('incomplete').hidden=false;}})();"
)
_SITE_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="robots" content="noindex, nofollow">'
    "<title>Opening shared site</title></head><body><main><h1>Opening shared site</h1>"
    '<form method="post"><input type="hidden" name="token">'
    "<button>Continue</button></form>"
    '<p id="incomplete" hidden>This link is incomplete. Open the full link you were sent.</p>'
    f"<script>{_SITE_SCRIPT}</script></main></body></html>"
).encode()
_SITE_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SITE_SCRIPT.encode()).digest()).decode()


class CaptureUnavailable(RuntimeError):
    """The declared capture service cannot be reached."""


class SignInFailure(Exception):
    """A named, fail-closed outcome of the magic-link exchange."""

    def __init__(self, code, status):
        super().__init__(code)
        self.code = code
        self.status = status


def _write_private(path, text):
    """Write a small record so no reader ever sees part of it."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _unlink(path):
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


class SiteStore:
    """Share links, sessions and stack pages of one site_gate entry, as files on the tmpfs.

    Links and sessions are named by the SHA-256 of their secret, so nothing here stores
    a token or a session secret. Every record also carries a public id for list and
    revoke. A stack page record holds a WordPress verification key (spec 081): it can
    mint sign-ins only while its link lives, and goes with the link.
    """

    _LINK_KEYS = {"schema", "entry", "id", "token_sha256", "issued_at", "expires_at", "uses",
                  "scope", "kind"}

    def __init__(self, root, name, clock):
        self.name = name
        self.clock = clock
        base = Path(root) / "sites" / name
        self.links, self.used, self.sessions = base / "links", base / "used", base / "sessions"
        self.stacks = base / "stacks"

    def ensure(self):
        for directory in (self.links, self.used, self.sessions, self.stacks):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def _load(path, schema, limit=MAX_RECORD_BYTES):
        try:
            with open(path, "rb") as handle:
                data = handle.read(limit + 1)
                if len(data) > limit:
                    return None
                raw = json.loads(data)
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) and raw.get("schema") in ((schema,) if isinstance(schema, str) else schema) else None

    def _valid_link(self, raw, digest=None):
        if raw is None or raw.get("entry") != self.name or set(raw) != self._LINK_KEYS:
            return None
        found, link_id = raw.get("token_sha256"), raw.get("id")
        issued, expires, uses = raw.get("issued_at"), raw.get("expires_at"), raw.get("uses")
        if not isinstance(found, str) or not _HASH.fullmatch(found) or (digest and found != digest):
            return None
        if not isinstance(link_id, str) or not _LINK_ID.fullmatch(link_id):
            return None
        # A short id is its own hash's prefix, so it names no other link.
        if _SHORT_LINK_ID.fullmatch(link_id) and link_id[2:] != found[:12]:
            return None
        if type(issued) is not int or type(expires) is not int:
            return None
        if uses is not None and (type(uses) is not int or not 1 <= uses <= SITE_MAX_USES):
            return None
        if not MIN_LINK_SECONDS <= expires - issued <= MAX_SHARE_SECONDS:
            return None
        scope = raw.get("scope")
        if not isinstance(scope, str) or not valid_scope(scope) or raw.get("kind") not in ("site", "stack"):
            return None
        return raw

    def link(self, digest):
        """The well-formed link record for a token hash, or None."""
        if not _HASH.fullmatch(digest):
            return None
        return self._valid_link(self._load(self.links / f"{digest}.json", SITE_LINK_SCHEMA), digest)

    def _links(self):
        for path in sorted(self.links.glob("*.json")):
            record = self._valid_link(self._load(path, SITE_LINK_SCHEMA), path.stem)
            if record is not None:
                yield path, record

    def find_short(self, digest):
        """The short-form link whose token hashes to ``digest``, or None.

        Every stored record is compared, in constant time and without stopping early, so
        a wrong, unknown, revoked, expired or malformed token costs the same work.
        """
        found = None
        for _, record in self._links():
            if hmac.compare_digest(record["token_sha256"], digest) and _SHORT_LINK_ID.fullmatch(record["id"]):
                found = record
        return found

    def live_id(self, link_id):
        """Whether a live old-form link has this id (its opener page answers only then).

        LEGACY(081 short links).
        """
        return any(record["id"] == link_id and self.is_live(record) for _, record in self._links())

    def stack_link_alive(self, link_id):
        """Whether a stack link is unrevoked and unexpired: its sign-in links live only that long."""
        if not isinstance(link_id, str) or not _LINK_ID.fullmatch(link_id):
            return False
        return (self.stacks / f"{link_id}.json").exists() and any(
            record["id"] == link_id and self.clock() < record["expires_at"] for _, record in self._links())

    def spent(self, record):
        if record["uses"] is None:
            return 0
        return sum(1 for slot in range(record["uses"])
                   if (self.used / f"{record['token_sha256']}.{slot}").exists())

    def is_live(self, record):
        return self.clock() < record["expires_at"] and (
            record["uses"] is None or self.spent(record) < record["uses"])

    def claim(self, record):
        """Spend one use; of any number of concurrent callers, one gets each slot."""
        if record["uses"] is None:
            return True
        for slot in range(record["uses"]):
            marker = self.used / f"{record['token_sha256']}.{slot}"
            try:
                os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                continue
            return True
        return False

    def _link_alive(self, digest):
        record = self.link(digest)
        return record is not None and self.clock() < record["expires_at"]

    def _valid_session(self, raw):
        if raw is None or raw.get("entry") != self.name:
            return None
        session_id, link_id, link_digest = raw.get("id"), raw.get("link"), raw.get("link_sha256")
        created, expires, scope = raw.get("created_at"), raw.get("expires_at"), raw.get("scope")
        if (not isinstance(session_id, str) or not _SITE_SESSION_ID.fullmatch(session_id)
                or not isinstance(link_id, str) or not _LINK_ID.fullmatch(link_id)
                or not isinstance(link_digest, str) or not _HASH.fullmatch(link_digest)
                or type(created) is not int or type(expires) is not int
                or not isinstance(scope, str) or not valid_scope(scope)
                or raw.get("kind") not in ("site", "stack")):
            return None
        return raw

    def _sessions(self):
        for path in sorted(self.sessions.glob("*.json")):
            record = self._valid_session(self._load(path, SITE_SESSION_SCHEMA))
            if record is not None:
                yield path, record

    def start_session(self, record, session_seconds, service_ends_at):
        """Create a session for a claimed link; returns (secret, expires_at) or None."""
        self.prune()
        if sum(1 for _ in self.sessions.glob("*.json")) >= SITE_MAX_SESSIONS:
            return None
        secret = secrets.token_urlsafe(32)
        now = int(self.clock())
        expires = min(now + session_seconds, service_ends_at, record["expires_at"])
        if expires <= now:
            return None
        path = self.sessions / f"{hashlib.sha256(secret.encode()).hexdigest()}.json"
        _write_private(path, json.dumps({
            "schema": SITE_SESSION_SCHEMA, "entry": self.name, "id": f"s-{secrets.token_hex(16)}",
            "link": record["id"], "link_sha256": record["token_sha256"], "created_at": now,
            "expires_at": expires, "scope": record["scope"], "kind": record["kind"]}, sort_keys=True))
        # A revoke deletes the link before its sessions, so whichever order the two
        # take, a session from a revoked link never survives.
        if not (self.links / f"{record['token_sha256']}.json").exists():
            _unlink(path)
            return None
        return secret, expires

    def session(self, secret):
        """The live session for one cookie secret, or None.

        Live means unexpired and with its link still on disk: a revoke ends access at
        the next request even if removing the session file lost a race.
        """
        if not isinstance(secret, str) or not _SITE_SECRET.fullmatch(secret):
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        record = self._valid_session(self._load(self.sessions / f"{digest}.json", SITE_SESSION_SCHEMA))
        if record is None or self.clock() >= record["expires_at"] or not self._link_alive(record["link_sha256"]):
            return None
        return dict(record, digest=digest, secret=secret)

    def sessions_for(self, cookie):
        """Every live session a share cookie holds, newest last."""
        found = []
        for secret in (cookie or "").split(".")[:SHARE_COOKIE_SESSIONS]:
            session = self.session(secret)
            if session is not None:
                found.append(session)
        return found

    def session_alive(self, digest):
        record = self._valid_session(self._load(self.sessions / f"{digest}.json", SITE_SESSION_SCHEMA))
        return (record is not None and self.clock() < record["expires_at"]
                and self._link_alive(record["link_sha256"]))

    def prune(self):
        """Drop expired links with their markers and stack records, and dead sessions."""
        now = self.clock()
        for path, record in list(self._links()):
            if now >= record["expires_at"]:
                self._drop_link(path, record)
        for path, record in list(self._sessions()):
            if now >= record["expires_at"] or not (self.links / f"{record['link_sha256']}.json").exists():
                _unlink(path)

    def _drop_link(self, path, record):
        removed = _unlink(path)
        for slot in range(record["uses"] or 0):
            _unlink(self.used / f"{record['token_sha256']}.{slot}")
        _unlink(self.stacks / f"{record['id']}.json")
        return removed

    def stack(self, link_id, config):
        """The stack page record of a stack link, or None."""
        if not _LINK_ID.fullmatch(link_id or ""):
            return None
        return valid_stack(self._load(self.stacks / f"{link_id}.json", (STACK_SCHEMA, STACK_SCHEMA_V2), MAX_STACK_PAGE_BYTES * 2), self.name, link_id, config)

    def mint(self, text, *, service_ends_at, config):
        """Store a link (and its stack page) sent by the operator's controller."""
        try:
            raw = json.loads(text)
        except ValueError:
            return {"ok": False, "code": "site_record_invalid"}
        if not isinstance(raw, dict) or set(raw) != {"link", "stack"}:
            return {"ok": False, "code": "site_record_invalid"}
        link = raw["link"]
        record = self._valid_link(link if isinstance(link, dict) and link.get("schema") == SITE_LINK_SCHEMA else None)
        if record is None:
            return {"ok": False, "code": "site_record_invalid"}
        stack = None
        if record["kind"] == "stack":
            stack = valid_stack(raw["stack"], self.name, record["id"], config)
            if (stack is None or record["scope"] != STACK_PAGE_PATH
                    or any(child["expires_at"] != record["expires_at"] for child in stack.get("companions", []))):
                return {"ok": False, "code": "site_record_invalid"}
        elif raw["stack"] is not None:
            return {"ok": False, "code": "site_record_invalid"}
        if stack is not None and stack["schema"] == STACK_SCHEMA and len(json.dumps(stack).encode()) > MAX_RECORD_BYTES:
            return {"ok": False, "code": "site_record_invalid"}
        if self.clock() >= service_ends_at or record["expires_at"] > service_ends_at:
            return {"ok": False, "code": "login_service_expired"}
        with self._mint_lock():
            return self._admit(record, stack)

    @contextlib.contextmanager
    def _mint_lock(self):
        """One mint at a time per entry, across the separate operator processes, so the
        live-link and kept-record caps hold under concurrent mints."""
        descriptor = os.open(self.links.parent / ".mint.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _admit(self, record, stack):
        """Count, check and write one link; the caller holds the mint lock."""
        self.prune()
        stored = list(self._links())
        if sum(1 for _, item in stored if self.is_live(item)) >= SITE_MAX_LINKS:
            return {"ok": False, "code": "site_link_limit"}
        if len(stored) >= SITE_MAX_RECORDS or len(list(self.links.glob("*.json"))) >= SITE_MAX_RECORDS:
            return {"ok": False, "code": "site_record_limit"}
        target = self.links / f"{record['token_sha256']}.json"
        if target.exists() or any(item["id"] == record["id"] for _, item in stored):
            return {"ok": False, "code": "site_record_invalid"}
        if stack is not None:
            # Before the link: a live stack link always finds its page.
            _write_private(self.stacks / f"{record['id']}.json", json.dumps(stack, sort_keys=True))
        _write_private(target, json.dumps(record, sort_keys=True))
        return {"ok": True, "id": record["id"], "expires_at": record["expires_at"], "uses": record["uses"]}

    def listing(self):
        """Ids, times, scopes and counts only: never a token, a secret, a key or a hash.

        Spent links are listed too (``used`` equals ``uses``): they count toward
        SITE_MAX_RECORDS until they expire, so the owner needs their ids to revoke them.
        """
        self.prune()
        links = [{"id": record["id"], "kind": record["kind"], "scope": record["scope"],
                  "issued_at": record["issued_at"], "expires_at": record["expires_at"],
                  "uses": record["uses"], "used": self.spent(record)}
                 for _, record in self._links()]
        sessions = [{"id": record["id"], "link": record["link"], "created_at": record["created_at"],
                     "expires_at": record["expires_at"]}
                    for _, record in self._sessions() if self.clock() < record["expires_at"]]
        return {"ok": True, "links": sorted(links, key=lambda item: item["issued_at"]),
                "sessions": sorted(sessions, key=lambda item: item["created_at"])}

    def revoke(self, target):
        """Revoke one link (with its sessions and stack page), one session, or everything."""
        links = sessions = 0
        if target == "all" or _LINK_ID.fullmatch(target or ""):
            for path, record in list(self._links()):
                if target == "all" or record["id"] == target:
                    links += self._drop_link(path, record)
            for path, record in list(self._sessions()):
                if target == "all" or record["link"] == target:
                    sessions += _unlink(path)
            if target == "all":
                for path in self.stacks.glob("*.json"):
                    _unlink(path)
        elif _SITE_SESSION_ID.fullmatch(target or ""):
            for path, record in list(self._sessions()):
                if record["id"] == target:
                    sessions += _unlink(path)
        if target != "all" and not links and not sessions:
            return {"ok": False, "code": "site_link_unknown"}
        return {"ok": True, "revoked_links": links, "revoked_sessions": sessions}


def _validate_stack_email(value):
    address = str(value or "").strip()
    if len(address) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", address):
        raise ValueError("invalid email")
    return address


def valid_stack_v2(record):
    """Exact private gate wire contract; stdlib-only rules mirrored by runtime owner.

    This validator must never be used to sanitize public list/audit output: the
    record deliberately contains keys and companion bearer credentials.
    """

    def text(value, limit):
        return (isinstance(value, str) and 0 < len(value) <= limit
                and all(ord(c) >= 32 and ord(c) != 127 for c in value))

    def host(value):
        return isinstance(value, str) and len(value) <= 253 and bool(_HOSTNAME.fullmatch(value))

    def origin(value):
        return (isinstance(value, str) and value.startswith("https://")
                and host(value[8:]))

    def notes(value):
        return isinstance(value, list) and len(value) <= 16 and all(text(v, 512) for v in value)

    def rows(value, actions, wp):
        if not isinstance(value, list) or len(value) > MAX_STACK_HOSTS:
            return False
        seen = set()
        for row in value:
            if not isinstance(row, dict) or set(row) != {"hostname", "role", "probe", "commit", "state", "access"}:
                return False
            if (not host(row["hostname"]) or row["hostname"] in seen
                    or not text(row["role"], 32) or row["probe"] not in ("internal", "public")
                    or row["state"] not in ("unknown", "serving", "redirecting", "applying", "failed", "instance_not_found")
                    or (row["commit"] is not None and (not isinstance(row["commit"], str)
                        or not re.fullmatch(r"[0-9a-f]{40}", row["commit"])))
                    or not isinstance(row["access"], str)
                    or row["access"] not in {"open", *actions, *(["$wordpress"] if wp else [])}):
                return False
            seen.add(row["hostname"])
        return True

    try:
        if not isinstance(record, dict) or set(record) != {
                "schema", "entry", "link", "title", "hosts", "notes", "wordpress", "sign_in", "inbox", "companions"}:
            return False
        if len(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > MAX_STACK_PAGE_BYTES:
            return False
        if (record["schema"] != STACK_SCHEMA_V2 or not isinstance(record["entry"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", record["entry"])
                or not isinstance(record["link"], str) or not _LINK_ID.fullmatch(record["link"])
                or not text(record["title"], 63) or not notes(record["notes"])):
            return False
        actions = set()
        if not isinstance(record["sign_in"], list) or len(record["sign_in"]) > 16:
            return False
        for row in record["sign_in"]:
            if not isinstance(row, dict) or set(row) not in ({"entry"}, {"entry", "email"}):
                return False
            name = row["entry"]
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name)
                    or name in actions or name == "share"):
                return False
            if "email" in row:
                _validate_stack_email(row["email"])
            actions.add(name)
        wp = record["wordpress"]
        if wp is not None:
            if (not isinstance(wp, dict) or set(wp) != {"kid", "key", "url", "claim_origin"}
                    or not isinstance(wp["kid"], str) or not re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{32}", wp["kid"])
                    or wp["kid"] != record["link"][2:]
                    or not isinstance(wp["key"], str) or not _HASH.fullmatch(wp["key"])
                    or not origin(wp["claim_origin"]) or not isinstance(wp["url"], str)):
                return False
            parsed = urllib.parse.urlsplit(wp["url"])
            if (not origin("https://" + parsed.netloc) or parsed.scheme != "https"
                    or parsed.query or parsed.fragment or not re.fullmatch(r"/[A-Za-z0-9._~/-]*", parsed.path)
                    or ".." in parsed.path.split("/")):
                return False
        if not rows(record["hosts"], actions, wp is not None):
            return False
        inbox = record["inbox"]
        if inbox is not None and (not isinstance(inbox, dict) or set(inbox) != {"entry"}
                                  or not isinstance(inbox["entry"], str) or inbox["entry"] not in actions
                                  or any(row["entry"] == inbox["entry"] and "email" in row
                                         for row in record["sign_in"])):
            return False
        companions = record["companions"]
        if not isinstance(companions, list) or len(companions) > MAX_COMPANIONS:
            return False
        seen = set()
        for row in companions:
            if not isinstance(row, dict) or set(row) != {"slug", "target", "hostname", "link_id", "expires_at", "token", "title", "hosts", "notes"}:
                return False
            if (not isinstance(row["slug"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", row["slug"])
                    or not isinstance(row["target"], str)
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,191}", row["target"])
                    or any(p in ("", ".", "..") for p in row["target"].split("/"))
                    or not host(row["hostname"]) or row["target"] in seen
                    or not isinstance(row["token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", row["token"])
                    or row["link_id"] != ("l-" + hashlib.sha256(row["token"].encode()).hexdigest()[:12])
                    or type(row["expires_at"]) is not int or not 0 < row["expires_at"] < 10**12
                    or not text(row["title"], 63) or not notes(row["notes"])):
                return False
            # Companion rows are display metadata. Their own page owns actions.
            display_actions = {r.get("access") for r in row["hosts"] if isinstance(r, dict)
                               and isinstance(r.get("access"), str)} if isinstance(row["hosts"], list) else set()
            if (any(action not in ("open", "$wordpress") and not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", action)
                    for action in display_actions) or not rows(row["hosts"], display_actions, True)):
                return False
            seen.add(row["target"])
        return True
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def valid_stack(raw, entry, link_id, config):
    """A stack page record for ``link_id``, or None. Its fields come from the controller."""
    if isinstance(raw, dict) and raw.get("schema") == STACK_SCHEMA_V2:
        if not valid_stack_v2(raw) or raw["entry"] != entry or raw["link"] != link_id:
            return None
        own = config["entries"].get(entry, {})
        wp = raw["wordpress"]
        if wp and wp["claim_origin"] != "https://" + own.get("hostname", ""):
            return None
        suffixes = config.get("allowed_host_suffixes") or []
        def covered(host):
            return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)
        if (any(not covered(row["hostname"]) for row in raw["hosts"] + raw["companions"])
                or any(row["hostname"] == own.get("hostname") for row in raw["companions"])
                or (wp and not covered(urllib.parse.urlsplit(wp["url"]).hostname))):
            return None
        for row in raw["sign_in"]:
            declared = config["entries"].get(row["entry"], {})
            if declared.get("mode") not in ("magic_link", "inbox_gate"):
                return None
            if (declared["mode"] == "magic_link") != ("email" in row):
                return None
            if "email" in row and row["email"].strip().casefold() in declared["magic_link"].get("denied_emails", []):
                return None
        return raw
    if not isinstance(raw, dict) or raw.get("schema") != STACK_SCHEMA or raw.get("entry") != entry \
            or raw.get("link") != link_id \
            or set(raw) != {"schema", "entry", "link", "title", "hosts", "wordpress", "sign_in"}:
        return None
    if not isinstance(raw["title"], str) or not _STACK_TITLE.fullmatch(raw["title"]):
        return None
    hosts = raw["hosts"]
    if not isinstance(hosts, list) or len(hosts) > MAX_STACK_HOSTS:
        return None
    for item in hosts:
        if (not isinstance(item, dict) or set(item) != {"hostname", "role", "probe"}
                or not isinstance(item["hostname"], str) or not _HOSTNAME.fullmatch(item["hostname"])
                or not isinstance(item["role"], str) or not _ROLE.fullmatch(item["role"])
                or item["probe"] not in ("internal", "public")):
            return None
    wordpress = raw["wordpress"]
    if wordpress is not None:
        if (not isinstance(wordpress, dict) or set(wordpress) != {"kid", "key", "url"}
                or wordpress["kid"] != link_id[2:] or not isinstance(wordpress["key"], str)
                or not _HASH.fullmatch(wordpress["key"]) or not isinstance(wordpress["url"], str)
                or not _WORDPRESS_URL.fullmatch(wordpress["url"])):
            return None
    sign_in = raw["sign_in"]
    if not isinstance(sign_in, list) or len(sign_in) > 8:
        return None
    for item in sign_in:
        declared = config["entries"].get(item.get("entry")) if isinstance(item, dict) else None
        if not isinstance(declared, dict) or declared.get("mode") not in ("magic_link", "inbox_gate"):
            return None
        email = item.get("email")
        if set(item) - {"entry", "email"} or (declared["mode"] == "magic_link") != isinstance(email, str) \
                or (email is not None and not _ADDRESS.fullmatch(email)):
            return None
    return raw


def unsafe_path(raw):
    """True for any path an upstream could resolve to somewhere a scope check did not see.

    Kept identical to sandbox.hosting.share.unsafe_path; a test compares the two.
    """
    lowered = raw.lower()
    if not raw.startswith("/") or any(marker in lowered for marker in _UNSAFE_RAW):
        return True
    decoded = urllib.parse.unquote(raw)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        return True
    return any(segment in (".", "..") for segment in decoded.split("/"))


def valid_scope(scope):
    return bool(_SCOPE_CHARS.fullmatch(scope)) and len(scope) <= 512 and not unsafe_path(scope)


def scope_covers(scope, raw_path):
    """Kept identical to sandbox.hosting.share.scope_covers."""
    if scope == "/":
        return True
    wanted, path = urllib.parse.unquote(scope), urllib.parse.unquote(raw_path)
    return path.startswith(wanted) if wanted.endswith("/") else path == wanted



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
        self.sites = {}
        for name, entry in config["entries"].items():
            if entry["mode"] == "site_gate":
                self.sites[name] = SiteStore(self.state_dir, name, clock)
                self.sites[name].ensure()
        self._attempts = collections.defaultdict(collections.deque)
        self._attempts_lock = threading.Lock()
        self._pool_lock = threading.RLock()
        # Mailbox snapshots and polling are shared across all magic-link sources.
        # Keep this separate so pending token issue/claim/eviction never waits on I/O.
        self._capture_exchange_lock = threading.Lock()
        self._pending = collections.defaultdict(collections.OrderedDict)
        self._claim_nonces = {}
        self._health = {}
        self._health_lock = threading.Lock()

    def site_for_host(self, host):
        for name, entry in self.config["entries"].items():
            if entry["mode"] == "site_gate" and entry["hostname"] == host:
                return name, entry
        return None

    def allow_attempt(self, name, limit=SITE_ATTEMPTS):
        """A sliding window per entry. A per-address limit could only key on a header
        the client controls, so the window is per entry (and per stack-page bucket)."""
        with self._attempts_lock:
            now = self.clock()
            window = self._attempts[name]
            while window and window[0] <= now - SITE_ATTEMPT_WINDOW_SECONDS:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(now)
            return True

    def health(self, hosts, upstream):
        """Health per hostname, probed in parallel and cached for HEALTH_CACHE_SECONDS."""
        now, results, pending = time.monotonic(), {}, []
        with self._health_lock:
            for item in hosts:
                cached = self._health.get(item["hostname"])
                if cached and now - cached[0] < HEALTH_CACHE_SECONDS:
                    results[item["hostname"]] = cached[1]
                else:
                    pending.append(item)
        suffixes = self.config.get("allowed_host_suffixes") or []

        def run(item):
            status = _probe(item["hostname"], item["probe"], upstream, suffixes)
            with self._health_lock:
                self._health[item["hostname"]] = (time.monotonic(), status)

        threads = [threading.Thread(target=run, args=(item,), daemon=True) for item in pending]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS + 1
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._health_lock:
            for item in pending:
                cached = self._health.get(item["hostname"])
                results[item["hostname"]] = cached[1] if cached else "unreachable"
        return results

    def mint_sign_in(self, name, email, expires_at, via):
        """Issue into the eight-record pool without replacing the direct CLI singleton."""
        entry = self.config["entries"].get(name)
        issued = int(self.clock())
        if (not isinstance(entry, dict) or entry.get("mode") not in ("magic_link", "inbox_gate")
                or not 0 < int(expires_at) - issued <= SIGN_IN_SECONDS):
            return None
        if entry["mode"] == "magic_link" and (not isinstance(email, str) or not _ADDRESS.fullmatch(email)
                or email.casefold() in entry["magic_link"].get("denied_emails", [])):
            return None
        token = secrets.token_urlsafe(32)
        record = {"schema": RECORD_SCHEMA, "entry": name,
                  "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                  "issued_at": issued, "expires_at": int(expires_at),
                  "via": {"entry": via[0], "link": via[1]}, "email": email}
        if not self.register_pending(name, record):
            return None
        return f"https://{entry['hostname']}{entry['path']}#sandbox_autologin={token}"

    def _prune_pending(self):
        for pool in self._pending.values():
            for digest, record in list(pool.items()):
                if self.clock() >= record["expires_at"] or not self.bound(record):
                    del pool[digest]
        self._claim_nonces = {key: expiry for key, expiry in self._claim_nonces.items()
                              if self.clock() < expiry}

    def register_pending(self, name, record):
        with self._pool_lock:
            self._prune_pending()
            if not self.enabled() or not self.bound(record) or self.clock() >= record["expires_at"]:
                return False
            pool = self._pending[name]
            pending = [digest for digest, row in pool.items() if not row.get("claimed")]
            while len(pending) >= 8:
                del pool[pending.pop(0)]
            # Claimed records are needed only by in-flight exchanges, whose callers retain them.
            for digest, row in list(pool.items()):
                if row.get("claimed"):
                    del pool[digest]
            pool[record["token_sha256"]] = record
            return True

    def pooled_record(self, name, digest):
        with self._pool_lock:
            self._prune_pending()
            return self._pending.get(name, {}).get(digest)

    def claim_pending(self, name, record):
        with self._pool_lock:
            if self.pooled_record(name, record["token_sha256"]) is not record or record.get("claimed"):
                return False
            if not self.enabled() or not self.bound(record):
                return False
            record["claimed"] = True
            return True

    def csrf(self, session):
        return hmac.new(self.session_key, ("stack:" + session["digest"]).encode(), hashlib.sha256).hexdigest()

    def wordpress_claim(self, name, payload):
        """Authenticate and atomically consume one registered WP hash; never accept stateless tokens."""
        if not isinstance(payload, dict) or set(payload) != {"kid", "token_sha256", "nonce", "at", "mac"}:
            return None
        kid, digest, nonce, at, mac = (payload[k] for k in ("kid", "token_sha256", "nonce", "at", "mac"))
        if (not isinstance(kid, str) or not re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{32}", kid)
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)
                or type(at) is not int or abs(self.clock() - at) > 30
                or not isinstance(mac, str) or not _HASH.fullmatch(mac)):
            return None
        with self._pool_lock:
            record = self.pooled_record(name + ":wordpress", digest)
            store = self.sites[name]
            page = store.stack("l-" + kid, self.config)
            wp = page.get("wordpress") if page else None
            if (not record or not wp or record["via"] != {"entry": name, "link": "l-" + kid}
                    or record.get("claimed") or not self.enabled() or not self.bound(record)):
                return None
            key = bytes.fromhex(wp["key"])
            message = f"sandbox-share-wp-claim-request.v1|{kid}|{digest}|{nonce}|{at}"
            if not hmac.compare_digest(mac, hmac.new(key, message.encode(), hashlib.sha256).hexdigest()):
                return None
            replay = (name, kid, nonce)
            if replay in self._claim_nonces or not self.claim_pending(name + ":wordpress", record):
                return None
            self._claim_nonces[replay] = at + 31
            now, expiry = int(self.clock()), record["expires_at"]
            if not self.enabled() or not self.bound(record) or now >= expiry or abs(now - at) > 30:
                return None
            message = f"sandbox-share-wp-claim-response.v1|{kid}|{digest}|{nonce}|{now}|{expiry}"
            return {"ok": True, "kid": kid, "token_sha256": digest, "nonce": nonce,
                    "at": now, "expires_at": expiry,
                    "mac": hmac.new(key, message.encode(), hashlib.sha256).hexdigest()}

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
        return raw if self.bound(raw) else None

    def bound(self, record):
        """Whether a token record's stack link (if a stack page minted it) is still alive.

        A sign-in from a stack page dies with that stack link. Callers check again after
        the body arrives and right before granting, so a revoke during a slow sign-in wins.
        """
        if "via" not in record:
            return True
        via = record["via"]
        store = self.sites.get(via.get("entry")) if isinstance(via, dict) else None
        return store is not None and store.stack_link_alive(via.get("link"))

    def current(self, name, record):
        """Whether ``record`` is still this entry's token and its stack link is alive.

        Checked after the body arrives and right before a grant: a newer link for the
        entry, or a revoke of the stack link, beats a sign-in that was still being posted.
        """
        if "via" in record and record.get("claimed"):
            return self.enabled() and self.clock() < record["expires_at"] and self.bound(record)
        if "via" in record:
            return self.pooled_record(name, record["token_sha256"]) is record and self.bound(record)
        now = self.record(name)
        return (now is not None and hmac.compare_digest(now["token_sha256"], record["token_sha256"])
                and self.bound(record))

    def _used_marker(self, digest):
        return self.state_dir / "used" / digest

    def is_live(self, name):
        with self._pool_lock:
            self._prune_pending()
            if any(not r.get("claimed") for r in self._pending.get(name, {}).values()):
                return True
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

    def _signature(self, name, expires, via=None):
        message = f"{name}:{expires}"
        if via is not None:
            message += f":stack:{via['entry']}:{via['link']}"
        return hmac.new(self.session_key, message.encode(), hashlib.sha256).hexdigest()

    def new_session(self, name, record=None):
        expires = int(self.clock()) + self.config["session_ttl_seconds"]
        if record is not None and "via" in record:
            if not self.current(name, record):
                return None
            via = record["via"]
            store = self.sites[via["entry"]]
            parent = next((row for _, row in store._links() if row["id"] == via["link"]), None)
            if parent is None:
                return None
            expires = min(expires, parent["expires_at"])
            if not self.enabled() or self.clock() >= expires or not self.bound(record):
                return None
            return f"{expires}.{via['entry']}.{via['link']}.{self._signature(name, expires, via)}"
        return f"{expires}.{self._signature(name, expires)}"

    def session_valid(self, name, value):
        parts = (value or "").split(".")
        via = None
        if len(parts) == 4:
            expires, entry, link, signature = parts
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", entry) or not _LINK_ID.fullmatch(link):
                return False
            via = {"entry": entry, "link": link}
        elif len(parts) == 2:
            expires, signature = parts
        else:
            return False
        if not _SESSION_EXPIRY.fullmatch(expires):
            return False
        # Bytes, because compare_digest raises on a non-ASCII str from a client.
        if not hmac.compare_digest(self._signature(name, int(expires), via).encode(),
                                   signature.encode("utf-8", "replace")):
            return False
        return self.clock() < int(expires) and (via is None or self.bound({"via": via}))

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

    def sign_in(self, settings, email, *, source_is_current=None):
        """Serialize the mailbox snapshot, application request, and capture poll."""
        if not self._capture_exchange_lock.acquire(timeout=UPSTREAM_TIMEOUT_SECONDS):
            raise SignInFailure("capture_busy", 503)
        try:
            if not self.enabled() or (source_is_current is not None and not source_is_current()):
                raise SignInFailure("source_unavailable", 404)
            existing = set(self._message_ids())
            started = time.time()
            # Snapshot I/O can itself take time; refuse if its source expires or is
            # revoked before the application receives a request.
            if not self.enabled() or (source_is_current is not None and not source_is_current()):
                raise SignInFailure("source_unavailable", 404)
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
        finally:
            self._capture_exchange_lock.release()


def capture_headers(capture, credential):
    """Basic auth for a capture service with a login; nothing for one without."""
    if not capture.get("username"):
        return {}
    secret = base64.b64encode(f"{capture['username']}:{credential}".encode()).decode()
    return {"Authorization": f"Basic {secret}"}


def wordpress_link(wordpress, expires_at):
    """A one-time WordPress admin link that the target's share-key mu-plugin verifies.

    The token rides in the fragment; the mu-plugin's page posts it, so no request line
    or access log ever holds it. The key never leaves this service.
    """
    nonce = secrets.token_urlsafe(16)
    message = f"sandbox-share-login.v1|{wordpress['kid']}|{int(expires_at)}|{nonce}"
    mac = hmac.new(bytes.fromhex(wordpress["key"]), message.encode(), hashlib.sha256).hexdigest()
    return f"{wordpress['url']}?sandbox_share_login={wordpress['kid']}#{int(expires_at)}.{nonce}.{mac}"


def classify(status):
    if status is None:
        return "unreachable"
    if 200 <= status < 300:
        return "up"
    if 300 <= status < 400:
        return "redirect"
    if status in (401, 403):
        return "up_login"
    return "client_error" if status < 500 else "server_error"


MAX_UPGRADE_HEAD = 16384
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _read_upgrade_head(upstream):
    """The upstream's answer to an upgrade: (status, headers, bytes after the head).

    Anything unreadable comes back as status 0, which the caller refuses.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_UPGRADE_HEAD:
            return 0, [], b""
        chunk = upstream.recv(4096)
        if not chunk:
            return 0, [], b""
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or parts[0] not in ("HTTP/1.0", "HTTP/1.1") or not re.fullmatch(r"[0-9]{3}", parts[1]):
        return 0, [], b""
    headers = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not _HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
            return 0, [], b""
        headers.append((name, value.strip()))
    return int(parts[1]), headers, rest


def _probe(hostname, probe, upstream, suffixes):
    """One GET /, no redirects followed. Internal probes go through the project's network."""
    if probe == "public" and not any(hostname == suffix or hostname.endswith(f".{suffix}")
                                     for suffix in suffixes):
        return "not_checked"
    connection = None
    try:
        if probe == "internal":
            connection = http.client.HTTPConnection(*upstream, timeout=HEALTH_TIMEOUT_SECONDS)
        else:
            connection = http.client.HTTPSConnection(
                hostname, 443, timeout=HEALTH_TIMEOUT_SECONDS, context=ssl.create_default_context())
        connection.request("GET", "/", headers={"Host": hostname, "User-Agent": "sandbox-share-health"})
        response = connection.getresponse()
        response.read(65536)
        return classify(response.status)
    except (OSError, http.client.HTTPException, ValueError):
        return "unreachable"
    finally:
        if connection is not None:
            connection.close()


_STACK_STYLE = (
    "body{font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;color:#1d2433;background:#f7f8fa}"
    "main{max-width:760px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}"
    "p{color:#4a5468;margin:0 0 16px}table{width:100%;border-collapse:collapse;background:#fff}"
    "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #e3e6ec;word-break:break-all}"
    "th{font-size:13px;color:#4a5468}.up{color:#137333}.up_login{color:#137333}"
    ".redirect{color:#8a5a00}.client_error,.server_error,.unreachable{color:#b3261e}"
    "ul{padding-left:20px}li{margin:6px 0}a{color:#1a56c4}"
    "@media (prefers-color-scheme:dark){body{background:#14171c;color:#e6e9ef}"
    "table{background:#1c2027}th,p{color:#a7afbe}td,th{border-color:#2c313a}a{color:#8ab4f8}}"
)
_STACK_STYLE_HASH = base64.b64encode(hashlib.sha256(_STACK_STYLE.encode()).digest()).decode()
_HEALTH_LABELS = {"up": "up", "up_login": "up (sign-in required)", "redirect": "redirects",
                  "client_error": "client error", "server_error": "server error",
                  "unreachable": "unreachable", "not_checked": "not checked"}


def render_stack_page(record, health, links):
    """Plain HTML, every value escaped; the links are this view's alone."""
    rows = "".join(
        f'<tr><td><a href="https://{html.escape(item["hostname"])}/" rel="noreferrer noopener" '
        f'target="_blank">{html.escape(item["hostname"])}</a></td><td>{html.escape(item["role"])}</td>'
        f'<td class="{html.escape(health.get(item["hostname"], "not_checked"))}">'
        f'{html.escape(_HEALTH_LABELS.get(health.get(item["hostname"], "not_checked"), "unknown"))}</td></tr>'
        for item in record["hosts"])
    sign_in = "".join(
        f'<li><a href="{html.escape(url)}" rel="noreferrer noopener" target="_blank">{html.escape(label)}</a></li>'
        for label, url in links) or "<li>This stack declares no sign-in links.</li>"
    title = html.escape(record["title"])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>Stack {title}</title><style>{_STACK_STYLE}</style></head><body><main>"
        f"<h1>Stack {title}</h1>"
        f"<p>Each click creates a one-time sign-in lasting up to {SIGN_IN_SECONDS // 60} minutes.</p>"
        "<table><thead><tr><th>Server</th><th>Role</th><th>Health</th></tr></thead>"
        f"<tbody>{rows}</tbody></table><h2>Sign in</h2><ul>{sign_in}</ul></main></body></html>"
    ).encode()


_STACK_ACTION_SCRIPT = (
    "document.querySelectorAll('[data-action]').forEach(function(b){b.onclick=async function(){"
    "var w=window.open('about:blank','_blank');if(!w)return;w.opener=null;"
    "try{var r=await fetch('/__sandbox/stack/sign-in',{method:'POST',credentials:'same-origin',"
    "headers:{'Content-Type':'application/json','X-Sandbox-Stack-Action':'1'},"
    "body:JSON.stringify({action:b.dataset.action,csrf:document.querySelector('meta[name=stack-csrf]').content})});"
    "if(!r.ok)throw Error();var v=await r.json();if(!v.url)throw Error();w.location.replace(v.url);"
    "}catch(e){w.close();document.getElementById('action-result').textContent='Unavailable. Try a fresh click.';}};});"
)
_STACK_ACTION_HASH = base64.b64encode(hashlib.sha256(_STACK_ACTION_SCRIPT.encode()).digest()).decode()


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def companion_available(descriptor):
    """Authenticated non-consuming child proof. No redirects, cookies, fallback or remint."""
    body = {key: descriptor[key] for key in ("token", "link_id", "expires_at", "title", "hosts", "notes")}
    connection = http.client.HTTPSConnection(descriptor["hostname"], timeout=5)
    try:
        connection.request("POST", COMPANION_STATUS_PATH, body=json.dumps(body, separators=(",", ":")),
                           headers={"Content-Type": "application/json", "X-Sandbox-Companion-Probe": "1"})
        response = connection.getresponse()
        data = response.read(2049)
        if response.status != 200 or len(data) > 2048:
            return False
        return _strict_json(data) == {"ok": True, "available": True,
                                      "link_id": descriptor["link_id"], "expires_at": descriptor["expires_at"]}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


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
        if not any(name.lower() == "referrer-policy" for name, _ in headers):
            self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", ROBOTS_TAG)
        self.send_header(GATE_HEADER, "1")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self):
        self._drain()
        self._answer_not_found()

    def _answer_not_found(self):
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
        if urllib.parse.urlsplit(self.path).path == GATE_CHECK_PATH:
            return self._not_found()
        host = self._host()
        site = gate.site_for_host(host)
        if site is not None:
            return self._site(*site)
        path = urllib.parse.urlsplit(self.path).path
        entries = gate.config["entries"]
        for name, entry in entries.items():
            if entry["hostname"] == host and entry["path"] == path:
                return self._handle_entry(name, entry)
        for name, entry in entries.items():
            if entry["hostname"] == host and entry["mode"] == "inbox_gate":
                cookie = self._cookie(gate.cookie_name(name))
                if gate.session_valid(name, cookie):
                    return self._proxy(authenticated=True, session_expires=int(cookie.partition(".")[0]),
                                       still_allowed=lambda: gate.enabled() and gate.session_valid(name, cookie))
                if gate.is_live(name):
                    # Anyone without a link still meets the service's own login,
                    # but only while a link is outstanding (no standing access).
                    return self._proxy(authenticated=False)
        return self._not_found()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _dispatch

    def _site(self, name, entry):
        """A site_gate hostname: robots, a link opener, the stack page, or the upstream."""
        # One Host header, so the upstream cannot pick a different one than was checked.
        if len(self.headers.get_all("Host") or []) != 1:
            return self._not_found()
        store = self.gate.sites[name]
        # Split at "?" only: urlsplit would also drop a "#..." that a request line can
        # carry, and the upstream would then resolve a path the scope never saw.
        raw_path = self.path.split("?", 1)[0]
        if raw_path == "/robots.txt" and self.command in ("GET", "HEAD"):
            self._drain()
            return self._reply(200, ROBOTS_DENY_BODY, [("Content-Type", "text/plain; charset=utf-8")])
        if raw_path == WP_CLAIM_PATH:
            payload = self._json_body(1024)
            if payload is None:
                return self._answer_not_found()
            if not self.gate.allow_attempt(name + ":wpclaim", 120):
                return self._fail(429, "rate_limited")
            receipt = self.gate.wordpress_claim(name, payload)
            if receipt is None:
                return self._answer_not_found()
            self.gate.log(f"wordpress_claimed entry={name}")
            return self._json_reply(receipt)
        if raw_path == COMPANION_STATUS_PATH:
            return self._companion_probe(name, store)
        if raw_path == STACK_ACTION_PATH:
            return self._stack_action(name, entry, store)
        if raw_path == SHARE_PREFIX:
            return self._share_open_short(name, entry, store)
        if raw_path.startswith(SHARE_PREFIX):
            return self._share_open(name, entry, store, raw_path[len(SHARE_PREFIX):])
        if unsafe_path(raw_path):
            return self._not_found()
        sessions = store.sessions_for(self._cookie(SHARE_COOKIE))
        if raw_path == STACK_PAGE_PATH:
            stack = next((item for item in reversed(sessions) if item["kind"] == "stack"), None)
            if stack is None or self.command not in ("GET", "HEAD"):
                return self._not_found()
            self._drain()
            return self._stack_page(name, entry, store, stack)
        session = next((item for item in reversed(sessions)
                        if item["kind"] == "site" and scope_covers(item["scope"], raw_path)), None)
        if session is None:
            return self._not_found()
        upstream = urllib.parse.urlsplit(entry["upstream"])
        return self._forward((upstream.hostname, upstream.port), self._site_headers(entry),
                             session["expires_at"], lambda: store.session_alive(session["digest"]))

    def _share_open_short(self, name, entry, store):
        """``/s/``: a page that posts the fragment's token, then the token-for-cookie swap.

        The page is the same whatever links exist. Past the rate limit, every refusal is
        one 404 after the same scan of every stored link (spec 081 Story 7, FR-038).
        """
        gate = self.gate
        if self.command in ("GET", "HEAD"):
            self._drain()
            return self._reply(200, _SITE_PAGE, self._site_page_headers())
        if self.command != "POST":
            return self._not_found()
        if not gate.allow_attempt(name):
            self._drain()
            return self._fail(429, "rate_limited")
        token = self._read_token() or ""
        well_formed = _SHORT_TOKEN.fullmatch(token) is not None
        # A malformed token is hashed and looked up too, so it costs what a wrong one does.
        record = store.find_short(hashlib.sha256(token.encode("utf-8", "replace")).hexdigest())
        # Under Referrer-Policy: no-referrer a browser sends "Origin: null" on its own
        # form POST. A cross-site post still needs the token, and would only spend a use.
        origin = self.headers.get("Origin")
        if (not well_formed or record is None or origin not in (None, "null", f"https://{entry['hostname']}")
                or not store.is_live(record) or not gate.enabled() or not store.claim(record)):
            return self._answer_not_found()
        return self._start_share(name, store, record)

    def _share_open(self, name, entry, store, link_id):
        """``/s/<id>``: an old-form link's opener page, then the token-for-cookie swap.

        LEGACY(081 short links): kept until the last old-form link expires (tasks.md T049).
        """
        gate = self.gate
        if not _LEGACY_LINK_ID.fullmatch(link_id):
            return self._not_found()
        if self.command in ("GET", "HEAD"):
            self._drain()
            if not store.live_id(link_id):
                return self._answer_not_found()
            return self._reply(200, _SITE_PAGE, self._site_page_headers())
        if self.command != "POST":
            return self._not_found()
        if not gate.allow_attempt(name):
            self._drain()
            return self._fail(429, "rate_limited")
        token = self._read_token()
        # Every refusal past this point is the same 404, so a probe learns nothing.
        if token is None or not _TOKEN.fullmatch(token):
            return self._answer_not_found()
        # Under Referrer-Policy: no-referrer a browser sends "Origin: null" on its own
        # form POST. A cross-site post still needs the token, and would only spend a use.
        origin = self.headers.get("Origin")
        if origin not in (None, "null", f"https://{entry['hostname']}"):
            return self._answer_not_found()
        record = store.link(hashlib.sha256(token.encode()).hexdigest())
        if (record is None or record["id"] != link_id or not store.is_live(record)
                or not gate.enabled() or not store.claim(record)):
            return self._answer_not_found()
        return self._start_share(name, store, record)

    def _start_share(self, name, store, record):
        """A claimed link becomes a session; the cookie keeps this browser's other shares."""
        gate = self.gate
        started = store.start_session(record, gate.config["session_ttl_seconds"],
                                      gate.config["enabled_until_epoch"])
        if started is None:
            gate.log(f"share_session_refused entry={name}")
            return self._answer_not_found()
        secret, expires = started
        gate.log(f"share_opened entry={name} link={record['id']}")
        # Keep the other shares this browser already holds on this gate, newest last.
        kept = [item for item in store.sessions_for(self._cookie(SHARE_COOKIE)) if item["secret"] != secret]
        kept = kept[-(SHARE_COOKIE_SESSIONS - 1):]
        latest = max([expires, *(item["expires_at"] for item in kept)])
        value = ".".join([*(item["secret"] for item in kept), secret])
        cookie = (f"{SHARE_COOKIE}={value}; Path=/; Max-Age={max(1, latest - int(gate.clock()))}; "
                  "Secure; HttpOnly; SameSite=Lax")
        landing = STACK_PAGE_PATH if record["kind"] == "stack" else record["scope"]
        return self._reply(303, b"", [("Location", landing), ("Set-Cookie", cookie)])

    def _json_reply(self, payload):
        return self._reply(200, json.dumps(payload, separators=(",", ":")).encode(),
                           [("Content-Type", "application/json"), ("X-Content-Type-Options", "nosniff")])

    def _json_body(self, maximum):
        length = self._content_length()
        if (self.command != "POST" or self.path not in (STACK_ACTION_PATH, WP_CLAIM_PATH, COMPANION_STATUS_PATH)
                or self.headers.get_all("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length") or []) != 1
                or len(self.headers.get_all("Content-Type") or []) != 1
                or self.headers.get("Content-Type") != "application/json"
                or length is None or not 0 < length <= maximum):
            self.close_connection = True
            return None
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                return None
            return _strict_json(data)
        except (UnicodeError, ValueError, RecursionError):
            return None

    def _companion_probe(self, name, store):
        payload = self._json_body(MAX_STACK_PAGE_BYTES)
        gate = self.gate
        if not gate.allow_attempt(name + ":probe", 120):
            return self._fail(429, "rate_limited")
        if (not isinstance(payload, dict) or set(payload) != {"token", "link_id", "expires_at", "title", "hosts", "notes"}
                or self.headers.get("X-Sandbox-Companion-Probe") != "1"
                or not isinstance(payload["token"], str) or not _SHORT_TOKEN.fullmatch(payload["token"])):
            return self._answer_not_found()
        record = store.find_short(hashlib.sha256(payload["token"].encode()).hexdigest())
        page = store.stack(record["id"], gate.config) if record else None
        if (not record or not page or page["schema"] != STACK_SCHEMA_V2 or record["kind"] != "stack"
                or record["id"] != payload["link_id"] or record["expires_at"] != payload["expires_at"]
                or any(page[key] != payload[key] for key in ("title", "hosts", "notes"))
                or not gate.enabled() or not store.is_live(record) or not store.stack_link_alive(record["id"])):
            return self._answer_not_found()
        gate.log(f"companion_status entry={name}")
        return self._json_reply({"ok": True, "available": True, "link_id": record["id"],
                                 "expires_at": record["expires_at"]})

    def _stack_action(self, name, entry, store):
        gate = self.gate
        payload = self._json_body(1024)
        session = next((row for row in reversed(store.sessions_for(self._cookie(SHARE_COOKIE)))
                        if row["kind"] == "stack"), None)
        if (not session or not isinstance(payload, dict) or set(payload) != {"action", "csrf"}
                or self.headers.get_all("Origin") != ["https://" + entry["hostname"]]
                or self.headers.get_all("X-Sandbox-Stack-Action") != ["1"]
                or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin")
                or not isinstance(payload["csrf"], str) or not _HASH.fullmatch(payload["csrf"])
                or not hmac.compare_digest(payload["csrf"], gate.csrf(session))):
            return self._answer_not_found()
        if not gate.allow_attempt(name + ":action"):
            return self._fail(429, "rate_limited")
        page = store.stack(session["link"], gate.config)
        def alive():
            return gate.enabled() and store.session_alive(session["digest"]) and store.stack_link_alive(session["link"])
        if not page or not alive() or not isinstance(payload["action"], str):
            return self._answer_not_found()
        action = payload["action"]
        ends = min(int(gate.clock()) + SIGN_IN_SECONDS, session["expires_at"], gate.config["enabled_until_epoch"])
        url = None
        if action == "$wordpress":
            wp = page["wordpress"]
            # Legacy stateless plugins cannot enforce pool eviction. Fail closed until reissued as v2.
            if not wp or page["schema"] != STACK_SCHEMA_V2:
                return self._answer_not_found()
            url = wordpress_link(wp, ends)
            digest = hashlib.sha256(url.partition("#")[2].encode()).hexdigest()
            pending = {"token_sha256": digest, "issued_at": int(gate.clock()), "expires_at": ends,
                       "via": {"entry": name, "link": session["link"]}}
            if not gate.register_pending(name + ":wordpress", pending):
                return self._answer_not_found()
        elif action.startswith("companion:") or action.startswith("companion-status:"):
            prefix, _, index = action.partition(":")
            children = page.get("companions", [])
            if not re.fullmatch(r"[0-3]", index) or int(index) >= len(children):
                return self._answer_not_found()
            child = children[int(index)]
            if (gate.clock() >= child["expires_at"] or not companion_available(child) or not alive()):
                return self._answer_not_found()
            gate.log(f"companion_status entry={name}")
            if prefix == "companion-status":
                return self._json_reply({"ok": True, "available": True})
            url = "https://" + child["hostname"] + SHARE_PREFIX + "#" + child["token"]
        else:
            row = next((row for row in page["sign_in"] if row["entry"] == action), None)
            if row is None:
                return self._answer_not_found()
            url = gate.mint_sign_in(row["entry"], row.get("email"), ends, (name, session["link"]))
        if not url or not alive():
            return self._answer_not_found()
        gate.log(f"stack_action entry={name}")
        return self._json_reply({"ok": True, "url": url})

    def _stack_page(self, name, entry, store, session):
        """GET/HEAD expose metadata and action controls only, without issuing tokens."""
        gate = self.gate
        record = store.stack(session["link"], gate.config)
        if record is None:
            return self._answer_not_found()
        if not gate.allow_attempt(f"{name}:view", STACK_VIEWS):
            return self._fail(429, "rate_limited")
        upstream = urllib.parse.urlsplit(entry["upstream"])
        health = gate.health(record["hosts"], (upstream.hostname, upstream.port))
        actions = [(row["entry"], "Sign in: " + row["entry"]) for row in record["sign_in"]]
        if record["wordpress"] and record["schema"] == STACK_SCHEMA_V2:
            actions.insert(0, ("$wordpress", "WordPress admin"))
        for index, child in enumerate(record.get("companions", [])):
            actions.append((f"companion:{index}", "Open shared stack: " + child["title"]))
        controls = "".join('<li><button data-action="' + html.escape(action, quote=True) + '">'
                           + html.escape(label) + '</button></li>' for action, label in actions)
        body = render_stack_page(record, health, [])
        body = body.replace(b"</head>", ('<meta name="stack-csrf" content="' + gate.csrf(session) + '"></head>').encode())
        body = body.replace(b"<ul><li>This stack declares no sign-in links.</li></ul>",
                            ('<ul>' + controls + '</ul><p id="action-result" role="status"></p>').encode())
        details = "".join("<p>" + html.escape(note) + "</p>" for note in record.get("notes", []))
        for host in record["hosts"]:
            if "state" in host:
                details += ("<p>" + html.escape(host["hostname"]) + ": " + html.escape(host["state"])
                            + " / " + html.escape(host["commit"] or "revision unknown")
                            + " / " + html.escape(host["access"]) + "</p>")
        for child in record.get("companions", []):
            details += "<h2>" + html.escape(child["title"]) + "</h2>"
            details += "".join("<p>" + html.escape(row["hostname"]) + " / " + html.escape(row["role"]) + "</p>"
                               for row in child["hosts"])
            details += "".join("<p>" + html.escape(note) + "</p>" for note in child["notes"])
        body = body.replace(b"</main>", (details + "</main>").encode())
        body = body.replace(b"</body>", ('<script>' + _STACK_ACTION_SCRIPT + '</script></body>').encode())
        if not gate.enabled() or not store.session_alive(session["digest"]):
            return self._answer_not_found()
        gate.log(f"stack_viewed entry={name} link={session['link']}")
        return self._reply(200, body, self._stack_page_headers())

    @staticmethod
    def _site_page_headers():
        policy = ("default-src 'none'; "
                  f"script-src 'sha256-{_SITE_SCRIPT_HASH}'; "
                  "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        return [("Content-Type", "text/html; charset=utf-8"), ("Content-Security-Policy", policy),
                ("X-Content-Type-Options", "nosniff")]

    @staticmethod
    def _stack_page_headers():
        policy = (f"default-src 'none'; style-src 'sha256-{_STACK_STYLE_HASH}'; "
                  f"script-src 'sha256-{_STACK_ACTION_HASH}'; connect-src 'self'; "
                  "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
        return [("Referrer-Policy", "same-origin"), ("Content-Type", "text/html; charset=utf-8"), ("Content-Security-Policy", policy),
                ("X-Content-Type-Options", "nosniff")]

    def _site_headers(self, entry):
        """The visitor's headers for the upstream, with Host fixed and the gate's cookies removed."""
        headers = [("Host", entry["hostname"])]
        for name, value in self.headers.items():
            lowered = name.lower()
            if lowered in _HOP_BY_HOP or lowered == "host" or "\r" in value or "\n" in value:
                continue
            if lowered == "cookie":
                kept = [part.strip() for part in value.split(";")
                        if part.strip() and not part.strip().startswith(
                            (SHARE_COOKIE, "__Host-sandbox_login_"))]
                if kept:
                    headers.append((name, "; ".join(kept)))
                continue
            headers.append((name, value))
        return headers

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
        if record is None and not gate.is_live(name):
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
        # The confirmation page sends Referrer-Policy: no-referrer, under which a browser
        # posts "Origin: null"; the token still has to match.
        origin = self.headers.get("Origin")
        if origin not in (None, "null", f"https://{entry['hostname']}"):
            return self._fail(403, "invalid_link")
        if not _TOKEN.fullmatch(token):
            return self._fail(403, "invalid_link")
        digest = hashlib.sha256(token.encode()).hexdigest()
        record = gate.pooled_record(name, digest) or record
        if record is None:
            return self._answer_not_found()
        if not hmac.compare_digest(digest, record["token_sha256"]):
            return self._fail(403, "invalid_link")
        if gate.clock() >= record["expires_at"]:
            return self._fail(410, "link_expired")
        email = record.get("email")
        if entry["mode"] == "magic_link" and not (isinstance(email, str) and _ADDRESS.fullmatch(email)):
            return self._fail(403, "invalid_link")
        # The body can arrive long after dispatch loaded the record: recheck the cutoff,
        # that the record is still the entry's current one, and its stack link.
        if not gate.enabled() or not gate.current(name, record):
            return self._answer_not_found()
        if not (gate.claim_pending(name, record) if "via" in record else gate.claim(digest)):
            return self._fail(410, "link_used")
        gate.log(f"consumed entry={name}")
        if entry["mode"] == "inbox_gate":
            if not gate.current(name, record):
                return self._answer_not_found()
            return self._start_session(name, entry, record)
        return self._exchange_magic_link(name, entry, email, record)

    def _start_session(self, name, entry, record=None):
        # No Max-Age or Expires: the cookie ends with the browser session. The
        # server-side limit is carried, signed, inside the value.
        value = self.gate.new_session(name, record)
        if value is None:
            return self._answer_not_found()
        cookie = (f"{self.gate.cookie_name(name)}={value}; Path=/; "
                  "Secure; HttpOnly; SameSite=Strict")
        self._reply(303, b"", [("Location", entry.get("landing_path") or "/"), ("Set-Cookie", cookie)])

    def _exchange_magic_link(self, name, entry, email, record):
        if not self.gate.enabled() or not self.gate.current(name, record):
            return self._answer_not_found()
        try:
            link = self.gate.sign_in(
                entry["magic_link"], email,
                source_is_current=lambda: self.gate.enabled() and self.gate.current(name, record))
        except SignInFailure as failure:
            self.gate.log(f"signin_failed entry={name} code={failure.code}")
            return self._fail(failure.status, failure.code)
        # The exchange waits on mail; source revocation or expiry still wins.
        if not self.gate.enabled() or not self.gate.current(name, record):
            return self._answer_not_found()
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

    def _proxy(self, authenticated, session_expires=None, still_allowed=None):
        target = urllib.parse.urlsplit(self.gate.config["capture"]["api_url"])
        return self._forward((target.hostname, target.port), self._upstream_headers(authenticated),
                             session_expires, still_allowed)

    def _forward(self, address, headers, session_expires, still_allowed=None):
        """Send this request to a fixed address; nothing in it chooses where."""
        # Framing first, for both modes: a request the gate cannot frame never goes on.
        length = self._content_length()
        if length is None or "Transfer-Encoding" in self.headers:
            self.close_connection = True
            return self._fail(411, "length_required")
        if length > MAX_PROXY_BODY:
            self.close_connection = True
            return self._fail(413, "too_large")
        if self.headers.get("Upgrade", "").lower() == "websocket":
            if self.command != "GET" or length:
                self.close_connection = True
                return self._fail(400, "bad_upgrade")
            return self._tunnel(address, headers, session_expires, still_allowed)
        connection = http.client.HTTPConnection(*address, timeout=30)
        try:
            body = self.rfile.read(length) if length else None
            if still_allowed is not None and not still_allowed():
                return self._answer_not_found()
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for name, value in headers:
                connection.putheader(name, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            if still_allowed is not None and not still_allowed():
                return self._answer_not_found()
            self._relay(response, still_allowed)
        except (OSError, http.client.HTTPException):
            self.close_connection = True
            return self._fail(502, "upstream_unavailable")
        finally:
            connection.close()
        return None

    def _relay(self, response, still_allowed=None):
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in _HOP_BY_HOP and name.lower() not in (
                    "referrer-policy", "x-robots-tag", GATE_HEADER.lower()):
                self.send_header(name, value)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", ROBOTS_TAG)
        self.send_header(GATE_HEADER, "1")
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
            if still_allowed is not None and not still_allowed():
                self.close_connection = True
                return
            chunk = response.read(65536)
            if not chunk:
                return
            # A slow upstream body can outlive the check at response headers.
            if still_allowed is not None and not still_allowed():
                self.close_connection = True
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

    def _tunnel(self, address, headers, session_expires, still_allowed=None):
        """A websocket. Raw bytes flow only after the upstream accepts the upgrade.

        Until the upstream answers 101 with ``Upgrade: websocket``, nothing the client sent
        after its request goes upstream. An upstream that declines and keeps the
        connection open therefore never sees later requests that skipped the gate's checks.
        """
        self.close_connection = True
        if still_allowed is not None and not still_allowed():
            return self._answer_not_found()
        pending = self._buffered_client_bytes()
        try:
            upstream = socket.create_connection(address, timeout=10)
        except OSError:
            return self._fail(502, "upstream_unavailable")
        try:
            # Connecting can block long enough for the original grant to end.
            if still_allowed is not None and not still_allowed():
                return self._answer_not_found()
            lines = [f"{self.command} {self.path} HTTP/1.1"]
            lines += [f"{name}: {value}" for name, value in headers]
            lines += ["Upgrade: websocket", "Connection: Upgrade", "", ""]
            upstream.sendall("\r\n".join(lines).encode("latin-1", "replace"))
            status, response_headers, rest = _read_upgrade_head(upstream)
            if status != 101 or not any(name.lower() == "upgrade" and value.lower() == "websocket"
                                        for name, value in response_headers):
                return self._fail(502, "upgrade_refused")
            if still_allowed is not None and not still_allowed():
                return self._answer_not_found()
            self.send_response(101)
            for name, value in response_headers:
                if name.lower() not in ("x-robots-tag", "referrer-policy", "server", "date",
                                        GATE_HEADER.lower()):
                    self.send_header(name, value)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", ROBOTS_TAG)
            self.send_header(GATE_HEADER, "1")
            self.end_headers()
            self.wfile.flush()
            if rest:
                self.connection.sendall(rest)
            if pending:
                upstream.sendall(pending)
            self._pump(self.connection, upstream, session_expires, still_allowed)
        except OSError:
            pass
        finally:
            upstream.close()
        return None

    def send_error(self, code, message=None, explain=None):
        """Errors http.server raises itself (an unknown method, a bad request line) carry the gate's headers too."""
        self.close_connection = True
        self._reply(code, f"{int(code)}\n".encode(),
                    [("Content-Type", "text/plain; charset=utf-8"), ("Connection", "close")])

    def _pump(self, client, upstream, session_expires, still_allowed=None):
        """Copy bytes both ways until a side closes, the link idles out, or access ends.

        With ``still_allowed`` the session is re-read at least every SITE_RECHECK_SECONDS,
        so a revoked session loses its tunnel too.
        """
        gate = self.gate
        ends_at = gate.config["enabled_until_epoch"]
        if session_expires is not None:
            ends_at = min(ends_at, session_expires)
        peers = {client: upstream, upstream: client}
        idle_since = checked_at = time.monotonic()
        while True:
            remaining = ends_at - gate.clock()
            if remaining <= 0:
                return
            wait = min(TUNNEL_IDLE_SECONDS, remaining)
            if still_allowed is not None:
                wait = min(wait, SITE_RECHECK_SECONDS)
            readable, _, _ = select.select(list(peers), [], [], wait)
            if gate.clock() >= ends_at:
                return
            if still_allowed is not None and (
                    not readable or time.monotonic() - checked_at >= SITE_RECHECK_SECONDS):
                if not still_allowed():
                    return
                checked_at = time.monotonic()
            if not readable:
                if still_allowed is None or time.monotonic() - idle_since >= TUNNEL_IDLE_SECONDS:
                    return
                continue
            idle_since = time.monotonic()
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


def manage(config, argv, state_dir, *, stdin, stdout, clock=time.time):
    """``site mint|list|revoke <entry> [id]``, run by the operator inside the service.

    Prints one JSON line of ids, times and counts. A refusal is ``ok: false`` with a code.
    """
    def emit(payload):
        stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        return 0

    if len(argv) < 2:
        return emit({"ok": False, "code": "site_usage"})
    verb, name, extra = argv[0], argv[1], argv[2:]
    entry = config["entries"].get(name)
    if not isinstance(entry, dict) or entry.get("mode") != "site_gate":
        return emit({"ok": False, "code": "site_entry_unknown"})
    if verb == "capabilities" and not extra:
        return emit({"ok": True, "capabilities": ["stack-page-v2", "stack-signin-pool-v1",
                     "wordpress-claim-v1", "companion-status-v1", "owner-panel-gate-v1"],
                     "config_digest": hashlib.sha256(json.dumps(config, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest(),
                     "panel_access_digest": config.get("panel_access_digest"),
                     "panel_target_digest": config.get("panel_target_digest"),
                     "stack_schemas": [STACK_SCHEMA, STACK_SCHEMA_V2], "link_schemas": [SITE_LINK_SCHEMA]})
    store = SiteStore(state_dir, name, clock)
    store.ensure()
    if verb == "mint" and not extra:
        return emit(store.mint(stdin.read(MAX_STACK_PAGE_BYTES * 2 + MAX_RECORD_BYTES), service_ends_at=config["enabled_until_epoch"],
                               config=config))
    if verb == "list" and not extra:
        return emit(store.listing())
    if verb == "revoke" and len(extra) == 1:
        return emit(store.revoke(extra[0]))
    return emit({"ok": False, "code": "site_usage"})


def main():
    config = json.loads(base64.b64decode(os.environ["SANDBOX_LOGIN_CONFIG"]))
    # Under ``python3 -c``, argv[1] is this source; operator verbs follow it.
    if sys.argv[2:3] == ["site"]:
        raise SystemExit(manage(config, sys.argv[3:], STATE_DIRECTORY, stdin=sys.stdin, stdout=sys.stdout))
    credential = os.environ.get("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", "")
    capture = config.get("capture")
    if capture and capture.get("username") and not credential:
        _log("refused code=capture_credential_missing")
        raise SystemExit(1)
    if capture:
        try:
            wait_for_capture(config, attempts=30, delay=2)
        except CaptureUnavailable:
            _log("refused code=capture_unreachable")
            raise SystemExit(1) from None
    public, health = create_servers(config, credential, STATE_DIRECTORY)
    threading.Thread(target=health.serve_forever, daemon=True).start()
    _log("started")
    public.serve_forever()


if __name__ == "__main__":
    main()
