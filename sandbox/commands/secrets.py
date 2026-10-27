"""CLI adapters for least-disclosure secret operations."""
from __future__ import annotations

from contextlib import contextmanager
import getpass
import io
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.secrets.ownership.manifest import OwnershipAdapter

from sandbox.registry import CommandSpec, register_specs
from sandbox.services.redaction import redact_structure
from sandbox.secrets import SecretBrokerError
from sandbox.secrets.context import build_secret_service
from sandbox.secrets.models import (
    DEFAULT_SESSION_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_SESSION_SECONDS,
    MAX_VALUE_BYTES,
)


ACTIONS = ("source-info", "inspect", "validate", "use-profile", "credential-set",
           "run", "set", "organize", "reveal", "migrate-zshrc", "ownership")
_DISPLAY_ENTRY_FIELDS = (
    "key", "state", "kind", "length_bucket", "exact_length",
    "public_prefix", "last4", "masked", "disclosed_material",
)
_DISPLAY_SOURCE_FIELDS = (
    "source", "scope", "format", "exists", "file_type", "content_state",
    "size_bucket", "size_bytes", "broker_readable", "safety",
    "source_type", "configured_modes", "provider_authorization_scope",
    "sandbox_selection_policy", "credential_backend", "credential_state",
    "reachability", "provider_state", "provider_error_code",
)


def configure_parser(parser) -> None:
    parser.description = "Inspect, use, or update registered secrets with least disclosure"
    actions = parser.add_subparsers(dest="action", required=True)
    source_info = actions.add_parser(
        "source-info", help="inspect registered file metadata without reading its contents",
    )
    source_info.add_argument("--source", required=True)
    source_info.add_argument("--exact-size", action="store_true")
    source_info.add_argument("--json", action="store_true")
    source_info.add_argument("--project-dir", default=".")

    inspect = actions.add_parser("inspect", help="list keys (default) or inspect bounded metadata")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--key", action="append", dest="keys")
    inspect.add_argument("--mode", choices=("keys", "metadata", "masked"), default="keys")
    inspect.add_argument("--exact-length", action="store_true")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--project-dir", default=".")

    validate = actions.add_parser("validate", help="run a reviewed offline shape profile")
    validate.add_argument("--source", required=True)
    validate.add_argument("--key", required=True)
    validate.add_argument("--profile", required=True)
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--project-dir", default=".")

    use_profile = actions.add_parser(
        "use-profile", help="run one registered fixed secret-use profile",
    )
    use_profile.add_argument("--profile", required=True)
    use_profile.add_argument("--json", action="store_true")
    use_profile.add_argument("--project-dir", default=".")

    credential = actions.add_parser(
        "credential-set", help="place a provider credential in the local OS credential store",
    )
    credential.add_argument("--source", required=True)
    credential_input = credential.add_mutually_exclusive_group()
    credential_input.add_argument("--stdin", action="store_true",
                                  help="read one protected line from stdin")
    credential_intent = credential.add_mutually_exclusive_group(required=True)
    credential_intent.add_argument("--create-only", action="store_true",
                                   help="create the local credential only if absent")
    credential_intent.add_argument("--replace-only", action="store_true",
                                   help="replace the local credential only if present")
    credential.add_argument("--json", action="store_true")
    credential.add_argument("--project-dir", default=".")

    run = actions.add_parser(
        "run", help="inject one or more secrets into one direct-argv child",
        description=(
            "Inject one or more secrets into one direct-argv child. Default: a bounded run "
            "(1-1800 seconds) whose redacted output is shown when it ends. --session is "
            "operator-only: a long-running child in your own foreground terminal, never "
            "for agents."
        ),
    )
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source")
    selection.add_argument("--ownership-profile")
    run.add_argument("--ownership-key", action="append", dest="ownership_keys")
    run.add_argument("--json", action="store_true")
    run.add_argument("--key", help="one secret key (use --secret for paired bindings)")
    run.add_argument("--secret", action="append", dest="secrets", metavar="KEY=DEST",
                     help="bind one key to one child environment name; repeatable")
    run.add_argument("--destination", default=None,
                     help="destination environment variable name (defaults to KEY)")
    run.add_argument("--timeout-seconds", type=int, default=None,
                     help="maximum child lifetime (1-1800 seconds; default 300); "
                          "not accepted with --session")
    run.add_argument("--session", action="store_true",
                     help="operator-only session: keep one child running in this foreground "
                          "terminal with live redacted output until the lifetime ends, "
                          "Ctrl-C, the terminal closes, or the child exits. Requires an "
                          "interactive foreground terminal; agents must use a bounded run "
                          "instead")
    run.add_argument("--lifetime-seconds", default=None, metavar="N",
                     help="session lifetime in whole seconds, 1-43200 (default 28800 = 8h); "
                          "only with --session")
    run.add_argument("--project-dir", default=".")
    run.add_argument("command", nargs="+")

    setting = actions.add_parser("set", help="create or replace one assignment without displaying it")
    setting.add_argument("--source", required=True)
    setting.add_argument("key")
    inputs = setting.add_mutually_exclusive_group()
    inputs.add_argument("--stdin", action="store_true")
    inputs.add_argument("--from-ref")
    inputs.add_argument("--generate")
    intent = setting.add_mutually_exclusive_group()
    intent.add_argument("--create-only", action="store_true")
    intent.add_argument("--replace-only", action="store_true")
    setting.add_argument("--if-revision")
    setting.add_argument("--profile")
    setting.add_argument("--json", action="store_true")
    setting.add_argument("--project-dir", default=".")

    organize = actions.add_parser(
        "organize", help="group one dotenv source into documented sections without reading values",
    )
    organize.add_argument("--source", default="personal")
    organize.add_argument("--apply", action="store_true", help="write the grouped file (default: report only)")
    organize.add_argument("--if-revision")
    organize.add_argument("--json", action="store_true")
    organize.add_argument("--project-dir", default=".")

    reveal = actions.add_parser("reveal", help="human-only display of exactly one value on the controlling TTY")
    reveal.add_argument("--source", required=True)
    reveal.add_argument("--key", required=True)
    reveal.add_argument("--project-dir", default=".")

    migrate = actions.add_parser("migrate-zshrc", help="migrate legacy personal exports")
    migrate.add_argument("--json", action="store_true")

    ownership = actions.add_parser("ownership", help="explicit secret ownership profiles")
    ownership_actions = ownership.add_subparsers(dest="ownership_action", required=True)
    for action in ("classify", "credential-set", "evidence-check", "approve", "migrate",
                   "status", "cleanup", "retention"):
        child = ownership_actions.add_parser(action)
        child.add_argument("--profile", required=True)
        child.add_argument("--project-dir", default=".")
        child.add_argument("--json", action="store_true")
        if action == "classify":
            child.add_argument("--check-destination", action="store_true")
        elif action == "credential-set":
            child.add_argument("--capability", choices=("read", "create"), required=True)
            child.add_argument("--stdin", action="store_true")
            intent = child.add_mutually_exclusive_group(required=True)
            intent.add_argument("--create-only", action="store_true")
            intent.add_argument("--replace-only", action="store_true")
        elif action == "evidence-check":
            child.add_argument("--evidence", required=True)
        elif action == "approve":
            child.add_argument("--operation", choices=("migrate", "cleanup"), required=True)
            child.add_argument("--evidence")
            child.add_argument("--minutes", type=int, default=15)
        elif action in ("migrate", "cleanup"):
            child.add_argument("--approval")
            child.add_argument("--confirm", action="store_true")
            if action == "migrate":
                child.add_argument("--timeout-seconds", type=int, default=300)
        elif action == "retention":
            child.add_argument("--confirm", action="store_true")


