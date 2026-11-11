"""Front-door-only refresh rules (spec 084).

The refresh re-renders one hosted target's front-door route with the current
renderer and reloads it, without touching Compose, containers, initializers, DNS
or certificates. This module holds the rules that decide whether a refresh may run
and what it records; ``sandbox.commands.hosting`` runs the remote steps through the
same edge adapters, verification and fail-closed restore an apply uses.

Everything here is pure and stdlib-only except the audit log, which appends one
JSON line per event to a controller file opened before the first remote change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FENCED = "front_door_refresh_fenced"
NO_ROUTE = "front_door_refresh_no_route"
ROUTE_UNREADABLE = "front_door_refresh_route_unreadable"
DECLARATION_CHANGED = "front_door_refresh_declaration_changed"
CERTIFICATE_MISSING = "front_door_refresh_certificate_missing"
PRIVACY_UNPROVEN = "front_door_refresh_privacy_unproven"
WOULD_PUBLISH = "front_door_refresh_would_publish"
AUTH_WEAKENED = "front_door_refresh_auth_weakened"
HTTPS_LOST = "front_door_refresh_https_lost"
NAMES_CHANGED = "front_door_refresh_names_changed"
UPSTREAM_CHANGED = "front_door_refresh_upstream_changed"
SECRETS_MISSING = "front_door_refresh_secrets_missing"
UNAVAILABLE = "front_door_refresh_unavailable"
AUDIT_UNAVAILABLE = "front_door_refresh_audit_unavailable"
FAILED = "front_door_refresh_failed"

Outcome = Literal["refreshed", "unchanged", "failed"]
Restore = Literal["restored", "removed", "restore_failed"]

# The record parts a declaration mismatch names.
PARTS = ("front_door", "route_name", "loopback_port", "records", "dns_coverage",
         "certificate", "declaration")

_ROUTE_TAG_RE = re.compile(r"X-Sandbox-Route\"?\s+\"?([0-9a-f]{32})\b")


class FrontDoorRefreshRefused(Exception):
    """A refusal with a stable code; raised before any remote change unless FAILED."""

    def __init__(self, code: str, message: str, *, part: str | None = None,
                 restore: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.part = part
        self.restore = restore

    def payload(self) -> dict:
        result = {"ok": False, "code": self.code, "error": self.message}
        if self.part is not None:
            result["part"] = self.part
        if self.restore is not None:
            result["restore"] = self.restore
        return result


@dataclass(frozen=True)
class Binding:
    """What the target's last successful delivery recorded for its front door."""

    loopback_port: int
    legacy: bool


