"""``sb host login-url`` for a target a joined server records (feedback 3234ad2b).

On a joined Tailnet remote, ``host stack up`` and ``host apply`` run on the
server, so the stack record, its rendered manifest and the hosting state live
there. The controller first reads the installed runtime's ``job-capabilities``
(its owner identity and whether it serves :data:`CONTRACT`), then makes one
control call, ``sb host login-url --here --token-sha256 H``. The installed
owner resolves the recorded stack, installs ``H`` and records the user and
expiry under the target's writer lock, and prints one JSON line with the link
*without* its ``#token`` fragment. The token is made on the controller and
never leaves it: it is appended to the server's ``login_base`` only for this
terminal. Re-sending the same hash after a timeout installs the same entry,
so that retry cannot mint a second link. Nothing here writes the link to a
durable job, log, audit line, state file or argv. Both sides refuse inside a
durable job, whose output is stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
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
PROBE_TIMEOUT_SECONDS = 30
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SB = '"${SANDBOX_HOME:-$HOME/sandbox}/sb-src/sb" '

UNAVAILABLE = "login_url_server_unavailable"
JOB_REFUSED = "job_output_refused"
UNSUPPORTED = "login_url_server_unsupported"
NOT_DECLARED = "autologin_not_declared"
NOT_STACK = "login_url_target_not_stack"
REMOTE_MISMATCH = "login_url_remote_not_this_host"
FAILED = "login_url_failed"
AFTER_INSTALL = "login_url_failed_after_install"


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
    if getattr(args, "project_dir", None):
        raise HostingRefusal("host_target_invalid", "--project-dir cannot be combined with --name or --target "
                             "on a joined remote: the server's recorded stack is used, never a local checkout")
    if name is not None and not _NAME_RE.fullmatch(name):
        raise HostingRefusal("host_target_invalid", "invalid stack name")
    if not isinstance(getattr(args, "remote", None), str) or not _REMOTE_RE.fullmatch(args.remote):
        raise HostingRefusal("host_target_invalid", "--remote must name the remote the target is recorded for")
    ttl = getattr(args, "ttl_seconds", None)
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= 3600):
        raise HostingRefusal("login_url_invalid", "--ttl-seconds must be between 60 and 3600")


def issue_argv(sb: str, args, token_hash: str) -> list[str]:
    """The fixed server command: selectors, a lifetime and the token's hash, never the token."""
    name, target = getattr(args, "name", None), getattr(args, "target", None)
    argv = [sb, "host", "login-url", "--here", "--remote", args.remote,
            "--name" if name else "--target", name or target]
    if getattr(args, "environment", None):
        argv += ["--environment", args.environment]
    if getattr(args, "ttl_seconds", None) is not None:
        argv += ["--ttl-seconds", str(args.ttl_seconds)]
    return argv + ["--token-sha256", token_hash, "--confirm", "--json"]


def issue_command(args, token_hash: str) -> str:
    """One shell command; the server expands its own home as resolve_sandbox_home does.

    Expanding it there saves the separate SSH session that resolving it here
    would cost (feedback: that session timed out before anything was issued).
    """
    return _SB + shlex.join(issue_argv("sb", args, token_hash)[1:])


PROBE_COMMAND = _SB + "job-capabilities --json"


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


def _call(entry, ssh_run, command: str, timeout: int, *, what: str, retry_timeout: bool = True):
    """Run one control command; retry a connect failure or a timeout, nothing else.

    A connect-phase failure never started the command. A timeout may have: with
    ``retry_timeout`` (the default) it is retried because the probe is read-only and the mint re-sends the same
    hash, which installs the same entry rather than a second link. Any other
    answer, including an exit 255 after the session opened, is returned as is.
    """
    timed_out = False
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            result = ssh_run(entry, command, timeout=timeout)
        except subprocess.TimeoutExpired:
            if not retry_timeout:
                # A repeated command could act twice; its first run may have.
                raise HostingRefusal(UNAVAILABLE, f"{what} did not answer in time; it may have finished, "
                                     "so it was not repeated; nothing was shown") from None
            timed_out = True
        else:
            if not _not_connected(result):
                return result
        if attempt + 1 < CONNECT_ATTEMPTS:
            time.sleep(2 * (attempt + 1))
    if timed_out:
        raise HostingRefusal(UNAVAILABLE, f"{what} did not answer in time after {CONNECT_ATTEMPTS} "
                             "attempts; no link was shown, and any entry it installed expires unused")
    raise HostingRefusal(UNAVAILABLE, f"could not reach {what} after {CONNECT_ATTEMPTS} attempts; "
                         "nothing was issued")


