"""Server-owner apply/destroy target leases: recorded, and taken over once dead.

A server-cli job that takes a target lease (``host apply``, edge recovery,
``host destroy``, ``host stack down``) appends the exact lease it wrote (holder,
token, path, target) to its own job events, and its release likewise. A later
server-owner operation on the same target may replace that lease only when,
under the lease's own lock, the file's holder and token match one recorded
acquisition of a server-cli job in this server's registry that is terminal (not
``interrupted``), recorded its owned work stopped, has no live process, process
group or unfinished child, ran against the same target and took the lease while
it ran. A withheld (effect-unknown) apply never releases its lease; this is how
it stops holding the target forever. Persisted host-state fences
(``active_operation``, ``recovery_uncertainty``) are separate and still refuse.
Any other holder (another controller, an unrecorded or live job) is never taken.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

ACQUIRED = "host_apply_lease_acquired"
RELEASED = "host_apply_lease_released"
TAKEN_OVER = "host_apply_lease_taken_over"
_JOB_PAGES = 50


class _Unproven(Exception):
    pass


def apply_lease_owner(job_id: str) -> str:
    """The lease holder a server-cli apply/destroy job writes (never its exec holder)."""
    return hashlib.sha256(("server-cli:" + job_id).encode()).hexdigest()[:32]


def _repository(*, read_only):
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    return JobRepository.open_existing(RUNTIME_DIR / "jobs" / "registry.sqlite3", read_only=read_only)


def record(job_id, kind, lease, target, *, repository=None):
    """Append one acquisition/release to this job's own events; False if it failed.

    A missing record only removes later automatic takeover; the lease itself is
    unaffected, so a failure warns instead of stopping the operation.
    """
    payload = {"holder": lease["owner"], "token": lease["token"], "path": lease["path"], "target": target}
    try:
        own = repository is None
        repository = repository or _repository(read_only=False)
        try:
            repository.append_event(job_id, kind, payload)
        finally:
            if own:
                repository.close()
        return True
    except Exception:  # noqa: BLE001 - evidence only; never changes the lease
        return False


def _lease_body(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        body = os.read(descriptor, 257)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or len(body) > 256):
            raise _Unproven()
        token, holder, since = body.decode("ascii").split()
        if (not re.fullmatch(r"[0-9a-f]{32}", token) or not re.fullmatch(r"[0-9a-f]{32}", holder)
                or not since.isdigit()):
            raise _Unproven()
        return token, holder, int(since)
    finally:
        os.close(descriptor)


def _events(repository, job_id, kind):
    rows = repository.connection.execute(
        "SELECT payload_json FROM job_events WHERE job_id=? AND kind=? ORDER BY sequence",
        (job_id, kind)).fetchall()
    return [json.loads(row[0]) for row in rows]


def _epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _ended_stopped(repository, job_id):
    """The job's snapshot when it ended with its owned work proven stopped."""
    from sandbox.application.server_cli_exec_recovery import ENDED_LIFECYCLES, _ceased
    row = repository.snapshot(job_id)
    stopped = json.loads(row.get("result_json") or "{}")
    process = row.get("process") or {}
    if (row.get("job_id") != job_id or row.get("kind") != "server-cli"
            or row.get("target_kind") != "local"
            or row.get("lifecycle") not in ENDED_LIFECYCLES
            or not row.get("started_at") or not row.get("finished_at")
            or not process.get("supervisor_start_identity") or not process.get("child_start_identity")
            or stopped.get("owned_work_stopped") is not True or stopped.get("stop_error") is not None
            or not isinstance(stopped.get("owned_descendants"), list)):
        raise _Unproven()
    _ceased(process, stopped)  # refuses (ValueError) on any live or reused process
    return row


