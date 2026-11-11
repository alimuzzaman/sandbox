"""``sb host share create|list|revoke``: share links an agent can make (spec 081).

A link comes back exactly once: in the ``--json`` result or MCP result when the target's
manifest sets ``share.agent_mint: true``, or on the controlling terminal otherwise (the
075 rule). Only the token's SHA-256 travels to the server, over SSH stdin. Every
create, list and revoke is audited on the controller and on the server, without a
token, hash, key or address.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.hosting import login_service as login
from sandbox.hosting import share, target_resolution
from sandbox.hosting.share import ShareRefusal
from sandbox.hosting.share import stack_page

# The MCP tools set this, so a target without the agent opt-in never reaches for a
# terminal that may belong to the person's coding session.
CALLER_ENV = "SANDBOX_SHARE_CALLER"
JOB_ENV = "SANDBOX_DURABLE_JOB_ID"


@dataclass(frozen=True)
class ShareTarget:
    """A resolved target with its manifest, remote registration and label."""
    remote: str
    validated: dict
    entry: dict
    label: str
    project_dir: str
    stack: dict | None = None


def _refusal_from(exc: Exception) -> ShareRefusal:
    if isinstance(exc, ShareRefusal):
        return exc
    if isinstance(exc, login.LoginServiceError):
        return ShareRefusal(exc.code, exc.message)
    code = getattr(exc, "code", None)
    detail = getattr(exc, "detail", None) or str(exc)
    return ShareRefusal(code if isinstance(code, str) else "share_failed",
                        remote.redact_text(str(detail))[:500])


def resolve_target(remote_name: str | None, *, target: str | None = None, name: str | None = None,
                   stack: str | None = None, project_dir: str | None = None,
                   environment: str | None = None) -> ShareTarget:
    """Find the target by ``--target``, ``--name``, ``--stack`` or a project directory."""
    if not remote_name:
        raise ShareRefusal("share_options_conflict", "--remote is required")
    if sum(1 for value in (target, name, stack) if value) > 1:
        raise ShareRefusal("share_options_conflict", "pass at most one of --target, --name or --stack")
    entry = remote.get_remote(remote_name)
    if not entry:
        raise ShareRefusal("share_failed", f"no remote named '{remote_name}'")
    if not entry.get("provisioned"):
        raise ShareRefusal("share_failed", f"remote '{remote_name}' is not provisioned")
    state = hosting.load_host_state()
    record = None
    if target or name or stack:
        resolved = target_resolution.resolve(
            remote_name, target=target, name=name or stack, project_dir=project_dir,
            environment=environment, state=state)
        directory, env_name = resolved.project_dir, resolved.environment
        if stack:
            from sandbox.hosting import stack_registry
            record = stack_registry.read().get("stacks", {}).get(stack_registry.record_key(remote_name, stack))
            if not isinstance(record, dict) or resolved.kind != "stack":
                raise ShareRefusal("stack_unknown", f"no stack named {stack!r} is recorded for {remote_name}")
    else:
        directory, env_name = project_dir or ".", environment
    validated = hosting.validate_manifest(directory, env_name)
    key = hosting.state_key(remote_name, validated)
    if not isinstance((state.get("hosts") or {}).get(key), dict):
        raise ShareRefusal("share_failed", "host is not deployed; run `./sb host apply` before sharing it")
    return ShareTarget(remote=remote_name, validated=validated, entry=entry,
                       label=f"{validated['project']}/{validated['environment']}",
                       project_dir=str(directory), stack=record)


def _declaration(found: ShareTarget) -> dict:
    declaration = found.validated.get("login_service")
    if not declaration or not login.has_site_gate(declaration):
        raise ShareRefusal(
            "share_not_declared",
            "this environment declares no share gate; add a `share:` block (hostname, private, "
            "agent_mint) to its hosting manifest and run `./sb host apply`")
    return declaration


def _entry_name(declaration: dict, requested: str | None) -> str:
    sites = sorted(name for name, entry in declaration["entries"].items() if entry["mode"] == "site_gate")
    if requested:
        if requested not in sites:
            raise ShareRefusal("share_entry_unknown", "site_gate entries are: " + ", ".join(sites))
        return requested
    if share.SHARE_ENTRY in sites:
        return share.SHARE_ENTRY
    if len(sites) == 1:
        return sites[0]
    raise ShareRefusal("share_entry_required", "pass --entry with one of: " + ", ".join(sites))


def _remote_site(found: ShareTarget, verb: str, name: str, *extra: str, input_data: str | None = None) -> dict:
    from sandbox.commands.hosting import _site_remote
    return _site_remote(found.validated, found.entry, name, verb, *extra, input_data=input_data)


def new_link(validated: dict, request: share.LinkRequest) -> share.MintedLink:
    """A fresh id and token, with the expiry clamped to the gate's enabled_until."""
    return share.mint(request, now=time.time(),
                      ends_at=login.enabled_until_epoch(validated["login_service"]))


