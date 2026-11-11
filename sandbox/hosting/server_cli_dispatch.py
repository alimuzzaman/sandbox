"""Select installed owner execution before controller hosting preflight.

Only a registered joined Tailscale target selects this path. The durable job
adapter owns admission, source staging, deadlines and the original receipt.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets


def dispatch_host_if_selected(args) -> bool:
    original = getattr(args, "_server_cli_argv", None)
    remote_name = getattr(args, "remote", None)
    if not original or not remote_name:
        return False
    # Explicit credential declarations retain their existing closed contract.
    # Explicit personal-local is also an execution choice, not a fallback.
    if getattr(args, "credential_scope", None) is not None or any(
            getattr(args, field, None) is not None for field in (
                "credential_project", "credential_source", "credential_source_revision",
                "credential_profile", "credential_profile_revision")):
        return False
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
    request_id = getattr(args, "request_id", None) or "host-cli-" + secrets.token_hex(12)
    receipt = dispatch_server_cli(remote_name, argv, project_dir=str(root),
        request_id=request_id, timeout_seconds=getattr(args, "timeout", None) or 900)
    # The adapter returns the retained original receipt, including unknown
    # acceptance. Never reinterpret it as execution or delivery success.
    print(json.dumps(receipt, sort_keys=True))
    return True
