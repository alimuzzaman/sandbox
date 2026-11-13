"""Plan-bound retirement of one stopped original server host-exec lease.

This never signals, resumes or replays a job, repairs its result, or retires
scheduler/workspace leases. Historical execution authority is not reissued.
A later exec job may also take over a lease whose exec job has ended with its
owned work stopped (``take_over_ended_exec_lease``); no other lease is taken.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from sandbox.jobs.models import validate_job_id


def _refuse():
    raise ValueError("original_exec_recovery_unavailable: original job, cessation, target or lease proof changed")


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _binding(repository, job_id, owner):
    row = repository.snapshot(job_id)
    proof = json.loads(row.get("execution_policy_provenance_json") or "{}")
    stopped = json.loads(row.get("result_json") or "{}")
    snapshot = json.loads(proof.get("server_cli.target_snapshot") or "{}")
    process = row.get("process") or {}
    target = proof.get("server_cli.exec_target")
    if (row.get("job_id") != job_id or row.get("kind") != "server-cli"
            or row.get("target_kind") != "local"
            or row.get("parent_job_id") is not None or repository.children(job_id)
            or row.get("lifecycle") not in {"failed", "timed_out", "cancelled", "interrupted"}
            or stopped.get("owned_work_stopped") is not True
            or stopped.get("stop_error") is not None
            or not isinstance(stopped.get("owned_descendants"), list)
            or proof.get("server_cli.output_contract") != "artifact.v1"
            or proof.get("server_cli.target_identity") != owner.target_identity
            or proof.get("server_cli.ownership_marker") != owner.ownership_marker
            or snapshot.get("target_identity") != owner.target_identity
            or snapshot.get("ownership_marker") != owner.ownership_marker
            or snapshot.get("runtime_revision") != proof.get("server_cli.runtime_revision")
            or snapshot.get("origin_remote_name") != proof.get("server_cli.remote_name")
            or snapshot.get("source_commit") != row.get("source_commit")
            or snapshot.get("selector_digest") != proof.get("server_cli.target_selector_digest")
            or not isinstance(target, str)
            or target != snapshot.get("project", "") + "/" + snapshot.get("environment", "")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target)
            or not process.get("supervisor_start_identity") or not process.get("child_start_identity")
            or not isinstance(snapshot.get("native_keys"), list) or not snapshot["native_keys"]):
        _refuse()
    return row, proof, stopped, snapshot, process


def _ceased(process, stopped):
    from sandbox.jobs.process import is_boot_session_identity, process_absence_proven
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    pgid = process.get("child_pgid")
    pids = stopped["owned_descendants"] + [process.get("child_pid"), process.get("supervisor_pid")]
    if (not is_boot_session_identity(boot) or boot != process.get("host_boot_id")
            or type(pgid) is not int or pgid <= 1
            or any(type(pid) is not int or pid <= 1 for pid in pids)):
        _refuse()
    # A reused recorded PID conservatively refuses; no signals or adoption.
    # Two complete group scans supplement the original supervisor's durable
    # cessation proof. Missing/denied proc evidence never means absence.
    for _ in range(2):
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                fields = (directory / "stat").read_text().rpartition(")")[2].split()
            except FileNotFoundError:
                continue
            if int(fields[2]) == pgid:
                _refuse()
        if not all(process_absence_proven(pid) for pid in pids):
            _refuse()
    return {"boot_id": boot, "pgid": pgid, "recorded_pids": sorted(set(pids)),
        "owned_work_stopped": True, "group_absent": True, "recorded_pids_absent": True}


@contextmanager
def _lease_lock(path, wait_seconds=0):
    parent = path.parent
    info = parent.lstat()
    if (parent.resolve() != parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022):
        _refuse()
    descriptor = os.open(str(path) + ".lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022):
            _refuse()
        end = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def _lease(path, job_id, row):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        body = os.read(descriptor, 257)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or len(body) > 256):
            _refuse()
        token, holder, since = body.decode("ascii").split()
        expected = hashlib.sha256(("server-cli-exec:" + job_id).encode()).hexdigest()[:32]
        if (not re.fullmatch(r"[0-9a-f]{32}", token) or holder != expected
                or not since.isdigit()
                or not int(datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")).timestamp())
                    <= int(since) <= int(datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")).timestamp()) + 1):
            _refuse()
        return {"device": info.st_dev, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "body_digest": "sha256:" + hashlib.sha256(body).hexdigest()}
    finally:
        os.close(descriptor)


def reconcile_exec_lease(job_id, *, confirm=False, expected_plan=None):
    """Compose only this existing repository, never durable startup services."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    validate_job_id(job_id)
    repository = JobRepository.open_existing(RUNTIME_DIR / "jobs" / "registry.sqlite3",
        read_only=not confirm)
    try:
        return _reconcile_exec_lease(repository, job_id,
            confirm=confirm, expected_plan=expected_plan)
    finally:
        repository.close()


