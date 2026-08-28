from __future__ import annotations
import json
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
_JOB_LAUNCH_POLL_SECONDS = 0.05
_JOB_LAUNCH_STOP_SECONDS = 2.0


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


def _cleanup_receipt_path(instance: str, jid: str) -> Path:
    return _job_dir(instance, create=False) / f"job_{jid}.cleanup"


def _known_job(paths: tuple[Path, Path, Path]) -> bool:
    return any(path.exists() for path in paths)


def _read_group_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _read_docker_launcher_pid(pid_file: Path) -> int | None:
    try:
        marker = pid_file.read_text().strip()
        prefix, raw = marker.split(":", 1)
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if prefix == "launch" and pid > 1 else None


def _read_docker_handle(pid_file: Path) -> str | None:
    try:
        marker = pid_file.read_text().strip()
    except OSError:
        return None
    if marker == "container":
        return marker
    return "launch" if _read_docker_launcher_pid(pid_file) is not None else None


def _write_new_artifact(path: Path, value: str) -> None:
    """Create one durable marker without replacing another job's evidence."""
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_cleanup_receipt(instance: str, jid: str, kind: str, pid: int) -> Path:
    if kind == "docker":
        value = f"docker-cleanup-v1|{pid}|{pid}|{_job_name(instance, jid)}"
    elif kind == "herd":
        value = f"herd-cleanup-v1|{pid}|{pid}"
    else:
        raise ValueError("invalid cleanup receipt kind")
    path = _cleanup_receipt_path(instance, jid)
    _write_new_artifact(path, value)
    return path


def _read_cleanup_receipt(instance: str, jid: str) -> tuple[str, int] | None:
    path = _cleanup_receipt_path(instance, jid)
    try:
        with path.open("rb") as handle:
            raw = handle.read(513)
    except OSError:
        return None
    if len(raw) > 512:
        return None
    try:
        fields = raw.decode("ascii").split("|")
    except UnicodeDecodeError:
        return None
    try:
        if len(fields) == 3 and fields[0] == "herd-cleanup-v1":
            pid, pgid = int(fields[1]), int(fields[2])
            return ("herd", pid) if pid > 1 and pid == pgid else None
        if len(fields) == 4 and fields[0] == "docker-cleanup-v1":
            pid, pgid = int(fields[1]), int(fields[2])
            expected = _job_name(instance, jid)
            return (
                ("docker", pid)
                if pid > 1 and pid == pgid and fields[3] == expected
                else None
            )
    except ValueError:
        return None
    return None


