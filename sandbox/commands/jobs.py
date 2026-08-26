from __future__ import annotations
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import signal
import subprocess
import time
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register

_JOB_RE = re.compile(r"^[a-f0-9]{16}$")
_JOB_MAX_AGE = 24 * 3600  # prune jobs older than 24h (spec FR-007)
_JOB_ORPHAN_EXIT = 1
_JOB_ACCEPTANCE_TIMEOUT = 15.0
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class RequestIdConflict(ValueError):
    """A request identity was already used for a different WP-CLI command."""


def _job_dir(instance: str, *, create: bool = True) -> Path:
    d = wp_dir(instance) / ".sb-jobs"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_job_id(jid: str) -> bool:
    return bool(_JOB_RE.match(jid or ""))


def _job_name(instance: str, jid: str) -> str:
    return f"sb-job-{instance}-{jid}"


def _job_paths(instance: str, jid: str) -> tuple[Path, Path, Path]:
    """Return the per-job log, terminal-status, and process-handle paths.

    Callers validate ``jid`` before reaching here.  Keeping all three paths in
    one helper makes reaping an atomic *job group* operation rather than a
    best-effort deletion of whichever individual artifact happened to age out.
    """
    job_dir = _job_dir(instance, create=False)
    return tuple(job_dir / f"job_{jid}.{suffix}" for suffix in ("log", "status", "pid"))


def _job_launcher_path(instance: str, jid: str) -> Path:
    """Return the internal launcher marker for a Docker job.

    The marker distinguishes a job running in the shared web container from a
    legacy one-shot `wpcli` container.  It is deliberately not part of the
    public status payload; it only lets polling/cancellation choose the same
    process boundary that accepted the job.
    """
    return _job_dir(instance, create=False) / f"job_{jid}.launcher"


def _job_receipt_path(instance: str, jid: str) -> Path:
    """Return the private acceptance receipt path for a job."""
    return _job_dir(instance, create=False) / f"job_{jid}.receipt"


def validate_request_id(value: object) -> str:
    """Validate the replay identity before touching instance or job state."""
    if not isinstance(value, str) or not _REQUEST_ID_RE.fullmatch(value):
        raise ValueError(
            "request id is invalid (use 1-64 letters, digits, '.', '_' or '-')"
        )
    return value


def _request_record_path(instance: str, request_id: str) -> Path:
    """Return a private, path-safe record keyed by a request-id digest."""
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return _job_dir(instance, create=False) / f"request_{digest}.json"


@contextlib.contextmanager
def _request_lock(instance: str):
    """Serialize request reservation across CLI/MCP processes on one instance."""
    job_dir = _job_dir(instance)
    lock_path = job_dir / ".request.lock"
    with lock_path.open("a+") as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            # Sandbox targets macOS/Linux. On a platform without advisory file
            # locks, the atomic record replace still preserves a readable
            # record; callers fail closed on an ambiguous/malformed record.
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


