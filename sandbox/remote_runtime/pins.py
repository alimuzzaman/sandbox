"""Strict-mode runtime pins (spec 061 FR-007..FR-015).

A pin is a small JSON file under ``<remote SANDBOX_HOME>/runtime/remote-pins``
naming one controller checkout that requires the exact installed revision.
One fixed program, run over the existing authenticated SSH transport under a
remote flock, owns every read and write; this module never parses remote
output it has not validated. Pins carry no secrets and expire (at most four
hours after their last renewal); expiry is the only stale-holder detection.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Callable

SCHEMA = 1
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 4 * 3600
MAX_LISTED = 64
MAX_PURPOSE = 120
MAX_CHECKOUT = 4096
TIMEOUT_SECONDS = 15

HOLDER_RE = re.compile(r"h-[0-9a-f]{16}")
REVISION_RE = re.compile(r"[0-9a-f]{24}")
DIGEST_RE = re.compile(r"[0-9a-f]{16}")
ACTIVE = "active"
BROKEN = "broken"

PIN_UNVERIFIABLE = "strict_pin_unverifiable"
PINS_UNAVAILABLE = "pins_unavailable"


class PinError(RuntimeError):
    """A pin operation failed; ``code`` is a fixed vocabulary value."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def holder_identity(home: str | os.PathLike, checkout: str | os.PathLike) -> str:
    """``h-`` + 16 hex of sha256(local home realpath NUL checkout realpath)."""
    return "h-" + _digest16(f"{os.path.realpath(home)}\0{os.path.realpath(checkout)}")


def _printable(value: str) -> bool:
    return all(ch.isprintable() for ch in value)


def _secret_free(value: str) -> bool:
    from sandbox.services.redaction import redact_text
    return redact_text(value) == value


def purpose_for(command: str, checkout: str) -> str:
    """A bounded, printable purpose from the command and checkout path."""
    text = " ".join(f"{command} {checkout}".split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_PURPOSE]


@dataclass(frozen=True)
class Pin:
    holder: str
    checkout_path: str
    controller_home_digest: str
    revision: str
    purpose: str
    registered_at: int
    renewed_at: int
    expires_at: int
    state: str = ACTIVE
    broken_by: str | None = None
    broken_at: int | None = None

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at

    def binding(self, now: float | None = None) -> bool:
        """An unexpired, unbroken pin binds the installed revision."""
        return self.state == ACTIVE and not self.expired(now)

    def as_mapping(self, now: float | None = None) -> dict:
        return {
            "schema": SCHEMA, "holder": self.holder,
            "checkout_path": self.checkout_path,
            "controller_home_digest": self.controller_home_digest,
            "revision": self.revision, "purpose": self.purpose,
            "registered_at": self.registered_at, "renewed_at": self.renewed_at,
            "expires_at": self.expires_at, "state": self.state,
            "broken_by": self.broken_by, "broken_at": self.broken_at,
            "expired": self.expired(now),
        }

    def stored(self) -> dict:
        value = self.as_mapping(0)
        value.pop("expired")
        return value


def _int(value) -> int | None:
    return value if type(value) is int and 0 <= value < 2**40 else None


