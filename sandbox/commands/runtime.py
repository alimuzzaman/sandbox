"""CLI-first runtime operations shared by non-WordPress Compose projects."""

from __future__ import annotations

import json
import hashlib
import time
import argparse
from pathlib import Path

from sandbox.application.context import preflight_instance_capability, runtime_service
from sandbox.core import _core, die
from sandbox.registry import CommandSpec, register_specs
from sandbox.runtimes.base import OperationError, OperationRequest


_GUIDES = {
    "compose": (
        ("init", "./sb init --type compose", "Create a local Compose project instance."),
        ("ensure", "./sb ensure", "Start or reconcile the local instance."),
        ("status", "./sb status", "Inspect the declared runtime."),
        ("logs", "./sb logs", "Read service logs."),
        ("exec", "./sb exec [--local] --workspace <name> --timeout <seconds> -- <argv...>",
         "Run a durable job; a configured remote is the default and --local is explicit."),
        ("test", "./sb test <declared-mode> [--local] --timeout <seconds>",
         "Run the declared test command with a configured remote by default."),
        ("jobs", "./sb job-status <job-id> && ./sb job-output <job-id>",
         "Inspect durable retained output after a disconnected caller resumes."),
        ("deploy", "./sb deploy --remote <name> --ensure --expose", "Deploy to a provisioned remote."),
    ),
    "wordpress": (
        ("init", "./sb init", "Create a local WordPress project instance."),
        ("ensure", "./sb ensure", "Start or reconcile the local instance."),
        ("status", "./sb status", "Inspect the declared runtime."),
        ("wp", "./sb wp -- <wp-cli args...>", "Run WP-CLI."),
        ("test", "./sb test [--local] --timeout <seconds>",
         "Run the configured test mode with a configured remote by default."),
        ("jobs", "./sb job-status <job-id> && ./sb job-output <job-id>",
         "Inspect durable retained output after a disconnected caller resumes."),
        ("deploy", "./sb deploy --remote <name> --ensure --expose", "Deploy to a provisioned remote."),
    ),
}


def configure_exec_parser(parser) -> None:
    parser.description = "Run an argv list in a generic Compose public service."
    parser.add_argument("command", nargs="...", help="argv after --; shell text is not inferred")
    parser.add_argument("--json", action="store_true", help="emit the runtime result as JSON")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true", help="use the host-local durable job runtime")
    target.add_argument("--remote", help="use a provisioned remote durable job runtime")
    parser.add_argument("--workspace", help="persistent or isolated workspace label")
    parser.add_argument("--timeout", type=int, help="finite maximum execution time in seconds")
    parser.add_argument("--detach", action="store_true", help="return a durable job ID without waiting")
    parser.add_argument("--output-profile", default="smart", help="retained-output presentation profile")
    # Internal controller escape hatch.  A remote durable job has already
    # selected its VPS and owns the process/output lifecycle; it needs to
    # invoke the project Compose service directly without recursively creating
    # another durable job because that project's policy is remote-first.
    parser.add_argument("--in-instance", action="store_true", help=argparse.SUPPRESS)


def configure_guide_parser(parser) -> None:
    parser.description = "Show the CLI-first workflow for a project runtime."
    parser.add_argument("--project-dir", default=None,
                        help="project to inspect (default: current directory when configured)")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable command catalog")


