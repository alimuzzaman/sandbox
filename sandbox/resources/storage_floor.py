"""Remote storage-floor admission (spec 066).

A remote may declare ``storage_floor_gb``: free space that remote test and job
work must leave for hosted sites. Every remote deploy runs one bounded probe
before its first remote write. The probe measures free space, prunes expired
reservations, estimates this submission from recent workspaces of the same
repository and, under a host lock, either records a reservation or refuses.

No floor configured means no probe. With a floor, any failure to measure
refuses: protecting production outranks test convenience.
"""
from __future__ import annotations

import base64
import json
import shlex
import re

from sandbox.core._remote import ssh_run

GB = 1024 ** 3
DEFAULT_ESTIMATE_BYTES = 5 * GB
RESERVATION_WINDOW_SECONDS = 45 * 60
PROBE_TIMEOUT_SECONDS = 60
MAX_FLOOR_GB = 100000

CODE = "remote_storage_floor"


def floor_bytes(remote: dict | None) -> int | None:
    """The configured floor in bytes, or None when the remote has none."""
    value = (remote or {}).get("storage_floor_gb") if isinstance(remote, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value * GB


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def evaluate_storage_floor(payload, *, floor_gb: int) -> dict:
    """Turn one probe payload into a bounded admission decision."""
    floor = floor_gb * GB
    base = {"code": CODE, "floor_bytes": floor, "free_bytes": None,
            "reserved_bytes": None, "estimate_bytes": None,
            "estimate_source": None}
    if not isinstance(payload, dict) or payload.get("status") != "complete":
        return {**base, "ok": False, "reason": "measurement_unavailable"}
    free = _count(payload.get("free_bytes"))
    reserved = _count(payload.get("reserved_bytes"))
    if free is None or reserved is None:
        return {**base, "ok": False, "reason": "measurement_unavailable"}
    history = _count(payload.get("history_bytes"))
    estimate = history if history else DEFAULT_ESTIMATE_BYTES
    source = "history" if history else "default"
    fits = free - reserved - estimate >= floor
    if payload.get("admitted") is False:
        fits = False
    return {**base, "ok": fits, "reason": None if fits else "below_floor",
            "free_bytes": free, "reserved_bytes": reserved,
            "estimate_bytes": estimate, "estimate_source": source}


class StorageFloorAdmissionError(RuntimeError):
    """A remote deploy was refused by the storage floor before any write."""

    def __init__(self, decision: dict):
        self.decision = decision
        super().__init__(describe(decision))


def describe(decision: dict) -> str:
    def gb(key):
        value = decision.get(key)
        return "unknown" if value is None else f"{value / GB:.1f} GB"
    if decision.get("reason") == "measurement_unavailable":
        return ("remote job submission blocked by the remote storage floor: free "
                f"space could not be measured (floor {gb('floor_bytes')})")
    return ("remote job submission blocked by the remote storage floor: free "
            f"{gb('free_bytes')}, reserved {gb('reserved_bytes')}, estimate "
            f"{gb('estimate_bytes')} ({decision.get('estimate_source')}), floor "
            f"{gb('floor_bytes')}")


def refusal_payload(decision: dict, *, remote=None, detail=None) -> dict:
    """Local admission error, outside the controller/runtime protocol."""
    def count(key):
        return _count(decision.get(key))
    source = decision.get("estimate_source")
    reason = decision.get("reason")
    return {
        "ok": False, "status": "blocked", "code": CODE,
        "error": "remote job submission blocked by the remote storage floor",
        "detail": detail or describe(decision),
        "target": {"kind": "remote", "remote": remote if isinstance(remote, str)
                   and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", remote) else None},
        "storage": {"free_bytes": count("free_bytes"), "reserved_bytes": count("reserved_bytes"),
                    "estimate_bytes": count("estimate_bytes"), "floor_bytes": count("floor_bytes"),
                    "estimate_source": source if source in ("history", "default") else None,
                    "reason": reason if reason in ("below_floor", "measurement_unavailable")
                    else "measurement_unavailable"},
        "recovery": {"automatic_cleanup": False, "automatic_retry": False,
                     "guidance": "Reclaim space with ./sb workspace reap --remote REMOTE_NAME "
                                 "--dry-run, then --confirm, before retrying."},
        "retryable": False, "side_effects": {"staging_started": False},
    }


def _run_probe(remote: dict, request: dict, *, timeout: int) -> dict | None:
    program = "REQUEST = " + repr(request) + "\n" + _PROGRAM
    encoded = base64.b64encode(program.encode()).decode()
    command = "python3 -c " + shlex.quote(
        "import base64;exec(base64.b64decode(" + repr(encoded) + "))")
    try:
        result = ssh_run(remote, command, timeout=timeout)
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    found = []
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            found.append(candidate)
    return found[0] if len(found) == 1 else None


def remote_storage_floor_admission(remote: dict, target: str, *,
                                   timeout: int = PROBE_TIMEOUT_SECONDS) -> dict | None:
    """Probe, decide and (when admitted) reserve. None when no floor is set."""
    value = remote.get("storage_floor_gb")
    if value is None or (type(value) is int and value == 0):
        return None
    floor = floor_bytes(remote)
    if floor is None or value > MAX_FLOOR_GB:
        return evaluate_storage_floor(None, floor_gb=0)
    request = {"target": str(target), "floor_bytes": floor,
               "default_estimate": DEFAULT_ESTIMATE_BYTES,
               "window_seconds": RESERVATION_WINDOW_SECONDS,
               "reservations": None}
    payload = _run_probe(remote, request, timeout=timeout)
    return evaluate_storage_floor(payload, floor_gb=floor // GB)


def remote_free_space(remote: dict, *, timeout: int = 30) -> dict:
    """Measure free space only (no estimate, no reservation) for heal."""
    request = {"target": None, "floor_bytes": None, "default_estimate": 0,
               "window_seconds": RESERVATION_WINDOW_SECONDS, "reservations": None,
               "measure_only": True}
    payload = _run_probe(remote, request, timeout=timeout)
    if not isinstance(payload, dict) or payload.get("status") != "complete":
        return {"status": "unavailable", "free_bytes": None}
    free = _count(payload.get("free_bytes"))
    return {"status": "complete" if free is not None else "unavailable",
            "free_bytes": free}


# Runs on the remote with ``REQUEST`` bound. Prints exactly one JSON line.
_PROGRAM = r'''
import fcntl, json, math, os, stat, subprocess, time
from pathlib import Path

def emit(payload):
    print(json.dumps(payload, sort_keys=True))

def free_bytes(paths):
    values = []
    for path in paths:
        try:
            stat = os.statvfs(path)
        except OSError:
            return None
        values.append(stat.f_bavail * stat.f_frsize)
    return min(values) if values else None

def main():
    target = REQUEST.get("target")
    home = Path(os.environ.get("SANDBOX_HOME") or (Path.home() / "sandbox"))
    deploy = Path(target).parent if target else home / "deploy-src"
    if target and deploy.name == "deploy-src":
        home = deploy.parent
    roots = [str(deploy if deploy.exists() else home if home.exists() else Path.home())]
    for docker_root in ("/var/lib/docker",):
        if os.path.isdir(docker_root):
            roots.append(docker_root)
    free = free_bytes(roots)
    if free is None:
        return emit({"status": "unavailable", "reason": "statvfs_failed"})
    if REQUEST.get("measure_only"):
        return emit({"status": "complete", "free_bytes": free})
    # Serialize admission with reclaim. The reservation protects the source
    # family after this guard is released, throughout staging/job registration.
    guard_path = home / "runtime" / "resources" / "reclaim-host.lock"
    guard_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = os.open(str(guard_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    guard_state = os.fstat(guard)
    if (not stat.S_ISREG(guard_state.st_mode) or guard_state.st_uid != os.getuid()
            or stat.S_IMODE(guard_state.st_mode) & 0o077):
        os.close(guard)
        return emit({"status": "unavailable", "reason": "unsafe_reclaim_guard"})
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(guard)
        return emit({"status": "unavailable", "reason": "reclaim_busy"})
    GUARDS.append(guard)
    store = Path(REQUEST.get("reservations") or (home / "runtime" / "storage"))
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    history = None
    if target:
        slug = Path(target).name
        try:
            siblings = [p for p in deploy.iterdir()
                        if p.is_dir() and not p.is_symlink()
                        and (p.name == slug or p.name.startswith(slug + "-workspace-"))]
        except OSError:
            siblings = []
        siblings.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        sizes = []
        deadline = time.monotonic() + 20
        for path in siblings[:3]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                out = subprocess.run(["du", "-sk", str(path)], capture_output=True,
                                     text=True, timeout=remaining)
                if out.returncode == 0:
                    sizes.append(int(out.stdout.split()[0]) * 1024)
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                continue
        if sizes:
            history = max(sizes)
    estimate = history or int(REQUEST.get("default_estimate") or 0)
    lock = os.open(str(store / "reservations.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock)
        return emit({"status": "unavailable", "reason": "reservation_lock_busy"})
    try:
        path = store / "reservations.json"
        now = time.time()
        try:
            entries = json.loads(path.read_text())
        except FileNotFoundError:
            entries = []
        except (OSError, ValueError):
            return emit({"status": "unavailable", "reason": "reservations_unreadable"})
        if not isinstance(entries, list) or any(
                not isinstance(e, dict)
                or type(e.get("expires_at")) not in (int, float)
                or not math.isfinite(e["expires_at"])
                or type(e.get("estimate_bytes")) is not int
                or e["estimate_bytes"] < 0 for e in entries):
            return emit({"status": "unavailable", "reason": "reservations_invalid"})
        live = [e for e in entries if isinstance(e, dict)
                and isinstance(e.get("expires_at"), (int, float)) and e["expires_at"] > now
                and isinstance(e.get("estimate_bytes"), int) and e["estimate_bytes"] >= 0]
        reserved = sum(e["estimate_bytes"] for e in live)
        free = free_bytes(roots)
        if free is None:
            return emit({"status": "unavailable", "reason": "statvfs_failed"})
        floor = int(REQUEST.get("floor_bytes") or 0)
        admitted = free - reserved - estimate >= floor
        if admitted:
            live.append({"id": os.urandom(8).hex(), "target": str(target), "estimate_bytes": int(estimate),
                         "created_at": now,
                         "expires_at": now + int(REQUEST.get("window_seconds") or 0)})
        if admitted:
            tmp = store / ("reservations.json.tmp-%d" % os.getpid())
            tmp.write_text(json.dumps(live))
            os.replace(tmp, path)
        emit({"status": "complete", "free_bytes": free, "reserved_bytes": reserved,
              "history_bytes": history, "admitted": admitted})
    finally:
        os.close(lock)

GUARDS = []
try:
    main()
except Exception as exc:
    emit({"status": "unavailable", "reason": type(exc).__name__})
finally:
    for descriptor in GUARDS:
        os.close(descriptor)
'''
