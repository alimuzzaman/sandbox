from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
import time
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register

import ipaddress
from urllib.parse import urlsplit

_TAILNET_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def _ip(value):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_tailnet_ip(value) -> bool:
    address = _ip(value)
    return address is not None and any(address in network for network in _TAILNET_NETWORKS)


def _is_loopback_peer(value) -> bool:
    address = _ip(value)
    if address is not None and getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    return address is not None and address.is_loopback


def _web_bind_host(value: str) -> str:
    """The address `sb web` listens on: an IP literal, or `tailscale` for this machine's tailnet IPv4."""
    if value != "tailscale":
        if _ip(value) is None:
            die("--host must be an IP address or 'tailscale'")
        return value
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True,
                                timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    address = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if not _is_tailnet_ip(address):
        die("tailscale_unavailable: this machine has no Tailscale address; is Tailscale running?")
    return address


_LISTED_HOST_RE = re.compile(r"([A-Za-z0-9.-]{1,253})(?::[0-9]{1,5})?")


def _web_allowed_names(values) -> frozenset[str]:
    """`--allow-host` names: plain DNS names, never an IP, loopback name or wildcard."""
    from sandbox.core._hosting import HostingError, normalize_hostname
    names = set()
    for value in values or []:
        try:
            name = normalize_hostname(str(value), wildcard=False)
        except HostingError:
            die(f"--allow-host must be a host name, got {value!r}")
        if _ip(name) is not None or name in _LOOPBACK_NAMES or "." not in name:
            die(f"--allow-host must be a DNS name such as sandbox.example.test, got {value!r}")
        names.add(name)
    return frozenset(names)


def _web_request_allowed(peer, bound_host, host_header, origin,
                         allowed_names: frozenset[str] = frozenset()) -> bool:
    """Dashboard access: this machine, or any tailnet device when listening on the tailnet.

    The Host and Origin checks stop a web page in the operator's browser from
    driving the dashboard through DNS rebinding or a cross-site form post.
    ``allowed_names`` (`sb web --allow-host`) adds operator-chosen names, for a
    tailnet-only proxy in front of the dashboard. They count only on a
    tailnet-bound address, and their Origin must be exactly ``https://<Host>``.
    Forwarding headers such as X-Forwarded-Host are never read.
    """
    if not (_is_loopback_peer(peer) or (_is_tailnet_ip(bound_host) and _is_tailnet_ip(peer))):
        return False
    try:
        name = urlsplit(f"//{host_header}").hostname if host_header else None
    except ValueError:
        return False
    # A listed name must be the whole Host value (an optional numeric port at
    # most): no userinfo, path or query rides along on the permissive parse above.
    exact = _LISTED_HOST_RE.fullmatch(host_header or "")
    listed = (exact is not None and exact.group(1).lower() in allowed_names
              and _is_tailnet_ip(bound_host))
    if not name or not (name in _LOOPBACK_NAMES or _ip(name) is not None
                        or name.endswith(".ts.net") or listed):
        return False
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
        origin_name = parts.hostname
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not origin_name:
        return False
    if listed:
        return parts.scheme == "https" and parts.netloc.lower() == host_header.lower()
    if parts.netloc.lower() == host_header.lower():
        return True
    # The desktop app's proxy and the web dev server run on this machine and
    # forward a loopback Origin; never trust one from a tailnet device's browser.
    return origin_name in _LOOPBACK_NAMES and _is_loopback_peer(peer)


def _remote_summary_rows() -> list[dict]:
    """Return the lightweight remote list used by the dashboard rail.

    Local instance running-state probes can take several seconds on a machine
    with many registered projects. Remote availability is already present in
    the local registry, so it must be available without waiting for those
    probes or for a remote inventory scan.
    """
    import sandbox.core._remote as remote_core

    registered = remote_core.list_remotes()
    rows = [
        {
            "name": name,
            "provisioned": bool(entry.get("provisioned")),
            "control_ready": bool(
                remote_core.control_ready(entry)
            ),
        }
        for name, entry in sorted(registered.items())
    ]
    if THIS_SERVER not in registered and _hosts_control_service():
        rows.insert(0, {"name": THIS_SERVER, "provisioned": True,
                        "control_ready": True, "local": True})
    return rows


