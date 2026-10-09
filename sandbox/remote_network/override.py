"""Compose override placing a stack's networks in granted range subnets.

Spec 063 research R1: for every network the effective Compose config creates,
the override sets ``networks.<name>.ipam.config: [{subnet}]`` and is passed
last in the ``-f`` chain. External networks, and networks the project already
pins with its own IPAM, are left alone and reported ``outside_range``.
"""
from __future__ import annotations

import ipaddress
import re

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_WORKSPACE_RUNS = frozenset({"workspace", "job", "preview", "exec", "ci_cell"})
_COLLISION = re.compile(r"pool overlaps", re.IGNORECASE)


class OverrideError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def compose_networks(config: dict) -> tuple[list[str], list[str]]:
    """``(created, outside_range)`` network keys of ``docker compose config`` JSON."""
    networks = config.get("networks") if isinstance(config, dict) else None
    if not isinstance(networks, dict):
        return [], []
    created, outside = [], []
    for name, spec in networks.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise OverrideError("range_override_invalid", "compose network name is invalid")
        spec = spec if isinstance(spec, dict) else {}
        ipam = spec.get("ipam") if isinstance(spec.get("ipam"), dict) else {}
        if spec.get("external") or ipam.get("config"):
            outside.append(name)
        else:
            created.append(name)
    return sorted(created), sorted(outside)


def plan(config: dict, granted: dict[str, str]) -> dict:
    """The override text for ``granted`` (network -> subnet); every created network needs one."""
    created, outside = compose_networks(config)
    missing = [name for name in created if name not in granted]
    if missing:
        raise OverrideError("range_allocation_incomplete",
                            "a network the stack creates has no granted subnet")
    lines = ["networks:"] if created else []
    for name in created:
        try:
            subnet = ipaddress.IPv4Network(granted[name], strict=True)
        except (TypeError, ValueError):
            raise OverrideError("range_override_invalid", "granted subnet is invalid") from None
        lines += [f"  {name}:", "    ipam:", "      config:", f"        - subnet: {subnet}"]
    return {"text": "\n".join(lines) + "\n" if lines else "", "covered": created,
            "outside_range": outside}


def owner(run: str, *, workspace_id: str, job_id: str | None) -> tuple[str, str]:
    """Owner kind and id per the data-model mapping (FR-004)."""
    if run in _WORKSPACE_RUNS:
        return "workspace", workspace_id
    if run == "job_stack" and job_id:
        return "job", job_id
    raise OverrideError("range_request_invalid", "run kind has no range owner")


def classify_create_failure(stderr: str) -> dict | None:
    """A typed, non-retryable refusal when Docker reports a subnet collision."""
    if isinstance(stderr, str) and _COLLISION.search(stderr):
        return {"code": "range_network_collision", "retryable": False,
                "message": "a granted subnet collides with a network Sandbox did not observe; "
                           "run ./sb remote network-range list and propose a new range"}
    return None
