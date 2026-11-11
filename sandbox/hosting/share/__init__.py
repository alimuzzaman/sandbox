"""Share links for hosted targets (spec 081).

A target opts in with a ``share:`` block in its hosting manifest. Links open at
``https://<gate hostname>/s/#<token>``: one 128-bit token after the ``#``, so no request
line holds it, and only its SHA-256 is stored anywhere. The public id is ``l-`` and the
first 12 hex characters of that hash. The contract is docs/login-service.md#share-links
and specs/081-hosted-share-links/ (Story 7 for the short form).

Links minted before Story 7 read ``/s/l-<32 random hex>#<43-character token>``. The gate
keeps opening them until they expire (30 days at most), so the id rules below still
accept that form; search ``LEGACY(081 short links)`` to remove it (tasks.md T049).

This module holds the types and rules both the controller and its tests rely on. The
gate runtime (``login_service/runtime.py``) is injected into a container as one stdlib
file, so it repeats the path rules; ``tests/test_share_gate_runtime.py`` checks the two
copies agree.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import socket
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SHARE_ENTRY = "share"
LINK_PREFIX = "/s/"
STACK_PAGE_PATH = "/__sandbox/stack"
ROBOTS_TAG = "noindex, nofollow, noarchive"
INDEXING_VALUES = ("noindex", "public")
MIN_TTL_SECONDS = 60
DEFAULT_TTL_SECONDS = 7 * 86400
MAX_TTL_SECONDS = 30 * 86400
MAX_USES = 20
DEFAULT_SESSION_SECONDS = 43200
# A stack page's sign-in links live this long after the view that minted them.
SIGN_IN_SECONDS = 600
MAX_STACK_HOSTS = 16
# The gate's published loopback port sits this far above the target's own port, in a
# range nothing else in Sandbox uses (plugin-check lanes take 19000-20999).
GATE_PORT_OFFSET = 3000

SHORT_ID_HEX = 12
TOKEN_BYTES = 16
# A short link's id, then (LEGACY(081 short links)) an old-form random id.
LINK_ID_RE = re.compile(r"l-[0-9a-f]{12}|l-[0-9a-f]{32}")
SESSION_ID_RE = re.compile(r"s-[0-9a-f]{32}")
REVOKE_TARGET_RE = re.compile(r"all|l-[0-9a-f]{12}|[ls]-[0-9a-f]{32}")
_TTL_RE = re.compile(r"([0-9]{1,7})([smhdw]?)")
_TTL_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
# What a scope may hold: an encoded URL path, nothing that needs quoting in a header.
_SCOPE_CHARS = re.compile(r"/[A-Za-z0-9._~!$&'()*+,=:@%/-]*")
_UNSAFE_RAW = ("%2f", "%5c", "%2e", "%00", "%25", "\\", ";", "//", "#")

ShareKind = Literal["site", "stack"]


class ShareRefusal(Exception):
    """A finite, named refusal; ``message`` never holds a token."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LinkRequest:
    """One link to mint, after validation."""
    entry: str
    kind: ShareKind
    scope: str
    ttl_seconds: int
    uses: int | None


@dataclass(frozen=True)
class MintedLink:
    """What the controller learns from a mint. ``token`` leaves only in ``url``."""
    link_id: str
    token: str
    issued_at: int
    expires_at: int
    clamped: bool

    @property
    def token_sha256(self) -> str:
        return token_hash(self.token)


