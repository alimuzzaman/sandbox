"""Runtime-neutral durable job MCP group.

The group is registered before tools are added so its dependency ownership is explicit
throughout the implementation sequence.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from dependencies import ToolDependencies
from sandbox.jobs.models import (ArtifactQuery, JobSubmission, OutputQuery, TargetRequest,
                                 normalize_output_page_bytes, normalize_output_wait_seconds)
from sandbox.application.target_service import TargetResolutionError
from sandbox.commands.jobs_runtime import _resolved_project_identity, _source_identity
from sandbox.transports.remote_jobs import RemoteJobAdmissionError


_job_service = None
_target_service = None
_workspace_service = None


def _mcp_execution_policy(target, *, execution_profile: str | None, timeout_seconds: int | None,
                          output_profile: str | None, stall_seconds: int | None = None,
                          cancel_grace_seconds: int | None = None,
                          cancel_on_stall: bool | None = None,
                          cleanup_policy: str | None = None):
    """Use the shared pure resolver without routing MCP validation through CLI exit handling."""
    from sandbox.config.runtime import normalize_runtime_policy, resolve_execution_policy

    runtime = normalize_runtime_policy(getattr(target, "runtime_policy", None))
    workspace = getattr(target, "workspace_label", runtime["workspace"])
    policy = resolve_execution_policy(
        runtime, workspace=workspace, execution_profile=execution_profile,
        timeout_seconds=timeout_seconds, stall_seconds=stall_seconds,
        cancel_grace_seconds=cancel_grace_seconds, cancel_on_stall=cancel_on_stall,
        cleanup_policy=cleanup_policy,
    )
    output_workspace = runtime["workspaces"].get(workspace, {})
    output = (output_profile if output_profile is not None else
              output_workspace.get("outputProfile") if output_workspace.get("outputProfile") is not None
              else runtime["outputProfile"])
    if output not in runtime["outputProfiles"]:
        raise ValueError("output profile is invalid")
    return policy, output


def _remote_transport():
    from sandbox.core import _remote
    from sandbox.transports.remote_jobs import RemoteJobTransport
    return RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
        ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
        remote_sb_path=_remote.remote_sb_path)


def register(server, dependencies: ToolDependencies) -> None:
    global _job_service, _target_service, _workspace_service
    _job_service = dependencies.require("job_service")
    _target_service = dependencies.require("target_service")
    _workspace_service = dependencies.require("workspace_service")
    for tool in (job_start, job_matrix, job_status, job_list, job_output, job_follow, job_metrics, job_reconcile, job_retention, job_cancel,
                 job_artifacts, job_artifact_get, job_retry, job_cleanup,
                 workspace_create, workspace_list, workspace_status, workspace_reset, workspace_destroy,
                 workspace_migration_plan, workspace_migration_apply):
        server.tool()(tool)


def _submit_explicit_job(command: list[str], project_dir: str, *, local: bool = False,
                         remote: str | None = None, workspace: str | None = None,
                         timeout_seconds: int | None = None, output_profile: str | None = None,
                         execution_profile: str | None = None, stall_seconds: int | None = None,
                         cancel_grace_seconds: int | None = None,
                         cancel_on_stall: bool | None = None,
                         cleanup_policy: str | None = None, request_id: str | None = None,
                         kind: str = "exec") -> dict:
    """Shared implementation for MCP tools that submit an explicit argv job.

    ``job_start`` is a general host-job primitive. Runtime-neutral
    ``instance_exec`` passes ``runtime-exec`` so the remote transport installs
    the co-located instance controller before executing the argv. This helper
    is intentionally not registered as an MCP tool.
    """
    if not command or any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        return {"ok": False, "code": "invalid_argv", "error": "command must be a non-empty argv list"}
    try:
        target = _target_service.resolve(TargetRequest(project_dir=project_dir, local=local, remote=remote,
            workspace=workspace, required_capability="job.exec" if not local else None))
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "invalid_target"), "error": str(exc)}
    try:
        policy, default_output = _mcp_execution_policy(
            target, execution_profile=execution_profile, timeout_seconds=timeout_seconds,
            output_profile=output_profile, stall_seconds=stall_seconds,
            cancel_grace_seconds=cancel_grace_seconds, cancel_on_stall=cancel_on_stall,
            cleanup_policy=cleanup_policy)
        resolved_output = default_output
        submission = JobSubmission(kind, target.project_root,
            _resolved_project_identity(target), target.kind, target.workspace_label,
            tuple(command), policy.deadline_seconds, _source_identity(target.project_root),
            remote_name=target.remote_name, request_id=request_id, execution_profile=policy.execution_profile,
            output_profile=resolved_output,
            output_profile_definition=(getattr(target, "runtime_policy", {}).get("outputProfiles", {})
                                       .get(resolved_output)),
            deadline_source=policy.deadline_source, deadline_reminder=policy.deadline_reminder,
            stall_seconds=policy.stall_seconds, cancel_grace_seconds=policy.cancel_grace_seconds,
            cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
            execution_policy_provenance=policy.provenance)
        if target.kind == "remote":
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            return RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path).submit(submission)
        return _job_service.submit(submission)
    except RemoteJobAdmissionError as exc:
        return exc.to_payload()
    except ValueError:
        return {"ok": False, "code": "invalid_execution_policy", "error": "execution policy is invalid"}
    except Exception:
        return {"ok": False, "code": "supervisor_launch_failed", "error": "job submission failed"}


def job_start(command: list[str], project_dir: str, *, local: bool = False,
              remote: str | None = None, workspace: str | None = None,
              timeout_seconds: int | None = None, output_profile: str | None = None,
              execution_profile: str | None = None, stall_seconds: int | None = None,
              cancel_grace_seconds: int | None = None,
              cancel_on_stall: bool | None = None, cleanup_policy: str | None = None,
              request_id: str | None = None) -> dict:
    """Durably accept a detached explicit-argv host job; output is read separately.

    The call never streams the child process's pipes over MCP.  Use
    ``instance_exec`` for an argv that must execute in a declared Compose
    service rather than the selected job host.
    """
    return _submit_explicit_job(command, project_dir, local=local, remote=remote,
                                workspace=workspace, timeout_seconds=timeout_seconds,
                                output_profile=output_profile, execution_profile=execution_profile,
                                stall_seconds=stall_seconds, cancel_grace_seconds=cancel_grace_seconds,
                                cancel_on_stall=cancel_on_stall, cleanup_policy=cleanup_policy,
                                request_id=request_id)


def job_matrix(command: list[str], workspaces: list[str], project_dir: str, *,
               local: bool = False, remote: str | None = None,
               timeout_seconds: int | None = None, output_profile: str | None = None,
               execution_profile: str | None = None, stall_seconds: int | None = None,
               cancel_grace_seconds: int | None = None,
               cancel_on_stall: bool | None = None, cleanup_policy: str | None = None) -> dict:
    """Submit one explicit command per isolated workspace under an aggregate job."""
    if not workspaces or not command:
        return {"ok": False, "code": "invalid_matrix", "error": "command and workspaces are required"}
    if len(set(workspaces)) != len(workspaces):
        return {"ok": False, "code": "invalid_matrix", "error": "workspace labels must be unique"}
    try:
        targets = [_target_service.resolve(TargetRequest(
            project_dir=project_dir, local=local, remote=remote, workspace=workspace,
            required_capability="job.exec" if remote else None)) for workspace in workspaces]
        first = targets[0]
        if any((target.kind, target.remote_name, target.project_root) !=
               (first.kind, first.remote_name, first.project_root) for target in targets):
            raise ValueError("matrix workspaces must resolve to one target")
        submissions = []
        for workspace, target in zip(workspaces, targets):
            policy, resolved_output = _mcp_execution_policy(
                target, execution_profile=execution_profile, timeout_seconds=timeout_seconds,
                output_profile=output_profile, stall_seconds=stall_seconds,
                cancel_grace_seconds=cancel_grace_seconds, cancel_on_stall=cancel_on_stall,
                cleanup_policy=cleanup_policy)
            submissions.append(JobSubmission(
                "test", target.project_root, _resolved_project_identity(target), target.kind, workspace,
                tuple(command), policy.deadline_seconds, _source_identity(target.project_root),
                remote_name=target.remote_name, workspace_mode="isolated",
                execution_profile=policy.execution_profile, output_profile=resolved_output,
                output_profile_definition=(getattr(target, "runtime_policy", {}).get("outputProfiles", {})
                                           .get(resolved_output)), deadline_source=policy.deadline_source,
                deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
                cancel_grace_seconds=policy.cancel_grace_seconds, cancel_on_stall=policy.cancel_on_stall,
                cleanup_policy=policy.cleanup_policy, execution_policy_provenance=policy.provenance))
        if first.kind == "remote":
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            return RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path).submit_many(submissions)
        return _job_service.submit_matrix(submissions)
    except RemoteJobAdmissionError as exc:
        return exc.to_payload()
    except TargetResolutionError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    except ValueError:
        return {"ok": False, "code": "invalid_execution_policy", "error": "execution policy is invalid"}
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "matrix_submission_failed"),
                "error": "job matrix submission failed"}


def job_status(job_id: str, *, remote: str | None = None) -> dict:
    """Return durable lifecycle, process, and retained-output metadata for a job."""
    try:
        result = _remote_transport().status(remote, job_id) if remote else _job_service.get(job_id)
        return result if remote and result.get("ok") is False else {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "code": "job_not_found", "error": str(exc)}


def job_list(project_dir: str | None = None, *, limit: int = 50,
             remote: str | None = None, workspace: str | None = None,
             lifecycle: str | None = None, kind: str | None = None,
             cursor: str | None = None) -> dict:
    """List a bounded, filterable durable-job page newest first."""
    try:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        project_identity = None
        if project_dir is not None:
            target = _target_service.resolve(TargetRequest(
                project_dir=project_dir, local=not bool(remote), remote=remote,
                workspace=workspace,
            ))
            project_identity = _resolved_project_identity(target)
        cursor_job_id = None
        if cursor is not None:
            try:
                padding = "=" * (-len(cursor) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(cursor + padding))
                cursor_job_id = decoded["job_id"]
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError("cursor is invalid") from exc
        query = {"limit": min(200, limit + 1)}
        if project_identity:
            query["project_identity"] = project_identity
        if workspace:
            query["workspace_label"] = workspace
        if lifecycle:
            query["lifecycle"] = lifecycle
        category = kind
        if category:
            query["kind"] = category
        if cursor_job_id:
            query["cursor_job_id"] = cursor_job_id
        result = (_remote_transport().list(
            remote, limit=min(200, limit + 1), project_identity=project_identity,
            workspace=workspace, lifecycle=lifecycle, kind=category,
            cursor_job_id=cursor_job_id,
        ) if remote else {"jobs": _job_service.list(query)})
        jobs = result.get("jobs") if isinstance(result, dict) else None
        if not isinstance(jobs, list) or any(not isinstance(item, dict) for item in jobs):
            raise ValueError("job list returned an invalid page")
        page = jobs[:limit]
        has_more = len(jobs) > limit
        next_cursor = None
        if has_more and page:
            payload = json.dumps(
                {"job_id": page[-1].get("job_id")},
                sort_keys=True, separators=(",", ":"),
            ).encode()
            next_cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        return {"ok": True, "jobs": page, "next_cursor": next_cursor,
                "has_more": has_more}
    except Exception as exc:
        return {"ok": False, "code": "invalid_query", "error": str(exc)}


def job_output(job_id: str, *, stream: str = "combined", cursor: str | None = None,
               offset: int | None = None, tail_bytes: int | None = None,
               lines: int | None = None, since: str | None = None, max_bytes: int = 65536,
               encoding: str = "utf8", profile: str = "full", wait_seconds: int = 0,
               remote: str | None = None) -> dict:
    """Read a bounded retained output page. Returned cursors are exclusive."""
    try:
        max_bytes = normalize_output_page_bytes(max_bytes)
        wait_seconds = normalize_output_wait_seconds(wait_seconds)
        if remote:
            return _remote_transport().read_output(remote, job_id, stream=stream, cursor=cursor,
                offset=offset, tail_bytes=tail_bytes, lines=lines, since=since,
                max_bytes=max_bytes, wait_seconds=wait_seconds,
                encoding=encoding, profile=profile)
        return _job_service.read_output(job_id, OutputQuery(stream=stream, cursor=cursor,
            offset=offset, tail_bytes=tail_bytes, lines=lines, since=since,
            max_bytes=max_bytes, encoding=encoding, profile=profile, wait_seconds=wait_seconds))
    except ValueError as exc:
        return {"ok": False, "code": "invalid_output_query", "error": str(exc)}
    except RuntimeError as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def job_follow(job_id: str, *, cursor: str | None = None, max_bytes: int = 65536,
               max_updates: int = 1, max_duration_seconds: int = 2,
               progress_token: str | None = None, profile: str = "smart",
               remote: str | None = None) -> dict:
    """Return a bounded set of retained-output updates for one MCP request.

    ``progress_token`` is deliberately request-scoped.  It produces compact,
    monotonic observation summaries in the response without adding a second
    durable state channel or implying anything about the child process's
    completion percentage.
    """
    if (isinstance(max_updates, bool) or not isinstance(max_updates, int)
            or not 1 <= max_updates <= 20):
        return {"ok": False, "code": "invalid_follow_query",
                "error": "max_updates must be between 1 and 20"}
    if (isinstance(max_duration_seconds, bool) or not isinstance(max_duration_seconds, int)
            or not 1 <= max_duration_seconds <= 20):
        return {"ok": False, "code": "invalid_follow_query",
                "error": "max_duration_seconds must be between 1 and 20"}
    if progress_token is not None and (not isinstance(progress_token, str) or not progress_token):
        return {"ok": False, "code": "invalid_follow_query", "error": "progress_token is invalid"}

    deadline = time.monotonic() + max_duration_seconds
    current_cursor = cursor
    updates, progress = [], []
    next_poll_at = time.monotonic()
    for index in range(max_updates):
        pause = next_poll_at - time.monotonic()
        if pause > 0:
            if time.monotonic() + pause >= deadline:
                break
            time.sleep(pause)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Each retained-output wait is bounded and notifications are no more
        # frequent than once every two seconds for the active MCP request.
        page = job_output(job_id, cursor=current_cursor, max_bytes=max_bytes,
                          profile=profile, wait_seconds=min(2, max(1, int(remaining))), remote=remote)
        if not page.get("ok", False):
            return page
        updates.append(page)
        current_cursor = page.get("cursor", current_cursor)
        next_poll_at = time.monotonic() + 2
        if progress_token is not None:
            progress.append({"token": progress_token, "current": index + 1,
                             "total": max_updates, "events_observed": page.get("events_read", 0)})
        if not page.get("has_more", False):
            break
    return {"ok": True, "job_id": job_id, "updates": updates,
            "cursor": current_cursor, "bounded": True,
            **({"progress": progress} if progress_token is not None else {})}


def job_metrics(job_id: str, *, limit: int = 500, remote: str | None = None) -> dict:
    """Read bounded persisted CPU/RSS/I/O evidence for a durable job."""
    try:
        return _remote_transport().metrics(remote, job_id, limit=limit) if remote else _job_service.read_metrics(job_id, limit=limit)
    except RuntimeError as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def job_reconcile(*, limit: int = 200, remote: str | None = None) -> dict:
    """Reconcile active jobs after a supervisor or host interruption."""
    try:
        if remote:
            return _remote_transport().control(remote, ["job-reconcile", "--limit", str(limit)])
        return _job_service.reconcile_startup(limit=limit)
    except Exception as exc:
        return {"ok": False, "code": "reconciliation_failed", "error": str(exc)}


def job_retention(*, retention_days: int = 7, limit: int = 200,
                  storage_pressure: bool = False, remote: str | None = None) -> dict:
    """Apply terminal log/metric/artifact retention to old jobs."""
    try:
        if remote:
            args = ["job-retention", "--retention-days", str(retention_days), "--limit", str(limit)]
            if storage_pressure: args.append("--storage-pressure")
            return _remote_transport().control(remote, args)
        return _job_service.retention_sweep(retention_days=retention_days, limit=limit,
                                            storage_pressure=storage_pressure)
    except Exception as exc:
        return {"ok": False, "code": "retention_failed", "error": str(exc)}


def job_cancel(job_id: str, *, force: bool = False, remote: str | None = None) -> dict:
    """Cancel only a verified owned job process group."""
    try:
        result = _remote_transport().cancel(remote, job_id, force=force) if remote else _job_service.cancel(job_id, force=force)
        return result if remote and result.get("ok") is False else {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def job_artifacts(job_id: str, *, remote: str | None = None) -> dict:
    """List metadata for retained, project-contained job artifacts."""
    try:
        if remote:
            return _remote_transport().artifacts(remote, job_id)
        return {"ok": True, "artifacts": _job_service.list_artifacts(job_id)}
    except Exception as exc:
        return {"ok": False, "code": "job_not_found", "error": str(exc)}


def job_artifact_get(job_id: str, artifact_id: str, *, offset: int = 0,
                     max_bytes: int = 1_048_576, remote: str | None = None) -> dict:
    """Return one bounded base64 artifact chunk by immutable artifact ID."""
    try:
        query = ArtifactQuery(artifact_id=artifact_id, offset=offset,
                              max_bytes=max_bytes, encoding="base64")
    except ValueError as exc:
        return {"ok": False, "code": "invalid_artifact_query", "error": str(exc)}
    try:
        if remote:
            return _remote_transport().artifact_get(remote, job_id, artifact_id,
                offset=query.offset, max_bytes=query.max_bytes)
        metadata = next((item for item in _job_service.list_artifacts(job_id)
                         if item.get("artifact_id") == artifact_id), None)
        if metadata is None:
            raise RuntimeError("artifact_not_found")
        data = _job_service.get_artifact(job_id, artifact_id,
                                         offset=query.offset, max_bytes=query.max_bytes)
        next_offset = query.offset + len(data)
        return {"ok": True, "job_id": job_id, "artifact_id": artifact_id,
                "offset": query.offset, "data": base64.b64encode(data).decode(), "bytes_read": len(data),
                "encoding": "base64", "size_bytes": metadata["size_bytes"],
                "sha256": metadata["sha256"], "status": metadata.get("status", "available"),
                "next_offset": next_offset, "has_more": next_offset < metadata["size_bytes"]}
    except Exception as exc:
        return {"ok": False, "code": "artifact_not_found", "error": str(exc)}


def job_retry(job_id: str, *, request_id: str | None = None, remote: str | None = None) -> dict:
    """Create a linked retry attempt without mutating the prior terminal job."""
    try:
        return _remote_transport().retry(remote, job_id, request_id=request_id) if remote else _job_service.retry(job_id, request_id=request_id)
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}


def _workspace_request(project_dir: str = ".", *, local: bool, remote: str | None,
                       workspace: str | None, project_identity: str | None = None,
                       workspace_id: str | None = None,
                       migration_plan_id: str | None = None,
                       confirm: bool = False,
                       expected_legacy_namespace: str | None = None,
                       inventory_digest: str | None = None,
                       index_generation: int | None = None,
                       limit: int = 50, active_only: bool = False,
                       measure_sizes: bool = False,
                       mode: str = "persistent") -> TargetRequest:
    return TargetRequest(
        project_dir=project_dir, local=local, remote=remote, workspace=workspace,
        project_identity=project_identity, workspace_id=workspace_id,
        migration_plan_id=migration_plan_id, confirm=confirm,
        expected_legacy_namespace=expected_legacy_namespace,
        inventory_digest=inventory_digest, index_generation=index_generation,
        limit=limit, active_only=active_only, measure_sizes=measure_sizes, mode=mode,
    )


def _workspace(action: str, project_dir: str = ".", *, local: bool = False,
               remote: str | None = None, workspace: str | None = None,
               project_identity: str | None = None,
               workspace_id: str | None = None,
               migration_plan_id: str | None = None,
               confirm: bool = False,
               expected_legacy_namespace: str | None = None,
               inventory_digest: str | None = None,
               index_generation: int | None = None,
               limit: int = 50, active_only: bool = False,
               measure_sizes: bool = False,
               mode: str = "persistent") -> dict:
    """Use the shared namespace-aware workspace lifecycle service."""
    try:
        return getattr(_workspace_service, action)(_workspace_request(
            project_dir, local=local, remote=remote, workspace=workspace,
            project_identity=project_identity, workspace_id=workspace_id,
            migration_plan_id=migration_plan_id, confirm=confirm,
            expected_legacy_namespace=expected_legacy_namespace,
            inventory_digest=inventory_digest, index_generation=index_generation,
            limit=limit, active_only=active_only, measure_sizes=measure_sizes,
            mode=mode))
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "workspace_operation_failed"), "error": str(exc)}


def workspace_create(project_dir: str = ".", *, local: bool = False, remote: str | None = None,
                     workspace: str | None = None,
                     project_identity: str | None = None,
                     mode: str = "persistent") -> dict:
    """Create (or retain) a named reusable local or provisioned-remote workspace."""
    return _workspace("create", project_dir, local=local, remote=remote, workspace=workspace,
                      project_identity=project_identity, mode=mode)


def workspace_list(project_dir: str = ".", *, local: bool = False, remote: str | None = None,
                   workspace: str | None = None, project_identity: str | None = None,
                   workspace_id: str | None = None, limit: int = 50,
                   active_only: bool = False, measure_sizes: bool = False) -> dict:
    """List workspaces, plus on-disk deployment storage, in one namespace.

    A degraded index reports `index.complete=false` with
    `workspace_index_incomplete` instead of failing: read-only reporting must
    never hide occupied storage. Sizes stay null unless `measure_sizes=True`.
    """
    return _workspace("list", project_dir, local=local, remote=remote, workspace=workspace,
                      project_identity=project_identity, workspace_id=workspace_id,
                      limit=limit, active_only=active_only, measure_sizes=measure_sizes)


def workspace_status(project_dir: str = ".", *, local: bool = False, remote: str | None = None,
                     workspace: str | None = None, project_identity: str | None = None,
                     workspace_id: str | None = None) -> dict:
    """Read one workspace's lifecycle metadata without touching its contents."""
    return _workspace("status", project_dir, local=local, remote=remote, workspace=workspace,
                      project_identity=project_identity, workspace_id=workspace_id)


