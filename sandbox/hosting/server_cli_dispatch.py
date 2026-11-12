"""Select installed owner execution before controller hosting preflight.

Only a registered joined Tailscale target selects this path. The durable job
adapter owns admission, source staging, deadlines and the original receipt.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import subprocess
import sys

STACK_REF_CONTRACT = 'job.server-cli-stack-ref.v1'


class StackRefBindingError(RuntimeError):
    code = 'server_cli_stack_ref_unavailable'


def stack_ref_contract_available():
    """The real installed host parser and receiver must compose this option."""
    from sandbox.application.command_selection import selected_invocation, CommandSelectionUnavailable
    try:
        selected = selected_invocation(('host', 'stack', 'up', '--server-cli-stack-ref={}'))
        return selected is not None and selected.command == ('host', 'stack') and callable(resolve_stack_ref)
    except CommandSelectionUnavailable:
        return False


def resolve_stack_ref(args, checkout):
    """Use retained source provenance without inventing a branch in Git."""
    from sandbox.application.server_cli import server_cli_context_if_selected, validate_owner_current
    from sandbox.commands.host_stack import resolve_ref
    from sandbox.hosting.stack_template import StackRef
    packed = getattr(args, 'server_cli_stack_ref', None)
    context = server_cli_context_if_selected()
    if packed is None:
        return resolve_ref(checkout, getattr(args, 'ref', None))
    if context is None:
        raise StackRefBindingError('server_cli_stack_ref_requires_retained_job')
    if not isinstance(packed, str) or len(packed) > 4096:
        raise StackRefBindingError('server_cli_stack_ref_unavailable')
    try:
        value = json.loads(packed)
    except ValueError:
        raise StackRefBindingError('server_cli_stack_ref_invalid') from None
    if (not isinstance(value, dict) or set(value) != {'ref', 'commit', 'ref_branch'}
            or any(not isinstance(item, str) for item in value.values())
            or any(len(item) > 1024 or any(ord(char) < 32 for char in item) for item in value.values())
            or not value['ref'] or value['ref'] != (getattr(args, 'ref', None) or 'HEAD').strip()
            or value['commit'] != context.source_commit
            or Path(context.project_root).resolve() != Path(checkout).resolve()
            or '--server-cli-stack-ref=' + packed not in context.argv):
        raise StackRefBindingError('server_cli_stack_ref_mismatch')
    if validate_owner_current(context.owner) != context:
        raise StackRefBindingError('server_cli_stack_ref_owner_changed')
    actual = resolve_ref(checkout, 'HEAD')
    if actual.commit != context.source_commit:
        raise StackRefBindingError('server_cli_stack_ref_source_changed')
    try:
        ref = StackRef(**value)
    except ValueError:
        raise StackRefBindingError('server_cli_stack_ref_invalid') from None
    if validate_owner_current(context.owner) != context:
        raise StackRefBindingError('server_cli_stack_ref_owner_changed')
    return ref


def dispatch_host_if_selected(args) -> bool:
    try:
        return _dispatch_host_if_selected(args)
    except Exception as error:
        code = getattr(error, "code", None)
        unknown = bool(getattr(error, "completion_unknown", False)) or (
            bool(getattr(args, "_server_cli_submission_started", False))
            and code != "server_cli_capability_unavailable")
        _failure(args, "server_cli_acceptance_unknown" if unknown else
                 "server_cli_execution_unavailable", unknown=unknown)


def _failure(args, code, *, unknown, receipt=None):
    request_id = getattr(args, "_server_cli_request_id", None) or getattr(args, "request_id", None)
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id) is None:
        request_id = None
    payload = {"ok": False, "code": code, "request_id": request_id,
        "acceptance": "unknown" if unknown else "not_accepted",
        "error": ("Inspect the original request/job; execution is not safe to replay."
                  if unknown else "Server execution is unavailable; no controller fallback was attempted.")}
    job_id = receipt.get("job_id") if isinstance(receipt, dict) else None
    if isinstance(job_id, str) and re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{32})", job_id):
        payload["job_id"] = job_id
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{code}: {payload['error']} request_id={request_id or 'unavailable'}", file=sys.stderr)
    raise SystemExit(1)


def _dispatch_host_if_selected(args) -> bool:
    original = getattr(args, "_server_cli_argv", None)
    remote_name = getattr(args, "remote", None)
    if not original or not remote_name:
        return False
    # Exec has a separate no-source contract and result adapter. Provider-aware
    # hosting commands run in the retained owner context before local preflight.
    action = getattr(args, "action", None)
    if action == "exec":
        from sandbox.hosting.server_cli_exec_client import dispatch_exec_if_selected
        return dispatch_exec_if_selected(args)
    stack_action = getattr(args, "image_action", None)
    selected = (action in {"plan", "status", "tailnet-name", "recover", "retire-delivery",
                           "refresh-front-door", "destroy"} or
                (action == "apply" and getattr(args, "confirm", False)) or
                (action == "stack" and stack_action in {
                    "up", "list", "down", "ttl", "sweep", "reap", "limits"}))
    if not selected:
        return False
    args._server_cli_request_id = (getattr(args, "_server_cli_request_id", None)
        or getattr(args, "request_id", None) or "host-cli-" + secrets.token_hex(12))
    from sandbox.hosting.host_exec import split_passthrough
    _, tail = split_passthrough(list(original))
    if tail is not None:
        raise ValueError("server_cli_unexpected_stdin_or_command")
    try:
        from sandbox.application.server_cli import server_cli_context_if_selected
    except ModuleNotFoundError as exc:
        if exc.name != "sandbox.application.server_cli":
            raise
    else:
        # An invalid selected child raises. Never turn lost authority into a
        # controller fallback or submit another job from the same invocation.
        if server_cli_context_if_selected() is not None:
            return False
    from sandbox.core import _remote
    entry = _remote.get_remote(remote_name)
    if not isinstance(entry, dict) or not entry.get("joined") or entry.get("control_transport") != "tailscale":
        return False
    if getattr(args, "credential_scope", None) is not None or any(
            getattr(args, field, None) is not None for field in (
                "credential_project", "credential_source", "credential_source_revision",
                "credential_profile", "credential_profile_revision")):
        raise ValueError("joined hosting requires server-held credentials")
    try:
        from sandbox.application.server_cli import dispatch_server_cli
    except ModuleNotFoundError as exc:
        if exc.name != "sandbox.application.server_cli":
            raise
        raise RuntimeError("server_cli_unavailable: installed server execution adapter is unavailable") from None
    project_dir = getattr(args, "project_dir", None)
    template = getattr(args, "template", None)
    if project_dir is None and template:
        from sandbox.commands.host_stack import template_checkout
        root, _ = template_checkout(Path(template).expanduser().resolve(strict=True))
    else:
        root = Path(project_dir or ".").expanduser().resolve(strict=True)
    argv = list(original)
    source_ref = None
    if action == 'stack' and stack_action == 'up':
        if getattr(args, 'server_cli_stack_ref', None) is not None or any(
                value.split('=', 1)[0] == '--server-cli-stack-ref' for value in argv):
            raise ValueError('server_cli_stack_ref_reserved')
        from sandbox.commands.host_stack import resolve_ref
        ref = resolve_ref(root, getattr(args, 'ref', None))
        source_ref = ref.commit
        argv.append('--server-cli-stack-ref=' + json.dumps({
            'ref': ref.ref, 'commit': ref.commit, 'ref_branch': ref.ref_branch},
            sort_keys=True, separators=(',', ':')))
    # Paths in the fixed command refer to its staged source, not the Mac.
    # No host path, runtime path or target identity is supplied as authority.
    head = argv.index("--") if "--" in argv else len(argv)
    index = 0
    while index < head:
        token = argv[index]
        option, equals, value = token.partition("=")
        if option in {"--project-dir", "--template"}:
            if not equals:
                if index + 1 >= head:
                    raise ValueError("server_cli_path_invalid")
                value = argv[index + 1]
            path = Path(value).expanduser().resolve(strict=True)
            try:
                relocated = "." if option == "--project-dir" else path.relative_to(root).as_posix()
                if option == "--project-dir" and path != root:
                    raise ValueError
            except ValueError:
                raise ValueError("server_cli_path_outside_source") from None
            if equals:
                argv[index] = option + "=" + relocated
            else:
                argv[index + 1] = relocated
                index += 1
        index += 1
    request_id = args._server_cli_request_id
    args._server_cli_submission_started = True
    receipt = dispatch_server_cli(remote_name, argv, project_dir=str(root),
        request_id=request_id, timeout_seconds=getattr(args, "timeout", None) or 900,
        **({'source_ref': source_ref} if source_ref is not None else {}))
    # The adapter returns the retained original receipt, including unknown
    # acceptance. Never reinterpret it as execution or delivery success.
    if not isinstance(receipt, dict):
        _failure(args, "server_cli_acceptance_unknown", unknown=True)
    if receipt.get("ok") is False or receipt.get("status") == "acceptance_unknown":
        unknown = receipt.get("acceptance") != "not_accepted"
        _failure(args, "server_cli_acceptance_unknown" if unknown else "server_cli_execution_unavailable",
                 unknown=unknown, receipt=receipt)
    print(json.dumps(receipt, sort_keys=True))
    return True
