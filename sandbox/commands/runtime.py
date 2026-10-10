"""CLI-first runtime operations shared by non-WordPress Compose projects."""

from __future__ import annotations

import json
import time
import argparse
import re
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
        ("status", "./sb status",
         "Inspect the declared runtime after init/ensure; unregistered projects get a bootstrap hint."),
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
        ("status", "./sb status",
         "Inspect the declared runtime after init/ensure; unregistered projects get a bootstrap hint."),
        ("wp", "./sb wp [--remote <name>] --timeout 60 -- <wp-cli args...>",
         "Run bounded WP-CLI locally or against an existing deployed remote instance; use --async for long work."),
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
    parser.add_argument("--project-dir", default=None,
                        help="project directory whose registered instance should run (default: current directory)")
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
    # Internal controller step: hand entries a root container wrote into the
    # workspace bind mount back to the Sandbox user. Fail-soft by contract.
    parser.add_argument("--repair-workspace-ownership", action="store_true",
                        help=argparse.SUPPRESS)


def _exec_exit_code(data: dict) -> int:
    """Return a safe child exit status from an operation envelope."""
    value = data.get("exit_code")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 1


_REMOTE_EXEC_TERMINAL_STATES = frozenset({
    "succeeded", "failed", "timed_out", "cancelled", "interrupted",
})
_REMOTE_EXEC_ACTIVE_STATES = frozenset({"accepted", "queued", "running", "cancelling"})
_REMOTE_EXEC_OUTPUT_PAGE_BYTES = 65_536
_REMOTE_EXEC_STATUS_TIMEOUT_SECONDS = 5
_REMOTE_EXEC_OUTPUT_TIMEOUT_SECONDS = 25
_REMOTE_EXEC_OBSERVATION_GRACE_SECONDS = 5


def _remote_exec_failure(remote_name: str, message: str, *, job_id: str | None = None,
                         accepted: bool = False, as_json: bool = False) -> None:
    """Report a bounded follow failure without converting acceptance into success."""
    if as_json:
        payload = {
            "ok": False,
            "status": "unknown",
            "acceptance": "accepted" if accepted else "unknown",
            "operation": "exec",
            "target": {"kind": "remote", "remote": remote_name},
            "error": message[:500],
        }
        if job_id:
            payload["job_id"] = job_id
            payload["recovery"] = shlex.join([
                "./sb", "job-status", job_id, "--remote", remote_name, "--json",
            ])
        print(json.dumps(payload, sort_keys=True))
    else:
        if job_id:
            command = shlex.join(["./sb", "job-status", job_id, "--remote", remote_name, "--json"])
            recovery = f" Accepted job {job_id}; inspect it with `{command}`."
        else:
            recovery = ""
        print(f"error: {message}.{recovery}", file=sys.stderr)
    raise SystemExit(1)


def _validate_remote_exec_acceptance(accepted: object, remote_name: str,
                                     *, as_json: bool) -> str:
    """Require the durable receipt before any remote follow can claim success."""
    from sandbox.jobs.models import validate_ack_job_id

    if not isinstance(accepted, dict) or accepted.get("ok") is not True or \
            accepted.get("status") != "accepted":
        _remote_exec_failure(remote_name,
                             "remote exec acceptance is unknown or malformed",
                             as_json=as_json)
    try:
        job_id = validate_ack_job_id(accepted.get("job_id"))
    except ValueError:
        _remote_exec_failure(remote_name,
                             "remote exec acceptance is unknown or malformed",
                             as_json=as_json)
    return job_id


