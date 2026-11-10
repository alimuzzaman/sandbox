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


def _web_request_allowed(peer, bound_host, host_header, origin) -> bool:
    """Dashboard access: this machine, or any tailnet device when listening on the tailnet.

    The Host and Origin checks stop a web page in the operator's browser from
    driving the dashboard through DNS rebinding or a cross-site form post.
    """
    if not (_is_loopback_peer(peer) or (_is_tailnet_ip(bound_host) and _is_tailnet_ip(peer))):
        return False
    try:
        name = urlsplit(f"//{host_header}").hostname if host_header else None
    except ValueError:
        return False
    if not name or not (name in _LOOPBACK_NAMES or _ip(name) is not None or name.endswith(".ts.net")):
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

    return [
        {
            "name": name,
            "provisioned": bool(entry.get("provisioned")),
            "control_ready": bool(
                entry.get("control_url") and entry.get("bearer_token")
            ),
        }
        for name, entry in sorted(remote_core.list_remotes().items())
    ]



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
    host = _web_bind_host(getattr(args, "host", None) or "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):     # quiet — don't spam the console
            pass

        def _send(self, code, body, ctype="application/json", *, no_store=False):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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
            return _web_request_allowed(self.client_address[0], host,
                                        self.headers.get("Host"), self.headers.get("Origin"))

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
                entry = remote_core.get_remote(name)
                if not entry:
                    return self._send(404, json.dumps({"error": "no such remote"}))
                deep = qs.get("deep") == ["1"]
                if qs and any(key != "deep" or values != ["1"]
                             for key, values in qs.items()):
                    return self._send(400, json.dumps({"error": "only deep=1 is supported"}))
                try:
                    inventory = remote_core.remote_inventory(
                        entry, mode="deep" if deep else "fast",
                    )
                except RuntimeError as exc:
                    return self._send(503, json.dumps({
                        "ok": False, "inventory_schema": 1, "transport": "control",
                        "evidence_status": "unavailable", "partial_reasons": [str(exc)],
                    }))
                inventory["name"] = name
                return self._send(200, json.dumps(inventory), no_store=True)
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
    # The snapshot-bridge auto-start passes --host 0.0.0.0 so a container can
    # reach it via the docker host-gateway; dashboard routes stay gated by
    # _local, and the /api/instance/ bridge routes are per-instance-token-gated.
    # `--exact-port` (used by the bridge auto-start) binds only the requested
    # port instead of scanning, so the bridge never lands on a different port
    # than the mu-plugin expects.
    exact = getattr(args, "exact_port", False)
    server_class = ThreadingHTTPServer
    if ":" in host:
        server_class = type("ThreadingHTTPServerV6", (ThreadingHTTPServer,),
                            {"address_family": socket.AF_INET6})
    httpd = None
    for cand in range(requested, requested + (1 if exact else 20)):
        try:
            httpd = server_class((host, cand), Handler)
            port = cand
            break
        except OSError:
            continue
    if httpd is None:
        die(f"couldn't bind port {requested}"
            + ("" if exact else f"–{requested+19}")
            + f". Something may be stuck — try: lsof -iTCP:{requested} -sTCP:LISTEN")
    if port != requested:
        info(f"port {requested} was busy — using {port} instead "
             f"(another `./sb web` may already be running).")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else (f"[{host}]" if ":" in host else host)
    url = f"http://{shown}:{port}"
    reach = "any device on your tailnet" if _is_tailnet_ip(host) else "this machine only"
    ok(f"Sandbox web UI: {url}  (dashboard: {reach}, full control — Ctrl-C to stop)")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if getattr(args, "open", False):
        run([opener, url], check=False)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        info("shutting down web UI")
        httpd.shutdown()

register({
    'dashboard': cmd_dashboard,
    'ui': cmd_dashboard,
    'web': cmd_web,
})
