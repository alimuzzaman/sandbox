"""Runtime-neutral durable job MCP group.

The group is registered before tools are added so its dependency ownership is explicit
throughout the implementation sequence.
"""

from __future__ import annotations

import hashlib
import base64
from pathlib import Path

from dependencies import ToolDependencies
from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity, TargetRequest


_job_service = None
_target_service = None


def register(server, dependencies: ToolDependencies) -> None:
    global _job_service, _target_service
    _job_service = dependencies.require("job_service")
    _target_service = dependencies.require("target_service")
    dependencies.require("workspace_service")
    for tool in (job_start, job_status, job_list, job_output, job_follow, job_metrics, job_cancel,
                 job_artifacts, job_artifact_get, job_retry, job_cleanup):
        server.tool()(tool)


def job_start(command: list[str], project_dir: str, *, local: bool = False,
              remote: str | None = None, workspace: str | None = None,
              timeout_seconds: int = 900, output_profile: str = "smart",
              request_id: str | None = None) -> dict:
    """Durably accept a detached explicit-argv job; output is read separately.

    The call never streams the child process's pipes over MCP.  A remote target
    must have been deployed and expose the remote job capability.
    """
    if not command or any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        return {"ok": False, "code": "invalid_argv", "error": "command must be a non-empty argv list"}
    try:
        target = _target_service.resolve(TargetRequest(project_dir=project_dir, local=local, remote=remote,
            workspace=workspace, required_capability="compose.remote-deploy" if remote else None))
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "invalid_target"), "error": str(exc)}
    try:
        submission = JobSubmission("exec", target.project_root,
            hashlib.sha256(target.project_root.encode()).hexdigest(), target.kind, target.workspace_label,
            tuple(command), timeout_seconds, SourceIdentity("sha256:" + hashlib.sha256(target.project_root.encode()).hexdigest()),
            remote_name=target.remote_name, request_id=request_id, output_profile=output_profile)
        if target.kind == "remote":
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            return RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote).submit(submission)
        return _job_service.submit(submission)
    except Exception as exc:
        return {"ok": False, "code": "supervisor_launch_failed", "error": str(exc)}


def job_status(job_id: str) -> dict:
    """Return durable lifecycle, process, and retained-output metadata for a job."""
    try:
        return {"ok": True, **_job_service.get(job_id)}
    except Exception as exc:
        return {"ok": False, "code": "job_not_found", "error": str(exc)}


def job_list(limit: int = 50) -> dict:
    """List durable jobs newest first with a bounded result page."""
    try:
        return {"ok": True, "jobs": _job_service.list({"limit": limit})}
    except Exception as exc:
        return {"ok": False, "code": "invalid_query", "error": str(exc)}


def job_output(job_id: str, *, stream: str = "combined", cursor: str | None = None,
               tail_bytes: int | None = None, max_bytes: int = 65536,
               encoding: str = "utf8") -> dict:
    """Read a bounded retained output page. Returned cursors are exclusive."""
    try:
        return _job_service.read_output(job_id, OutputQuery(stream=stream, cursor=cursor,
            tail_bytes=tail_bytes, max_bytes=max_bytes, encoding=encoding))
    except Exception as exc:
        return {"ok": False, "code": "invalid_output_query", "error": str(exc)}


def job_follow(job_id: str, *, cursor: str | None = None, max_bytes: int = 65536) -> dict:
    """Return one bounded long-poll output page; callers repeat with its cursor."""
    return job_output(job_id, cursor=cursor, max_bytes=max_bytes)


def job_metrics(job_id: str, *, limit: int = 500) -> dict:
    """Read bounded persisted CPU/RSS/I/O evidence for a durable job."""
    try:
        return _job_service.read_metrics(job_id, limit=limit)
    except Exception as exc:
        return {"ok": False, "code": "job_not_found", "error": str(exc)}


def job_cancel(job_id: str, *, force: bool = False) -> dict:
    """Cancel only a verified owned job process group."""
    try:
        return {"ok": True, **_job_service.cancel(job_id, force=force)}
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def job_artifacts(job_id: str) -> dict:
    """List metadata for retained, project-contained job artifacts."""
    try:
        return {"ok": True, "artifacts": _job_service.list_artifacts(job_id)}
    except Exception as exc:
        return {"ok": False, "code": "job_not_found", "error": str(exc)}


def job_artifact_get(job_id: str, artifact_id: str, *, offset: int = 0,
                     max_bytes: int = 1_048_576) -> dict:
    """Return one bounded base64 artifact chunk by immutable artifact ID."""
    try:
        data = _job_service.get_artifact(job_id, artifact_id, offset=offset, max_bytes=max_bytes)
        return {"ok": True, "job_id": job_id, "artifact_id": artifact_id,
                "offset": offset, "data": base64.b64encode(data).decode(), "bytes_read": len(data),
                "encoding": "base64"}
    except Exception as exc:
        return {"ok": False, "code": "artifact_not_found", "error": str(exc)}


def job_retry(job_id: str, *, request_id: str | None = None) -> dict:
    """Create a linked retry attempt without mutating the prior terminal job."""
    try:
        return _job_service.retry(job_id, request_id=request_id)
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def job_cleanup(job_id: str, *, logs: bool = True, artifacts: bool = True) -> dict:
    """Explicitly remove retained logs/artifacts for a terminal job only."""
    try:
        return _job_service.cleanup(job_id, logs=logs, artifacts=artifacts)
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}