def workspace_reset(project_dir: str = ".", *, local: bool = False, remote: str | None = None,
                    workspace: str | None = None, project_identity: str | None = None,
                    workspace_id: str | None = None, confirm: bool = False) -> dict:
    """Reset a non-busy workspace; active durable job leases are protected."""
    if confirm is not True:
        return {"ok": False, "code": "confirmation_required",
                "error": "workspace reset requires confirm=true"}
    return _workspace("reset", project_dir, local=local, remote=remote, workspace=workspace,
                      project_identity=project_identity, workspace_id=workspace_id,
                      confirm=True)


def workspace_destroy(project_dir: str = ".", *, local: bool = False, remote: str | None = None,
                      workspace: str | None = None, project_identity: str | None = None,
                      workspace_id: str | None = None, confirm: bool = False) -> dict:
    """Explicitly remove a non-busy named workspace from its selected namespace."""
    if confirm is not True:
        return {"ok": False, "code": "confirmation_required",
                "error": "workspace destroy requires confirm=true"}
    return _workspace("destroy", project_dir, local=local, remote=remote, workspace=workspace,
                      project_identity=project_identity, workspace_id=workspace_id,
                      confirm=True)


def workspace_migration_plan(project_dir: str = ".", *, local: bool = False,
                             remote: str | None = None,
                             project_identity: str | None = None,
                             expected_legacy_namespace: str | None = None,
                             inventory_digest: str | None = None,
                             index_generation: int | None = None) -> dict:
    """Plan a metadata-only adoption of legacy workspace records."""
    return _workspace("migration_plan", project_dir, local=local, remote=remote,
                      project_identity=project_identity,
                      expected_legacy_namespace=expected_legacy_namespace,
                      inventory_digest=inventory_digest,
                      index_generation=index_generation)


