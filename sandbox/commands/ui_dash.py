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
_SHARE_ID_RE = re.compile(r"[ls]-[0-9a-f]{32}")
_SHARE_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHARE_KEYS = {"create": frozenset({"action", "remote", "stack"}),
               "list": frozenset({"action", "remote", "stack"}),
               "revoke": frozenset({"action", "remote", "stack", "id"})}
_SHARE_CREATE_FIELDS = ("ok", "link", "link_id", "target", "entry", "kind", "scope", "expires_at",
                        "expires_clamped", "uses", "hostname", "notes")
_SHARE_LINK_FIELDS = ("id", "kind", "scope", "issued_at", "expires_at", "uses", "used")
_SHARE_SESSION_FIELDS = ("id", "link", "created_at", "expires_at")
_SHARE_REVOKE_FIELDS = ("ok", "target", "entry", "revoked_links", "revoked_sessions", "revoked_keys")


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


def _web_share(payload) -> tuple[int, dict]:
    """Create, list or revoke a stack's share links in process, as the panel caller.

    The share functions own validation, the agent opt-in and the audit. Only the
    create result carries ``link``; nothing here prints, logs or keeps it.
    """
    import sandbox.core._remote as remote_core
    from sandbox.commands import host_share
    from sandbox.hosting.share import ShareRefusal

    try:
        action, remote_name, stack, link_id = _share_request(payload)
    except ValueError as exc:
        return 400, {"ok": False, "code": "panel_request_invalid", "message": str(exc)}
    try:
        if action == "create":
            return 200, _pick(host_share.create_share(remote_name, stack=stack, caller="panel"),
                              _SHARE_CREATE_FIELDS)
        if action == "list":
            result = host_share.list_shares(remote_name, stack=stack)
            return 200, {"ok": True, "target": result.get("target"), "entry": result.get("entry"),
                         "links": [_pick(row, _SHARE_LINK_FIELDS) for row in result.get("links") or []
                                   if isinstance(row, dict)],
                         "sessions": [_pick(row, _SHARE_SESSION_FIELDS) for row in result.get("sessions") or []
                                      if isinstance(row, dict)]}
        return 200, _pick(host_share.revoke_share(remote_name, link_id, stack=stack), _SHARE_REVOKE_FIELDS)
    except ShareRefusal as exc:
        code = exc.code if isinstance(exc.code, str) and _SHARE_CODE_RE.fullmatch(exc.code) else "share_failed"
        return 409, {"ok": False, "code": code, "message": remote_core.redact_text(str(exc.message))[:500]}
    except (Exception, SystemExit):  # noqa: BLE001 -- the panel never shows an unexpected error's detail
        return 500, {"ok": False, "code": "share_failed",
                     "message": "the share request failed; run `sb host share` on the controller for details"}


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

        def _send(self, code, body, ctype="application/json", *, no_store=False):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # No page may frame the panel: a framed panel could be clickjacked into
            # its mutating actions, such as "Share this stack".
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            if no_store:
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