def parse_pin(value) -> Pin | None:
    """Validate one stored pin; anything malformed is dropped, never trusted."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    holder, revision = value.get("holder"), value.get("revision")
    checkout, home = value.get("checkout_path"), value.get("controller_home_digest")
    purpose, state = value.get("purpose"), value.get("state")
    times = [_int(value.get(k)) for k in ("registered_at", "renewed_at", "expires_at")]
    if (not isinstance(holder, str) or not HOLDER_RE.fullmatch(holder)
            or not isinstance(revision, str) or not REVISION_RE.fullmatch(revision)
            or not isinstance(home, str) or not DIGEST_RE.fullmatch(home)
            or not isinstance(checkout, str) or not 0 < len(checkout) <= MAX_CHECKOUT
            or not _printable(checkout) or not _secret_free(checkout)
            or not isinstance(purpose, str) or len(purpose) > MAX_PURPOSE
            or not _printable(purpose) or not _secret_free(purpose)
            or state not in (ACTIVE, BROKEN) or None in times):
        return None
    registered, renewed, expires = times
    if not registered <= renewed <= expires or expires - renewed > MAX_TTL_SECONDS:
        return None
    broken_by, broken_at = value.get("broken_by"), value.get("broken_at")
    if state == BROKEN:
        if (not isinstance(broken_by, str) or not HOLDER_RE.fullmatch(broken_by)
                or _int(broken_at) is None):
            return None
    else:
        broken_by = broken_at = None
    return Pin(holder, checkout, home, revision, purpose, registered, renewed, expires,
               state, broken_by, broken_at)


# The remote side. Fixed text; its only inputs are the remote Sandbox home and
# one base64 JSON request. Writes are atomic (temp file + rename, 0600) under
# an exclusive flock; expired pins are removed on every write.
PROGRAM = r'''
import base64, fcntl, json, os, re, sys, time
home = sys.argv[1]
request = json.loads(base64.b64decode(sys.argv[2]).decode())
HOLDER = re.compile(r"h-[0-9a-f]{16}")
root = os.path.join(home, "runtime", "remote-pins")
now = int(time.time())
if request.get("action") == "list":
    # Read-only: never create the directory or its lock file.
    if not os.path.isdir(root):
        print(json.dumps({"ok": True, "action": "list", "now": now, "pins": []}))
        raise SystemExit(0)
    try:
        lock = os.open(os.path.join(root, ".lock"), os.O_RDONLY)
        fcntl.flock(lock, fcntl.LOCK_SH)
    except FileNotFoundError:
        pass
else:
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = os.open(os.path.join(root, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)

def path(holder):
    if not isinstance(holder, str) or not HOLDER.fullmatch(holder):
        raise SystemExit(3)
    return os.path.join(root, holder + ".json")

def read(holder):
    try:
        with open(path(holder)) as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def write(value):
    target = path(value.get("holder"))
    temp = target + ".tmp"
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
    os.replace(temp, target)

def every():
    out = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".json") and HOLDER.fullmatch(name[:-5]):
            value = read(name[:-5])
            if value is not None:
                out.append(value)
    return out

def sweep():
    for value in every():
        expires = value.get("expires_at")
        if type(expires) is int and expires <= now:
            try:
                os.unlink(path(value.get("holder")))
            except OSError:
                pass

action = request.get("action")
result = {"ok": True, "action": action, "now": now}
if action == "list":
    result["pins"] = every()[:64]
elif action == "register":
    sweep()
    write(request["pin"])
    result["pin"] = read(request["pin"]["holder"])
elif action == "release":
    sweep()
    try:
        os.unlink(path(request["holder"]))
        result["released"] = True
    except FileNotFoundError:
        result["released"] = False
elif action == "break":
    sweep()
    broken = []
    for holder in request["holders"]:
        value = read(holder)
        if value is not None and value.get("state") == "active":
            value["state"] = "broken"
            value["broken_by"] = request["by"]
            value["broken_at"] = now
            write(value)
            broken.append(holder)
    result["broken"] = broken
else:
    raise SystemExit(2)
print(json.dumps(result, sort_keys=True))
'''


def remote_command(request: dict) -> str:
    payload = base64.b64encode(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).decode()
    return (f"python3 -c {shlex.quote(PROGRAM)} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(payload)}")


class PinStore:
    """Pins on one remote, reached through an injected ``ssh_run``."""

    def __init__(self, entry: dict, ssh_run: Callable | None = None):
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        self.entry = entry
        self._ssh_run = ssh_run

    def _call(self, request: dict) -> dict:
        try:
            result = self._ssh_run(self.entry, remote_command(request), timeout=TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - transport detail is never forwarded
            raise PinError(PINS_UNAVAILABLE, "remote pins are unreachable") from exc
        stdout = getattr(result, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if getattr(result, "returncode", 1) != 0 or not isinstance(stdout, str):
            raise PinError(PINS_UNAVAILABLE, "remote pin program failed")
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise PinError(PINS_UNAVAILABLE, "remote pin program output is invalid") from None
        if not isinstance(value, dict) or value.get("ok") is not True \
                or value.get("action") != request["action"]:
            raise PinError(PINS_UNAVAILABLE, "remote pin program output is invalid")
        return value

    def list(self) -> list[Pin]:
        raw = self._call({"action": "list"}).get("pins")
        if not isinstance(raw, list):
            raise PinError(PINS_UNAVAILABLE, "remote pin list is invalid")
        pins = [pin for pin in (parse_pin(item) for item in raw[:MAX_LISTED]) if pin]
        return sorted(pins, key=lambda pin: pin.holder)

    def get(self, holder: str) -> Pin | None:
        return next((pin for pin in self.list() if pin.holder == holder), None)

    def register(self, pin: Pin) -> Pin:
        if parse_pin(pin.stored()) != pin:
            raise PinError(PIN_UNVERIFIABLE, "pin is invalid")
        stored = parse_pin(self._call({"action": "register", "pin": pin.stored()}).get("pin"))
        if stored != pin:
            raise PinError(PIN_UNVERIFIABLE, "remote did not store the pin")
        return stored

    def release(self, holder: str) -> bool:
        if not HOLDER_RE.fullmatch(holder or ""):
            raise PinError("pin_holder_invalid", "holder is invalid")
        return bool(self._call({"action": "release", "holder": holder}).get("released"))

    def break_pins(self, holders: list[str], by: str) -> list[str]:
        if not HOLDER_RE.fullmatch(by or "") or not all(
                HOLDER_RE.fullmatch(h or "") for h in holders):
            raise PinError("pin_holder_invalid", "holder is invalid")
        broken = self._call({"action": "break", "holders": list(holders), "by": by}).get("broken")
        return [h for h in broken if h in holders] if isinstance(broken, list) else []


def local_holder(checkout: str | os.PathLike | None = None) -> tuple[str, str, str]:
    """``(holder, checkout_path, home_digest)`` for this controller checkout."""
    from sandbox.core._paths import BASE, ROOT
    checkout = os.path.realpath(checkout or ROOT)
    home = os.path.realpath(BASE)
    return holder_identity(home, checkout), checkout, _digest16(home)


def new_pin(revision: str, purpose: str, *, checkout=None, previous: Pin | None = None,
            ttl_seconds: int = DEFAULT_TTL_SECONDS, now: float | None = None) -> Pin:
    holder, checkout_path, home_digest = local_holder(checkout)
    moment = int(time.time() if now is None else now)
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    registered = previous.registered_at if previous and previous.binding(moment) \
        and previous.revision == revision else moment
    return Pin(holder, checkout_path, home_digest, revision, purpose[:MAX_PURPOSE],
               registered, moment, moment + ttl)


def strict_gate(entry: dict, status: dict, *, purpose: str, store: PinStore | None = None,
                checkout=None, now: float | None = None) -> dict:
    """Apply strict mode to a status envelope before a remote effect.

    The returned envelope's ``compatibility`` is refused unless the installed
    revision is exactly this controller's and the holder's pin was registered
    or renewed. A refused strict invocation registers nothing; a broken pin is
    reported with who broke it and when; an unverifiable pin fails closed
    (FR-008..FR-011).
    """
    status = dict(status) if isinstance(status, dict) else {}
    verdict = dict(status.get("compatibility") or {})
    verdict["strict"] = True
    local = status.get("local_runtime_revision")
    installed = status.get("installed_runtime_revision")

    def refuse(reason: str, **extra) -> dict:
        verdict.update(ok=False, reason=reason, **extra)
        status["compatibility"] = verdict
        return status

    store = store or PinStore(entry)
    holder = local_holder(checkout)[0]
    try:
        previous = store.get(holder)
    except PinError:
        previous, reachable = None, False
    else:
        reachable = True
    broken = ({"broken_pin": {"holder": previous.holder, "broken_by": previous.broken_by,
                              "broken_at": previous.broken_at}}
              if previous is not None and previous.state == BROKEN else {})
    if (not isinstance(local, str) or not REVISION_RE.fullmatch(local)
            or local != installed):
        return refuse("strict_requires_exact_revision", **broken)
    if not reachable:
        return refuse(PIN_UNVERIFIABLE, state="unknown")
    try:
        pin = store.register(new_pin(local, purpose, checkout=checkout,
                                     previous=previous, now=now))
    except PinError:
        return refuse(PIN_UNVERIFIABLE, state="unknown")
    verdict.update(ok=True, reason="strict_pin_held",
                   pin={"holder": pin.holder, "expires_at": pin.expires_at})
    status["compatibility"] = verdict
    return status
