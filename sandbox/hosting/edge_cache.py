"""Closed policy and provider boundary for declared edge-cache purges."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sandbox.core import _cloudflare as cloudflare

MAX_ZONES = 32
MAX_ROUTES = 64
MAX_RECEIPT_BYTES = 64 * 1024
_HOST = re.compile(r"(?:\*\.)?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_ZONE = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class EdgeCacheError(ValueError):
    """Stable refusal; provider details never escape the boundary."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _hostname(value: object) -> str:
    if not isinstance(value, str):
        raise EdgeCacheError("route_invalid")
    text = value.strip().rstrip(".").lower()
    if not _HOST.fullmatch(text) or ".." in text or len(text) > 253:
        raise EdgeCacheError("route_invalid")
    return text


def _zone_name(value: object) -> str:
    if not isinstance(value, str):
        raise EdgeCacheError("zone_invalid")
    text = value.strip().rstrip(".").lower()
    if not _ZONE.fullmatch(text) or ".." in text or len(text) > 253:
        raise EdgeCacheError("zone_invalid")
    return text


def _route_hostnames(routes: object) -> tuple[str, ...]:
    if not isinstance(routes, list) or not routes or len(routes) > MAX_ROUTES:
        raise EdgeCacheError("route_invalid")
    values = []
    for route in routes:
        if not isinstance(route, dict):
            raise EdgeCacheError("route_invalid")
        values.append(_hostname(route.get("hostname")))
    result = tuple(sorted(set(values)))
    if not result:
        raise EdgeCacheError("route_invalid")
    return result


def validate_policy(raw: object, *, routes: object, proxied: object) -> dict:
    """Return the closed, canonical cache-purge policy for one environment."""
    hostnames = _route_hostnames(routes)
    if raw is None:
        return {"enabled": False, "on_deploy": False, "provider": "cloudflare", "scope": None,
                "zones": [], "routes": list(hostnames),
                "policy_digest": _digest({"enabled": False, "on_deploy": False, "provider": "cloudflare",
                                            "scope": None, "zones": [], "routes": list(hostnames)})}
    if not isinstance(raw, dict):
        raise EdgeCacheError("policy_invalid")
    allowed = {"on_deploy", "scope", "zones"}
    if set(raw) - allowed:
        raise EdgeCacheError("policy_invalid")
    enabled = raw.get("on_deploy")
    if not isinstance(enabled, bool):
        raise EdgeCacheError("policy_invalid")
    if not enabled:
        if set(raw) != {"on_deploy"}:
            raise EdgeCacheError("policy_invalid")
        body = {"enabled": False, "on_deploy": False, "provider": "cloudflare", "scope": None,
                "zones": [], "routes": list(hostnames)}
        return {**body, "policy_digest": _digest(body)}
    if proxied is not True or raw.get("scope") != "zone_all":
        raise EdgeCacheError("policy_invalid")
    zones_raw = raw.get("zones")
    if not isinstance(zones_raw, list) or not 1 <= len(zones_raw) <= MAX_ZONES:
        raise EdgeCacheError("policy_invalid")
    zones = tuple(sorted({_zone_name(item) for item in zones_raw}))
    if len(zones) != len(zones_raw):
        raise EdgeCacheError("policy_invalid")
    for hostname in hostnames:
        bare = hostname.removeprefix("*.")
        if not any(bare == zone or bare.endswith("." + zone) for zone in zones):
            raise EdgeCacheError("policy_route_mismatch")
    body = {"enabled": True, "on_deploy": True, "provider": "cloudflare", "scope": "zone_all",
            "zones": list(zones), "routes": list(hostnames)}
    return {**body, "policy_digest": _digest(body)}