# On a Sandbox remote, the dashboard also shows the host it runs on, built
# in-process like the control service's /inventory, so no token is needed.
THIS_SERVER = "this-server"


def _hosts_control_service() -> bool:
    """True where `sb remote provision` installed a control service for this account."""
    return (Path.home() / ".sandbox" / "mcp-remote.env").exists()


def _remote_view(name: str, deep: bool) -> tuple[int, dict]:
    """One remote's dashboard inventory, or this host's own on a Sandbox remote."""
    import sandbox.core._remote as remote_core

    entry = remote_core.get_remote(name)
    local = entry is None and name == THIS_SERVER and _hosts_control_service()
    if entry is None and not local:
        return 404, {"error": "no such remote"}
    try:
        if local:
            from sandbox.remote_runtime import host_inventory
            inventory = remote_core.redact_structure(
                host_inventory._hosted_inventory_snapshot(deep=deep))
        else:
            inventory = remote_core.remote_inventory(entry, mode="deep" if deep else "fast")
    except RuntimeError as exc:
        return 503, {"ok": False, "inventory_schema": 1, "transport": "control",
                     "evidence_status": "unavailable", "partial_reasons": [str(exc)]}
    inventory["name"] = name
    return 200, inventory


# Stacks and hosted sites (spec 076). One health cache per `sb web` process,
# so every viewer and refresh shares the same bounded probes.
_HOSTING_HEALTH = None
_HOSTING_HEALTH_LOCK = threading.Lock()
_CADDY_ROUTE_DIR = Path("/etc/caddy/conf.d")


def _hosting_health():
    global _HOSTING_HEALTH
    from sandbox.hosting.panel_health import HealthCache
    with _HOSTING_HEALTH_LOCK:
        if _HOSTING_HEALTH is None:
            _HOSTING_HEALTH = HealthCache()
        return _HOSTING_HEALTH


_HOSTING_LIFETIMES = None


def _hosting_lifetimes():
    """One per-remote `sb host stack list` cache per `sb web` process (controller side)."""
    global _HOSTING_LIFETIMES
    from sandbox.core import _paths
    from sandbox.hosting.panel_lifetime import LifetimeCache, stack_list_fetcher
    with _HOSTING_HEALTH_LOCK:
        if _HOSTING_LIFETIMES is None:
            _HOSTING_LIFETIMES = LifetimeCache(stack_list_fetcher(_paths.ENTRY))
        return _HOSTING_LIFETIMES


def _instance_route_file_names() -> list[str]:
    """Names of this host's Caddy instance route files. Their contents are never read."""
    try:
        return sorted(path.name for path in _CADDY_ROUTE_DIR.glob("sandbox-instance-*.caddy"))[:500]
    except OSError:
        return []


# ---- "Share this stack" (spec 081 follow-up, contracts/panel-http.md) ----
# The panel has no login, and a share link is a login bypass. Beyond the panel's
# access gate, a share request must prove it came from the panel's own page: an
# Origin equal to Host, a JSON body and a panel-only header. A cross-site form can
# send neither; a cross-site fetch that tries triggers a preflight nothing answers.
_SHARE_PATH = "/api/hosting/share"
_SIGN_IN_PATH = "/api/hosting/sign-in"
_SHARE_HEADER = "X-Sandbox-Panel"
_SHARE_BODY_MAX = 4096
_SHARE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# One link (short id, or an old-form one until it expires: LEGACY(081 short links)) or session.
_SHARE_ID_RE = re.compile(r"l-[0-9a-f]{12}|[ls]-[0-9a-f]{32}")
_SHARE_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHARE_KEYS = {"create": frozenset({"action", "remote", "stack"}),
               "list": frozenset({"action", "remote", "stack"}),
               "revoke": frozenset({"action", "remote", "stack", "id"})}