def workspace_migration_apply(plan_id: str, project_dir: str = ".", *,
                              local: bool = False, remote: str | None = None,
                              project_identity: str | None = None,
                              expected_legacy_namespace: str | None = None,
                              confirm: bool = False) -> dict:
    """Apply one exact unexpired metadata migration plan."""
    if confirm is not True:
        return {"ok": False, "code": "confirmation_required",
                "error": "workspace migration apply requires confirm=true"}
    return _workspace(
        "migration_apply", project_dir, local=local, remote=remote,
        project_identity=project_identity, migration_plan_id=plan_id,
        expected_legacy_namespace=expected_legacy_namespace,
        confirm=True,
    )


def job_cleanup(job_id: str, *, logs: bool = True, artifacts: bool = True,
                metrics: bool = True, remote: str | None = None, confirm: bool = False) -> dict:
    """Explicitly remove retained logs/artifacts for a terminal job only."""
    if confirm is not True:
        return {"ok": False, "code": "confirmation_required",
                "error": "job cleanup requires confirm=true"}
    try:
        return _remote_transport().cleanup(remote, job_id, logs=logs, artifacts=artifacts, metrics=metrics) \
            if remote else _job_service.cleanup(job_id, logs=logs, artifacts=artifacts, metrics=metrics)
    except Exception as exc:
        return {"ok": False, "code": str(exc), "error": str(exc)}
