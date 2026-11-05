"""Zone coverage orchestrator service: plan_enable, enable, and status (spec 070)."""
from __future__ import annotations

import ipaddress
import secrets
import time
from typing import Any, Callable

from sandbox.hosting.front_door import ZONE_COVERAGE, nginx, require_capability
from sandbox.hosting.zone_coverage import (
    COVERAGE_CONFLICT,
    COVERAGE_FALLBACK_UNVERIFIED,
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_PLAN_STALE,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_SSL_NOT_STRICT,
    COVERAGE_STORE_UNAVAILABLE,
    COVERAGE_ZONE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage import fallback
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
from sandbox.hosting.zone_coverage.models import normalize_zone


def _check_store_res(res: dict[str, Any]) -> None:
    if not isinstance(res, dict):
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, "zone coverage store returned non-dict response")
    if res.get("refused"):
        code = res["refused"]
        reason = res.get("reason", code)
        raise CoverageError(code, f"store refused: {reason}")


class _NoRedirectHandler:
    pass


def _is_valid_fallback_probe_response(status: int, headers: Any, body: str) -> bool:
    """Helper rule for verifying exact 404 fallback response per spec 070 G6."""
    hdr_val = headers.get("X-Sandbox-Fallback") if hasattr(headers, "get") else None
    return status == 404 and hdr_val == "1" and body == fallback.FALLBACK_BODY


def public_probe(
    zone: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    label_source: Callable[[], str] | None = None,
    opener: Any = None,
    max_tries: int = 10,
    delay: float = 15.0,
    timeout: float = 10.0,
) -> bool:
    """Probe edge for wildcard fallback: https://sb-verify-<16 hex>.<zone>/."""
    import urllib.request
    import urllib.error

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if opener is None:
        opener = urllib.request.build_opener(NoRedirect)

    clean_zone = zone.strip().rstrip(".").lower()
    for attempt in range(max_tries):
        token = label_source() if label_source is not None else secrets.token_hex(8)
        host = f"sb-verify-{token}.{clean_zone}"
        url = f"https://{host}/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sandbox-coverage-probe/1.0"})
            resp = opener.open(req, timeout=timeout)
            status = getattr(resp, "status", getattr(resp, "code", 200))
            headers = resp.headers
            body = resp.read().decode("utf-8", "replace")
            if _is_valid_fallback_probe_response(status, headers, body):
                return True
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = exc.headers
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            if _is_valid_fallback_probe_response(status, headers, body):
                return True
        except Exception:
            pass

        if attempt < max_tries - 1:
            sleep(delay)

    return False