def _share_request_allowed(peer, bound_host, headers, allowed_names: frozenset[str] = frozenset()) -> bool:
    """True only for a same-origin JSON request from the panel's page (R10)."""
    host, origin = headers.get("Host"), headers.get("Origin")
    if not host or not origin:
        return False
    if not _web_request_allowed(peer, bound_host, host, origin, allowed_names):
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.netloc.lower() != host.lower() or parts.path or parts.query or parts.fragment:
        return False
    if (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
        return False
    if headers.get(_SHARE_HEADER) != "share":
        return False
    site = headers.get("Sec-Fetch-Site")
    if site is not None and site != "same-origin":
        return False
    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError:
        return False
    return 0 < length <= _SHARE_BODY_MAX


def _share_request(payload) -> tuple[str, str, str, str | None]:
    """(action, remote, stack, id) from a share body; ValueError names what is wrong."""
    import sandbox.core._remote as remote_core

    if not isinstance(payload, dict):
        raise ValueError("the body must be a JSON object")
    action = payload.get("action")
    keys = _SHARE_KEYS.get(action) if isinstance(action, str) else None
    if keys is None:
        raise ValueError("action must be create, list or revoke")
    if set(payload) != keys:
        raise ValueError(f"{action} takes exactly: " + ", ".join(sorted(keys)))
    if not all(isinstance(payload[key], str) for key in keys):
        raise ValueError("every value must be a string")
    remote_name, stack = payload["remote"], payload["stack"]
    try:
        if remote_core.validate_remote_name(remote_name) != remote_name:
            raise ValueError
    except ValueError:
        raise ValueError("remote is not a valid remote name") from None
    if not _SHARE_SLUG_RE.fullmatch(stack):
        raise ValueError("stack is not a valid stack slug")
    link_id = payload.get("id")
    if action == "revoke" and not _SHARE_ID_RE.fullmatch(link_id):
        raise ValueError("id must be one link (l-...) or session (s-...) id")
    return action, remote_name, stack, link_id


def _pick(source: dict, fields) -> dict:
    return {key: source[key] for key in fields if key in source}


# This is a public read model, never a shallow copy of a private gate/ledger body.
_SHARE_PHASES = frozenset({"prepared", "minting", "active", "revoking", "cleanup_pending", "revoked", "expired"})
_SHARE_GUIDANCE = {
    "share_missing": "declare share with a served gate hostname and apply",
    "basic_auth": "remove BasicAuth or provide supported access",
    "private_wordpress": "private WordPress cannot be reached by a public stack grant",
    "inbox_uncovered": "set share.inbox_sign_in to a listed inbox_gate entry and apply",
    "host_invalid": "record an exact lowercase DNS hostname; wildcards are unsupported",
    "host_suffix": "add this hostname to allowed_host_suffixes and apply",
    "linked_access_unknown": "resolve linked-site access metadata before sharing",
    "linked_access_unsupported": "declare supported gate access for the linked-site",
    "gate_uncovered": "list a supported magic_link or inbox_gate action",
    "private_route": "configure supported gate access for private routes",
    "companion_overflow": "declare at most four direct uses",
    "companion_duplicate": "remove duplicate direct uses",
    "remote_mismatch": "use stacks on the same remote",
    "companion_unavailable": "restore the recorded same-remote stack and manifest",
    "agent_mint_not_enabled": "enable share.agent_mint and apply",
    "share_protocol_unsupported": "restore the gate and install all four stack-page capabilities",
    "share_page_unavailable": "resolve declared sign-in email and exact page bounds",
}
_SHARE_MESSAGES = {
    "agent_mint_not_enabled": "set `share.agent_mint: true`",
    "stack_share_not_declared": "declare stack share access and apply",
    "share_runtime_unknown": "original runtime identity could not be confirmed",
    "site_link_unknown": "no live share link or session has that id",
    "share_target_changed": "target changed during share preparation; retry after checking its state",
    "share_window_expired": "every stack needs at least 60 seconds remaining",
    "share_protocol_unsupported": "the gate needs all four stack-page capabilities",
    "share_mint_unknown": "grant creation could not be confirmed; inspect cleanup status",
    "share_page_invalid": "the declared stack page is invalid or exceeds its bounds",
    "login_service_expired": "the declared login service has expired",
    "share_options_conflict": "share options conflict with the declared access policy",
    "share_cleanup_pending": "Original grants require confirmed cleanup before another share.",
    "stack_unknown": "the controller has no available record for this stack",
    "stack_share_coverage_incomplete": "stack access coverage is incomplete",
    "share_failed": "the share request failed; inspect it on the controller",
}
_SHARE_REASONS = frozenset({"cleanup_unconfirmed", "share_runtime_unknown", "share_cleanup_failed",
    "current_parent_unavailable", "original_runtime_unproven", "parent_unavailable", "parent_missing", "parent_unreachable", "gate_unavailable",
    "list_unavailable", "wordpress_cleanup_unknown", "gate_cleanup_unknown", "cleanup_pending", "projection_invalid", "share_failed"})


def _share_name(value, *, target=False, hostname=False):
    if not isinstance(value, str) or len(value) > 253:
        return None
    parts = re.split(r"[./:]", value) if target or hostname else [value]
    if (not parts or len(parts) > 16 or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", part)
            or re.search(r"[0-9a-f]{12,}", part) or len(part) > 32 for part in parts)):
        return None
    if not (target or hostname) and len(parts) != 1:
        return None
    return value


