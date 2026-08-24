"""Bounded, read-only resource snapshots for one local Compose project."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from typing import Any

from sandbox.services.process import BoundedProcessRunner, ProcessRunner


_MAX_CONTAINERS = 64
_SAFE_ID = re.compile(r"[0-9a-f]{12,64}")
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_MEMORY = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB)")
_MULTIPLIERS = {"B": 1, "KiB": 1024, "MiB": 1024**2,
                "GiB": 1024**3, "TiB": 1024**4}


def _number(value: object, *, maximum: float, integer: bool = False) -> int | float:
    raw = str(value).strip().removesuffix("%")
    try:
        parsed = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid numeric value") from None
    if parsed < 0 or parsed > maximum or (not integer and not math.isfinite(parsed)):
        raise ValueError("invalid numeric value")
    return parsed


def _memory_bytes(value: object) -> int:
    match = _MEMORY.fullmatch(str(value).strip())
    if not match:
        raise ValueError("unsupported memory unit")
    result = float(match.group(1)) * _MULTIPLIERS[match.group(2)]
    if not math.isfinite(result) or result < 0 or result > (1 << 63) - 1:
        raise ValueError("invalid memory value")
    return int(result)


def parse_container_stats(output: str, *, truncated: bool = False) -> dict[str, Any]:
    """Parse Docker's line-delimited JSON stats without trusting observed names."""
    rows: list[dict[str, Any]] = []
    malformed = 0
    for line in (output or "").splitlines():
        try:
            item = json.loads(line)
            name = str(item["Name"]).strip()
            usage = str(item["MemUsage"]).split("/", 1)[0].strip()
            if not _SAFE_NAME.fullmatch(name):
                name = "redacted"
            rows.append({
                "name": name,
                "cpu_percent": _number(item["CPUPerc"], maximum=1_000_000),
                "memory_used_bytes": _memory_bytes(usage),
                "memory_percent": _number(item["MemPerc"], maximum=100),
                "pids": _number(item["PIDs"], maximum=(1 << 31) - 1, integer=True),
            })
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            malformed += 1
    rows.sort(key=lambda row: (-row["cpu_percent"], -row["memory_used_bytes"], row["name"]))
    return {
        "status": "partial" if malformed or truncated else "complete",
        "source": "docker_stats_no_stream", "snapshot": "point_in_time",
        "observed_count": len(rows) + malformed, "malformed_count": malformed,
        "truncated": truncated, "rows": rows,
    }


def _unavailable(code: str) -> dict[str, Any]:
    return {
        "status": "unavailable", "source": "docker_stats_no_stream",
        "snapshot": "point_in_time", "observed_count": 0,
        "malformed_count": 0, "truncated": False, "rows": [],
        "error": {"code": code},
    }


def local_container_stats(instance: str, *, runner: ProcessRunner | None = None,
                          timeout: float = 5) -> dict[str, Any]:
    """Collect one bounded sample for containers owned by ``instance``."""
    process = runner or BoundedProcessRunner(max_output=262_144)
    project = f"sandbox-{instance}"
    limitations = [
        "This is a point-in-time sample and can drift immediately.",
        "CPU is Docker's no-stream sample and is not evidence that a container is idle.",
        "Memory is container usage, not additive host RAM attribution; shared and cached pages may overlap.",
        "A zero CPU sample does not prove that the instance is unused or safe to stop.",
    ]
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        listed = process.run((
            "docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
            "--format", "{{.ID}}",
        ), timeout=timeout)
    except (FileNotFoundError, OSError):
        return {**_unavailable("docker_unavailable"), "observed_at": observed_at,
                "limitations": limitations}
    if listed.returncode == 124:
        return {**_unavailable("docker_timeout"), "observed_at": observed_at,
                "limitations": limitations}
    if listed.returncode != 0:
        return {**_unavailable("docker_unavailable"), "observed_at": observed_at,
                "limitations": limitations}

    identifiers = [line.strip() for line in listed.stdout.splitlines()
                   if _SAFE_ID.fullmatch(line.strip())]
    truncated = len(identifiers) > _MAX_CONTAINERS
    identifiers = identifiers[:_MAX_CONTAINERS]
    if not identifiers:
        parsed = parse_container_stats("", truncated=truncated)
        return {**parsed, "observed_at": observed_at, "limitations": limitations}
    try:
        sampled = process.run((
            "docker", "stats", "--no-stream", "--format", "{{json .}}", *identifiers,
        ), timeout=timeout)
    except (FileNotFoundError, OSError):
        return {**_unavailable("docker_unavailable"), "observed_at": observed_at,
                "limitations": limitations}
    if sampled.returncode == 124:
        return {**_unavailable("docker_timeout"), "observed_at": observed_at,
                "limitations": limitations}
    if sampled.returncode != 0:
        return {**_unavailable("docker_stats_unavailable"), "observed_at": observed_at,
                "limitations": limitations}
    parsed = parse_container_stats(sampled.stdout, truncated=truncated)
    return {**parsed, "observed_at": observed_at, "limitations": limitations}