def _service(project_dir: str):
    from sandbox.application.context import load_project_descriptor
    from sandbox.core._paths import BASE
    from sandbox.core._secrets import secret_file
    root = Path(project_dir).expanduser().resolve()
    return build_secret_service(
        project_root=root, config=load_project_descriptor(root), personal_path=secret_file(),
        runtime_root=BASE / "runtime",
    )


def _emit(payload: dict, as_json: bool) -> None:
    payload = redact_structure(payload)
    if not isinstance(payload, dict):
        payload = {"ok": False, "operation": "secrets", "error": "redaction_failed"}
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    operation = payload.get("operation", "secrets")
    print(f"secrets {operation}: {'ok' if payload.get('ok') else 'failed'}")
    if "keys" in payload:
        if operation == "run":
            # Label the selection so a bare key name is never mistaken for
            # the first line of the child's own output.
            print(f"  keys={','.join(payload['keys'])}")
        else:
            for key in payload["keys"]:
                print(key)
    for entry in payload.get("entries", ()):
        fields = ", ".join(
            f"{name}={entry[name]}" for name in _DISPLAY_ENTRY_FIELDS if name in entry
        )
        print(f"  {fields}")
    if operation == "organize":
        state = "applied" if payload.get("applied") else (
            "rewrite pending" if payload.get("changed") else "already organized")
        print(f"  {payload.get('count', 0)} keys, {state}")
        for group in payload.get("groups", ()):
            print(f"  {group['title']}: {len(group['keys'])}")
            print(f"    {', '.join(group['keys'])}")
    if payload.get("validation"):
        print(json.dumps(payload["validation"], sort_keys=True))
    if payload.get("action"):
        print(f"  {payload['action']}: {payload.get('key', '')}")
    if operation == "source_info":
        fields = ", ".join(
            f"{name}={payload[name]}" for name in _DISPLAY_SOURCE_FIELDS if name in payload
        )
        print(f"  {fields}")
    result = payload.get("result") or {}
    if result.get("output"):
        sys.stdout.write(result["output"])
    if result:
        print(f"  termination={result.get('termination')} exit_code={result.get('exit_code')} "
              f"truncated={result.get('truncated')}")