def _share_int(value):
    return value if type(value) is int and 0 <= value <= 253402300799 else None


def _share_id(value, *, attempt=False):
    pattern = r"a-[0-9a-f]{32}" if attempt else _SHARE_ID_RE
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _share_scope(value):
    if value in ("/", "/__sandbox/stack"):
        return value
    if not isinstance(value, str) or len(value) > 512 or not value.startswith("/"):
        return None
    parts = value.strip("/").split("/")
    return value if len(parts) <= 16 and all(_share_name(part) for part in parts) else None


def _share_grants(rows):
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        safe = {"target": _share_name(row.get("target"), target=True), "entry": _share_name(row.get("entry")),
                "id": _share_id(row.get("id")), "expires_at": _share_int(row.get("expires_at")),
                "status": row.get("status") if row.get("status") in ("live", "unknown") else "unknown",
                "reason_code": row.get("reason_code") if isinstance(row.get("reason_code"), str)
                    and row["reason_code"] in _SHARE_REASONS else "cleanup_unconfirmed"}
        if all(value is not None for value in safe.values()):
            result.append(safe)
    return result


def _share_attempts(rows):
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        safe = {"attempt_id": _share_id(row.get("attempt_id"), attempt=True),
                "phase": row.get("phase") if isinstance(row.get("phase"), str) and row["phase"] in _SHARE_PHASES else None,
                "link_id": _share_id(row.get("link_id")), "expires_at": _share_int(row.get("expires_at"))}
        if all(value is not None for value in safe.values()):
            if "still_live" in row:
                safe["still_live"] = _share_grants(row["still_live"])
            elif isinstance(row.get("grants"), list):
                # Controller summaries expose dispositions, never their private cleanup descriptors.
                safe["still_live"] = _share_grants([grant for grant in row["grants"]
                    if isinstance(grant, dict) and grant.get("status") in ("live", "unknown")])
            result.append(safe)
    return result


def _share_gaps(rows):
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        code = row.get("code")
        code = code if isinstance(code, str) and code in _SHARE_GUIDANCE else "coverage_unknown"
        safe = {"stack": _share_name(row.get("stack")) or "unknown",
                "target": _share_name(row.get("target"), target=True) or "unknown",
                "piece": row.get("piece") if row.get("piece") in ("target", "access", "protocol", "page") else "target",
                "code": code, "fix": _SHARE_GUIDANCE.get(code, "inspect stack access coverage on the controller")}
        hostname = _share_name(row.get("hostname"), hostname=True)
        if hostname:
            safe["hostname"] = hostname
        result.append(safe)
    return result


def _share_failure(source):
    code = source.get("code")
    code = code if isinstance(code, str) and code in _SHARE_MESSAGES else "share_failed"
    result = {"ok": False, "code": code, "message": _SHARE_MESSAGES[code]}
    if source.get("phase") == "cleanup_pending":
        result.update(phase="cleanup_pending", still_live=_share_grants(source.get("still_live")))
        attempt_id = _share_id(source.get("attempt_id"), attempt=True)
        if attempt_id:
            result["attempt_id"] = attempt_id
        result["message"] = "Cleanup is pending. Grants may remain live; repeat revoke using the original parent link id."
    if "gaps" in source:
        result["gaps"] = _share_gaps(source["gaps"])
    return result