def cmd_exec(cfg, args) -> None:
    """Execute explicit argv in a generic Compose service without MCP."""
    command = list(getattr(args, "command", ()) or ())
    # argparse REMAINDER deliberately retains the conventional separator. It
    # is syntax, not part of the command passed to the container.
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        die("usage: ./sb exec -- <argv...>")
    if any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        die("exec requires a non-empty argv list without NUL bytes")

    target = None
    if not args.local and not args.remote:
        # Resolve the configured target even when the caller did not spell out
        # a durable-job flag.  A project that opts into a remote default must
        # not silently fall back to direct local Compose execution.
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import TargetRequest
        try:
            target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
                project_dir=str(Path.cwd()), workspace=args.workspace,
                required_capability="job.exec",
            ))
        except TargetResolutionError as exc:
            die(f"{exc.code}: {exc}")

    if args.local or args.remote or args.detach or (target is not None and target.kind == "remote"):
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
        if target is None:
            try:
                target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
                    project_dir=str(Path.cwd()), local=args.local, remote=args.remote,
                    workspace=args.workspace,
                    required_capability="job.exec" if args.remote else None,
                ))
            except TargetResolutionError as exc:
                die(f"{exc.code}: {exc}")
        timeout = args.timeout or 900
        source = SourceIdentity("sha256:" + hashlib.sha256(target.project_root.encode()).hexdigest())
        submission = JobSubmission("runtime-exec" if target.kind == "remote" else "exec", target.project_root,
            hashlib.sha256(target.project_root.encode()).hexdigest(), target.kind,
            target.workspace_label, tuple(command), timeout, source,
            remote_name=target.remote_name, output_profile=args.output_profile,
            output_profile_definition=(getattr(target, "runtime_policy", {}).get("outputProfiles", {})
                                       .get(args.output_profile)),
            deadline_source="explicit" if args.timeout else "profile:exec")
        if target.kind == "remote":
            from sandbox.core import _remote
            from sandbox.transports.remote_jobs import RemoteJobTransport
            accepted = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path).submit(submission)
        else:
            service = durable_job_dependencies()["job_service"]
            accepted = service.submit(submission)
        if args.detach or target.kind == "remote":
            if args.json:
                print(json.dumps(accepted))
            else:
                target_info = accepted.get("target", {})
                target_name = target_info.get("remote") or target_info.get("kind") or target.remote_name or target.kind
                deadline = accepted.get("deadline", {})
                print(f"{accepted['job_id']} target={target_name} "
                      f"workspace={accepted.get('workspace', target.workspace_label)} "
                      f"deadline={deadline.get('seconds', timeout)}s "
                      f"source={deadline.get('source', submission.deadline_source)}")
            return
        service = durable_job_dependencies()["job_service"]
        while True:
            state = service.get(accepted["job_id"])
            if state["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                output = service.read_output(accepted["job_id"])
                print(json.dumps({**accepted, "result": state, "output": output}) if args.json else output["data"], end="" if not args.json else "\n")
                if state["lifecycle"] != "succeeded":
                    die(f"job {accepted['job_id']} {state['lifecycle']}")
                return
            time.sleep(.1)

    project_root = None
    if args.in_instance:
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import TargetRequest
        try:
            target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
                project_dir=str(Path.cwd()), local=True, workspace=args.workspace,
            ))
        except TargetResolutionError as exc:
            die(f"{exc.code}: {exc}")
        project_root = target.project_root

    capability_error = preflight_instance_capability(cfg, args.resolved_instance, "compose.exec")
    if capability_error is not None:
        die(capability_error.message)
    owner = _core().registry_find_instance(args.resolved_instance) or {}
    result = runtime_service(cfg).invoke(OperationRequest(
        project_root=project_root or owner["root"], operation="exec",
        label=owner.get("label", "default"), arguments={
            "argv": command,
            **({"timeout": args.timeout} if getattr(args, "timeout", None) is not None else {}),
        },
    ))
    if isinstance(result, OperationError):
        die(result.message)
    data = {"ok": result.ok, "operation": result.operation, **dict(result.data)}
    if getattr(args, "json", False):
        print(json.dumps(data))
        return
    print(data.get("output", ""), end="")


def cmd_guide(_cfg, args) -> None:
    """Print the no-MCP command catalog, optionally tailored to a project."""
    project_dir = getattr(args, "project_dir", None)
    kind = None
    root = None
    if project_dir:
        try:
            project = _core().load_project_config(project_dir)
        except Exception as exc:
            die(f"invalid --project-dir {project_dir!r}: {exc}")
        kind = project.get("kind")
        root = project.get("root")
    else:
        try:
            project = _core().load_project_config(Path.cwd())
        except Exception:
            project = None
        if project:
            kind = project.get("kind")
            root = project.get("root")

    selected = (kind or "compose").lower()
    if selected not in _GUIDES:
        die(f"no CLI guide for project kind {selected!r}")
    commands = [
        {"name": name, "command": command, "purpose": purpose}
        for name, command, purpose in _GUIDES[selected]
    ]
    payload = {
        "mode": "cli-first",
        "project_kind": selected,
        "project_root": root,
        "skill": "./sb skill show sandbox-cli",
        "commands": commands,
        "mcp": "optional; use ./sb mcp only when an MCP client needs live tools",
    }
    if getattr(args, "json", False):
        print(json.dumps(payload))
        return
    print(f"CLI-first Sandbox guide ({selected})")
    if root:
        print(f"  project: {root}")
    print("  skill:   ./sb skill show sandbox-cli")
    print("  Configured remote execution is the default; use --local deliberately.")
    print("  MCP is optional; for live remote jobs prefer the co-located remote MCP server.")
    for item in commands:
        print(f"  {item['command']:<58} {item['purpose']}")


register_specs((
    CommandSpec(name="exec", handler=cmd_exec, configure=configure_exec_parser,
                owner=__name__, scope="instance", required_capability="compose.exec"),
    CommandSpec(name="guide", handler=cmd_guide, configure=configure_guide_parser,
                owner=__name__, scope="global"),
))
