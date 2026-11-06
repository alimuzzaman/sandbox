"""Target decision and public edge verification for wildcard subdomain hosting.

Implements Stage 2 of Feature 070:
- decide(): resolves whether a target deployment uses exact DNS records or shared
  zone coverage, enforcing the decision table from research R6.
- check_exact_record(): applies the drift rule for exact records in covered zones.
- pinned_edge_handler(): creates a urllib HTTPSHandler pinned to the wildcard IP.
- resolver_state(): inspects DNS resolver reachability for a target name.
- verify_shared(): validates shared public edge reachability and records evidence.
- bind_pending(), bind_bound(), release(): thin store dependent management.
"""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import secrets
import socket
import ssl
from typing import Any, Callable
import urllib.parse
import urllib.request

from sandbox.core import _cloudflare as cloudflare
from sandbox.core import _hosting as hosting
from sandbox.hosting.front_door import ZONE_COVERAGE, declaration, front_door_mode
from sandbox.hosting.front_door.nginx import coverage_zone_of as _nginx_coverage_zone_of
from sandbox.hosting.zone_coverage import (
    CoverageError,
    COVERAGE_DISABLED_DEPENDENT,
    COVERAGE_DRIFT,
    COVERAGE_IMAGE_UNSUPPORTED,
    COVERAGE_NAME_INELIGIBLE,
    COVERAGE_NOT_ACTIVE,
    COVERAGE_RECORD_MISSING,
    COVERAGE_STORE_UNAVAILABLE,
    MANAGED_RECORD_DRIFT,
    RECORD_NOT_OWNED,
)
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
from sandbox.hosting.zone_coverage import models


def coverage_zone_of(hostname: str, candidate_zones: list[str]) -> str | None:
    """Return the longest matching candidate zone for hostname, or None.

    The apex is matched for subsequent eligibility checks; lookalike suffixes are not.
    """
    clean_host = hostname.strip().rstrip(".").lower()
    matches = []
    for z in candidate_zones:
        clean_z = z.strip().rstrip(".").lower()
        if clean_host == clean_z or clean_host.endswith("." + clean_z):
            matches.append(clean_z)
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    return matches[0]


def _refusal(code: str, message: str, report: bool) -> dict:
    if report:
        return {"mode": "refused", "code": code, "reason": message}
    raise CoverageError(code, message)


def _target_has_owned_record(target_record: dict | None, route_hostnames: set[str]) -> bool:
    if not isinstance(target_record, dict):
        return False
    records = target_record.get("records") or []
    for item in records:
        if not isinstance(item, dict):
            continue
        applied = item.get("applied") or {}
        name = applied.get("name") or (item.get("previous") or {}).get("name") or item.get("hostname")
        if name:
            try:
                clean_name = models.normalize_hostname(name)
            except ValueError:
                clean_name = name.strip().rstrip(".").lower()
            if clean_name in route_hostnames:
                return True
    return False


def _find_matching_zone(route_hostnames: set[str], listed_zones: list[dict]) -> dict | None:
    zone_names = [item["zone"] for item in listed_zones if item.get("zone")]
    matches = []
    for h in route_hostnames:
        matched_z = coverage_zone_of(h, zone_names)
        if matched_z:
            for item in listed_zones:
                if item.get("zone") == matched_z:
                    matches.append(item)
                    break
    if not matches:
        return None
    # Pick longest matching zone name
    matches.sort(key=lambda item: len(item.get("zone", "")), reverse=True)
    return matches[0]