def mint_link(validated: dict, entry: dict, request: share.LinkRequest, *,
              minted: share.MintedLink | None = None,
              stack_record: dict | None = None) -> tuple[share.MintedLink, dict]:
    """Store one link record (and its stack page) in the running gate.

    Returns the minted link and the gate's confirmation. The token itself goes
    nowhere but the returned value: the record holds its hash, on SSH stdin.
    """
    from sandbox.commands.hosting import _site_remote
    minted = minted or new_link(validated, request)
    payload = {"link": share.link_record(request, minted),
               "stack": stack_page.gate_record(stack_record, request.entry, minted.link_id)
               if stack_record is not None else None}
    reply = _site_remote(validated, entry, request.entry, "mint",
                         input_data=json.dumps(payload, sort_keys=True))
    if reply.get("id") != minted.link_id:
        raise ShareRefusal("share_failed", "the gate did not confirm the link")
    return minted, reply


def _audit(found: ShareTarget | None, op: str, *, remote_name: str, target_label: str, outcome: str,
           code: str | None = None, **fields) -> None:
    """Controller record always; server record when the target is known. Never blocks."""
    record = share.audit_record(
        op, remote=remote_name, target=target_label, outcome=outcome, code=code,
        controller=share.controller_name(),
        owner=share.owner_label(found.project_dir if found else None), **fields)
    try:
        share.append_audit(record)
    except OSError as exc:
        print(f"sb host share: warning: audit record not written: {exc.strerror or exc}", file=sys.stderr)
    if found is None:
        return
    from sandbox.commands.hosting import _remote_checked
    try:
        home = remote.resolve_sandbox_home(found.entry)
        _remote_checked(found.entry, share.remote_audit_command(home, record), timeout=30)
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print("sb host share: warning: server audit record not written: "
              + remote.redact_text(str(exc))[:200], file=sys.stderr)


def _operator_terminal():
    from sandbox.commands.hosting import _operator_terminal as terminal
    return terminal()


def create_share(remote_name: str, *, stack: str | None = None, target: str | None = None,
                 name: str | None = None, project_dir: str | None = None, environment: str | None = None,
                 entry: str | None = None, path: str | None = None, ttl: object = None,
                 uses: object = None, email: str | None = None, caller: str = "cli") -> dict:
    """Mint a share link and return the ``create`` result (contracts/cli.md).

    ``caller`` is ``cli``, ``mcp`` or ``panel``. Only ``cli`` may fall back to the
    terminal when the target has not opted in to agent-made links.
    """
    found = None
    label = target or (f"stack:{stack}" if stack else name) or "unresolved"
    request = None
    try:
        if os.environ.get(JOB_ENV):
            raise ShareRefusal("job_output_refused",
                               "a durable job stores its output, and a share link must not land there")
        found = resolve_target(remote_name, target=target, name=name, stack=stack,
                               project_dir=project_dir, environment=environment)
        label = found.label
        declaration = _declaration(found)
        login.assert_applicable(declaration, now=time.time())
        name_ = _entry_name(declaration, entry)
        if stack and path is not None:
            raise ShareRefusal("share_options_conflict", "--path does not apply to a stack page")
        if stack and name_ != share.SHARE_ENTRY:
            # The page probes the stack's hostnames through the share entry's upstream.
            raise ShareRefusal(
                "stack_share_not_declared",
                f"stack {stack} has no `share:` block in its manifest; add one (hostname, agent_mint) "
                "to the stack template and run `sb host stack up` again")
        if email is not None and not stack:
            raise ShareRefusal("share_options_conflict", "--email applies only to --stack")
        request = share.LinkRequest(
            entry=name_, kind="stack" if stack else "site",
            scope=share.STACK_PAGE_PATH if stack else share.validate_scope(path),
            ttl_seconds=share.parse_ttl(ttl), uses=share.parse_uses(uses))
        agent_mint = bool((login.share_declaration(declaration) or {}).get("agent_mint"))
        terminal = None
        if not agent_mint:
            if caller != "cli" or os.environ.get(CALLER_ENV):
                raise ShareRefusal(
                    "agent_mint_not_enabled",
                    "this target does not allow agent-made links; set `share.agent_mint: true` in its "
                    "manifest and apply, or have the owner run `sb host share create` in a terminal")
            terminal = _operator_terminal()
            if terminal is None:
                raise ShareRefusal(
                    "terminal_required",
                    "this target does not allow agent-made links, so the link is shown only on the "
                    "owner's terminal; set `share.agent_mint: true` to return it in --json")
        stack_record = None
        if stack:
            stack_record = stack_page.build(found, declaration, request_email=email)
        result = _mint_locked(found, request, stack_record)
        minted = result.pop("_minted")
        result["link_id"] = minted.link_id
        link = share.share_url(result["hostname"], minted.link_id, minted.token)
        if terminal is not None:
            with terminal:
                try:
                    terminal.write(f"{link}\n")
                    terminal.flush()
                except OSError:
                    raise ShareRefusal("share_failed", f"the terminal could not be written; revoke "
                                                       f"{minted.link_id} and mint a new link") from None
            result["link_shown_on"] = "terminal"
        else:
            result["link"] = link
        del link
    except Exception as exc:  # noqa: BLE001 -- every refusal is audited, then re-raised
        refusal = _refusal_from(exc)
        _audit(found, "create", remote_name=remote_name, target_label=label, outcome="refused",
               code=refusal.code, entry=getattr(request, "entry", None),
               kind=getattr(request, "kind", None), scope=getattr(request, "scope", None))
        raise refusal from None
    _audit(found, "create", remote_name=remote_name, target_label=label, outcome="ok",
           entry=request.entry, kind=request.kind, link_id=result["link_id"], scope=request.scope,
           expires_at=result["expires_at"], uses=request.uses)
    return {"ok": True, **result}


