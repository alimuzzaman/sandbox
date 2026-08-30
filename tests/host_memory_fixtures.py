"""Deterministic, secret-free Feature 046 fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

GIB = 1024 ** 3
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
MARKER = "a" * 24
REVISION = "b" * 24
TARGET = "host-fixture"


def eligible_state(**overrides):
    state = {
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "memory": {"total_bytes": 16 * GIB, "available_bytes": 12 * GIB},
        "filesystem": {"total_bytes": 100 * GIB, "free_bytes": 80 * GIB},
        "swap_areas": [],
        "swappiness": {"effective": 60, "owned": False, "drifted": False},
        "monitor": {"service_state": "missing", "timer_state": "missing",
                    "freshness": "missing", "interval_seconds": None},
        "container_eligibility": {"state": "unknown"},
        "reboot_verification": "unverified",
        "operation_block": None,
        "ownership": "absent",
        "evidence_state": "known",
    }
    state.update(overrides)
    return state


def service_evidence():
    return {
        "remote_name": "approved-swap-fixture", "target_identity": TARGET,
        "ownership_marker": MARKER, "runtime_revision": REVISION,
        "resource_schema": 1, "host_memory_schema": 1, "transport": "control",
    }


def sample(at="2026-08-30T12:00:00Z", used=0, status="valid"):
    return {
        "schema_version": 1, "sampled_at": at, "status": status,
        "memory": {"total_bytes": 16 * GIB, "available_bytes": 12 * GIB,
                   "free_bytes": 2 * GIB, "buffers_bytes": GIB,
                   "cached_bytes": 9 * GIB},
        "swap": {"total_bytes": 4 * GIB, "free_bytes": 4 * GIB - used,
                 "used_bytes": used},
        "pressure": None,
        "vm_counters": {"pswpin": 0, "pswpout": 0, "pgmajfault": 0},
        "errors": [],
    }
