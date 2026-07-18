"""Feature-owned CLI adapters for durable job execution and observation."""

from __future__ import annotations

import hashlib
import base64
import json
import time
from pathlib import Path

from sandbox.application.context import durable_job_dependencies
from sandbox.application.target_service import TargetResolutionError
from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity, TargetRequest
from sandbox.registry import CommandSpec, register_specs


def _die(message: str) -> None:
    from sandbox.core import die
    die(message)


def _source_identity(root: str) -> SourceIdentity:
    # Local execution still has an identity. Remote submission replaces this with
    # the deploy identity returned by the deployment transport before acceptance.
    return SourceIdentity("sha256:" + hashlib.sha256(str(Path(root)).encode()).hexdigest())


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
    parser.add_argument("--cursor")
    parser.add_argument("--tail-bytes", type=int)
    parser.add_argument("--max-bytes", type=int, default=65536)
    parser.add_argument("--encoding", choices=("utf8", "base64"), default="utf8")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=0,
                        help="bounded retained-log long poll (0 disables waiting)")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def configure_list_parser(parser) -> None:
    parser.add_argument("--limit", type=int, default=50)
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
        deadline_source="explicit" if args.timeout else f"profile:{profile}",
    )
    if target.kind == "remote":
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
            ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote).submit(submission)
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
        print(accepted["job_id"])


def cmd_job_status(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote).status(args.remote, args.job_id)
    else:
        result = durable_job_dependencies()["job_service"].get(args.job_id)
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} {result['lifecycle']} ({result['health']})")


def cmd_job_output(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote)
        cursor = args.cursor
        while True:
            result = transport.read_output(args.remote, args.job_id, stream=args.stream, cursor=cursor,
                tail_bytes=args.tail_bytes, max_bytes=args.max_bytes,
                wait_seconds=max(args.wait_seconds, 1) if args.follow else args.wait_seconds,
                encoding=args.encoding)
            if args.json: print(json.dumps(result, sort_keys=True))
            elif result.get("data"): print(result["data"], end="")
            if not args.follow: return
            state = transport.status(args.remote, args.job_id)
            if state.get("lifecycle") in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}: return
            cursor = result.get("cursor"); time.sleep(.2)
    service = durable_job_dependencies()["job_service"]
    cursor = args.cursor
    while True:
        result = service.read_output(args.job_id, OutputQuery(stream=args.stream, cursor=cursor,
            tail_bytes=args.tail_bytes, max_bytes=args.max_bytes,
            wait_seconds=max(args.wait_seconds, 1) if args.follow else args.wait_seconds,
            encoding=args.encoding))
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
            remote_lookup=_remote.get_remote).list(args.remote, limit=args.limit)
        result = result.get("jobs", result)
    else:
        result = durable_job_dependencies()["job_service"].list({"limit": args.limit})
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
                remote_lookup=_remote.get_remote).cancel(args.remote, args.job_id, force=args.force)
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
            remote_lookup=_remote.get_remote).retry(args.remote, args.job_id, request_id=args.request_id)
    else:
        result = durable_job_dependencies()["job_service"].retry(args.job_id, request_id=args.request_id)
    print(json.dumps(result, sort_keys=True) if args.json else result["job_id"])


def cmd_job_cleanup(_cfg, args) -> None:
    selected = args.logs or args.artifacts or args.metrics
    options = {"logs": args.logs or not selected, "artifacts": args.artifacts or not selected,
               "metrics": args.metrics or not selected}
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote).cleanup(args.remote, args.job_id, **options)
    else:
        result = durable_job_dependencies()["job_service"].cleanup(args.job_id, **options)
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} cleanup={result['cleanup_state']}")


def cmd_job_metrics(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote).metrics(args.remote, args.job_id, limit=args.limit)
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
            remote_lookup=_remote.get_remote).artifacts(args.remote, args.job_id)
    else:
        result = {"ok": True, "artifacts": durable_job_dependencies()["job_service"].list_artifacts(args.job_id)}
    print(json.dumps(result, sort_keys=True) if args.json else "\n".join(
        f"{item['artifact_id']} {item['display_name']} {item['size_bytes']} bytes"
        for item in result.get("artifacts", ())))


def cmd_job_artifact_get(_cfg, args) -> None:
    if args.remote:
        from sandbox.core import _remote
        from sandbox.transports.remote_jobs import RemoteJobTransport
        result = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
            remote_lookup=_remote.get_remote).artifact_get(args.remote, args.job_id, args.artifact_id,
                offset=args.offset, max_bytes=args.max_bytes)
    else:
        data = durable_job_dependencies()["job_service"].get_artifact(args.job_id, args.artifact_id,
            offset=args.offset, max_bytes=args.max_bytes)
        result = {"ok": True, "job_id": args.job_id, "artifact_id": args.artifact_id,
                  "offset": args.offset, "data": base64.b64encode(data).decode(),
                  "bytes_read": len(data), "encoding": "base64"}
    if args.output_file:
        Path(args.output_file).expanduser().resolve().write_bytes(base64.b64decode(result["data"]))
    elif args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['artifact_id']} {result['bytes_read']} bytes")


def cmd_job_reconcile(_cfg, args) -> None:
    result = durable_job_dependencies()["job_service"].reconcile_startup(limit=args.limit)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"interrupted={len(result['interrupted'])} released={len(result['released_leases'])}")


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
                    if Path(target.project_root) not in Path(project_root).parents:
                        raise ValueError("matrix project directory must remain under the deployed project")
                submissions.append(JobSubmission(
                    kind=item.get("kind", "test"), project_root=project_root,
                    project_identity=hashlib.sha256(project_root.encode()).hexdigest(), target_kind=target.kind,
                    remote_name=target.remote_name, workspace_label=item["workspace"],
                    argv=tuple(item["argv"]), deadline_seconds=item.get("timeout", args.timeout),
                    source=SourceIdentity(**item.get("source", {"identity": source.identity})),
                    workspace_mode=item.get("workspace_mode", "isolated"),
                    output_profile=item.get("output_profile", args.output_profile),
                    deadline_source=item.get("deadline_source", "explicit"),
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
            remote_lookup=_remote.get_remote)
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