def _mint_locked(found: ShareTarget, request: share.LinkRequest, stack_record: dict | None) -> dict:
    """Mint under the target's writer lock, so an apply never recreates the gate mid-mint."""
    from sandbox.commands.hosting import _with_host_writer_lock

    def mint(_current, _save):
        minted = new_link(found.validated, request)
        page = None
        if stack_record is not None:
            # The key is named for the link, so revoking the link can find and delete it.
            page = stack_page.with_key(stack_record, minted.link_id)
            stack_page.install_wordpress_key(found, page, minted.expires_at)
        mint_link(found.validated, found.entry, request, minted=minted, stack_record=page)
        return minted

    try:
        minted = _with_host_writer_lock(found.validated, found.remote, mint)
    except TimeoutError:
        raise ShareRefusal("share_failed", "another host apply or recovery owns this target") from None
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        raise ShareRefusal("share_failed", remote.redact_text(str(exc))[:500]) from None
    entry_config = found.validated["login_service"]["entries"][request.entry]
    return {"_minted": minted, "target": found.label, "entry": request.entry, "kind": request.kind,
            "scope": request.scope, "expires_at": minted.expires_at, "expires_clamped": minted.clamped,
            "uses": request.uses, "hostname": entry_config["hostname"],
            **({"notes": stack_record["notes"]} if stack_record and stack_record.get("notes") else {})}


def list_shares(remote_name: str, *, target: str | None = None, name: str | None = None,
                stack: str | None = None, project_dir: str | None = None,
                environment: str | None = None, entry: str | None = None) -> dict:
    found, label, name_ = None, target or name or stack or "unresolved", entry
    try:
        found = resolve_target(remote_name, target=target, name=name, stack=stack,
                               project_dir=project_dir, environment=environment)
        label = found.label
        name_ = _entry_name(_declaration(found), entry)
        reply = _remote_site(found, "list", name_)
    except Exception as exc:  # noqa: BLE001
        refusal = _refusal_from(exc)
        _audit(found, "list", remote_name=remote_name, target_label=label, outcome="refused",
               code=refusal.code, entry=name_)
        raise refusal from None
    _audit(found, "list", remote_name=remote_name, target_label=label, outcome="ok", entry=name_)
    return {"ok": True, "target": label, "entry": name_, "links": list(reply.get("links") or []),
            "sessions": list(reply.get("sessions") or [])}


