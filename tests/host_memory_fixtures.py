"""Deterministic, secret-free Feature 046 fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib
import json

GIB = 1024 ** 3
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
MARKER = "a" * 24
REVISION = "b" * 24
TARGET = "host-fixture"
SWAP_UNIT_TEXT = "[Unit]\nDescription=Sandbox host memory swap\n[Swap]\nWhat=/var/lib/sandbox/host-memory/sandbox.swap\n"
SYSCTL_TEXT = "vm.swappiness=15\n"
PROC_MEMINFO = """\
MemTotal:       16777216 kB
MemFree:         2097152 kB
MemAvailable:   12582912 kB
Buffers:         1048576 kB
Cached:          9437184 kB
SwapCached:            0 kB
SwapTotal:       4194304 kB
SwapFree:        3670016 kB
"""
PROC_SWAPS_EMPTY = "Filename\tType\tSize\tUsed\tPriority\n"
PROC_SWAPS_OWNED = (
    "Filename\tType\tSize\tUsed\tPriority\n"
    "/var/lib/sandbox/host-memory/sandbox.swap file 4194304 524288 -2\n"
)
CGROUP_V2 = {
    "/proc/self/cgroup": "0::/fixture.scope\n",
    "/sys/fs/cgroup/fixture.scope/memory.max": str(12 * GIB),
    "/sys/fs/cgroup/fixture.scope/memory.current": str(6 * GIB),
    "/sys/fs/cgroup/fixture.scope/memory.swap.max": str(2 * GIB),
    "/sys/fs/cgroup/fixture.scope/memory.swap.current": str(256 * 1024 ** 2),
    "/sys/fs/cgroup/memory.max": "max",
    "/sys/fs/cgroup/memory.swap.max": "max",
}
CGROUP_V1 = {
    "/proc/self/cgroup": "5:memory:/fixture.scope\n",
    "/sys/fs/cgroup/memory/fixture.scope/memory.limit_in_bytes": str(12 * GIB),
    "/sys/fs/cgroup/memory/fixture.scope/memory.usage_in_bytes": str(6 * GIB),
    "/sys/fs/cgroup/memory/fixture.scope/memory.memsw.limit_in_bytes": str(14 * GIB),
    "/sys/fs/cgroup/memory/fixture.scope/memory.memsw.usage_in_bytes": str(7 * GIB),
    "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(16 * GIB),
    "/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes": str(20 * GIB),
}


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


def status_state(**overrides):
    state = {
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "target_identity": TARGET,
        "memory": {"total_bytes": 16 * GIB, "available_bytes": 12 * GIB,
                   "state": "known"},
        "filesystem": {"total_bytes": 100 * GIB, "free_bytes": 80 * GIB,
                       "state": "known"},
        "swap_areas": [],
        "swappiness": {"effective": 60, "owned": False, "drifted": False},
        "monitor": {
            "service_state": "missing", "timer_state": "missing",
            "interval_seconds": None, "latest_sample_at": None,
            "age_seconds": None, "freshness": "missing", "next_sample_at": None,
            "sustained_swap_use": None, "pressure_state": "unknown",
            "retention": {"current_files": 0, "history_files": 0,
                          "total_bytes": 0, "compliant": True, "truncated": False},
        },
        "container_eligibility": {
            "state": "eligible", "version": "v2", "memory_limit_bytes": None,
            "memory_used_bytes": 6 * GIB, "swap_limit_bytes": None,
            "swap_used_bytes": 0, "evidence_state": "known",
        },
        "reboot_verification": {"state": "unverified", "observed_at": None},
        "operation_block": None, "ownership": "absent", "evidence_state": "known",
    }
    state.update(overrides)
    state["observation_digest"] = hashlib.sha256(json.dumps(
        {key: value for key, value in state.items() if key != "observation_digest"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    return state


def service_evidence():
    return {
        "remote_name": "approved-swap-fixture", "target_identity": TARGET,
        "ownership_marker": MARKER, "runtime_revision": REVISION,
        "resource_schema": 1, "host_memory_schema": 1, "transport": "control",
    }


def ownership_receipt(*, lifecycle_state="enabled"):
    return {
        "schema_version": 1,
        "target_identity": TARGET,
        "created_by_operation": "c" * 64,
        "last_verified_operation": "d" * 64,
        "policy": {
            "size_gib": 4, "swappiness": 15,
            "sample_interval_seconds": 300, "freshness_seconds": 660,
            "warning_swap_used_bytes": 512 * 1024 ** 2,
            "warning_consecutive_samples": 3, "history_files": 9,
            "history_bytes": 32 * 1024 ** 2, "sample_timeout_seconds": 5,
        },
        "artifacts": {
            "swap_file": {"kind": "regular", "mode": 0o600,
                          "digest": "e" * 64, "state": "active"},
            "swap_unit": {"kind": "regular", "mode": 0o644,
                          "digest": hashlib.sha256(SWAP_UNIT_TEXT.encode()).hexdigest(),
                          "state": "enabled"},
            "swappiness_policy": {"kind": "regular", "mode": 0o644,
                                  "digest": hashlib.sha256(SYSCTL_TEXT.encode()).hexdigest(),
                                  "state": "active"},
        },
        "swap_area_id": "f" * 24,
        "prior_swappiness": {"effective": 60, "persistent": False},
        "verified_at": "2026-08-30T12:00:00Z",
        "reboot_verification": {"state": "unverified", "observed_at": None},
        "lifecycle_state": lifecycle_state,
    }


def command_result(*, returncode=0, output="active"):
    """A narrow fake runner result; it deliberately has no environment field."""
    return SimpleNamespace(returncode=returncode, stdout=output, stderr="")


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
