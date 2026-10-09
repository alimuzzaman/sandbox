"""Fail-closed admission policy for remote Docker network capacity.

The resource inventory used by ``resources status`` is intentionally a
diagnostic view.  Admission needs a smaller, stricter contract: an explicit
address-pool inventory, an explicit allocation count, and complete ownership
classification for every observed user-defined network.  A network count,
filesystem free-space value, or partial Docker response is not capacity
evidence.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


NETWORK_RESOURCE_CLASS = "docker_user_defined_network_subnet"
CAPACITY_PLAN_COMMAND = "./sb remote docker-pool REMOTE_NAME --json"
NETWORK_ALLOCATION_CONFLICT = "network_allocation_conflict"
_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}-[0-9a-f]{16,64}$")
_OWNER_CLASSES = frozenset({"sandbox", "foreign", "unattributed"})
_CAPACITY_STATES = frozenset({"complete", "partial", "unavailable"})
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
# Spec 063: development-range evidence from the admission program.
_ALLOCATION_ID = re.compile(r"^a-[0-9a-f]{16}$")
_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OWNER_KINDS = frozenset({"workspace", "job"})
MAX_ALLOCATION_TABLE = 32
MISSING_POOL_GUIDANCE = (
    "no Docker address-pool evidence and no Sandbox development range "
    "(missing evidence, not exhausted capacity); assign a range: "
)


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _opaque_id(value: Any, *, kind: str) -> str:
    """Return a stable, non-sensitive identifier for untrusted probe data."""
    if isinstance(value, str) and _OPAQUE_ID.fullmatch(value):
        return value
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()
    return f"{kind}-{digest[:20]}"


def _safe_reason(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_REASON.fullmatch(value) else fallback


def _capacity_plan_command(remote_name: str | None) -> str:
    if isinstance(remote_name, str) and re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,63}", remote_name):
        return f"./sb remote docker-pool {remote_name} --json"
    return CAPACITY_PLAN_COMMAND


def _safe_remote(remote_name: str | None) -> str | None:
    if isinstance(remote_name, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", remote_name):
        return remote_name
    return None


def _range_propose_command(remote_name: str | None) -> str:
    return f"./sb remote network-range propose {_safe_remote(remote_name) or 'REMOTE_NAME'}"


def _normalize_range(range_evidence: Any) -> dict | None:
    """Validate the program's ``range`` result; None when it is malformed.

    Grants are all-or-nothing, so a grant list is either empty or complete.
    Subnets in the grants are dropped here and never reach the envelope.
    """
    if not isinstance(range_evidence, dict):
        return None
    stats = range_evidence.get("range")
    if not isinstance(stats, dict):
        return None
    capacity, allocated, usable = (stats.get(k) for k in ("capacity", "allocated", "usable"))
    if not all(_non_negative_int(v) for v in (capacity, allocated, usable)) \
            or allocated > capacity or usable != capacity - allocated:
        return None
    granted = range_evidence.get("granted", [])
    if not isinstance(granted, list):
        return None
    ids = []
    for item in granted:
        value = item.get("allocation_id") if isinstance(item, dict) else None
        if not isinstance(value, str) or not _ALLOCATION_ID.fullmatch(value):
            return None
        ids.append(value)
    if len(set(ids)) != len(ids):
        return None
    from sandbox.services.redaction import redact_text
    rows = []
    table = range_evidence.get("table", [])
    for row in table if isinstance(table, list) else []:
        if not isinstance(row, dict):
            continue
        owner, workspace, kind, age = (row.get(k) for k in (
            "owner_id", "workspace_id", "owner_kind", "age_seconds"))
        # Secret-shaped identifiers are dropped, never echoed (SC-009).
        if (isinstance(owner, str) and _OWNER_ID.fullmatch(owner) and redact_text(owner) == owner
                and isinstance(workspace, str) and _OWNER_ID.fullmatch(workspace)
                and redact_text(workspace) == workspace
                and kind in _OWNER_KINDS and _non_negative_int(age)):
            rows.append({"owner_id": owner, "owner_kind": kind,
                         "workspace_id": workspace, "age_seconds": age})
        if len(rows) == MAX_ALLOCATION_TABLE:
            break
    return {"capacity": capacity, "allocated": allocated, "usable": usable,
            "granted": ids, "table": rows}


def _release_commands(rows: list[dict], remote_name: str | None) -> list[str]:
    remote = _safe_remote(remote_name) or "REMOTE_NAME"
    seen: list[str] = []
    for row in rows:
        if row["workspace_id"] not in seen:
            seen.append(row["workspace_id"])
    return [f"./sb workspace release {workspace} --remote {remote}" for workspace in seen]


def _blocked(
    *,
    code: str,
    state: str,
    remote_name: str | None,
    capacity: dict,
    evidence: dict | None = None,
    next_command: str | None = None,
    guidance: str | None = None,
    extra: dict | None = None,
) -> dict:
    # Remote names are deliberately not interpolated into the command.  The
    # placeholder keeps this envelope safe even when an untrusted record has a
    # path, shell metacharacter, or credential-like value in it.
    blocked = {
        "ok": False,
        "status": "blocked",
        "code": code,
        "resource_class": NETWORK_RESOURCE_CLASS,
        "resource_kind": "network",
        "owner_classes": ["sandbox", "foreign", "unattributed"],
        "target": {
            "kind": "remote",
            "remote": remote_name if isinstance(remote_name, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", remote_name)
            else None,
        },
        "capacity": capacity,
        "evidence": evidence or {"status": state},
        "recovery": {
            "automatic_cleanup": False,
            "automatic_retry": False,
            "plan": "reviewed_docker_network_capacity",
            "next_command": next_command or _capacity_plan_command(remote_name),
            "guidance": guidance or (
                "Review the bounded Docker address-pool plan and scoped "
                "Sandbox ownership evidence before retrying. Do not delete "
                "Docker networks directly or infer capacity from disk space "
                "or a raw network count."
            ),
        },
        "retryable": False,
        "side_effects": {"staging_started": False, "network_allocation_started": False},
    }
    blocked.update(extra or {})
    return blocked


def _missing_pool_evidence(remote_name: str | None, required_subnets: int) -> dict:
    return _blocked(
        code="docker_network_capacity_unavailable",
        state="partial",
        remote_name=remote_name,
        capacity={"status": "partial", "usable_subnets": None,
                  "required_subnets": required_subnets},
        evidence={"status": "partial", "reason": "missing_pool_evidence"},
        next_command=_range_propose_command(remote_name),
        guidance=MISSING_POOL_GUIDANCE + _range_propose_command(remote_name),
    )


def evaluate_network_capacity(
    evidence: Any,
    *,
    required_subnets: int = 1,
    remote_name: str | None = None,
    range_evidence: Any = None,
) -> dict:
    """Evaluate pool evidence and, when present, development-range evidence.

    Spec 063 FR-008: usable capacity is configured daemon-pool capacity plus
    unallocated range capacity; Docker's built-in default pools never count.
    One run's networks come from one source: a complete range grant admits,
    otherwise the pools must cover the run on their own. Every pool-side
    fail-closed rule still applies, except that an absent pool configuration
    is not a failure when a range exists.
    """
    if not _non_negative_int(required_subnets) or required_subnets < 1:
        raise ValueError("required_subnets must be a positive integer")
    if range_evidence is None:
        return _evaluate_pools(evidence, required_subnets=required_subnets,
                               remote_name=remote_name)
    ranged = _normalize_range(range_evidence)
    if ranged is None or (ranged["granted"] and len(ranged["granted"]) != required_subnets):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "invalid_range_evidence"},
        )
    pools = _evaluate_pools(evidence, required_subnets=required_subnets,
                            remote_name=remote_name)
    # Only a probe that passed every other check and found no configured
    # pool counts as "no pools"; every other refusal stands (FR-009).
    if pools["evidence"].get("reason") == "missing_pool_evidence":
        if ranged["capacity"] == 0:
            return pools
        pools = None
    elif pools["code"] not in (None, "docker_network_subnet_exhausted"):
        return pools
    pool_capacity = pools["capacity"] if pools else {
        "total_subnets": 0, "allocated_subnets": 0, "usable_subnets": 0, "pools": []}
    capacity = {
        "status": "complete",
        "total_subnets": pool_capacity["total_subnets"] + ranged["capacity"],
        "allocated_subnets": pool_capacity["allocated_subnets"] + ranged["allocated"],
        "usable_subnets": pool_capacity["usable_subnets"] + ranged["usable"],
        "required_subnets": required_subnets,
        "pools": pool_capacity["pools"],
        "range_usable_subnets": ranged["usable"],
    }
    if ranged["granted"] or (pools and pools["ok"]):
        summary = dict(pools["evidence"]) if pools else {
            "status": "complete", "inventory": "development_range"}
        summary["range"] = "development_range"
        admitted = _admitted(remote_name, capacity, summary)
        admitted["granted"] = ranged["granted"]
        return admitted
    return _blocked(
        code="docker_network_subnet_exhausted",
        state="complete",
        remote_name=remote_name,
        capacity=capacity,
        evidence={"status": "complete", "reason": "range_exhausted"},
        guidance=("Pool and development-range capacity are exhausted. Release a workspace "
                  "you no longer need with one of release_commands; nothing was deleted."),
        extra={"allocation_table": ranged["table"],
               "release_commands": _release_commands(ranged["table"], remote_name)},
    )


def _evaluate_pools(
    evidence: Any,
    *,
    required_subnets: int = 1,
    remote_name: str | None = None,
) -> dict:
    """Validate a bounded probe result and decide whether admission is safe.

    ``evidence`` is treated as untrusted remote data.  The evaluator only
    accepts explicit pool totals and per-owner allocation counts.  Every
    allocation is subtracted from usable capacity, including foreign and
    unattributed networks, so those resources can never be claimed as free.
    """
    if not _non_negative_int(required_subnets) or required_subnets < 1:
        raise ValueError("required_subnets must be a positive integer")
    if not isinstance(evidence, dict):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="unavailable",
            remote_name=remote_name,
            capacity={"status": "unavailable", "usable_subnets": None},
        )

    if evidence.get("ok") is False and evidence.get("code") == "docker_address_pools_unavailable":
        return _missing_pool_evidence(remote_name, required_subnets)
    if "ok" in evidence and evidence.get("ok") is not True:
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="unavailable",
            remote_name=remote_name,
            capacity={"status": "unavailable", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "unavailable", "reason": "probe_not_successful"},
        )

    state = evidence.get("status")
    if state not in _CAPACITY_STATES:
        state = "unavailable"

    # A collision is not an allocation owned by an unknown party.  It is an
    # ambiguous observation: two user-defined networks claim the same pool
    # unit, so no amount of aggregate arithmetic can establish safe capacity.
    # Keep only a bounded count in the public envelope; never echo network
    # names, IDs, subnets, or probe diagnostics.
    collisions_present = "collisions" in evidence
    collisions = evidence.get("collisions", [])
    collision_count = evidence.get("collision_count")
    if not isinstance(collisions, list) or any(
            not isinstance(item, dict) for item in collisions):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "invalid_collision_evidence"},
        )
    if collision_count is None:
        collision_count = len(collisions)
    if (not _non_negative_int(collision_count)
            or (collisions_present and collision_count != len(collisions))):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "invalid_collision_evidence"},
        )
    if collision_count:
        return _blocked(
            code=NETWORK_ALLOCATION_CONFLICT,
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": NETWORK_ALLOCATION_CONFLICT,
                      "collision_count": collision_count},
        )

    pools = evidence.get("pools")
    totals = evidence.get("totals")
    ownership = evidence.get("ownership")
    if state != "complete" or not isinstance(pools, list) or not isinstance(totals, dict):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state=state,
            remote_name=remote_name,
            capacity={
                "status": state,
                "usable_subnets": None,
                "required_subnets": required_subnets,
                "total_subnets": totals.get("total_subnets")
                if isinstance(totals, dict) and _non_negative_int(totals.get("total_subnets"))
                else None,
            },
            evidence={"status": state,
                      "reason": _safe_reason(evidence.get("reason"), "probe_incomplete")},
        )

    total = totals.get("total_subnets")
    allocated = totals.get("allocated_subnets")
    usable = totals.get("usable_subnets")
    if not all(_non_negative_int(value) for value in (total, allocated, usable)):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "invalid_capacity_totals"},
        )
    if allocated > total or usable != total - allocated:
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "inconsistent_capacity_totals"},
        )

    if not pools:
        if total:
            return _blocked(
                code="docker_network_capacity_unavailable",
                state="partial",
                remote_name=remote_name,
                capacity={"status": "partial", "usable_subnets": None,
                          "required_subnets": required_subnets},
                evidence={"status": "partial", "reason": "inconsistent_pool_totals"},
            )
        return _missing_pool_evidence(remote_name, required_subnets)

    normalized_pools: list[dict] = []
    pool_ids: set[str] = set()
    for item in pools:
        if not isinstance(item, dict):
            return _blocked(
                code="docker_network_capacity_unavailable",
                state="partial",
                remote_name=remote_name,
                capacity={"status": "partial", "usable_subnets": None,
                          "required_subnets": required_subnets},
                evidence={"status": "partial", "reason": "invalid_pool_evidence"},
            )
        if "pool_id" not in item:
            return _blocked(
                code="docker_network_capacity_unavailable",
                state="partial",
                remote_name=remote_name,
                capacity={"status": "partial", "usable_subnets": None,
                          "required_subnets": required_subnets},
                evidence={"status": "partial", "reason": "invalid_pool_evidence"},
            )
        pool_total = item.get("capacity_subnets")
        pool_allocated = item.get("allocated_subnets")
        pool_usable = item.get("usable_subnets")
        if not all(_non_negative_int(value) for value in (
            pool_total, pool_allocated, pool_usable,
        )) or pool_allocated > pool_total or pool_usable != pool_total - pool_allocated:
            return _blocked(
                code="docker_network_capacity_unavailable",
                state="partial",
                remote_name=remote_name,
                capacity={"status": "partial", "usable_subnets": None,
                          "required_subnets": required_subnets},
                evidence={"status": "partial", "reason": "invalid_pool_capacity"},
            )
        pool_id = _opaque_id(item.get("pool_id"), kind="pool")
        if pool_id in pool_ids:
            return _blocked(
                code="docker_network_capacity_unavailable",
                state="partial",
                remote_name=remote_name,
                capacity={"status": "partial", "usable_subnets": None,
                          "required_subnets": required_subnets},
                evidence={"status": "partial", "reason": "ambiguous_pool_evidence"},
            )
        pool_ids.add(pool_id)
        normalized_pools.append({
            "pool_id": pool_id,
            "capacity_subnets": pool_total,
            "allocated_subnets": pool_allocated,
            "usable_subnets": pool_usable,
        })

    normalized_ownership = {
        "sandbox_allocated_subnets": 0,
        "foreign_allocated_subnets": 0,
        "unattributed_allocated_subnets": 0,
    }
    if isinstance(ownership, dict):
        for owner in _OWNER_CLASSES:
            field = f"{owner}_allocated_subnets"
            value = ownership.get(field, 0)
            if not _non_negative_int(value):
                return _blocked(
                    code="docker_network_capacity_unavailable",
                    state="partial",
                    remote_name=remote_name,
                    capacity={"status": "partial", "usable_subnets": None,
                              "required_subnets": required_subnets},
                    evidence={"status": "partial", "reason": "invalid_ownership_evidence"},
                )
            normalized_ownership[field] = value
    if sum(normalized_ownership.values()) != allocated:
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "ownership_does_not_cover_allocations"},
        )

    pool_total = sum(item["capacity_subnets"] for item in normalized_pools)
    pool_allocated = sum(item["allocated_subnets"] for item in normalized_pools)
    pool_usable = sum(item["usable_subnets"] for item in normalized_pools)
    if (pool_total, pool_allocated, pool_usable) != (total, allocated, usable):
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "inconsistent_pool_totals"},
        )

    capacity = {
        "status": "complete",
        "total_subnets": total,
        "allocated_subnets": allocated,
        "usable_subnets": usable,
        "required_subnets": required_subnets,
        "pools": normalized_pools,
    }
    evidence_summary = {
        "status": "complete",
        "inventory": "address_pools_and_network_ipam",
        "ownership": normalized_ownership,
    }
    if usable < required_subnets:
        return _blocked(
            code="docker_network_subnet_exhausted",
            state="complete",
            remote_name=remote_name,
            capacity=capacity,
            evidence=evidence_summary,
        )
    return _admitted(remote_name, capacity, evidence_summary)


def _admitted(remote_name: str | None, capacity: dict, evidence_summary: dict) -> dict:
    return {
        "ok": True,
        "status": "admitted",
        "code": None,
        "resource_class": NETWORK_RESOURCE_CLASS,
        "resource_kind": "network",
        "owner_classes": ["sandbox", "foreign", "unattributed"],
        "target": {
            "kind": "remote",
            "remote": remote_name if isinstance(remote_name, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", remote_name)
            else None,
        },
        "capacity": capacity,
        "evidence": evidence_summary,
        "recovery": {
            "automatic_cleanup": False,
            "automatic_retry": False,
            "plan": "reviewed_docker_network_capacity",
            "next_command": _capacity_plan_command(remote_name),
            "guidance": "Explicit subnet capacity was observed; continue with the bounded remote operation.",
        },
        "retryable": False,
        "side_effects": {"staging_started": False, "network_allocation_started": False},
    }


def network_capacity_admission(
    evidence: Any,
    *,
    required_subnets: int = 1,
    remote_name: str | None = None,
) -> dict:
    """Compatibility spelling for callers that treat admission as a policy."""
    return evaluate_network_capacity(
        evidence, required_subnets=required_subnets, remote_name=remote_name,
    )
