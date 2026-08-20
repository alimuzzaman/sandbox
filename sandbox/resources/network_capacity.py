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


def _blocked(
    *,
    code: str,
    state: str,
    remote_name: str | None,
    capacity: dict,
    evidence: dict | None = None,
) -> dict:
    # Remote names are deliberately not interpolated into the command.  The
    # placeholder keeps this envelope safe even when an untrusted record has a
    # path, shell metacharacter, or credential-like value in it.
    return {
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
            "next_command": _capacity_plan_command(remote_name),
            "guidance": (
                "Review the bounded Docker address-pool plan and scoped "
                "Sandbox ownership evidence before retrying. Do not delete "
                "Docker networks directly or infer capacity from disk space "
                "or a raw network count."
            ),
        },
        "retryable": False,
        "side_effects": {"staging_started": False, "network_allocation_started": False},
    }


def evaluate_network_capacity(
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
        return _blocked(
            code="docker_network_capacity_unavailable",
            state="partial",
            remote_name=remote_name,
            capacity={"status": "partial", "usable_subnets": None,
                      "required_subnets": required_subnets},
            evidence={"status": "partial", "reason": "missing_pool_evidence"},
        )

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
