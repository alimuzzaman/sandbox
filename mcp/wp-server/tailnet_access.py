"""Record which Tailscale user made each control request.

Attribution only: the bearer token decides access. Nothing here can refuse or
fail a request, and no token, header, body or tool argument is ever written.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

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
        self.lock = threading.Lock()

    def lookup(self, address) -> dict:
        if not is_tailnet_address(address):
            return _unknown("invalid_address")
        key = str(ipaddress.ip_address(address))
        with self.lock:
            now = self.clock()
            cached = self.entries.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]
            result = self._resolve(key)
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


class AccessLog:
    """Append-only private JSON lines with size-bounded rotation."""

    def __init__(self, path, max_bytes=10 * 1024 * 1024, backups=3):
        self.path, self.max_bytes, self.backups = Path(path), max_bytes, backups
        self.lock = threading.Lock()

    def write(self, record: dict) -> None:
        try:
            line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._rotate_for(len(line))
                descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
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
    def __init__(self, log: AccessLog, whois: Whois):
        self.log, self.whois = log, whois

    async def observe(self, request, call_next):
        """Serve the request, then record who made it."""
        tool = await self._tool(request)
        response = await call_next(request)
        try:
            client = request.client.host if request.client else None
            await asyncio.to_thread(self._record, client, request.method, request.url.path,
                                    response.status_code, tool)
        except Exception:  # Attribution must never fail the request it describes.
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

    def _record(self, client, method, path, status, tool) -> None:
        identity = self.whois.lookup(client)
        record = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "login": identity["login"], "node": identity["node"],
                  "method": _clean(method), "path": _clean(path), "status": status}
        if identity["reason"]:
            record["reason"] = identity["reason"]
        if tool:
            record["tool"] = tool
        self.log.write(record)