def decide(
    validated: dict,
    entry: dict,
    target_record: dict | None,
    *,
    store_client: ZoneCoverageClient | None = None,
    cf_client: Any | None = None,
    facts: dict | None = None,
    report: bool = False,
    image: bool = False,
) -> dict:
    """Decide whether a target uses exact DNS or shared zone coverage.

    Follows the decision table from Spec 070 research R6 in order, stopping at
    the first matching rule.
    """
    # 1. Front door lacks ZONE_COVERAGE: {"mode": "exact"} with no store, SSH or provider call.
    decl = declaration(front_door_mode(entry))
    if ZONE_COVERAGE not in decl.capabilities or (
        isinstance(entry.get("capabilities"), (list, set, tuple, frozenset))
        and ZONE_COVERAGE not in entry["capabilities"]
        and "zone_coverage_v1" not in entry["capabilities"]
    ):
        return {"mode": "exact"}

    # 2. Store list fails: refuse coverage_store_unavailable.
    if store_client is None:
        store_client = ZoneCoverageClient(entry)
    try:
        envelope = store_client.list()
        if not isinstance(envelope, dict) or envelope.get("refused") or not isinstance(envelope.get("zones"), list) or not all(isinstance(item, dict) for item in envelope["zones"]):
            raise ValueError("invalid list envelope")
        listed_zones = envelope["zones"]
    except Exception:
        return _refusal(
            COVERAGE_STORE_UNAVAILABLE,
            "zone coverage store is unavailable on this remote",
            report,
        )

    # 3. Find the covered zone: a listed zone equal to a route's hostname or a suffix of it after a dot.
    route_hostnames = {
        models.normalize_hostname(r["hostname"])
        for r in (validated.get("routes") or [])
    }
    matching_zone_item = _find_matching_zone(route_hostnames, listed_zones)
    if matching_zone_item is None or matching_zone_item.get("state") == "retired":
        # Check if nginx facts hold a fallback file for a suffix of a route's hostname
        has_fallback = False
        if facts and isinstance(facts.get("routes"), list):
            for route_fact in facts["routes"]:
                fb_zone = route_fact.get("coverage_zone") or _nginx_coverage_zone_of(route_fact.get("file", ""))
                if fb_zone and any(h == fb_zone or h.endswith("." + fb_zone) for h in route_hostnames):
                    has_fallback = True
                    break
        if has_fallback:
            return _refusal(
                COVERAGE_RECORD_MISSING,
                "fallback route exists in nginx configuration but zone coverage is missing or retired",
                report,
            )
        return {"mode": "exact"}

    z = matching_zone_item["zone"]

    # Read the store document for the zone
    try:
        envelope = store_client.read(z)
        if not isinstance(envelope, dict) or envelope.get("refused") or not isinstance(envelope.get("document"), dict):
            raise ValueError("invalid read envelope")
        doc = envelope["document"]
    except Exception:
        return _refusal(
            COVERAGE_STORE_UNAVAILABLE,
            f"failed to read zone coverage document for {z}",
            report,
        )

    cf_config = validated.get("cloudflare") or {}
    cf_dns = cf_config.get("dns", "auto")
    proxied = bool(cf_config.get("proxied"))

    # 4. cloudflare.dns == "exact": exact with drift rule
    if cf_dns == "exact":
        return {"mode": "exact", "zone": z, "drift_rule": True}

    # 5. Target record holds an owned record for a route hostname: exact with drift rule
    if _target_has_owned_record(target_record, route_hostnames):
        return {"mode": "exact", "zone": z, "drift_rule": True}

    # 6. Read zone_records. A provider failure refuses coverage_drift in apply.
    try:
        if cf_client is None:
            cf_client = cloudflare.Client()
        zone_id = doc.get("zone_id")
        if not zone_id:
            zone_id = cf_client.zone(z)["id"]
        zone_records = cf_client.zone_records(zone_id)
    except Exception:
        return _refusal(
            COVERAGE_DRIFT,
            "cloudflare zone records check failed",
            report,
        )

    # Check adoption: every route has records equal to desired (same type, content, proxied)
    origin_ipv4 = entry.get("origin_ipv4") or (entry.get("address") if ":" not in (entry.get("address") or "") else None)
    origin_ipv6 = entry.get("origin_ipv6") or (entry.get("address") if ":" in (entry.get("address") or "") else None)
    addresses = [a for a in [origin_ipv4, origin_ipv6] if a]
    all_routes_adopted = bool(addresses) and bool(validated.get("routes"))
    for r in (validated.get("routes") or []):
        r_name = models.normalize_hostname(r["hostname"])
        for a in addresses:
            wanted_type = "AAAA" if ":" in a else "A"
            found_equal = any(
                item.get("name") == r_name
                and item.get("type") == wanted_type
                and item.get("content") == a
                and bool(item.get("proxied")) == proxied
                for item in zone_records
            )
            if not found_equal:
                all_routes_adopted = False
                break
        if not all_routes_adopted:
            break

    if all_routes_adopted:
        return {"mode": "exact", "zone": z, "drift_rule": True}

    # DNS-only (proxied false) under auto:
    # If state is active and target has no owned or equal record: refuse coverage_name_ineligible
    if not proxied:
        if doc.get("state") == "active":
            return _refusal(
                COVERAGE_NAME_INELIGIBLE,
                "DNS-only hosting under active zone coverage requires an explicit or adopted record; set cloudflare.dns: exact in the manifest",
                report,
            )
        return {"mode": "exact", "zone": z, "drift_rule": True}

    # 7. Image activation path: refuse coverage_image_unsupported for what would be shared
    if image:
        return _refusal(
            COVERAGE_IMAGE_UNSUPPORTED,
            "image activation does not support shared zone coverage; set cloudflare.dns: exact in the manifest",
            report,
        )

    # 8. By state:
    state = doc.get("state")
    dependents = doc.get("dependents") or {}
    target_key = (
        hosting.state_key(entry.get("name", ""), validated)
        if (entry.get("name") and isinstance(validated, dict) and "project" in validated and "environment" in validated)
        else None
    )
    target_in_dependents = target_key in dependents

    if state in ("fallback_only", "disabled"):
        if target_in_dependents:
            return _refusal(
                COVERAGE_DISABLED_DEPENDENT,
                f"target is a dependent of {state} zone coverage; specify cloudflare.dns: exact to manage an individual record",
                report,
            )
        return {"mode": "exact", "zone": z, "drift_rule": True}

    if state in ("enabling", "incomplete", "disabling"):
        return _refusal(
            COVERAGE_NOT_ACTIVE,
            f"zone coverage is not active (state: {state})",
            report,
        )

    # 9. active state:
    if state == "active":
        recorded_cov = (target_record or {}).get("dns_coverage")
        if isinstance(recorded_cov, dict) and "generation" in recorded_cov:
            rec_gen = recorded_cov.get("generation")
            if rec_gen is not None and rec_gen != doc.get("generation"):
                return _refusal(
                    COVERAGE_DRIFT,
                    f"zone {z} coverage generation drifted: recorded {rec_gen} != store {doc.get('generation')}",
                    report,
                )

        routes = validated.get("routes") or []
        if len(routes) > 1:
            return _refusal(
                COVERAGE_NAME_INELIGIBLE,
                "multi-route targets are not eligible for shared zone coverage; set cloudflare.dns: exact in the manifest",
                report,
            )
        primary_host = models.normalize_hostname(routes[0]["hostname"])
        if not models.eligible(primary_host, z, zone_records):
            # Check for conflicting record in zone_records
            conflicting_rec = next(
                (rec for rec in zone_records
                 if rec.get("name") == primary_host or (rec.get("name") or "").endswith("." + primary_host)),
                None,
            )
            if conflicting_rec:
                rec_type = conflicting_rec.get("type", "record")
                rec_name = conflicting_rec.get("name", primary_host)
                return _refusal(
                    COVERAGE_NAME_INELIGIBLE,
                    f"hostname {primary_host} is ineligible for shared zone coverage because a conflicting {rec_type} record exists at {rec_name}; set cloudflare.dns: exact in the manifest",
                    report,
                )
            if not primary_host.endswith("." + z) or primary_host.count(".") != z.count(".") + 1:
                return _refusal(
                    COVERAGE_NAME_INELIGIBLE,
                    f"hostname {primary_host} is ineligible for shared zone coverage because it is not a direct subdomain of {z}; set cloudflare.dns: exact in the manifest",
                    report,
                )
            return _refusal(
                COVERAGE_NAME_INELIGIBLE,
                f"hostname {primary_host} is ineligible for shared zone coverage; set cloudflare.dns: exact in the manifest",
                report,
            )

        # Check zone TLS mode is strict
        try:
            ssl_mode = cf_client.current_ssl_mode(zone_id)
        except Exception:
            return _refusal(COVERAGE_DRIFT, "cloudflare SSL mode check failed", report)
        if ssl_mode != "strict":
            return _refusal(
                COVERAGE_DRIFT,
                f"zone {z} SSL mode is {ssl_mode}; shared coverage requires strict",
                report,
            )

        # Check observed shared records match document's shared_records
        expected_shared = doc.get("shared_records") or []
        actual_shared = [
            r for r in zone_records
            if r.get("name") == f"*.{z}"
        ]
        if not expected_shared or not actual_shared:
            return _refusal(
                COVERAGE_DRIFT,
                "observed shared records do not match store document",
                report,
            )
        expected_ids = {r.get("id") for r in expected_shared if r.get("id")}
        actual_ids = {r.get("id") for r in actual_shared if r.get("id")}
        if expected_ids != actual_ids:
            return _refusal(
                COVERAGE_DRIFT,
                "observed shared records do not match store document",
                report,
            )
        for exp in expected_shared:
            act = next((x for x in actual_shared if x.get("id") == exp.get("id")), None)
            if (not act or act.get("type") != exp.get("type")
                    or act.get("content") != exp.get("content")
                    or exp.get("content") != {"A": origin_ipv4, "AAAA": origin_ipv6}.get(exp.get("type"))
                    or exp.get("proxied") is not True or act.get("proxied") is not True):
                return _refusal(
                    COVERAGE_DRIFT,
                    "observed shared records do not match store document",
                    report,
                )

        # Check fallback file is present in facts
        if facts and isinstance(facts.get("routes"), list):
            fb_present = any(
                (r.get("coverage_zone") or _nginx_coverage_zone_of(r.get("file", ""))) == z
                for r in facts["routes"]
            )
            if not fb_present:
                return _refusal(
                    COVERAGE_DRIFT,
                    f"fallback file for {z} not present in nginx configuration facts",
                    report,
                )

        generation = doc.get("generation", 0)
        return {"mode": "shared", "zone": z, "generation": generation}

    return _refusal(COVERAGE_NOT_ACTIVE, f"zone coverage state {state} is not supported", report)


