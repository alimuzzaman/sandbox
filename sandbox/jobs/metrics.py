"""Portable best-effort process metrics for job health observation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def sample(pid: int) -> dict:
    result = {"timestamp": time.time(), "pid": pid, "cpu_seconds": None,
              "rss_bytes": None, "io_read_bytes": None, "io_write_bytes": None,
              "state": None, "capabilities": []}
    stat = Path("/proc") / str(pid) / "stat"
    status = Path("/proc") / str(pid) / "status"
    io = Path("/proc") / str(pid) / "io"
    try:
        values = stat.read_text().split()
        ticks = os.sysconf("SC_CLK_TCK")
        result["cpu_seconds"] = (int(values[13]) + int(values[14])) / ticks
        result["state"] = values[2]
        result["capabilities"].append("proc_stat")
    except (OSError, ValueError, IndexError):
        pass
    try:
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                result["rss_bytes"] = int(line.split()[1]) * 1024
                result["capabilities"].append("proc_rss")
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        pairs = dict(line.split(":", 1) for line in io.read_text().splitlines())
        result["io_read_bytes"] = int(pairs.get("read_bytes", 0).strip())
        result["io_write_bytes"] = int(pairs.get("write_bytes", 0).strip())
        result["capabilities"].append("proc_io")
    except (OSError, ValueError):
        pass
    return result


def append(storage, repository, job_id: str, value: dict) -> dict:
    path = storage.job_dir(job_id) / "metrics.jsonl"
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with path.open("ab") as handle:
        os.chmod(path, 0o600); handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
    lines = path.read_bytes().splitlines()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    repository.upsert_metrics_index(job_id, samples=len(lines), first_at=str(json.loads(lines[0])["timestamp"]),
        last_at=str(value["timestamp"]), sha256=digest, complete=False)
    return value


def read(storage, job_id: str, *, limit: int = 500) -> list[dict]:
    path = storage.job_dir(job_id) / "metrics.jsonl"
    if not path.exists(): return []
    return [json.loads(line) for line in path.read_bytes().splitlines()[-limit:]]
