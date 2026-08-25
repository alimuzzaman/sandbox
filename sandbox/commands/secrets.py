"""CLI adapters for least-disclosure secret operations."""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from sandbox.registry import CommandSpec, register_specs
from sandbox.services.redaction import redact_structure
from sandbox.secrets import SecretBrokerError
from sandbox.secrets.context import build_secret_service
from sandbox.secrets.models import MAX_VALUE_BYTES


ACTIONS = ("source-info", "inspect", "validate", "run", "set", "organize", "reveal", "migrate-zshrc")
_DISPLAY_ENTRY_FIELDS = (
    "key", "state", "kind", "length_bucket", "exact_length",
    "public_prefix", "last4", "masked", "disclosed_material",
)
_DISPLAY_SOURCE_FIELDS = (
    "source", "scope", "format", "exists", "file_type", "content_state",
    "size_bucket", "size_bytes", "broker_readable", "safety",
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

    run = actions.add_parser("run", help="inject one or more secrets into one direct-argv child")
    run.add_argument("--source", required=True)
    run.add_argument("--key", help="one secret key (use --secret for paired bindings)")
    run.add_argument("--secret", action="append", dest="secrets", metavar="KEY=DEST",
                     help="bind one key to one child environment name; repeatable")
    run.add_argument("--destination", default="SANDBOX_SECRET")
    run.add_argument("--timeout-seconds", type=int, default=300)
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


def _tty_secret(prompt: str) -> str:
    try:
        with open("/dev/tty", "r+") as tty:
            if not os.isatty(tty.fileno()):
                raise OSError
            value = getpass.getpass(prompt, stream=tty)
    except OSError as exc:
        raise SecretBrokerError("tty_required", "a controlling TTY is required") from exc
    if not value:
        raise SecretBrokerError("input_invalid", "secret input must not be empty")
    return value


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


def cmd_secrets(cfg, args) -> None:
    if args.action == "migrate-zshrc":
        _migrate(args)
        return
    try:
        service = _service(args.project_dir)
        if args.action == "source-info":
            _emit(service.source_info(args.source, exact_size=args.exact_size), args.json)
        elif args.action == "inspect":
            payload = service.inspect(args.source, keys=args.keys, mode=args.mode,
                                      exact_length=args.exact_length)
            _emit(payload, args.json)
        elif args.action == "validate":
            _emit(service.validate(args.source, args.key, args.profile), args.json)
        elif args.action == "run":
            command = list(args.command)
            if command and command[0] == "--":
                command.pop(0)
            if args.secrets:
                if args.key or args.destination != "SANDBOX_SECRET":
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
            else:
                if not args.key:
                    raise SecretBrokerError(
                        "selection_invalid", "--key is required unless --secret is provided",
                    )
                bindings = [(args.key, args.destination)]
            payload = service.run_many(
                args.source, bindings, command,
                timeout_seconds=args.timeout_seconds,
            )
            _emit(payload, False)
            result = payload.get("result") or {}
            exit_code = result.get("exit_code")
            termination = result.get("termination")
            if termination == "exited" and isinstance(exit_code, int) and exit_code != 0:
                from sandbox.core import die
                # Preserve the trusted child's failure without rendering its
                # command, environment, or raw output in the error message.
                die("child_failed: secret use command failed", code=exit_code if 1 <= exit_code <= 125 else 1)
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
                with open("/dev/tty", "r+") as tty:
                    if not os.isatty(tty.fileno()):
                        raise OSError
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
        from sandbox.core import die
        die(f"{exc.code}: {exc.message}")
    except Exception:
        # Do not render, repr, or chain unknown failures. Parser and backend
        # exceptions may retain secret-bearing source buffers or stderr.
        from sandbox.core import die
        die("operation_failed: secret operation failed")


register_specs((CommandSpec(
    name="secrets", handler=cmd_secrets, configure=configure_parser,
    owner=__name__, scope="global", destructive=False,
),))
