"""Exposure admission and one finite, anonymous public observation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from sandbox.config.delivery import (
    DeliveryRouteError, deadline_seconds, normalize_delivery,
    wordpress_delivery_contract,
)
from sandbox.hosting.recovery.models import canonical_digest


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value):
    return canonical_digest(value)


def _hostname(value):
    if type(value) is not str or len(value) > 253:
        raise DeliveryRouteError()
    # Public origins use DNS names, never IP literals or a forced Host header.
    host = value.lower()
    if host != value or "." not in host or re.fullmatch(r"[0-9.]+", host):
        raise DeliveryRouteError()
    if any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in host.split(".")):
        raise DeliveryRouteError()
    return host


def _binding(raw):
    if type(raw) is not dict or set(raw) != {"target_digest", "config_digest", "release_digest"} or any(type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in raw.values()):
        raise DeliveryRouteError("delivery_edge_proof_required")
    return dict(raw)


def prepare_route_verification(delivery, *, runtime_kind, primary_hostname,
                               aliases=(), verify_timeout=None,
                               application_commit=None, artifact_digest=None,
                               edge_required=False, edge_supported=False,
                               edge_binding=None):
    """Freeze the nonsecret exposure contract before route/DNS effects.

    The caller supplies runtime kind and edge requirements from their existing
    owners. Config cannot turn off an owner-required edge proof. This function
    never reads owners, credentials, environments, files, or networks.
    """
    normalized = normalize_delivery(delivery)
    if normalized is None:
        if runtime_kind not in {"wordpress", "wp"}:
            raise DeliveryRouteError("delivery_route_contract_required")
        normalized = wordpress_delivery_contract()
    primary = _hostname(primary_hostname)
    if type(aliases) not in {list, tuple} or len(aliases) > 19:
        raise DeliveryRouteError()
    hosts = [primary] + [_hostname(host) for host in aliases]
    if len(set(hosts)) != len(hosts):
        raise DeliveryRouteError()
    if verify_timeout is not None:
        normalized["routes"]["deadlineSeconds"] = deadline_seconds(verify_timeout)
    if type(edge_required) is not bool or type(edge_supported) is not bool:
        raise DeliveryRouteError()
    required = edge_required or normalized["routes"]["edgeProof"]["required"]
    normalized["routes"]["edgeProof"]["required"] = required
    if required and (not edge_supported or edge_binding is None):
        raise DeliveryRouteError("delivery_edge_proof_required")
    binding = _binding(edge_binding) if edge_binding is not None else None
    release = normalized["routes"]["releaseIdentity"]
    expected = None
    if "expectedFrom" in release:
        expected = application_commit if release["expectedFrom"] == "application_commit" else artifact_digest
        pattern = r"(?:[0-9a-f]{40}|[0-9a-f]{64})" if release["expectedFrom"] == "application_commit" else r"sha256:[0-9a-f]{64}"
        if expected is not None and (type(expected) is not str or re.fullmatch(pattern, expected) is None):
            raise DeliveryRouteError("delivery_release_identity_required")
    if release["required"] and expected is None:
        raise DeliveryRouteError("delivery_release_identity_required")
    result = {"schemaVersion": 1, "delivery": normalized, "primary_hostname": primary, "hostnames": hosts, "release_expected": expected, "edge_binding": binding}
    result["contract_digest"] = _digest(result)
    # Reserve envelope space for the monotonic worker deadline.
    if len(_encode(result)) > 32000:
        raise DeliveryRouteError()
    return result


def _edge(contract, evidence):
    required = contract["delivery"]["routes"]["edgeProof"]["required"]
    if evidence is None:
        return {"result": "incomplete" if required else "not_applicable", "reason": "edge_proof_missing" if required else "edge_proof_not_required"}
    keys = {"target_digest", "config_digest", "release_digest", "result", "observed_at", "proof_digest"}
    if type(evidence) is not dict or set(evidence) != keys:
        return {"result": "incomplete", "reason": "edge_proof_invalid"}
    try:
        binding = _binding({key: evidence[key] for key in ("target_digest", "config_digest", "release_digest")})
        stamp = evidence["observed_at"]
        if type(stamp) is not str or len(stamp) > 40 or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", stamp) is None:
            raise ValueError()
        parsed = datetime.fromisoformat(stamp[:-1] + "+00:00")
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
            raise ValueError()
        if type(evidence["proof_digest"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["proof_digest"]):
            raise ValueError()
        if contract["edge_binding"] is None or binding != contract["edge_binding"]:
            return {"result": "incomplete", "reason": "edge_proof_identity_mismatch"}
        if evidence["result"] not in {"passed", "failed", "pending", "stale", "unknown"}:
            raise ValueError()
    except (ValueError, TypeError):
        return {"result": "incomplete", "reason": "edge_proof_invalid"}
    return {"result": "passed" if evidence["result"] == "passed" else "failed" if evidence["result"] == "failed" else "incomplete", "reason": "edge_proof_" + evidence["result"], "observed_at": stamp, "proof_digest": evidence["proof_digest"]}


def observe_routes(prepared, *, edge_evidence=None):
    """Observe once; never mutate routes or retry outside the bounded worker.

    edge_evidence is an injected owner projection, not user-provided authority.
    The owner must already have checked freshness and exact active generation.
    """
    start = time.monotonic()
    started_at = _now()
    # Detach and reject accidental mutation of the frozen admission contract.
    if type(prepared) is not dict or set(prepared) != {"schemaVersion", "delivery", "primary_hostname", "hostnames", "release_expected", "edge_binding", "contract_digest"}:
        raise DeliveryRouteError()
    contract = json.loads(_encode(prepared))
    digest = contract.pop("contract_digest", None)
    if digest != _digest(contract):
        raise DeliveryRouteError()
    contract["contract_digest"] = digest
    try:
        release = contract["delivery"]["routes"]["releaseIdentity"]
        validated = prepare_route_verification(
            contract["delivery"], runtime_kind="custom",
            primary_hostname=contract["primary_hostname"],
            aliases=contract["hostnames"][1:],
            application_commit=contract["release_expected"] if release.get("expectedFrom") == "application_commit" else None,
            artifact_digest=contract["release_expected"] if release.get("expectedFrom") == "artifact_digest" else None,
            edge_supported=contract["edge_binding"] is not None,
            edge_binding=contract["edge_binding"],
        )
        if validated != contract:
            raise DeliveryRouteError()
    except (TypeError, KeyError, AttributeError, IndexError):
        raise DeliveryRouteError() from None
    budget = deadline_seconds(contract["delivery"]["routes"]["deadlineSeconds"])
    deadline = start + budget
    result = {"schemaVersion": 1, "contract_digest": digest, "hostnames": contract["hostnames"], "deadline_seconds": budget, "started_at": started_at, "result": "incomplete", "hosts": [], "edge": _edge(contract, edge_evidence), "scope": "application_and_release" if contract["release_expected"] is not None else "application_availability_only", "release_identity_state": "declared" if contract["release_expected"] is not None else "unsupported"}
    process = None
    try:
        # Reserve cleanup time inside the aggregate deadline, including startup.
        payload = _encode({"contract": contract, "deadline": deadline - 1.0})
        if len(payload) > 32768:
            raise DeliveryRouteError()
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).with_name("route_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
            close_fds=True, start_new_session=True,
        )
        remaining = deadline - time.monotonic() - 1.0
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, 0)
        output, _ = process.communicate(payload, timeout=remaining)
        if process.returncode != 0 or len(output) > 131072:
            raise ValueError()
        observation = json.loads(output)
        hosts = observation.get("hosts")
        if observation.get("error") or type(hosts) is not list or len(hosts) != len(contract["hostnames"]) or [row.get("hostname") for row in hosts] != contract["hostnames"]:
            raise ValueError()
        result["hosts"] = hosts
        states = []
        for host in hosts:
            states.append(host["dns"]["result"])
            if len(host["checks"]) != len(contract["delivery"]["routes"]["checks"]):
                states.append("incomplete")
            states.extend(check["result"] for check in host["checks"])
            release = host["release_identity"]
            if contract["delivery"]["routes"]["releaseIdentity"]["required"]:
                states.append(release.get("result", "incomplete"))
        if contract["release_expected"] is not None:
            release_states = [host["release_identity"].get("result", "incomplete") for host in hosts]
            result["release_identity_state"] = "passed" if all(state == "passed" for state in release_states) else "failed" if "failed" in release_states else "incomplete"
            if result["release_identity_state"] != "passed":
                result["scope"] = "application_availability_only"
        if contract["delivery"]["routes"]["edgeProof"]["required"]:
            states.append(result["edge"]["result"])
        result["result"] = "failed" if "failed" in states else "verified" if states and all(state == "passed" for state in states) else "incomplete"
    except subprocess.TimeoutExpired:
        result["reason"] = "deadline_exceeded"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        result["reason"] = "route_observation_unavailable"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            # Worker cannot spawn children. Kill and wait include the reserved
            # second; no worker is left executing after this function returns.
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                result.update(result="incomplete", reason="worker_reap_unconfirmed")
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
    result["finished_at"] = _now()
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    if time.monotonic() > deadline:
        result.update(result="incomplete", reason="deadline_exceeded")
    if len(_encode(result)) > 65536:
        result.update(result="incomplete", reason="route_evidence_too_large", hosts=[])
    return result
