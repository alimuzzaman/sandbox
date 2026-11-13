"""`sb host exec`: one argv in a recorded target's service, stdin passed through."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
import dataclasses
import datetime
import math

import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.hosting import host_exec, target_resolution
from sandbox.hosting.host_exec import ExecRefusal, ExecRequest
from sandbox.hosting.server_cli_target import host_target, is_owner_target, can_operate_host

# v1 means retained selector consumption plus the native/physical held scope
# below. It describes the existing redacted UTF-8 capture, not binary transport.
SERVER_CLI_EXEC_BINDING_VERSION = 1
# Requires the committed private-artifact custody API, metadata-only emitter,
# actual EOF capture and byte-aware client renderer shipped in this increment.
SERVER_CLI_EXEC_CAPTURE_VERSION = 1


def _refuse(code: str, message: str, *, as_json: bool, exit_code: int) -> None:
    if as_json:
        print(json.dumps({"ok": False, "code": code, "error": message}, sort_keys=True))
    else:
        print(f"error: {code}: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def _stack_job_lifecycle(target) -> str | None:
    if not target.last_apply_job_id:
        return None
    from sandbox.commands.host_stack import job_view
    return (job_view(target.last_apply_job_id) or {}).get("lifecycle")


def _deployed_services(entry: dict, prefix: str) -> list[str]:
    from sandbox.commands.hosting import _remote_checked
    try:
        output = _remote_checked(entry, f"{prefix} --profile {shlex.quote('*')} config --services",
                                 timeout=60)
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        raise ExecRefusal(host_exec.SERVICES_UNAVAILABLE,
                          "could not read the target's deployed services: "
                          + remote.redact_text(str(exc))[:500]) from None
    return sorted({line.strip() for line in (output or "").splitlines() if line.strip()})


def _prepare(args) -> tuple[ExecRequest, dict, str]:
    """Every check that must pass before anything runs on the remote."""
    argv = getattr(args, "exec_argv", None)
    if not argv:
        raise ExecRefusal(host_exec.USAGE,
                          "host exec needs a command after --, e.g. -- sh -c 'id -u'",
                          host_exec.EXIT_USAGE)
    timeout = host_exec.parse_timeout(getattr(args, "timeout", None))
    service = getattr(args, "service", None)
    if not service:
        raise ExecRefusal(host_exec.USAGE, "host exec requires --service", host_exec.EXIT_USAGE)
    if not getattr(args, "remote", None):
        raise ExecRefusal(host_exec.USAGE, "host exec requires --remote", host_exec.EXIT_USAGE)
    entry = host_target(args.remote)
    if not entry:
        raise ExecRefusal(host_exec.REMOTE_UNKNOWN, f"no remote named '{args.remote}'")
    if not can_operate_host(entry):
        raise ExecRefusal(host_exec.REMOTE_NOT_PROVISIONED,
                          f"remote '{args.remote}' is not provisioned")
    if is_owner_target(entry):
        from sandbox.hosting.server_target_selection import read_owner_exec_selection
        context = entry.require_current()
        if context.target_selector_digest is None:
            raise ExecRefusal("server_cli_target_binding_unavailable",
                              "original server exec has no retained target selector")
        selected = read_owner_exec_selection(entry,
            target=getattr(args, "target", None), name=getattr(args, "name", None),
            environment=getattr(args, "environment", None),
            expected_selector_digest=context.target_selector_digest)
        remaining = int((datetime.datetime.fromisoformat(context.deadline_at.replace("Z", "+00:00"))
                         - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        if remaining <= 0:
            raise ExecRefusal(host_exec.TIMED_OUT, "original server CLI deadline expired")
        request = ExecRequest(target=selected.target, service=service, argv=tuple(argv),
            timeout_seconds=min(timeout, remaining), capture=bool(getattr(args, "json", False)),
            owner_selection=selected)
        # Live service/container labels are part of the current selector. This
        # branch never renders Compose with the provider-bearing env file.
        prefix = selected.exec_prefix(service)
        return request, entry, prefix
    state = hosting.load_host_state()
    target = target_resolution.resolve(
        args.remote, target=getattr(args, "target", None), name=getattr(args, "name", None),
        project_dir=getattr(args, "project_dir", None),
        environment=getattr(args, "environment", None), state=state)
    reason = host_exec.busy_reason((state.get("hosts") or {}).get(target.state_key),
                                   _stack_job_lifecycle(target))
    if reason is None and not host_exec.effect_lock_free(target.state_key):
        reason = "another host operation holds this target's lock"
    if reason is not None:
        raise ExecRefusal(target_resolution.TARGET_BUSY,
                          f"{target.label} on {args.remote}: {reason}; retry when it finishes")
    request = ExecRequest(target=target, service=service, argv=tuple(argv),
                          timeout_seconds=timeout, capture=bool(getattr(args, "json", False)))
    from sandbox.commands.hosting import _compose_prefix
    validated = hosting.validate_manifest(target.project_dir, target.environment)
    try:
        home = remote.resolve_sandbox_home(entry)
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        raise ExecRefusal(host_exec.TRANSPORT_FAILED,
                          "could not reach the remote: " + remote.redact_text(str(exc))[:500],
                          host_exec.EXIT_TRANSPORT) from None
    runtime_dir = f"{home}/runtime/hosts/{target.project}/{target.environment}"
    prefix = _compose_prefix(validated, f"{home}/deploy-src/hosts/{target.project}",
                             f"{runtime_dir}/compose.override.yml",
                             f"{runtime_dir}/environment.env")
    services = _deployed_services(entry, prefix)
    if service not in services:
        raise ExecRefusal(host_exec.UNKNOWN_SERVICE,
                          f"{target.label} has no service {service!r}; deployed services: "
                          + (", ".join(services) or "none"))
    return request, entry, prefix


def _fit_deadline(request, deadline_at):
    """Never take the lease without time to run, kill and release it by the deadline."""
    remaining = (datetime.datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
                 - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    budget = math.floor(remaining) - host_exec.SERVER_POST_COMMAND_SECONDS
    if budget < host_exec.TIMEOUT_MIN:
        raise ExecRefusal(host_exec.TIMED_OUT,
                          f"the original server CLI deadline leaves {max(0, math.floor(remaining))}s, "
                          f"less than the {host_exec.SERVER_POST_COMMAND_SECONDS + host_exec.TIMEOUT_MIN}s "
                          "needed to run the command and release the target; nothing ran")
    if budget >= request.timeout_seconds:
        return request
    return dataclasses.replace(request, timeout_seconds=budget)


def _command(request, entry, prefix):
    """The argv and process options for one run of ``request``."""
    command = (shlex.join(["timeout", f"--kill-after={host_exec.KILL_AFTER_SECONDS}",
                           str(request.timeout_seconds), "env"]) +
               " " + prefix + " " + shlex.join(request.argv) if request.owner_selection is not None else
               host_exec.remote_command(prefix, request))
    if is_owner_target(entry):
        from sandbox.application.server_cli import owner_command_args, owner_environment
        context = entry.require_current()
        return (owner_command_args(context.owner, command),
                {"env": owner_environment(context.owner), "cwd": context.owner.home})
    return remote.ssh_command_args(entry, command, multiplex=False), {}


def _known_failure(exc):
    """(code, message, exit code) for a failure whose cause is known, else None."""
    from sandbox.application.server_cli import ServerCLIDeadlineExpired
    from sandbox.hosting import target_lease
    if isinstance(exc, ExecRefusal):
        return exc.code, exc.detail, exc.exit_code
    if isinstance(exc, target_lease.LeaseHeld):
        return (target_resolution.TARGET_BUSY, f"{exc}; nothing ran. Retry once the holding job finishes. "
                "A lease left by an exec job that ended with its work stopped is taken over by the next exec; "
                "a lease held by an apply, a destroy, another controller or an exec job whose end is not "
                "proven stays until it is removed by hand (docs/remote-hosting.md, host_apply_lease_held)",
                host_exec.EXIT_REFUSAL)
    if isinstance(exc, ServerCLIDeadlineExpired):
        return (host_exec.TIMED_OUT, "the original server CLI deadline expired; "
                "inspect the original job before any replay", host_exec.EXIT_TIMEOUT)
    return None


def _take_over_ended_exec_lease(request, path, owner, job_id):
    """The lease of an ended, stopped exec job on this target, taken over and audited."""
    from sandbox.application.server_cli_exec_recovery import take_over_ended_exec_lease
    target = request.owner_selection.target
    # Opened first: a takeover happens only once the audit file is open. A failed
    # write of its line only warns, like the command's own audit line.
    with host_exec.AuditLog() as audit:
        taken = take_over_ended_exec_lease(path, owner=owner,
                                           target=f"{target.project}/{target.environment}")
        if taken is None:
            return None
        lease, previous_job_id = taken
        if previous_job_id is not None:
            try:
                audit.write_lease_takeover(request, previous_job_id=previous_job_id, job_id=job_id)
            except OSError as exc:
                print(f"sb host exec: warning: lease takeover from job {previous_job_id} not audited: "
                      f"{exc.strerror or exc}", file=sys.stderr)
    return lease


@contextmanager
def _execution_scope(request, entry):
    """Native alias locks plus the existing physical target lease, held once.

    Yields the request to run. On the owner path its timeout is clamped so the
    command, its kill grace and the lease release all fit the job deadline.
    """
    selected = request.owner_selection
    if selected is None:
        yield request
        return
    from sandbox.hosting.server_target_selection import OwnerExecSelection
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting import target_lease
    if type(selected) is not OwnerExecSelection or not is_owner_target(entry):
        raise ExecRefusal(host_exec.TRANSPORT_FAILED, "current server target evidence is required")
    context = entry.require_current()
    if (context.target_selector_digest is None or
            selected.expected_selector_digest != context.target_selector_digest):
        raise ExecRefusal("server_cli_target_binding_unavailable",
                          "original server exec target binding changed")
    repository = RecoveryRepository()
    with ExitStack() as held:
        for key in selected.native_keys:
            held.enter_context(repository.effect_lock(key, timeout_seconds=0))
        selected.require_current()
        context = entry.require_current()
        request = _fit_deadline(request, context.deadline_at)
        # One job cannot take over another live job's physical lease merely
        # because both execute as the same shared Linux owner.
        from sandbox.application.server_cli_exec_recovery import exec_lease_owner
        owner = exec_lease_owner(context.job_id)
        path = target_lease.lease_path(entry.sandbox_home(), selected.target.project,
                                       selected.target.environment)
        try:
            lease = target_lease.acquire(lambda command: entry.process(command, timeout=30),
                                         path, owner)
        except target_lease.LeaseHeld:
            lease = _take_over_ended_exec_lease(request, path, owner, context.job_id)
            if lease is None:
                raise
        selected.require_current()
        yield request
        selected.require_current()
    # Native context exit can fail. Retain the physical fence in that case;
    # neither clean child exit nor a successful inner check proves cleanup.
    selected.require_current()
    result = entry.process(target_lease.command(lease["path"], lease["token"],
        lease["owner"], "release"), timeout=30)
    if result.returncode:
        raise RuntimeError("host_exec_lease_retirement_unknown")


@contextmanager
def _capture_lifetime(custody):
    try:
        yield
    finally:
        if custody is not None:
            custody.close()


def cmd_host_exec(args) -> None:
    as_json = bool(getattr(args, "json", False))
    artifact_capture = bool(getattr(args, "exec_capture_artifacts", False))
    custody = None
    capture_metadata = None
    setup_complete = False
    try:
        request, entry, prefix = _prepare(args)
        if artifact_capture:
            if not as_json or not is_owner_target(entry) or request.owner_selection is None:
                raise ExecRefusal(host_exec.USAGE, "artifact capture requires original server exec",
                                  host_exec.EXIT_USAGE)
            # This hidden receiver option cannot select paths or issue custody.
            # The SDK revalidates the genuine retained original execution.
            from sandbox.application.server_cli_capture import prepare_server_cli_capture
            request.owner_selection.require_current()
            custody = prepare_server_cli_capture()
        ssh_argv, process_options = _command(request, entry, prefix)
        audit = host_exec.AuditLog()
        setup_complete = True
    except ExecRefusal as exc:
        _refuse(exc.code, exc.detail, as_json=as_json, exit_code=exc.exit_code)
    except hosting.HostingError as exc:
        _refuse(getattr(exc, "code", None) or "host_exec_refused",
                getattr(exc, "detail", None) or str(exc), as_json=as_json,
                exit_code=host_exec.EXIT_REFUSAL)
    except ImportError:
        if not artifact_capture:
            raise
        _refuse("host_exec_capture_unavailable", "installed original capture facility is unavailable",
                as_json=as_json, exit_code=host_exec.EXIT_REFUSAL)
    except RuntimeError as error:
        if not artifact_capture or getattr(error, "code", None) != "server_cli_capture_unavailable":
            raise
        _refuse("host_exec_capture_unavailable", "original capture preparation is unavailable before effects",
                as_json=as_json, exit_code=host_exec.EXIT_REFUSAL)
    finally:
        if not setup_complete and custody is not None:
            custody.close()
    audit_failed = False
    failure = None
    started = time.monotonic()
    with audit, _capture_lifetime(custody):
        try:
            with _execution_scope(request, entry) as scoped:
                if scoped is not request:
                    request = scoped
                    ssh_argv, process_options = _command(request, entry, prefix)
                outcome = host_exec.run(ssh_argv, request, stdin=host_exec.stdin_source(),
                                        **process_options)
                if request.owner_selection is not None and outcome.code in {
                        host_exec.WATCHDOG_EXPIRED, host_exec.INTERRUPTED, host_exec.TIMED_OUT}:
                    # Preserve the physical fence when descendant completion
                    # cannot be established. Never replay the command here.
                    raise RuntimeError("host_exec_effects_unknown")
            if custody is not None:
                # Historical byte custody follows all writer/context cleanup.
                # Cleanup failure must never create a positive capture receipt.
                request.owner_selection.require_current()
                entry.require_current()
                capture_metadata = custody.publish(outcome)
        except KeyboardInterrupt:
            outcome = host_exec.ExecOutcome(
                exit_code=130, code=host_exec.INTERRUPTED,
                duration_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # never leave a run unaudited or exit 0
            failure = _known_failure(exc)
            if failure is not None:
                outcome = host_exec.ExecOutcome(exit_code=failure[2], code=failure[0],
                    duration_ms=int((time.monotonic() - started) * 1000))
            else:
                outcome = host_exec.ExecOutcome(
                    exit_code=host_exec.EXIT_TRANSPORT, code=host_exec.TRANSPORT_FAILED,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stderr=f"host exec failed locally: {type(exc).__name__}\n".encode()
                    if request.capture else b"")
        try:
            audit.write(request, outcome)
        except OSError as exc:
            # The command already ran; keep its status and say the record is missing.
            audit_failed = True
            print(f"sb host exec: warning: audit record not written: {exc.strerror or exc}",
                  file=sys.stderr)
    if failure is not None:
        _refuse(failure[0], failure[1], as_json=as_json, exit_code=failure[2])
    if artifact_capture:
        if capture_metadata is None:
            _refuse("host_exec_capture_unavailable",
                    "original capture could not be retained; inspect the original job before replay",
                    as_json=as_json, exit_code=host_exec.EXIT_TRANSPORT)
        # No raw or reversibly encoded output enters the ordinary job log.
        print(json.dumps(capture_metadata, sort_keys=True))
    elif as_json:
        envelope = outcome.envelope(request)
        if audit_failed:
            envelope["audit"] = "failed"
        print(json.dumps(envelope, sort_keys=True))
    elif outcome.code in {host_exec.TIMED_OUT, host_exec.TRANSPORT_FAILED,
                          host_exec.WATCHDOG_EXPIRED, host_exec.INTERRUPTED}:
        note = {
            host_exec.TIMED_OUT: f"timed out after {request.timeout_seconds}s "
                                 f"(exit {outcome.exit_code})",
            host_exec.TRANSPORT_FAILED: "exit 255: the SSH connection failed, or the command exited 255",
            host_exec.WATCHDOG_EXPIRED: (
                "the local watchdog stopped ssh before the remote status arrived; the server's "
                "timeout still stops the exec client on its own, but a command that ignores end "
                "of input can keep running in the container. Check with: sb host status --remote "
                f"{request.target.remote} --target {request.target.label}"),
            host_exec.INTERRUPTED: "interrupted",
        }[outcome.code]
        print(f"sb host exec: {outcome.code}: {note}", file=sys.stderr)
    if outcome.exit_code != 0:
        raise SystemExit(outcome.exit_code)
