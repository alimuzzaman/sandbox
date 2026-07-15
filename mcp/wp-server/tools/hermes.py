"""Thin MCP controls for Hermes sessions on configured Sandbox remotes."""
from __future__ import annotations

import json
import subprocess


_command_service = None


def _defer(function):
    return function


class _DeferredMCP:
    """Keep legacy decorators inert until the manifest registers this group."""

    @staticmethod
    def tool():
        return lambda function: function


mcp = _DeferredMCP()


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
    if _command_service is not None:
        return _command_service.run(args, timeout)
    import app
    SANDBOX_ROOT = app.SANDBOX_ROOT
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


@_defer
def hermes_status(remote: str) -> dict:
    """Read the non-secret Hermes status for one configured remote.

    remote: configured Sandbox remote name. This call is read-only.
    """
    return _run_sb(["hermes", "status", "--remote", remote], 30)


@_defer
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


@_defer
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


@_defer
def hermes_job_kill(remote: str, job_id: str) -> dict:
    """Cancel a detached Hermes prompt by its remote Hermes job ID."""
    return _run_sb([
        "hermes", "job", "kill", "--remote", remote, "--job-id", job_id,
    ], 30)


@_defer
def hermes_cron_list(remote: str) -> dict:
    """List non-secret Hermes cron metadata on a configured remote."""
    return _run_sb(["hermes", "cron", "list", "--remote", remote], 30)


@_defer
def hermes_cron_validate(remote: str) -> dict:
    """Audit Hermes cron model snapshots for invalid model/effort combinations."""
    return _run_sb(["hermes", "cron", "validate", "--remote", remote], 30)


@_defer
def hermes_cron_create(remote: str, schedule: str, prompt: str, name: str = "",
                       workdir: str = "", profile: str = "terra",
                       confirm: bool = False) -> dict:
    """Create a locally-delivered cron job with a validated Sandbox route.

    profile is one of luna, terra, or sol. Provider, model, and reasoning
    effort are resolved separately; callers cannot pass a free-form model.
    confirm must be true because this changes an external scheduler.
    """
    args = ["hermes", "cron", "create", "--remote", remote,
            "--schedule", schedule, "--prompt", prompt, "--profile", profile]
    if name:
        args += ["--name", name]
    if workdir:
        args += ["--workdir", workdir]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 60)


@_defer
def hermes_cron_route(remote: str, job_id: str, profile: str = "terra",
                      confirm: bool = False) -> dict:
    """Atomically repair a cron job's provider/model snapshot using a named route."""
    args = ["hermes", "cron", "route", job_id, "--remote", remote,
            "--profile", profile]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 30)


@_defer
def hermes_cron_run(remote: str, job_id: str, confirm: bool = False) -> dict:
    """Trigger a validated existing cron job on the next scheduler tick."""
    args = ["hermes", "cron", "run", job_id, "--remote", remote]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 30)


@_defer
def hermes_cron_output(remote: str, job_id: str, lines: int = 200) -> dict:
    """Read the bounded latest saved output for one validated cron job."""
    return _run_sb([
        "hermes", "cron", "output", job_id, "--remote", remote,
        "--lines", str(lines),
    ], 30)


@_defer
def hermes_authorization_sync(remote: str) -> dict:
    """Create review-only drafts from the latest non-secret REVIEW_REQUIRED cron results."""
    return _run_sb(["hermes", "authorization", "sync", "--remote", remote], 60)


@_defer
def hermes_authorization_list(remote: str) -> dict:
    """List sanitized Hermes authorization requests. This call is read-only."""
    return _run_sb(["hermes", "authorization", "list", "--remote", remote], 30)


@_defer
def hermes_authorization_show(remote: str, request_id: str) -> dict:
    """Show one sanitized Hermes authorization request and its audit history."""
    return _run_sb(["hermes", "authorization", "show", request_id, "--remote", remote], 30)