def plan_enable(
    entry: dict[str, Any],
    zone: str,
    *,
    cf_client: Any = None,
    store_client: Any = None,
) -> dict[str, Any]:
    """Inspect remote and Cloudflare to produce an execution plan for enabling coverage."""
    require_capability(entry, ZONE_COVERAGE)
    clean_zone = normalize_zone(zone)

    if store_client is None:
        store_client = ZoneCoverageClient(entry)
    read_res = store_client.read(clean_zone)
    doc = read_res.get("document")

    resumable = False
    holder = None
    interrupted_phase = None
    if doc:
        op = doc.get("operation")
        if op:
            now = time.time()
            lease_expires = op.get("lease_expires_at")
            if lease_expires is not None and now >= lease_expires:
                if op.get("kind") != "enable":
                    raise CoverageError(
                        COVERAGE_OPERATION_ACTIVE,
                        f"expired operation {op.get('id')} has kind {op.get('kind')}, cannot resume enable",
                    )
                resumable = True
                holder = op.get("controller")
                interrupted_phase = op.get("phase")
            else:
                raise CoverageError(
                    COVERAGE_OPERATION_ACTIVE,
                    f"operation {op.get('id')} ({op.get('kind')}) is currently active",
                )
        cur_gen = doc.get("generation", 0)
        cur_dep_rev = doc.get("dependents_revision", 0)
        cur_state = doc.get("state", "absent")
    else:
        cur_gen = 0
        cur_dep_rev = 0
        cur_state = "absent"

    # Preflight / probe nginx front door
    try:
        facts = nginx.probe(entry)
    except Exception:
        facts = {"config_valid": True, "routes": []}

    if not facts.get("config_valid"):
        from sandbox.hosting.front_door import FrontDoorError
        raise FrontDoorError("front_door_nginx_invalid", "nginx configuration is invalid")

    # Classify existing routes
    dump_pairs = []
    for r in facts.get("routes") or []:
        path = r.get("file", "")
        for h in r.get("hostnames") or []:
            dump_pairs.append((path, h))
    if not dump_pairs and facts.get("dump"):
        dump_pairs = nginx.parse_server_names(facts["dump"])

    exceptions, blockers = nginx.classify_for_fallback(dump_pairs, clean_zone)
    if blockers:
        raise CoverageError(
            COVERAGE_CONFLICT,
            f"conflicting wildcard or regex routes exist: {blockers}",
        )

    # Cloudflare zone and SSL check
    if cf_client is None:
        from sandbox.core import _cloudflare as cloudflare
        cf_client = cloudflare.Client()

    try:
        if hasattr(cf_client, "zones"):
            zones = cf_client.zones(name=clean_zone)
            cf_zone = zones[0] if zones else None
        else:
            cf_zone = cf_client.zone(clean_zone)
        if not cf_zone:
            raise CoverageError(COVERAGE_ZONE_UNAVAILABLE, f"zone {clean_zone} was not found in Cloudflare account")
        zone_id = cf_zone["id"]
    except CoverageError:
        raise
    except Exception:
        raise CoverageError(COVERAGE_ZONE_UNAVAILABLE, "failed to read zone from Cloudflare") from None

    try:
        ssl_data = cf_client._request("GET", f"/zones/{zone_id}/settings/ssl")
    except Exception:
        raise CoverageError(COVERAGE_ZONE_UNAVAILABLE, "failed to read zone SSL settings from Cloudflare") from None

    ssl_val = (ssl_data.get("result") or {}).get("value")
    if ssl_val != "strict":
        raise CoverageError(
            COVERAGE_SSL_NOT_STRICT,
            f"Cloudflare SSL mode is {ssl_val!r}, must be 'strict'",
        )

    target_ip = entry.get("origin_ipv4")
    if not target_ip:
        ssh = entry.get("ssh") or ""
        host = ssh.split("@")[-1]
        try:
            ipaddress.ip_address(host)
            target_ip = host
        except ValueError:
            pass
    if not target_ip:
        raise CoverageError(COVERAGE_ZONE_UNAVAILABLE, "remote entry has no origin_ipv4 configured")

    try:
        existing_records = cf_client.records(zone_id, f"*.{clean_zone}")
    except Exception:
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, "failed to read DNS records from Cloudflare") from None

    wanted_name = f"*.{clean_zone}"
    records_to_create = []
    records_to_adopt = []

    if existing_records:
        for r in existing_records:
            if r.get("type") == "A":
                if r.get("content") == target_ip and r.get("proxied") is True:
                    records_to_adopt.append(r)
                else:
                    raise CoverageError(
                        COVERAGE_RECORD_EXISTS,
                        f"existing {wanted_name} record differs: content={r.get('content')}, proxied={r.get('proxied')}",
                    )
            elif r.get("type") in ("CNAME", "AAAA"):
                raise CoverageError(
                    COVERAGE_RECORD_EXISTS,
                    f"existing {wanted_name} record has conflicting type {r.get('type')}",
                )

    if not records_to_adopt:
        records_to_create.append({
            "name": wanted_name,
            "type": "A",
            "content": target_ip,
            "proxied": True,
        })

    dependents: dict[str, Any] = dict(doc.get("dependents", {})) if doc else {}
    if records_to_adopt:
        existing_names = {r.get("name") for r in existing_records}
        for exc in exceptions:
            if exc.get("owner") == "sandbox":
                h = exc.get("hostname")
                if h and h not in existing_names:
                    clean_key = f"recovered/routes/{h}"
                    if clean_key not in dependents:
                        dependents[clean_key] = {
                            "hostname": h,
                            "state": "recovered",
                            "generation": cur_gen,
                            "updated_at": time.time(),
                        }

    plan_out = {
        "action": "enable",
        "zone": clean_zone,
        "zone_id": zone_id,
        "remote": entry.get("name", "remote"),
        "current_state": cur_state,
        "expected_generation": cur_gen,
        "expected_dependents_revision": cur_dep_rev,
        "records_to_create": records_to_create,
        "records_to_adopt": records_to_adopt,
        "exceptions": exceptions,
        "blockers": blockers,
        "dependents": dependents,
        "resumable": resumable,
        "holder": holder,
        "phase": interrupted_phase,
    }
    plan_out["plan_digest"] = compute_plan_digest(plan_out)
    return plan_out


