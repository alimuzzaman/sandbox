"""Feature-owned CLI adapters for durable job execution and observation."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import tempfile
import time
from pathlib import Path

from sandbox.application.context import durable_job_dependencies
from sandbox.application.target_service import TargetResolutionError
from sandbox.jobs.models import ArtifactQuery, JobSubmission, OutputQuery, SourceIdentity, TargetRequest
from sandbox.registry import CommandSpec, register_specs


def _die(message: str) -> None:
    from sandbox.core import die
    die(message)


def _source_identity(root: str) -> SourceIdentity:
    # Local execution still has an identity. Remote submission replaces this with
    # the deploy identity returned by the deployment transport before acceptance.
    return SourceIdentity("sha256:" + hashlib.sha256(str(Path(root)).encode()).hexdigest())


def _download_artifact_file(destination: str | Path, metadata: dict, fetch) -> dict:
    """Download every bounded page, then atomically publish verified bytes."""
    if metadata.get("status", "available") != "available":
        raise RuntimeError("artifact metadata is unavailable")
    expected_size = metadata.get("size_bytes")
    expected_sha = metadata.get("sha256")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise RuntimeError("artifact size metadata is invalid")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise RuntimeError("artifact sha256 metadata is invalid")
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    digest = hashlib.sha256()
    offset = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while offset < expected_size:
                page = fetch(offset)
                if not isinstance(page, dict) or not page.get("ok", False):
                    raise RuntimeError("artifact chunk retrieval failed")
                if page.get("offset") != offset or page.get("encoding", "base64") != "base64":
                    raise RuntimeError("artifact chunk offset or encoding mismatch")
                try:
                    chunk = base64.b64decode(page.get("data", ""), validate=True)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("artifact chunk base64 is invalid") from exc
                if page.get("bytes_read") != len(chunk):
                    raise RuntimeError("artifact chunk size mismatch")
                if not chunk:
                    raise RuntimeError("artifact download ended before declared size")
                if offset + len(chunk) > expected_size:
                    raise RuntimeError("artifact download exceeds declared size")
                handle.write(chunk)
                digest.update(chunk)
                offset += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if offset != expected_size:
            raise RuntimeError("artifact size validation failed")
        actual_sha = digest.hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError("artifact sha256 validation failed")
        os.replace(temporary, target)
        return {"ok": True, "artifact_id": metadata.get("artifact_id"),
                "output_file": str(target), "size_bytes": offset, "sha256": actual_sha}
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _profile_timeout(target, name: str, explicit: int | None) -> tuple[int, str]:
    from sandbox.config.runtime import BUILTIN_EXECUTION_PROFILES
    chosen = name or "exec"
    profile = BUILTIN_EXECUTION_PROFILES.get(chosen)
    if profile is None:
        _die(f"unknown execution profile {chosen!r}")
    return (explicit or profile["timeoutSeconds"], chosen)


def configure_start_parser(parser) -> None:
    parser.description = "Start a detached durable job from explicit argv after --."
    parser.add_argument("--project-dir", default=".")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true")
    target.add_argument("--remote")
    parser.add_argument("--workspace")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--stall-seconds", type=int, default=300)
    parser.add_argument("--cancel-on-stall", action="store_true")
    parser.add_argument("--profile", default="exec")
    parser.add_argument("--output-profile", default="smart")
    parser.add_argument("--request-id")
    parser.add_argument("--source-identity")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="...", help="argv after --")


def configure_status_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_output_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--stream", choices=("combined", "stdout", "stderr"), default="combined")
    position = parser.add_mutually_exclusive_group()
    position.add_argument("--cursor")
    position.add_argument("--offset", type=int)
    position.add_argument("--tail-bytes", type=int)
    position.add_argument("--lines", type=int)
    position.add_argument("--since", help="RFC 3339 timestamp or Unix seconds")
    parser.add_argument("--max-bytes", type=int, default=65536)
    parser.add_argument("--encoding", choices=("utf8", "base64"), default="utf8")
    parser.add_argument("--profile", default="full", help="declarative retained-output presentation profile")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=0,
                        help="bounded retained-log long poll (0 disables waiting)")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_list_parser(parser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--project-dir")
    parser.add_argument("--workspace")
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_cancel_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_retry_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--request-id")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_cleanup_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--artifacts", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--confirm", "--yes", dest="confirm", action="store_true",
                        help="required before retained job data is removed")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_metrics_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_artifacts_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_artifact_get_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("artifact_id")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-bytes", type=int, default=1_048_576)
    parser.add_argument("--output-file")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_reconcile_parser(parser) -> None:
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--remote",
                        help="reconcile durable jobs on a provisioned remote controller")
    parser.add_argument("--json", action="store_true")


def configure_retention_parser(parser) -> None:
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--storage-pressure", action="store_true",
                        help="reclaim oldest terminal data only when below the disk reserve")
    parser.add_argument("--json", action="store_true")


def configure_matrix_parser(parser) -> None:
    parser.description = "Fan out one explicit argv into isolated durable workspace jobs."
    parser.add_argument("--project-dir", default=".")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true")
    target.add_argument("--remote")
    parser.add_argument("--workspace", action="append",
                        help="isolated workspace label; repeat for each matrix cell")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output-profile", default="smart")
    parser.add_argument("--spec-json", help="internal encoded matrix child submission plan")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="...")


def cmd_job_start(_cfg, args) -> None:
    command = list(args.command or ())
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        _die("usage: ./sb job-start [target options] -- <argv...>")
    dependencies = durable_job_dependencies()
    try:
        target = dependencies["target_service"].resolve(TargetRequest(
            project_dir=args.project_dir, local=args.local, remote=args.remote,
            workspace=args.workspace, required_capability="job.exec" if args.remote else None,
        ))
    except TargetResolutionError as exc:
        _die(f"{exc.code}: {exc}")
    timeout, profile = _profile_timeout(target, args.profile, args.timeout)
    source = SourceIdentity(args.source_identity) if args.source_identity else _source_identity(target.project_root)
    submission = JobSubmission(
        kind="exec", project_root=target.project_root, project_identity=hashlib.sha256(target.project_root.encode()).hexdigest(),
        target_kind=target.kind, remote_name=target.remote_name, workspace_label=target.workspace_label,
        argv=tuple(command), deadline_seconds=timeout, source=source,
        request_id=args.request_id, execution_profile=profile, output_profile=args.output_profile,
        output_profile_definition=(getattr(target, "runtime_policy", {}).get("outputProfiles", {})
                                   .get(args.output_profile)),
        deadline_source="explicit" if args.timeout else f"profile:{profile}",
        stall_seconds=args.stall_seconds, cancel_on_stall=args.cancel_on_stall,
    )
    if target.kind == "remote":
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
            ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
            remote_sb_path=_remote.remote_sb_path).submit(submission)
    else:
        accepted = dependencies["job_service"].submit(submission)
    if args.wait and target.kind != "remote":
        while True:
            state = dependencies["job_service"].get(accepted["job_id"])
            if state["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                accepted["result"] = state
                break
            time.sleep(.2)
    if args.json:
        print(json.dumps(accepted, sort_keys=True))
    else:
        target_info = accepted.get("target", {})
        target_name = target_info.get("remote") or target_info.get("kind", target.kind)
        deadline = accepted.get("deadline", {})
        print(f"{accepted['job_id']} target={target_name} workspace={accepted.get('workspace', target.workspace_label)} "
              f"deadline={deadline.get('seconds', timeout)}s source={deadline.get('source', submission.deadline_source)}")


def cmd_job_status(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).status(args.remote, args.job_id)
    else:
        result = durable_job_dependencies()["job_service"].get(args.job_id)
    if args.json:
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return
    target_info = result.get("target") or {"kind": result.get("target_kind"), "remote": result.get("remote_name")}
    target_name = target_info.get("remote") or target_info.get("kind", "unknown")
    workspace = result.get("workspace") or result.get("workspace_label", "unknown")
    deadline = result.get("deadline") or {"seconds": result.get("deadline_seconds"),
                                            "source": result.get("deadline_source")}
    print(f"{result['job_id']} {result['lifecycle']} ({result['health']}) target={target_name} "
          f"workspace={workspace} deadline={deadline.get('seconds')}s source={deadline.get('source')}")


def cmd_job_output(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path)
        cursor = args.cursor
        while True:
            result = transport.read_output(args.remote, args.job_id, stream=args.stream, cursor=cursor,
                offset=getattr(args, "offset", None), tail_bytes=args.tail_bytes,
                lines=getattr(args, "lines", None), since=getattr(args, "since", None),
                max_bytes=args.max_bytes,
                wait_seconds=max(args.wait_seconds, 1) if args.follow else args.wait_seconds,
                encoding=args.encoding, profile=getattr(args, "profile", "full"))
            if args.json: print(json.dumps(result, sort_keys=True))
            elif result.get("data"): print(result["data"], end="")
            if not args.follow: return
            state = transport.status(args.remote, args.job_id)
            if state.get("lifecycle") in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}: return
            cursor = result.get("cursor"); time.sleep(.2)
    service = durable_job_dependencies()["job_service"]
    cursor = args.cursor
    while True:
        try:
            result = service.read_output(args.job_id, OutputQuery(stream=args.stream, cursor=cursor,
                offset=getattr(args, "offset", None), tail_bytes=args.tail_bytes,
                lines=getattr(args, "lines", None), since=getattr(args, "since", None),
                max_bytes=args.max_bytes,
                wait_seconds=max(args.wait_seconds, 1) if args.follow else args.wait_seconds,
                encoding=args.encoding, profile=getattr(args, "profile", "full")))
        except RuntimeError as exc:
            if args.json:
                print(json.dumps({"ok": False, "code": str(exc), "error": str(exc)}, sort_keys=True))
                return
            _die(str(exc))
        if args.json:
            print(json.dumps(result, sort_keys=True))
        elif result["data"]:
            print(result["data"], end="")
        if not args.follow or service.get(args.job_id)["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
            return
        cursor = result["cursor"]
        time.sleep(.2)


def cmd_job_list(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).list(args.remote, limit=args.limit,
                project_dir=args.project_dir, workspace=args.workspace, active_only=args.active_only)
        result = result.get("jobs", result)
    else:
        from pathlib import Path
        query = {"limit": args.limit}
        if args.project_dir:
            query["project_identity"] = hashlib.sha256(
                str(Path(args.project_dir).expanduser().resolve()).encode()).hexdigest()
        if args.workspace:
            query["workspace_label"] = args.workspace
        result = durable_job_dependencies()["job_service"].list(query)
    if args.active_only:
        result = [item for item in result if item.get("lifecycle") in {
            "accepted", "queued", "running", "cancelling"}]
    if args.json:
        print(json.dumps({"ok": True, "jobs": result}, sort_keys=True))
    else:
        for item in result:
            print(f"{item['job_id']} {item['lifecycle']} {item['workspace_label']}")


def cmd_job_cancel(_cfg, args) -> None:
    try:
        if args.remote:
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
                remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).cancel(args.remote, args.job_id, force=args.force)
        else:
            result = durable_job_dependencies()["job_service"].cancel(args.job_id, force=args.force)
    except RuntimeError as exc:
        _die(str(exc))
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} cancelling")


def cmd_job_retry(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).retry(args.remote, args.job_id, request_id=args.request_id)
    else:
        result = durable_job_dependencies()["job_service"].retry(args.job_id, request_id=args.request_id)
    print(json.dumps(result, sort_keys=True) if args.json else result["job_id"])


def cmd_job_cleanup(_cfg, args) -> None:
    if not args.confirm:
        _die("job cleanup requires --yes")
    selected = args.logs or args.artifacts or args.metrics
    options = {"logs": args.logs or not selected, "artifacts": args.artifacts or not selected,
               "metrics": args.metrics or not selected}
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).cleanup(args.remote, args.job_id, **options)
    else:
        result = durable_job_dependencies()["job_service"].cleanup(args.job_id, **options)
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} cleanup={result['cleanup_state']}")


def cmd_job_metrics(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).metrics(args.remote, args.job_id, limit=args.limit)
    else:
        result = durable_job_dependencies()["job_service"].read_metrics(args.job_id, limit=args.limit)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for item in result["samples"]:
            print(json.dumps(item, sort_keys=True))


def cmd_job_artifacts(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).artifacts(args.remote, args.job_id)
    else:
        result = {"ok": True, "artifacts": durable_job_dependencies()["job_service"].list_artifacts(args.job_id)}
    print(json.dumps(result, sort_keys=True) if args.json else "\n".join(
        f"{item['artifact_id']} {item['display_name']} {item['size_bytes']} bytes"
        for item in result.get("artifacts", ())))


def cmd_job_artifact_get(_cfg, args) -> None:
    query = ArtifactQuery(artifact_id=args.artifact_id, offset=args.offset,
                          max_bytes=args.max_bytes, encoding="base64")
    transport = None
    service = None
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path)
        artifacts = transport.artifacts(args.remote, args.job_id).get("artifacts", ())
    else:
        service = durable_job_dependencies()["job_service"]
        artifacts = service.list_artifacts(args.job_id)
    metadata = next((item for item in artifacts if item.get("artifact_id") == args.artifact_id), None)
    if metadata is None:
        raise RuntimeError("artifact_not_found")

    def fetch(offset: int) -> dict:
        if transport is not None:
            return transport.artifact_get(args.remote, args.job_id, args.artifact_id,
                                          offset=offset, max_bytes=args.max_bytes)
        data = service.get_artifact(args.job_id, args.artifact_id,
                                    offset=offset, max_bytes=args.max_bytes)
        return {"ok": True, "job_id": args.job_id, "artifact_id": args.artifact_id,
                "offset": offset, "data": base64.b64encode(data).decode(),
                "bytes_read": len(data), "encoding": "base64",
                "size_bytes": metadata["size_bytes"], "sha256": metadata["sha256"],
                "next_offset": offset + len(data),
                "has_more": offset + len(data) < metadata["size_bytes"]}

    if args.output_file:
        if args.offset:
            raise RuntimeError("--offset cannot be combined with --output-file")
        _download_artifact_file(args.output_file, metadata, fetch)
        return
    result = fetch(args.offset)
    result.setdefault("size_bytes", metadata["size_bytes"])
    result.setdefault("sha256", metadata["sha256"])
    result.setdefault("status", metadata.get("status", "available"))
    result.setdefault("next_offset", args.offset + result.get("bytes_read", 0))
    result.setdefault("has_more", result["next_offset"] < metadata["size_bytes"])
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['artifact_id']} {result['bytes_read']} bytes")


def cmd_job_reconcile(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(
            deploy=_remote.deploy_exact_working_tree,
            ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote,
            remote_sb_path=_remote.remote_sb_path,
        ).control(args.remote, ["job-reconcile", "--limit", str(args.limit)])
    else:
        result = durable_job_dependencies()["job_service"].reconcile_startup(limit=args.limit)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"interrupted={len(result['interrupted'])} released={len(result['released_leases'])}")


def cmd_declared_test_plan(_cfg, args) -> None:
    """Submit one configured multi-step test plan as durable matrix children."""
    dependencies = durable_job_dependencies()
    try:
        target = dependencies["target_service"].resolve(TargetRequest(
            args.project_dir, local=args.local, remote=args.remote,
            required_capability="job.exec" if not args.local else None))
    except TargetResolutionError as exc:
        _die(f"{exc.code}: {exc}")
    runtime = getattr(target, "runtime_policy", {}) or {}
    plans = runtime.get("testPlans", {})
    plan = plans.get(args.plan)
    if not isinstance(plan, dict):
        _die(f"unknown declared test plan {args.plan!r}")
    profiles = runtime.get("executionProfiles", {})
    profile_name = plan.get("executionProfile", runtime.get("executionProfile", "exec"))
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        _die(f"declared test plan {args.plan!r} references unknown execution profile {profile_name!r}")
    output_profile = getattr(args, "output_profile", None) or plan.get(
        "outputProfile", runtime.get("outputProfile", "smart"))
    if output_profile not in runtime.get("outputProfiles", {}):
        _die(f"declared test plan {args.plan!r} references unknown output profile {output_profile!r}")
    timeout = getattr(args, "timeout", None) or profile["timeoutSeconds"]
    deadline_source = "explicit" if getattr(args, "timeout", None) else f"plan:{args.plan}"
    raw_steps = plan.get("steps", [])
    if not isinstance(raw_steps, list) or not raw_steps:
        _die(f"declared test plan {args.plan!r} has no steps")
    labels = {}
    for step in raw_steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            _die(f"declared test plan {args.plan!r} has an invalid step")
        label = step.get("workspace") or f"{args.plan}-{step['id']}"
        if not isinstance(label, str) or len(label) > 64 or not label:
            _die(f"declared test plan {args.plan!r} has an invalid workspace label")
        if step["id"] in labels or label in labels.values():
            _die(f"declared test plan {args.plan!r} has duplicate step/workspace labels")
        labels[step["id"]] = label
    max_parallel = plan.get("maxParallel", runtime.get("maxParallel", 1))
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 64:
        _die(f"declared test plan {args.plan!r} has an invalid maxParallel")
    source = _source_identity(target.project_root)
    project_identity = hashlib.sha256(target.project_root.encode()).hexdigest()
    submissions = []
    for index, step in enumerate(raw_steps):
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            _die(f"declared test plan {args.plan!r} step {step['id']!r} has an invalid argv")
        needs = step.get("needs", [])
        if not isinstance(needs, list) or any(not isinstance(item, str) or item not in labels for item in needs):
            _die(f"declared test plan {args.plan!r} step {step['id']!r} has an unknown dependency")
        dependencies_by_label = [labels[item] for item in needs]
        # Steps run independently only when configuration opts in. Otherwise
        # preserve declared order as an explicit durable edge.
        if not step.get("parallelSafe", False) and index:
            dependencies_by_label.append(labels[raw_steps[index - 1]["id"]])
        # A plan-level cap adds stable backwards-only edges, allowing the
        # scheduler to enforce the cap without an unbounded local coordinator.
        if index >= max_parallel:
            dependencies_by_label.append(labels[raw_steps[index - max_parallel]["id"]])
        artifacts = step.get("artifacts", [])
        if not isinstance(artifacts, list) or any(not isinstance(item, str) or not item for item in artifacts):
            _die(f"declared test plan {args.plan!r} step {step['id']!r} has invalid artifacts")
        submissions.append(JobSubmission(
            "test", target.project_root, project_identity, target.kind, labels[step["id"]], tuple(argv), timeout,
            source, remote_name=target.remote_name, workspace_mode="isolated",
            output_profile=output_profile, execution_profile=profile_name,
            deadline_source=deadline_source, depends_on=tuple(dict.fromkeys(dependencies_by_label)),
            artifact_paths=tuple(artifacts),
        ))
    if target.kind == "remote":
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path).submit_many(submissions)
    else:
        result = dependencies["job_service"].submit_matrix(submissions)
    result = {**result, "plan": args.plan,
              "target": {"kind": target.kind, "remote": target.remote_name},
              "deadline": {"seconds": timeout, "source": deadline_source}}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result.get("parent_job_id", ""))


def cmd_job_retention(_cfg, args) -> None:
    result = durable_job_dependencies()["job_service"].retention_sweep(
        retention_days=args.retention_days, limit=args.limit,
        storage_pressure=args.storage_pressure)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"cleaned={len(result['cleaned'])} retention_days={result['retention_days']}")


def cmd_job_matrix(_cfg, args) -> None:
    command = list(args.command or ())
    if command[:1] == ["--"]: command = command[1:]
    if not args.spec_json and (not args.workspace or not command):
        _die("usage: ./sb job-matrix --workspace LABEL [--workspace LABEL] -- <argv...>")
    # The remote transport uses deterministic sibling workspaces of the exact
    # deployment path. The resolved config root can differ (for example when
    # a copied checkout retains a Git worktree pointer), so retain the
    # explicitly declared deployment boundary for validating those children.
    declared_project_root = Path(args.project_dir).expanduser().resolve()
    dependencies = durable_job_dependencies()
    try:
        target = dependencies["target_service"].resolve(TargetRequest(args.project_dir, local=args.local,
            remote=args.remote, required_capability="job.exec" if args.remote else None))
    except TargetResolutionError as exc:
        _die(f"{exc.code}: {exc}")
    source = _source_identity(target.project_root)
    project_identity = hashlib.sha256(target.project_root.encode()).hexdigest()
    if args.spec_json:
        try:
            decoded = base64.b64decode(args.spec_json.encode(), validate=True).decode()
            specs = json.loads(decoded)
            if not isinstance(specs, list) or not specs:
                raise ValueError("matrix plan must be a non-empty list")
        except Exception as exc:
            _die(f"invalid matrix submission plan: {exc}")
        submissions = []
        difference_by_workspace = {}
        for item in specs:
            if not isinstance(item, dict):
                _die("invalid matrix submission plan entry")
            try:
                project_root = target.project_root
                if item.get("project_dir"):
                    project_root = str(Path(item["project_dir"]).expanduser().resolve())
                    deployed_root = declared_project_root
                    candidate_root = Path(project_root)
                    # Remote matrix workspaces are deterministic sibling
                    # copies (`<deploy>-workspace-<hash>`), not descendants:
                    # the shape keeps them valid project slugs while ensuring
                    # they cannot escape the exact deployed source tree.
                    isolated_sibling = (
                        candidate_root.parent == deployed_root.parent and
                        candidate_root.name.startswith(deployed_root.name + "-workspace-")
                    )
                    if deployed_root not in candidate_root.parents and not isolated_sibling:
                        raise ValueError("matrix project directory must remain under the deployed project")
                submissions.append(JobSubmission(
                    kind=item.get("kind", "test"), project_root=project_root,
                    project_identity=hashlib.sha256(project_root.encode()).hexdigest(), target_kind=target.kind,
                    remote_name=target.remote_name, workspace_label=item["workspace"],
                    argv=tuple(item["argv"]), deadline_seconds=item.get("timeout", args.timeout),
                    source=SourceIdentity(**item.get("source", {"identity": source.identity})),
                    workspace_mode=item.get("workspace_mode", "isolated"),
                    cwd_relative=item.get("cwd_relative", "."),
                    execution_profile=item.get("execution_profile", "exec"),
                    output_profile=item.get("output_profile", args.output_profile),
                    deadline_source=item.get("deadline_source", "explicit"),
                    stall_seconds=item.get("stall_seconds", 300),
                    cancel_on_stall=bool(item.get("cancel_on_stall", False)),
                    environment_keys=tuple(item.get("environment_keys", ())),
                    request_id=item.get("request_id"), cleanup_policy=item.get("cleanup_policy", "retain"),
                    depends_on=tuple(item.get("depends_on", ())),
                    failure_policy=item.get("failure_policy", "fail-fast"),
                    artifact_paths=tuple(item.get("artifact_paths", ())),
                    compatibility_differences=tuple(item.get("compatibility_differences", ())),
                ))
                difference_by_workspace[item["workspace"]] = tuple(item.get("compatibility_differences", ()))
            except (KeyError, TypeError, ValueError) as exc:
                _die(f"invalid matrix submission plan entry: {exc}")
    else:
        submissions = [JobSubmission("test", target.project_root, project_identity,
            target.kind, workspace, tuple(command), args.timeout, source, remote_name=target.remote_name,
            workspace_mode="isolated", output_profile=args.output_profile, deadline_source="explicit")
            for workspace in args.workspace]
        difference_by_workspace = {}
    if target.kind == "remote":
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path)
        result = transport.submit_many(submissions)
    else:
        result = dependencies["job_service"].submit_matrix(
            submissions, allow_project_variants=bool(args.spec_json))
    if args.spec_json and target.kind != "remote" and result.get("parent_job_id"):
        accepted_children = result.get("children", ())
        for accepted in accepted_children:
            differences = difference_by_workspace.get(accepted.get("workspace"), ())
            if differences:
                dependencies["job_service"].repository.record_compatibility_differences(
                    accepted["job_id"], list(differences))
    if args.json: print(json.dumps(result, sort_keys=True))
    else:
        for child in result["children"]: print(child["job_id"])


register_specs((
    CommandSpec("job-start", cmd_job_start, configure=configure_start_parser, owner=__name__, scope="global"),
    CommandSpec("job-status", cmd_job_status, configure=configure_status_parser, owner=__name__, scope="global"),
    CommandSpec("job-output", cmd_job_output, configure=configure_output_parser, owner=__name__, scope="global"),
    CommandSpec("job-list", cmd_job_list, configure=configure_list_parser, owner=__name__, scope="global"),
    CommandSpec("job-cancel", cmd_job_cancel, configure=configure_cancel_parser, owner=__name__, scope="global"),
    CommandSpec("job-retry", cmd_job_retry, configure=configure_retry_parser, owner=__name__, scope="global"),
    CommandSpec("job-cleanup", cmd_job_cleanup, configure=configure_cleanup_parser, owner=__name__, scope="global"),
    CommandSpec("job-metrics", cmd_job_metrics, configure=configure_metrics_parser, owner=__name__, scope="global"),
    CommandSpec("job-artifacts", cmd_job_artifacts, configure=configure_artifacts_parser, owner=__name__, scope="global"),
    CommandSpec("job-artifact-get", cmd_job_artifact_get, configure=configure_artifact_get_parser, owner=__name__, scope="global"),
    CommandSpec("job-reconcile", cmd_job_reconcile, configure=configure_reconcile_parser, owner=__name__, scope="global"),
    CommandSpec("job-retention", cmd_job_retention, configure=configure_retention_parser, owner=__name__, scope="global"),
    CommandSpec("job-matrix", cmd_job_matrix, configure=configure_matrix_parser, owner=__name__, scope="global"),
))