def revoke_on_gate(found: ShareTarget, name: str, link_id: str) -> dict:
    """Revoke on the gate, then delete a stack link's WordPress key file.

    The gate drops the stack page first. The key file goes even when the gate no longer
    knows the link, so a revoke whose key removal failed can be retried until it is gone.
    Callers hold the host writer lock.
    """
    try:
        reply = _remote_site(found, "revoke", name, link_id)
    except login.LoginServiceError as exc:
        if exc.code != "site_link_unknown" or link_id.startswith("s-"):
            raise
        reply = None
    keys = 0 if link_id.startswith("s-") else stack_page.remove_wordpress_keys(found, link_id)
    if reply is None:
        if not keys:
            raise login.LoginServiceError("site_link_unknown", "no live share link or session has that id")
        reply = {"revoked_links": 0, "revoked_sessions": 0}
    return {**reply, "revoked_keys": keys}


def revoke_share(remote_name: str, link_id: str, *, target: str | None = None, name: str | None = None,
                 stack: str | None = None, project_dir: str | None = None,
                 environment: str | None = None, entry: str | None = None) -> dict:
    found, label, name_ = None, target or name or stack or "unresolved", entry
    try:
        if not isinstance(link_id, str) or not share.REVOKE_TARGET_RE.fullmatch(link_id):
            raise ShareRefusal("site_link_unknown", "pass a link id (l-...), a session id (s-...), or all")
        found = resolve_target(remote_name, target=target, name=name, stack=stack,
                               project_dir=project_dir, environment=environment)
        label = found.label
        name_ = _entry_name(_declaration(found), entry)
        from sandbox.commands.hosting import _with_host_writer_lock

        try:
            reply = _with_host_writer_lock(found.validated, found.remote,
                                           lambda _current, _save: revoke_on_gate(found, name_, link_id))
        except TimeoutError:
            raise ShareRefusal("share_failed", "another host apply or recovery owns this target") from None
    except Exception as exc:  # noqa: BLE001
        refusal = _refusal_from(exc)
        _audit(found, "revoke", remote_name=remote_name, target_label=label, outcome="refused",
               code=refusal.code, entry=name_, link_id=link_id if isinstance(link_id, str) else None)
        raise refusal from None
    _audit(found, "revoke", remote_name=remote_name, target_label=label, outcome="ok", entry=name_,
           link_id=link_id)
    return {"ok": True, "target": label, "entry": name_,
            "revoked_links": reply.get("revoked_links", 0), "revoked_sessions": reply.get("revoked_sessions", 0),
            "revoked_keys": reply.get("revoked_keys", 0)}


def _utc(epoch: object) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def cmd_host_share(args) -> None:
    verb = getattr(args, "image_action", None)
    as_json = bool(getattr(args, "json", False))
    selector = {"target": getattr(args, "target", None), "name": getattr(args, "name", None),
                "stack": getattr(args, "stack", None), "project_dir": getattr(args, "project_dir", None),
                "environment": getattr(args, "environment", None), "entry": getattr(args, "entry", None)}
    try:
        if verb == "create":
            result = create_share(args.remote, path=getattr(args, "share_path", None),
                                  ttl=getattr(args, "ttl", None), uses=getattr(args, "uses", None),
                                  email=getattr(args, "email", None), caller="cli", **selector)
            line = (f"Share link {result['link_id']} for {result['hostname']}"
                    f" ({result['kind']}, scope {result['scope']}), "
                    + (f"{result['uses']} use(s)" if result["uses"] else "no use limit")
                    + f", expires {_utc(result['expires_at'])}"
                    + (" (clamped to enabled_until)" if result["expires_clamped"] else ""))
            text = (line + "; shown on the terminal above") if "link_shown_on" in result \
                else f"{result['link']}\n{line}"
        elif verb == "list":
            result = list_shares(args.remote, **selector)
            text = "\n".join(
                [f"{len(result['links'])} live link(s), {len(result['sessions'])} session(s) for "
                 f"{result['target']} ({result['entry']})"]
                + [f"  {item.get('id')} {item.get('kind')} {item.get('scope')} used {item.get('used')}"
                   f"/{item.get('uses') or 'unlimited'}, expires {_utc(item.get('expires_at'))}"
                   for item in result["links"]]
                + [f"  {item.get('id')} from {item.get('link')}, expires {_utc(item.get('expires_at'))}"
                   for item in result["sessions"]])
        elif verb == "revoke":
            target_id = getattr(args, "share_id", None)
            result = revoke_share(args.remote, target_id, **selector)
            text = (f"Revoked {result['revoked_links']} link(s), {result['revoked_sessions']} session(s) "
                    f"and {result['revoked_keys']} sign-in key(s)")
        else:
            raise ShareRefusal("share_options_conflict", "usage: sb host share create|list|revoke ID|all")
    except ShareRefusal as exc:
        if as_json:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, sort_keys=True))
        else:
            print(f"sb host share: {exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True) if as_json else text)


