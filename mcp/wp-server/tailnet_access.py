"""Who may call the control service, and a record of who did.

Access is the bearer token, or, unless the owner turned it off, any node that
Tailscale resolves on this tailnet (`ControlGate`, `MemberAccess`).
Attribution never refuses or fails a request, and no token, header, body or
tool argument is ever written.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import queue
import re
import stat
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary

from sandbox.hosting.shared_credentials.authentication import (
    AuthenticatedControlReceipt, _ControlReceiptIssuer,
)

_TAILNET_RANGES = (ipaddress.ip_network("100.64.0.0/10"),
                   ipaddress.ip_network("fd7a:115c:a1e0::/48"))
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_MAX_FIELD = 256
_MAX_TOOL_BODY = 1024 * 1024
_WHOIS_TIMEOUT_SECONDS = 2


def is_tailnet_address(value) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address.version == net.version and address in net for net in _TAILNET_RANGES)


def build_attribution(bind: str, home) -> Attribution | None:
    """Only a tailnet bind sees real peers; behind a proxy the client is the proxy."""
    if not is_tailnet_address(bind):
        return None
    return Attribution(AccessLog(Path(home) / "runtime" / "logs" / "control-access.jsonl"), Whois())


def tool_from_body(body: bytes) -> str | None:
    """The name of an MCP tool call. Arguments are never returned."""
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(message, dict) or message.get("method") != "tools/call":
        return None
    params = message.get("params")
    name = params.get("name") if isinstance(params, dict) else None
    return name if isinstance(name, str) and _TOOL_NAME.fullmatch(name) else None


def _clean(value) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if char.isprintable())[:_MAX_FIELD]


def _unknown(reason: str) -> dict:
    return {"login": "unknown", "node": None, "reason": reason}


class Whois:
    """Resolve a tailnet address to its user, caching so a burst spawns one process."""

    def __init__(self, run=subprocess.run, clock=time.monotonic, ttl_seconds=60, max_entries=1024):
        self.run, self.clock, self.ttl_seconds, self.max_entries = run, clock, ttl_seconds, max_entries
        self.entries: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()  # The log worker and request threads share the cache.

    def lookup(self, address) -> dict:
        if not is_tailnet_address(address):
            return _unknown("invalid_address")
        key = str(ipaddress.ip_address(address))
        now = self.clock()
        with self.lock:
            cached = self.entries.get(key)
        if cached is not None and now < cached[0]:
            return cached[1]
        result = self._resolve(key)
        with self.lock:
            if key not in self.entries and len(self.entries) >= self.max_entries:
                self.entries.pop(next(iter(self.entries)))
            self.entries[key] = (now + self.ttl_seconds, result)
        return result

    def _resolve(self, address: str) -> dict:
        try:
            completed = self.run(["tailscale", "whois", "--json", address], capture_output=True,
                                 text=True, timeout=_WHOIS_TIMEOUT_SECONDS,
                                 stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return _unknown("timeout")
        except FileNotFoundError:
            return _unknown("not_installed")
        except (OSError, ValueError):
            return _unknown("whois_failed")
        if completed.returncode != 0:
            return _unknown("whois_failed")
        try:
            document = json.loads(completed.stdout)
            login = _clean(document["UserProfile"]["LoginName"])
            node = _clean((document.get("Node") or {}).get("ComputedName")) or None
        except (ValueError, KeyError, TypeError, AttributeError):
            return _unknown("bad_response")
        return {"login": login, "node": node, "reason": None} if login else _unknown("bad_response")


class MemberAccess:
    """The owner's switch for tailnet member access, in `runtime/control-access.json`.

    On by default: a missing file means members are admitted. `sb remote
    tailnet-access` writes it over SSH, and it is re-read whenever it changes,
    so no restart is needed. A file that is malformed, a symlink, someone
    else's, or writable by others means off.
    """

    def __init__(self, home):
        self.path = Path(home) / "runtime" / "control-access.json"
        self.lock = threading.Lock()
        self.seen, self.value = None, False

    def enabled(self) -> bool:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o022):
                return False
            key = (info.st_ino, info.st_mtime_ns, info.st_size)
            with self.lock:
                if key == self.seen:
                    return self.value
            data = os.read(descriptor, 4096)
        except OSError:
            return False
        finally:
            os.close(descriptor)
        try:
            document = json.loads(data)
            value = isinstance(document, dict) and document.get("tailnet_members") is True
        except (ValueError, UnicodeDecodeError):
            value = False
        with self.lock:
            self.seen, self.value = key, value
        return value


class ControlGate:
    """Admit the bearer token, or a tailnet node Tailscale resolves unless members are turned off."""

    def __init__(self, token: str, members: MemberAccess | None, whois: Whois | None,
                 *, service_principal: str = "sandbox-control-service"):
        if (not isinstance(service_principal, str) or not service_principal
                or not all(c.isprintable() for c in service_principal)
                or len(service_principal.encode("utf-8")) > 128):
            raise ValueError("invalid control service principal")
        self.expected = f"Bearer {token}".encode()
        self.members, self.whois = members, whois
        # Trusted server configuration, independent of bearer content and clients.
        self._service_principal = service_principal
        self._policy_epoch = uuid4().hex
        self._receipt_issuer = _ControlReceiptIssuer(self._receipt_current)
        self._receipt_peers = WeakKeyDictionary()
        self._receipt_lock = threading.Lock()

    async def admit_request(self, authorization, peer) -> tuple[dict | None, AuthenticatedControlReceipt | None]:
        """Issue provenance only after this gate's actual admission decision.

        Existing callers retain the dictionary-returning admit API. Attribution
        logging is independent and cannot mint an authentication receipt.
        """
        admission = await self.admit(authorization, peer)
        if admission is None:
            return None, None
        if admission["via"] == "token":
            return admission, self._receipt_issuer._issue(
                via="token", policy_epoch=self._policy_epoch,
                service_principal=self._service_principal,
            )
        # Existing admission allows a resolved login without a node. Preserve
        # that decision for existing routes, but it cannot authorize credentials.
        if not admission.get("login") or not admission.get("node"):
            return admission, None
        receipt = self._receipt_issuer._issue(
            via="tailnet", policy_epoch=self._policy_epoch,
            login=admission["login"], node=admission["node"],
        )
        # Peer is server-observed, retained privately for current whois checks.
        with self._receipt_lock:
            self._receipt_peers[receipt] = peer
        return admission, receipt

    def _receipt_current(self, receipt: AuthenticatedControlReceipt) -> bool:
        """Synchronous service-worker check; no event loop bridging or bearer copy."""
        if (receipt._issuer is not self._receipt_issuer
                or receipt.policy_epoch != self._policy_epoch):
            return False
        if receipt.via == "token":
            return receipt.service_principal == self._service_principal
        if self.members is None or self.whois is None or not self.members.enabled():
            return False
        with self._receipt_lock:
            peer = self._receipt_peers.get(receipt)
        if peer is None:
            return False
        identity = self.whois.lookup(peer)
        return (identity.get("reason") is None and identity.get("login") == receipt.login
                and identity.get("node") == receipt.node)

    async def admit(self, authorization, peer) -> dict | None:
        supplied = (authorization or "").encode("utf-8", "surrogateescape")
        if hmac.compare_digest(supplied, self.expected):
            return {"via": "token"}
        if (self.members is None or self.whois is None or not is_tailnet_address(peer)
                or not self.members.enabled()):
            return None
        identity = await asyncio.to_thread(self.whois.lookup, peer)
        if identity["reason"] is not None:
            return None
        return {"via": "tailnet", "login": identity["login"], "node": identity["node"]}


class AccessLog:
    """Append-only private JSON lines with size-bounded rotation."""

    def __init__(self, path, max_bytes=10 * 1024 * 1024, backups=3):
        self.path, self.max_bytes, self.backups = Path(path), max_bytes, backups

    def write(self, record: dict) -> None:
        try:
            line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._rotate_for(len(line))
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, line)
            finally:
                os.close(descriptor)
        except (OSError, TypeError, ValueError):
            return

    def _rotate_for(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size == 0 or size + incoming <= self.max_bytes:
            return
        for index in range(self.backups - 1, 0, -1):
            older = Path(f"{self.path}.{index}")
            if older.exists():
                os.replace(older, f"{self.path}.{index + 1}")
        os.replace(self.path, f"{self.path}.1")


class Attribution:
    """One worker resolves and writes, so requests never wait on a lookup."""

    def __init__(self, log: AccessLog, whois: Whois, max_queue=1024):
        self.log, self.whois = log, whois
        self.pending: queue.Queue = queue.Queue(maxsize=max_queue)
        threading.Thread(target=self._work, name="tailnet-attribution", daemon=True).start()

    def drain(self) -> None:
        self.pending.join()

    def _work(self) -> None:
        while True:
            item = self.pending.get()
            try:
                self._record(*item)
            except Exception:  # One bad record must not end the worker.
                pass
            finally:
                self.pending.task_done()

    async def observe(self, request, call_next):
        """Serve the request and queue a record of who made it."""
        tool = await self._tool(request)
        response = await call_next(request)
        try:
            client = request.client.host if request.client else None
            when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.pending.put_nowait((when, client, request.method, request.url.path,
                                     response.status_code, tool))
        except Exception:  # Includes a full queue: attribution never fails a request.
            pass
        return response

    @staticmethod
    async def _tool(request) -> str | None:
        length = request.headers.get("content-length", "")
        if (request.method != "POST" or request.url.path.rstrip("/") != "/mcp"
                or not length.isdigit() or int(length) > _MAX_TOOL_BODY):
            return None
        try:
            return tool_from_body(await request.body())
        except Exception:
            return None

    def _record(self, when, client, method, path, status, tool) -> None:
        identity = self.whois.lookup(client)
        record = {"time": when,
                  "login": identity["login"], "node": identity["node"],
                  "method": _clean(method), "path": _clean(path), "status": status}
        if identity["reason"]:
            record["reason"] = identity["reason"]
        if tool:
            record["tool"] = tool
        self.log.write(record)
