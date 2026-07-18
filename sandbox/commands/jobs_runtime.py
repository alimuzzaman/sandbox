"""Feature-owned CLI adapters for durable job execution and observation."""

from __future__ import annotations

import hashlib
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
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="...", help="argv after --")


def configure_status_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--json", action="store_true")


def configure_output_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--stream", choices=("combined", "stdout", "stderr"), default="combined")
    parser.add_argument("--cursor")
    parser.add_argument("--tail-bytes", type=int)
    parser.add_argument("--max-bytes", type=int, default=65536)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--json", action="store_true")


def configure_list_parser(parser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")


def configure_cancel_parser(parser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")


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
            workspace=args.workspace, required_capability="compose.remote-deploy" if args.remote else None,
        ))
    except TargetResolutionError as exc:
        _die(f"{exc.code}: {exc}")
    timeout, profile = _profile_timeout(target, args.profile, args.timeout)
    submission = JobSubmission(
        kind="exec", project_root=target.project_root, project_identity=hashlib.sha256(target.project_root.encode()).hexdigest(),
        target_kind=target.kind, remote_name=target.remote_name, workspace_label=target.workspace_label,
        argv=tuple(command), deadline_seconds=timeout, source=_source_identity(target.project_root),
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
    if args.wait:
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
    result = durable_job_dependencies()["job_service"].get(args.job_id)
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} {result['lifecycle']} ({result['health']})")


def cmd_job_output(_cfg, args) -> None:
    service = durable_job_dependencies()["job_service"]
    cursor = args.cursor
    while True:
        result = service.read_output(args.job_id, OutputQuery(stream=args.stream, cursor=cursor,
            tail_bytes=args.tail_bytes, max_bytes=args.max_bytes, wait_seconds=1 if args.follow else 0))
        if args.json:
            print(json.dumps(result, sort_keys=True))
        elif result["data"]:
            print(result["data"], end="")
        if not args.follow or service.get(args.job_id)["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
            return
        cursor = result["cursor"]
        time.sleep(.2)


def cmd_job_list(_cfg, args) -> None:
    result = durable_job_dependencies()["job_service"].list({"limit": args.limit})
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for item in result:
            print(f"{item['job_id']} {item['lifecycle']} {item['workspace_label']}")


def cmd_job_cancel(_cfg, args) -> None:
    try:
        result = durable_job_dependencies()["job_service"].cancel(args.job_id, force=args.force)
    except RuntimeError as exc:
        _die(str(exc))
    print(json.dumps(result, sort_keys=True) if args.json else f"{result['job_id']} cancelling")


register_specs((
    CommandSpec("job-start", cmd_job_start, configure=configure_start_parser, owner=__name__, scope="global"),
    CommandSpec("job-status", cmd_job_status, configure=configure_status_parser, owner=__name__, scope="global"),
    CommandSpec("job-output", cmd_job_output, configure=configure_output_parser, owner=__name__, scope="global"),
    CommandSpec("job-list", cmd_job_list, configure=configure_list_parser, owner=__name__, scope="global"),
    CommandSpec("job-cancel", cmd_job_cancel, configure=configure_cancel_parser, owner=__name__, scope="global"),
))
