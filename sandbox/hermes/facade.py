"""Compatibility facade while callers migrate from the legacy Hermes module."""

from __future__ import annotations

import sandbox.core._hermes as _legacy


# Migrated transports depend on explicit facade-owned names. Direct aliases keep
# callable identity, argument handling, authorization order, and error behavior
# byte-for-observable-result compatible while each implementation is extracted.
status = _legacy.status
run = _legacy.run
job_status = _legacy.job_status
job_kill = _legacy.job_kill
gateway = _legacy.gateway
backup_list = _legacy.backup_list


def __getattr__(name):
    return getattr(_legacy, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_legacy)))
