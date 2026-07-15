from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import (
    PROXY_TLD, SANDBOX_ROOT, _core, _load_sandbox_yml, _project_instance,
    _resolve_instance, _safe_json, _site_url, mcp,
)
from dependencies import ToolDependencies
from tools.manifest import built_in_tool_registry


def _runtime_service():
    from sandbox.application.context import runtime_service
    from sandbox.core._config import load_config
    return runtime_service(load_config())


def _last_json(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _run_hermes_command(args: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(
            [str(SANDBOX_ROOT / "sb"), *args, "--json"], cwd=str(SANDBOX_ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Hermes control command timed out"}
    payload = _last_json(result.stdout)
    if payload is not None:
        return payload
    message = result.stderr or result.stdout or "Hermes control command failed"
    return {"ok": False, "error": message.strip()[:1000]}


class _HermesCommandAdapter:
    """MCP transport adapter satisfying the explicit Hermes command service."""

    def run(self, arguments: list[str], timeout: int) -> dict:
        return _run_hermes_command(arguments, timeout)


built_in_tool_registry().compose(mcp, ToolDependencies({
    "app": mcp,
    "sandbox_root": SANDBOX_ROOT,
    "proxy_tld": PROXY_TLD,
    "core": _core,
    "load_sandbox_yml": _load_sandbox_yml,
    "project_instance": _project_instance,
    "resolve_instance": _resolve_instance,
    "safe_json": _safe_json,
    "site_url": _site_url,
    "runtime_service": _runtime_service,
    "hermes_service": _HermesCommandAdapter(),
}))



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
    p.add_argument("--public-url", default=None)
    ns, _ = p.parse_known_args(argv)
    return vars(ns)


def _run_streamable_http(bind: str, port: int, token: str,
                         public_url: str | None = None) -> None:
    """Spec 014 remote hosting: co-located Model B -- this same server.py,
    running ON the VPS with its own local $SANDBOX_HOME. In public-HTTPS mode
    it binds to loopback behind Caddy; in Tailscale mode it binds to the
    tailnet interface. FastMCP's built-in `auth=`/`token_verifier=` mechanism is
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
            "be a specific address, never 0.0.0.0 (spec FR-014)"
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
    # allowed_hosts/allowed_origins -- ADD the bind/public addresses rather
    # than replacing the defaults, so the protection stays meaningful.
    mcp.settings.transport_security.allowed_hosts.append(f"{bind}:*")
    mcp.settings.transport_security.allowed_origins.append(f"http://{bind}:*")
    if public_url:
        from urllib.parse import urlparse
        parsed = urlparse(public_url)
        if parsed.netloc:
            mcp.settings.transport_security.allowed_hosts.append(parsed.netloc)
            mcp.settings.transport_security.allowed_origins.append(
                f"{parsed.scheme}://{parsed.netloc}"
            )

    app = mcp.streamable_http_app()
    app.add_middleware(_BearerAuthMiddleware)
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":
    import sys as _sys

    opts = _parse_transport_args(_sys.argv[1:])
    if opts["transport"] == "streamable-http":
        _run_streamable_http(
            opts["bind"], opts["port"], opts["token"], opts.get("public_url")
        )
    else:
        mcp.run()
