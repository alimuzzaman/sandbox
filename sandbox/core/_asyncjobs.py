from __future__ import annotations
import os
import re
import secrets
import shlex
import signal
import subprocess
from pathlib import Path

# Generic HOST-level detached background job runner, for operations that
# don't fit sandbox/commands/jobs.py's model (`launch_job`/`job_status`/
# `kill_job`) — that one runs exactly one `wp <args>` command INSIDE one
# instance's container, keyed under that instance's own wp_dir. `run_e2e` and
# `ci_run` aren't instance-scoped at all (they mint MULTIPLE instances via the
# fan-out helper) and run partly on the host (`npx playwright`, `act`), so they
# need their own job directory independent of any single instance. Mirrors the
# same on-disk contract (log/status/pid files, incremental offset reads) so
# polling feels identical from the caller's side.

_JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")


def valid_async_job_id(jid: str) -> bool:
    return bool(_JOB_ID_RE.match(jid or ""))


def _jobs_root() -> Path:
    # RUNTIME_DIR is back-filled from sandbox.core._paths — the same resolved
    # $SANDBOX_HOME/runtime every other instance-state path derives from.
    d = RUNTIME_DIR / "async-jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def launch_background_job(cmd: list[str], cwd) -> str:
    """Run `cmd` fully detached — survives the launching process's own exit,
    unlike a plain `subprocess.Popen` background process, which a short-lived
    CLI invocation would otherwise take down with it. `start_new_session=True`
    makes Python call `setsid()` itself before exec (POSIX) — no dependency on
    the external `setsid` BINARY, which macOS doesn't ship (util-linux is
    Linux-only); this runs identically on both. Returns a 16-hex job id
    immediately; poll with `background_job_status`."""
    jid = secrets.token_hex(8)
    jdir = _jobs_root() / jid
    jdir.mkdir(parents=True)
    log, status, pidfile, script = (jdir / "output.log", jdir / "status",
                                    jdir / "pid", jdir / "run.sh")
    quoted_cmd = " ".join(shlex.quote(c) for c in cmd)
    script.write_text(
        "#!/bin/sh\n"
        # Redirected (non-tty) stdout makes Python switch from line- to full
        # block-buffering — without this, `--follow` shows NOTHING until the
        # buffer fills or the process exits, even though it's actively
        # running (caught live: docker containers progressing, output.log
        # still 0 bytes minutes in). PYTHONUNBUFFERED forces real streaming.
        "export PYTHONUNBUFFERED=1\n"
        f"echo $$ > {shlex.quote(str(pidfile))}\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"{quoted_cmd} > {shlex.quote(str(log))} 2>&1\n"
        f"echo $? > {shlex.quote(str(status))}\n"
    )
    script.chmod(0o755)
    subprocess.Popen(["sh", str(script)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return jid


def background_job_status(jid: str, offset: int = 0,
                          limit: int = 1_048_576) -> dict:
    """{job_id, status: running|completed|not_found, exit_code?, stdout,
    bytes_read, truncated} — same shape as commands/jobs.py's job_status so
    polling code (and agents) don't need two mental models."""
    jdir = _jobs_root() / jid
    log, status = jdir / "output.log", jdir / "status"
    if not jdir.is_dir():
        return {"job_id": jid, "status": "not_found"}
    st = "completed" if status.exists() else "running"
    out = {"job_id": jid, "status": st, "stdout": "", "bytes_read": 0, "truncated": False}
    if status.exists():
        c = status.read_text().strip()
        if c:
            try:
                out["exit_code"] = int(c)
            except ValueError:
                pass
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


def kill_background_job(jid: str) -> dict:
    jdir = _jobs_root() / jid
    status, pidfile = jdir / "status", jdir / "pid"
    if not jdir.is_dir():
        return {"job_id": jid, "status": "not_found"}
    if status.exists():
        return {"job_id": jid, "status": "completed", "killed": False}
    pid = pidfile.read_text().strip() if pidfile.exists() else ""
    if pid:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    status.write_text("143")
    return {"job_id": jid, "status": "completed", "exit_code": 143, "killed": True}
