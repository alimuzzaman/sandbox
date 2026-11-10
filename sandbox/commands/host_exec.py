"""`sb host exec`: one argv in a recorded target's service, stdin passed through."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys

import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.hosting import host_exec, target_resolution
from sandbox.hosting.host_exec import ExecRefusal, ExecRequest


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
    entry = remote.get_remote(args.remote)
    if not entry:
        raise ExecRefusal(host_exec.REMOTE_UNKNOWN, f"no remote named '{args.remote}'")
    if not entry.get("provisioned"):
        raise ExecRefusal(host_exec.REMOTE_NOT_PROVISIONED,
                          f"remote '{args.remote}' is not provisioned")
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


def cmd_host_exec(args) -> None:
    as_json = bool(getattr(args, "json", False))
    try:
        request, entry, prefix = _prepare(args)
        audit = host_exec.AuditLog()
    except ExecRefusal as exc:
        _refuse(exc.code, exc.detail, as_json=as_json, exit_code=exc.exit_code)
    except hosting.HostingError as exc:
        _refuse(getattr(exc, "code", None) or "host_exec_refused",
                getattr(exc, "detail", None) or str(exc), as_json=as_json,
                exit_code=host_exec.EXIT_REFUSAL)
    command = host_exec.remote_command(prefix, request)
    ssh_argv = remote.ssh_command_args(entry, command, multiplex=False)
    outcome = host_exec.run(ssh_argv, request, stdin=host_exec.stdin_source())
    audit_failed = False
    try:
        audit.write(request, outcome)
    except OSError as exc:
        # The command already ran; keep its status and say the record is missing.
        audit_failed = True
        print(f"sb host exec: warning: audit record not written: {exc.strerror}", file=sys.stderr)
    if as_json:
        envelope = outcome.envelope(request)
        if audit_failed:
            envelope["audit"] = "failed"
        print(json.dumps(envelope, sort_keys=True))
    elif outcome.code in {host_exec.TIMED_OUT, host_exec.TRANSPORT_FAILED,
                          host_exec.INTERRUPTED}:
        note = {
            host_exec.TIMED_OUT: f"timed out after {request.timeout_seconds}s",
            host_exec.TRANSPORT_FAILED: "exit 255: the SSH connection failed, or the command exited 255",
            host_exec.INTERRUPTED: "interrupted",
        }[outcome.code]
        print(f"sb host exec: {outcome.code}: {note}", file=sys.stderr)
    if outcome.exit_code != 0:
        raise SystemExit(outcome.exit_code)