@_defer
def hermes_authorization_request(remote: str, job: str, scope: str, replay_origin: str,
                                 reason: str, expires_in_minutes: int = 1440) -> dict:
    """Create a bounded pending request for an enabled catalog-managed Hermes job."""
    return _run_sb(["hermes", "authorization", "request", "--remote", remote, "--job", job,
                    "--scope", scope, "--replay-origin", replay_origin, "--reason", reason,
                    "--expires-in-minutes", str(expires_in_minutes)], 30)


@_defer
def hermes_authorization_approve(remote: str, request_id: str, confirm: bool = False) -> dict:
    """Approve exactly one pending request and deliver its context to the matching cron job."""
    args = ["hermes", "authorization", "approve", request_id, "--remote", remote]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 60)


@_defer
def hermes_health(remote: str) -> dict:
    """Read aggregated gateway, scheduler, cron, and worktree health."""
    return _run_sb(["hermes", "health", "--remote", remote], 90)


@_defer
def hermes_worktree_list(remote: str) -> dict:
    """List bounded managed repository and worktree evidence."""
    return _run_sb(["hermes", "worktree", "list", "--remote", remote], 90)


@_defer
def hermes_worktree_inspect(remote: str, name: str) -> dict:
    """Inspect a bounded, secret-screened managed worktree diff."""
    return _run_sb(["hermes", "worktree", "inspect", "--remote", remote, "--name", name], 60)


@_defer
def hermes_worktree_preserve(remote: str, name: str, confirm: bool = False) -> dict:
    """Commit tracked reviewed changes and push the explicit managed branch."""
    args = ["hermes", "worktree", "preserve", "--remote", remote, "--name", name]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 240)


@_defer
def hermes_repo_sync(remote: str, repo: str, confirm: bool = False) -> dict:
    """Fast-forward a clean managed repo and refresh runtime for the Sandbox repo."""
    args = ["hermes", "repo", "sync", "--remote", remote, "--repo", repo]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 240)


@_defer
def hermes_gateway_converge(remote: str, confirm: bool = False) -> dict:
    """Preview or establish the Sandbox gateway as the sole owner."""
    args = ["hermes", "gateway", "converge", "--remote", remote]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 90)


@_defer
def hermes_cron_catalog(remote: str) -> dict:
    """Validate and render the committed non-secret cron catalog."""
    return _run_sb(["hermes", "cron", "catalog", "--remote", remote], 30)


@_defer
def hermes_cron_reconcile(remote: str, confirm: bool = False,
                          force_replace: bool = False) -> dict:
    """Preview or apply exact desired cron state from the committed catalog."""
    args = ["hermes", "cron", "reconcile", "--remote", remote]
    if force_replace:
        args.append("--force-replace")
    if confirm:
        args.append("--confirm")
    return _run_sb(args, 300)


@_defer
def hermes_cron_verify(remote: str, job_id: str, timeout: int = 600,
                       confirm: bool = False) -> dict:
    """Run one cron and wait for evidence-backed terminal status."""
    args = ["hermes", "cron", "verify", job_id, "--remote", remote,
            "--timeout", str(timeout)]
    if confirm:
        args.append("--confirm")
    return _run_sb(args, min(timeout + 60, 7260))


def register(server, dependencies) -> None:
    """Register Hermes tools against explicitly supplied command transport."""
    global _command_service
    _command_service = dependencies.require("hermes_service")
    for function in (
        hermes_status, hermes_run, hermes_job_status, hermes_job_kill,
        hermes_cron_list, hermes_cron_validate, hermes_cron_create,
        hermes_cron_route, hermes_cron_run, hermes_cron_output,
        hermes_authorization_sync, hermes_authorization_list,
        hermes_authorization_show, hermes_authorization_request,
        hermes_authorization_approve, hermes_health, hermes_worktree_list,
        hermes_worktree_inspect, hermes_worktree_preserve, hermes_repo_sync,
        hermes_gateway_converge, hermes_cron_catalog, hermes_cron_reconcile,
        hermes_cron_verify,
    ):
        server.tool()(function)