def _remove_launch_artifacts(paths: tuple[Path, Path, Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _herd_group_running(pid: int) -> bool | None:
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
    except OSError:
        return None
    return True


def _docker_container_running(instance: str, jid: str) -> bool | None:
    """Return True/False for a known job container, or None if unobservable.

    A Docker daemon/transport failure is deliberately not treated as a dead
    process: doing so would manufacture a terminal result during an outage.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", _job_name(instance, jid)],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        state = result.stdout.strip().lower()
        if state == "true":
            return True
        if state == "false":
            return False
        return None
    detail = ((result.stdout or "") + (result.stderr or "")).lower()
    if "no such object" in detail or "no such container" in detail:
        return False
    return None


def _docker_supervisor_running(instance: str, jid: str, pid: int) -> bool | None:
    """Observe one receipt-bound Docker supervisor as running/dead/unknown."""
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "pid=,pgid=,command="],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = (result.stdout or "").strip().split(maxsplit=2)
    if result.returncode != 0:
        # ``ps`` uses exit 1 with no row for an absent process.  Other probe
        # failures cannot authorize either reconciliation or signalling.
        return (
            False
            if result.returncode == 1 and not fields and not (result.stderr or "").strip()
            else None
        )
    if len(fields) != 3:
        return None
    try:
        observed_pid, observed_pgid = int(fields[0]), int(fields[1])
    except ValueError:
        return False
    return (
        observed_pid == pid
        and observed_pgid == pid
        and _job_name(instance, jid) in fields[2]
    )


def _docker_launcher_running(instance: str, jid: str, pid_file: Path) -> bool | None:
    """Observe the exact host supervisor as running, dead, or unknown."""
    pid = _read_docker_launcher_pid(pid_file)
    return (
        _docker_supervisor_running(instance, jid, pid)
        if pid is not None else None
    )


def _herd_launcher_running(jid: str, pid: int) -> bool | None:
    """Validate the receipt-bound Herd wrapper before signalling its group."""
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "pid=,pgid=,command="],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = (result.stdout or "").strip().split(maxsplit=2)
    if result.returncode != 0:
        return (
            False
            if result.returncode == 1 and not fields and not (result.stderr or "").strip()
            else None
        )
    if len(fields) != 3:
        return None
    try:
        observed_pid, observed_pgid = int(fields[0]), int(fields[1])
    except ValueError:
        return None
    return (
        observed_pid == pid
        and observed_pgid == pid
        and f"job_{jid}.log" in fields[2]
        and "wp" in fields[2]
    )


def _docker_job_running(instance: str, jid: str, pid_file: Path) -> bool | None:
    handle = _read_docker_handle(pid_file)
    container = _docker_container_running(instance, jid)
    if container is True:
        return True
    if handle == "container":
        return container
    if handle == "launch":
        launcher = _docker_launcher_running(instance, jid, pid_file)
        if launcher is True:
            return True
        # Before the atomic transition to ``container``, absence of the named
        # container does not prove terminal failure.  The supervisor may still
        # be between create and publication, and a failed probe is unknown.
        return None
    return None


def _remove_docker_job_container(instance: str, jid: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "-f", _job_name(instance, jid)],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _stop_owned_group(
    pgid: int,
    observe,
    process: subprocess.Popen | None = None,
) -> bool:
    """TERM, then KILL if needed, and prove the entire owned group absent."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        return False
    if process is not None:
        try:
            process.wait(timeout=_JOB_LAUNCH_STOP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass
    state = observe()
    if state is False:
        return True
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        return False
    if process is not None:
        try:
            process.wait(timeout=_JOB_LAUNCH_STOP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass
    deadline = time.monotonic() + _JOB_LAUNCH_STOP_SECONDS
    state = observe()
    while state is True and time.monotonic() < deadline:
        time.sleep(_JOB_LAUNCH_POLL_SECONDS)
        state = observe()
    return state is False


def _stop_process_group(process: subprocess.Popen) -> bool:
    return _stop_owned_group(
        process.pid,
        lambda: _herd_group_running(process.pid),
        process,
    )


def _retain_cleanup_unknown(log_file: Path) -> None:
    """Keep bounded, secret-free evidence when launch cleanup is uncertain."""
    try:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("[sandbox] launch cleanup could not be verified\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def _abort_docker_launch(
    instance: str,
    jid: str,
    process: subprocess.Popen,
    log_file: Path,
) -> bool:
    stopped = _stop_owned_group(
        process.pid,
        lambda: _herd_group_running(process.pid),
        process,
    )
    _remove_docker_job_container(instance, jid)
    absent = _docker_container_running(instance, jid) is False
    if not (stopped and absent):
        _retain_cleanup_unknown(log_file)
        return False
    return True


def _retry_cleanup_receipt(instance: str, jid: str, kind: str, pid: int) -> bool:
    if kind == "herd":
        state = _herd_launcher_running(jid, pid)
        if state is None:
            return False
        group = _herd_group_running(pid)
        if group is None:
            return False
        if state is False:
            # The original wrapper is gone. A live group at this reused PGID
            # is not receipt-owned and must never be signalled.
            return group is False
        stopped = group is False or _stop_owned_group(
            pid, lambda: _herd_group_running(pid),
        )
        return stopped
    state = _docker_supervisor_running(instance, jid, pid)
    if state is None:
        return False
    group = _herd_group_running(pid)
    if group is None:
        return False
    if state is False:
        stopped = group is False
    else:
        stopped = group is False or _stop_owned_group(
            pid, lambda: _herd_group_running(pid),
        )
    _remove_docker_job_container(instance, jid)
    return stopped and _docker_container_running(instance, jid) is False


def _job_process_running(instance: str, jid: str, pid_file: Path) -> bool | None:
    if _is_herd_instance(instance):
        pid = _read_group_pid(pid_file)
        return _herd_group_running(pid) if pid is not None else None
    return _docker_job_running(instance, jid, pid_file)


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
    receipt = _read_cleanup_receipt(instance, jid) if not pid_file.exists() else None
    if receipt is not None:
        if _retry_cleanup_receipt(instance, jid, *receipt):
            _record_terminal(status_file, _JOB_ORPHAN_EXIT)
            try:
                _cleanup_receipt_path(instance, jid).unlink()
            except OSError:
                pass
            return "completed"
        return "running"
    running = _job_process_running(instance, jid, pid_file)
    if running is False:
        _record_terminal(status_file, _JOB_ORPHAN_EXIT)
        return "completed"
    return "running"


def launch_job(instance: str, wp_args: list[str]) -> str:
    """Start `wp <wp_args>` detached; return a 16-hex job id. State lands in
    runtime/wp-<instance>/.sb-jobs/job_<id>.{pid,log,status}."""
    jid = secrets.token_hex(8)
    _job_dir(instance)  # ensure exists
    paths = _job_paths(instance, jid)
    log_file, status_file, pid_file = paths
    quoted = " ".join(shlex.quote(a) for a in wp_args)
    if _is_herd_instance(instance):
        root = wp_dir(instance)
        wp = (" ".join(shlex.quote(x) for x in _herd_wp_cmd(instance))
              + f" --path={shlex.quote(str(root))}")
        wrapper = (
            f"{wp} {quoted} > .sb-jobs/job_{jid}.log 2>&1; "
            f"echo $? > .sb-jobs/job_{jid}.status"
        )
        _write_new_artifact(log_file, "")
        try:
            process = subprocess.Popen(
                ["sh", "-c", wrapper], cwd=str(root),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            _remove_launch_artifacts(paths)
            raise
        try:
            receipt_path = _write_cleanup_receipt(
                instance, jid, "herd", process.pid,
            )
            _write_new_artifact(pid_file, str(process.pid))
        except Exception:
            cleanup_proven = _stop_process_group(process)
            if cleanup_proven:
                _remove_launch_artifacts(paths)
                try:
                    _cleanup_receipt_path(instance, jid).unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                _retain_cleanup_unknown(log_file)
            raise
        try:
            receipt_path.unlink()
        except OSError:
            # A retained receipt is safe: the normal pid handle remains
            # authoritative and the status path will reconcile it.
            pass
    else:
        wrapper = (
            f"wp {quoted} > /var/www/html/.sb-jobs/job_{jid}.log 2>&1; "
            f"echo $? > /var/www/html/.sb-jobs/job_{jid}.status"
        )
        name = _job_name(instance, jid)
        temporary_handle = str(pid_file) + ".container"
        supervisor = """
container_name=$1
status_file=$2
handle_file=$3
temporary_handle=$4
shift 4
while [ ! -s "$handle_file" ]; do sleep 0.01; done
container_absent() {
  observation=$(docker inspect --type container "$container_name" 2>&1)
  code=$?
  if [ "$code" -eq 0 ]; then return 1; fi
  case "$observation" in
    *"No such object"*|*"No such container"*) return 0 ;;
  esac
  return 1
}
cleanup_and_record() {
  terminal_code=$1
  docker rm -f -- "$container_name" >/dev/null 2>&1 || :
  if ! container_absent; then return 1; fi
  printf '%s' "$terminal_code" > "$status_file" || return 1
  rm -f -- "$temporary_handle"
  return 0
}
cancel_launch() {
  trap - TERM INT HUP
  if cleanup_and_record 143; then exit 143; fi
  exit 125
}
trap cancel_launch TERM INT HUP
"$@" >/dev/null 2>&1
code=$?
if [ "$code" -eq 0 ]; then
  if printf 'container' > "$temporary_handle" && mv -f -- "$temporary_handle" "$handle_file"; then
    exit 0
  fi
  if cleanup_and_record 1; then exit 1; fi
  exit 125
else
  if cleanup_and_record "$code"; then exit "$code"; fi
  exit 125
fi
""".strip()
        compose_argv = [
            "docker", "compose", "-p", project_name(instance),
            "-f", str(compose_file(instance)),
            "--project-directory", str(ROOT),
            "run", "-d", "--name", name,
            "--entrypoint", "sh", "wpcli", "-c", wrapper,
        ]
        try:
            _write_new_artifact(log_file, "")
            process = subprocess.Popen(
                ["sh", "-c", supervisor, name, name, str(status_file),
                 str(pid_file), temporary_handle, *compose_argv],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            _remove_launch_artifacts(paths)
            try:
                Path(temporary_handle).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            receipt_path = _write_cleanup_receipt(
                instance, jid, "docker", process.pid,
            )
            _write_new_artifact(pid_file, f"launch:{process.pid}")
        except Exception:
            cleanup_proven = _abort_docker_launch(
                instance, jid, process, log_file,
            )
            if cleanup_proven:
                _remove_launch_artifacts(paths)
                try:
                    Path(temporary_handle).unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    _cleanup_receipt_path(instance, jid).unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        try:
            receipt_path.unlink()
        except OSError:
            pass
    return jid


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
        if _herd_launcher_running(jid, pid) is not True:
            return {"job_id": jid, "status": "running", "killed": False,
                    "error": "job process identity could not be verified"}
        terminated = _stop_owned_group(
            pid, lambda: _herd_group_running(pid),
        )
    else:
        handle = _read_docker_handle(pid_file)
        launcher_pid = _read_docker_launcher_pid(pid_file)
        launcher_state = (
            _docker_launcher_running(instance, jid, pid_file)
            if handle == "launch" else False
        )
        group_stopped = handle == "container"
        if launcher_pid is not None and launcher_state is True:
            group_stopped = _stop_owned_group(
                launcher_pid, lambda: _herd_group_running(launcher_pid),
            )
        elif launcher_pid is not None and launcher_state is False:
            group = _herd_group_running(launcher_pid)
            if group is False:
                group_stopped = True
            # If a group still exists after exact supervisor mismatch, the
            # PGID may have been reused. Refuse rather than signal it.
        _remove_docker_job_container(instance, jid)
        # Do not create a successful cancellation record solely because the
        # removal command returned. Both owner and container observations must
        # be exact; unknown never becomes terminal.
        if handle == "launch":
            launcher_state = _docker_launcher_running(instance, jid, pid_file)
            terminated = (
                group_stopped
                and
                launcher_state is False
                and _docker_container_running(instance, jid) is False
            )
        elif handle == "container":
            terminated = _docker_container_running(instance, jid) is False
        else:
            terminated = False
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
                      if (match := re.fullmatch(r"job_([a-f0-9]{16})\.(?:log|status|pid|cleanup)", path.name))})
    for jid in job_ids:
        paths = _job_paths(instance, jid)
        try:
            # Reconcile first: only terminal job *groups* are eligible.  An
            # active long-running log must never be partially removed merely
            # because its first output is older than the retention window.
            if _reconcile_job(instance, jid, paths) != "completed":
                continue
            newest = max(path.stat().st_mtime for path in paths if path.exists())
            if now - newest > max_age:
                for path in paths:
                    path.unlink(missing_ok=True)
                _cleanup_receipt_path(instance, jid).unlink(missing_ok=True)
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
                  if (match := re.fullmatch(r"job_([a-f0-9]{16})\.(?:log|status|pid|cleanup)", path.name))}) if jd.is_dir() else []
    if not ids:
        info(f"no jobs for instance '{inst}'")
        return
    for jid in ids:
        s = job_status(inst, jid)
        print(f"  {jid}  {s['status']:<10}" + (f" exit={s['exit_code']}" if "exit_code" in s else ""))


register({'job': cmd_job, 'jobs': cmd_jobs, 'async-job': cmd_async_job})
