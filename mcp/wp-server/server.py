from __future__ import annotations
import asyncio
import json
import math
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


_DIAGNOSTICS_SCHEMA_VERSION = 2
_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS = 5
_DIAGNOSTICS_MAX_PROBE_ROWS = 101


def _job_counts() -> dict:
    home = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
    result = {"total": None, "active": None, "queued": None, "by_lifecycle": {}}
    try:
        connection = sqlite3.connect(home / "runtime" / "jobs" / "registry.sqlite3")
        try:
            rows = connection.execute(
                "SELECT lifecycle, COUNT(*) FROM jobs GROUP BY lifecycle"
            ).fetchall()
        finally:
            connection.close()
        counts = {str(state): int(count) for state, count in rows}
        result = {
            "total": sum(counts.values()),
            "active": sum(counts.get(state, 0) for state in
                          ("accepted", "queued", "running", "cancelling")),
            "queued": counts.get("queued", 0),
            "by_lifecycle": counts,
        }
    except (OSError, sqlite3.Error):
        pass
    return result


def _diagnostic_process_snapshot() -> dict:
    """Collect bounded process evidence with fixed argv and no shell or SSH."""
    from sandbox.core import _remote as remote_core

    sections = ["__SANDBOX_PS_BEGIN__"]
    try:
        ps_result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,pcpu=,pmem=,rss=,comm="],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        ps_result = None
    if ps_result is not None and ps_result.returncode == 0:
        sections.extend(ps_result.stdout.splitlines()[:_DIAGNOSTICS_MAX_PROBE_ROWS])
    sections.extend(("__SANDBOX_PS_END__", "__SANDBOX_DOCKER_BEGIN__"))

    try:
        docker_result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=_DIAGNOSTICS_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        docker_result = None
    if docker_result is not None and docker_result.returncode == 0:
        sections.append("__SANDBOX_DOCKER_AVAILABLE__")
        sections.extend(docker_result.stdout.splitlines()[:_DIAGNOSTICS_MAX_PROBE_ROWS])
    sections.append("__SANDBOX_DOCKER_END__")

    probe_output = "\n".join(sections)
    process_view, _ = remote_core._parse_ssh_process_view(probe_output)
    containers = remote_core._parse_container_view(probe_output)
    return {
        "diagnostics_schema": _DIAGNOSTICS_SCHEMA_VERSION,
        "transport": "control",
        "capabilities": ["process_view", "container_view"],
        "process_view": process_view,
        "containers": containers,
    }


