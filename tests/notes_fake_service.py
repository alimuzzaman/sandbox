"""An in-memory Agent Notes service for ``sb notes`` / ``sb mockups`` tests.

It answers the MCP tools with the shapes of agent-notes ``src/mcp.php``, serves
note assets, and implements the dashboard token forms (with a CSRF value bound
to the session cookie) the way ``public/index.php`` and ``views/*.php`` do.
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import secrets
import threading
import urllib.parse

_SIGS = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif"}


class FakeNotes:
    def __init__(self) -> None:
        self.notes: dict[str, dict] = {}
        self.assets: dict[str, dict[str, bytes]] = {}
        self.links: list[dict] = []
        self.tokens: list[dict] = []          # {"id", "name", "value", "revoked"}
        self.calls: list[str] = []
        self.trusted = True
        self.redirect_mcp = False          # answer /mcp with a 302 to /sink
        self.sink_headers: list[dict] = []
        self.sessions: set[str] = set()
        self._seq = 0
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def add_token(self, name: str) -> str:
        self._seq += 1
        value = "an_" + secrets.token_hex(24)
        self.tokens.append({"id": self._seq, "name": name, "value": value, "revoked": False})
        return value

    def live_tokens(self, name: str | None = None) -> list[dict]:
        return [t for t in self.tokens if not t["revoked"] and (name is None or t["name"] == name)]

    # -- MCP ----------------------------------------------------------------

    def _summary(self, note: dict, content: bool = False) -> dict:
        out = {k: note[k] for k in ("slug", "title", "format", "visibility", "created_at",
                                    "updated_at")}
        out["filename"] = note["filename"] + (".html" if note["format"] == "html" else ".md")
        out["size_bytes"] = len(note["content"].encode())
        out["url"] = f"{self.url}/n/{note['slug']}"
        if content:
            out["content"] = note["content"]
        return out

    def _asset_summary(self, slug: str, name: str) -> dict:
        path = f"/n/{slug}/assets/{name}"
        return {"name": name, "path": path, "url": self.url + path,
                "size_bytes": len(self.assets[slug][name])}

    def tool(self, name: str, args: dict) -> tuple[dict, bool]:
        self.calls.append(name)
        self._seq += 1
        stamp = "2026-11-18 10:00:%02d" % (self._seq % 60)
        slug = args.get("slug")
        note = self.notes.get(slug) if isinstance(slug, str) else None
        if name == "create_note":
            slug = secrets.token_hex(5)
            note = {"slug": slug, "title": args["title"], "content": args["content"],
                    "format": args.get("format", "markdown"),
                    "visibility": args.get("visibility", "tailnet"),
                    "filename": _slugify(args.get("filename") or args["title"]),
                    "created_at": stamp, "updated_at": stamp}
            self.notes[slug] = note
            self.assets[slug] = {}
            return {"message": "Note published.", **self._summary(note)}, False
        if name == "list_notes":
            ordered = sorted(self.notes.values(), key=lambda n: n["updated_at"], reverse=True)
            offset, limit = int(args.get("offset", 0)), min(int(args.get("limit", 50)), 200)
            page = ordered[offset:offset + limit]
            return {"count": len(page), "notes": [self._summary(n) for n in page]}, False
        if name == "list_share_links":
            links = [dict(link) for link in self.links if not slug or link["slug"] == slug]
            return {"count": len(links), "links": links}, False
        if name == "revoke_share_link":
            for link in self.links:
                if link["id"] == args.get("id") and link["status"] == "active":
                    link["status"] = "revoked"
                    return {"message": "Share link revoked."}, False
            return {"error": "no active share link with that id on your notes"}, True
        if note is None:
            return {"error": f"note '{slug}' not found"}, True
        if name == "update_note":
            for key in ("title", "content", "format", "visibility"):
                if key in args:
                    note[key] = args[key]
            if "filename" in args:
                note["filename"] = _slugify(args["filename"])
            note["updated_at"] = stamp
            return {"message": "Note updated.", **self._summary(note)}, False
        if name == "get_note":
            out = self._summary(note, content=True)
            out["assets"] = [self._asset_summary(slug, a) for a in sorted(self.assets[slug])]
            return out, False
        if name == "delete_note":
            del self.notes[slug]
            del self.assets[slug]
            return {"message": f"Note '{slug}' deleted."}, False
        if name == "upload_asset":
            data = base64.b64decode(args["data"])
            ext = next((e for sig, e in _SIGS.items() if data.startswith(sig)), None)
            if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                ext = "webp"
            if ext is None:
                return {"error": "only PNG, JPEG, GIF and WebP images are accepted"}, True
            asset = hashlib.sha256(data).hexdigest()[:16] + "." + ext
            self.assets[slug][asset] = data
            return {"message": "Asset stored.", **self._asset_summary(slug, asset)}, False
        if name == "delete_asset":
            if self.assets[slug].pop(args.get("name"), None) is None:
                return {"error": "no such asset on this note"}, True
            return {"message": "Asset deleted."}, False
        if name == "create_share_link":
            ident = len(self.links) + 1
            link = {"id": ident, "slug": slug, "title": note["title"], "status": "active",
                    "expires_at": "in %dh" % args.get("ttl_hours", 24), "use_count": 0}
            self.links.append(link)
            return {"message": "Share link created.", **link,
                    "url": "https://share.example/s/" + secrets.token_hex(16)}, False
        return {"error": "unknown"}, True

    # -- HTTP ---------------------------------------------------------------

    def _handler(self):
        service = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, ctype="application/json", headers=()):
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def _session(self):
                cookie = self.headers.get("Cookie") or ""
                for part in cookie.split(";"):
                    key, _, value = part.strip().partition("=")
                    if key == "an_session" and value in service.sessions:
                        return value
                return None

            def _csrf(self, session):
                return hashlib.sha256(("csrf" + session).encode()).hexdigest()[:20] + "&x"

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path == "/sink":
                    service.sink_headers.append(dict(self.headers))
                    return self._send(200, "{}")
                if path.startswith("/n/") and "/assets/" in path:
                    _, _, slug, _, name = path.split("/", 4)
                    data = service.assets.get(slug, {}).get(name)
                    return self._send(200, data, "image/png") if data else self._send(404, "")
                if path == "/dashboard":
                    session = self._session()
                    headers = []
                    if session is None:
                        if not service.trusted:
                            return self._send(302, "", "text/html", [("Location", "/login")])
                        session = secrets.token_hex(8)
                        service.sessions.add(session)
                        headers.append(("Set-Cookie", f"an_session={session}; Path=/"))
                    return self._send(200, service.dashboard(self._csrf(session)), "text/html", headers)
                if path == "/login":
                    return self._send(200, "<form>login</form>", "text/html")
                return self._send(404, "")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                path = urllib.parse.urlparse(self.path).path
                if path == "/sink":
                    service.sink_headers.append(dict(self.headers))
                    return self._send(200, "{}")
                if path == "/mcp" and service.redirect_mcp:
                    return self._send(302, "", "text/html", [("Location", "/sink")])
                if path == "/mcp":
                    auth = self.headers.get("Authorization") or ""
                    live = {t["value"] for t in service.live_tokens()}
                    if not auth.startswith("Bearer ") or auth[7:] not in live:
                        return self._send(401, json.dumps({"jsonrpc": "2.0", "id": None,
                                                           "error": {"code": -32001,
                                                                     "message": "Unauthorized"}}))
                    request = json.loads(raw)
                    params = request.get("params") or {}
                    data, is_error = service.tool(params.get("name"), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(data)}]}
                    if is_error:
                        result["isError"] = True
                    return self._send(200, json.dumps({"jsonrpc": "2.0", "id": request.get("id"),
                                                       "result": result}))
                session = self._session()
                form = dict(urllib.parse.parse_qsl(raw.decode()))
                if session is None or form.get("_csrf") != self._csrf(session):
                    return self._send(419, "CSRF token mismatch", "text/plain")
                if path == "/tokens/revoke":
                    for token in service.tokens:
                        if str(token["id"]) == form.get("id"):
                            token["revoked"] = True
                    return self._send(302, "", "text/html", [("Location", "/dashboard")])
                if path == "/tokens/create":
                    value = service.add_token(form.get("name") or "token")
                    return self._send(200, service.token_page(value), "text/html")
                return self._send(404, "")

        return Handler

    def dashboard(self, csrf: str) -> str:
        """The API-token block of views/dashboard.php, rendered."""
        rows = []
        for token in self.live_tokens():
            rows.append(
                '<div class="flex flex-wrap items-center gap-2 sm:gap-3 px-4 py-3">\n'
                '<div class="min-w-0 flex-1">\n'
                f'<span class="font-medium">{html.escape(token["name"])}</span>\n'
                f'<code class="text-xs">{token["value"][:12]}…</code>\n</div>\n'
                '<form method="post" action="/tokens/revoke" onsubmit="return confirm(\'Revoke?\')">\n'
                f'<input type="hidden" name="_csrf" value="{html.escape(csrf)}">\n'
                f'<input type="hidden" name="id" value="{token["id"]}">\n'
                '<button>Revoke</button>\n</form>\n</div>')
        return ("<html><body><h2>Notes</h2><span class=\"font-medium\">a note title</span>"
                "<h2>API tokens</h2>" + "\n".join(rows)
                + '<form method="post" action="/tokens/create">'
                f'<input type="hidden" name="_csrf" value="{html.escape(csrf)}">'
                '<input type="text" name="name"></form></body></html>')

    def token_page(self, value: str) -> str:
        return (f'<pre class="text-sm font-mono text-emerald-300">{value}</pre>'
                f'<pre>claude mcp add ... --header "Authorization: Bearer {value}"</pre>')


def _slugify(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:60] or "note"   # src/notes.php slugify cuts to 60 characters
