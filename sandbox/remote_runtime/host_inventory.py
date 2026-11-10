"""Bounded, secret-free inventory of the Sandbox host this process runs on.

The remote control service serves it at ``/inventory``; ``sb web`` on the
same host builds it directly, so it needs no HTTP call or bearer token.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

_DIAGNOSTICS_SCHEMA_VERSION = 2

_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS = 5

_DIAGNOSTICS_MAX_PROBE_ROWS = 101

def _job_counts() -> dict:
    home = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
    result = {"total": None, "active": None, "queued": None, "by_lifecycle": {}}
    try:
        connection = sqlite3.connect(home / "runtime" / "jobs" / "registry.sqlite3")
        try:
            rows = connection.execute(
                "SELECT lifecycle, COUNT(*) FROM jobs GROUP BY lifecycle"
            ).fetchall()
        finally:
            connection.close()
        counts = {str(state): int(count) for state, count in rows}
        result = {
            "total": sum(counts.values()),
            "active": sum(counts.get(state, 0) for state in
                          ("accepted", "queued", "running", "cancelling")),
            "queued": counts.get("queued", 0),
            "by_lifecycle": counts,
        }
    except (OSError, sqlite3.Error):
        pass
    return result


def _diagnostic_process_snapshot() -> dict:
    """Collect bounded process evidence with fixed argv and no shell or SSH."""
    from sandbox.core import _remote as remote_core

    sections = ["__SANDBOX_PS_BEGIN__"]
    try:
        ps_result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,pcpu=,pmem=,rss=,comm="],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        ps_result = None
    if ps_result is not None and ps_result.returncode == 0:
        sections.extend(ps_result.stdout.splitlines()[:_DIAGNOSTICS_MAX_PROBE_ROWS])
    sections.extend(("__SANDBOX_PS_END__", "__SANDBOX_DOCKER_BEGIN__"))

    try:
        docker_result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        docker_result = None
    if docker_result is not None and docker_result.returncode == 0:
        sections.append("__SANDBOX_DOCKER_AVAILABLE__")
        sections.extend(docker_result.stdout.splitlines()[:_DIAGNOSTICS_MAX_PROBE_ROWS])
    sections.append("__SANDBOX_DOCKER_END__")

    probe_output = "\n".join(sections)
    process_view, _ = remote_core._parse_ssh_process_view(probe_output)
    containers = remote_core._parse_container_view(probe_output)
    return {
        "diagnostics_schema": _DIAGNOSTICS_SCHEMA_VERSION,
        "transport": "control",
        "capabilities": ["process_view", "container_view"],
        "process_view": process_view,
        "containers": containers,
    }


def _hosted_inventory_snapshot(*, deep: bool = False) -> dict:
    """Build a secret-free, bounded dashboard view of this hosted Sandbox."""
    from sandbox.core._config import load_config
    from sandbox.core._dash import collect_instance_rows
    from sandbox.resources.remote import LocalProbeAdapter

    partial = []
    try:
        source_rows = collect_instance_rows(load_config())
        instances = [{
            "name": str(row.get("name") or "unknown")[:128],
            "running": bool(row.get("running")),
            "server": str(row.get("server") or "unknown")[:64],
            "project": str(row.get("project") or "unknown")[:128],
            "label": str(row.get("label") or "default")[:128],
        } for row in source_rows[:500]]
        if len(source_rows) > 500:
            partial.append("instance_rows_truncated")
    except Exception:
        instances = []
        partial.append("instance_inventory_unavailable")

    diagnostics = _diagnostic_process_snapshot()
    container_view = diagnostics.get("containers") or {"status": "unavailable", "rows": []}
    container_rows = container_view.get("rows") if isinstance(container_view, dict) else []
    container_rows = container_rows if isinstance(container_rows, list) else []
    per_instance = []
    attributed_names = set()
    for instance in instances:
        needle = instance["name"].lower().replace("_", "-")
        matched = []
        for row in container_rows:
            name = str(row.get("name") or "")
            normalized = name.lower().replace("_", "-")
            if needle and (normalized == needle or normalized.startswith(needle + "-")
                           or ("-" + needle + "-") in ("-" + normalized + "-")):
                matched.append(row)
                attributed_names.add(name)
        per_instance.append({
            "name": instance["name"],
            "attribution_status": "heuristic" if matched else "unknown",
            "container_count": len(matched),
            "memory_used_bytes": sum(int(row.get("memory_used_bytes") or 0) for row in matched),
            "cpu_percent": round(sum(float(row.get("cpu_percent") or 0) for row in matched), 2),
        })
    unattributed = [row for row in container_rows if str(row.get("name") or "") not in attributed_names]

    try:
        storage_probe = LocalProbeAdapter().observe_reclaim(
            budget_seconds=30 if deep else 8,
            directory_cache="refresh" if deep else "cache_only",
        )
        storage = {
            "status": "partial" if any(
                str(item.get("status")) not in {"complete", "measured"}
                for item in (storage_probe.get("category_outcomes") or ())
                if isinstance(item, dict)
            ) else "complete",
            "capacity": storage_probe.get("capacity"),
            "capacity_scope_id": storage_probe.get("capacity_scope_id"),
            "attribution_status": (
                "available" if storage_probe.get("deep_attribution") else "unknown"
            ),
            "category_outcomes": list(storage_probe.get("category_outcomes") or ())[:50],
        }
    except Exception:
        storage = {"status": "unavailable", "capacity": None,
                   "attribution_status": "unknown", "category_outcomes": []}
        partial.append("storage_inventory_unavailable")

    running = sum(1 for row in instances if row["running"])
    host = _memory_snapshot()
    try:
        # Dashboard disk pressure is host-wide. The resource probe also uses
        # the root filesystem as its capacity scope, so do not silently report
        # only the Sandbox home mount when those differ.
        disk = shutil.disk_usage("/")
        host.update({"disk_total_bytes": disk.total, "disk_used_bytes": disk.used,
                     "disk_free_bytes": disk.free, "disk_scope": "/"})
    except OSError:
        host.update({"disk_total_bytes": None, "disk_used_bytes": None,
                     "disk_free_bytes": None, "disk_scope": None})
        partial.append("disk_inventory_unavailable")
    host["load_1m"] = round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None
    jobs = _job_counts()
    return {
        "ok": True, "inventory_schema": 1, "transport": "control",
        "instances": {"total": len(instances), "running": running,
                      "stopped": len(instances) - running, "rows": instances},
        "host": host, "jobs": jobs,
        "process_view": diagnostics.get("process_view"), "containers": container_view,
        "per_instance_usage": per_instance,
        "unattributed_containers": unattributed,
        "storage": storage,
        "scan_mode": "deep" if deep else "fast",
        "evidence_status": "partial" if partial or storage.get("status") != "complete"
                           or container_view.get("status") != "complete" else "complete",
        "partial_reasons": partial,
        "migration": {
            "service_backed": ["resource_observe", "resource_cleanup", "host_inventory"],
            "operator_only": ["remote_ssh"],
            "remaining": ["remote_domains", "remote_plugins", "remote_service_lifecycle",
                          "remote_docker_pool", "deploy_transport"],
        },
    }


def _memory_snapshot(meminfo_path: Path = Path("/proc/meminfo")) -> dict:
    """Return bounded host RAM totals derived from Linux meminfo.

    ``MemAvailable`` is the kernel's reclaimable-memory estimate, so
    ``total - available`` is a more useful usage figure than ``total -
    MemFree`` on a cache-heavy VPS. Keep the response in MiB and expose only
    aggregate numbers; no process or path details leave the host.
    """
    values = {}
    try:
        for line in meminfo_path.read_text().splitlines():
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            parts = raw.strip().split()
            if not parts:
                continue
            value = int(parts[0])
            if len(parts) > 1 and parts[1].lower() == "kb":
                value *= 1024
            values[key] = value
    except (OSError, ValueError, IndexError):
        values = {}

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not isinstance(total, int) or not isinstance(available, int) or total <= 0:
        return {
            "memory_total_mb": None,
            "memory_used_mb": None,
            "memory_available_mb": None,
            "memory_used_percent": None,
        }
    available = min(max(available, 0), total)
    used = total - available
    return {
        "memory_total_mb": total // (1024 * 1024),
        "memory_used_mb": used // (1024 * 1024),
        "memory_available_mb": available // (1024 * 1024),
        "memory_used_percent": round((used * 100) / total, 2),
    }