def _migrate_hint(remote_name: str) -> str:
    return f"sb remote service migrate {remote_name} --confirm"


def _require_capability(args, entry, ssh_run, identity: str, marker: str, *,
                        key: str = "host_login_url_capabilities", contract: str = CONTRACT,
                        purpose: str = "issue login links") -> None:
    """Admit only the registered owner whose installed runtime serves ``contract``, before any mint."""
    import sandbox.core._remote as remote
    result = _call(entry, ssh_run, PROBE_COMMAND, PROBE_TIMEOUT_SECONDS,
                   what=f"{args.remote}'s capability probe")
    observed = _reply(getattr(result, "stdout", ""))
    if observed is None or observed.get("ok") is not True:
        detail = remote.redact_text((getattr(result, "stderr", "") or "").strip())[-300:]
        raise HostingRefusal(UNAVAILABLE, f"could not read {args.remote}'s installed capabilities "
                             f"(exit {getattr(result, 'returncode', None)}); nothing was issued"
                             + (f": {detail}" if detail else ""))
    if observed.get("target_identity") != identity or observed.get("ownership_marker") != marker:
        raise HostingRefusal(UNAVAILABLE, f"{args.remote} did not answer as its registered owner; "
                             "nothing was issued")
    capabilities = observed.get(key)
    if not isinstance(capabilities, list) or contract not in capabilities:
        raise HostingRefusal(UNAVAILABLE, f"the runtime installed on {args.remote} cannot {purpose} "
                             f"for the targets it records yet; migrate it first ({_migrate_hint(args.remote)}); "
                             "nothing was issued")


def issue_from_controller(args, entry, *, ssh_run=None) -> dict:
    """Have the installed owner install this controller's token hash; show the link here only."""
    import sandbox.core._remote as remote
    _check_request(args)
    if getattr(args, "token_sha256", None) is not None:
        raise HostingRefusal("host_target_invalid", "--token-sha256 is only for the server side (--here)")
    ssh_run = ssh_run or remote.ssh_run
    service = entry.get("mcp_service") if isinstance(entry.get("mcp_service"), dict) else {}
    control = entry.get("control_identity") if isinstance(entry.get("control_identity"), dict) else {}
    identity, marker = control.get("target_identity"), service.get("ownership_marker")
    if not (isinstance(identity, str) and identity and isinstance(marker, str) and marker):
        raise HostingRefusal(UNAVAILABLE, f"the registration for {args.remote} records no owner identity or "
                             "ownership marker to check the reply against; re-join or refresh it "
                             f"(sb remote service status {args.remote}); nothing was issued")
    _require_capability(args, entry, ssh_run, identity, marker)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = _call(entry, ssh_run, issue_command(args, token_hash), ISSUE_TIMEOUT_SECONDS,
                   what=f"{args.remote}'s login-url issuer")
    payload = _reply(getattr(result, "stdout", ""))
    if payload is None:
        detail = remote.redact_text((getattr(result, "stderr", "") or "").strip())[-300:]
        raise HostingRefusal(UNAVAILABLE, f"the server gave no result (exit {getattr(result, 'returncode', None)})"
                             + (f": {detail}" if detail else ""))
    if payload.get("ok") is not True:
        code = payload.get("code") if isinstance(payload.get("code"), str) else UNAVAILABLE
        raise HostingRefusal(code, remote.redact_text(str(payload.get("error") or "refused"))[:500])
    if (payload.get("contract") != CONTRACT or payload.get("target_identity") != identity
            or payload.get("ownership_marker") != marker or payload.get("token_sha256") != token_hash):
        raise HostingRefusal(UNAVAILABLE, "the reply did not come from this remote's registered owner "
                             "for this request; the link was not shown")
    base = payload.get("login_base")
    if (not isinstance(base, str) or not base.startswith("https://") or "#" in base
            or any(char.isspace() for char in base)):
        raise HostingRefusal(UNAVAILABLE, "the server reply holds no login link")
    return {"url": f"{base}#{token}",
            **{key: payload[key] for key in ("expires_at", "one_time", "user", "target", "name")
               if key in payload}}


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