def _validate_remote_exec_status(state: object, job_id: str) -> tuple[str, dict]:
    """Accept only the selected job's successful status envelope and known lifecycle."""
    if not isinstance(state, dict) or state.get("ok") is not True or \
            state.get("job_id") != job_id:
        raise ValueError("remote job status is unknown or malformed")
    lifecycle = state.get("lifecycle")
    if not isinstance(lifecycle, str) or lifecycle not in (
            _REMOTE_EXEC_ACTIVE_STATES | _REMOTE_EXEC_TERMINAL_STATES):
        raise ValueError("remote job status is unknown or malformed")
    if lifecycle == "succeeded":
        exit_code = state.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
            raise ValueError("remote job success status is missing exit_code=0")
    return lifecycle, state


def _validate_remote_exec_output(page: object, job_id: str) -> dict:
    """Validate one bounded retained-output page before rendering or advancing its cursor."""
    cursor = page.get("cursor") if isinstance(page, dict) else None
    if not isinstance(page, dict) or page.get("ok") is not True or \
            page.get("job_id") != job_id or not isinstance(page.get("data"), str) or \
            not isinstance(cursor, str) or len(cursor) > 4096 or \
            re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", cursor) is None or \
            type(page.get("has_more")) is not bool:
        raise ValueError("remote job output page is unknown or malformed")
    if len(page["data"].encode("utf-8", errors="replace")) > _REMOTE_EXEC_OUTPUT_PAGE_BYTES:
        raise ValueError("remote job output page exceeded its requested bound")
    return page


def _follow_remote_exec(transport, remote_name: str, accepted: dict, job_id: str,
                        policy, output_profile: str, *, as_json: bool) -> None:
    """Wait for one remote exec within its resolved deadline and read bounded output pages."""
    from sandbox.transports.remote_jobs import RemoteJobTransportError

    started = time.monotonic()
    wait_deadline = started + policy.deadline_seconds + _REMOTE_EXEC_OBSERVATION_GRACE_SECONDS
    attached_human = bool(sys.stdout.isatty() and not as_json)
    cursor = None
    page = None
    terminal_state = None

    while terminal_state is None:
        remaining = wait_deadline - time.monotonic()
        if remaining < 1:
            _remote_exec_failure(
                remote_name,
                "remote exec did not reach a terminal status within its resolved deadline",
                job_id=job_id, accepted=True, as_json=as_json,
            )
        try:
            state = transport.status(
                remote_name, job_id,
                timeout=min(_REMOTE_EXEC_STATUS_TIMEOUT_SECONDS, int(remaining)),
            )
        except RemoteJobTransportError as exc:
            _remote_exec_failure(remote_name, str(exc), job_id=job_id,
                                 accepted=True, as_json=as_json)
        except Exception:
            _remote_exec_failure(remote_name, "remote job status observation failed",
                                 job_id=job_id, accepted=True, as_json=as_json)
        try:
            lifecycle, state = _validate_remote_exec_status(state, job_id)
        except ValueError as exc:
            _remote_exec_failure(remote_name, str(exc), job_id=job_id,
                                 accepted=True, as_json=as_json)
        if lifecycle in _REMOTE_EXEC_TERMINAL_STATES:
            terminal_state = state
            break

        # Each transport request has its own finite timeout. Leave enough room
        # for the 25-second bounded output read before the resolved wait deadline.
        remaining = wait_deadline - time.monotonic()
        if attached_human and remaining > _REMOTE_EXEC_OUTPUT_TIMEOUT_SECONDS:
            try:
                page = _validate_remote_exec_output(transport.read_output(
                    remote_name, job_id, cursor=cursor,
                    max_bytes=_REMOTE_EXEC_OUTPUT_PAGE_BYTES,
                    wait_seconds=2, profile=output_profile,
                ), job_id)
            except RemoteJobTransportError as exc:
                _remote_exec_failure(remote_name, str(exc), job_id=job_id,
                                     accepted=True, as_json=as_json)
            except Exception:
                _remote_exec_failure(remote_name, "remote job output observation failed",
                                     job_id=job_id, accepted=True, as_json=as_json)
            if page["data"]:
                print(page["data"], end="", flush=True)
            cursor = page["cursor"]
        else:
            time.sleep(min(2.0, max(0.0, remaining)))

    # JSON and non-terminal callers get one bounded final page. Attached human
    # callers resume from the last acknowledged cursor and print that page too.
    try:
        page = _validate_remote_exec_output(transport.read_output(
            remote_name, job_id, cursor=cursor,
            max_bytes=_REMOTE_EXEC_OUTPUT_PAGE_BYTES,
            wait_seconds=0, profile=output_profile,
        ), job_id)
    except RemoteJobTransportError as exc:
        _remote_exec_failure(remote_name, str(exc), job_id=job_id,
                             accepted=True, as_json=as_json)
    except Exception:
        _remote_exec_failure(remote_name, "remote job output observation failed",
                             job_id=job_id, accepted=True, as_json=as_json)

    if as_json:
        payload = {**accepted, "ok": terminal_state["lifecycle"] == "succeeded",
                   "result": terminal_state, "output": page}
        print(json.dumps(payload, sort_keys=True))
    elif not attached_human and page["data"]:
        print(page["data"], end="")
    elif attached_human and page["data"]:
        print(page["data"], end="", flush=True)
    if page["has_more"]:
        command = shlex.join([
            "./sb", "job-output", job_id, "--remote", remote_name,
            "--cursor", page["cursor"],
        ])
        print(
            f"remote exec output continues; resume with `{command}`.",
            file=sys.stderr,
        )

    if terminal_state["lifecycle"] != "succeeded":
        code = terminal_state.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= 255:
            code = 1
        if not as_json:
            print(
                f"remote exec job {job_id} {terminal_state['lifecycle']} "
                f"(exit={terminal_state.get('exit_code')})",
                file=sys.stderr,
            )
        raise SystemExit(code)