def _request_digest(wp_args: list[str]) -> str:
    payload = json.dumps(list(wp_args), ensure_ascii=False,
                         separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_request_record(instance: str, request_id: str,
                         command_digest: str) -> str | None:
    path = _request_record_path(instance, request_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RequestIdConflict(
            "request id record is unreadable; inspect the existing job before retrying"
        ) from exc
    job_id = record.get("job_id") if isinstance(record, dict) else None
    if not isinstance(job_id, str) or not _valid_job_id(job_id):
        raise RequestIdConflict(
            "request id record has no valid job id; inspect the existing job before retrying"
        )
    if record.get("version") != 1 or record.get("status") not in {
            "pending", "accepted", "unknown"}:
        raise RequestIdConflict(
            "request id record has an unsupported state; inspect the existing job before retrying"
        )
    if record.get("command_digest") != command_digest:
        raise RequestIdConflict(
            "request id was already used for a different WP-CLI command"
        )
    return job_id


def _write_request_record(instance: str, request_id: str, job_id: str,
                          command_digest: str, status: str) -> None:
    if status not in {"pending", "accepted", "unknown"}:
        raise ValueError("invalid request record status")
    target = _request_record_path(instance, request_id)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    payload = {
        "version": 1,
        "job_id": job_id,
        "command_digest": command_digest,
        "status": status,
    }
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _request_records_for_job(instance: str, jid: str) -> list[Path]:
    """Find request records for one job without trusting their filenames."""
    directory = _job_dir(instance, create=False)
    if not directory.is_dir():
        return []
    matches = []
    for path in directory.glob("request_*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("job_id") == jid:
            matches.append(path)
    return matches


def _job_launcher_mode(instance: str, jid: str) -> str | None:
    try:
        mode = _job_launcher_path(instance, jid).read_text(errors="replace").strip()
    except OSError:
        return None
    return mode or None


def _read_acceptance_receipt(instance: str, jid: str) -> dict | None:
    """Read a validated, value-free acceptance receipt when present."""
    try:
        receipt = json.loads(_job_receipt_path(instance, jid).read_text())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict) or receipt.get("job_id") != jid \
            or receipt.get("status") != "accepted":
        return None
    launcher = receipt.get("launcher")
    elapsed = receipt.get("acceptance_ms")
    try:
        elapsed_value = float(elapsed)
    except (TypeError, ValueError, OverflowError):
        return None
    if launcher not in {"herd", "web-exec", "run"} \
            or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) \
            or not math.isfinite(elapsed_value) or elapsed < 0:
        return None
    return {"launcher": launcher, "acceptance_ms": elapsed_value}


def _write_acceptance_receipt(instance: str, jid: str, launcher: str,
                              started: float) -> None:
    """Atomically retain launcher acceptance timing without command argv/output."""
    if launcher not in {"herd", "web-exec", "run"}:
        raise ValueError("invalid async job launcher")
    target = _job_receipt_path(instance, jid)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    payload = {
        "job_id": jid,
        "status": "accepted",
        "launcher": launcher,
        "acceptance_ms": round(max(0.0, (time.monotonic() - started) * 1000), 3),
        "accepted_at": time.time(),
    }
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _remaining_acceptance_timeout(started: float) -> float:
    """Return a positive Compose deadline shared by probe and launch."""
    return max(0.1, _JOB_ACCEPTANCE_TIMEOUT - (time.monotonic() - started))


def _known_job(paths: tuple[Path, Path, Path]) -> bool:
    return any(path.exists() for path in paths)


def _read_group_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _herd_group_running(pid: int) -> bool:
    """Whether a Herd wrapper's process group still exists.

    ``start_new_session=True`` below makes the wrapper PID its process-group
    leader without depending on an external ``setsid`` executable.
    """
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists, but the current user is not allowed to signal it.
        return True
    return True


def _docker_job_running(instance: str, jid: str) -> bool | None:
    """Return True/False for a known job container, or None if unobservable.

    A Docker daemon/transport failure is deliberately not treated as a dead
    process: doing so would manufacture a terminal result during an outage.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", _job_name(instance, jid)],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return result.stdout.strip().lower() == "true"
    detail = ((result.stdout or "") + (result.stderr or "")).lower()
    if "no such object" in detail or "no such container" in detail:
        return False
    return None


def _docker_exec_job_running(instance: str, jid: str, pid_file: Path) -> bool | None:
    """Observe a job launched inside the already-running web container.

    A shared-container job has no per-job Docker object to inspect.  Probe its
    wrapper PID through the same `compose exec` boundary instead.  Transport
    failures stay unknown; a normal `kill -0` miss means the wrapper is gone.
    """
    pid = _read_group_pid(pid_file)
    if pid is None:
        return None
    try:
        result = compose(
            "exec", "-T", "wp", "sh", "-c", f"kill -0 {pid}",
            instance=instance, check=False, capture=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) == 0:
        return True
    detail = ((getattr(result, "stdout", "") or "") +
              (getattr(result, "stderr", "") or "")).lower()
    if any(marker in detail for marker in (
            "cannot connect", "error response from daemon", "timed out",
            "timeout", "connection refused", "docker: command not found")):
        return None
    return False


def _kill_docker_exec_job(instance: str, jid: str, pid_file: Path) -> bool | None:
    """Ask the shared web container to terminate one wrapper process.

    The wrapper installs a TERM trap and owns the WP-CLI child, so signalling
    this PID does not require removing or restarting the web container.
    """
    pid = _read_group_pid(pid_file)
    if pid is None:
        return None
    try:
        result = compose(
            "exec", "-T", "wp", "sh", "-c", f"kill -TERM {pid}",
            instance=instance, check=False, capture=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) == 0:
        return True
    detail = ((getattr(result, "stdout", "") or "") +
              (getattr(result, "stderr", "") or "")).lower()
    if any(marker in detail for marker in
           ("no such process", "no such container", "is not running")):
        return False
    if any(marker in detail for marker in (
            "cannot connect", "error response from daemon", "timed out",
            "timeout", "connection refused", "docker: command not found")):
        return None
    return None


def _job_process_running(instance: str, jid: str, pid_file: Path) -> bool | None:
    if _is_herd_instance(instance):
        pid = _read_group_pid(pid_file)
        return _herd_group_running(pid) if pid is not None else None
    if _job_launcher_mode(instance, jid) == "web-exec":
        return _docker_exec_job_running(instance, jid, pid_file)
    return _docker_job_running(instance, jid)


def _record_terminal(status_file: Path, exit_code: int) -> None:
    """Persist a terminal state only after its process is known to be gone."""
    status_file.write_text(str(exit_code))


def _reconcile_job(instance: str, jid: str, paths: tuple[Path, Path, Path] | None = None) -> str:
    """Turn a dead, unrecorded job into a durable terminal failure.

    A status file remains authoritative.  If a known process handle says the
    wrapper/container is gone before it could write that file, record a stable
    non-zero result so polling cannot claim ``running`` indefinitely.
    """
    log, status_file, pid_file = paths or _job_paths(instance, jid)
    if status_file.exists():
        return "completed"
    if not _known_job((log, status_file, pid_file)):
        return "not_found"
    running = _job_process_running(instance, jid, pid_file)
    if running is False:
        _record_terminal(status_file, _JOB_ORPHAN_EXIT)
        return "completed"
    return "running"


def _launch_job(instance: str, wp_args: list[str], jid: str,
                started: float) -> str:
    """Launch one already-reserved job identity."""
    _job_dir(instance)  # ensure exists
    quoted = " ".join(shlex.quote(a) for a in wp_args)
    if _is_herd_instance(instance):
        root = wp_dir(instance)
        wp = (" ".join(shlex.quote(x) for x in _herd_wp_cmd(instance))
              + f" --path={shlex.quote(str(root))}")
        wrapper = (
            f"echo $$ > .sb-jobs/job_{jid}.pid; "
            f"{wp} {quoted} > .sb-jobs/job_{jid}.log 2>&1; "
            f"echo $? > .sb-jobs/job_{jid}.status"
        )
        process = subprocess.Popen(["sh", "-c", wrapper], cwd=str(root),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        # The wrapper writes the same value as ``$$`` once scheduled.  Write it
        # here too so an immediate poll/cancel has a process handle instead of
        # racing the shell's first instruction.
        _job_paths(instance, jid)[2].write_text(str(process.pid))
        _write_acceptance_receipt(instance, jid, "herd", started)
    else:
        # Apache and Nginx web services mount the shipped wp-cli phar at
        # /usr/local/bin/wp.  Reuse that already-running container so async
        # acceptance does not pay the per-job `compose run` container-create
        # cost.  Older instances, LiteSpeed, and stopped web services do not
        # necessarily have that binary; retain the run-style wpcli fallback
        # until the new path has parity evidence for those cases.
        wrapper = (
            "job_root=/var/www/html; "
            "if [ -d /var/www/vhosts/localhost/html/.sb-jobs ]; then "
            "job_root=/var/www/vhosts/localhost/html; fi; "
            f"echo $$ > \"$job_root/.sb-jobs/job_{jid}.pid\"; "
            # Keep the child under a shell-owned PID so a shared-container
            # cancellation can signal the wrapper and have it reap WP-CLI.
            "trap 'kill -TERM \"$child\" 2>/dev/null || true; "
            "wait \"$child\" 2>/dev/null || true; exit 143' TERM INT; "
            f"wp {quoted} > \"$job_root/.sb-jobs/job_{jid}.log\" 2>&1 & "
            "child=$!; wait \"$child\"; rc=$?; "
            f"echo \"$rc\" > \"$job_root/.sb-jobs/job_{jid}.status\""
        )
        # `wp db ...` needs the mysql client that only the dedicated wpcli
        # image carries; keep that command family on the run-style service
        # even when the web container has the built-in phar.
        use_builtin = (
            bool(wp_args) and wp_args[0] != "db" and
            _wp_has_builtin_cli(instance, timeout=_remaining_acceptance_timeout(started))
        )
        if use_builtin:
            # `exec -d` is the lightweight detached launcher.  The web
            # container is the cancellation boundary for this path; the
            # wrapper's PID remains an internal observation handle.
            compose("exec", "-d", "-u", "www-data", "-T", "wp", "sh", "-c",
                    wrapper, instance=instance,
                    timeout=_remaining_acceptance_timeout(started))
            _job_launcher_path(instance, jid).write_text("web-exec\n")
            launcher = "web-exec"
        else:
            # wpcli is a run-style service; entrypoint is `wp`, override to sh
            # (gotcha #6). This compatibility path starts dependencies when
            # the web service is unavailable.
            compose("run", "-d", "--name", _job_name(instance, jid),
                    "--entrypoint", "sh", "wpcli", "-c", wrapper,
                    instance=instance,
                    timeout=_remaining_acceptance_timeout(started))
            _job_launcher_path(instance, jid).write_text("run\n")
            launcher = "run"
        _write_acceptance_receipt(instance, jid, launcher, started)
        # The launcher has accepted the detached shell by this point. The
        # empty log is the durable running marker until the wrapper writes its
        # PID and first output.
        _job_paths(instance, jid)[0].touch(exist_ok=True)
    return jid


def launch_job(instance: str, wp_args: list[str], *,
               request_id: str | None = None) -> str:
    """Start `wp <wp_args>` detached; return a 16-hex job id.

    An optional request ID makes an uncertain caller replay-safe: the same
    instance/request/command returns the original job ID without launching a
    second process, while reusing the ID for different argv fails closed. The
    request record stores only a command digest and job ID, never the command
    text or output.
    """
    started = time.monotonic()
    if request_id is None:
        return _launch_job(instance, wp_args, secrets.token_hex(8), started)
    request_id = validate_request_id(request_id)
    command_digest = _request_digest(wp_args)
    with _request_lock(instance):
        existing = _read_request_record(instance, request_id, command_digest)
        if existing is not None:
            return existing
        jid = secrets.token_hex(8)
        _write_request_record(instance, request_id, jid, command_digest, "pending")
        try:
            result = _launch_job(instance, wp_args, jid, started)
        except BaseException:
            # Preserve the reserved ID after an acceptance/transport failure so
            # a replay inspects the same outcome instead of starting a duplicate.
            try:
                _write_request_record(instance, request_id, jid, command_digest, "unknown")
            except OSError:
                pass
            raise
        _write_request_record(instance, request_id, jid, command_digest, "accepted")
        return result


def job_status(instance: str, jid: str, offset: int = 0, limit: int = 1_048_576) -> dict:
    if not _valid_job_id(jid):
        raise ValueError("invalid job id")
    if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or isinstance(limit, bool) or not isinstance(limit, int) or limit < -1):
        raise ValueError("offset must be non-negative and limit must be -1 or non-negative")
    log, st, pid_file = _job_paths(instance, jid)
    state = _reconcile_job(instance, jid, (log, st, pid_file))
    if state == "not_found":
        return {"job_id": jid, "status": "not_found"}
    out = {"job_id": jid, "status": state, "stdout": "", "bytes_read": 0, "truncated": False}
    receipt = _read_acceptance_receipt(instance, jid)
    if receipt is not None:
        out.update(receipt)
    if st.exists():
        c = st.read_text().strip()
        if c:
            try:
                out["exit_code"] = int(c)
            except ValueError:
                out["exit_code"] = _JOB_ORPHAN_EXIT
    if log.exists():
        size = log.stat().st_size
        if offset < size:
            with log.open("rb") as f:
                f.seek(max(0, offset))
                data = f.read(size - offset if limit == -1 else limit)
            out["stdout"] = data.decode("utf-8", "replace")
            out["bytes_read"] = len(data)
            out["truncated"] = limit != -1 and (offset + len(data)) < size
    return out


def kill_job(instance: str, jid: str) -> dict:
    if not _valid_job_id(jid):
        raise ValueError("invalid job id")
    log, st, pid_file = _job_paths(instance, jid)
    if not _known_job((log, st, pid_file)):
        return {"job_id": jid, "status": "not_found", "killed": False}
    state = _reconcile_job(instance, jid, (log, st, pid_file))
    if state == "completed":
        return {"job_id": jid, "status": "completed", "killed": False}
    if _is_herd_instance(instance):
        pid = _read_group_pid(pid_file)
        if pid is None:
            return {"job_id": jid, "status": "running", "killed": False,
                    "error": "job process group is not available"}
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            _record_terminal(st, _JOB_ORPHAN_EXIT)
            return {"job_id": jid, "status": "completed", "exit_code": _JOB_ORPHAN_EXIT,
                    "killed": False}
        except PermissionError:
            return {"job_id": jid, "status": "running", "killed": False,
                    "error": "permission denied terminating job process group"}
        deadline = time.monotonic() + 2
        while _herd_group_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        terminated = not _herd_group_running(pid)
    elif _job_launcher_mode(instance, jid) == "web-exec":
        requested = _kill_docker_exec_job(instance, jid, pid_file)
        if requested is False:
            _record_terminal(st, _JOB_ORPHAN_EXIT)
            return {"job_id": jid, "status": "completed", "exit_code": _JOB_ORPHAN_EXIT,
                    "killed": False}
        if requested is not True:
            return {"job_id": jid, "status": "running", "killed": False,
                    "error": "job termination request could not be verified"}
        pid = _read_group_pid(pid_file)
        deadline = time.monotonic() + 2
        while pid is not None and time.monotonic() < deadline:
            if _docker_exec_job_running(instance, jid, pid_file) is False:
                break
            time.sleep(0.05)
        terminated = pid is not None and _docker_exec_job_running(instance, jid, pid_file) is False
    else:
        result = subprocess.run(["docker", "rm", "-f", _job_name(instance, jid)],
                                check=False, capture_output=True, text=True)
        # Do not create a successful cancellation record solely because the
        # removal command returned.  The follow-up inspect is the proof that
        # no child process remains in the detached container.
        terminated = result.returncode == 0 and _docker_job_running(instance, jid) is False
    if not terminated:
        return {"job_id": jid, "status": "running", "killed": False,
                "error": "job termination could not be verified"}
    _record_terminal(st, 143)
    return {"job_id": jid, "status": "completed", "exit_code": 143, "killed": True}


def prune_jobs(instance: str, max_age: int = _JOB_MAX_AGE) -> int:
    jd = _job_dir(instance, create=False)
    if not jd.is_dir():
        return 0
    now = time.time()
    n = 0
    job_ids = sorted({match.group(1) for path in jd.glob("job_*")
                      if (match := re.fullmatch(r"job_([a-f0-9]{16})\.(?:log|status|pid)", path.name))})
    for jid in job_ids:
        paths = _job_paths(instance, jid)
        try:
            # Reconcile first: only terminal job *groups* are eligible.  An
            # active long-running log must never be partially removed merely
            # because its first output is older than the retention window.
            if _reconcile_job(instance, jid, paths) != "completed":
                continue
            artifacts = (*paths, _job_launcher_path(instance, jid),
                         _job_receipt_path(instance, jid))
            newest = max(path.stat().st_mtime for path in artifacts if path.exists())
            if now - newest > max_age:
                for path in artifacts:
                    path.unlink(missing_ok=True)
                for path in _request_records_for_job(instance, jid):
                    path.unlink(missing_ok=True)
                n += 1
        except OSError:
            pass
    return n


def cmd_job(cfg, args) -> None:
    inst = args.resolved_instance
    jid = args.job_id
    if not _valid_job_id(jid):
        die("invalid job id (expected 16 hex chars)")
    if getattr(args, "kill", False):
        r = kill_job(inst, jid)
        ok(f"job {jid}: {'killed' if r.get('killed') else 'already finished'}")
        return
    if getattr(args, "follow", False):
        offset = 0
        while True:
            s = job_status(inst, jid, offset=offset)
            chunk = s.get("stdout", "")
            if chunk:
                print(chunk, end="")
                offset += s.get("bytes_read", 0)
            if s["status"] != "running":
                print(f"\n[{s['status']} exit={s.get('exit_code', '?')}]")
                return
            time.sleep(1)
    s = job_status(inst, jid)
    print(f"job {jid}: {s['status']}" + (f" (exit {s['exit_code']})" if "exit_code" in s else ""))
    if s.get("stdout"):
        print(s["stdout"], end="")


def cmd_async_job(cfg, args) -> None:
    """`./sb async-job <job_id> [--follow] [--kill] [--json]` — poll/follow/
    kill a background e2e/ci run started with `--async` (sandbox/core/
    _asyncjobs.py). NOT instance-scoped — e2e/ci jobs mint multiple instances
    themselves, so they don't fit commands/jobs.py's per-instance `job`/`jobs`
    (one wp-cli command in one container)."""
    from sandbox.application.context import durable_job_dependencies
    from sandbox.jobs.models import OutputQuery
    from sandbox.transports.jobs import AsyncJobCompatibilityRouter, LegacyAsyncJobAdapter

    durable = None

    def durable_service():
        nonlocal durable
        if durable is None:
            durable = durable_job_dependencies()["job_service"]
        return durable

    adapter = AsyncJobCompatibilityRouter(
        LegacyAsyncJobAdapter(valid_async_job_id, background_job_status, kill_background_job),
        durable_status=lambda job_id: durable_service().get(job_id),
        durable_output=lambda job_id, *, offset, limit: durable_service().read_output(
            job_id, OutputQuery(offset=offset, max_bytes=limit)),
        durable_cancel=lambda job_id: durable_service().cancel(job_id),
    )
    jid = args.job_id
    try:
        adapter._kind(jid)
    except ValueError:
        die("invalid job id (expected 16 hex chars)")
    if getattr(args, "kill", False):
        r = adapter.cancel(jid)
        if getattr(args, "json", False):
            print(json.dumps(r))
        else:
            ok(f"job {jid}: {'killed' if r.get('killed') else 'already finished'}")
        return
    if getattr(args, "follow", False) and not getattr(args, "json", False):
        offset = 0
        while True:
            s = adapter.status(jid, offset=offset)
            chunk = s.get("stdout", "")
            if chunk:
                print(chunk, end="")
                offset += s.get("bytes_read", 0)
            if s["status"] != "running":
                print(f"\n[{s['status']} exit={s.get('exit_code', '?')}]")
                return
            time.sleep(1)
    s = adapter.status(jid, offset=int(getattr(args, "offset", 0) or 0))
    if getattr(args, "json", False):
        print(json.dumps(s))
        return
    print(f"job {jid}: {s['status']}" + (f" (exit {s['exit_code']})" if "exit_code" in s else ""))
    if s.get("stdout"):
        print(s["stdout"], end="")


def cmd_jobs(cfg, args) -> None:
    inst = args.resolved_instance
    if getattr(args, "prune", False):
        n = prune_jobs(inst)
        ok(f"pruned {n} old terminal job group(s)")
        return
    # Ordinary list is also the retention sweep required by FR-007.
    prune_jobs(inst)
    jd = _job_dir(inst, create=False)
    ids = sorted({match.group(1) for path in jd.glob("job_*")
                  if (match := re.fullmatch(r"job_([a-f0-9]{16})\.(?:log|status|pid)", path.name))}) if jd.is_dir() else []
    if not ids:
        info(f"no jobs for instance '{inst}'")
        return
    for jid in ids:
        s = job_status(inst, jid)
        print(f"  {jid}  {s['status']:<10}" + (f" exit={s['exit_code']}" if "exit_code" in s else ""))


register({'job': cmd_job, 'jobs': cmd_jobs, 'async-job': cmd_async_job})
