"""Read-only secret metadata queries through the retained server owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _source_commit(root: Path) -> str:
    env = {
        "PATH": os.defpath,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True, text=True, timeout=5, env=env, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("server_cli_source_unavailable") from None
    commit = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("server_cli_source_unavailable")
    return commit


def dispatch_remote_metadata(args) -> bool:
    """Submit only exact-key metadata selectors and print the durable receipt."""
    if args.action not in {"source-info", "inspect"}:
        return False
    remote = getattr(args, "remote", None)
    request_id = getattr(args, "request_id", None)
    timeout = getattr(args, "timeout", None)
    target_project_dir = getattr(args, "target_project_dir", None)
    if remote is None:
        if request_id is not None or timeout is not None or target_project_dir is not None:
            raise ValueError("remote_metadata_requires_remote")
        return False

    if (not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
            or type(timeout) is not int or not 1 <= timeout <= 1800):
        raise ValueError("remote_metadata_request_invalid")
    if not isinstance(args.source, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.source) is None:
        raise ValueError("remote_metadata_source_invalid")
    if target_project_dir is not None and (
            not isinstance(target_project_dir, str) or not target_project_dir.startswith("/")
            or "\x00" in target_project_dir or any(ord(char) < 32 for char in target_project_dir)):
        raise ValueError("remote_metadata_target_project_invalid")

    if args.action == "source-info":
        if getattr(args, "exact_size", False):
            raise ValueError("remote_metadata_exact_size_refused")
        argv = ["secrets", "source-info", "--source", args.source, "--json"]
    else:
        keys = getattr(args, "keys", None)
        from sandbox.secrets.models import MAX_SELECTED_KEYS
        from sandbox.secrets.policy import validate_key
        if (getattr(args, "mode", None) != "metadata" or not keys
                or len(keys) > MAX_SELECTED_KEYS
                or any(not isinstance(key, str) or not key for key in keys)
                or getattr(args, "exact_length", False)):
            raise ValueError("remote_metadata_requires_exact_key_metadata")
        argv = ["secrets", "inspect", "--source", args.source]
        for key in keys:
            validate_key(key)
            argv.extend(("--key", key))
        argv.extend(("--mode", "metadata", "--json"))

    if target_project_dir is not None:
        argv.extend(("--project-dir", target_project_dir))
    root = Path(args.project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("server_cli_source_unavailable")
    source_commit = _source_commit(root)

    from sandbox.application.server_cli import dispatch_server_cli
    from sandbox.hosting.server_cli_dispatch import _failure

    args._server_cli_request_id = request_id
    args._server_cli_submission_started = True
    try:
        receipt = dispatch_server_cli(
            remote, argv, project_dir=str(root), request_id=request_id,
            timeout_seconds=timeout, source_ref=source_commit,
        )
    except Exception as error:
        args._server_cli_request_id = request_id
        unknown = getattr(error, "code", None) != "server_cli_capability_unavailable"
        _failure(args, "server_cli_acceptance_unknown" if unknown else
                 "server_cli_execution_unavailable", unknown=unknown)
    if (not isinstance(receipt, dict) or receipt.get("ok") is False
            or receipt.get("status") == "acceptance_unknown"):
        args._server_cli_request_id = request_id
        unknown = not isinstance(receipt, dict) or receipt.get("acceptance") != "not_accepted"
        _failure(args, "server_cli_acceptance_unknown" if unknown else
                 "server_cli_execution_unavailable", unknown=unknown, receipt=receipt)
    print(json.dumps(receipt, sort_keys=True))
    return True