def build_purge_plan(*, policy: dict, project: str, environment: str,
                     request_id: str, deployment_revision: str | None = None,
                     generation_subject_digest: str | None = None,
                     activation_request_digest: str | None = None) -> dict:
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        raise EdgeCacheError("provider_not_configured")
    if (not isinstance(project, str) or not project or not isinstance(environment, str)
            or not environment or not isinstance(request_id, str)
            or not _ID.fullmatch(request_id)):
        raise EdgeCacheError("request_invalid")
    if deployment_revision is not None and (
            not isinstance(deployment_revision, str) or not deployment_revision):
        raise EdgeCacheError("request_invalid")
    if generation_subject_digest is not None and not _DIGEST.fullmatch(generation_subject_digest):
        raise EdgeCacheError("request_invalid")
    if activation_request_digest is not None and not _DIGEST.fullmatch(activation_request_digest):
        raise EdgeCacheError("request_invalid")
    zones = policy.get("zones")
    routes = policy.get("routes")
    if (policy.get("provider") != "cloudflare" or policy.get("scope") != "zone_all"
            or not isinstance(zones, list) or not isinstance(routes, list)
            or not _DIGEST.fullmatch(str(policy.get("policy_digest")))):
        raise EdgeCacheError("policy_invalid")
    identity = {"schema_version": 1, "project": project, "environment": environment,
                "request_id": request_id, "deployment_revision": deployment_revision,
                "generation_subject_digest": generation_subject_digest,
                "activation_request_digest": activation_request_digest,
                "policy_digest": policy["policy_digest"], "routes": tuple(routes),
                "zones": tuple(zones), "scope": "zone_all"}
    return {**identity, "request_digest": _digest(identity)}


def resolve_zones(client: Any, plan: dict) -> tuple[dict, ...]:
    """Resolve every exact allowlisted zone before issuing any purge."""
    resolved = []
    for requested in plan["zones"]:
        try:
            zone = client.zone(requested)
        except cloudflare.CloudflareError as exc:
            code = getattr(exc, "code", None) or "zone_unavailable"
            raise EdgeCacheError(code) from None
        if (not isinstance(zone, dict) or not isinstance(zone.get("id"), str)
                or not _ID.fullmatch(zone["id"]) or not isinstance(zone.get("name"), str)):
            raise EdgeCacheError("provider_response_invalid")
        if _zone_name(zone["name"]) != requested:
            raise EdgeCacheError("zone_mismatch")
        resolved.append({"id": zone["id"], "name": requested})
    return tuple(resolved)


def purge_plan(*, client: Any, plan: dict, before_zone: Any = None,
               after_zone: Any = None, prepare_zones: Any = None) -> dict:
    """Execute one prepared plan, returning only closed per-zone receipts."""
    zones = resolve_zones(client, plan)
    if prepare_zones is not None:
        prepare_zones(zones)
    purges = []
    for zone in zones:
        if before_zone is not None:
            before_zone(zone)
        try:
            result = client.purge_cache(zone["id"])
        except cloudflare.CloudflareError as exc:
            code = getattr(exc, "code", None) or "provider_failed"
            if code in {"provider_timeout", "provider_acceptance_unknown"}:
                code = "acceptance_unknown"
            raise EdgeCacheError(code) from None
        if (not isinstance(result, dict) or not _ID.fullmatch(str(result.get("id") or ""))):
            raise EdgeCacheError("provider_response_invalid")
        purges.append({"zone_id": zone["id"], "zone": zone["name"],
                       "state": "acknowledged", "provider_id": result["id"]})
        if after_zone is not None:
            after_zone(purges[-1])
    receipt = {"schema_version": 1, "status": "complete", "provider": "cloudflare",
               "scope": "zone_all", "project": plan["project"],
               "environment": plan["environment"], "request_id": plan["request_id"],
               "request_digest": plan["request_digest"],
               "activation_request_digest": plan.get("activation_request_digest"),
               "policy_digest": plan["policy_digest"], "routes": list(plan["routes"]),
               "zones": purges}
    receipt["receipt_digest"] = _digest(receipt)
    if len(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
        raise EdgeCacheError("receipt_too_large")
    return receipt


__all__ = ["EdgeCacheError", "build_purge_plan", "purge_plan", "resolve_zones",
           "validate_policy"]