def _store_mutation(operation: str, call: Callable[[], dict]) -> dict:
    """Keep transport diagnostics and untrusted store reasons out of public errors."""
    from sandbox.hosting.zone_coverage import (
        REFUSAL_CODES, COVERAGE_GENERATION_STALE, COVERAGE_DEPENDENTS_STALE,
        COVERAGE_DOCUMENT_CORRUPT, COVERAGE_KIND_MISMATCH, COVERAGE_LEASE_ACTIVE,
    )
    allowed = set(REFUSAL_CODES) | {COVERAGE_GENERATION_STALE, COVERAGE_DEPENDENTS_STALE,
                                  COVERAGE_DOCUMENT_CORRUPT, COVERAGE_KIND_MISMATCH, COVERAGE_LEASE_ACTIVE}
    try:
        result = call()
        if not isinstance(result, dict):
            raise ValueError("invalid store response")
        if result.get("refused"):
            code = result["refused"] if result["refused"] in allowed else COVERAGE_STORE_UNAVAILABLE
            raise CoverageError(code, f"zone coverage {operation} was refused")
        return result
    except CoverageError as exc:
        code = exc.code if exc.code in allowed else COVERAGE_STORE_UNAVAILABLE
        raise CoverageError(code, f"zone coverage {operation} failed") from None
    except Exception:
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, f"zone coverage {operation} failed") from None