def _emit_exec_result(data: dict, *, as_json: bool) -> None:
    """Render one Compose exec result while preserving its child streams.

    Human output keeps stdout and stderr separate so a durable parent can
    retain the same evidence. JSON callers receive one machine-readable
    envelope; the streams remain fields in that envelope to preserve the
    contract that stdout contains one document.
    """
    if as_json:
        print(json.dumps(data))
    else:
        stdout = data.get("stdout", data.get("output", ""))
        stderr = data.get("stderr", "")
        if isinstance(stdout, str):
            print(stdout, end="")
        if isinstance(stderr, str):
            print(stderr, file=sys.stderr, end="")
    if not data.get("ok", True):
        raise SystemExit(_exec_exit_code(data))


def configure_guide_parser(parser) -> None:
    parser.description = "Show the CLI-first workflow for a project runtime."
    parser.add_argument("--project-dir", default=None,
                        help="project to inspect (default: current directory when configured)")
    parser.add_argument("--local", action="store_true",
                        help="explicitly select the host-local guide context")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable command catalog")


def _require_matching_remote_instance(args, target) -> None:
    """Refuse an explicit --instance that names another project's workspace.

    Remote exec deploys the caller's working tree first, so it runs in the
    instance derived from the resolved project and workspace label. An
    explicit --instance used to be ignored, sending the command into whatever
    project the cwd belonged to. An explicit selector must match or fail.
    """
    requested = getattr(args, "instance", None)
    if not requested:
        return
    from sandbox.core import _remote
    try:
        expected = _remote.remote_workspace_instance_name(
            target.project_root, target.workspace_label)
    except ValueError as exc:
        die(f"remote_instance_mismatch: {exc}")
    if requested == expected:
        return
    message = (
        f"--instance {requested} is not the remote instance for project "
        f"{target.project_root} (workspace '{target.workspace_label}' runs in "
        f"{expected}). Remote exec deploys the project's working tree, so select "
        "the project, not just the instance: pass --project-dir <that project> "
        "and, for a non-default workspace, --workspace <label>.")
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "code": "remote_instance_mismatch",
                          "error": message, "requested_instance": requested,
                          "resolved_instance": expected,
                          "project_root": str(target.project_root),
                          "workspace": target.workspace_label}))
        raise SystemExit(2)
    die(f"remote_instance_mismatch: {message}", 2)


