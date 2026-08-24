"""Loopback-only HTTP server for the activation application."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .http import ActivationHTTPApplication


def serve(application: ActivationHTTPApplication, *, host: str = "127.0.0.1",
          port: int = 8766) -> None:
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("activation service must bind to loopback")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("activation service port is invalid")

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(min(length, 1)) if length else b""
            response = application.handle(self.command, self.path, dict(self.headers), body)
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle

        def log_message(self, _format: str, *_args: object) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


__all__ = ["serve"]