def new_token() -> str:
    """128 random bits, 22 URL-safe characters."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def short_link_id(token: str) -> str:
    """The public id: 48 bits of the hash, which name the link and do not lead back to it."""
    return "l-" + token_hash(token)[:SHORT_ID_HEX]


def share_url(hostname: str, token: str) -> str:
    """The fragment never reaches a request line, so no access log can hold the token."""
    return f"https://{hostname}{LINK_PREFIX}#{token}"


def parse_ttl(value: object) -> int:
    """``7d``, ``12h``, ``90m``, ``3600s`` or plain seconds; 60 s to 30 days."""
    if value is None:
        return DEFAULT_TTL_SECONDS
    if isinstance(value, bool):
        raise ShareRefusal("share_ttl_invalid", "--ttl must be a duration such as 7d, 12h or 90m")
    match = _TTL_RE.fullmatch(str(value).strip().lower())
    if match is None:
        raise ShareRefusal("share_ttl_invalid", "--ttl must be a duration such as 7d, 12h or 90m")
    seconds = int(match.group(1)) * _TTL_UNITS[match.group(2)]
    if not MIN_TTL_SECONDS <= seconds <= MAX_TTL_SECONDS:
        raise ShareRefusal("share_ttl_invalid", "--ttl must be from 1 minute to 30 days")
    return seconds


def parse_uses(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_USES:
        raise ShareRefusal("uses_out_of_range", f"--uses must be from 1 to {MAX_USES}")
    return value


def unsafe_path(raw: str) -> bool:
    """True for any path an upstream could resolve to somewhere a scope check did not see."""
    lowered = raw.lower()
    if not raw.startswith("/") or any(marker in lowered for marker in _UNSAFE_RAW):
        return True
    decoded = urllib.parse.unquote(raw)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        return True
    return any(segment in (".", "..") for segment in decoded.split("/"))


def validate_scope(value: object) -> str:
    """``/``, a folder ending in ``/``, or one exact page; refused if unsafe."""
    scope = "/" if value is None else str(value).strip()
    if not _SCOPE_CHARS.fullmatch(scope) or unsafe_path(scope) or len(scope) > 512:
        raise ShareRefusal(
            "share_scope_invalid",
            "--path must be an absolute URL path such as /designs/home/ or /designs/home.html, "
            "with no dot segments, encoded slashes or query")
    return scope


def scope_covers(scope: str, raw_path: str) -> bool:
    """Whether a session scoped to ``scope`` may request ``raw_path`` (already judged safe)."""
    if scope == "/":
        return True
    wanted, path = urllib.parse.unquote(scope), urllib.parse.unquote(raw_path)
    return path.startswith(wanted) if wanted.endswith("/") else path == wanted


def clamp_expiry(issued_at: int, ttl_seconds: int, ends_at: int) -> tuple[int, bool]:
    """A link never outlives the gate's ``enabled_until``."""
    expires = issued_at + ttl_seconds
    if expires > ends_at:
        return ends_at, True
    return expires, False


def link_record(request: LinkRequest, minted: MintedLink) -> dict:
    """The record the gate stores: the hash, never the token."""
    return {"schema": "sandbox.share-link.v2", "entry": request.entry, "id": minted.link_id,
            "token_sha256": minted.token_sha256, "issued_at": minted.issued_at,
            "expires_at": minted.expires_at, "uses": request.uses, "scope": request.scope,
            "kind": request.kind}


def mint(request: LinkRequest, *, now: float, ends_at: int) -> MintedLink:
    issued = int(now)
    expires, clamped = clamp_expiry(issued, request.ttl_seconds, ends_at)
    if expires - issued < MIN_TTL_SECONDS:
        raise ShareRefusal("login_service_expired",
                           "the gate's enabled_until is less than a minute away; extend it and apply")
    token = new_token()
    return MintedLink(short_link_id(token), token, issued, expires, clamped)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

AUDIT_FIELDS = ("at", "op", "controller", "owner", "remote", "target", "entry", "kind",
                "link_id", "scope", "expires_at", "uses", "outcome", "code")


def controller_name() -> str:
    return socket.gethostname().split(".")[0][:64] or "unknown"


def owner_label(project_dir: str | None) -> str:
    """The 077 owner rule (a name, never an email), else the OS user."""
    from sandbox.hosting.lifetime import naming
    try:
        return naming.owner_label(Path(project_dir or "."), None)
    except Exception:  # noqa: BLE001 -- an audit label never blocks a share
        try:
            return getpass.getuser()[:64]
        except Exception:  # noqa: BLE001
            return "unknown"


def audit_record(op: str, *, remote: str, target: str, entry: str | None, outcome: str,
                 code: str | None = None, kind: str | None = None, link_id: str | None = None,
                 scope: str | None = None, expires_at: int | None = None, uses: int | None = None,
                 controller: str, owner: str, now: float | None = None) -> dict:
    """A fixed set of fields: nothing here can carry a token, a hash or an address."""
    return {"at": int(time.time() if now is None else now), "op": op, "controller": controller,
            "owner": owner, "remote": remote, "target": target, "entry": entry, "kind": kind,
            "link_id": link_id, "scope": scope, "expires_at": expires_at, "uses": uses,
            "outcome": outcome, "code": code}


def audit_path() -> Path:
    from sandbox.core import _paths
    return _paths.RUNTIME_DIR / "share-links" / "audit.jsonl"


def append_audit(record: dict, path: Path | None = None) -> None:
    path = path or audit_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({key: record.get(key) for key in AUDIT_FIELDS}, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, line.encode())
    finally:
        os.close(descriptor)


_REMOTE_AUDIT = "\n".join((
    "import json,os,sys",
    "home,line=sys.argv[1],sys.argv[2]",
    "json.loads(line)",
    "d=os.path.join(home,'runtime','share-links')",
    "os.makedirs(d,mode=0o700,exist_ok=True)",
    "fd=os.open(os.path.join(d,'audit.jsonl'),os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)",
    "os.write(fd,(line+'\\n').encode());os.close(fd)",
))


def remote_audit_command(home: str, record: dict) -> str:
    """Append one audit line on the server. The record has no secret, so argv is fine."""
    line = json.dumps({key: record.get(key) for key in AUDIT_FIELDS}, sort_keys=True)
    return shlex.join(["python3", "-c", _REMOTE_AUDIT, home, line])
