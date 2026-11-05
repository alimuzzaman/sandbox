"""Zone coverage orchestrator service: plan_enable, enable, and status (spec 070)."""
from __future__ import annotations

import ipaddress
from typing import Any, Callable

from sandbox.hosting.front_door import ZONE_COVERAGE, nginx, require_capability
from sandbox.hosting.zone_coverage import (
    COVERAGE_CONFLICT,
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_PLAN_STALE,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_SSL_NOT_STRICT,
    COVERAGE_ZONE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage import fallback
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
from sandbox.hosting.zone_coverage.models import normalize_zone


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

    if doc:
        op = doc.get("operation")
        if op:
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
    facts = nginx.probe(entry)
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
        cf_zone = cf_client.zone(clean_zone)
        zone_id = cf_zone["id"]
    except Exception as exc:
        raise CoverageError(COVERAGE_ZONE_UNAVAILABLE, f"Cloudflare zone {clean_zone} unavailable: {exc}") from exc

    ssl_data = cf_client._request("GET", f"/zones/{zone_id}/settings/ssl")
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

    existing_records = cf_client.records(zone_id, f"*.{clean_zone}")
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
        "exceptions": plan.get("exceptions", []),
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
) -> dict[str, Any]:
    """Execute coverage enable per plan, updating store and Cloudflare."""
    clean_zone = normalize_zone(zone)
    if store_client is None:
        store_client = ZoneCoverageClient(entry, ssh_run=ssh_run)

    read_res = store_client.read(clean_zone)
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
    )
    if begin_res.get("refused"):
        raise CoverageError(
            begin_res["refused"],
            f"store refused begin: {begin_res.get('reason', begin_res['refused'])}",
        )
    op_id = begin_res["operation_id"]

    # Fallback installation phase
    store_client.phase(clean_zone, op_id, "fallback_install")
    try:
        fallback_meta = fallback.install_fallback(
            entry,
            clean_zone,
            client=cf_client,
            ipv6=bool(entry.get("origin_ipv6")),
            ssh_run=ssh_run,
        )
        store_client.phase(
            clean_zone,
            op_id,
            "fallback_verified",
            updates={"fallback": fallback_meta, "exceptions": plan.get("exceptions", [])},
        )
    except Exception:
        store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        raise

    if stop_before_publish:
        store_client.finish(clean_zone, op_id, "fallback_only")
        return {"state": "fallback_only", "fallback": fallback_meta}

    # DNS publish phase
    store_client.phase(clean_zone, op_id, "dns_publish")
    try:
        shared_records = []
        for r in plan.get("records_to_create", []):
            created = cf_client.upsert_address(
                plan["zone_id"],
                r["name"],
                r["content"],
                proxied=r.get("proxied", True),
            )
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
        store_client.phase(
            clean_zone,
            op_id,
            "dns_published",
            updates={"shared_records": shared_records},
        )
    except Exception:
        store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        raise

    store_client.phase(clean_zone, op_id, "edge_verified")
    finish_res = store_client.finish(clean_zone, op_id, "active", raise_generation=True)
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
        except Exception as exc:
            drift = {"reason": "provider_error", "error": str(exc)}

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