def _reconcile_exec_lease(repository, job_id, *, confirm=False, expected_plan=None):
    validate_job_id(job_id)
    if type(confirm) is not bool or confirm != bool(expected_plan):
        _refuse()
    from sandbox.application.server_cli import read_current_server_owner
    from sandbox.hosting.server_target_selection import inspect_server_exec_target
    from sandbox.hosting.recovery.repository import RecoveryRepository
    owner = read_current_server_owner()
    row, proof, stopped, snapshot, process = _binding(repository, job_id, owner)
    from sandbox.hosting.target_lease import lease_path
    path = Path(lease_path(owner.sandbox_home, snapshot["project"], snapshot["environment"]))
    with ExitStack() as held:
        for key in sorted(snapshot["native_keys"]):
            held.enter_context(RecoveryRepository().effect_lock(key, timeout_seconds=0))
        held.enter_context(_lease_lock(path))
        if repository.list(limit=1, active_only=True):
            _refuse()
        cessation = _ceased(process, stopped)
        current = inspect_server_exec_target(origin_remote_name=proof["server_cli.remote_name"],
            target=proof["server_cli.exec_target"])
        # An installed revision update does not reissue the original context.
        # Every persisted public incarnation field must still match exactly.
        if ({key: value for key, value in current.items() if key not in {"runtime_revision", "selector_digest"}}
                != {key: value for key, value in snapshot.items() if key not in {"runtime_revision", "selector_digest"}}):
            _refuse()
        lease = _lease(path, job_id, row)
        plan = {"job_id": job_id, "request_id": row["request_id"],
            "target_identity": owner.target_identity, "ownership_marker": owner.ownership_marker,
            "installed_runtime_revision": owner.runtime_revision,
            "target": proof["server_cli.exec_target"], "original_provenance": proof,
            "process": process, "cessation": cessation, "lease": lease}
        digest = _digest(plan)
        result = {"ok": True, "job_id": job_id, "request_id": row["request_id"],
            "target": proof["server_cli.exec_target"], "plan_digest": digest,
            "cessation_proven": True, "lease_present": lease is not None,
            "status": "planned" if lease else "lease_absent", "requires_confirm": bool(lease)}
        if not confirm:
            return result
        if expected_plan != digest:
            _refuse()
        if lease is None:
            return result
        # Recheck original row, live installed owner, cessation and exact file
        # under the same native and physical locks immediately before unlink.
        if (_binding(repository, job_id, owner) != (row, proof, stopped, snapshot, process)
                or read_current_server_owner() != owner or _ceased(process, stopped) != cessation
                or _lease(path, job_id, row) != lease):
            _refuse()
        repository.append_event(job_id, "server_exec_lease_retirement_intent",
            {"plan_digest": digest, "lease": lease, "cessation": cessation})
        os.unlink(path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        repository.append_event(job_id, "server_exec_lease_retired", {"plan_digest": digest})
        return {**result, "status": "retired", "lease_present": False, "requires_confirm": False}


ENDED_LIFECYCLES = frozenset({"succeeded", "failed", "timed_out", "cancelled", "interrupted"})
TAKEOVER_LOCK_SECONDS = 5
_JOB_PAGES = 50


def exec_lease_owner(job_id):
    """The lease holder ``sb host exec`` writes for one server-cli job."""
    return hashlib.sha256(("server-cli-exec:" + job_id).encode()).hexdigest()[:32]


def _lease_holder(path):
    """The holder written in the lease file, with the same file checks as recovery."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        body = os.read(descriptor, 257)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or len(body) > 256):
            _refuse()
        holder = body.decode("ascii").split()[1]
        if not re.fullmatch(r"[0-9a-f]{32}", holder):
            _refuse()
        return holder
    finally:
        os.close(descriptor)


def _ended_exec_job(repository, holder, target):
    """The registry's server-cli job whose exec lease ``holder`` is, proven ended and stopped.

    An apply, destroy or controller holder matches no job and refuses.
    """
    cursor = None
    for _ in range(_JOB_PAGES):
        rows = repository.list(kind="server-cli", limit=200, cursor_job_id=cursor)
        for listed in rows:
            if exec_lease_owner(listed["job_id"]) != holder:
                continue
            row = repository.snapshot(listed["job_id"])
            stopped = json.loads(row.get("result_json") or "{}")
            proof = json.loads(row.get("execution_policy_provenance_json") or "{}")
            process = row.get("process") or {}
            # The same job, cessation and identity checks as ``_binding``.
            if (row.get("job_id") != listed["job_id"] or row.get("kind") != "server-cli"
                    or row.get("target_kind") != "local"
                    or row.get("parent_job_id") is not None or repository.children(listed["job_id"])
                    or not process.get("supervisor_start_identity") or not process.get("child_start_identity")
                    or row.get("lifecycle") not in ENDED_LIFECYCLES
                    or stopped.get("owned_work_stopped") is not True
                    or stopped.get("stop_error") is not None
                    or not isinstance(stopped.get("owned_descendants"), list)
                    or proof.get("server_cli.exec_target") != target):
                _refuse()
            # No recorded process, descendant or process group may still run.
            _ceased(row.get("process") or {}, stopped)
            return row
        if len(rows) < 200:
            break
        cursor = rows[-1]["job_id"]
    _refuse()


def _write_lease(path, token, owner):
    """The same body and mode the shell lease script writes, replaced atomically."""
    temporary = str(path) + ".new"
    # Unlink a leftover first and create exclusively, so a pre-placed hard
    # link or symlink is replaced, never written through.
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{token} {owner} {int(time.time())}\n".encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _registry():
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository
    return JobRepository.open_existing(RUNTIME_DIR / "jobs" / "registry.sqlite3", read_only=True)


def take_over_ended_exec_lease(path, *, owner, target, repository=None):
    """Take a target lease held by an ended exec job; ``None`` when it is not one.

    Under the lease's own lock, the holder must be the exec lease of a
    server-cli job in this server's registry that is terminal, recorded its
    owned work stopped, has no live process, ran against ``target`` and took
    the lease while it ran. Returns ``(lease, previous_job_id)``; the lease is
    released like any acquired one. ``previous_job_id`` is ``None`` when the
    lease was already gone.
    """
    path = Path(path)
    with ExitStack() as held:
        try:
            held.enter_context(_lease_lock(path, TAKEOVER_LOCK_SECONDS))
        except (ValueError, OSError):
            return None  # the lock is unsafe, missing or stayed held
        try:
            if repository is None:
                repository = _registry()
                held.callback(repository.close)
            holder = _lease_holder(path)
            previous = None
            if holder is not None:
                row = _ended_exec_job(repository, holder, target)
                _lease(path, row["job_id"], row)
                previous = row["job_id"]
        except Exception:
            # Anything unproven keeps the lease where it is.
            return None
        lease = {"path": str(path), "token": secrets.token_hex(16), "owner": owner}
        _write_lease(path, lease["token"], owner)
        return lease, previous