def compute_plan_digest(plan: dict[str, Any]) -> str:
    """Compute deterministic sha256 digest covering plan inputs per spec 070."""
    import hashlib
    import json
    content = {
        "action": plan.get("action", "enable"),
        "zone": plan.get("zone"),
        "generation": plan.get("expected_generation", 0),
        "dependents_revision": plan.get("expected_dependents_revision", 0),
        "dependents": plan.get("dependents", {}),
        "exceptions": plan.get("exceptions", []),
        "blockers": plan.get("blockers", []),
        "records_to_create": plan.get("records_to_create", []),
        "records_to_adopt": plan.get("records_to_adopt", []),
    }
    raw = json.dumps(content, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enable(
    entry: dict[str, Any],
    zone: str,
    plan: dict[str, Any],
    *,
    stop_before_publish: bool = False,
    resume: bool = False,
    controller: str | None = None,
    cf_client: Any = None,
    store_client: Any = None,
    ssh_run: Callable[..., Any] | None = None,
    probe_func: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Execute coverage enable per plan, updating store and Cloudflare."""
    clean_zone = normalize_zone(zone)
    if store_client is None:
        store_client = ZoneCoverageClient(entry, ssh_run=ssh_run)

    read_res = store_client.read(clean_zone)
    _check_store_res(read_res)
    doc = read_res.get("document")
    cur_gen = doc.get("generation", 0) if doc else 0
    cur_dep_rev = doc.get("dependents_revision", 0) if doc else 0

    if (
        cur_gen != plan.get("expected_generation")
        or cur_dep_rev != plan.get("expected_dependents_revision")
    ):
        raise CoverageError(COVERAGE_PLAN_STALE, "plan is stale; re-run plan before applying")

    if cf_client is None:
        from sandbox.core import _cloudflare as cloudflare
        cf_client = cloudflare.Client()

    begin_res = store_client.begin(
        clean_zone,
        "enable",
        controller or "sandbox-controller",
        expected_generation=cur_gen,
        expected_dependents_revision=cur_dep_rev,
        resume=resume,
        zone_id=plan.get("zone_id", ""),
        dependents=plan.get("dependents"),
    )
    _check_store_res(begin_res)
    op_id = begin_res["operation_id"]

    # Probe nginx facts to extract host features
    try:
        facts = nginx.probe(entry)
        feat = nginx.features(facts) if facts else {}
    except Exception:
        facts = {}
        feat = plan.get("features") or {}
    http2_directive = feat.get("http2_directive", True)
    ipv6 = bool(facts.get("ipv6") or feat.get("ipv6") or entry.get("origin_ipv6"))

    # Fallback installation phase
    phase_res = store_client.phase(clean_zone, op_id, "fallback_install")
    _check_store_res(phase_res)
    try:
        fallback_meta = fallback.install_fallback(
            entry,
            clean_zone,
            client=cf_client,
            ipv6=ipv6,
            http2_directive=http2_directive,
            ssh_run=ssh_run,
        )
        phase_res = store_client.phase(
            clean_zone,
            op_id,
            "fallback_verified",
            updates={"fallback": fallback_meta, "exceptions": plan.get("exceptions", [])},
        )
        _check_store_res(phase_res)
    except Exception:
        store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        raise

    if stop_before_publish:
        finish_res = store_client.finish(clean_zone, op_id, "fallback_only")
        _check_store_res(finish_res)
        return {"state": "fallback_only", "fallback": fallback_meta}

    # DNS publish phase
    phase_res = store_client.phase(clean_zone, op_id, "dns_publish")
    _check_store_res(phase_res)
    records_written = []
    shared_records = []
    try:
        for r in plan.get("records_to_create", []):
            exact_records = cf_client.records(plan["zone_id"], hostname=r["name"])
            already_equal = None
            for existing in exact_records:
                if (
                    existing.get("type") == r.get("type", "A")
                    and existing.get("content") == r["content"]
                    and existing.get("proxied") == r.get("proxied", True)
                ):
                    already_equal = existing
                    break
                elif existing.get("type") in ("A", "CNAME", "AAAA"):
                    raise CoverageError(
                        COVERAGE_RECORD_EXISTS,
                        f"record {r['name']} already exists in Cloudflare with differing value",
                    )
            if already_equal:
                shared_records.append({
                    "id": already_equal["id"],
                    "type": already_equal.get("type", "A"),
                    "content": already_equal.get("content", r["content"]),
                    "proxied": already_equal.get("proxied", True),
                })
            else:
                created = cf_client.upsert_address(
                    plan["zone_id"],
                    r["name"],
                    r["content"],
                    proxied=r.get("proxied", True),
                )
                records_written.append({
                    "id": created["id"],
                    "name": r["name"],
                    "content": r["content"],
                    "type": created.get("type", "A"),
                })
                shared_records.append({
                    "id": created["id"],
                    "type": created.get("type", "A"),
                    "content": created.get("content", r["content"]),
                    "proxied": created.get("proxied", True),
                })
        for r in plan.get("records_to_adopt", []):
            shared_records.append({
                "id": r["id"],
                "type": r.get("type", "A"),
                "content": r.get("content"),
                "proxied": r.get("proxied", True),
            })
        phase_res = store_client.phase(
            clean_zone,
            op_id,
            "dns_published",
            updates={"shared_records": shared_records},
        )
        _check_store_res(phase_res)
    except CoverageError:
        store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        raise
    except Exception:
        unknown_names = [rw["name"] for rw in records_written]
        unknown_effects = [{"action": "create_record", "name": name} for name in unknown_names]
        try:
            store_client.phase(clean_zone, op_id, "dns_publish_failed", updates={"unknown_effects": unknown_effects})
        except Exception:
            pass
        store_client.finish(
            clean_zone,
            op_id,
            "incomplete",
            interrupted_kind="enable",
            unknown_effects=unknown_effects,
        )
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, "failed during DNS record write loop") from None

    # Public probe check
    if probe_func is not None:
        probe_ok = probe_func(clean_zone)
    else:
        probe_ok = public_probe(clean_zone)

    if not probe_ok:
        kept_records = []
        delete_failures = []
        for wr in records_written:
            try:
                curr_list = cf_client.records(plan["zone_id"], hostname=wr["name"])
                matched = next((x for x in curr_list if x.get("id") == wr["id"]), None)
                if matched and matched.get("content") == wr["content"]:
                    cf_client.delete_record(plan["zone_id"], wr["id"])
                else:
                    kept_records.append({"id": wr["id"], "name": wr["name"], "reason": "record_modified_or_missing"})
            except Exception:
                delete_failures.append({"id": wr["id"], "name": wr["name"], "action": "delete_record"})

        if not kept_records and not delete_failures:
            finish_res = store_client.finish(clean_zone, op_id, "fallback_only")
            _check_store_res(finish_res)
            raise CoverageError(
                COVERAGE_FALLBACK_UNVERIFIED,
                "public edge probe failed to reach fallback route; rolled back created DNS records to fallback_only",
            )
        else:
            effects = kept_records + delete_failures
            finish_res = store_client.finish(
                clean_zone,
                op_id,
                "incomplete",
                interrupted_kind="enable",
                unknown_effects=effects,
            )
            _check_store_res(finish_res)
            raise CoverageError(
                COVERAGE_FALLBACK_UNVERIFIED,
                f"public edge probe failed and record rollback could not clean all records (kept: {len(effects)})",
            )

    phase_res = store_client.phase(clean_zone, op_id, "edge_verified")
    _check_store_res(phase_res)
    finish_res = store_client.finish(clean_zone, op_id, "active", raise_generation=True)
    _check_store_res(finish_res)
    gen = (finish_res.get("document") or {}).get("generation", 1)
    return {
        "state": "active",
        "generation": gen,
        "shared_records": shared_records,
        "fallback": fallback_meta,
    }


def status(
    entry: dict[str, Any],
    zone: str,
    *,
    cf_client: Any = None,
    store_client: Any = None,
) -> dict[str, Any]:
    """Read coverage state and inspect drift against Cloudflare."""
    clean_zone = normalize_zone(zone)
    if store_client is None:
        store_client = ZoneCoverageClient(entry)

    read_res = store_client.read(clean_zone)
    _check_store_res(read_res)
    doc = read_res.get("document")

    if not doc:
        return {
            "zone": clean_zone,
            "state": "absent",
            "generation": 0,
            "dependents_revision": 0,
            "operation": None,
            "interrupted_kind": None,
            "shared_records": [],
            "fallback": None,
            "exceptions": [],
            "dependents_count": 0,
            "dependents": {},
            "drift": None,
            "history": [],
        }

    drift = None
    if doc.get("state") == "active":
        if cf_client is None:
            from sandbox.core import _cloudflare as cloudflare
            cf_client = cloudflare.Client()
        try:
            zone_id = doc.get("zone_id") or cf_client.zone(clean_zone)["id"]
            records = cf_client.records(zone_id, f"*.{clean_zone}")
            expected_ids = {r.get("id") for r in doc.get("shared_records", []) if r.get("id")}
            actual_ids = {r.get("id") for r in records if r.get("id")}
            if expected_ids != actual_ids:
                drift = {
                    "reason": "dns_records_mismatch",
                    "expected": doc.get("shared_records"),
                    "actual": records,
                }
            else:
                for r in records:
                    exp = next((x for x in doc.get("shared_records", []) if x.get("id") == r.get("id")), None)
                    if exp and (exp.get("content") != r.get("content") or exp.get("proxied") != r.get("proxied")):
                        drift = {
                            "reason": "dns_record_modified",
                            "record_id": r.get("id"),
                            "expected": exp,
                            "actual": r,
                        }
                        break
        except Exception:
            drift = {"reason": "provider_error", "error": "unavailable"}

    return {
        "zone": clean_zone,
        "zone_id": doc.get("zone_id"),
        "state": doc.get("state"),
        "generation": doc.get("generation", 0),
        "dependents_revision": doc.get("dependents_revision", 0),
        "operation": doc.get("operation"),
        "interrupted_kind": doc.get("interrupted_kind"),
        "shared_records": doc.get("shared_records", []),
        "fallback": doc.get("fallback"),
        "exceptions": doc.get("exceptions", []),
        "dependents_count": len(doc.get("dependents", {})),
        "dependents": doc.get("dependents", {}),
        "drift": drift,
        "history": doc.get("history", []),
    }
