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
import inspect
import math

from sandbox.core._remote import ssh_run

GB = 1024 ** 3
DEFAULT_ESTIMATE_BYTES = 5 * GB
RESERVATION_WINDOW_SECONDS = 45 * 60
# Numeric expiry stays compatible with older admission/reclaim readers.
OPERATION_RESERVATION_EXPIRY = 253402300799  # 9999-12-31, explicit release only.
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
    reported_estimate = _count(payload.get("estimate_bytes"))
    reported_source = payload.get("estimate_source")
    if reported_estimate is not None and reported_source in ("history", "default", "minimum"):
        estimate, source = reported_estimate, reported_source
    else:
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
        return "unknown" if value is None else f"{value / GB:.1f} GiB"
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
                    "estimate_source": source if source in ("history", "default", "minimum") else None,
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


def release_storage_reservation(store: str, reservation_id: "str | None", *,
                                orphan_target: "str | None" = None, verify_only: bool = False, orphan_engine: bool = False,
                                lock_timeout: float = 10) -> None:
    """Atomically release only one owned operation reservation; usable in workers."""
    import fcntl
    import json
    import os
    import re
    import stat
    import tempfile
    import time
    from pathlib import Path

    if type(orphan_engine) is not bool or (orphan_engine and (reservation_id is not None or orphan_target is not None or verify_only)):
        raise ValueError("invalid orphan engine release")
    if reservation_id is None and orphan_target is None and not orphan_engine:
        raise ValueError("operation or target required")
    if reservation_id is not None and not re.fullmatch(r"[0-9a-f]{32}", reservation_id):
        raise ValueError("invalid operation reservation")
    root = Path(store)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("unsafe reservation store")
    lock = os.open(root / "reservations.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("unsafe reservation lock")
        deadline = time.monotonic() + lock_timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("reservation lock busy")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            fd = os.open(root / "reservations.json", os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if verify_only:
                raise ValueError("operation reservation missing")
            return
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 8 * 1024**2):
                raise ValueError("unsafe reservation ledger")
            # Older admissions rewrite this same-owner ledger as 0644.
            os.fchmod(handle.fileno(), 0o600)
            entries = json.loads(handle.read(8 * 1024**2 + 1))
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("invalid reservation ledger")
        if verify_only:
            if not any(e.get("id") == reservation_id and e.get("kind") == "host-local-images" for e in entries):
                raise ValueError("operation reservation missing")
            return
        # orphan_engine is allowed only after the host-global engine lease and
        # exclusive worker fence prove that every image uploader is idle and no
        # uncertain load exists. Durable job reservations are never image pins.
        retained = [e for e in entries if not (
            e.get("kind") == "host-local-images" if orphan_engine else
            e.get("kind") == "host-local-images" and e.get("target") == orphan_target
            if orphan_target is not None else e.get("id") == reservation_id)]
        if retained == entries:
            return  # Idempotent after an acknowledgement was lost.
        if reservation_id is not None and any(e.get("id") == reservation_id and e.get("kind") != "host-local-images" for e in entries):
            raise ValueError("reservation ownership differs")
        descriptor, temporary = tempfile.mkstemp(prefix=".reservation-release-", dir=root)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(retained, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, root / "reservations.json")
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        os.close(lock)


# One canonical stdlib function runs in the controller helper and load worker.
RESERVATION_RELEASE_PROGRAM = inspect.getsource(release_storage_reservation)


def remote_storage_floor_release(remote: dict, target: str, reservation_id: str, *, timeout: int = 30) -> bool:
    if not isinstance(reservation_id, str) or re.fullmatch(r"[0-9a-f]{32}", reservation_id) is None:
        return False
    payload = _run_probe(remote, {"target": target, "release_id": reservation_id}, timeout=timeout)
    return payload == {"status": "released"}


def remote_storage_floor_admission(remote: dict, target: str, *,
                                   timeout: int = PROBE_TIMEOUT_SECONDS,
                                   minimum_estimate_bytes: int = 0,
                                   reservation_id: str | None = None) -> dict | None:
    """Probe, decide and (when admitted) reserve. None when no floor is set."""
    value = remote.get("storage_floor_gb")
    if value is None or (type(value) is int and value == 0):
        return None
    floor = floor_bytes(remote)
    if floor is None or value > MAX_FLOOR_GB:
        return evaluate_storage_floor(None, floor_gb=0)
    if type(minimum_estimate_bytes) is not int or minimum_estimate_bytes < 0:
        return evaluate_storage_floor(None, floor_gb=floor // GB)
    if reservation_id is not None and (not isinstance(reservation_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", reservation_id) is None):
        return evaluate_storage_floor(None, floor_gb=floor // GB)
    request = {"target": str(target), "floor_bytes": floor,
               "default_estimate": DEFAULT_ESTIMATE_BYTES,
               "minimum_estimate": minimum_estimate_bytes,
               "window_seconds": RESERVATION_WINDOW_SECONDS,
               "reservations": None}
    if reservation_id is not None:
        request["reservation_id"] = reservation_id
        request["operation_expiry"] = OPERATION_RESERVATION_EXPIRY
    payload = _run_probe(remote, request, timeout=timeout)
    return evaluate_storage_floor(payload, floor_gb=floor // GB)


def remote_storage_floor_inspection(remote: dict, target: str, *, archive_bytes: int | None = None,
                                    multiplier: int = 3, timeout: int = 60) -> dict:
    """Advisory admission only. The later push must reserve and recheck."""
    from sandbox.hosting.local_images import MAX_RECEIPT_BYTES
    floor_value = remote.get("storage_floor_gb")
    absent_floor = floor_value is None or (type(floor_value) is int and floor_value == 0)
    floor = 1 if absent_floor else floor_value
    valid = type(floor) is int and 0 < floor <= MAX_FLOOR_GB
    valid = valid and (archive_bytes is None or _count(archive_bytes) is not None)
    valid = valid and type(multiplier) is int and multiplier in (2, 3)
    minimum = max(multiplier * (archive_bytes or 0), MAX_RECEIPT_BYTES) if valid else 0
    archive_bytes = _count(archive_bytes)
    skipped = valid and multiplier == 2 and absent_floor
    request = {"target": target, "floor_bytes": floor * GB if valid else 0,
               "default_estimate": DEFAULT_ESTIMATE_BYTES, "minimum_estimate": minimum,
               "inspect_only": True}
    payload = _run_probe(remote, request, timeout=timeout) if valid and not skipped else None
    live = _count(payload.get("live_reservations")) if isinstance(payload, dict) else None
    snapshot_at = payload.get("snapshot_at") if isinstance(payload, dict) else None
    try:
        timestamp_valid = type(snapshot_at) in (int, float) and math.isfinite(snapshot_at) and snapshot_at > 0
    except OverflowError:
        timestamp_valid = False
    if not timestamp_valid:
        snapshot_at = None
    complete = isinstance(payload, dict) and payload.get("status") == "complete"
    evidence_valid = live is not None and timestamp_valid
    decision = evaluate_storage_floor(payload if not complete or evidence_valid else None, floor_gb=floor if valid else 0)
    if decision["reason"] == "measurement_unavailable" and isinstance(payload, dict):
        reason = payload.get("reason")
        if reason in ("storage_snapshot_changed", "storage_evidence_unavailable", "statvfs_failed"):
            decision["reason"] = reason
    margin = (decision["free_bytes"] - decision["reserved_bytes"]
              - decision["estimate_bytes"] - decision["floor_bytes"]
              if decision["free_bytes"] is not None else None)
    decision.update(reserve_bytes=DEFAULT_ESTIMATE_BYTES, archive_bytes=archive_bytes,
                    margin_bytes=margin, provisional=archive_bytes is None,
                    live_reservations=live if evidence_valid else None,
                    reservation_created=False, advisory=True, floor_applied=True,
                    snapshot_kind="point_in_time", snapshot_at=snapshot_at if evidence_valid else None)
    if skipped:
        # V1 push performs no storage admission at all when no floor is configured.
        decision.update(ok=True, reason="floor_not_configured", floor_applied=False,
                        floor_bytes=0, reserve_bytes=0, estimate_bytes=None, estimate_source=None)
        decision.pop("snapshot_kind")
    for key in ("free", "floor", "reserve", "reserved", "archive", "estimate", "margin"):
        value = decision.get(key + "_bytes")
        decision[key + "_gib"] = value / GB if value is not None else None
    return decision


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


def storage_estimate(deploy, target, request):
    import subprocess, time
    from pathlib import Path
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
    default_estimate = int(request.get("default_estimate") or 0)
    minimum_estimate = int(request.get("minimum_estimate") or 0)
    ordinary_estimate = history or default_estimate
    estimate = max(ordinary_estimate, minimum_estimate)
    estimate_source = ("minimum" if minimum_estimate > ordinary_estimate
                       else "history" if history else "default")
    return history, estimate, estimate_source


def storage_free_bytes(paths):
    import os
    values = []
    for path in paths:
        try:
            state = os.statvfs(path)
        except OSError:
            return None
        values.append(state.f_bavail * state.f_frsize)
    return min(values) if values else None


def inspect_storage_floor(home, deploy, target, request, roots):
    """Lock-free optimistic snapshot. Never contends with admission or reclaim."""
    import json, math, os, stat, time
    store = home / "runtime" / "storage"
    ledger = store / "reservations.json"
    paths = ((home / "runtime", True, None),
             (home / "runtime" / "resources", True, False),
             (store, True, True),
             (home / "runtime" / "resources" / "reclaim-host.lock", False, True),
             (store / "reservations.lock", False, True),
             (ledger, False, False))

    def signature(path, directory, private):
        try:
            state = path.lstat()
        except FileNotFoundError:
            return None
        if (not (stat.S_ISDIR(state.st_mode) if directory else stat.S_ISREG(state.st_mode))
                or state.st_uid != os.geteuid()
                or (private is not None and state.st_mode & (0o077 if private else 0o022))
                or (not directory and state.st_nlink != 1)):
            raise ValueError("unsafe storage evidence")
        return (state.st_dev, state.st_ino, state.st_mode, state.st_size,
                state.st_mtime_ns, state.st_ctime_ns)

    def snapshot():
        before = tuple(signature(*item) for item in paths)
        raw = b"[]"
        if before[-1] is not None:
            descriptor = os.open(ledger, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                state = os.fstat(handle.fileno())
                if (not stat.S_ISREG(state.st_mode) or state.st_uid != os.geteuid()
                        or state.st_mode & 0o022 or state.st_nlink != 1 or state.st_size > 8 * 1024**2):
                    raise ValueError("unsafe storage ledger")
                raw = handle.read(8 * 1024**2 + 1)
                if len(raw) > 8 * 1024**2:
                    raise ValueError("oversized storage ledger")
        after = tuple(signature(*item) for item in paths)
        if before != after:
            raise ValueError("storage snapshot changed")
        return before, raw

    try:
        # The slow history scan precedes the measurement and holds no locks.
        history, estimate, source = storage_estimate(deploy, target, request)
        before = snapshot()
        measured_at = time.time()
        free = storage_free_bytes(roots)
        after = snapshot()
        if before != after:
            return {"status": "unavailable", "reason": "storage_snapshot_changed"}
        if free is None:
            return {"status": "unavailable", "reason": "statvfs_failed"}
        entries = json.loads(before[1])
        if not isinstance(entries, list) or any(
                not isinstance(e, dict) or type(e.get("expires_at")) not in (int, float)
                or not math.isfinite(e["expires_at"]) or type(e.get("estimate_bytes")) is not int
                or e["estimate_bytes"] < 0 for e in entries):
            raise ValueError("invalid storage ledger")
        live = [e for e in entries if e["expires_at"] > measured_at]
        return {"status": "complete", "free_bytes": free,
                "reserved_bytes": sum(e["estimate_bytes"] for e in live),
                "live_reservations": len(live), "estimate_bytes": estimate,
                "estimate_source": source, "history_bytes": history,
                "snapshot_at": measured_at, "snapshot_kind": "point_in_time"}
    except Exception:
        return {"status": "unavailable", "reason": "storage_evidence_unavailable"}


# Runs on the remote with ``REQUEST`` bound. Prints exactly one JSON line.
_PROGRAM = (RESERVATION_RELEASE_PROGRAM + "\n" + inspect.getsource(storage_estimate)
            + "\n" + inspect.getsource(storage_free_bytes)
            + "\n" + inspect.getsource(inspect_storage_floor)) + r'''
import fcntl, json, math, os, stat, subprocess, time
from pathlib import Path

def emit(payload):
    print(json.dumps(payload, sort_keys=True))

free_bytes = storage_free_bytes

def main():
    target = REQUEST.get("target")
    home = Path(os.environ.get("SANDBOX_HOME") or (Path.home() / "sandbox"))
    deploy = Path(target).parent if target else home / "deploy-src"
    if target and deploy.name == "deploy-src":
        home = deploy.parent
    if REQUEST.get("release_id") is not None:
        release_storage_reservation(str(home / "runtime" / "storage"), REQUEST["release_id"])
        return emit({"status": "released"})
    roots = [str(deploy if deploy.exists() else home if home.exists() else Path.home())]
    for docker_root in ("/var/lib/docker",):
        if os.path.isdir(docker_root):
            roots.append(docker_root)
    if REQUEST.get("inspect_only"):
        return emit(inspect_storage_floor(home, deploy, target, REQUEST, roots))
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
    history, estimate, estimate_source = storage_estimate(deploy, target, REQUEST)
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
        operation_id = REQUEST.get("reservation_id")
        if operation_id is not None:
            import re
            if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
                return emit({"status": "unavailable", "reason": "invalid_operation_reservation"})
            if any(e.get("id") == operation_id and (e.get("target") != str(target)
                    or e.get("kind") != "host-local-images") for e in live):
                return emit({"status": "unavailable", "reason": "reservation_owner_mismatch"})
        if operation_id is not None:
            estimate = max([estimate] + [e["estimate_bytes"] for e in live if e.get("id") == operation_id])
        reserved = sum(e["estimate_bytes"] for e in live if operation_id is None or e.get("id") != operation_id)
        free = free_bytes(roots)
        if free is None:
            return emit({"status": "unavailable", "reason": "statvfs_failed"})
        floor = int(REQUEST.get("floor_bytes") or 0)
        admitted = free - reserved - estimate >= floor
        if admitted:
            if operation_id is not None:
                # Never shorten an existing reservation on idempotent replay.
                estimate = max([estimate] + [e["estimate_bytes"] for e in live if e.get("id") == operation_id])
                live = [e for e in live if e.get("id") != operation_id]
            record = {"id": operation_id or os.urandom(8).hex(), "target": str(target), "estimate_bytes": int(estimate),
                      "created_at": now,
                      "expires_at": now + int(REQUEST.get("window_seconds") or 0)}
            if operation_id is not None:
                record["expires_at"] = int(REQUEST["operation_expiry"])
                record["kind"] = "host-local-images"
            live.append(record)
        if admitted:
            tmp = store / ("reservations.json.tmp-%d" % os.getpid())
            descriptor = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(live, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            directory = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        emit({"status": "complete", "free_bytes": free, "reserved_bytes": reserved,
              "history_bytes": history, "estimate_bytes": estimate,
              "estimate_source": estimate_source, "admitted": admitted})
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
