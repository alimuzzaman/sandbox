"""``sb host login-url`` for a target a joined server records (feedback 3234ad2b).

On a joined Tailnet remote, ``host stack up`` and ``host apply`` run on the
server, so the stack record, its rendered manifest and the hosting state live
there. The controller therefore makes one control call to the installed
runtime, ``sb host login-url --here``, which resolves the target, mints and
records the token under the target's writer lock on the server, and prints a
single JSON reply. That reply is the only place the link exists: it travels
back on the SSH channel's stdout to the controller, which prints it on its own
terminal. Nothing here writes the link to a durable job, log, audit line, state
file or argv: only the token's SHA-256 is installed, and the host state keeps
the user and expiry. Both sides refuse inside a durable job, whose output is
stored.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time

import sandbox.core._hosting as hosting
from sandbox.core._hosting import HostingRefusal

CONTRACT = "host.login-url-here.v1"
JOB_ENV = "SANDBOX_DURABLE_JOB_ID"
_REMOTE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# OpenSSH reports these before any session exists, so the remote command never
# started and one more attempt cannot mint a second link.
_NOT_CONNECTED = re.compile(
    r"^ssh: connect to .*: (?:Operation timed out|Connection timed out|Connection refused|"
    r"No route to host|Network is unreachable)\s*$"
    r"|^Connection timed out during banner exchange\s*$"
    r"|^ssh: Could not resolve hostname .*$", re.MULTILINE)
CONNECT_ATTEMPTS = 3
ISSUE_TIMEOUT_SECONDS = 240

UNAVAILABLE = "login_url_server_unavailable"
JOB_REFUSED = "job_output_refused"
UNSUPPORTED = "login_url_server_unsupported"
NOT_DECLARED = "autologin_not_declared"


def selected(args, entry) -> bool:
    """A joined Tailnet remote's named target is the server's, never this controller's."""
    return (not getattr(args, "here", False)
            and isinstance(entry, dict) and entry.get("joined") is True
            and entry.get("control_transport") == "tailscale"
            and bool(getattr(args, "name", None) or getattr(args, "target", None)))


def _unsupported_options(args) -> list[str]:
    return [flag for flag, value in (
        ("--service", getattr(args, "service", None)), ("--email", getattr(args, "email", None)),
        ("--save-email", getattr(args, "save_email", False)),
        ("--list", getattr(args, "list_links", False)),
        ("--revoke", getattr(args, "revoke", None)), ("--uses", getattr(args, "uses", None)))
        if value not in (None, False)]


def _check_request(args) -> None:
    if os.environ.get(JOB_ENV):
        raise HostingRefusal(JOB_REFUSED, "a durable job stores its output, and a login link must not land there")
    if not getattr(args, "confirm", False):
        raise HostingRefusal("confirmation_required",
                             "host login-url is protected; pass --confirm to issue a one-time admin link")
    extra = _unsupported_options(args)
    if extra:
        raise HostingRefusal(UNSUPPORTED, f"{', '.join(extra)} is not available for a target the "
                             "server records; this path issues the WordPress autologin link only")
    name, target = getattr(args, "name", None), getattr(args, "target", None)
    if bool(name) == bool(target):
        raise HostingRefusal("host_target_invalid", "pass exactly one of --name or --target")
    if name is not None and not _NAME_RE.fullmatch(name):
        raise HostingRefusal("host_target_invalid", "invalid stack name")
    if not isinstance(getattr(args, "remote", None), str) or not _REMOTE_RE.fullmatch(args.remote):
        raise HostingRefusal("host_target_invalid", "--remote must name the remote the target is recorded for")
    ttl = getattr(args, "ttl_seconds", None)
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= 3600):
        raise HostingRefusal("login_url_invalid", "--ttl-seconds must be between 60 and 3600")


def issue_argv(sb: str, args) -> list[str]:
    """The fixed server command: selectors and a lifetime, never a secret."""
    name, target = getattr(args, "name", None), getattr(args, "target", None)
    argv = [sb, "host", "login-url", "--here", "--remote", args.remote,
            "--name" if name else "--target", name or target]
    if getattr(args, "environment", None):
        argv += ["--environment", args.environment]
    if getattr(args, "ttl_seconds", None) is not None:
        argv += ["--ttl-seconds", str(args.ttl_seconds)]
    return argv + ["--confirm", "--json"]


def issue_command(args) -> str:
    """One shell command; the server expands its own home as resolve_sandbox_home does.

    Expanding it there saves the separate SSH session that resolving it here
    would cost (feedback: that session timed out before anything was issued).
    """
    argv = issue_argv("sb", args)
    return '"${SANDBOX_HOME:-$HOME/sandbox}/sb-src/sb" ' + shlex.join(argv[1:])


def _not_connected(result) -> bool:
    return (getattr(result, "returncode", None) == 255 and not (getattr(result, "stdout", "") or "").strip()
            and bool(_NOT_CONNECTED.search(getattr(result, "stderr", "") or "")))


