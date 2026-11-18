"""``sb host share`` for a stack a joined server records (feedback f0176eae).

On a joined Tailnet remote, ``host stack up``, ``host apply`` and the server's
own follows run on the server, so the stack record, its rendered manifest and
the hosting state live there, and the controller's registry never holds them.
As ``host login-url`` does (``server_login_url``), the controller reads the
installed runtime's ``job-capabilities`` and makes one control call,
``sb host share create|list|revoke --here``. The installed owner resolves its
recorded stack, changes the gate under the target's writer lock, audits the
operation and prints one JSON line.

For ``create`` the token is made on the controller and only its SHA-256 crosses
the channel, so the server's reply holds the link id and hostname but never the
link. A ``create`` that timed out is not repeated: it may have stored the record.
``--agent-link`` tells the server the controller has no terminal to show the link
on, so a target without ``share.agent_mint: true`` refuses before anything is
stored. Both sides refuse inside a durable job, whose output is stored.

Only ``--name`` and ``--target`` selectors use this path; a stack page
(``--stack``) keeps the controller path.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import sandbox.core._hosting as hosting
from sandbox.hosting import server_login_url as slu
from sandbox.hosting.share import ShareRefusal

CONTRACT = "host.share-here.v1"
CAPABILITY_KEY = "host_share_capabilities"
TIMEOUT_SECONDS = 120
JOB_ENV = slu.JOB_ENV
VERBS = ("create", "list", "revoke")


# ------------------------------------------------------------------ controller

def joined_entry(remote_name: str | None, *, target: str | None, name: str | None,
                 stack: str | None = None) -> dict | None:
    """The joined Tailnet registration when the named target is the server's own, else None."""
    if stack or not (target or name) or not remote_name:
        return None
    import sandbox.core._remote as remote
    entry = remote.get_remote(remote_name)
    if isinstance(entry, dict) and entry.get("joined") is True and entry.get("control_transport") == "tailscale":
        return entry
    return None


def check_selectors(*, remote_name, target, name, project_dir, email=None, stack=None) -> None:
    """The refusals both sides share, before anything runs."""
    if os.environ.get(JOB_ENV):
        raise ShareRefusal("job_output_refused",
                           "a durable job stores its output, and a share link must not land there")
    if stack or bool(target) == bool(name):
        raise ShareRefusal("share_options_conflict",
                           "a target the server records takes exactly one of --name or --target")
    if project_dir:
        raise ShareRefusal("share_options_conflict",
                           "--project-dir cannot be combined with --name or --target on a joined remote: "
                           "the server's recorded stack is used, never a local checkout")
    if email is not None:
        raise ShareRefusal("share_options_conflict", "--email applies only to --stack")
    if name is not None and not slu._NAME_RE.fullmatch(name):
        raise ShareRefusal("share_options_conflict", "invalid stack name")
    if not isinstance(remote_name, str) or not slu._REMOTE_RE.fullmatch(remote_name):
        raise ShareRefusal("share_options_conflict", "--remote must name the remote the target is recorded for")


def command(verb: str, remote_name: str, *, target=None, name=None, environment=None, entry=None,
            link_id=None, path=None, ttl=None, uses=None, token_sha256=None, agent_link=False) -> str:
    """The fixed server command; selectors, options and at most the token's hash, never a link."""
    argv = ["host", "share", verb, *([link_id] if link_id else []), "--here", "--remote", remote_name,
            "--name" if name else "--target", name or target]
    for flag, value in (("--environment", environment), ("--entry", entry), ("--path", path),
                        ("--ttl", ttl), ("--uses", uses), ("--token-sha256", token_sha256)):
        if value is not None:
            argv += [flag, str(value)]
    if agent_link:
        argv.append("--agent-link")
    return slu._SB + shlex.join([*argv, "--json"])


