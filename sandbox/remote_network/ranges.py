"""Development range validation, overlap classification and proposal.

Pure functions: the remote program supplies the inventory, this module
decides. See specs/063-remote-development-readiness (research R3, data-model).
"""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Iterable, Iterator

CIDR_PREFIX_MIN = 12
CIDR_PREFIX_MAX = 24
SUBNET_PREFIX_MIN = 24
SUBNET_PREFIX_MAX = 29
DEFAULT_SUBNET_PREFIX = 26

PROPOSAL_SPACE = ipaddress.IPv4Network("10.200.0.0/14")
PROPOSAL_PREFIX = 20
CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
# Docker's documented built-in default address pools.
DOCKER_DEFAULT_POOLS = tuple(
    [ipaddress.IPv4Network(f"172.{n}.0.0/16") for n in range(17, 32)]
    + [ipaddress.IPv4Network("192.168.0.0/16")]
)

# Overlap classes, checked in this order (FR-002).
DOCKER_NETWORK = "docker_network"
HOST_ROUTE = "host_route"
DOCKER_DEFAULT_POOL = "docker_default_pool"
CGNAT_CLASS = "cgnat"

INVENTORY_COMPLETE = "complete"
INVENTORY_PARTIAL = "partial"


class RangeError(ValueError):
    """Typed refusal; ``code`` is the public reason, ``data`` is secret-free."""

    def __init__(self, code: str, message: str, **data):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class DevRange:
    cidr: ipaddress.IPv4Network
    subnet_prefix: int

    @property
    def range_id(self) -> str:
        return range_id(str(self.cidr))

    @property
    def capacity(self) -> int:
        return 2 ** (self.subnet_prefix - self.cidr.prefixlen)

    def subnets(self) -> Iterator[ipaddress.IPv4Network]:
        return self.cidr.subnets(new_prefix=self.subnet_prefix)


@dataclass(frozen=True)
class Inventory:
    status: str
    networks: tuple[ipaddress.IPv4Network, ...] = ()
    routes: tuple[ipaddress.IPv4Network, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == INVENTORY_COMPLETE


def range_id(cidr: str) -> str:
    return "r-" + hashlib.sha256(cidr.encode()).hexdigest()[:12]


def parse_range(cidr, subnet_prefix=DEFAULT_SUBNET_PREFIX) -> DevRange:
    """Validate an operator-supplied range; raise ``range_invalid``."""
    if not isinstance(cidr, str):
        raise RangeError("range_invalid", "range must be an IPv4 CIDR string")
    try:
        network = ipaddress.IPv4Network(cidr.strip(), strict=True)
    except (ValueError, TypeError):
        # Fixed text: the rejected input may be a path or a secret (SC-009).
        raise RangeError("range_invalid", "range is not an IPv4 network address with "
                         "host bits clear, e.g. 10.200.0.0/20") from None
    if "/" not in cidr:
        raise RangeError("range_invalid", "range must carry an explicit prefix")
    if not CIDR_PREFIX_MIN <= network.prefixlen <= CIDR_PREFIX_MAX:
        raise RangeError(
            "range_invalid",
            f"range prefix must be /{CIDR_PREFIX_MIN}../{CIDR_PREFIX_MAX}",
        )
    if isinstance(subnet_prefix, bool) or not isinstance(subnet_prefix, int):
        raise RangeError("range_invalid", "subnet prefix must be an integer")
    if not SUBNET_PREFIX_MIN <= subnet_prefix <= SUBNET_PREFIX_MAX:
        raise RangeError(
            "range_invalid",
            f"subnet prefix must be {SUBNET_PREFIX_MIN}..{SUBNET_PREFIX_MAX}",
        )
    if subnet_prefix < network.prefixlen:
        raise RangeError("range_invalid", "subnet prefix must not be shorter than the range prefix")
    return DevRange(network, subnet_prefix)


def _networks(values: Iterable) -> tuple[ipaddress.IPv4Network, ...]:
    out = []
    for value in values or ():
        try:
            out.append(ipaddress.IPv4Network(str(value), strict=False))
        except (ValueError, TypeError):
            continue  # IPv6 and malformed entries are not IPv4 conflicts.
    return tuple(out)


def inventory_from_payload(payload) -> Inventory:
    """Build an inventory from the program's ``inventory`` output.

    Anything that is not an explicit complete inventory is partial.
    """
    if not isinstance(payload, dict):
        return Inventory(INVENTORY_PARTIAL)
    status = INVENTORY_COMPLETE if payload.get("status") == INVENTORY_COMPLETE else INVENTORY_PARTIAL
    # A default route (prefix 0) covers everything and is not a conflict.
    routes = tuple(r for r in _networks(payload.get("routes")) if r.prefixlen > 0)
    return Inventory(status, _networks(payload.get("networks")), routes)


def overlap_class(network: ipaddress.IPv4Network, inventory: Inventory) -> str | None:
    """First FR-002 class ``network`` overlaps, or None; partial refuses."""
    if not inventory.complete:
        raise RangeError(
            "range_inventory_unknown",
            "the remote network inventory is partial; nothing was recorded",
        )
    for cls, candidates in (
        (DOCKER_NETWORK, inventory.networks),
        (HOST_ROUTE, inventory.routes),
        (DOCKER_DEFAULT_POOL, DOCKER_DEFAULT_POOLS),
        (CGNAT_CLASS, (CGNAT,)),
    ):
        if any(network.overlaps(candidate) for candidate in candidates):
            return cls
    return None


def check_assignment(dev_range: DevRange, inventory: Inventory,
                     assigned: Iterable[DevRange] = ()) -> bool:
    """Return True when ``dev_range`` is already assigned (a no-op).

    Raises ``range_conflict`` against an assigned range and ``range_overlap``
    against the inventory; returns False when the range may be recorded.
    """
    for existing in assigned:
        if existing.cidr == dev_range.cidr:
            if existing.subnet_prefix == dev_range.subnet_prefix:
                return True
            raise RangeError(
                "range_conflict",
                "range is already assigned with a different subnet prefix",
                range_id=existing.range_id,
            )
        if existing.cidr.overlaps(dev_range.cidr):
            raise RangeError(
                "range_conflict", "range overlaps an assigned development range",
                range_id=existing.range_id,
            )
    cls = overlap_class(dev_range.cidr, inventory)
    if cls is not None:
        raise RangeError("range_overlap", f"range overlaps a {cls.replace('_', ' ')}", **{"class": cls})
    return False


def propose(inventory: Inventory, assigned: Iterable[DevRange] = ()) -> DevRange:
    """First free /20 in 10.200.0.0/14 with the default subnet size."""
    assigned = tuple(assigned)
    for candidate in PROPOSAL_SPACE.subnets(new_prefix=PROPOSAL_PREFIX):
        dev_range = DevRange(candidate, DEFAULT_SUBNET_PREFIX)
        if any(existing.cidr.overlaps(candidate) for existing in assigned):
            continue
        if overlap_class(candidate, inventory) is None:
            return dev_range
    raise RangeError(
        "range_proposal_unavailable",
        f"no free /{PROPOSAL_PREFIX} inside {PROPOSAL_SPACE}; assign a range explicitly",
    )


def free_subnets(ranges: Iterable[DevRange], used: Iterable[str]) -> Iterator[tuple[DevRange, ipaddress.IPv4Network]]:
    """Unallocated subnets across ``ranges`` in a stable order."""
    taken = set(_networks(used))
    for dev_range in ranges:
        for subnet in dev_range.subnets():
            if subnet not in taken:
                yield dev_range, subnet