def _repair_workspace_ownership(cfg, args, project_dir: str) -> None:
    """Run the bounded, fail-soft ownership repair for one Compose workspace.

    Prints one summary line on stderr (or one JSON document with --json) and
    exits 0 when ownership is repaired or there is nothing to repair, 1 when
    the repair could not run. Callers chain it with ``|| true``; it never
    replaces the exit status of the command it follows.
    """
    try:
        root = str(_core().find_project_root(Path(project_dir)))
        # A fixed find/chown maintenance operation, not a project payload: it
        # carries no caller argv, so the execution gateway does not apply.
        service = runtime_service(cfg)
        result = service.invoke(OperationRequest(
            project_root=root, operation="ownership_repair",
            label=getattr(args, "label", None) or "default",
            arguments={"timeout": min(int(getattr(args, "timeout", None) or 300), 600)},
        ))
    except Exception as exc:  # fail-soft: the repair must never crash a job
        result = OperationError("ownership_repair_failed",
                                f"{type(exc).__name__}: {str(exc)[:300]}")
    if isinstance(result, OperationError):
        # A runtime without the capability (WordPress, Herd) has nothing to
        # repair this way; that is a skip, not a failure line on every job.
        skipped = result.code in {"unsupported_capability", "unsupported_kind"}
        data = {"ok": skipped, "repaired": False,
                "reason": {"code": result.code, "message": result.message}}
    else:
        data = {"ok": bool(result.ok), **dict(result.data)}
    if getattr(args, "json", False):
        print(json.dumps(data))
    else:
        reason = (data.get("reason") or {}).get("code")
        state = ("repaired" if data.get("repaired") else
                 "skipped" if data.get("ok") else "failed")
        print(f"[sandbox] workspace ownership repair: {state}"
              + (f" ({reason})" if reason else ""), file=sys.stderr)
    if not data.get("ok"):
        raise SystemExit(1)


def cmd_exec(cfg, args) -> None:
    """Execute explicit argv in a generic Compose service without MCP."""
    project_dir = getattr(args, "project_dir", None) or str(Path.cwd())
    if getattr(args, "repair_workspace_ownership", False):
        _repair_workspace_ownership(cfg, args, project_dir)
        return
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
                project_dir=project_dir, workspace=args.workspace,
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
                    project_dir=project_dir, local=args.local, remote=args.remote,
                    workspace=args.workspace,
                    required_capability="job.exec" if args.remote else None,
                ))
            except TargetResolutionError as exc:
                die(f"{exc.code}: {exc}")
        if target.kind == "remote":
            _require_matching_remote_instance(args, target)
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
            from sandbox.readiness.gate import require_ready
            transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                remote_sb_path=_remote.remote_sb_path, readiness=require_ready)
            accepted = transport.submit(submission)
        else:
            service = durable_job_dependencies()["job_service"]
            accepted = service.submit(submission)
        if target.kind == "remote":
            job_id = _validate_remote_exec_acceptance(
                accepted, target.remote_name, as_json=bool(args.json))
            if not args.detach:
                _follow_remote_exec(
                    transport, target.remote_name, accepted, job_id, policy,
                    output_profile, as_json=bool(args.json),
                )
                # Reached only on success: a failed exec exits in the follower.
                from sandbox.readiness import check as _readiness
                try:
                    _readiness.record_exec(target.remote_name, (getattr(target, "sources", None) or {}).get(
                        "identity") or target.project_root)
                except (OSError, ValueError):
                    pass
                return
        if args.detach:
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
                project_dir=project_dir, local=True, workspace=args.workspace,
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
    _emit_exec_result(data, as_json=bool(getattr(args, "json", False)))


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