def call(remote_name: str, registration: dict, verb: str, *, ssh_run=None, **options) -> dict:
    """Run one ``--here`` verb on the registered owner and return its checked reply."""
    import sandbox.core._remote as remote
    ssh_run = ssh_run or remote.ssh_run
    service = registration.get("mcp_service") if isinstance(registration.get("mcp_service"), dict) else {}
    control = registration.get("control_identity") if isinstance(registration.get("control_identity"), dict) else {}
    identity, marker = control.get("target_identity"), service.get("ownership_marker")
    if not (isinstance(identity, str) and identity and isinstance(marker, str) and marker):
        raise ShareRefusal(slu.UNAVAILABLE, f"the registration for {remote_name} records no owner identity or "
                           "ownership marker to check the reply against; re-join or refresh it "
                           f"(sb remote service status {remote_name}); nothing was changed")
    try:
        slu._require_capability(SimpleNamespace(remote=remote_name), registration, ssh_run, identity, marker,
                                key=CAPABILITY_KEY, contract=CONTRACT, purpose="manage share links")
        result = slu._call(registration, ssh_run, command(verb, remote_name, **options), TIMEOUT_SECONDS,
                           what=f"{remote_name}'s share {verb}", retry_timeout=verb != "create")
    except hosting.HostingRefusal as exc:
        raise ShareRefusal(exc.code, exc.detail) from None
    payload = slu._reply(getattr(result, "stdout", ""))
    if payload is None:
        detail = remote.redact_text((getattr(result, "stderr", "") or "").strip())[-300:]
        raise ShareRefusal(slu.UNAVAILABLE, f"the server gave no result (exit {getattr(result, 'returncode', None)})"
                           + (f": {detail}" if detail else ""))
    if payload.get("ok") is not True:
        code = payload.get("code") if isinstance(payload.get("code"), str) else slu.UNAVAILABLE
        raise ShareRefusal(code, remote.redact_text(str(payload.get("error") or "refused"))[:500])
    if (payload.get("contract") != CONTRACT or payload.get("target_identity") != identity
            or payload.get("ownership_marker") != marker
            or payload.get("token_sha256") != options.get("token_sha256")):
        raise ShareRefusal(slu.UNAVAILABLE, "the reply did not come from this remote's registered owner "
                           "for this request; nothing was shown")
    return payload


# ---------------------------------------------------------------------- server

def _site_here(validated: dict, home: str, verb: str, name: str, *extra: str,
               input_data: str | None = None) -> dict:
    """``_site_remote`` on the host itself: one site verb in the running login service."""
    from sandbox.commands.hosting import _compose_prefix, _remote_failure_message
    from sandbox.hosting import login_service as login
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    prefix = _compose_prefix(validated, f"{home}/deploy-src/hosts/{validated['project']}",
                             f"{runtime_dir}/compose.override.yml", f"{runtime_dir}/environment.env")
    try:
        result = subprocess.run(
            ["sh", "-c", login.site_command(prefix, verb, name, *extra)], capture_output=True, text=True,
            check=False, timeout=60, start_new_session=True,
            **({"input": input_data} if input_data is not None else {"stdin": subprocess.DEVNULL}))
    except subprocess.TimeoutExpired:
        raise RuntimeError("the login service did not answer within 60 seconds") from None
    if result.returncode:
        raise RuntimeError(_remote_failure_message((result.stderr or "") + "\n" + (result.stdout or ""), 500))
    return login.parse_site_reply(result.stdout)


def _resolve_here(args):
    """The owner, the recorded stack's share target and its site_gate entry name."""
    from sandbox.application.server_cli import read_current_server_owner, ServerCLIUnavailable
    from sandbox.commands.host_share import ShareTarget, _declaration, _entry_name
    from sandbox.hosting import target_resolution
    check_selectors(remote_name=getattr(args, "remote", None), target=getattr(args, "target", None),
                    name=getattr(args, "name", None), project_dir=getattr(args, "project_dir", None),
                    email=getattr(args, "email", None), stack=getattr(args, "stack", None))
    try:
        owner = read_current_server_owner()
    except ServerCLIUnavailable:
        raise ShareRefusal(slu.UNAVAILABLE, "--here runs only on a remote's installed Sandbox owner") from None
    slu._require_this_host(args.remote, command="host share")
    registry = slu._require_stack(args.remote, args, command="host share")
    resolved = target_resolution.resolve(
        args.remote, target=getattr(args, "target", None), name=getattr(args, "name", None),
        environment=getattr(args, "environment", None), registry=registry)
    if resolved.kind != "stack":
        raise ShareRefusal(slu.NOT_STACK, f"{resolved.label} is not a stack this server records")
    validated = hosting.validate_manifest(resolved.project_dir, resolved.environment)
    if (validated["project"], validated["environment"]) != (resolved.project, resolved.environment):
        raise ShareRefusal("host_target_invalid", "recorded stack manifest no longer matches the selected target")
    if not isinstance((hosting.load_host_state().get("hosts") or {}).get(
            hosting.state_key(args.remote, validated)), dict):
        raise ShareRefusal("share_failed", "host is not deployed; apply the stack before sharing it")
    found = ShareTarget(remote=args.remote, validated=validated, entry=None, label=resolved.label,
                        project_dir=resolved.project_dir)
    declaration = _declaration(found)
    return owner, found, declaration, _entry_name(declaration, getattr(args, "entry", None))


def _locked(found, callback):
    from sandbox.commands.hosting import _with_host_writer_lock
    try:
        return _with_host_writer_lock(found.validated, found.remote, lambda _current, _save: callback())
    except TimeoutError:
        raise ShareRefusal("share_failed", "another host apply or recovery owns this target") from None


