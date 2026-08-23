"""Durable host-level resource scans.

Resource probes are host observations, not Compose commands.  This module
therefore uses the existing durable-job control plane only as a supervisor and
executes the worker through the selected host's local resource adapter.  A
remote worker deliberately omits ``--remote``: once the job is on the VPS,
the VPS itself is the observation target.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandbox.core._paths import ROOT
from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
from sandbox.resources.service import ResourceError, result


RESOURCE_SCAN_WORKSPACE = "resource-scan"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_PROBE_BUDGET = 3600
_JOB_GRACE_SECONDS = 120


def _budget(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
        or float(value) > _MAX_PROBE_BUDGET
    ):
        raise ResourceError(
            f"budget must be between 0 and {_MAX_PROBE_BUDGET} seconds",
            "invalid_budget",
        )
    return float(value)


def _request_id(value: object) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ResourceError(
            "--request-id is required for detached resource scans and must be a safe name",
            "invalid_request_id",
        )
    return value


def _source_identity(project_root: str) -> SourceIdentity:
    """Stable source identity for local durable-job metadata."""
    import hashlib

    digest = hashlib.sha256(str(Path(project_root).expanduser().resolve()).encode()).hexdigest()
    return SourceIdentity(f"sha256:{digest}")


def _worker_flags(args, probe_budget: float) -> tuple[str, ...]:
    flags = ["--worker", "--budget", str(float(probe_budget))]
    for name in ("thorough", "deep", "fast", "refresh"):
        if bool(getattr(args, name, False)):
            flags.append(f"--{name}")
    return tuple(flags)


def _submission(*, args, target, worker_argv: tuple[str, ...], probe_budget: float) -> JobSubmission:
    """Build one replay-safe submission from the resolver-owned target identity."""
    deadline = min(int(math.ceil(probe_budget)) + _JOB_GRACE_SECONDS, 604800)
    return JobSubmission(
        kind="resource-scan",
        project_root=target.project_root,
        project_identity=target.project_identity,
        target_kind=target.kind,
        workspace_label=RESOURCE_SCAN_WORKSPACE,
        argv=worker_argv,
        deadline_seconds=deadline,
        source=_source_identity(target.project_root),
        remote_name=target.remote_name,
        request_id=_request_id(args.request_id),
        workspace_mode="persistent",
        cwd_relative=".",
        execution_profile="exec",
        output_profile="full",
        output_profile_definition={"mode": "full"},
        deadline_source="resource_probe_budget",
        deadline_reminder=(
            f"resource probe budget is {int(probe_budget)}s; "
            f"the durable supervisor allows {deadline}s"
        ),
        stall_seconds=max(300, min(deadline, 3600)),
        cancel_grace_seconds=20,
        cancel_on_stall=False,
        cleanup_policy="retain",
        execution_policy_provenance={
            # The remote job wire contract requires the canonical six policy
            # provenance fields. Keep scan-specific context in the reminder
            # and the public probe receipt rather than adding wire keys.
            "execution_profile": "operation",
            "deadline": "resource_probe_budget",
            "stall": "resource_probe_budget",
            "cancel_grace": "profile:exec",
            "cancel_on_stall": "profile:exec",
            "cleanup": "profile:exec",
        },
    )


def _poll_commands(job_id: str, remote: str | None) -> dict[str, str]:
    suffix = f" --remote {remote}" if remote else ""
    return {
        "status": f"./sb job-status {job_id}{suffix} --json",
        "output": (
            f"./sb job-output {job_id}{suffix} --stream combined "
            "--wait-seconds 20 --json"
        ),
    }


def start(args) -> dict:
    """Accept one idempotent detached host scan through the durable job API."""
    try:
        requested_budget = args.budget
        if requested_budget is None:
            requested_budget = (
                10 if bool(getattr(args, "fast", False)) else
                900 if bool(getattr(args, "refresh", False)) else 15
            )
        probe_budget = _budget(requested_budget)
        request_id = _request_id(args.request_id)
        if bool(getattr(args, "cancelled", False)):
            raise ResourceError(
                "--cancelled cannot be combined with --detach",
                "invalid_mode",
            )

        from sandbox.application.context import durable_job_dependencies
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport

        remote_name = getattr(args, "remote", None)
        dependencies = durable_job_dependencies()
        target = dependencies["target_service"].resolve(TargetRequest(
            project_dir=str(ROOT),
            local=not bool(remote_name),
            remote=remote_name,
            workspace=RESOURCE_SCAN_WORKSPACE,
            required_capability="job.exec" if remote_name else None,
            allow_inferred_remote=False,
        ))

        # ``./sb`` is relative to the staged checkout prepared by the remote
        # job transport.  It therefore runs the submitted source revision,
        # rather than the independently installed controller revision.
        if target.kind == "remote":
            worker_argv = (
                "./sb", "resources", "status",
                *_worker_flags(args, probe_budget),
            )
        else:
            worker_argv = (
                sys.executable,
                "-m",
                "sandbox.cli",
                "resources",
                "status",
                *_worker_flags(args, probe_budget),
            )
        submission = _submission(
            args=args, target=target, worker_argv=worker_argv,
            probe_budget=probe_budget,
        )
        if target.kind == "remote":
            accepted = RemoteJobTransport(
                deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run,
                remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path,
            ).submit(submission)
        else:
            accepted = dependencies["job_service"].submit(submission)
        accepted = dict(accepted)
        # The generic remote transport includes an internal staged path for
        # diagnostics.  Resource scans expose only the durable identity and
        # target; filesystem paths are not part of this public helper.
        accepted.pop("workspace_path", None)
        job_id = accepted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise ResourceError(
                "detached resource scan was accepted without a job id",
                "acceptance_unknown",
                retryable=True,
            )
        accepted.update({
            "action": "status",
            "kind": "resource-scan",
            "target": {
                "kind": "remote" if remote_name else "local",
                "name": remote_name or "local",
            },
            "probe": {
                "target": {"kind": "remote" if remote_name else "local",
                            "name": remote_name or "local"},
                "budget_seconds": probe_budget,
                "worker_mode": "host-local",
            },
            "poll": _poll_commands(job_id, remote_name),
        })
        return accepted
    except ResourceError as exc:
        return result(False, "status", status="failed", error=exc)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code):
            code = "detached_scan_failed"
        return result(
            False,
            "status",
            status="failed",
            error=ResourceError(
                str(exc)[:240] or "detached resource scan could not be accepted",
                code,
                retryable=True,
            ),
        )


def _event(kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "event": kind,
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **payload,
    }


def run_worker(args) -> int:
    """Run inside the selected host and emit interval-friendly JSONL output."""
    try:
        if getattr(args, "remote", None):
            raise ResourceError(
                "detached resource workers must use the host-local adapter",
                "invalid_worker_target",
            )
        probe_budget = _budget(args.budget)
        from sandbox.resources.context import resource_service

        def progress(category: str) -> None:
            print(json.dumps(_event("progress", category=str(category)), sort_keys=True), flush=True)

        service = resource_service(None)
        payload = service.status(
            thorough=bool(args.thorough or args.deep),
            budget_seconds=probe_budget,
            progress=progress,
            deep=bool(args.deep or args.fast or args.refresh),
            directory_cache=(
                "cache_only" if args.fast else
                "refresh" if args.refresh else None
            ),
        )
    except ResourceError as exc:
        payload = result(False, "status", status="failed", error=exc)
    except Exception as exc:
        payload = result(
            False,
            "status",
            status="failed",
            error=ResourceError(
                str(exc)[:240] or "resource worker failed",
                "measurement_unavailable",
                retryable=True,
            ),
        )
    print(json.dumps(_event("result", payload=payload), sort_keys=True), flush=True)
    return 0 if payload.get("ok") else 1
