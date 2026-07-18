from __future__ import annotations
import json
import re
import secrets
import shlex
import subprocess
import time
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register

_JOB_RE = re.compile(r"^[a-f0-9]{16}$")
_JOB_MAX_AGE = 24 * 3600  # prune jobs older than 24h (spec FR-007)


def _job_dir(instance: str) -> Path:
    d = wp_dir(instance) / ".sb-jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_job_id(jid: str) -> bool:
    return bool(_JOB_RE.match(jid or ""))


def _job_name(instance: str, jid: str) -> str:
    return f"sb-job-{instance}-{jid}"


def launch_job(instance: str, wp_args: list[str]) -> str:
    """Start `wp <wp_args>` detached; return a 16-hex job id. State lands in
    runtime/wp-<instance>/.sb-jobs/job_<id>.{pid,log,status}."""
    jid = secrets.token_hex(8)
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
        subprocess.Popen(["setsid", "sh", "-c", wrapper], cwd=str(root),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    else:
        wrapper = (
            f"echo $$ > /var/www/html/.sb-jobs/job_{jid}.pid; "
            f"wp {quoted} > /var/www/html/.sb-jobs/job_{jid}.log 2>&1; "
            f"echo $? > /var/www/html/.sb-jobs/job_{jid}.status"
        )
        # wpcli is a run-style service; entrypoint is `wp`, override to sh (gotcha #6).
        compose("run", "-d", "--name", _job_name(instance, jid),
                "--entrypoint", "sh", "wpcli", "-c", wrapper, instance=instance)
    return jid


def job_status(instance: str, jid: str, offset: int = 0, limit: int = 1_048_576) -> dict:
    jd = _job_dir(instance)
    log = jd / f"job_{jid}.log"
    st = jd / f"job_{jid}.status"
    if not log.exists() and not st.exists():
        return {"job_id": jid, "status": "not_found"}
    status = "completed" if st.exists() else "running"
    out = {"job_id": jid, "status": status, "stdout": "", "bytes_read": 0, "truncated": False}
    if st.exists():
        c = st.read_text().strip()
        if c:
            out["exit_code"] = int(c)
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
    jd = _job_dir(instance)
    st = jd / f"job_{jid}.status"
    if st.exists():
        return {"job_id": jid, "status": "completed", "killed": False}
    if _is_herd_instance(instance):
        pidf = jd / f"job_{jid}.pid"
        pid = pidf.read_text().strip() if pidf.exists() else ""
        if pid:
            subprocess.run(["kill", "-TERM", f"-{pid}"], check=False,
                           capture_output=True)  # negative pid = process group
    else:
        subprocess.run(["docker", "rm", "-f", _job_name(instance, jid)],
                       check=False, capture_output=True)  # kills container + children
    st.write_text("143")
    return {"job_id": jid, "status": "completed", "exit_code": 143, "killed": True}


def prune_jobs(instance: str, max_age: int = _JOB_MAX_AGE) -> int:
    jd = wp_dir(instance) / ".sb-jobs"
    if not jd.is_dir():
        return 0
    now = time.time()
    n = 0
    for f in jd.glob("job_*"):
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
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
    from sandbox.transports.jobs import LegacyAsyncJobAdapter
    adapter = LegacyAsyncJobAdapter(valid_async_job_id, background_job_status, kill_background_job)
    jid = args.job_id
    try:
        adapter._check(jid)
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
        ok(f"pruned {n} old job artifact(s)")
        return
    jd = wp_dir(inst) / ".sb-jobs"
    ids = sorted({p.name[4:].split(".")[0] for p in jd.glob("job_*")}) if jd.is_dir() else []
    if not ids:
        info(f"no jobs for instance '{inst}'")
        return
    for jid in ids:
        s = job_status(inst, jid)
        print(f"  {jid}  {s['status']:<10}" + (f" exit={s['exit_code']}" if "exit_code" in s else ""))


register({'job': cmd_job, 'jobs': cmd_jobs, 'async-job': cmd_async_job})