def _create_here(args, owner, found, declaration, entry_name) -> dict:
    from sandbox.hosting import login_service as login
    from sandbox.hosting import share
    login.assert_applicable(declaration, now=time.time())
    request = share.LinkRequest(entry=entry_name, kind="site", scope=share.validate_scope(args.share_path),
                                ttl_seconds=share.parse_ttl(args.ttl), uses=share.parse_uses(args.uses))
    agent_mint = bool((login.share_declaration(declaration) or {}).get("agent_mint"))
    if getattr(args, "agent_link", False) and not agent_mint:
        raise ShareRefusal(
            "agent_mint_not_enabled",
            "this target does not allow agent-made links; set `share.agent_mint: true` in its "
            "manifest and apply, or have the owner run `sb host share create` in a terminal")

    def mint():
        record, clamped = share.hashed_link_record(
            request, args.token_sha256, now=time.time(),
            ends_at=login.enabled_until_epoch(found.validated["login_service"]))
        reply = _site_here(found.validated, owner.sandbox_home, "mint", entry_name,
                           input_data=json.dumps({"link": record, "stack": None}, sort_keys=True))
        if reply.get("id") != record["id"]:
            raise ShareRefusal("share_failed", "the gate did not confirm the link")
        return record, clamped

    record, clamped = _locked(found, mint)
    return {"entry": entry_name, "kind": "site", "scope": request.scope, "uses": request.uses,
            "link_id": record["id"], "expires_at": record["expires_at"], "expires_clamped": clamped,
            "hostname": found.validated["login_service"]["entries"][entry_name]["hostname"],
            "agent_mint": agent_mint, "token_sha256": args.token_sha256}


def _list_here(args, owner, found, _declaration, entry_name) -> dict:
    reply = _site_here(found.validated, owner.sandbox_home, "list", entry_name)
    if reply.get("ok") is not True or not isinstance(reply.get("links"), list) \
            or not isinstance(reply.get("sessions"), list):
        raise ShareRefusal("share_list_unavailable", "current gate grants could not be confirmed")
    return {"entry": entry_name, "links": reply["links"], "sessions": reply["sessions"]}


def _revoke_here(args, owner, found, _declaration, entry_name) -> dict:
    from sandbox.hosting import share
    link_id = getattr(args, "share_id", None)
    if not isinstance(link_id, str) or not share.REVOKE_TARGET_RE.fullmatch(link_id):
        raise ShareRefusal("site_link_unknown", "pass a link id (l-...), a session id (s-...), or all")
    reply = _locked(found, lambda: _site_here(found.validated, owner.sandbox_home, "revoke", entry_name, link_id))
    return {"entry": entry_name, "revoked_links": reply.get("revoked_links", 0),
            "revoked_sessions": reply.get("revoked_sessions", 0), "revoked_keys": 0}


_HANDLERS = {"create": _create_here, "list": _list_here, "revoke": _revoke_here}


def run_here(args) -> None:
    """``sb host share create|list|revoke --here``: always one JSON line, for the controller to read."""
    from sandbox.commands.host_share import _refusal_from
    from sandbox.hosting import share
    from sandbox.hosting.login_service import LoginServiceError
    verb = getattr(args, "image_action", None)
    found, entry_name, payload = None, None, None
    try:
        if verb not in _HANDLERS:
            raise ShareRefusal("share_options_conflict", "usage: sb host share create|list|revoke ID|all --here")
        if verb == "create" and not getattr(args, "token_sha256", None):
            raise ShareRefusal("share_options_conflict", "--here create needs --token-sha256 with the controller's hash")
        if verb != "create" and (getattr(args, "token_sha256", None) or getattr(args, "agent_link", False)):
            raise ShareRefusal("share_options_conflict", "--token-sha256 and --agent-link apply only to create")
        owner, found, declaration, entry_name = _resolve_here(args)
        result = _HANDLERS[verb](args, owner, found, declaration, entry_name)
        payload = {"ok": True, "contract": CONTRACT, "target": found.label, "name": getattr(args, "name", None),
                   "target_identity": owner.target_identity, "ownership_marker": owner.ownership_marker,
                   **result}
    except Exception as exc:  # noqa: BLE001 - the contract is one JSON line, whatever failed
        known = isinstance(exc, (ShareRefusal, LoginServiceError, hosting.HostingError, RuntimeError,
                                 subprocess.SubprocessError, OSError, ValueError))
        refusal = _refusal_from(exc) if known else ShareRefusal("share_failed", f"unexpected {type(exc).__name__}")
        payload = {"ok": False, "code": refusal.code, "error": refusal.message}
    try:
        share.append_audit(share.audit_record(
            verb if verb in _HANDLERS else "unknown", remote=str(getattr(args, "remote", None)),
            target=found.label if found else str(getattr(args, "name", None) or getattr(args, "target", None)),
            entry=entry_name, outcome="ok" if payload["ok"] else "refused", code=payload.get("code"),
            controller="server-here", owner=share.owner_label(found.project_dir if found else None),
            link_id=payload.get("link_id") or (getattr(args, "share_id", None) if payload["ok"] else None)))
    except OSError:
        print("sb host share: warning: server audit record not written", file=sys.stderr)
    print(json.dumps(payload, sort_keys=True))
    if not payload["ok"]:
        raise SystemExit(1)