def _share_rows(rows, *, sessions=False):
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        safe = {"id": _share_id(row.get("id")), "expires_at": _share_int(row.get("expires_at"))}
        if sessions:
            safe.update(link=_share_id(row.get("link")), created_at=_share_int(row.get("created_at")))
        else:
            scope = row.get("scope")
            safe.update(kind=row.get("kind") if row.get("kind") in ("stack", "site") else None,
                        scope=_share_scope(scope),
                        issued_at=_share_int(row.get("issued_at")), used=_share_int(row.get("used")),
                        uses=None if row.get("uses") is None else _share_int(row.get("uses")))
        if all(value is not None for key, value in safe.items() if key != "uses"):
            result.append(safe)
    return result


def _web_share(payload) -> tuple[int, dict]:
    """Project only public share metadata; the create link is the one secret exception."""
    from sandbox.commands import host_share
    from sandbox.hosting.share import ShareRefusal

    try:
        action, remote_name, stack, link_id = _share_request(payload)
    except ValueError as exc:
        return 400, {"ok": False, "code": "panel_request_invalid", "message": str(exc)}
    try:
        if action == "create":
            source = host_share.create_share(remote_name, stack=stack, caller="panel")
        elif action == "list":
            source = host_share.list_shares(remote_name, stack=stack)
        else:
            source = host_share.revoke_share(remote_name, link_id, stack=stack)
        if not isinstance(source, dict):
            raise ValueError("invalid share response")
        if action == "list":
            # Keep durable attempts even when the parent gate cannot be reached.
            result = {"ok": source.get("ok") is True,
                      "links": _share_rows(source.get("links")),
                      "sessions": _share_rows(source.get("sessions"), sessions=True),
                      "attempts": _share_attempts(source.get("attempts"))}
            for key in ("target", "entry"):
                value = _share_name(source.get(key), target=key == "target")
                if value:
                    result[key] = value
            invalid = any((source.get(key) is not None and not isinstance(source[key], list))
                          or len(result[key]) != len(source[key] if isinstance(source.get(key), list) else [])
                          for key in ("links", "sessions", "attempts"))
            unfinished = any(row["phase"] not in ("active", "revoked", "expired") for row in result["attempts"])
            if unfinished:
                result["ok"] = False
            reasons = source.get("partial_reasons")
            if reasons or invalid:
                result["ok"] = False
                result["partial_reasons"] = [reason if isinstance(reason, str) and reason in _SHARE_REASONS
                    else "projection_invalid" for reason in (reasons if isinstance(reasons, list) else [])[:500]]
                if invalid:
                    result["partial_reasons"].append("projection_invalid")
            return (200 if result["ok"] else 409), result
        if source.get("ok") is not True or source.get("phase") == "cleanup_pending" or source.get("still_live"):
            return 409, _share_failure(source)
        result = {"ok": True}
        for key in ("target", "entry", "hostname"):
            if key in source:
                value = _share_name(source[key], target=key == "target", hostname=key == "hostname")
                if value:
                    result[key] = value
        if action == "create":
            # Only this one-shot response may carry a bearer URL. No child/private fields.
            value = source.get("link")
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError("invalid parent link")
            result.update(link=value, link_id=_share_id(source.get("link_id")),
                kind=source.get("kind") if source.get("kind") in ("stack", "site") else "stack",
                scope=_share_scope(source.get("scope")) or "/",
                expires_at=_share_int(source.get("expires_at")), expires_clamped=source.get("expires_clamped") is True,
                uses=_share_int(source.get("uses")))
        else:
            for key in ("revoked_links", "revoked_sessions", "revoked_keys"):
                result[key] = _share_int(source.get(key)) or 0
        attempt_id = _share_id(source.get("attempt_id"), attempt=True)
        if attempt_id:
            result["attempt_id"] = attempt_id
        if isinstance(source.get("phase"), str) and source["phase"] in _SHARE_PHASES:
            result["phase"] = source["phase"]
        return 200, result
    except ShareRefusal as exc:
        # Never stringify exceptions or project arbitrary attributes/messages.
        source = {"code": exc.code}
        if hasattr(exc, "gaps"):
            source["gaps"] = exc.gaps
        return 409, _share_failure(source)
    except (Exception, SystemExit):
        return 500, _share_failure({})