def _require_this_host(remote_name: str, *, command: str = "login-url") -> None:
    """``--remote`` must name this host, the origin its controllers registered it under.

    A name this host itself registers as one of its own remotes is another
    machine: its records here belong to a target that is not local.
    """
    import sandbox.core._remote as remote
    try:
        own = remote.get_remote(remote_name, adopt_joined=False)
    except Exception:  # noqa: BLE001 - fail closed; the detail is not needed
        raise HostingRefusal(REMOTE_MISMATCH, "this host's own remote registry could not be read") from None
    if own is not None:
        raise HostingRefusal(REMOTE_MISMATCH, f"{remote_name} is a remote this host controls, not this host; "
                             f"run {command} from that remote's controller")


def _require_stack(remote_name: str, args, *, command: str = "login-url") -> dict:
    """Only a stack this host records; a plain target would read a manifest from the cwd."""
    from sandbox.hosting import stack_registry, target_resolution
    registry = stack_registry.read()
    target = getattr(args, "target", None)
    if target:
        project, environment = target_resolution.parse_target(target)
        if target_resolution._stack_record(registry, remote_name, project=project,
                                           environment=environment) is None:
            raise HostingRefusal(NOT_STACK, f"{target} is not a stack this server records for {remote_name}; "
                                 f"{command} on a joined remote covers recorded stacks only")
    return registry


def issue_here(args, *, owner=None, runner=None) -> dict:
    """Server side: install the controller's token hash for a stack this host records."""
    from sandbox.hosting import target_resolution
    from sandbox.commands import hosting as hosting_cmd
    _check_request(args)
    token_hash = getattr(args, "token_sha256", None)
    if not isinstance(token_hash, str) or not _HASH_RE.fullmatch(token_hash):
        raise HostingRefusal("login_url_invalid", "--here needs --token-sha256 with the controller's token hash")
    if owner is None:
        from sandbox.application.server_cli import read_current_server_owner, ServerCLIUnavailable
        try:
            owner = read_current_server_owner()
        except ServerCLIUnavailable:
            raise HostingRefusal(UNAVAILABLE, "--here runs only on a remote's installed Sandbox owner") from None
    _require_this_host(args.remote)
    registry = _require_stack(args.remote, args)
    resolved = target_resolution.resolve(
        args.remote, target=getattr(args, "target", None), name=getattr(args, "name", None),
        environment=getattr(args, "environment", None), registry=registry)
    if resolved.kind != "stack":
        raise HostingRefusal(NOT_STACK, f"{resolved.label} is not a stack this server records")
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
                save_state=durable_save, run=runner or _local_runner, home=owner.sandbox_home,
                token_hash=token_hash))
    except TimeoutError:
        raise HostingRefusal("host_target_busy", "another host apply or recovery owns this target") from None
    if "url" in result or not isinstance(result.get("login_base"), str):
        raise RuntimeError("the issuer returned an unexpected result")
    return {**result, "contract": CONTRACT, "target": resolved.label, "name": resolved.slug,
            "token_sha256": token_hash, "target_identity": owner.target_identity,
            "ownership_marker": owner.ownership_marker}


def _refusal(exc: BaseException) -> dict:
    import sandbox.core._remote as remote
    code = getattr(exc, "code", None)
    detail = getattr(exc, "detail", None) or str(exc)
    return {"ok": False, "code": code if isinstance(code, str) else FAILED,
            "error": remote.redact_text(str(detail))[:500]}


_KNOWN = (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError)


def run_here(args) -> None:
    """``sb host login-url --here``: always one JSON line, for the controller to read.

    No token exists on this side, and an unexpected error's text is never
    printed (it could carry anything); its type alone is named.
    """
    started = []

    def runner(command, timeout):
        started.append(True)
        return _local_runner(command, timeout)
    try:
        payload = {"ok": True, **issue_here(args, runner=runner)}
    except Exception as exc:  # noqa: BLE001 - the contract is one JSON line, whatever failed
        known = isinstance(exc, _KNOWN)
        payload = _refusal(exc) if known else {
            "ok": False, "code": FAILED, "error": f"unexpected {type(exc).__name__} while issuing"}
        if started:
            payload["code"] = AFTER_INSTALL
            payload["error"] = ("the sign-in hash may be installed, but issuing did not finish ("
                                + (getattr(exc, "code", None) if known and isinstance(getattr(exc, "code", None), str)
                                   else type(exc).__name__)
                                + "); no link was shown and that entry expires unused; run the command again")
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