def _stdin_secret() -> str:
    candidate = sys.stdin.buffer.read(MAX_VALUE_BYTES + 2)
    if len(candidate) > MAX_VALUE_BYTES + 1:
        raise SecretBrokerError("input_invalid", "secret stdin exceeds the supported limit")
    if candidate.endswith(b"\r\n"):
        candidate = candidate[:-2]
    elif candidate.endswith(b"\n"):
        candidate = candidate[:-1]
    if b"\n" in candidate or b"\r" in candidate or b"\x00" in candidate:
        raise SecretBrokerError("input_invalid", "secret stdin must contain one non-empty line")
    try:
        value = candidate.decode("utf-8")
    except UnicodeDecodeError:
        # Raise after the decoder exception leaves scope: it retains the input
        # bytes and must never become a public exception context.
        value = None
    if value is None:
        raise SecretBrokerError("input_invalid", "secret stdin must be UTF-8 text")
    if not value:
        raise SecretBrokerError("input_invalid", "secret stdin must not be empty")
    return value


@contextmanager
def _controlling_tty():
    """Open /dev/tty without requiring the terminal to support seeking."""
    descriptor = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
    raw = None
    stream = None
    try:
        if not os.isatty(descriptor):
            raise OSError("controlling device is not a terminal")
        raw = io.FileIO(descriptor, "w+")
        descriptor = -1
        stream = io.TextIOWrapper(raw, encoding="utf-8")
        raw = None
        yield stream
    finally:
        if stream is not None:
            stream.close()
        elif raw is not None:
            raw.close()
        if descriptor >= 0:
            os.close(descriptor)


def _tty_secret(prompt: str) -> str:
    try:
        with _controlling_tty() as tty:
            value = getpass.getpass(prompt, stream=tty)
    except OSError as exc:
        raise SecretBrokerError("tty_required", "a controlling TTY is required") from exc
    if not value:
        raise SecretBrokerError("input_invalid", "secret input must not be empty")
    return value


def _session_lifetime(raw) -> int:
    if raw is None:
        return DEFAULT_SESSION_SECONDS
    lifetime = None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        lifetime = int(raw)
    if lifetime is None or not 1 <= lifetime <= MAX_SESSION_SECONDS:
        raise SecretBrokerError(
            "lifetime_invalid", "session lifetime must be a whole number from 1 to 43200 seconds",
        )
    return lifetime


def _require_foreground_terminal() -> None:
    """Session mode only runs as the foreground job of a live terminal.

    This keeps CI, MCP, durable jobs and captured remote paths out, and makes
    Ctrl-C and terminal hangup real bounds on the session.
    """
    ready = False
    try:
        if sys.stdout.isatty():
            # A raw descriptor: text-mode "r+" on /dev/tty is refused as
            # unseekable by current Python versions.
            descriptor = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
            try:
                ready = os.isatty(descriptor) and os.tcgetpgrp(descriptor) == os.getpgrp()
            finally:
                os.close(descriptor)
    except (OSError, ValueError, AttributeError):
        ready = False
    if not ready:
        raise SecretBrokerError(
            "tty_required",
            "session mode needs an interactive terminal with sb as its foreground job",
        )


def _session_preflight(args):
    """Return the session lifetime, or None for an ordinary run.

    Runs before the service is built so every refusal precedes any read."""
    session = bool(getattr(args, "session", False))
    raw = getattr(args, "lifetime_seconds", None)
    if not session and raw is None:
        return None
    lifetime = _session_lifetime(raw)
    if not session:
        raise SecretBrokerError("option_conflict", "--lifetime-seconds requires --session")
    if getattr(args, "timeout_seconds", None) is not None:
        raise SecretBrokerError(
            "option_conflict", "--timeout-seconds cannot be combined with --session",
        )
    _require_foreground_terminal()
    return lifetime


