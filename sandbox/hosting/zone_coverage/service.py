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


def _is_ours(record: dict[str, Any] | None, intent: dict[str, Any] | None) -> bool:
    """Return True only if record comment equals intent['marker'] and matches a tuple in intent['records']."""
    if not isinstance(record, dict) or not isinstance(intent, dict):
        return False
    marker = intent.get("marker")
    if not marker or not (record.get("comment") == marker):
        return False
    r_name = record.get("name")
    r_type = record.get("type", "A")
    r_content = record.get("content")
    r_proxied = bool(record.get("proxied", True))
    for item in intent.get("records") or []:
        if not isinstance(item, dict):
            continue
        if (
            item.get("name") == r_name
            and item.get("type", "A") == r_type
            and item.get("content") == r_content
            and bool(item.get("proxied", True)) == r_proxied
        ):
            return True
    return False


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


def _inspect_error_path_call(res: Any, call_name: str) -> str:
    """Inspect phase or finish call in error path per spec 070 G4/G9/H2."""
    if isinstance(res, dict):
        if res.get("refused"):
            reason = res.get("reason", res["refused"])
            return f"; store was not updated: refused {call_name} ({reason})"
        if res.get("error"):
            return f"; store was not updated: {call_name} error ({res['error']})"
        if res.get("ok") is not True:
            return f"; store was not updated: {call_name} failed"
    elif isinstance(res, Exception):
        return f"; store was not updated: {call_name} raised {type(res).__name__}"
    elif res is None:
        return f"; store was not updated: {call_name} failed"
    return ""


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
    except Exception as exc:
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            f"failed to probe nginx front door: {exc}",
        ) from None

    if not facts or not facts.get("config_valid"):
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            "nginx configuration is invalid or probe returned empty facts",
        )

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

    wanted_name = f"*.{clean_zone}"
    needed_records = [
        {
            "name": wanted_name,
            "type": "A",
            "content": target_ip,
            "proxied": True,
        }
    ]
    origin_ipv6 = entry.get("origin_ipv6")
    if origin_ipv6:
        needed_records.append({
            "name": wanted_name,
            "type": "AAAA",
            "content": origin_ipv6,
            "proxied": True,
        })

    try:
        all_zone_records = _list_records(cf_client, zone_id)
        existing_records = [
            r for r in all_zone_records
            if str(r.get("name", "")).strip().rstrip(".").lower() == wanted_name
        ]
    except Exception:
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, "failed to read DNS records from Cloudflare") from None

    stored_intent = doc.get("publish_intent") if isinstance(doc, dict) else None
    stored_marker = stored_intent.get("marker") if isinstance(stored_intent, dict) else None

    records_to_create = []
    records_to_adopt = []
    records_owned_from_prior_attempt = []

    existing_by_type: dict[str, list[dict]] = {}
    for r in existing_records:
        existing_by_type.setdefault(r.get("type", "A"), []).append(r)

    if "CNAME" in existing_by_type:
        raise CoverageError(
            COVERAGE_RECORD_EXISTS,
            f"existing {wanted_name} record has conflicting type CNAME",
        )

    if not origin_ipv6 and "AAAA" in existing_by_type:
        raise CoverageError(
            COVERAGE_RECORD_EXISTS,
            f"existing {wanted_name} record has conflicting type AAAA",
        )

    for cand in needed_records:
        rtype = cand["type"]
        cand_content = cand["content"]
        cand_proxied = cand["proxied"]

        recs_of_type = existing_by_type.get(rtype, [])
        if recs_of_type:
            exact_match = None
            for er in recs_of_type:
                same_content = er.get("content") == cand_content
                same_proxied = bool(er.get("proxied", True)) == cand_proxied
                if not same_content or not same_proxied:
                    raise CoverageError(
                        COVERAGE_RECORD_EXISTS,
                        f"existing {wanted_name} record differs: content={er.get('content')}, proxied={er.get('proxied')}",
                    )
                exact_match = er

            if exact_match:
                if _is_ours(exact_match, stored_intent):
                    records_owned_from_prior_attempt.append(exact_match)
                else:
                    records_to_adopt.append(exact_match)
        else:
            records_to_create.append(cand)

    dependents: dict[str, Any] = dict(doc.get("dependents", {})) if doc else {}
    from sandbox.hosting.zone_coverage.models import eligible
    for exc in exceptions:
        if exc.get("owner") == "sandbox":
            h = exc.get("hostname")
            if h and eligible(h, clean_zone, all_zone_records):
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
        "records_owned_from_prior_attempt": records_owned_from_prior_attempt,
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

    sanitized_dependents = {}
    for dep_k, dep_v in sorted((plan.get("dependents") or {}).items()):
        if isinstance(dep_v, dict):
            sanitized_dependents[dep_k] = {
                k: v for k, v in sorted(dep_v.items())
                if k not in ("updated_at", "timestamp", "time", "created_at")
            }
        else:
            sanitized_dependents[dep_k] = dep_v

    content = {
        "action": plan.get("action", "enable"),
        "zone": plan.get("zone"),
        "generation": plan.get("expected_generation", 0),
        "dependents_revision": plan.get("expected_dependents_revision", 0),
        "dependents": sanitized_dependents,
        "exceptions": plan.get("exceptions", []),
        "blockers": plan.get("blockers", []),
        "records_to_create": plan.get("records_to_create", []),
        "records_to_adopt": plan.get("records_to_adopt", []),
        "records_owned_from_prior_attempt": plan.get("records_owned_from_prior_attempt", []),
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

    # Probe nginx facts to extract host features and re-classify routes
    try:
        facts = nginx.probe(entry)
        if not facts or not facts.get("config_valid"):
            raise FrontDoorError("front_door_nginx_invalid", "nginx configuration is invalid or probe returned empty facts")
        feat = nginx.features(facts)

        fresh_pairs = []
        for r in facts.get("routes") or []:
            path = r.get("file", "")
            for h in r.get("hostnames") or []:
                fresh_pairs.append((path, h))
        if not fresh_pairs and facts.get("dump"):
            fresh_pairs = nginx.parse_server_names(facts["dump"])

        _, fresh_blockers = nginx.classify_for_fallback(fresh_pairs, clean_zone)
        if fresh_blockers:
            detail = ", ".join(f"{b.get('file')}: {b.get('server_name')}" for b in fresh_blockers)
            raise CoverageError(
                COVERAGE_CONFLICT,
                f"conflicting wildcard or regex routes exist on front door: {detail}",
            )
    except Exception as exc:
        finish_res = None
        try:
            finish_res = store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        except Exception as fe:
            finish_res = {"error": str(fe)}
        err_suffix = _inspect_error_path_call(finish_res, "finish")
        if isinstance(exc, CoverageError):
            raise CoverageError(exc.code, f"{exc.message}{err_suffix}") from None
        raise CoverageError(
            COVERAGE_FALLBACK_UNVERIFIED,
            f"nginx probe failed: {type(exc).__name__}{err_suffix}",
        ) from None
    http2_directive = bool(feat.get("http2_directive"))
    ipv6 = bool(entry.get("origin_ipv6"))

    # Fallback installation phase
    phase_res = store_client.phase(clean_zone, op_id, "fallback_install")
    _check_store_res(phase_res)
    try:
        def _before_fallback_effect(effect_name: str) -> None:
            pres = store_client.phase(clean_zone, op_id, effect_name)
            _check_store_res(pres)

        fallback_meta = fallback.install_fallback(
            entry,
            clean_zone,
            client=cf_client,
            ipv6=ipv6,
            http2_directive=http2_directive,
            ssh_run=ssh_run,
            before_effect=_before_fallback_effect,
        )
        phase_res = store_client.phase(
            clean_zone,
            op_id,
            "fallback_verified",
            updates={"fallback": fallback_meta, "exceptions": plan.get("exceptions", [])},
        )
        _check_store_res(phase_res)
    except Exception as exc:
        finish_res = None
        try:
            finish_res = store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        except Exception as fe:
            finish_res = fe
        err_suffix = _inspect_error_path_call(finish_res, "finish")
        if isinstance(exc, CoverageError):
            raise CoverageError(exc.code, f"{exc.message}{err_suffix}") from None
        if err_suffix:
            raise type(exc)(f"{exc}{err_suffix}") from exc
        raise

    if stop_before_publish:
        finish_res = store_client.finish(clean_zone, op_id, "fallback_only")
        _check_store_res(finish_res)
        return {"state": "fallback_only", "fallback": fallback_meta}

    # DNS publish phase
    phase_res = store_client.phase(clean_zone, op_id, "dns_publish")
    _check_store_res(phase_res)

    # Durable publish intent before any provider write per G1 / H1
    stored_intent = (doc.get("publish_intent") if doc else None)
    if isinstance(stored_intent, dict) and stored_intent.get("marker"):
        marker = stored_intent["marker"]
    else:
        marker = f"sandbox-zone-coverage {secrets.token_hex(8)}"

    merged_intent_records = []
    if isinstance(stored_intent, dict) and isinstance(stored_intent.get("records"), list):
        merged_intent_records.extend(stored_intent["records"])

    for r in plan.get("records_to_create", []):
        rtype = r.get("type", "A")
        rcontent = r.get("content")
        rproxied = bool(r.get("proxied", True))
        exists = any(
            ir.get("name") == r["name"]
            and ir.get("type", "A") == rtype
            and ir.get("content") == rcontent
            and bool(ir.get("proxied", True)) == rproxied
            for ir in merged_intent_records
        )
        if not exists:
            merged_intent_records.append({
                "name": r["name"],
                "type": rtype,
                "content": rcontent,
                "proxied": rproxied,
            })

    publish_intent = {
        "marker": marker,
        "records": merged_intent_records,
    }
    phase_res = store_client.phase(
        clean_zone,
        op_id,
        "publish_intent",
        updates={"publish_intent": publish_intent},
    )
    _check_store_res(phase_res)

    records_written = list(plan.get("records_owned_from_prior_attempt", []))
    records_adopted = list(plan.get("records_to_adopt", []))
    shared_records = list(plan.get("records_owned_from_prior_attempt", []))
    try:
        for r in plan.get("records_to_create", []):
            in_flight = r
            exact_records = _list_records(cf_client, plan["zone_id"], r["name"])
            rtype = r.get("type", "A")
            matching_type = [er for er in exact_records if er.get("type", "A") == rtype]
            ours = None
            adopted = None
            differing = None
            for er in matching_type:
                same_content = er.get("content") == r["content"]
                same_proxied = bool(er.get("proxied", True)) == bool(r.get("proxied", True))
                if not same_content or not same_proxied:
                    raise CoverageError(
                        COVERAGE_RECORD_EXISTS,
                        f"record {r['name']} already exists in Cloudflare with differing value",
                    )
                if _is_ours(er, publish_intent):
                    ours = er
                else:
                    adopted = er

            if ours:
                if ours not in records_written:
                    records_written.append(ours)
                shared_records.append({
                    "id": ours["id"],
                    "type": ours.get("type", rtype),
                    "content": ours.get("content", r["content"]),
                    "proxied": bool(ours.get("proxied", True)),
                })
            elif adopted:
                if adopted not in records_adopted:
                    records_adopted.append(adopted)
                shared_records.append({
                    "id": adopted["id"],
                    "type": adopted.get("type", rtype),
                    "content": adopted.get("content", r["content"]),
                    "proxied": bool(adopted.get("proxied", True)),
                })
            else:
                p_fence = store_client.phase(clean_zone, op_id, "dns_create_record")
                _check_store_res(p_fence)
                created = cf_client.create_address(
                    plan["zone_id"],
                    r["name"],
                    r["content"],
                    proxied=r.get("proxied", True),
                    comment=marker,
                )
                rec_entry = {
                    "id": created["id"],
                    "name": r["name"],
                    "content": r["content"],
                    "type": created.get("type", rtype),
                    "proxied": r.get("proxied", True),
                    "comment": marker,
                }
                records_written.append(rec_entry)
                shared_records.append({
                    "id": created["id"],
                    "type": created.get("type", rtype),
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
    except CoverageError as exc:
        finish_res = None
        try:
            finish_res = store_client.finish(clean_zone, op_id, "incomplete", interrupted_kind="enable")
        except Exception as fe:
            finish_res = {"error": str(fe)}
        err_suffix = _inspect_error_path_call(finish_res, "finish")
        if err_suffix:
            raise CoverageError(exc.code, f"{exc.message}{err_suffix}") from None
        raise
    except Exception as exc:
        unknown_names = [rw["name"] for rw in records_written]
        if "in_flight" in locals() and in_flight["name"] not in unknown_names:
            unknown_names.append(in_flight["name"])
        unknown_effects = [
            {"action": "create_record", "name": name} for name in unknown_names
        ]
        p_res = None
        try:
            p_res = store_client.phase(
                clean_zone,
                op_id,
                "dns_publish_failed",
                updates={"unknown_effects": unknown_effects},
            )
        except Exception as pe:
            p_res = pe

        f_res = None
        try:
            f_res = store_client.finish(
                clean_zone,
                op_id,
                "incomplete",
                interrupted_kind="enable",
                unknown_effects=unknown_effects,
            )
        except Exception as fe:
            f_res = fe

        err_suffix = _inspect_error_path_call(p_res, "phase") or _inspect_error_path_call(f_res, "finish")
        msg = f"failed during DNS record write loop: {type(exc).__name__}{err_suffix}"
        raise CoverageError(COVERAGE_STORE_UNAVAILABLE, msg) from None

    # Public probe check
    if probe_func is not None:
        probe_ok = probe_func(clean_zone)
    else:
        probe_ok = public_probe(clean_zone)

    if not probe_ok:
        unknown_effects = []
        intent_names = {pi["name"] for pi in publish_intent["records"]}
        adopted_ids = {r["id"] for r in (list(plan.get("records_to_adopt", [])) + records_adopted) if "id" in r}

        provider_records = []
        for n in intent_names:
            try:
                provider_records.extend(_list_records(cf_client, plan["zone_id"], n))
            except Exception as re:
                unknown_effects.append({"name": n, "reason": f"record_list_failed: {type(re).__name__}"})

        written_by_id = {wr["id"]: wr for wr in records_written if "id" in wr}

        for pr in provider_records:
            pr_id = pr.get("id")
            if pr_id in adopted_ids:
                # Never delete adopted records
                continue

            def _do_fenced_delete(rec_id: str, rec_name: str) -> bool:
                del_fence = None
                try:
                    del_fence = store_client.phase(clean_zone, op_id, "dns_rollback_delete")
                except Exception as de:
                    del_fence = de
                if (isinstance(del_fence, dict) and del_fence.get("refused")) or isinstance(del_fence, Exception):
                    unknown_effects.append({
                        "id": rec_id,
                        "name": rec_name,
                        "reason": f"delete_fenced_out: {del_fence}",
                    })
                    return False
                try:
                    cf_client.delete_record(plan["zone_id"], rec_id)
                except Exception as de:
                    unknown_effects.append({
                        "id": rec_id,
                        "name": rec_name,
                        "reason": f"delete_failed: {type(de).__name__}",
                    })
                return True

            if _is_ours(pr, publish_intent):
                if not _do_fenced_delete(pr_id, pr.get("name", "")):
                    break
            elif pr_id in written_by_id:
                unknown_effects.append({
                    "id": pr_id,
                    "name": pr.get("name"),
                    "reason": "record_modified_out_of_band",
                })
            else:
                # Third-party record without our marker: never delete!
                pass

        if not unknown_effects:
            finish_res = None
            try:
                finish_res = store_client.finish(clean_zone, op_id, "fallback_only")
            except Exception as fe:
                finish_res = fe
            err_suffix = _inspect_error_path_call(finish_res, "finish")
            raise CoverageError(
                COVERAGE_FALLBACK_UNVERIFIED,
                f"public edge probe failed to reach fallback route; rolled back created DNS records to fallback_only{err_suffix}",
            )
        else:
            finish_res = None
            try:
                finish_res = store_client.finish(
                    clean_zone,
                    op_id,
                    "incomplete",
                    interrupted_kind="enable",
                    unknown_effects=unknown_effects,
                )
            except Exception as fe:
                finish_res = fe
            err_suffix = _inspect_error_path_call(finish_res, "finish")
            raise CoverageError(
                COVERAGE_FALLBACK_UNVERIFIED,
                f"public edge probe failed and rollback had unknown effects: {unknown_effects}{err_suffix}",
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
        "records_written": records_written,
        "records_adopted": records_adopted,
        "fallback": fallback_meta,
    }


def _list_records(cf_client: Any, zone_id: str, name: str | None = None) -> list[dict[str, Any]]:
    if hasattr(cf_client, "zone_records"):
        all_recs = cf_client.zone_records(zone_id)
    else:
        all_recs = cf_client.records(zone_id, name or "")
    if name is not None:
        clean_name = name.strip().rstrip(".").lower()
        return [
            r for r in all_recs
            if str(r.get("name", "")).strip().rstrip(".").lower() == clean_name
        ]
    return list(all_recs)


def status(
    entry: dict[str, Any],
    zone: str,
    *,
    cf_client: Any = None,
    store_client: Any = None,
) -> dict[str, Any]:
    """Read coverage state and inspect drift against Cloudflare and local front door."""
    from sandbox.hosting.front_door import require_capability, ZONE_COVERAGE
    require_capability(entry, ZONE_COVERAGE)

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
            "observed": {
                "fallback": "missing",
                "shared_dns": "absent",
            },
            "safe": False,
            "history": [],
        }

    # 1. Observe shared DNS
    obs_shared_dns = "unavailable"
    drift = None
    if cf_client is None:
        from sandbox.core import _cloudflare as cloudflare
        cf_client = cloudflare.Client()
    try:
        zone_id = doc.get("zone_id") or cf_client.zone(clean_zone)["id"]
        records = _list_records(cf_client, zone_id, f"*.{clean_zone}")
        expected_records = doc.get("shared_records") or []
        expected_ids = {r.get("id") for r in expected_records if r.get("id")}
        actual_ids = {r.get("id") for r in records if r.get("id")}

        if not records:
            if not expected_records:
                obs_shared_dns = "match"
            else:
                obs_shared_dns = "absent"
        else:
            if not expected_records:
                obs_shared_dns = "changed"
            elif expected_ids != actual_ids:
                obs_shared_dns = "changed"
                drift = {
                    "reason": "dns_records_mismatch",
                    "expected": expected_records,
                    "actual": records,
                }
            else:
                is_modified = False
                for r in records:
                    exp = next((x for x in expected_records if x.get("id") == r.get("id")), None)
                    if exp and (exp.get("content") != r.get("content") or exp.get("proxied") != r.get("proxied")):
                        obs_shared_dns = "changed"
                        drift = {
                            "reason": "dns_record_modified",
                            "record_id": r.get("id"),
                            "expected": exp,
                            "actual": r,
                        }
                        is_modified = True
                        break
                if not is_modified:
                    obs_shared_dns = "match"
    except Exception:
        obs_shared_dns = "unavailable"
        drift = {"reason": "provider_error", "error": "unavailable"}

    # 2. Observe fallback route
    obs_fallback = "unavailable"
    stored_fallback = doc.get("fallback")
    route_name = f"coverage-{clean_zone}"
    try:
        route_content = nginx.read_route(entry, route_name)
        if route_content is None:
            if stored_fallback is None:
                obs_fallback = "match"
            else:
                obs_fallback = "missing"
        else:
            if stored_fallback is None:
                obs_fallback = "changed"
            else:
                import hashlib
                actual_digest = hashlib.sha256(route_content.encode()).hexdigest()
                expected_digest = stored_fallback.get("digest")
                if expected_digest and actual_digest == expected_digest:
                    obs_fallback = "match"
                else:
                    obs_fallback = "changed"
    except Exception:
        obs_fallback = "unavailable"

    safe = (obs_fallback == "match" and obs_shared_dns == "match")

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
        "observed": {
            "fallback": obs_fallback,
            "shared_dns": obs_shared_dns,
        },
        "safe": safe,
        "history": doc.get("history", []),
    }