def _holder_job(repository, holder, token, path, target, since):
    """The ended job whose recorded acquisition is exactly this lease file."""
    cursor = None
    for _ in range(_JOB_PAGES):
        rows = repository.list(kind="server-cli", limit=200, cursor_job_id=cursor)
        for listed in rows:
            job_id = listed["job_id"]
            if apply_lease_owner(job_id) != holder:
                continue
            expected = {"holder": holder, "token": token, "path": str(path), "target": target}
            if expected not in _events(repository, job_id, ACQUIRED):
                raise _Unproven()
            if any(event.get("token") == token for event in _events(repository, job_id, RELEASED)):
                raise _Unproven()  # released, yet the file is back: not this job's lease
            row = _ended_stopped(repository, job_id)
            if not _epoch(row["started_at"]) <= since <= _epoch(row["finished_at"]) + 1:
                raise _Unproven()
            for child in repository.children(job_id):
                _ended_stopped(repository, child["job_id"])
            return job_id
        if len(rows) < 200:
            break
        cursor = rows[-1]["job_id"]
    raise _Unproven()


def take_over_ended_apply_lease(path, *, owner, target, job_id, repository=None):
    """Replace a lease held by an ended, stopped apply/destroy job; ``None`` otherwise.

    Returns ``(lease, previous_job_id)``; release it like any acquired lease.
    ``previous_job_id`` is ``None`` when the lease was already gone.
    """
    from sandbox.application.server_cli_exec_recovery import (
        TAKEOVER_LOCK_SECONDS, _lease_lock, _write_lease)
    path = Path(path)
    with ExitStack() as held:
        try:
            held.enter_context(_lease_lock(path, TAKEOVER_LOCK_SECONDS))
        except (ValueError, OSError):
            return None  # the lock is unsafe, missing or stayed held
        try:
            if repository is None:
                repository = _repository(read_only=False)
                held.callback(repository.close)
            previous = None
            try:
                token, holder, since = _lease_body(path)
            except FileNotFoundError:
                token = None
            if token is not None:
                if holder == owner:
                    raise _Unproven()  # our own lease is never "taken over"
                previous = _holder_job(repository, holder, token, path, target, since)
        except Exception:  # noqa: BLE001 - anything unproven keeps the lease
            return None
        lease = {"path": str(path), "token": secrets.token_hex(16), "owner": owner}
        _write_lease(path, lease["token"], owner)
        if previous is not None:
            # Evidence on the ended job; the new holder records its own acquisition.
            try:
                repository.append_event(previous, TAKEN_OVER, {"holder": holder, "token": token,
                    "path": str(path), "target": target, "by_job_id": job_id})
            except Exception:  # noqa: BLE001 - evidence only; the takeover stands
                pass
        return lease, previous


def acquire_owner_lease(entry, path, project, environment, run):
    """Take this server-cli job's lease, or a dead job's, and record it."""
    import sys
    from sandbox.hosting import target_lease
    job_id = entry.require_current().job_id
    owner, target = apply_lease_owner(job_id), f"{project}/{environment}"
    try:
        lease = target_lease.acquire(run, path, owner)
    except target_lease.LeaseHeld:
        taken = take_over_ended_apply_lease(path, owner=owner, target=target, job_id=job_id)
        if taken is None:
            raise
        lease, previous = taken
        if previous is not None:
            print(f"sb host: took over the target lease of ended job {previous}", file=sys.stderr)
    lease = {**lease, "job_id": job_id, "target": target}
    if not record(job_id, ACQUIRED, lease, target):
        print("sb host: warning: the target lease was not recorded in this job; "
              "if the job dies holding it, recovery is manual", file=sys.stderr)
    return lease


def release_owner_lease(lease, run):
    """Release, and record it only once the release is confirmed."""
    from sandbox.hosting import target_lease
    if lease is None:
        return
    try:
        result = run(target_lease.command(lease["path"], lease["token"], lease["owner"], "release"))
    except Exception:  # noqa: BLE001 - like target_lease.release: the lease stays
        return
    if result.returncode == 0 and lease.get("job_id"):
        record(lease["job_id"], RELEASED, lease, lease["target"])
