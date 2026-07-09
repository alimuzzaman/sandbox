from __future__ import annotations
import json
import subprocess

from app import *  # noqa: F401,F403


@mcp.tool()
def run_e2e(project_dir: str, workers: int = 2, concurrency: int | None = None,
           grep: str | None = None, keep_on_fail: bool = False,
           strict_provision: bool = False, timeout: int = 900,
           async_: bool = False) -> dict:
    """Run Playwright e2e tests with N workers, EACH AGAINST ITS OWN FRESH
    WordPress instance (multi-instance-per-root) — instead of N workers
    sharing one site's state. See docs/ci-e2e-runner-spec.md §2.

    Requires the project to have an existing playwright config (searched at
    the project root and common subdirs: tests/, test/, e2e/, tests/e2e/)
    whose `use.baseURL` reads one of the env vars this sets per worker:
    SANDBOX_E2E_BASE_URL / WP_BASE_URL / BASE_URL / PLAYWRIGHT_TEST_BASE_URL.
    If that config is instead detected reading a shared `.wp-env-port`
    descriptor (an observed convention), that file is written too, and
    workers>1 is automatically clamped to 1 (that convention isn't
    worker-aware — see docs/ci-e2e-runner-spec.md §2.3).

    project_dir: the plugin project to test (its e2e setup must already exist).
    workers: number of parallel Playwright shards / fresh instances (default 2).
    concurrency: cap on simultaneously-booting instances (default: auto).
    grep: passed to `playwright test --grep`.
    keep_on_fail: preserve a failed worker's instance for inspection instead
      of tearing it down.
    strict_provision: abort the whole run if any worker's instance fails to boot.
    async_: run detached and return {ok, job_id} immediately instead of
      blocking — poll with async_job_status(job_id), cancel with
      async_job_kill(job_id).

    Returns {ok, workers, concurrency, passed, failed, by_worker:[{label, url,
    status, error?, exit_code?}]} — or {ok, job_id} when async_=true.
    """
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "e2e", "--project-dir", project_dir,
           "--workers", str(workers), "--json"]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]
    if grep:
        cmd += ["--grep", grep]
    if keep_on_fail:
        cmd.append("--keep-on-fail")
    if strict_provision:
        cmd.append("--strict-provision")
    if timeout:
        cmd += ["--timeout", str(timeout)]
    if async_:
        cmd.append("--async")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=60, cwd=str(SANDBOX_ROOT))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "run_e2e --async launch timed out after 60s"}
        lines = (res.stdout or "").strip().splitlines()
        launched = _safe_json(lines[-1]) if lines else None
        if isinstance(launched, dict) and "job_id" in launched:
            return launched
        return {"ok": False, "code": res.returncode,
                "error": (res.stderr or res.stdout or "async launch failed").strip()[:2000]}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 120, cwd=str(SANDBOX_ROOT))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"run_e2e timed out after {timeout + 120}s"}
    lines = (res.stdout or "").strip().splitlines()
    report = _safe_json(lines[-1]) if lines else None
    if isinstance(report, dict) and "workers" in report:
        return report
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "e2e failed").strip()[:2000]}
