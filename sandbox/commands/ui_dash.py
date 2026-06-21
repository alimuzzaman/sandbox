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
    """Serve a local browser dashboard for the sandbox (localhost only)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = getattr(args, "port", None) or 8765

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):     # quiet — don't spam the console
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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

        def do_DELETE(self):
            if self.path.startswith("/api/instance/"):
                return self._bridge("DELETE")
            return self._send(404, json.dumps({"error": "not found"}))

        def do_GET(self):
            # Split path + query.
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)

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
                }))
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
    httpd = None
    for cand in range(requested, requested + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", cand), Handler)
            port = cand
            break
        except OSError:
            continue
    if httpd is None:
        die(f"couldn't bind a port in {requested}–{requested+19}. "
            f"Something may be stuck — try: lsof -iTCP:{requested} -sTCP:LISTEN")
    if port != requested:
        info(f"port {requested} was busy — using {port} instead "
             f"(another `./sb web` may already be running).")
    url = f"http://127.0.0.1:{port}"
    ok(f"Sandbox web UI: {url}  (localhost only — Ctrl-C to stop)")
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