def _web_sign_in(payload) -> tuple[int, dict]:
    from sandbox.hosting import panel_access
    from sandbox.hosting.share import append_audit, audit_record

    if not isinstance(payload, dict) or set(payload) != {'remote', 'stack'}:
        return 400, {'ok': False, 'code': 'panel_request_invalid',
                     'message': 'sign-in takes exactly remote and stack'}
    try:
        _, remote_name, stack, _ = _share_request({'action': 'create', **payload})
    except ValueError as exc:
        return 400, {'ok': False, 'code': 'panel_request_invalid', 'message': str(exc)}
    def audit(outcome):
        append_audit(audit_record('sign_in', remote=remote_name, target=stack,
                                 entry=None, outcome=outcome, controller='panel', owner='panel'))
    try:
        # Record intent before the effect. Audit failure must not silently mint.
        audit('requested')
        result = panel_access.sign_in(remote_name, stack)
        audit('succeeded')
        return 200, {'ok': True, **_pick(result, ('url', 'expires_at', 'one_time', 'user'))}
    except panel_access.SignInUnavailable as exc:
        try:
            audit('refused')
        except Exception:
            pass
        return 409, {'ok': False, 'code': exc.code, 'message': exc.message}
    except (Exception, SystemExit):
        try:
            audit('failed')
        except Exception:
            pass
        return 409, {'ok': False, 'code': 'sign_in_unavailable',
                     'message': 'Sign-in unavailable. Check the recorded WordPress autologin declaration and remote.'}


def _hosting_view() -> tuple[int, dict]:
    """The read-only stacks and hosted sites view: this server's records, then the controller's."""
    import sandbox.core._hosting as hosting_core
    import sandbox.core._remote as remote_core
    from sandbox.core import _paths
    from sandbox.hosting import panel_lifetime, panel_records, panel_view, stack_registry

    try:
        sources, partial, known, lifetimes = [], [], [], {}
        now = int(time.time())
        if _hosts_control_service():
            sources.append(panel_view.server_source(
                panel_records.read_server_records(_paths.RUNTIME_DIR)))
            lifetimes["this-server"] = panel_lifetime.read_server(_paths.RUNTIME_DIR, now)
            try:
                rows = list(_core().registry_all().values())
            except Exception:
                rows = []
                partial.append("instance_registry_unavailable")
            known = panel_view.known_sites(rows, _instance_route_file_names())
        try:
            stack_data = stack_registry.read()
        except Exception:
            stack_data = {}
            partial.append("stack_registry_unavailable")
        try:
            host_state = hosting_core.load_host_state()
        except Exception:
            host_state = {}
            partial.append("hosting_state_unavailable")
        if stack_data.get("stacks") or host_state.get("hosts"):
            from sandbox.commands.host_stack import _last_apply_view
            controller = panel_view.controller_records(stack_data, host_state, _last_apply_view)
            sources.append(controller)
            # Lifetimes per remote come from `sb host stack list --remote R --json`,
            # fetched in the background; the page never waits for a remote.
            remotes = sorted({target["remote"] for target in controller["targets"]})
            lifetimes.update(_hosting_lifetimes().snapshot(remotes, now))
        view = panel_view.build_view(sources, known=known, health=_hosting_health().snapshot,
                                     lifetimes=lifetimes)
        from sandbox.hosting import panel_access
        panel_access.enrich(view, stack_data, host_state)
        view["partial_reasons"] = partial + view["partial_reasons"]
        return 200, remote_core.redact_structure(view)
    except Exception:
        return 500, {"ok": False, "error": "hosting view unavailable"}



def cmd_dashboard(cfg, args) -> None:
    from sandbox.commands.instances_cmd import cmd_instances
    """Interactive full-screen TUI to view + drive all sandbox instances."""
    if not sys.stdout.isatty():
        info("dashboard needs an interactive terminal — showing static list.")
        cmd_instances(cfg, args)
        return
    import curses
    curses.wrapper(_dash_run, cfg)

