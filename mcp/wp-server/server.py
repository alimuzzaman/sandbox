from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import mcp



import tools.instances  # noqa: F401
import tools.wp  # noqa: F401
import tools.net  # noqa: F401
import tools.data  # noqa: F401
import tools.fs  # noqa: F401
import tools.mail  # noqa: F401
import tools.context  # noqa: F401
import tools.cache  # noqa: F401
import tools.abilities  # noqa: F401
import tools.skills  # noqa: F401
import tools.debug  # noqa: F401
import tools.e2e  # noqa: F401
import tools.ci  # noqa: F401
import tools.asyncjobs  # noqa: F401
import tools.plugin_check  # noqa: F401
import tools.remote  # noqa: F401



def _parse_transport_args(argv):
    """Parse the --transport/--bind/--port/--token flags cmd_mcp (spec 014)
    passes through argv. Returns a dict; transport defaults to stdio when no
    flags are given at all -- this is what keeps every existing local
    invocation of `sb mcp` byte-identical (spec FR-015)."""
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--bind", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--token", default=None)
    ns, _ = p.parse_known_args(argv)
    return vars(ns)


def _run_streamable_http(bind: str, port: int, token: str) -> None:
    """Spec 014 remote hosting: co-located Model B -- this same server.py,
    running ON the VPS with its own local $SANDBOX_HOME, reached over a
    Tailscale mesh. FastMCP's built-in `auth=`/`token_verifier=` mechanism is
    an OAuth-resource-server flow (AuthSettings requires issuer_url +
    resource_server_url) -- real overkill for a single pre-shared bearer
    token between one client and one server on a private mesh. So: get the
    plain Starlette app FastMCP already builds for streamable-http
    (`streamable_http_app()`) and wrap it with a small bearer-check
    middleware instead, serving it via uvicorn directly rather than
    `mcp.run()`.

    Validates its own arguments (never 0.0.0.0, port/token required) here --
    at the actual point of action -- rather than relying solely on the thin
    `__main__`/cmd_mcp dispatchers to have checked first (FR-014's
    never-do is enforced wherever this function is ever called from)."""
    if not bind or bind == "0.0.0.0":
        raise SystemExit(
            f"refusing to bind streamable-http transport to {bind!r} -- must "
            "be a specific (Tailscale) address, never 0.0.0.0 (spec FR-014)"
        )
    if not port:
        raise SystemExit("a port is required for streamable-http transport")
    if not token:
        raise SystemExit("a token is required for streamable-http transport")

    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse

    class _BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != f"Bearer {token}":
                return PlainTextResponse("Unauthorized", status_code=401)
            return await call_next(request)

    mcp.settings.host = bind
    mcp.settings.port = port
    # DNS-rebinding protection (transport_security) defaults to localhost-only
    # allowed_hosts/allowed_origins -- ADD the bind address rather than
    # replacing the defaults, so the protection stays meaningful.
    mcp.settings.transport_security.allowed_hosts.append(f"{bind}:*")
    mcp.settings.transport_security.allowed_origins.append(f"http://{bind}:*")

    app = mcp.streamable_http_app()
    app.add_middleware(_BearerAuthMiddleware)
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":
    import sys as _sys

    opts = _parse_transport_args(_sys.argv[1:])
    if opts["transport"] == "streamable-http":
        _run_streamable_http(opts["bind"], opts["port"], opts["token"])
    else:
        mcp.run()
