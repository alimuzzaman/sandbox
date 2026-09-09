"""Closed, side-effect-free public application route declarations."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import unquote, urlsplit


class DeliveryRouteError(ValueError):
    def __init__(self, code: str = "delivery_route_contract_invalid") -> None:
        self.code = code
        super().__init__(code)


def _object(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise DeliveryRouteError()
    return value


def _text(value, limit=256):
    if type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DeliveryRouteError()
    try:
        if len(value.encode("utf-8")) > limit:
            raise DeliveryRouteError()
    except UnicodeError:
        raise DeliveryRouteError() from None
    return value


def _public_text(value, limit=256):
    value = _text(value, limit)
    if re.search(r"(?i)(?:password|passwd|secret|token|authorization|cookie|api[-_]?key|credential)", value):
        raise DeliveryRouteError()
    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise DeliveryRouteError() from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DeliveryRouteError()
    return value


def _path(value):
    value = _public_text(value, 1024)
    parsed = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parsed.netloc or parsed.scheme or parsed.query or parsed.fragment or "?" in value or "#" in value or "\\" in value:
        raise DeliveryRouteError()
    decoded = unquote(value)
    _public_text(decoded, 1024)
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded or decoded.startswith("//"):
        raise DeliveryRouteError()
    if re.search(r"(?i)(?:wp-login|login|logout|oauth|callback|reset-password)", decoded):
        raise DeliveryRouteError()
    return value


def _field(value, header=False):
    value = _public_text(value)
    if header:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", value) is None or value.lower() in {"location", "www-authenticate", "proxy-authenticate", "server", "date", "content-type", "content-length", "connection", "cache-control", "etag", "last-modified", "content-encoding", "transfer-encoding", "vary", "via"}:
            raise DeliveryRouteError()
    elif not value.startswith("/") or re.search(r"~(?![01])", value):
        raise DeliveryRouteError()
    return value


def _flag(value):
    if type(value) is not bool:
        raise DeliveryRouteError()
    return value


def deadline_seconds(value):
    if type(value) is not int or not 10 <= value <= 300:
        raise DeliveryRouteError("delivery_route_timeout_invalid")
    return value


def normalize_delivery(raw=None):
    """Absent delivery remains optional during ordinary local configuration."""
    if raw is None:
        return None
    _object(raw, {"schemaVersion", "routes"}, {"schemaVersion", "routes"})
    if type(raw["schemaVersion"]) is not int or raw["schemaVersion"] != 1:
        raise DeliveryRouteError()
    routes = _object(raw["routes"], {"deadlineSeconds", "aliasPolicy", "checks", "releaseIdentity", "edgeProof"}, {"checks"})
    policy = routes.get("aliasPolicy", "serve_or_redirect_to_primary")
    if type(policy) is not str or policy not in {"serve_or_redirect_to_primary", "serve_only"}:
        raise DeliveryRouteError()
    checks = routes["checks"]
    if type(checks) is not list or not 1 <= len(checks) <= 8:
        raise DeliveryRouteError()
    normalized = []
    for check in checks:
        _object(check, {"path", "statuses", "markers"}, {"path", "statuses", "markers"})
        statuses = check["statuses"]
        if type(statuses) is not list or not 1 <= len(statuses) <= 8 or any(type(s) is not int or not 200 <= s <= 599 for s in statuses) or len(set(statuses)) != len(statuses):
            raise DeliveryRouteError()
        markers = check["markers"]
        if type(markers) is not list or not 1 <= len(markers) <= 4:
            raise DeliveryRouteError()
        normalized_markers = []
        for marker in markers:
            _object(marker, {"kind", "field", "expected"}, {"kind", "field", "expected"})
            kind = marker["kind"]
            if type(kind) is not str or kind not in {"header_equals", "json_pointer_equals", "json_array_contains"}:
                raise DeliveryRouteError()
            expected = marker["expected"]
            if type(expected) not in {str, int, float, bool, type(None)} or (type(expected) is float and not math.isfinite(expected)):
                raise DeliveryRouteError()
            if type(expected) is int and expected.bit_length() > 800:
                raise DeliveryRouteError()
            if type(expected) is str:
                _public_text(expected)
                if "$" in expected and expected not in {"$primary_origin", "$requested_origin"}:
                    raise DeliveryRouteError()
            if kind == "header_equals" and type(expected) is not str:
                raise DeliveryRouteError()
            normalized_markers.append({"kind": kind, "field": _field(marker["field"], kind == "header_equals"), "expected": expected})
        normalized.append({"path": _path(check["path"]), "statuses": sorted(statuses), "markers": normalized_markers})
    release = routes.get("releaseIdentity", {"required": False})
    _object(release, {"required", "path", "kind", "field", "expectedFrom"}, {"required"})
    release = dict(release)
    _flag(release["required"])
    if set(release) != {"required"} or release["required"]:
        _object(release, {"required", "path", "kind", "field", "expectedFrom"}, {"required", "path", "kind", "field", "expectedFrom"})
        if type(release["kind"]) is not str or type(release["expectedFrom"]) is not str or release["kind"] not in {"header", "json_pointer"} or release["expectedFrom"] not in {"application_commit", "artifact_digest"}:
            raise DeliveryRouteError()
        release["path"] = _path(release["path"])
        release["field"] = _field(release["field"], release["kind"] == "header")
    edge = _object(routes.get("edgeProof", {"required": False}), {"required"}, {"required"})
    result = {"schemaVersion": 1, "routes": {"deadlineSeconds": deadline_seconds(routes.get("deadlineSeconds", 120)), "aliasPolicy": policy, "checks": normalized, "releaseIdentity": release, "edgeProof": {"required": _flag(edge["required"])}}}
    try:
        if len(json.dumps(result, allow_nan=False, ensure_ascii=False).encode("utf-8")) > 32768:
            raise DeliveryRouteError()
    except (ValueError, UnicodeError, OverflowError):
        raise DeliveryRouteError() from None
    return result


def delivery_config_provider(result):
    if "delivery" in result and result["delivery"] is None:
        raise DeliveryRouteError()
    return normalize_delivery(result.get("delivery"))


def wordpress_delivery_contract():
    return normalize_delivery({"schemaVersion": 1, "routes": {"checks": [{"path": "/wp-json/", "statuses": [200], "markers": [{"kind": "json_pointer_equals", "field": "/url", "expected": "$primary_origin"}, {"kind": "json_array_contains", "field": "/namespaces", "expected": "wp/v2"}]}]}})
