"""CLI-first runtime operations shared by non-WordPress Compose projects."""

from __future__ import annotations

import json
import time
import argparse
import shlex
import shutil
import sys
from pathlib import Path

from sandbox.application.context import preflight_instance_capability, runtime_service
from sandbox.core import _core, die
from sandbox.registry import CommandSpec, register_specs
from sandbox.runtimes.base import ExecutionRequest, OperationError, OperationRequest


_GUIDES = {
    "compose": (
        ("init", "./sb init --type compose",
         "Write/validate a reviewable Compose descriptor and print the ensure next step."),
        ("ensure", "./sb ensure", "Create, start, or reconcile the declared project."),
        ("status", "./sb status", "Inspect the declared runtime."),
        ("logs", "./sb logs", "Read service logs."),
        ("exec", "./sb exec [--local|--remote <name>] --workspace <name> --timeout <seconds> "
         "--detach --request-id <stable-id> -- <argv...>",
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

# The registry is the source of truth for the public command inventory.  Keep
# this exclusion explicit even when it is empty: a future controller-only
# command must opt out here rather than silently disappearing from `sb guide`.
# These names are intentionally not inferred from module ownership or parser
# shape, because those are implementation details rather than public API.
GUIDE_INTERNAL_ONLY_COMMANDS = frozenset()
GUIDE_COMMAND_EXCLUSIONS = GUIDE_INTERNAL_ONLY_COMMANDS


def _guide_invocation() -> str:
    """Return a command users can actually run from this checkout.

    Release/source archives may contain the Python package without the tracked
    `sb` wrapper.  Prefer a local/installed `sb`, otherwise show the portable
    module invocation instead of emitting a dead `./sb ...` recipe.
    """
    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.name == "sb" and argv0.exists():
        return "./sb"
    local = Path.cwd() / "sb"
    if local.is_file() and local.stat().st_mode & 0o111:
        return "./sb"
    installed = shutil.which("sb")
    if installed:
        return "sb"
    return f"{shlex.quote(sys.executable)} -m sandbox.cli"


def _with_invocation(command: str, invocation: str) -> str:
    """Replace the checked-in wrapper prefix in a curated guide command."""
    return command.replace("./sb", invocation, 1)


def _public_command_catalog(invocation: str) -> list[dict[str, str]]:
    """Render every public command registered by the CLI manifest."""
    from sandbox.registry import COMMAND_SPECS

    catalog = []
    for spec in COMMAND_SPECS.specs():
        if spec.name in GUIDE_INTERNAL_ONLY_COMMANDS:
            continue
        doc = (getattr(spec.handler, "__doc__", "") or "").strip().splitlines()
        purpose = doc[0].strip() if doc else f"Run the {spec.name} command."
        catalog.append({
            "name": spec.name,
            "command": f"{invocation} {spec.name}",
            "purpose": purpose,
            "aliases": ", ".join(spec.aliases),
        })
    return catalog


def configure_exec_parser(parser) -> None:
    parser.description = "Run an argv list in a generic Compose public service."
    parser.add_argument("command", nargs="...", help="argv after --; shell text is not inferred")
    parser.add_argument("--json", action="store_true", help="emit the runtime result as JSON")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true", help="use the host-local durable job runtime")
    target.add_argument("--remote", help="use a provisioned remote durable job runtime")
    parser.add_argument("--workspace", help="persistent or isolated workspace label")
    parser.add_argument("--timeout", type=int, help="finite maximum execution time in seconds")
    parser.add_argument("--execution-profile", dest="profile",
                        help="named execution policy profile")
    parser.add_argument("--stall-seconds", type=int)
    parser.add_argument("--cancel-grace-seconds", type=int)
    parser.add_argument("--cancel-on-stall", action="store_true", default=None)
    parser.add_argument("--no-cancel-on-stall", action="store_false", dest="cancel_on_stall")
    parser.add_argument("--cleanup-policy", choices=("retain", "always", "on-success", "ephemeral"))
    parser.add_argument("--detach", action="store_true", help="return a durable job ID without waiting")
    parser.add_argument("--request-id",
                        help="stable idempotency key for a durable submission")
    parser.add_argument("--output-profile", help="retained-output presentation profile")
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

    request_id = getattr(args, "request_id", None)
    if request_id and getattr(args, "in_instance", False):
        die("--request-id requires durable execution; add --detach or select --local/--remote")

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

    # The remote transport's in-instance controller has already selected its
    # VPS and ensured the project Compose service. It must invoke that service
    # directly; submitting another local durable job would execute the argv on
    # the VPS host instead of in the declared container image.
    if (request_id and not args.in_instance and not args.local and not args.remote and
            not args.detach and target is not None and target.kind == "local"):
        die("--request-id requires durable execution; add --detach or select --local/--remote")

    if not args.in_instance and (args.local or args.remote or args.detach or
                                 (target is not None and target.kind == "remote")):
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import JobSubmission, TargetRequest
        from sandbox.commands.jobs_runtime import (_resolved_execution_policy, _resolved_output_profile,
                                                   _resolved_project_identity, _source_identity)
        if target is None:
            try:
                target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
                    project_dir=str(Path.cwd()), local=args.local, remote=args.remote,
                    workspace=args.workspace,
                    required_capability="job.exec" if args.remote else None,
                ))
            except TargetResolutionError as exc:
                die(f"{exc.code}: {exc}")
        policy = _resolved_execution_policy(target, args)
        output_profile = _resolved_output_profile(target, args.output_profile)
        source = _source_identity(target.project_root)
        submission = JobSubmission("runtime-exec" if target.kind == "remote" else "exec", target.project_root,
            _resolved_project_identity(target), target.kind,
            target.workspace_label, tuple(command), policy.deadline_seconds, source,
            remote_name=target.remote_name, output_profile=output_profile,
            output_profile_definition=(getattr(target, "runtime_policy", {}).get("outputProfiles", {})
                                       .get(output_profile)),
            execution_profile=policy.execution_profile, deadline_source=policy.deadline_source,
            deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
            cancel_grace_seconds=policy.cancel_grace_seconds, cancel_on_stall=policy.cancel_on_stall,
            cleanup_policy=policy.cleanup_policy, execution_policy_provenance=policy.provenance,
            request_id=request_id)
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
                      f"deadline={deadline.get('seconds', policy.deadline_seconds)}s "
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

    owner = _core().registry_find_instance(args.resolved_instance) or {}
    core = _core()
    load_project_config = getattr(core, "load_project_config", None)
    descriptor = load_project_config(owner["root"], label=owner.get("label", "default")) \
        if owner.get("root") and load_project_config is not None else {}
    managed = descriptor.get("wordpressRuntime", {}).get("mode") == "managed_native"
    if managed:
        execution_request = ExecutionRequest(
            owner["root"], owner.get("label", "default"), "exec", tuple(command),
            args.timeout if args.timeout is not None else 900,
        )
    capability_error = preflight_instance_capability(
        cfg, args.resolved_instance, "exec" if managed else "compose.exec",
    )
    if capability_error is not None:
        die(capability_error.message)
    if managed:
        from sandbox.application.context import execute_project
        execution = execute_project(cfg, execution_request)
        data = {"ok": execution.ok, "operation": "exec", "state": execution.state,
                "exit_code": execution.exit_code, **dict(execution.data)}
        if getattr(args, "json", False):
            print(json.dumps(data))
        else:
            print(data.get("stdout", ""), end="")
        if not execution.ok:
            die(data.get("stderr") or data.get("reason", {}).get("message") or
                "managed isolated execution failed")
        return
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
    invocation = _guide_invocation()
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
        {"name": name, "command": _with_invocation(command, invocation), "purpose": purpose}
        for name, command, purpose in _GUIDES[selected]
    ]
    command_catalog = _public_command_catalog(invocation)
    payload = {
        "mode": "cli-first",
        "project_kind": selected,
        "project_root": root,
        "skill": f"{invocation} skill show sandbox-cli",
        "commands": commands,
        "command_catalog": command_catalog,
        "command_catalog_exclusions": sorted(GUIDE_INTERNAL_ONLY_COMMANDS),
        "mcp": f"optional; use {invocation} mcp only when an MCP client needs live tools",
    }
    if getattr(args, "json", False):
        print(json.dumps(payload))
        return
    print(f"CLI-first Sandbox guide ({selected})")
    if root:
        print(f"  project: {root}")
    print(f"  skill:   {invocation} skill show sandbox-cli")
    print("  Configured remote execution is the default; use --local deliberately.")
    print("  MCP is optional; for live remote jobs prefer the co-located remote MCP server.")
    for item in commands:
        print(f"  {item['command']:<58} {item['purpose']}")
    print("  public command catalog:")
    print("    " + ", ".join(item["name"] for item in command_catalog))


register_specs((
    CommandSpec(name="exec", handler=cmd_exec, configure=configure_exec_parser,
                owner=__name__, scope="instance", required_capability="compose.exec"),
    CommandSpec(name="guide", handler=cmd_guide, configure=configure_guide_parser,
                owner=__name__, scope="global"),
))