def bind_pending(store_client: ZoneCoverageClient, zone: str, state_key: str, hostname: str,
                 generation: int | None = None, operation_id: str | None = None) -> dict:
    return _store_mutation("pending bind", lambda: store_client.dependent_bind(
        zone, state_key, hostname, "pending", generation=generation, operation_id=operation_id))


def bind_bound(store_client: ZoneCoverageClient, zone: str, state_key: str, hostname: str,
               generation: int | None = None, operation_id: str | None = None) -> dict:
    return _store_mutation("bound bind", lambda: store_client.dependent_bind(
        zone, state_key, hostname, "bound", generation=generation, operation_id=operation_id))


def release(store_client: ZoneCoverageClient, zone: str, state_key: str) -> dict:
    return _store_mutation("release", lambda: store_client.dependent_release(zone, state_key))


def check_exact_record(
    wanted: dict,
    zone_records_at_name: list[dict],
    recorded_records: list[dict] | None,
    *,
    allow_overwrite: bool = False,
) -> dict:
    """Evaluate DNS drift rule for an exact record in a covered zone.

    Compares only A, AAAA, and CNAME records at the name.
    """
    wanted_type = wanted.get("record_type") or ("AAAA" if ":" in wanted.get("address", "") else "A")
    wanted_content = wanted.get("content") if wanted_type == "CNAME" else wanted.get("address")
    wanted_proxied = bool(wanted.get("proxied"))
    hostname = wanted.get("hostname")

    matching = [
        r for r in zone_records_at_name
        if r.get("name") == hostname and r.get("type") in (wanted_type, "CNAME")
    ]
    if not matching:
        return {"action": "write"}

    existing = next((r for r in matching if r.get("type") == wanted_type), matching[0])
    if (
        existing.get("type") == wanted_type
        and existing.get("content") == wanted_content
        and bool(existing.get("proxied")) == wanted_proxied
    ):
        return {"action": "adopt", "existing": existing}

    applied_snap = None
    for item in recorded_records or []:
        if not isinstance(item, dict):
            continue
        snap = item.get("applied")
        if isinstance(snap, dict):
            if (snap.get("name") == hostname and snap.get("type") == existing.get("type")
                    and (not item.get("created_id") or item["created_id"] == existing.get("id"))):
                applied_snap = snap
                break

    if applied_snap is not None:
        if (
            applied_snap.get("type") == existing.get("type")
            and applied_snap.get("content") == existing.get("content")
            and bool(applied_snap.get("proxied")) == bool(existing.get("proxied"))
        ):
            return {"action": "write"}
        if allow_overwrite:
            return {"action": "write"}
        raise CoverageError(
            MANAGED_RECORD_DRIFT,
            f"DNS record for {hostname} has drifted from recorded applied state; pass --allow-record-overwrite to overwrite",
        )

    raise CoverageError(
        RECORD_NOT_OWNED,
        f"DNS record for {hostname} exists but is not managed by Sandbox; refusing to overwrite unowned record",
    )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, pinned_ip: str, host: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        server_hostname = (self._tunnel_host or self.host or "").split(":")[0]
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=server_hostname,
        )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def https_open(self, req):
        def _factory(host, **conn_kwargs):
            return _PinnedHTTPSConnection(self._pinned_ip, host, **conn_kwargs)
        return self.do_open(_factory, req, context=self._context)


