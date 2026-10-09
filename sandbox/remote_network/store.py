"""Development ranges on one remote, reached through an injected ``ssh_run``.

Every call is bounded (15 s), output is validated before use, and transport
detail is never forwarded (spec 063 contract, SC-009).
"""
from __future__ import annotations

import json
import re
from typing import Callable

from sandbox.remote_network import program, ranges
from sandbox.remote_network.ranges import RangeError
from sandbox.remote_runtime.protocol import ControlProtocol

TIMEOUT_SECONDS = 15
# Lowest installed control protocol whose runtime admission and Compose
# override use ranges (research R10). Raised to the bumped number in T013a.
RANGES_PROTOCOL = 2
STORE_UNAVAILABLE = "range_store_unavailable"
RUNTIME_UNSUPPORTED = "range_runtime_unsupported"

_ALLOCATION_ID = re.compile(r"a-[0-9a-f]{16}")
_RANGE_ID = re.compile(r"r-[0-9a-f]{12}")


def migrate_remedy(remote: str) -> str:
    from sandbox.remote_runtime.refusal import migrate_confirm_remedy, remote_name_or_none
    name = remote_name_or_none(remote)
    return migrate_confirm_remedy(name) if name else "./sb remote service migrate <remote> --confirm"


def assign_command(remote: str, dev_range: ranges.DevRange) -> str:
    import shlex
    return shlex.join(["./sb", "remote", "network-range", "assign", remote,
                       "--cidr", str(dev_range.cidr),
                       "--subnet-prefix", str(dev_range.subnet_prefix), "--confirm"])


class RangeStore:
    def __init__(self, entry: dict, ssh_run: Callable | None = None, *,
                 installed_protocol: ControlProtocol | None = None):
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        self.entry = entry
        self.name = str(entry.get("name") or "")
        self._ssh_run = ssh_run
        self.installed_protocol = installed_protocol

    def _call(self, request: dict) -> dict:
        try:
            result = self._ssh_run(self.entry, program.remote_command(request),
                                   timeout=TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - transport detail is never forwarded
            raise RangeError(STORE_UNAVAILABLE, "remote range store is unreachable") from exc
        stdout = getattr(result, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if getattr(result, "returncode", 1) != 0 or not isinstance(stdout, str):
            raise RangeError(STORE_UNAVAILABLE, "remote range program failed")
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise RangeError(STORE_UNAVAILABLE, "remote range program output is invalid") from None
        if not isinstance(value, dict) or value.get("op") != request["op"]:
            raise RangeError(STORE_UNAVAILABLE, "remote range program output is invalid")
        if value.get("ok") is not True:
            code = value.get("code")
            data = value.get("data") if isinstance(value.get("data"), dict) else {}
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{3,48}", code):
                raise RangeError(STORE_UNAVAILABLE, "remote range program output is invalid")
            safe = {k: v for k, v in data.items()
                    if k in ("class", "range_id") and isinstance(v, str)}
            raise RangeError(code, str(value.get("message") or code)[:200], **safe)
        return value

    def _require_runtime(self) -> None:
        protocol = self.installed_protocol
        if protocol is None or protocol.spoken < RANGES_PROTOCOL:
            raise RangeError(
                RUNTIME_UNSUPPORTED,
                "the installed remote runtime predates development ranges; migrate it first",
                remedy=migrate_remedy(self.name),
            )

    def inventory(self) -> ranges.Inventory:
        return ranges.inventory_from_payload(self._call({"op": "inventory"}).get("inventory"))

    def list(self) -> dict:
        value = self._call({"op": "list"})
        rows = value.get("allocations")
        listed = value.get("ranges")
        if not isinstance(rows, list) or not isinstance(listed, list) \
                or len(rows) > program.MAX_LISTED or len(listed) > program.MAX_RANGES:
            raise RangeError(STORE_UNAVAILABLE, "remote range listing is invalid")
        for row in listed:
            if not isinstance(row, dict) or not _RANGE_ID.fullmatch(str(row.get("range_id"))):
                raise RangeError(STORE_UNAVAILABLE, "remote range listing is invalid")
        for row in rows:
            if not isinstance(row, dict) or not _ALLOCATION_ID.fullmatch(str(row.get("allocation_id"))):
                raise RangeError(STORE_UNAVAILABLE, "remote range listing is invalid")
        from sandbox.services.redaction import redact_structure
        return redact_structure({
            "ranges": listed, "allocations": rows,
            "capacity_proof": value.get("capacity_proof") if isinstance(value.get("capacity_proof"), dict) else None,
            "truncated": value.get("truncated") is True,
        })

    def assigned(self) -> list[ranges.DevRange]:
        return [ranges.parse_range(r["cidr"], r["subnet_prefix"]) for r in self.list()["ranges"]]

    def propose(self) -> dict:
        """Read-only: a range satisfying FR-002 and the exact assign command."""
        dev_range = ranges.propose(self.inventory(), self.assigned())
        return {"proposed": str(dev_range.cidr), "subnet_prefix": dev_range.subnet_prefix,
                "capacity": dev_range.capacity,
                "assign_command": assign_command(self.name, dev_range)}

    def assign(self, cidr: str, subnet_prefix: int = ranges.DEFAULT_SUBNET_PREFIX, *,
               confirm: bool = False, holder: str | None = None) -> dict:
        dev_range = ranges.parse_range(cidr, subnet_prefix)
        if not confirm:
            noop = ranges.check_assignment(dev_range, self.inventory(), self.assigned())
            return {"status": "planned", "range_id": dev_range.range_id,
                    "capacity": dev_range.capacity, "already_assigned": noop,
                    "confirm_command": assign_command(self.name, dev_range)}
        self._require_runtime()
        if holder is None:
            from sandbox.remote_runtime.pins import local_holder
            holder = local_holder()[0]
        value = self._call({"op": "assign", "cidr": str(dev_range.cidr),
                            "subnet_prefix": dev_range.subnet_prefix, "holder": holder})
        return {"status": "assigned" if value.get("assigned") else "unchanged",
                "range_id": dev_range.range_id, "capacity": dev_range.capacity}

    def allocate(self, *, owner_kind: str, owner_id: str, workspace_id: str,
                 networks: list[str], pool_capacity: int | None = None) -> dict:
        self._require_runtime()
        value = self._call({"op": "allocate", "owner_kind": owner_kind, "owner_id": owner_id,
                            "workspace_id": workspace_id, "networks": list(networks),
                            "pool_capacity": pool_capacity})
        granted = value.get("granted")
        if not isinstance(granted, list) or not all(
                isinstance(g, dict) and _ALLOCATION_ID.fullmatch(str(g.get("allocation_id")))
                for g in granted):
            raise RangeError(STORE_UNAVAILABLE, "remote allocation output is invalid")
        table = value.get("table") if isinstance(value.get("table"), list) else []
        return {"granted": granted, "exhausted": value.get("exhausted") is True,
                "no_range": value.get("no_range") is True,
                "range": value.get("range") if isinstance(value.get("range"), dict) else {},
                "table": table[:program.MAX_TABLE]}

    def release_owner(self, *, owner_id: str | None = None,
                      workspace_id: str | None = None) -> int:
        request = {"op": "release-owner"}
        if owner_id is not None:
            request["owner_id"] = owner_id
        if workspace_id is not None:
            request["workspace_id"] = workspace_id
        released = self._call(request).get("released")
        return released if type(released) is int and released >= 0 else 0
