"""Refuse a hostname another project already holds on the same remote (spec 071).

Holdings come from controller-side hosting state, which every apply already
loads under its locks, plus the stack registry. Older targets never recorded
their hostnames, so their DNS rows and shared-coverage hostname stand in.
"""
from __future__ import annotations

from typing import Iterable

from sandbox.core._hosting import HostingRefusal

HOSTNAME_OWNED = "hostname_owned_by_other_project"


class HostnameOwnedError(HostingRefusal):
    def __init__(self, hostname: str, owner: str, project: str):
        super().__init__(
            HOSTNAME_OWNED,
            f"hostname {hostname} is already held by project {owner} on this remote; "
            f"project {project} cannot claim it (destroy or rename the other target first)")
        self.hostname = hostname
        self.owner = owner


def _normal(hostname: object) -> str | None:
    if not isinstance(hostname, str) or not hostname.strip():
        return None
    return hostname.strip().rstrip(".").lower()


def _target_hostnames(record: dict) -> set[str]:
    names: set[str] = set()
    for value in record.get("hostnames") or []:
        if (name := _normal(value)):
            names.add(name)
    coverage = record.get("dns_coverage")
    if isinstance(coverage, dict) and (name := _normal(coverage.get("hostname"))):
        names.add(name)
    for row in record.get("records") or []:
        applied = row.get("applied") if isinstance(row, dict) else None
        if isinstance(applied, dict) and (name := _normal(applied.get("name"))):
            names.add(name)
    return names


def holdings(state: dict, remote_name: str, stacks: Iterable[dict] = ()) -> dict[str, set[str]]:
    """Map each hostname held on ``remote_name`` to the projects holding it."""
    held: dict[str, set[str]] = {}
    prefix = f"{remote_name}/"
    for key, record in (state.get("hosts") or {}).items():
        if not isinstance(key, str) or not key.startswith(prefix) or not isinstance(record, dict):
            continue
        parts = key.split("/")
        if len(parts) != 3:
            continue
        for name in _target_hostnames(record):
            held.setdefault(name, set()).add(parts[1])
    for stack in stacks:
        if not isinstance(stack, dict) or stack.get("remote") != remote_name:
            continue
        project = stack.get("project")
        if not isinstance(project, str):
            continue
        for value in stack.get("hostnames") or []:
            if (name := _normal(value)):
                held.setdefault(name, set()).add(project)
    return held


def assert_available(state: dict, remote_name: str, project: str, hostnames: Iterable[str],
                     stacks: Iterable[dict] = ()) -> None:
    """Raise ``HostnameOwnedError`` for the first hostname another project holds."""
    held = holdings(state, remote_name, stacks)
    for hostname in sorted({_normal(value) for value in hostnames} - {None}):
        owners = sorted(held.get(hostname, set()) - {project})
        if owners:
            raise HostnameOwnedError(hostname, ", ".join(owners), project)
