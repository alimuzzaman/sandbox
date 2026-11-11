"""Stack-page metadata and explicit sign-in actions for recorded controller targets."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from sandbox.hosting import panel_view
from sandbox.hosting.tailnet_names.models import TailnetNameError
from sandbox.hosting.tailnet_names.state import StateStore, default_root

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


class SignInUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _sign_in_manifest(remote_name: str, slug: str, **recorded):
    import sandbox.core._hosting as hosting
    from sandbox.hosting import login_service, target_resolution
    try:
        resolved = target_resolution.resolve(remote_name, name=slug, **recorded)
        validated = hosting.validate_manifest(resolved.project_dir, resolved.environment)
    except Exception:
        raise SignInUnavailable('sign_in_manifest_unavailable',
            'Rendered manifest unavailable on this controller. Reapply the recorded stack with sb host stack up.') from None
    if (validated['project'], validated['environment']) != (resolved.project, resolved.environment):
        raise SignInUnavailable('sign_in_target_mismatch',
            'Rendered manifest identity changed. Restore or reapply this recorded stack before signing in.')
    if not validated.get('autologin'):
        raise SignInUnavailable('sign_in_not_declared',
            'No WordPress admin sign-in declared. Add autologin user, service, container_path and request_path to this stack template, then reapply.')
    primary = next((route for route in validated['routes'] if route.get('primary')), None)
    if primary is None or login_service.is_private(validated.get('login_service')):
        raise SignInUnavailable('sign_in_private_route',
            'WordPress is behind a private gate. Panel sign-in handoff is not available yet; use the existing stack access link.')
    return validated


def sign_in_availability(stack, stack_data, host_state) -> panel_view.SignInAvailability:
    if stack['source'] != 'controller':
        return {'available': False, 'code': 'sign_in_controller_required',
                'message': 'Open this stack on its controller panel to sign in. This server has no recorded controller manifest.'}
    try:
        _sign_in_manifest(stack['remote'], stack['slug'], registry=stack_data, state=host_state)
        return {'available': True, 'code': 'sign_in_available', 'message': 'Sign in to WordPress admin'}
    except SignInUnavailable as exc:
        return {'available': False, 'code': exc.code, 'message': exc.message}


def sign_in(remote_name: str, slug: str) -> dict:
    """Mint once for a recorded stack, using the CLI's target writer boundary."""
    import sandbox.core._remote as remote
    from sandbox.commands import hosting as command
    validated = _sign_in_manifest(remote_name, slug)
    entry = remote.get_remote(remote_name)
    if not entry or not entry.get('provisioned'):
        raise SignInUnavailable('sign_in_remote_unavailable',
            'The recorded remote is unavailable. Restore its controller registration before signing in.')
    return command._with_host_writer_lock(
        validated, remote_name,
        lambda state, save: command._issue_host_autologin(
            validated, entry, remote_name, state, None, save_state=save))


def inbox_names(remote: str) -> dict[int, str]:
    """Recorded active loopback names by port, without remote probes or credentials."""
    names: dict[int, str] = {}
    for name, record in sorted(StateStore(default_root(), remote).load().items()):
        upstream = urlsplit("http://" + record.upstream)
        if record.state == "published" and upstream.hostname in {"127.0.0.1", "localhost", "::1"}:
            if upstream.port is not None:
                names.setdefault(upstream.port, name)
    return names


def enrich(view: panel_view.HostingView, stack_data: dict, host_state: dict) -> None:
    """Add bounded direct relationships and safe access metadata to the polling view."""
    records = stack_data.get("stacks") or {}
    hosts = host_state.get("hosts") or {}
    names_by_remote: dict[str, dict[int, str]] = {}
    for stack in view["stacks"]:
        remote, slug = stack["remote"], stack["slug"]
        record = (records.get(f"{remote}/{slug}") or {}) if stack["source"] == "controller" else {}
        uses = record.get("uses") or []
        uses = [name for name in uses[:4] if isinstance(name, str) and _SLUG.fullmatch(name)] \
            if isinstance(uses, list) else []
        if remote not in names_by_remote:
            try:
                names_by_remote[remote] = inbox_names(remote)
            except (TailnetNameError, OSError, ValueError):
                names_by_remote[remote] = {}
                view["partial_reasons"].append(f"inbox_names_unavailable:{remote}")
        port = stack["mailpit_port"]
        name = names_by_remote[remote].get(port) if port else None
        reason = None
        if not name:
            reason = (f"No inbox address recorded. Run sb host tailnet-name add --remote {remote} "
                      f"<inbox-name> --upstream 127.0.0.1:{port}."
                      if port else "This stack declares no mailpit port.")
        evidence = "verified" if stack["last_apply"]["status"] == "succeeded" else "unverified"
        if stack["source"] == "controller":
            entry = hosts.get(f"{remote}/{stack['project']}/{stack['environment']}") or {}
            evidence = "verified" if entry.get("observed_runtime_revision") == stack["commit"] else "unverified"
        if not stack["commit"]:
            evidence = "not_delivered"
        stack["page_access"] = {"uses": uses, "inbox": {"hostname": name, "url": f"https://{name}"}
                                if name else None, "inbox_reason": reason, "revision_status": evidence,
                                "sign_in": sign_in_availability(stack, stack_data, host_state)}