def _run_bindings(args) -> list[tuple[str, str]]:
    if args.secrets:
        if args.key or (args.destination is not None and args.destination != "SANDBOX_SECRET"):
            raise SecretBrokerError(
                "selection_invalid",
                "use either --key/--destination or repeat --secret KEY=DEST",
            )
        bindings = []
        for raw in args.secrets:
            if not isinstance(raw, str) or raw.count("=") != 1:
                raise SecretBrokerError(
                    "selection_invalid", "each --secret must be KEY=DEST",
                )
            key, destination = raw.split("=", 1)
            bindings.append((key, destination))
        return bindings
    if not args.key:
        raise SecretBrokerError(
            "selection_invalid", "--key is required unless --secret is provided",
        )
    destination = args.destination if args.destination is not None else args.key
    return [(args.key, destination)]


def _child_failed(exit_code) -> None:
    """Shared child-exit mapping for ordinary run and session mode."""
    if isinstance(exit_code, int) and exit_code != 0:
        from sandbox.core import die
        # Preserve the trusted child's failure without rendering its
        # command, environment, or raw output in the error message.
        # Flush first so the error follows the child's output instead of
        # jumping ahead of it when stdout is a pipe.
        sys.stdout.flush()
        die(f"child_failed: the command exited with status {exit_code}; "
            "secret delivery succeeded",
            code=exit_code if 1 <= exit_code <= 125 else 1)


def _duration(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3_600)
    minutes, rest = divmod(remainder, 60)
    parts = [f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (rest, "s")) if value]
    return "".join(parts) or "0s"


def _session_write(text: str) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode())
    sys.stdout.buffer.flush()