def _reply(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def issue_from_controller(args, entry, *, ssh_run=None, sleep=time.sleep) -> dict:
    """Ask the installed owner to mint the link; return its reply to this terminal only."""
    import sandbox.core._remote as remote
    _check_request(args)
    ssh_run = ssh_run or remote.ssh_run
    service = entry.get("mcp_service") if isinstance(entry.get("mcp_service"), dict) else {}
    identity = (entry.get("control_identity") or {}).get("target_identity")
    command = issue_command(args)
    result = None
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            result = ssh_run(entry, command, timeout=ISSUE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            # The command may have run; another attempt could mint a second link.
            raise HostingRefusal(UNAVAILABLE, "the server did not answer in time; whether a link was "
                                 "issued is unknown, so run the same command again for a fresh one") from None
        if not _not_connected(result):
            break
        if attempt + 1 < CONNECT_ATTEMPTS:
            sleep(2 * (attempt + 1))
    else:
        raise HostingRefusal(UNAVAILABLE, f"could not connect to {args.remote} after "
                             f"{CONNECT_ATTEMPTS} attempts; nothing was issued")
    payload = _reply(getattr(result, "stdout", ""))
    if payload is None:
        detail = remote.redact_text((getattr(result, "stderr", "") or "").strip())[-300:]
        if "unrecognized arguments" in detail or "invalid choice" in detail or "no remote named" in detail:
            raise HostingRefusal(UNAVAILABLE, "the remote runtime cannot issue login links for the targets it "
                                 f"records yet; migrate it first (sb remote service migrate {args.remote} --confirm)")
        raise HostingRefusal(UNAVAILABLE, f"the server gave no result (exit {getattr(result, 'returncode', None)})"
                             + (f": {detail}" if detail else ""))
    if payload.get("ok") is not True:
        code = payload.get("code") if isinstance(payload.get("code"), str) else UNAVAILABLE
        raise HostingRefusal(code, remote.redact_text(str(payload.get("error") or "refused"))[:500])
    if (payload.get("contract") != CONTRACT
            or (identity and payload.get("target_identity") != identity)
            or (service.get("ownership_marker") and payload.get("ownership_marker") != service["ownership_marker"])):
        raise HostingRefusal(UNAVAILABLE, "the reply did not come from this remote's registered owner; "
                             "the link was not shown")
    url = payload.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or "#" not in url:
        raise HostingRefusal(UNAVAILABLE, "the server reply holds no login link")
    return {key: payload[key] for key in ("url", "expires_at", "one_time", "user", "target", "name")
            if key in payload}


def _local_runner(command: str, timeout: int) -> str:
    try:
        result = subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=False,
                                timeout=timeout, start_new_session=True, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"command timed out after {timeout} seconds") from None
    if result.returncode:
        from sandbox.commands.hosting import _remote_failure_message
        raise RuntimeError(_remote_failure_message((result.stderr or "") + "\n" + (result.stdout or ""), 500))
    return result.stdout


def issue_here(args, *, owner=None, runner=None) -> dict:
    """Server side: mint and record the token for a target this host records."""
    from sandbox.hosting import target_resolution
    from sandbox.commands import hosting as hosting_cmd
    _check_request(args)
    if owner is None:
        from sandbox.application.server_cli import read_current_server_owner, ServerCLIUnavailable
        try:
            owner = read_current_server_owner()
        except ServerCLIUnavailable:
            raise HostingRefusal(UNAVAILABLE, "--here runs only on a remote's installed Sandbox owner") from None
    resolved = target_resolution.resolve(
        args.remote, target=getattr(args, "target", None), name=getattr(args, "name", None),
        environment=getattr(args, "environment", None))
    validated = hosting.validate_manifest(resolved.project_dir, resolved.environment)
    if (validated["project"], validated["environment"]) != (resolved.project, resolved.environment):
        raise HostingRefusal("host_target_invalid", "recorded stack manifest no longer matches the selected target")
    if not validated.get("autologin"):
        raise HostingRefusal(NOT_DECLARED, f"{resolved.label} declares no WordPress autologin; "
                             "a share gate link comes from `sb host share create`")
    try:
        result = hosting_cmd._with_host_writer_lock(
            validated, args.remote,
            lambda current, durable_save: hosting_cmd._issue_host_autologin(
                validated, None, args.remote, current, getattr(args, "ttl_seconds", None),
                save_state=durable_save, run=runner or _local_runner, home=owner.sandbox_home))
    except TimeoutError:
        raise HostingRefusal("host_target_busy", "another host apply or recovery owns this target") from None
    return {**result, "contract": CONTRACT, "target": resolved.label, "name": resolved.slug,
            "target_identity": owner.target_identity, "ownership_marker": owner.ownership_marker}


def _refusal(exc: BaseException) -> dict:
    import sandbox.core._remote as remote
    code = getattr(exc, "code", None)
    detail = getattr(exc, "detail", None) or str(exc)
    return {"ok": False, "code": code if isinstance(code, str) else "login_url_failed",
            "error": remote.redact_text(str(detail))[:500]}


def run_here(args) -> None:
    """``sb host login-url --here``: always one JSON line, for the controller to read."""
    try:
        payload = {"ok": True, **issue_here(args)}
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        payload = _refusal(exc)
    print(json.dumps(payload, sort_keys=True))
    if not payload["ok"]:
        raise SystemExit(1)


def run_from_controller(args, entry) -> None:
    try:
        result = issue_from_controller(args, entry)
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
        payload = _refusal(exc)
        if getattr(args, "json", False):
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"error: {payload['code']}: {payload['error']}", file=sys.stderr)
        raise SystemExit(1)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, sort_keys=True))
    else:
        print(result["url"])


def follow_up_command(remote_name: str, slug: str) -> str:
    """What `host stack up` tells the operator to run once its apply job succeeds."""
    return shlex.join(["sb", "host", "login-url", "--remote", remote_name, "--name", slug, "--confirm"])