def _resource_contract(payload: dict) -> dict:
    """Execute only the fixed resource probe contract on the co-located host."""
    from sandbox.resources.remote import LocalProbeAdapter

    action = payload.get("action")
    if action not in {"observe", "reclaim", "lease", "remove"}:
        raise ValueError("unsupported resource action")
    # The local adapter executes Sandbox's shipped probe source. The request has
    # no argv or shell field and the probe independently validates cleanup kinds,
    # locators, reviewed candidates, ownership, and active-use evidence.
    try:
        budget = float(payload.get("budget_seconds", 15))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("resource budget_seconds must be finite") from None
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("resource budget_seconds must be finite and positive")
    adapter = LocalProbeAdapter()
    response = adapter._run(payload, min(max(budget + 10, 1), 910))
    result = None
    for line in reversed((response.stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            result = candidate
            break
    if result is None:
        result = {"ok": False, "reason": "resource_response_invalid"}
    return {"resource_schema": 1, "transport": "control", "result": result}


def _hosted_inventory_snapshot(*, deep: bool = False) -> dict:
    """Build a secret-free, bounded dashboard view of this hosted Sandbox."""
    from sandbox.core._config import load_config
    from sandbox.core._dash import collect_instance_rows
    from sandbox.resources.remote import LocalProbeAdapter

    partial = []
    try:
        source_rows = collect_instance_rows(load_config())
        instances = [{
            "name": str(row.get("name") or "unknown")[:128],
            "running": bool(row.get("running")),
            "server": str(row.get("server") or "unknown")[:64],
            "project": str(row.get("project") or "unknown")[:128],
            "label": str(row.get("label") or "default")[:128],
        } for row in source_rows[:500]]
        if len(source_rows) > 500:
            partial.append("instance_rows_truncated")
    except Exception:
        instances = []
        partial.append("instance_inventory_unavailable")

    diagnostics = _diagnostic_process_snapshot()
    container_view = diagnostics.get("containers") or {"status": "unavailable", "rows": []}
    container_rows = container_view.get("rows") if isinstance(container_view, dict) else []
    container_rows = container_rows if isinstance(container_rows, list) else []
    per_instance = []
    attributed_names = set()
    for instance in instances:
        needle = instance["name"].lower().replace("_", "-")
        matched = []
        for row in container_rows:
            name = str(row.get("name") or "")
            normalized = name.lower().replace("_", "-")
            if needle and (normalized == needle or normalized.startswith(needle + "-")
                           or ("-" + needle + "-") in ("-" + normalized + "-")):
                matched.append(row)
                attributed_names.add(name)
        per_instance.append({
            "name": instance["name"],
            "attribution_status": "heuristic" if matched else "unknown",
            "container_count": len(matched),
            "memory_used_bytes": sum(int(row.get("memory_used_bytes") or 0) for row in matched),
            "cpu_percent": round(sum(float(row.get("cpu_percent") or 0) for row in matched), 2),
        })
    unattributed = [row for row in container_rows if str(row.get("name") or "") not in attributed_names]

    try:
        storage_probe = LocalProbeAdapter().observe_reclaim(
            budget_seconds=30 if deep else 8,
            directory_cache="refresh" if deep else "cache_only",
        )
        storage = {
            "status": "partial" if any(
                str(item.get("status")) not in {"complete", "measured"}
                for item in (storage_probe.get("category_outcomes") or ())
                if isinstance(item, dict)
            ) else "complete",
            "capacity": storage_probe.get("capacity"),
            "capacity_scope_id": storage_probe.get("capacity_scope_id"),
            "attribution_status": (
                "available" if storage_probe.get("deep_attribution") else "unknown"
            ),
            "category_outcomes": list(storage_probe.get("category_outcomes") or ())[:50],
        }
    except Exception:
        storage = {"status": "unavailable", "capacity": None,
                   "attribution_status": "unknown", "category_outcomes": []}
        partial.append("storage_inventory_unavailable")

    running = sum(1 for row in instances if row["running"])
    host = _memory_snapshot()
    try:
        # Dashboard disk pressure is host-wide. The resource probe also uses
        # the root filesystem as its capacity scope, so do not silently report
        # only the Sandbox home mount when those differ.
        disk = shutil.disk_usage("/")
        host.update({"disk_total_bytes": disk.total, "disk_used_bytes": disk.used,
                     "disk_free_bytes": disk.free, "disk_scope": "/"})
    except OSError:
        host.update({"disk_total_bytes": None, "disk_used_bytes": None,
                     "disk_free_bytes": None, "disk_scope": None})
        partial.append("disk_inventory_unavailable")
    host["load_1m"] = round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None
    jobs = _job_counts()
    return {
        "ok": True, "inventory_schema": 1, "transport": "control",
        "instances": {"total": len(instances), "running": running,
                      "stopped": len(instances) - running, "rows": instances},
        "host": host, "jobs": jobs,
        "process_view": diagnostics.get("process_view"), "containers": container_view,
        "per_instance_usage": per_instance,
        "unattributed_containers": unattributed,
        "storage": storage,
        "scan_mode": "deep" if deep else "fast",
        "evidence_status": "partial" if partial or storage.get("status") != "complete"
                           or container_view.get("status") != "complete" else "complete",
        "partial_reasons": partial,
        "migration": {
            "service_backed": ["resource_observe", "resource_cleanup", "host_inventory"],
            "operator_only": ["remote_ssh"],
            "remaining": ["remote_domains", "remote_plugins", "remote_service_lifecycle",
                          "remote_docker_pool", "deploy_transport"],
        },
    }


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


def _reclaim_service(remote=None):
    from sandbox.resources.context import reclaim_service
    return reclaim_service(remote)


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


def _sync_service():
    import sys
    repository_root = str(SANDBOX_ROOT)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from sandbox.application.context import sync_service_dependencies
    return sync_service_dependencies()


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
    "reclaim_service_factory": _reclaim_service,
    "feedback_service_factory": _feedback_service,
    "secret_service_factory": _secret_service,
    "hermes_service": _HermesCommandAdapter(),
    "sync_service": _sync_service(),
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


def _memory_snapshot(meminfo_path: Path = Path("/proc/meminfo")) -> dict:
    """Return bounded host RAM totals derived from Linux meminfo.

    ``MemAvailable`` is the kernel's reclaimable-memory estimate, so
    ``total - available`` is a more useful usage figure than ``total -
    MemFree`` on a cache-heavy VPS. Keep the response in MiB and expose only
    aggregate numbers; no process or path details leave the host.
    """
    values = {}
    try:
        for line in meminfo_path.read_text().splitlines():
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            parts = raw.strip().split()
            if not parts:
                continue
            value = int(parts[0])
            if len(parts) > 1 and parts[1].lower() == "kb":
                value *= 1024
            values[key] = value
    except (OSError, ValueError, IndexError):
        values = {}

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not isinstance(total, int) or not isinstance(available, int) or total <= 0:
        return {
            "memory_total_mb": None,
            "memory_used_mb": None,
            "memory_available_mb": None,
            "memory_used_percent": None,
        }
    available = min(max(available, 0), total)
    used = total - available
    return {
        "memory_total_mb": total // (1024 * 1024),
        "memory_used_mb": used // (1024 * 1024),
        "memory_available_mb": available // (1024 * 1024),
        "memory_used_percent": round((used * 100) / total, 2),
    }


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
        jobs = _job_counts()
        return {
            "ok": True,
            "service": "sandbox-remote-mcp",
            "runtime_revision": os.environ.get("SANDBOX_REMOTE_MCP_RUNTIME_REVISION", "unknown"),
            "load_1m": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
            **_memory_snapshot(),
            "disk_free_mb": shutil.disk_usage(home).free // (1024 * 1024),
            "jobs": jobs,
        }

    async def diagnostics(request):
        query = list(request.query_params.multi_items())
        if query and query != [("processes", "1")]:
            return JSONResponse({
                "ok": False,
                "error": "invalid diagnostics query; only processes=1 is supported",
            }, status_code=400)
        snapshot = diagnostic_snapshot()
        if query:
            snapshot.update(await asyncio.to_thread(_diagnostic_process_snapshot))
        return JSONResponse(snapshot)

    async def resources(request):
        body_bytes = await request.body()
        if len(body_bytes) > 64 * 1024:
            return JSONResponse({"ok": False, "error": "resource request is too large"},
                                status_code=413)
        try:
            body = json.loads(body_bytes)
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "resource request must be an object"},
                                status_code=400)
        try:
            result = await asyncio.to_thread(_resource_contract, body)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def inventory(request):
        query = list(request.query_params.multi_items())
        if query and query != [("deep", "1")]:
            return JSONResponse({"ok": False, "error": "only deep=1 is supported"},
                                status_code=400)
        return JSONResponse(await asyncio.to_thread(
            _hosted_inventory_snapshot, deep=bool(query),
        ))

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
    app.routes.append(Route("/resources", resources, methods=["POST"]))
    app.routes.append(Route("/inventory", inventory, methods=["GET"]))
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
