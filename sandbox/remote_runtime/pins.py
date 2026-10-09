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
MAX_LISTED = 64  # also the remote capacity: registration past it is refused
INSTALL_LOCK_WAIT_SECONDS = 20
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
REPLACEMENT_IN_PROGRESS = "runtime_replacement_in_progress"
PINS_UNACKNOWLEDGED = "remote_runtime_pins_unacknowledged"
PIN_CAPACITY_REACHED = "pin_capacity_reached"


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
#
# Runtime replacement (FR-015) is serialized by that same flock: the install
# command runs under INSTALL_GATE, which takes the lock, refuses over any
# binding pin for another revision, and holds the lock until the install
# process itself exits. Every pin program, including the ones older
# controllers send, takes this lock, so no pin can be registered between the
# check and the end of the install, two installs never overlap, and a lost
# SSH connection cannot release it early. ``.replacing.json`` is the fence an
# earlier revision of this program wrote; a live one is still honoured.
PROGRAM = r'''
import base64, fcntl, json, os, re, sys, time
home = sys.argv[1]
request = json.loads(base64.b64decode(sys.argv[2]).decode())
HOLDER = re.compile(r"h-[0-9a-f]{16}")
CAPACITY = 64
FENCE = ".replacing.json"
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

def put(target, value):
    temp = target + ".tmp"
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
    os.replace(temp, target)

def write(value):
    put(path(value.get("holder")), value)

def fence():
    try:
        with open(os.path.join(root, FENCE)) as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or type(value.get("expires_at")) is not int \
            or value["expires_at"] <= now:
        return None
    return value

def binding(value):
    expires = value.get("expires_at")
    return value.get("state") == "active" and type(expires) is int and expires > now

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
    result["pins"] = every()
elif action == "register":
    sweep()
    pin = request["pin"]
    live = fence()
    if live is not None and live.get("target") != pin.get("revision"):
        result["refused"] = "runtime_replacement_in_progress"
    elif read(pin.get("holder")) is None and len(every()) >= CAPACITY:
        result["refused"] = "pin_capacity_reached"
    else:
        write(pin)
        result["pin"] = read(pin["holder"])
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


# Wraps one runtime install command (``sh -c``) on the remote. Exit 45: the
# lock is held by another install (or a live legacy fence names another
# target); exit 46: binding pins for another revision, listed as one JSON line
# on stdout. Otherwise the child's status. The child does not inherit the lock
# descriptor, so a process it leaves running never holds the lock; SIGHUP is
# ignored so a dropped connection neither kills the install halfway nor
# releases the lock while it runs.
INSTALL_GATE = r'''
import base64, fcntl, json, os, re, signal, subprocess, sys, time
home, target = sys.argv[1], sys.argv[2]
acknowledged = set(json.loads(base64.b64decode(sys.argv[3]).decode()))
wait, inner = float(sys.argv[4]), sys.argv[5]
HOLDER = re.compile(r"h-[0-9a-f]{16}")
root = os.path.join(home, "runtime", "remote-pins")
os.makedirs(root, mode=0o700, exist_ok=True)
os.chmod(root, 0o700)
lock = os.open(os.path.join(root, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
deadline = time.monotonic() + wait
while True:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= deadline:
            raise SystemExit(45)
        time.sleep(0.2)
now = int(time.time())
try:
    with open(os.path.join(root, ".replacing.json")) as handle:
        fence = json.load(handle)
except (OSError, ValueError):
    fence = None
if isinstance(fence, dict) and type(fence.get("expires_at")) is int \
        and fence["expires_at"] > now and fence.get("target") != target:
    raise SystemExit(45)
blocking = []
for name in sorted(os.listdir(root)):
    if not (name.endswith(".json") and HOLDER.fullmatch(name[:-5])):
        continue
    try:
        with open(os.path.join(root, name)) as handle:
            pin = json.load(handle)
    except (OSError, ValueError):
        continue
    expires = pin.get("expires_at") if isinstance(pin, dict) else None
    if (isinstance(pin, dict) and pin.get("state") == "active" and type(expires) is int
            and expires > now and pin.get("revision") != target
            and name[:-5] not in acknowledged):
        blocking.append(name[:-5])
if blocking:
    print(json.dumps({"pin_gate": "blocked", "blocking": blocking}))
    raise SystemExit(46)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
raise SystemExit(subprocess.run(["sh", "-c", inner], close_fds=True).returncode)
'''
INSTALL_BUSY_EXIT = 45
INSTALL_BLOCKED_EXIT = 46


def install_gate_command(target: str, acknowledged, inner: str, *,
                         wait_seconds: float = INSTALL_LOCK_WAIT_SECONDS) -> str:
    """``inner`` wrapped so it runs only under the remote pin lock (FR-015)."""
    if not REVISION_RE.fullmatch(target or "") \
            or not all(HOLDER_RE.fullmatch(h or "") for h in acknowledged or ()):
        raise PinError("pin_holder_invalid", "install gate request is invalid")
    acknowledged_b64 = base64.b64encode(
        json.dumps(sorted(set(acknowledged or ()))).encode()).decode()
    return (f"python3 -c {shlex.quote(INSTALL_GATE)} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(target)} "
            f"{shlex.quote(acknowledged_b64)} {float(wait_seconds)!r} {shlex.quote(inner)}")


def install_gate_refusal(returncode: int, stdout) -> PinError | None:
    """The typed refusal for a gated install's exit status, else None."""
    if returncode == INSTALL_BUSY_EXIT:
        return PinError(REPLACEMENT_IN_PROGRESS,
                        "another install holds the remote runtime replacement lock")
    if returncode != INSTALL_BLOCKED_EXIT:
        return None
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    blocking = []
    for line in (stdout or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("pin_gate") == "blocked":
            blocking = [h for h in value.get("blocking") or ()
                        if isinstance(h, str) and HOLDER_RE.fullmatch(h)]
    error = PinError(PINS_UNACKNOWLEDGED, "pass --break-pin HOLDER for: " + ", ".join(blocking))
    error.blocking = blocking
    return error


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
        if len(raw) > MAX_LISTED:
            # Never decide on a partial list: an omitted pin may be the binding one.
            raise PinError(PINS_UNAVAILABLE, "remote pin list exceeds capacity")
        pins = [pin for pin in (parse_pin(item) for item in raw) if pin]
        return sorted(pins, key=lambda pin: pin.holder)

    def get(self, holder: str) -> Pin | None:
        return next((pin for pin in self.list() if pin.holder == holder), None)

    def register(self, pin: Pin) -> Pin:
        if parse_pin(pin.stored()) != pin:
            raise PinError(PIN_UNVERIFIABLE, "pin is invalid")
        result = self._call({"action": "register", "pin": pin.stored()})
        if result.get("refused") in (REPLACEMENT_IN_PROGRESS, PIN_CAPACITY_REACHED):
            raise PinError(result["refused"], "remote refused the pin")
        stored = parse_pin(result.get("pin"))
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
                checkout=None, now: float | None = None,
                recheck: Callable[[], dict] | None = None) -> dict:
    """Apply strict mode to a status envelope before a remote effect.

    The returned envelope's ``compatibility`` is refused unless the installed
    revision is exactly this controller's and the holder's pin was registered
    or renewed. A refused strict invocation registers nothing; a broken pin is
    reported with who broke it and when; an unverifiable pin fails closed
    (FR-008..FR-011).

    ``status`` was read before the pin existed. ``recheck`` re-reads it after
    registration: a runtime replaced in between (the fence was already gone)
    shows a different installed revision, and the new pin is released.
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
    if recheck is not None:
        try:
            after = recheck()
        except Exception:  # noqa: BLE001 - transport detail is never forwarded
            after = None
        installed_after = after.get("installed_runtime_revision") \
            if isinstance(after, dict) else None
        if installed_after != local:
            try:
                store.release(pin.holder)
            except PinError:
                pass  # it expires; it names this revision, which is no longer installed
            if installed_after is None:
                return refuse(PIN_UNVERIFIABLE, state="unknown")
            status["installed_runtime_revision"] = installed_after
            status["runtime_revision_state"] = "mismatch"
            return refuse("strict_requires_exact_revision")
    verdict.update(ok=True, reason="strict_pin_held",
                   pin={"holder": pin.holder, "expires_at": pin.expires_at})
    status["compatibility"] = verdict
    return status
