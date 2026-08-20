from __future__ import annotations
import json
import os
import shlex
import subprocess
import shutil
import sqlite3
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import (
    PROXY_TLD, SANDBOX_ROOT, _core, _load_sandbox_yml, _project_instance,
    _resolve_instance, _safe_json, _site_url, _compose, _herd_host_env,
    _host_run, _is_herd, _require_project_capability, _wp_root, _wpcli, mcp,
)
from dependencies import ToolDependencies
from tools.manifest import DEFAULT_MCP_GROUPS, built_in_tool_registry, project_default_groups


def _runtime_service():
    from sandbox.application.context import runtime_service
    from sandbox.core._config import load_config
    return runtime_service(load_config())


def _domain_service():
    from sandbox.application.context import domain_service
    from sandbox.core._config import load_config
    return domain_service(load_config())


def _ingress_service():
    from sandbox.application.context import ingress_service
    from sandbox.core._config import load_config
    return ingress_service(load_config())


def _native_preflight():
    from sandbox.application.context import native_isolation_preflight
    from sandbox.core._config import load_config
    return native_isolation_preflight(load_config())


def _managed_package_planner():
    from sandbox.application.context import managed_package_planner
    from sandbox.core._config import load_config
    return managed_package_planner(load_config())


def _resource_service(remote=None):
    from sandbox.resources.context import resource_service
    return resource_service(remote)


def _feedback_service():
    from sandbox.feedback.context import feedback_service
    return feedback_service()


def _secret_service(project_dir: str):
    from sandbox.secrets.context import build_secret_service
    from sandbox.core._paths import BASE
    from sandbox.core._secrets import secret_file
    root = Path(project_dir).expanduser().resolve()
    if _project_scope and root != Path(_project_scope).expanduser().resolve():
        from sandbox.secrets import SecretBrokerError
        raise SecretBrokerError("source_scope_denied", "project is outside the MCP secret scope")
    config = _core().load_project_config(str(root))
    return build_secret_service(
        project_root=root, config=config, personal_path=secret_file(),
        runtime_root=BASE / "runtime", project_scope=_project_scope or None,
    )


def _durable_job_dependencies():
    import sys
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from sandbox.application.context import durable_job_dependencies
    return durable_job_dependencies()


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


_group_filter = os.environ.get("SANDBOX_MCP_GROUPS", "").strip()
_project_scope = os.environ.get("SANDBOX_MCP_PROJECT_DIR", "").strip()
if _project_scope and not _group_filter:
    try:
        _project_kind = _core().load_project_config(_project_scope).get("kind", "wordpress")
        _scoped_groups = project_default_groups(_project_kind)
    except Exception as exc:
        raise RuntimeError(f"could not build scoped MCP catalog: {exc}") from exc
else:
    _scoped_groups = None
_selected_groups = (
    None if _group_filter.lower() == "all" else
    tuple(part.strip() for part in _group_filter.split(",") if part.strip())
    if _group_filter else DEFAULT_MCP_GROUPS
)
if _scoped_groups is not None:
    _selected_groups = _scoped_groups
_job_dependencies = _durable_job_dependencies() \
    if _selected_groups is None or "jobs" in _selected_groups else {}
built_in_tool_registry(_selected_groups).compose(mcp, ToolDependencies({
    "app": mcp,
    "sandbox_root": SANDBOX_ROOT,
    "proxy_tld": PROXY_TLD,
    "core": _core,
    "load_sandbox_yml": _load_sandbox_yml,
    "project_instance": _project_instance,
    "resolve_instance": _resolve_instance,
    "safe_json": _safe_json,
    "site_url": _site_url,
    "compose": _compose,
    "herd_host_env": _herd_host_env,
    "host_run": _host_run,
    "is_herd": _is_herd,
    "require_project_capability": _require_project_capability,
    "wp_root": _wp_root,
    "wpcli": _wpcli,
    "runtime_service": _runtime_service,
    "domain_service": _domain_service,
    "ingress_service": _ingress_service,
    "native_preflight": _native_preflight,
    "managed_package_planner": _managed_package_planner,
    "resource_service_factory": _resource_service,
    "feedback_service_factory": _feedback_service,
    "secret_service_factory": _secret_service,
    "hermes_service": _HermesCommandAdapter(),
    **_job_dependencies,
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
    values = vars(ns)
    environment_token = os.environ.get("SANDBOX_REMOTE_MCP_TOKEN")
    if environment_token:
        if values["token"] and values["token"] != environment_token:
            raise SystemExit("remote MCP token sources disagree")
        values["token"] = environment_token
    return values


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
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    def diagnostic_snapshot() -> dict:
        """Safe host evidence for control-plane outages; never returns secrets or logs."""
        home = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
        memory_mb = None
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    memory_mb = int(line.split()[1]) // 1024
                    break
        except (OSError, ValueError, IndexError):
            pass
        jobs = {"active": None, "queued": None}
        try:
            connection = sqlite3.connect(home / "runtime" / "jobs" / "registry.sqlite3")
            try:
                rows = connection.execute(
                    "SELECT lifecycle, COUNT(*) FROM jobs GROUP BY lifecycle"
                ).fetchall()
            finally:
                connection.close()
            counts = dict(rows)
            jobs = {"active": sum(int(counts.get(state, 0)) for state in ("accepted", "queued", "running", "cancelling")),
                    "queued": int(counts.get("queued", 0))}
        except (OSError, sqlite3.Error):
            pass
        return {
            "ok": True,
            "service": "sandbox-remote-mcp",
            "runtime_revision": os.environ.get("SANDBOX_REMOTE_MCP_RUNTIME_REVISION", "unknown"),
            "load_1m": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
            "memory_available_mb": memory_mb,
            "disk_free_mb": shutil.disk_usage(home).free // (1024 * 1024),
            "jobs": jobs,
        }

    async def diagnostics(_request):
        return JSONResponse(diagnostic_snapshot())

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
    app.routes.append(Route("/diagnostics", diagnostics, methods=["GET"]))
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
