"""Read-only Mailpit access from this server's managed tailnet-name metadata.

A bounded local read-only helper projects root-owned route metadata.
No network, controller configuration or private-key contents are read.
A loaded journal describes retained activation evidence, not a live health check.
"""
from __future__ import annotations

import json
import re
import tempfile
from urllib.parse import urlsplit
from typing import Literal, TypedDict

from sandbox.hosting.tailnet_names.models import TailnetNameError, hostname, tailnet_address


class ReadyMailAccess(TypedDict):
    status: Literal["ready"]
    url: str
    upstream: str


class UnavailableMailAccess(TypedDict):
    status: Literal["unavailable"]
    reason: Literal["no_port", "not_same_server", "inventory_unavailable", "inventory_partial",
                    "not_found", "not_ready", "ambiguous"]
    upstream: str | None


MailAccess = ReadyMailAccess | UnavailableMailAccess


class NameAccess(TypedDict):
    upstream: str
    url: str | None


class Inventory(TypedDict):
    available: bool
    partial: bool
    names: list[NameAccess]


MAX_NAMES = 1000


def read_server(*, upstreams: list[str], bound_host: str | None = None,
                now: float | None = None) -> Inventory:
    """Project root-held route facts through the existing local helper, never SSH."""
    result: Inventory = {"available": False, "partial": False, "names": []}
    try:
        if not isinstance(upstreams, list) or len(upstreams) > MAX_NAMES:
            return result
        requested: list[str] = []
        for upstream in upstreams:
            match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", upstream) if isinstance(upstream, str) else None
            if not match or not 1 <= int(match[1]) <= 65535:
                return result
            if upstream not in requested:
                requested.append(upstream)
        if not requested:
            return {"available": True, "partial": False, "names": []}
        bound_ip = tailnet_address(bound_host)
        from sandbox.hosting.tailnet_names import remote_program
        from sandbox.services.process import BoundedProcessRunner

        # Only fixed read-only action metadata enters this local privileged helper.
        # Existing helper source is embedded as in the managed-name adapter; it
        # does not import mutable owner modules in the root interpreter.
        with tempfile.TemporaryFile() as payload:
            payload.write(json.dumps({"action": "mail-access", "tailnet_ip": bound_ip,
                                      "upstreams": requested}).encode())
            payload.seek(0)
            observed = BoundedProcessRunner(max_output=524288, terminate_on_output_limit=True).run(
                ["sudo", "-n", "python3", "-c", remote_program.program_source()],
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
                stdin=payload.fileno(), timeout=12, json_output=True)
        if (observed.returncode != 0 or observed.termination_reason
                or observed.stdout_truncated or observed.stderr_truncated):
            return result
        data = json.loads(observed.stdout)
        if (not isinstance(data, dict) or data.get("ok") is not True
                or type(data.get("available")) is not bool
                or type(data.get("partial")) is not bool
                or not isinstance(data.get("names"), list) or len(data["names"]) > MAX_NAMES):
            return result
        rows: list[NameAccess] = []
        for row in data["names"]:
            if not isinstance(row, dict) or set(row) != {"upstream", "url"}:
                return result
            upstream = row["upstream"]
            match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", upstream) if isinstance(upstream, str) else None
            if not match or not 1 <= int(match[1]) <= 65535:
                return result
            url = row["url"]
            if url is not None:
                if not isinstance(url, str):
                    return result
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not hostname(parsed.netloc)
                        or parsed.path != "/mail/" or parsed.query or parsed.fragment):
                    return result
            rows.append({"upstream": upstream, "url": url})
        return {"available": data["available"], "partial": data["partial"], "names": rows}
    except (OSError, ValueError, TypeError, TailnetNameError):
        return result


def access(port: int | None, *, same_server: bool, inventory: Inventory | None) -> MailAccess:
    upstream = f"127.0.0.1:{port}" if port else None
    reason: UnavailableMailAccess["reason"]
    if not port:
        reason = "no_port"
    elif not same_server:
        reason = "not_same_server"
    elif inventory is None or not inventory["available"]:
        reason = "inventory_unavailable"
    elif inventory["partial"]:
        reason = "inventory_partial"
    else:
        matches = [row for row in inventory["names"] if row["upstream"] == upstream]
        if len(matches) > 1:
            reason = "ambiguous"
        elif not matches:
            reason = "not_found"
        else:
            url = matches[0]["url"]
            if url is None:
                reason = "not_ready"
            else:
                return {"status": "ready", "url": url, "upstream": f"127.0.0.1:{port}"}
    return {"status": "unavailable", "reason": reason, "upstream": upstream}