def digest_text(text: str | None) -> str | None:
    if text is None:
        return None
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _canonical(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def declaration_digest(validated: dict) -> str:
    """Digest of the non-secret front-door declaration of a validated manifest."""
    auth = validated.get("basic_auth") or None
    coverage = validated.get("dns_coverage") or {}
    return _canonical({
        "schema": 1,
        "project": validated.get("project"),
        "environment": validated.get("environment"),
        "routes": validated.get("routes") or [],
        "proxied": bool((validated.get("cloudflare") or {}).get("proxied")),
        "basic_auth": ({key: value for key, value in auth.items() if key != "password"}
                       if isinstance(auth, dict) else None),
        "login_service": validated.get("login_service"),
        "indexing": validated.get("indexing", "noindex"),
        "robots": validated.get("robots"),
        "dns_coverage": {"mode": coverage.get("mode"), "zone": coverage.get("zone")},
        "healthcheck_path": (validated.get("healthcheck") or {}).get("path"),
    })


def fence_reason(record: dict) -> str | None:
    """Why another owner fences this target, or None (same rule as host apply)."""
    if record.get("active_operation") is not None:
        return "another host apply, recovery or destroy owns this target"
    if record.get("recovery_uncertainty") is not None:
        return "uncertain host recovery state fences this target"
    activation = record.get("image_activation")
    if isinstance(activation, dict) and activation.get("active") is not None:
        return "host image activation or recovery owns this target"
    return None


def _changed(part: str, detail: str) -> FrontDoorRefreshRefused:
    return FrontDoorRefreshRefused(
        DECLARATION_CHANGED,
        f"{detail}; the manifest no longer matches the target's last successful delivery. "
        "Run `sb host apply` (with a new revision when the target's source revision is "
        "not declared) instead of a front-door refresh",
        part=part)


def check_recorded_binding(record: dict, validated: dict, *, desired_records: list[dict],
                           front_door: str, route_name: str) -> Binding:
    """Bind the current manifest to fields only a successful delivery writes.

    A refused or failed apply rewrites ``hosting_operation``, ``hostnames`` and the
    revision fields before it stops, so those say what was attempted. These fields
    (``loopback_port``, ``caddy_name``, ``front_door``, ``records``, ``dns_coverage``,
    ``certificate``, ``front_door_declaration``) are written only after the edge of
    a delivery was verified.
    """
    port = record.get("loopback_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise FrontDoorRefreshRefused(
            NO_ROUTE, "the target has no successful delivery on record (no loopback port)")
    if record.get("caddy_name") != route_name:
        raise FrontDoorRefreshRefused(
            NO_ROUTE, "the target has no successful delivery of this route on record")
    if (record.get("front_door") or "caddy") != front_door:
        raise _changed("front_door", f"the target was delivered through the "
                       f"{record.get('front_door') or 'caddy'} front door, the remote now uses {front_door}")
    hostnames = sorted({str(route["hostname"]) for route in validated.get("routes") or []})
    coverage = validated.get("dns_coverage") or {}
    recorded_coverage = record.get("dns_coverage")
    if coverage.get("mode") == "shared":
        expected = {"mode": "shared", "zone": coverage.get("zone"),
                    "hostname": (validated.get("routes") or [{}])[0].get("hostname")}
        if recorded_coverage != expected:
            raise _changed("dns_coverage", "the shared DNS coverage differs from the delivered one")
    elif isinstance(recorded_coverage, dict):
        raise _changed("dns_coverage", "the target was delivered on shared DNS coverage")
    else:
        wanted = {}
        for item in desired_records:
            wanted[str(item.get("hostname"))] = item
        delivered = {}
        for row in record.get("records") or []:
            applied = row.get("applied") if isinstance(row, dict) else None
            if not isinstance(applied, dict) or not applied.get("name"):
                raise _changed("records", "a delivered DNS record has no applied snapshot to compare")
            delivered[str(applied["name"]).rstrip(".").lower()] = applied
        if set(wanted) != set(delivered):
            raise _changed("records", "the declared DNS names differ from the delivered records")
        for hostname, item in wanted.items():
            applied = delivered[hostname]
            if bool(applied.get("proxied")) != bool(item.get("proxied")):
                raise _changed("records", f"{hostname} changes its Cloudflare proxy setting")
            if applied.get("type") in {"A", "AAAA"} and applied.get("content") != item.get("address"):
                raise _changed("records", f"{hostname} points at a different origin address")
    certificate = record.get("certificate")
    if isinstance(certificate, dict):
        if certificate.get("pending"):
            raise FrontDoorRefreshRefused(
                CERTIFICATE_MISSING, "the delivered certificate was still pending issuance")
        names = certificate.get("hostnames")
        if names and not set(hostnames) <= set(str(name) for name in names):
            raise _changed("certificate", "the delivered certificate does not cover every declared name")
    recorded_digest = record.get("front_door_declaration")
    if recorded_digest is not None and recorded_digest != declaration_digest(validated):
        raise _changed("declaration", "the front-door declaration (routes, basic auth, share "
                       "block, indexing, robots, proxy, DNS coverage or health path) changed")
    return Binding(loopback_port=port, legacy=recorded_digest is None)


def route_tag_of(text: str | None) -> str | None:
    """The ``X-Sandbox-Route`` tag rendered into a route, if any."""
    match = _ROUTE_TAG_RE.search(text or "")
    return match.group(1) if match else None


def has_basic_auth(text: str | None) -> bool:
    """True when a route Sandbox rendered asks for Basic Auth (Caddy or nginx)."""
    if not text:
        return False
    return (re.search(r"^\s*basicauth\s*\{", text, re.M) is not None
            or re.search(r"\bmap \$http_authorization\b", text) is not None)


def served_names(text: str | None) -> frozenset[str]:
    """Hostnames a route serves: nginx ``server_name`` values, or Caddy site addresses."""
    if not text:
        return frozenset()
    names: set[str] = set()
    if "server {" in text:
        for match in re.finditer(r"(?:^|[\s;{}])server_name\s+([^;]*);", text):
            names.update(name for name in match.group(1).split() if name)
        return frozenset(names)
    for line in text.splitlines():
        if not line or line[0] in " \t#({}" or not line.rstrip().endswith("{"):
            continue
        for token in re.split(r"[,\s]+", line.rstrip()[:-1].strip()):
            if not token:
                continue
            token = re.sub(r"^[a-z]+://", "", token)
            names.add(token.rsplit(":", 1)[0] if re.search(r":\d+$", token) else token)
    return frozenset(names)


def upstream_ports(text: str | None) -> frozenset[int]:
    """Loopback ports a route proxies to (application and gate)."""
    return frozenset(int(port) for port in re.findall(r"127\.0\.0\.1:(\d{2,5})(?![0-9])", text or ""))


def check_no_weakening(previous: str, rendered: str, *, private: bool, marked_private,
                       https_before: bool, https_after: bool) -> None:
    """Refuse a rendered route that is weaker than, or serves other names than, the live one."""
    if private:
        if not marked_private(previous):
            raise FrontDoorRefreshRefused(
                PRIVACY_UNPROVEN,
                "the target is declared share.private but its installed route is not a "
                "Sandbox-marked private route; run a full `sb host apply` (with a new "
                "revision when the source revision is not declared)")
        if not marked_private(rendered):
            raise FrontDoorRefreshRefused(
                PRIVACY_UNPROVEN, "the rendered route is not a marked private route")
    elif marked_private(previous):
        raise FrontDoorRefreshRefused(
            WOULD_PUBLISH,
            "the installed route is a private route and the manifest is no longer "
            "share.private; a refresh never makes a private target public")
    if https_before and not https_after:
        raise FrontDoorRefreshRefused(
            HTTPS_LOST, "the rendered route would stop listening on HTTPS")
    if has_basic_auth(previous) and not has_basic_auth(rendered):
        raise FrontDoorRefreshRefused(
            AUTH_WEAKENED, "the installed route asks for Basic Auth and the rendered route would not")
    if upstream_ports(previous) != upstream_ports(rendered):
        raise FrontDoorRefreshRefused(
            UPSTREAM_CHANGED,
            "the rendered route would proxy to other loopback ports than the installed "
            f"route ({sorted(upstream_ports(previous))} -> {sorted(upstream_ports(rendered))})")
    before, after = served_names(previous), served_names(rendered)
    if before != after:
        raise FrontDoorRefreshRefused(
            NAMES_CHANGED,
            "the rendered route would serve different hostnames than the installed route "
            f"(added {sorted(after - before)}, removed {sorted(before - after)})")


AUDIT_FIELDS = ("at", "op", "controller", "remote", "target", "front_door", "outcome",
                "code", "route_before", "route_after", "restore")


def audit_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / "front-door-refresh" / "audit.jsonl"


class AuditLog:
    """Append-only audit, opened before the first remote change so a run is never unaudited."""

    def __init__(self, path: Path):
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._fd: int | None = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError as exc:
            raise FrontDoorRefreshRefused(
                AUDIT_UNAVAILABLE, f"cannot open the front-door refresh audit log: {exc.strerror}") from None
        self.path = path

    def write(self, **fields) -> None:
        if self._fd is None:
            raise ValueError("audit log is closed")
        record = {key: fields.get(key) for key in AUDIT_FIELDS}
        record["at"] = int(record["at"] if record["at"] is not None else time.time())
        os.write(self._fd, (json.dumps(record, sort_keys=True) + "\n").encode())

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