def _session_display(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


_SESSION_EXIT = {"lifetime_expired": 0, "interrupted": 130, "hangup": 129}


def _detach_lost_terminal() -> None:
    """Point stdout and stderr at /dev/null once the terminal is gone.

    Otherwise the interpreter's final flush hits EIO and Python replaces the
    documented exit status (129 for hangup) with 120."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            descriptor = stream.fileno()
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, descriptor)
            finally:
                os.close(null)
        except (OSError, ValueError, AttributeError):
            pass


def _run_session(service, args, bindings, argv, lifetime: int) -> None:
    from datetime import datetime
    from sandbox.secrets.session import SessionSignals

    def started(wall_deadline: float) -> None:
        fields = redact_structure({
            "source": args.source,
            "keys": ",".join(key for key, _destination in bindings),
            "lifetime": f"{lifetime}s ({_duration(lifetime)})",
            "ends_at": datetime.fromtimestamp(wall_deadline).astimezone().isoformat(
                timespec="seconds"),
        })
        if not isinstance(fields, dict):
            fields = {}
        _session_write(
            f"secrets session: started source={fields.get('source')} keys={fields.get('keys')} "
            f"lifetime={fields.get('lifetime')} ends_at={fields.get('ends_at')}\n"
        )

    # Own interrupt and hangup before the service runs, so a Ctrl-C during the
    # source read still ends as an audited ``interrupted`` session.
    with SessionSignals() as signals:
        payload = service.run_session(
            args.source, bindings, argv, lifetime_seconds=lifetime, signals=signals,
            display=_session_display, on_start=started,
        )
    payload = redact_structure(payload)
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise SecretBrokerError("operation_failed", "secret operation failed")
    end_reason = result.get("end_reason")
    terminal_gone = end_reason == "hangup"
    try:
        _session_write(
            f"secrets session: ended end_reason={end_reason} exit_code={result.get('exit_code')} "
            f"elapsed={result.get('elapsed_class')} dropped_chunks={result.get('dropped_chunks')}\n"
        )
        if result.get("group_ended") is False:
            _session_write(
                "secrets session: warning: a process in the child's group could not be "
                "ended within the bound (did the child escalate privileges?); it may still "
                "hold the secret\n"
            )
    except OSError:
        terminal_gone = True  # the audit outcome already holds the reason
    if terminal_gone:
        _detach_lost_terminal()
    if end_reason == "child_exited":
        _child_failed(result.get("exit_code"))
        return
    code = _SESSION_EXIT.get(end_reason, 1)
    if code:
        sys.exit(code)


def _migrate(args) -> None:
    from sandbox.core import _cloudflare, _secrets
    from sandbox.core import die, ok
    try:
        moved = _secrets.migrate_zshrc()
        cloudflare_migrated = _cloudflare.migrate_legacy_token()
    except _secrets.SecretError as exc:
        from sandbox.services.redaction import redact_text
        die(redact_text(str(exc)))
    result = {"ok": True, "file": str(_secrets.secret_file()), "migrated": sorted(moved),
              "cloudflare_migrated": cloudflare_migrated}
    if args.json:
        print(json.dumps(result))
    elif moved:
        ok(f"migrated {len(moved)} secret exports to {result['file']}")
    else:
        ok(f"personal secret file already configured: {result['file']}")


def _ownership_emit(body: dict, as_json: bool) -> int:
    body = dict(body)
    code = body.pop("_exit", 0)
    # Ownership payloads have only whitelisted metadata. Do not pass NAME keys
    # through generic value redaction, which would erase credential-like names.
    if as_json:
        print(json.dumps(body, sort_keys=True))
    else:
        print(f"secrets ownership: {body.get('code')} profile={body.get('profile')}")
        for state, names in sorted(body.get("names", {}).items()):
            print(f"  {state}: {', '.join(names)}")
        for key in ("approval_id", "run_id", "revocation", "storage_attestation", "next_action",
                    "audit", "completion_record", "persistence"):
            if body.get(key) is not None:
                print(f"  {key}: {body[key]}")
        if "journals" in body:
            print(json.dumps({"journals": body["journals"], "runs": body["runs"]}, sort_keys=True))
        if body.get("last_run"):
            print(json.dumps({"last_run": body["last_run"]}, sort_keys=True))
    return int(code)


def _ownership_evidence(ctx, profile: str, filename: str):
    """Admit a private metadata document, copy exactly those bytes, re-admit."""
    import hashlib
    import time
    from sandbox.secrets.ownership.evidence import admit_evidence
    from sandbox.secrets.ownership.journal import read_private_file, write_private_file
    from sandbox.secrets.ownership.migrate import load_admission
    path = Path(filename)
    admission = admit_evidence(path, ctx.config.profiles[profile].destination, now=time.time())
    raw = read_private_file(path, limit=1_048_576)
    if hashlib.sha256(raw).hexdigest() != admission.evidence_sha256:
        raise SecretBrokerError("evidence_inadmissible", "evidence changed while being admitted")
    target = ctx.runtime_root / "evidence" / f"{admission.evidence_sha256}.json"
    try:
        write_private_file(target, raw)
    except FileExistsError:
        if read_private_file(target) != raw:
            raise SecretBrokerError("evidence_inadmissible", "stored evidence does not match") from None
    return load_admission(ctx, profile, admission.evidence_sha256)


def _ownership_command(ctx, args, adapter):
    import time
    from sandbox.secrets.ownership.approval import ApprovalStore, require_human_terminal
    from sandbox.secrets.ownership.journal import destination_sha256, read_journal_states, retention
    from sandbox.secrets.ownership.migrate import (
        authority_for, build_grant, classify_profile, last_run_record, payload,
        profile_entries, report_payload, resolve_adapter, revocation_outcome, revocation_status, run_migration,
    )
    profile, action = args.profile, args.ownership_action
    entries = profile_entries(ctx, profile)
    if action == "classify":
        report, _, source_names = classify_profile(ctx, profile)
        if report.blocking or not args.check_destination:
            return report_payload(report, "classification_blocked" if report.blocking else "classified",
                                  ok=not report.blocking)
        from sandbox.secrets.ownership.classify import classify
        grant = build_grant(ctx, profile, "read", tuple(sorted(e.name for e in entries)))
        session = resolve_adapter(adapter, capability="read").open_session(
            grant, deadline=time.time() + grant.destination.timeout_seconds)
        exists_exc = None
        try:
            with session:
                existence = session.exists(grant.names)
        except SecretBrokerError as exc:
            exists_exc = exc
        outcome = revocation_outcome(session)
        status = outcome.status
        if exists_exc is not None:
            if status == "cleanup_unknown":
                exists_exc.revocation = "cleanup_unknown"
                exists_exc.revocation_outcome = outcome
            raise exists_exc
        report = classify(ctx.config, profile, source_names, existence)
        unknown = bool(report.states.get("unknown"))
        code = "existence_unknown" if unknown else "cleanup_unknown" if status == "cleanup_unknown" else "classified"
        next_action = None
        if status == "cleanup_unknown":
            exp_str = f"expires at {int(outcome.token_expires_at)}" if (outcome.token_expires_at is not None and not isinstance(outcome.token_expires_at, bool) and isinstance(outcome.token_expires_at, (int, float)) and math.isfinite(outcome.token_expires_at)) else "expiry unknown"
            token_action = f"derived access token revocation not confirmed; account for its remaining lifetime ({exp_str})"
            operator_action = "native bootstrap entries and Cloudflare credentials remain operator-managed"
            next_action = f"{token_action}; {operator_action}"
        elif unknown:
            operator_action = "native bootstrap entries and Cloudflare credentials remain operator-managed"
            next_action = operator_action
        return report_payload(report, code, ok=code == "classified", revocation=status, next_action=next_action)
    if action == "credential-set":
        from sandbox.secrets.credentials.base import locator_for_ownership
        from sandbox.secrets.credentials.manifest import create_credential_store
        from sandbox.secrets.ownership.broker import validate_bootstrap_input
        grant = build_grant(ctx, profile, args.capability, tuple(sorted(e.name for e in entries)))
        locator_for_ownership(grant)  # Pure authority validation before protected input/store access.
        value = _stdin_secret() if args.stdin else _tty_secret("Ownership bootstrap credential: ")
        raw = bytearray(value.encode("utf-8"))
        try:
            validate_bootstrap_input(raw, grant.destination.gateway)
            store = ctx.credential_store
            if store is None:
                store = create_credential_store(bootstrap_dependencies=False)
            # Store abstraction accepts the validated ownership grant. It must
            # route it through locator_for_ownership, never a source locator.
            store.put(grant, value, intent="create" if args.create_only else "replace")
        finally:
            raw[:] = b"\0" * len(raw)
            value = ""
        return payload("credential_stored", profile, ok=True)
    if action == "evidence-check":
        _ownership_evidence(ctx, profile, args.evidence)
        return payload("evidence_admitted", profile, ok=True, storage_attestation="operator_attested")
    if action == "approve":
        require_human_terminal(sys.stdin, sys.stdout)
        admission = _ownership_evidence(ctx, profile, args.evidence) if args.evidence else None
        approval = ApprovalStore(ctx.runtime_root).grant_interactive(
            operation=args.operation, profile=profile, config=ctx.config, registry=ctx.registry,
            destination_sha256=destination_sha256(ctx.config.profiles[profile].destination),
            authority_digest=authority_for(ctx, profile, "create" if args.operation == "migrate" else "read"),
            admission=admission, minutes=args.minutes, tty_in=sys.stdin, tty_out=sys.stdout)
        return payload("approved", profile, ok=True, approval_id=approval.approval_id,
                       storage_attestation="operator_attested" if admission else None)
    if action == "migrate":
        return run_migration(ctx, profile, args.approval, confirm=args.confirm,
                             timeout_seconds=args.timeout_seconds, adapter=adapter)
    if action == "cleanup":
        from sandbox.secrets.ownership.cleanup import run_cleanup
        return run_cleanup(ctx, profile, args.approval, confirm=args.confirm, adapter=adapter)
    if action == "retention":
        return retention(ctx.runtime_root, profile, confirm=args.confirm)
    if action == "status":
        states = read_journal_states(ctx.runtime_root, profile,
                                     destination_sha256(ctx.config.profiles[profile].destination))
        record = last_run_record(ctx.runtime_root, profile)
        # Rebuild the public result instead of echoing a stored JSON document.
        body = payload("status", profile, ok=True, states=states or {})
        if record:
            from sandbox.secrets.ownership.journal import is_qualified_id
            if is_qualified_id(record.get("run_id"), "run"):
                body["run_id"] = record["run_id"]
            if record.get("revocation") in ("revoked", "cleanup_unknown", "not_applicable"):
                body["revocation"] = record["revocation"]
            names = record.get("names")
            allowed_states = {"pending", "attempted", "created", "pre_existing", "conflict",
                              "reconciled", "acceptance_unknown", "verified", "failed",
                              "unresolved", "removed"}
            if (isinstance(names, dict) and set(names) <= allowed_states
                    and all(isinstance(items, list) and all(
                        isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
                        for name in items) for items in names.values())):
                body["last_run"] = {
                    "run_id": body.get("run_id"), "revocation": body.get("revocation"),
                    "names": names, "counts": {state: len(items) for state, items in names.items()},
                }
        return body
    raise SecretBrokerError("input_invalid", "ownership action is invalid")


def _ownership_run(ctx, args, adapter):
    from sandbox.secrets.models import MAX_OUTPUT_BYTES
    from sandbox.secrets.ownership.models import ScopedRunRequest
    from sandbox.secrets.ownership.scoped_run import resolve_scoped_bindings
    from sandbox.secrets.runner import run_with_secrets
    argv = list(args.command)
    if argv and argv[0] == "--":
        argv.pop(0)
    request = ScopedRunRequest(
        profile=args.ownership_profile, keys=tuple(args.ownership_keys or ()), argv=tuple(argv),
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS if args.timeout_seconds is None else args.timeout_seconds,
        max_output_bytes=MAX_OUTPUT_BYTES)
    bindings = resolve_scoped_bindings(ctx, request, adapter)
    try:
        result = run_with_secrets(request.argv, secrets=bindings,
                                  timeout_seconds=request.timeout_seconds,
                                  max_output_bytes=request.max_output_bytes)
    finally:
        bindings.clear()
    child_code = 124 if result.termination == "timed_out" else result.exit_code if result.termination == "exited" else 1
    status = child_code if isinstance(child_code, int) and 0 <= child_code <= 125 else 1
    return {"ok": status == 0, "code": "ran" if status == 0 else "child_failed",
            "profile": request.profile, "counts": {"selected": len(request.keys)},
            "names": {"selected": sorted(request.keys)}, "result": result.as_dict(), "_exit": status}


def _ownership_dispatch(args, *, ownership_adapter=None) -> int:
    from sandbox.secrets.audit import SecretAudit
    from sandbox.secrets.ownership.migrate import exit_for, payload
    profile = getattr(args, "ownership_profile", None) if args.action == "run" else args.profile
    correlation, audit, operation = None, None, None
    try:
        if args.action == "run":
            if (not profile or getattr(args, "session", False) or any(
                    getattr(args, key, None) is not None for key in
                    ("source", "key", "secrets", "destination", "lifetime_seconds"))):
                raise SecretBrokerError("option_conflict", "ownership selection cannot be combined with source or session selectors")
            if not args.ownership_keys:
                raise SecretBrokerError("ownership_keys_required", "explicit ownership keys are required")
        elif args.ownership_action == "approve":
            from sandbox.secrets.ownership.approval import require_human_terminal
            require_human_terminal(sys.stdin, sys.stdout)
        from sandbox.secrets.context import build_ownership_context
        ctx = build_ownership_context(args.project_dir)
        action = "run" if args.action == "run" else args.ownership_action
        if action in ("classify", "approve", "migrate", "cleanup", "run"):
            operation = f"ownership_{action}"
            audit = SecretAudit(ctx.runtime_root / "audit.jsonl")
            correlation = audit.intent(operation, "ownership", [], surface="cli", profile=profile)
        body = _ownership_run(ctx, args, ownership_adapter) if args.action == "run" else _ownership_command(ctx, args, ownership_adapter)
    except SecretBrokerError as exc:
        # Fixed public codes only; never render backend messages or exception payloads.
        body = payload(exc.code, profile, ok=False)
        if exc.code in ("approval_mismatch", "journal_foreign_controller") and isinstance(exc.remedy, str):
            if re.fullmatch(r"(?:run|resume) from controller [0-9a-f]{32}", exc.remedy):
                body["next_action"] = exc.remedy
        rev_outcome = getattr(exc, "revocation_outcome", None)
        rev_status = getattr(exc, "revocation", None)
        if rev_status == "cleanup_unknown" or exc.code == "cleanup_unknown":
            body["revocation"] = "cleanup_unknown"
            token_expires_at = getattr(rev_outcome, "token_expires_at", None)
            exp_str = f"expires at {int(token_expires_at)}" if (token_expires_at is not None and not isinstance(token_expires_at, bool) and isinstance(token_expires_at, (int, float)) and math.isfinite(token_expires_at)) else "expiry unknown"
            token_action = f"derived access token revocation not confirmed; account for its remaining lifetime ({exp_str})"
            operator_action = "native bootstrap entries and Cloudflare credentials remain operator-managed"
            if body.get("next_action"):
                body["next_action"] = f"{token_action}; {body['next_action']}; {operator_action}"
            else:
                body["next_action"] = f"{token_action}; {operator_action}"
    except Exception:
        body = payload("provider_failed", profile, ok=False)
    if correlation is not None:
        try:
            names = sorted({name for items in body.get("names", {}).values() for name in items})
            audit.outcome(correlation, operation, "ownership", names, surface="cli", profile=profile,
                          decision="allowed" if body.get("ok") else "denied",
                          reason_code=body.get("code"), count=len(names))
        except Exception:
            body["audit"] = "audit_unavailable"
            body["audit_error"] = "audit_unavailable"
            if body.get("ok"):
                body["ok"] = False
                body["code"] = "audit_unavailable"
                body["_exit"] = exit_for("audit_unavailable")
            else:
                body["_exit"] = body.get("_exit") or exit_for(body.get("code", "audit_unavailable"))
    if args.action == "run" and body.get("result"):
        if getattr(args, "json", False):
            print(json.dumps({key: value for key, value in body.items() if key != "_exit"}, sort_keys=True))
        else:
            sys.stdout.write(body["result"].get("output", ""))
        return body.get("_exit", 0)
    return _ownership_emit(body, getattr(args, "json", False))


def cmd_secrets(cfg, args, *, ownership_adapter: OwnershipAdapter | None = None) -> int:
    if args.action == "ownership" or (
            args.action == "run" and (getattr(args, "ownership_profile", None)
                                      or getattr(args, "ownership_keys", None))):
        return _ownership_dispatch(args, ownership_adapter=ownership_adapter)
    if args.action == "migrate-zshrc":
        _migrate(args)
        return 0
    try:
        session_lifetime = _session_preflight(args) if args.action == "run" else None
        service = _service(args.project_dir)
        if args.action == "source-info":
            _emit(service.source_info(args.source, exact_size=args.exact_size), args.json)
        elif args.action == "inspect":
            payload = service.inspect(args.source, keys=args.keys, mode=args.mode,
                                      exact_length=args.exact_length)
            _emit(payload, args.json)
        elif args.action == "validate":
            _emit(service.validate(args.source, args.key, args.profile), args.json)
        elif args.action == "use-profile":
            _emit(service.use_profile(args.profile, surface="cli"), args.json)
        elif args.action == "credential-set":
            intent = "create" if args.create_only else "replace"
            value = _stdin_secret() if args.stdin else _tty_secret(
                f"Provider credential for {args.source}: ",
            )
            _emit(service.set_provider_credential(
                args.source, value, intent=intent,
                input_channel="stdin" if args.stdin else "tty",
            ), args.json)
        elif args.action == "run":
            command = list(args.command)
            if command and command[0] == "--":
                command.pop(0)
            bindings = _run_bindings(args)
            if session_lifetime is not None:
                _run_session(service, args, bindings, command, session_lifetime)
                return 0
            timeout = args.timeout_seconds
            payload = service.run_many(
                args.source, bindings, command,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout,
            )
            _emit(payload, False)
            result = payload.get("result") or {}
            if result.get("termination") == "exited":
                _child_failed(result.get("exit_code"))
        elif args.action == "set":
            intent = "create" if args.create_only else "replace" if args.replace_only else "either"
            kwargs = dict(intent=intent, expected_revision=args.if_revision,
                          validation_profile=args.profile)
            if args.from_ref:
                if ":" not in args.from_ref:
                    raise SecretBrokerError("input_invalid", "reference must be SOURCE:KEY")
                ref_source, ref_key = args.from_ref.split(":", 1)
                payload = service.copy_reference(args.source, args.key, ref_source, ref_key, **kwargs)
            elif args.generate:
                payload = service.generate(args.source, args.key, args.generate, **kwargs)
            else:
                value = _stdin_secret() if args.stdin else _tty_secret(f"New value for {args.key}: ")
                payload = service.set(args.source, args.key, value,
                                      input_channel="stdin" if args.stdin else "tty", **kwargs)
            _emit(payload, args.json)
        elif args.action == "organize":
            _emit(service.organize(args.source, apply=args.apply,
                                   expected_revision=args.if_revision), args.json)
        elif args.action == "reveal":
            try:
                with _controlling_tty() as tty:
                    tty.write("WARNING: this reveals one secret to your terminal. Never paste it into chat or logs.\n")
                    tty.write(f"Retype {args.key} to continue: ")
                    tty.flush()
                    confirmed = tty.readline().rstrip("\r\n")
                    service.reveal(
                        args.source, args.key,
                        lambda value: (tty.write(value), tty.write("\n"), tty.flush()),
                        confirmed=confirmed == args.key,
                    )
            except OSError as exc:
                raise SecretBrokerError("tty_required", "a controlling TTY is required for reveal") from exc
    except SecretBrokerError as exc:
        if getattr(args, "json", False):
            payload = redact_structure({
                "ok": False,
                "operation": args.action.replace("-", "_"),
                "error": exc.as_dict(),
            })
            print(json.dumps(payload, sort_keys=True))
            raise SystemExit(1) from None
        from sandbox.core import die
        remedy = f"; remedy: {exc.remedy}" if exc.remedy else ""
        die(f"{exc.code}: {exc.message}{remedy}")
    except Exception:
        # Do not render, repr, or chain unknown failures. Parser and backend
        # exceptions may retain secret-bearing source buffers or stderr.
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": False,
                "operation": args.action.replace("-", "_"),
                "error": {
                    "code": "operation_failed",
                    "message": "secret operation failed",
                    "retryable": False,
                },
            }, sort_keys=True))
            raise SystemExit(1) from None
        from sandbox.core import die
        die("operation_failed: secret operation failed")
    return 0


def _secrets_entrypoint(cfg, args) -> None:
    status = cmd_secrets(cfg, args)
    if status:
        raise SystemExit(status)


register_specs((CommandSpec(
    name="secrets", handler=_secrets_entrypoint, configure=configure_parser,
    owner=__name__, scope="global", destructive=False,
),))
