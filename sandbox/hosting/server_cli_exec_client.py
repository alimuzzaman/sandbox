"""Normal joined host exec, using one retained server job and bounded results.

The SDK owns authenticated metadata selection, original stdin admission and
receipt/result validation. Nothing here stages controller source or consults
controller hosting history. v1 carries the existing redacted UTF-8 capture.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
import secrets
import subprocess
import sys
import time

from sandbox.hosting import host_exec


def _handle(receipt, request_id):
    result = {"request_id": request_id}
    if isinstance(receipt, dict):
        job_id = receipt.get("job_id")
        if isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id):
            result["job_id"] = job_id
    return result


# Exit 2 for a request outside the documented bounds, as for other usage errors.
_REQUEST_REFUSALS = frozenset({"host_exec_argv_invalid", "host_exec_argv_too_large",
    "host_exec_argv_unsafe", "host_exec_stdin_too_large"})


def _limits():
    return {"argv_kib": host_exec.SERVER_ARGV_MAX_BYTES // 1024, "argv_items": host_exec.SERVER_ARGV_MAX_ITEMS,
            "stdin_mib": host_exec.SERVER_STDIN_MAX_BYTES >> 20,
            "intake": host_exec.SERVER_STDIN_INTAKE_SECONDS}


def _fail(args, code, request_id, *, acceptance, receipt=None, exit_code=1):
    limits = _limits()
    messages = {
        "server_cli_exec_unavailable": "Server exec is unavailable; no controller fallback was attempted.",
        "server_cli_exec_refused": "The server refused before accepting a job; nothing ran. Its runtime "
            "predates named refusals, so the cause is not reported.",
        "host_exec_argv_invalid": "The command after -- is not a valid argv; nothing was submitted.",
        "host_exec_argv_too_large": "The command after -- is over {argv_kib} KiB or {argv_items} arguments, "
            "or too long once quoted for the remote shell; nothing was submitted.".format(**limits),
        "host_exec_argv_unsafe": "The command after -- carries a secret-shaped value; nothing was submitted.",
        "host_exec_stdin_too_large": "stdin is over {stdin_mib} MiB; nothing was submitted.".format(**limits),
        "host_exec_stdin_timeout": "stdin did not reach end of input within {intake} seconds; "
            "nothing was submitted.".format(**limits),
        "host_exec_stdin_unavailable": "stdin could not be read or held; nothing was submitted.",
        "host_exec_stdin_mismatch": "The server received different stdin than was sent; nothing was accepted.",
        "host_exec_unknown_service": "The target has no running first container for --service; "
            "nothing was submitted.",
        "server_cli_exec_request_invalid": "The server refused the exec request as invalid; nothing was accepted.",
        "server_cli_exec_capability_unavailable": "The server's installed runtime does not offer this exec "
            "contract; nothing was submitted and no controller fallback was attempted.",
        "server_cli_exec_identity_changed": "The server's installed owner changed during the request; "
            "nothing was accepted.",
        "server_cli_exec_deadline_expired": "The --timeout ran out before the server accepted the job; "
            "nothing ran.",
        "server_cli_exec_target_unavailable": "The server could not read this target's records; "
            "nothing was accepted.",
        "server_cli_exec_target_changed": "The target's server records kept changing while the request "
            "was sent, so the server accepted nothing; nothing ran.",
        "server_cli_exec_acceptance_unknown": "Inspect the original request before any replay; acceptance is unknown.",
        "server_cli_exec_observation_unavailable": "The original server job is retained; inspect it before any replay.",
        "host_exec_usage": "Host exec requires a service, one target or name, and a command after --.",
        "host_server_target_evidence_unavailable": "The server's records for this target are missing "
            "or stale, such as no lifetime record; no controller fallback was attempted.",
        "host_target_busy": "The server target is busy or its last apply did not succeed; "
            "no controller fallback was attempted.",
        "host_target_invalid": "The server refused the target selector; no controller fallback was attempted.",
        "host_target_unverified": "The target's last apply ran but its delivery was not verified; "
            "no controller fallback was attempted.",
    }
    hints = {
        "host_server_target_evidence_unavailable":
            "an unclassified target needs sb host classify --remote NAME --target PROJECT/ENVIRONMENT "
            "--lifetime <hosted|temporary> --confirm; a classified target needs a succeeded apply "
            "of its current commit; then retry",
        "host_target_busy": "the target is being applied or reaped, or its last apply did not succeed; "
            "check sb host status before retrying",
        "host_target_invalid": "check --target, --name and --environment",
        "host_target_unverified": "check sb host status; if no runtime revision is declared, set "
            "deploy.derived_environment APP_SOURCE_REVISION: pushed_commit_sha in the hosting manifest "
            "(a stack's template), forward it on every service, then re-apply",
        "server_cli_exec_observation_unavailable":
            "inspect it with sb job-status JOB_ID --remote NAME; do not replay it",
        "host_exec_argv_too_large": "pass large input on stdin (up to {stdin_mib} MiB), e.g. "
            "sb host exec ... -- sh -c 'cat > /tmp/in' < FILE".format(**limits),
        "host_exec_argv_unsafe": "pass secret values on stdin, never in the command line",
        "host_exec_stdin_too_large": "split the input, or copy it in parts on stdin",
        "host_exec_unknown_service": "check --service against sb host status --remote NAME",
        "server_cli_exec_capability_unavailable": "check sb remote service status NAME; the server "
            "runtime may need sb remote service migrate",
        "server_cli_exec_deadline_expired": "raise --timeout, then retry",
        "server_cli_exec_target_changed": "retry; if it repeats, check sb host status for an apply, "
            "TTL or share activity on this target",
        "server_cli_exec_refused": "retry; if it repeats, check sb host status and sb remote service "
            "status NAME",
    }
    if getattr(args, "name", None):
        # Stacks get their lifetime record from sb host stack up, never classify.
        hints["host_server_target_evidence_unavailable"] = (
            "a stack needs a succeeded sb host stack up of its current commit on this server; "
            "check sb host stack list --remote NAME, then retry")
    payload = {"ok": False, "code": code, "acceptance": acceptance,
               "error": messages[code], **_handle(receipt, request_id)}
    if code in hints:
        payload["hint"] = hints[code]
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{code}: {payload['error']} " + " ".join(
            f"{key}={value}" for key, value in _handle(receipt, request_id).items()), file=sys.stderr)
        if "hint" in payload:
            print(f"hint: {payload['hint']}", file=sys.stderr)
    raise SystemExit(exit_code)


@contextmanager
def _original_stdin():
    descriptor = host_exec.stdin_source()
    if descriptor != subprocess.DEVNULL:
        yield descriptor
        return
    # TTY/unavailable stdin retains host exec's empty-input behavior. The SDK
    # requires an actual descriptor because it hashes the complete input once.
    with open(os.devnull, "rb") as empty:
        yield empty.fileno()


def _render(args, result, receipt, request_id):
    # The SDK validated the inner target, service, underlying exit and capture.
    # Keep the existing JSON projection and add only the original job handle.
    payload = {key: result[key] for key in (
        "ok", "code", "exit_code", "remote", "target", "service", "duration_ms",
        "stdout", "stderr", "stdout_truncated", "stderr_truncated", "error", "audit") if key in result}
    byte_capture = "stdout_bytes" in result or "stderr_bytes" in result
    if byte_capture or result.get("output_encoding") == "artifact-bytes":
        if (result.get("output_encoding") != "artifact-bytes" or
                any(type(result.get(key)) is not bytes for key in ("stdout_bytes", "stderr_bytes"))):
            raise ValueError("original capture bytes are incomplete")
        byte_capture = True
        # JSON's public default stays the original UTF-8 replacement projection.
        # Exact bytes are a local SDK result, never another JSON/log encoding.
        payload["stdout"] = result["stdout_bytes"].decode("utf-8", errors="replace")
        payload["stderr"] = result["stderr_bytes"].decode("utf-8", errors="replace")
    elif result.get("ok") or "stdout" in result or "stderr" in result:
        # This caller requested artifact mode. A known terminal pre-effect
        # refusal may have no capture, but UTF-8 log output is not a substitute.
        raise ValueError("original artifact capture is unavailable")
    if getattr(args, "json", False):
        print(json.dumps({**payload, **_handle(receipt, request_id)}, sort_keys=True))
    else:
        if byte_capture:
            _write_bytes(sys.stdout, result["stdout_bytes"])
            _write_bytes(sys.stderr, result["stderr_bytes"])
        else:
            sys.stdout.write(payload.get("stdout", ""))
            sys.stderr.write(payload.get("stderr", ""))
        if not payload.get("ok") and "error" in payload:
            print(payload["error"], file=sys.stderr)
    if result["exit_code"]:
        raise SystemExit(result["exit_code"])


def _write_bytes(stream, content):
    if not content:
        return
    stream.flush()
    sink = stream.buffer
    view = memoryview(content)
    while view:
        count = sink.write(view)
        if type(count) is not int or not 0 < count <= len(view):
            raise OSError("capture output was not completely written")
        view = view[count:]
    sink.flush()


def dispatch_exec_if_selected(args) -> bool:
    """Run before controller config and preserve the original synchronous CLI."""
    from sandbox.application.server_cli import server_cli_context_if_selected, ServerCLIUnavailable
    request_id = getattr(args, "request_id", None) or "host-exec-" + secrets.token_hex(12)
    args._server_cli_request_id = request_id
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", request_id) is None:
        _fail(args, "host_exec_usage", None, acceptance="not_accepted", exit_code=2)
    try:
        context = server_cli_context_if_selected()
        if context is not None:
            if args.remote != context.origin_remote_name:
                return False
            # This receiver is already the genuine retained child. Dispatch
            # directly before configuration/migration can enter any writers.
            from sandbox.commands.host_exec import cmd_host_exec
            try:
                cmd_host_exec(args)
            except Exception:
                _fail(args, "server_cli_exec_observation_unavailable", context.request_id,
                      acceptance="accepted", receipt={"job_id": context.job_id})
            return True
        from sandbox.core import _remote
        record = _remote.get_remote(args.remote)
        if not isinstance(record, dict) or not record.get("joined") or record.get("control_transport") != "tailscale":
            return False
        if getattr(args, "credential_scope", None) is not None or any(
                getattr(args, key, None) is not None for key in (
                    "credential_project", "credential_source", "credential_source_revision",
                    "credential_profile", "credential_profile_revision")):
            _fail(args, "server_cli_exec_unavailable", request_id, acceptance="not_accepted")
        timeout = host_exec.parse_timeout(getattr(args, "timeout", None))
        command = getattr(args, "exec_argv", None)
        service = getattr(args, "service", None)
        target, name = getattr(args, "target", None), getattr(args, "name", None)
        if (not command or not service or bool(target) == bool(name)
                or getattr(args, "tailnet_hostname", None) is not None
                or getattr(args, "image_action", None) is not None):
            _fail(args, "host_exec_usage", request_id, acceptance="not_accepted", exit_code=2)
        # The documented argv bound is checked before any metadata read.
        from sandbox.application.server_cli_exec import _exec_argv
        _exec_argv(command)
        from sandbox.application.server_cli_targets import observe_server_cli_target
        snapshot = observe_server_cli_target(args.remote, target=target, name=name,
            environment=getattr(args, "environment", None))
    except host_exec.ExecRefusal:
        _fail(args, "host_exec_usage", request_id, acceptance="not_accepted", exit_code=2)
    except Exception as error:
        from sandbox.application.server_cli_targets import ServerTargetRefused, TARGET_REFUSAL_CODES
        from sandbox.application.server_cli_exec import ServerCLIExecRefused
        if type(error) is ServerTargetRefused and error.code in TARGET_REFUSAL_CODES:
            _fail(args, error.code, request_id, acceptance="not_accepted")
        if type(error) is ServerCLIExecRefused:
            _fail(args, error.code, request_id, acceptance="not_accepted",
                  exit_code=2 if error.code in _REQUEST_REFUSALS else 1)
        _fail(args, "server_cli_exec_unavailable", request_id, acceptance="not_accepted")

    from sandbox.application.server_cli_exec import (
        submit_server_cli_exec, read_server_cli_exec_result, recover_server_cli_exec_acceptance,
        refusal_code, ServerCLIExecAcceptanceUnknown, ServerCLIExecObservationUnavailable)
    from sandbox.jobs.anonymous_input import AnonymousInputUnavailable
    receipt = None

    def reobserve(budget_seconds=45):
        # Only after the server's explicit "target changed" refusal (no job).
        return observe_server_cli_target(args.remote, target=target, name=name,
                                         environment=getattr(args, "environment", None),
                                         budget_seconds=budget_seconds)
    try:
        with _original_stdin() as descriptor:
            receipt = submit_server_cli_exec(args.remote, snapshot=snapshot, service=service,
                argv=command, stdin_fd=descriptor, request_id=request_id, timeout_seconds=timeout,
                output_mode="artifact", reobserve=reobserve)
    except (ServerCLIUnavailable, AnonymousInputUnavailable) as refused:
        code = refusal_code(refused)
        _fail(args, code, request_id, acceptance="not_accepted",
              exit_code=2 if code in _REQUEST_REFUSALS else 1)
    except ServerCLIExecAcceptanceUnknown as unknown:
        # The receipt was lost, which is not a refusal. Ask the server's ledger
        # once, read-only, for this exact request before reporting unknown.
        try:
            receipt = recover_server_cli_exec_acceptance(args.remote, unknown)
        except KeyboardInterrupt:
            _fail(args, "server_cli_exec_acceptance_unknown", request_id,
                  acceptance="unknown", exit_code=130)
        if receipt is None:
            _fail(args, "server_cli_exec_acceptance_unknown", request_id, acceptance="unknown")
    except KeyboardInterrupt:
        _fail(args, "server_cli_exec_acceptance_unknown", request_id,
              acceptance="unknown", exit_code=130)
    except Exception:
        # An unexpected exception after entering the submission port cannot
        # prove that its one remote attempt was never accepted.
        if receipt is not None:
            _fail(args, "server_cli_exec_observation_unavailable", request_id,
                  acceptance="accepted", receipt=receipt)
        _fail(args, "server_cli_exec_acceptance_unknown", request_id, acceptance="unknown")

    try:
        original_deadline = datetime.fromisoformat(receipt["execution_policy"]["provenance"][
            "server_cli.absolute_deadline_at"].replace("Z", "+00:00"))
        remaining = (original_deadline - datetime.now(timezone.utc)).total_seconds()
        stop = time.monotonic() + max(0, remaining)
        while True:
            # At least one original-job observation is safe after the effect
            # deadline. It may establish the terminal result without rerunning.
            # Result observation has several authenticated metadata/artifact
            # requests. Keep its finite read budget separate from the original
            # effect deadline; a late terminal read must not rerun execution.
            observed = read_server_cli_exec_result(args.remote, receipt,
                budget_seconds=60)
            if observed["result"] is not None:
                _render(args, observed["result"], receipt, request_id)
                return True
            remaining = stop - time.monotonic()
            if remaining <= 0:
                raise ServerCLIExecObservationUnavailable(receipt)
            time.sleep(min(1, remaining))
    except (Exception, KeyboardInterrupt) as error:
        _fail(args, "server_cli_exec_observation_unavailable", request_id,
              acceptance="accepted", receipt=receipt,
              exit_code=130 if isinstance(error, KeyboardInterrupt) else 1)
