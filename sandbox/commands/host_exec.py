"""`sb host exec`: one argv in a recorded target's service, stdin passed through."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
import datetime
import hashlib

import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.hosting import host_exec, target_resolution
from sandbox.hosting.host_exec import ExecRefusal, ExecRequest
from sandbox.hosting.server_cli_target import host_target, is_owner_target, can_operate_host


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
        selected = read_owner_exec_selection(entry,
            target=getattr(args, "target", None), name=getattr(args, "name", None),
            environment=getattr(args, "environment", None))
        context = entry.require_current()
        remaining = int((datetime.datetime.fromisoformat(context.deadline_at.replace("Z", "+00:00"))
                         - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        if remaining <= 0:
            raise ExecRefusal(host_exec.TIMED_OUT, "original server CLI deadline expired")
        request = ExecRequest(target=selected.target, service=service, argv=tuple(argv),
            timeout_seconds=min(timeout, remaining), capture=bool(getattr(args, "json", False)),
            owner_selection=selected)
        prefix = selected.compose_prefix()
        services = _deployed_services(entry, prefix)
        if service not in services:
            raise ExecRefusal(host_exec.UNKNOWN_SERVICE, "service is absent from the selected server target")
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


@contextmanager
def _execution_scope(request, entry):
    """Native alias locks plus the existing physical target lease, held once."""
    selected = request.owner_selection
    if selected is None:
        yield
        return
    from sandbox.hosting.server_target_selection import OwnerExecSelection
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting import target_lease
    if type(selected) is not OwnerExecSelection or not is_owner_target(entry):
        raise ExecRefusal(host_exec.TRANSPORT_FAILED, "current server target evidence is required")
    repository = RecoveryRepository()
    with ExitStack() as held:
        for key in selected.native_keys:
            held.enter_context(repository.effect_lock(key, timeout_seconds=0))
        selected.require_current()
        context = entry.require_current()
        # One job cannot take over another live job's physical lease merely
        # because both execute as the same shared Linux owner.
        owner = hashlib.sha256(("server-cli-exec:" + context.job_id).encode()).hexdigest()[:32]
        lease = target_lease.acquire(lambda command: entry.process(command, timeout=30),
            target_lease.lease_path(entry.sandbox_home(), selected.target.project,
                                    selected.target.environment), owner)
        clean_exit = False
        try:
            selected.require_current()
            yield
            selected.require_current()
            clean_exit = True
        finally:
            if clean_exit:
                # Do not acknowledge successful cleanup on an unavailable
                # process result. The generic release helper is best-effort.
                result = entry.process(target_lease.command(lease["path"], lease["token"],
                    lease["owner"], "release"), timeout=30)
                if result.returncode:
                    raise RuntimeError("host_exec_lease_retirement_unknown")


def cmd_host_exec(args) -> None:
    as_json = bool(getattr(args, "json", False))
    try:
        request, entry, prefix = _prepare(args)
        command = host_exec.remote_command(prefix, request)
        process_options = {}
        if is_owner_target(entry):
            from sandbox.application.server_cli import owner_command_args, owner_environment
            context = entry.require_current()
            ssh_argv = owner_command_args(context.owner, command)
            process_options = {"env": owner_environment(context.owner), "cwd": context.owner.home}
        else:
            ssh_argv = remote.ssh_command_args(entry, command, multiplex=False)
        audit = host_exec.AuditLog()
    except ExecRefusal as exc:
        _refuse(exc.code, exc.detail, as_json=as_json, exit_code=exc.exit_code)
    except hosting.HostingError as exc:
        _refuse(getattr(exc, "code", None) or "host_exec_refused",
                getattr(exc, "detail", None) or str(exc), as_json=as_json,
                exit_code=host_exec.EXIT_REFUSAL)
    audit_failed = False
    started = time.monotonic()
    with audit:
        try:
            with _execution_scope(request, entry):
                outcome = host_exec.run(ssh_argv, request, stdin=host_exec.stdin_source(),
                                        **process_options)
                if request.owner_selection is not None and outcome.code in {
                        host_exec.WATCHDOG_EXPIRED, host_exec.INTERRUPTED}:
                    # Preserve the physical fence when descendant completion
                    # cannot be established. Never replay the command here.
                    raise RuntimeError("host_exec_effects_unknown")
        except KeyboardInterrupt:
            outcome = host_exec.ExecOutcome(
                exit_code=130, code=host_exec.INTERRUPTED,
                duration_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # never leave a run unaudited or exit 0
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
    if as_json:
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