def pinned_edge_handler(
    zone: str,
    *,
    resolver: Callable[..., Any] | None = None,
    label_source: str | None = None,
) -> urllib.request.HTTPSHandler | None:
    """Resolve a random verify label under zone and return an HTTPSHandler pinned to that IP.

    If the label does not resolve, returns None.
    """
    res_fn = resolver if resolver is not None else socket.getaddrinfo
    label = label_source or f"sb-verify-{secrets.token_hex(8)}"
    verify_hostname = f"{label}.{zone}"
    try:
        addrinfo = res_fn(verify_hostname, 443)
        if not addrinfo:
            return None
        pinned_ip = addrinfo[0][4][0]
    except Exception:
        return None

    return _PinnedHTTPSHandler(pinned_ip)


def resolver_state(
    hostname: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> str:
    """Inspect resolver reachability for hostname: 'resolved', 'negative', or 'error'."""
    res_fn = resolver if resolver is not None else socket.getaddrinfo
    try:
        res = res_fn(hostname, 443)
        return "resolved" if res else "negative"
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -999)):
            return "negative"
        return "error"
    except Exception:
        return "error"


def verify_shared(
    routes: list[dict],
    zone: str,
    verify: Callable[..., Any],
    *,
    resolver: Callable[..., Any] | None = None,
    **verify_kwargs,
) -> dict:
    """Verify public edge reachability for shared zone coverage and return evidence."""
    observed_at = datetime.now(timezone.utc).isoformat()
    primary_host = models.normalize_hostname(routes[0]["hostname"])
    r_state = resolver_state(primary_host, resolver=resolver)
    resolver_evidence = {"result": r_state}

    handler = pinned_edge_handler(zone, resolver=resolver)
    if handler is None:
        public_edge = {"result": "unverified", "reason": "shared_label_unresolved"}
    else:
        # Strip fresh_hostnames from verify_kwargs to bypass DNS propagation wait
        vk = dict(verify_kwargs)
        vk.pop("fresh_hostnames", None)
        verify(routes, handlers=(handler,), fresh_hostnames=frozenset(), **vk)
        public_edge = {"result": "passed"}

    return {
        "origin": {"result": "passed"},
        "public_edge": public_edge,
        "resolver": resolver_evidence,
        "observed_at": observed_at,
    }
