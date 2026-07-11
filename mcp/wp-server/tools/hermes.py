"""Thin MCP controls for Hermes sessions on configured Sandbox remotes."""
from __future__ import annotations

import json
import subprocess

from app import SANDBOX_ROOT, mcp


def _last_json(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _run_sb(args: list[str], timeout: int) -> dict:
    try:
        res = subprocess.run([str(SANDBOX_ROOT / "sb"), *args, "--json"],
                             cwd=str(SANDBOX_ROOT), capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Hermes control command timed out"}
    payload = _last_json(res.stdout)
    if payload is not None:
        return payload
    return {"ok": False, "error": (res.stderr or res.stdout or "Hermes control command failed").strip()[:1000]}


@mcp.tool()
def hermes_status(remote: str) -> dict:
    """Read the non-secret Hermes status for one configured remote.

    remote: configured Sandbox remote name. This call is read-only.
    """
    return _run_sb(["hermes", "status", "--remote", remote], 30)


@mcp.tool()
def hermes_run(remote: str, repo: str, prompt: str, worktree: bool = True,
               async_: bool = True, timeout: int = 1200) -> dict:
    """Start Hermes against a managed repository on a configured remote.

    remote: configured Sandbox remote name.
    repo: registered managed repository name, never a filesystem path.
    prompt: one-shot task; it is not persisted by this wrapper.
    worktree: create an isolated worktree (default true).
    async_: return a remote Hermes job id immediately (default true).
    timeout: synchronous execution limit in seconds.
    """
    args = ["hermes", "run", "--remote", remote, "--repo", repo,
            "--prompt", prompt, "--timeout", str(timeout)]
    if not worktree:
        args.append("--no-worktree")
    if async_:
        args.append("--async")
    return _run_sb(args, min(timeout + 30, 1230) if not async_ else 30)


@mcp.tool()
def hermes_job_status(remote: str, job_id: str, offset: int = 0) -> dict:
    """Read bounded, incremental output for a detached Hermes prompt.

    remote: configured Sandbox remote name.
    job_id: job returned by ``hermes_run(..., async_=true)``.
    offset: byte offset from a previous response; defaults to zero.
    """
    return _run_sb([
        "hermes", "job", "status", "--remote", remote,
        "--job-id", job_id, "--offset", str(offset),
    ], 30)


@mcp.tool()
def hermes_job_kill(remote: str, job_id: str) -> dict:
    """Cancel a detached Hermes prompt by its remote Hermes job ID."""
    return _run_sb([
        "hermes", "job", "kill", "--remote", remote, "--job-id", job_id,
    ], 30)
