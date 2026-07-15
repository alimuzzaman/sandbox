from __future__ import annotations
import subprocess

from app import SANDBOX_ROOT, _safe_json, mcp


@mcp.tool()
def async_job_status(job_id: str, offset: int = 0) -> dict:
    """Poll a background e2e/ci run started with run_e2e(async_=true) or
    ci_run(async_=true). NOT the same job type as wp_cli_job (that polls a
    single wp-cli command in one instance's container) — e2e/ci runs mint
    multiple instances themselves, so they use a separate, non-instance-
    scoped job store. See docs/ci-e2e-runner-spec.md §4.3.

    job_id: the id returned by run_e2e(async_=true) / ci_run(async_=true).
    offset: byte offset for incremental output — advance by the previous
      call's `bytes_read` to fetch only new output since last poll.

    Returns {job_id, status: running|completed|not_found, exit_code?, stdout,
    bytes_read, truncated}. When status is "completed", `stdout` (from
    offset 0) contains the full run's final JSON report as its last line —
    parse that for the same shape run_e2e/ci_run return synchronously.
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "async-job", job_id, "--offset", str(offset), "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "status": "unknown",
                "error": "async_job_status timed out after 30s"}
    lines = (res.stdout or "").strip().splitlines()
    status = _safe_json(lines[-1]) if lines else None
    if isinstance(status, dict) and "status" in status:
        return status
    return {"job_id": job_id, "status": "unknown",
            "error": (res.stderr or res.stdout or "poll failed").strip()[:1000]}


@mcp.tool()
def async_job_kill(job_id: str) -> dict:
    """Cancel a running background e2e/ci job (run_e2e/ci_run with
    async_=true). No-op if already finished — never fails.

    Returns {job_id, status, exit_code?, killed}.
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "async-job", job_id, "--kill", "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "killed": False,
                "error": "async_job_kill timed out after 30s"}
    lines = (res.stdout or "").strip().splitlines()
    result = _safe_json(lines[-1]) if lines else None
    if isinstance(result, dict) and "status" in result:
        return result
    return {"job_id": job_id, "killed": False,
            "error": (res.stderr or res.stdout or "kill failed").strip()[:1000]}