def cmd_web(cfg, args) -> None:
    """Serve the browser dashboard: this machine only by default, or the tailnet with `--host tailscale`."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket

    port = getattr(args, "port", None) or 8765
    requested_hosts = getattr(args, "host", None) or ["127.0.0.1"]
    if isinstance(requested_hosts, str):
        requested_hosts = [requested_hosts]
    hosts = list(dict.fromkeys(_web_bind_host(value) for value in requested_hosts))
    host = hosts[0]
    allowed_names = _web_allowed_names(getattr(args, "allow_host", None))
    if allowed_names and not any(map(_is_tailnet_ip, hosts)):
        info("--allow-host is ignored: it applies only while listening on a tailnet address")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):     # quiet — don't spam the console
            pass

        def end_headers(self):
            # Also cover BaseHTTPRequestHandler's unsupported-method errors.
            if self.path.split("?", 1)[0] == _SHARE_PATH:
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send(self, code, body, ctype="application/json", *, no_store=False):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # No page may frame the panel: a framed panel could be clickjacked into
            # its mutating actions, such as "Share this stack".
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            if no_store and self.path.split("?", 1)[0] != _SHARE_PATH:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_page(self):
            page = (_WEB_PAGE
                    .replace("__SANDBOX_WEB_CSS__", _web_css())
                    .replace("__SANDBOX_WEB_JS__", _web_js()))
            return self._send(200, page, "text/html; charset=utf-8")

        def _bridge(self, method, body=None):
            # Token-authed snapshot bridge for the wp-admin mu-plugin (spec 002):
            #   /api/instance/<inst>/{snapshots,snapshot,restore,snapshot/<name>,job/<id>}
            from urllib.parse import urlparse, unquote
            rest = urlparse(self.path).path[len("/api/instance/"):]
            inst, _, sub = rest.partition("/")
            code, data = _bridge_handle(method, unquote(inst), "/" + sub,
                                        body or {}, self.headers.get("Authorization", ""))
            return self._send(code, json.dumps(data))

        def _local(self):
            # The dashboard routes have no login, so they're restricted to this
            # machine, or to tailnet devices when listening on the tailnet. The
            # /api/instance/ bridge routes are token-gated (see _bridge_handle)
            # and may be reached from a container via the docker host-gateway,
            # so they are NOT restricted this way.
            return _web_request_allowed(self.client_address[0], self.server.server_address[0],
                                        self.headers.get("Host"), self.headers.get("Origin"),
                                        allowed_names)

        def _share(self):
            if not _share_request_allowed(self.client_address[0], self.server.server_address[0],
                                          self.headers, allowed_names):
                return self._send(403, json.dumps({
                    "ok": False, "code": "panel_request_refused",
                    "message": "share requests must come from the panel page"}), no_store=True)
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except (ValueError, RecursionError):  # deep nesting fits in 4 KiB
                payload = None
            code, result = (_web_sign_in(payload) if self.path == _SIGN_IN_PATH else _web_share(payload))
            return self._send(code, json.dumps(result), no_store=True)

        def do_DELETE(self):
            if self.path.startswith("/api/instance/"):
                return self._bridge("DELETE")
            return self._send(404, json.dumps({"error": "not found"}))

        def do_GET(self):
            # Split path + query.
            from urllib.parse import urlparse, parse_qs, unquote
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)

            if not path.startswith("/api/instance/") and not self._local():
                return self._send(403, json.dumps({"error": "forbidden"}))

            if path == "/api/instances":
                cfg = load_config()
                # Per-project model: the "plugins" list is the set of projects
                # the registry tracks (each project dir = its plugin), not a
                # central catalog. "projects" is gone entirely.
                reg_plugins = sorted({
                    Path(e["root"]).name
                    for e in _core().registry_all().values() if e.get("root")
                })
                return self._send(200, json.dumps({
                    "instances": collect_instance_rows(cfg),
                    "plugins": reg_plugins,
                    "servers": list(SERVERS),
                    "seeds": _web_list_seeds(),
                    "domains_ready": domains_ready(),
                    "remotes": _remote_summary_rows(),
                }))
            if path == "/api/hosting":
                if u.query:
                    return self._send(400, json.dumps({"error": "no query parameters are supported"}))
                code, view = _hosting_view()
                return self._send(code, json.dumps(view), no_store=True)
            if path == "/api/remotes":
                return self._send(200, json.dumps({
                    "remotes": _remote_summary_rows(),
                }), no_store=True)
            if path.startswith("/api/remote/"):
                import sandbox.core._remote as remote_core
                name = unquote(path[len("/api/remote/"):])
                try:
                    name = remote_core.validate_remote_name(name)
                except ValueError:
                    return self._send(400, json.dumps({"error": "invalid remote name"}))
                deep = qs.get("deep") == ["1"]
                if qs and any(key != "deep" or values != ["1"]
                             for key, values in qs.items()):
                    return self._send(400, json.dumps({"error": "only deep=1 is supported"}))
                code, inventory = _remote_view(name, deep)
                return self._send(code, json.dumps(inventory), no_store=(code == 200))
            if path.startswith("/api/job/"):
                jid = path.rsplit("/", 1)[-1]
                offset = int((qs.get("offset") or ["0"])[0])
                snap = _job_snapshot(jid, offset)
                if snap is None:
                    return self._send(404, json.dumps({"error": "no such job"}))
                return self._send(200, json.dumps(snap))
            if path.startswith("/api/snapshots/"):
                inst = path.rsplit("/", 1)[-1]
                return self._send(200, json.dumps(
                    {"snapshots": _web_list_snapshots(inst)}))
            if path.startswith("/api/instance/"):
                return self._bridge("GET")
            if path == "/api/usage":
                cfg = load_config()
                insts = list(resolve_instances(cfg).keys())
                return self._send(200, json.dumps(claude_usage(insts)))
            if path.startswith("/api/"):
                return self._send(404, json.dumps({"error": "not found"}))
            # SPA fallback: any non-API path serves the app shell; the client
            # router renders the right view from location.pathname. Lets deep
            # links + hard-refresh on /instance/<name> and /usage work.
            return self._serve_page()

        def do_POST(self):
            if self.path.startswith("/api/instance/"):
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, json.dumps({"ok": False, "error": "bad JSON"}))
                return self._bridge("POST", body)
            if self.path in {_SHARE_PATH, _SIGN_IN_PATH}:
                # _share_request_allowed applies the _local() gate first, then its own checks,
                # so every share response (refusals included) is answered no-store.
                return self._share()
            if not self._local():
                return self._send(403, json.dumps({"error": "forbidden"}))
            if self.path != "/api/action":
                return self._send(404, json.dumps({"error": "not found"}))
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, json.dumps({"ok": False,
                                                   "output": "bad JSON"}))
            result = _web_do_action(payload)
            code = 200 if result.get("ok") or result.get("job_id") else 400
            return self._send(code, json.dumps(result))

    # Bind the requested port; if it's busy (a previous `./sb web` still up, or
    # anything else), auto-pick the next free one instead of crashing.
    requested = port
    # The snapshot-bridge auto-start also listens on the Docker gateway so a
    # container can reach it via the docker host-gateway; dashboard routes stay
    # gated by _local, and the /api/instance/ bridge routes are
    # per-instance-token-gated. `--exact-port` (used by the bridge auto-start)
    # binds only the requested port instead of scanning, so the bridge never
    # lands on a different port than the mu-plugin expects. Every address
    # shares one port.
    exact = getattr(args, "exact_port", False)

    def server_class(address):
        if ":" not in address:
            return ThreadingHTTPServer
        return type("ThreadingHTTPServerV6", (ThreadingHTTPServer,),
                    {"address_family": socket.AF_INET6})

    servers = []
    for cand in range(requested, requested + (1 if exact else 20)):
        try:
            for address in hosts:
                servers.append(server_class(address)((address, cand), Handler))
            port = cand
            break
        except OSError:
            for server in servers:
                server.server_close()
            servers = []
    httpd = servers[0] if servers else None
    if httpd is None:
        die(f"couldn't bind port {requested}"
            + ("" if exact else f"–{requested+19}")
            + f". Something may be stuck — try: lsof -iTCP:{requested} -sTCP:LISTEN")
    if port != requested:
        info(f"port {requested} was busy — using {port} instead "
             f"(another `./sb web` may already be running).")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else (f"[{host}]" if ":" in host else host)
    url = f"http://{shown}:{port}"
    reach = "any device on your tailnet" if any(map(_is_tailnet_ip, hosts)) else "this machine only"
    ok(f"Sandbox web UI: {url}  (dashboard: {reach}, full control — Ctrl-C to stop)")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if getattr(args, "open", False):
        run([opener, url], check=False)
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        info("shutting down web UI")
        for server in servers:
            server.shutdown()

register({
    'dashboard': cmd_dashboard,
    'ui': cmd_dashboard,
    'web': cmd_web,
})
