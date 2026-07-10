"""Project-local hosted Compose configuration and safe rendering helpers."""
from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import tempfile
from pathlib import Path

from sandbox.core._config import ensure_pyyaml
from sandbox.core._paths import RUNTIME_DIR


class HostingError(ValueError):
    pass


_SERVICE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_STATE_FILE = RUNTIME_DIR / "hosts.json"
_PORT_START = 18000
_PORT_COUNT = 1000


def normalize_hostname(value: str, *, wildcard: bool = True) -> str:
    host = (value or "").strip().rstrip(".").lower()
    prefix = ""
    if host.startswith("*."):
        if not wildcard:
            raise HostingError("wildcard hostname is not allowed here")
        prefix, host = "*.", host[2:]
    if not host or "/" in host or ":" in host or ".." in host:
        raise HostingError(f"invalid hostname {value!r}")
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HostingError(f"invalid internationalized hostname {value!r}") from exc
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", encoded):
        raise HostingError(f"invalid hostname {value!r}")
    return prefix + encoded


def _project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    manifest = root / "sandbox.hosting.yml"
    if not manifest.exists():
        raise HostingError(f"missing {manifest}; add a project-local sandbox.hosting.yml")
    return root


def load_manifest(project_dir: str | Path) -> tuple[Path, dict]:
    root = _project_root(project_dir)
    ensure_pyyaml()
    import yaml
    try:
        data = yaml.safe_load((root / "sandbox.hosting.yml").read_text()) or {}
    except yaml.YAMLError as exc:
        raise HostingError(f"invalid sandbox.hosting.yml: {exc}") from exc
    if not isinstance(data, dict):
        raise HostingError("sandbox.hosting.yml must contain a mapping")
    return root, data


def _environment(manifest: dict, name: str | None) -> tuple[str, dict]:
    environments = manifest.get("environments")
    if not isinstance(environments, dict) or not environments:
        raise HostingError("manifest requires a non-empty environments mapping")
    if name is None:
        if len(environments) != 1:
            raise HostingError("--environment is required when a manifest has multiple environments")
        name = next(iter(environments))
    env = environments.get(name)
    if not isinstance(env, dict):
        raise HostingError(f"unknown hosting environment {name!r}")
    return name, env


def validate_manifest(project_dir: str | Path, environment: str | None = None) -> dict:
    root, manifest = load_manifest(project_dir)
    if manifest.get("version") != 1:
        raise HostingError("manifest version must be 1")
    project = str(manifest.get("project") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project):
        raise HostingError("project must use lowercase letters, numbers, and hyphens")
    env_name, env = _environment(manifest, environment)
    compose = env.get("compose") or {}
    files = compose.get("files") or []
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        raise HostingError("compose.files must list one or more compose files")
    for file_name in files:
        if Path(file_name).is_absolute() or ".." in Path(file_name).parts:
            raise HostingError("compose files must be relative to the project root")
        if not (root / file_name).is_file():
            raise HostingError(f"compose file does not exist: {file_name}")
    if not str(compose.get("service") or "").strip() or not isinstance(compose.get("container_port"), int):
        raise HostingError("compose.service and integer compose.container_port are required")
    if not _SERVICE_RE.fullmatch(str(compose["service"])):
        raise HostingError("compose.service contains unsupported characters")
    if not 1 <= compose["container_port"] <= 65535:
        raise HostingError("compose.container_port must be between 1 and 65535")
    init_services = compose.get("init_services", [])
    if not isinstance(init_services, list) or not all(isinstance(s, str) and s for s in init_services):
        raise HostingError("compose.init_services must be a list of service names")
    healthcheck = env.get("healthcheck") or {}
    if not isinstance(healthcheck.get("path"), str) or not healthcheck["path"].startswith("/"):
        raise HostingError("healthcheck.path must start with /")
    if not isinstance(healthcheck.get("statuses"), list) or not healthcheck["statuses"]:
        raise HostingError("healthcheck.statuses must be a non-empty list")
    host = env.get("host") or {}
    primary = normalize_hostname(str(host.get("primary") or ""), wildcard=False)
    aliases = host.get("aliases") or []
    if not isinstance(aliases, list):
        raise HostingError("host.aliases must be a list")
    routes = [{"hostname": primary, "mode": "serve", "primary": True}]
    seen = {primary}
    for alias in aliases:
        if not isinstance(alias, dict):
            raise HostingError("each host alias must be a mapping")
        hostname = normalize_hostname(str(alias.get("hostname") or ""))
        mode = alias.get("mode")
        if mode not in {"serve", "redirect"}:
            raise HostingError(f"alias {hostname} must set mode to serve or redirect")
        if hostname in seen:
            raise HostingError(f"duplicate hostname {hostname}")
        if hostname.startswith("*.") and mode != "serve":
            raise HostingError("wildcard aliases may only serve an application")
        target = alias.get("target")
        if mode == "redirect":
            if not isinstance(target, str) or not target.startswith("https://"):
                raise HostingError(f"redirect alias {hostname} requires an https target")
            if normalize_hostname(target.split("/", 3)[2], wildcard=False) == hostname:
                raise HostingError(f"redirect alias {hostname} cannot target itself")
        routes.append({"hostname": hostname, "mode": mode, "target": target, "primary": False})
        seen.add(hostname)
    deploy = env.get("deploy") or {}
    if not isinstance(deploy.get("allowed_branches") or [], list) or not deploy["allowed_branches"] or not isinstance(deploy.get("require_clean"), bool):
        raise HostingError("deploy.allowed_branches and deploy.require_clean are required")
    cf = env.get("cloudflare") or {}
    if cf.get("proxied") is not True or cf.get("tls") != "origin-ca" or cf.get("ssl_mode") != "strict":
        raise HostingError("cloudflare must require proxied Origin CA with strict SSL")
    return {"project_root": str(root), "project": project, "environment": env_name,
            "compose": compose, "healthcheck": healthcheck, "routes": routes,
            "deploy": deploy, "cloudflare": cf}


def state_key(remote_name: str, validated: dict) -> str:
    """Stable key for one remote/project/environment deployment."""
    if not _SERVICE_RE.fullmatch(remote_name or ""):
        raise HostingError("remote name contains unsupported characters")
    return f"{remote_name}/{validated['project']}/{validated['environment']}"


def compose_project_name(validated: dict) -> str:
    """Compose project namespace that keeps environments and volumes isolated."""
    return f"sandbox-host-{validated['project']}-{validated['environment']}"


def load_host_state(path: Path | None = None) -> dict:
    """Read only Sandbox-managed host state; missing state is an empty mapping."""
    path = path or _STATE_FILE
    if not path.exists():
        return {"version": 1, "hosts": {}}
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HostingError(f"invalid managed-host state: {exc}") from exc
    if state.get("version") != 1 or not isinstance(state.get("hosts"), dict):
        raise HostingError("invalid managed-host state format")
    return state


def save_host_state(state: dict, path: Path | None = None) -> None:
    """Atomically persist managed state without touching project manifests."""
    path = path or _STATE_FILE
    if state.get("version") != 1 or not isinstance(state.get("hosts"), dict):
        raise HostingError("invalid managed-host state format")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="hosts-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise HostingError(f"could not save managed-host state: {exc}") from exc


def allocate_loopback_port(state: dict, key: str) -> int:
    """Return a deterministic, non-conflicting managed loopback port.

    The remote apply step verifies availability before binding. Persisting this
    allocation makes plan/apply idempotent without probing or altering a VPS.
    """
    existing = state.get("hosts", {}).get(key, {})
    if isinstance(existing.get("loopback_port"), int):
        return existing["loopback_port"]
    used = {entry.get("loopback_port") for entry in state.get("hosts", {}).values()
            if isinstance(entry, dict) and isinstance(entry.get("loopback_port"), int)}
    offset = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % _PORT_COUNT
    for index in range(_PORT_COUNT):
        candidate = _PORT_START + ((offset + index) % _PORT_COUNT)
        if candidate not in used:
            return candidate
    raise HostingError("no managed loopback ports are available")


def compose_override(validated: dict, loopback_port: int) -> str:
    """Minimal generated Compose override binding only the declared web service."""
    compose = validated["compose"]
    return (
        "services:\n"
        f"  {compose['service']}:\n"
        "    ports:\n"
        f"      - \"127.0.0.1:{int(loopback_port)}:{int(compose['container_port'])}\"\n"
    )


def render_compose_command(validated: dict, source_dir: str, override_path: str) -> str:
    """Render a shell-safe remote Compose command for the declared services only."""
    compose = validated["compose"]
    files = ["docker", "compose", "-p", compose_project_name(validated)]
    for file_name in compose["files"]:
        files.extend(["-f", str(Path(source_dir) / file_name)])
    files.extend(["-f", override_path, "up", "-d", "--remove-orphans"])
    files.extend(compose.get("init_services", []))
    files.append(compose["service"])
    return " ".join(shlex.quote(part) for part in files)


def desired_runtime(validated: dict, remote_name: str, state: dict | None = None) -> dict:
    """Produce a complete, mutation-free desired runtime record for plan/apply."""
    state = state or {"version": 1, "hosts": {}}
    key = state_key(remote_name, validated)
    port = allocate_loopback_port(state, key)
    return {
        "key": key,
        "compose_project": compose_project_name(validated),
        "loopback_port": port,
        "compose_override": compose_override(validated, port),
        "caddyfile": caddyfile(validated, port),
        "routes": validated["routes"],
        "healthcheck": validated["healthcheck"],
    }


def apply_with_rollback(apply, rollback) -> None:
    """Run a guarded mutation and restore managed remote/DNS state on failure."""
    try:
        apply()
    except Exception:
        try:
            rollback()
        except Exception as rollback_error:
            raise HostingError(f"hosting apply failed and rollback failed: {rollback_error}") from rollback_error
        raise


def caddyfile(validated: dict, port: int, cert_path: str | None = None, key_path: str | None = None) -> str:
    served = [r["hostname"] for r in validated["routes"] if r["mode"] == "serve"]
    tls = f"    tls {cert_path} {key_path}\n" if cert_path and key_path else ""
    blocks = [f"{', '.join(served)} {{\n{tls}    reverse_proxy 127.0.0.1:{int(port)}\n}}\n"]
    for route in validated["routes"]:
        if route["mode"] == "redirect":
            blocks.append(f"{route['hostname']} {{\n    redir {route['target']}{{uri}} 308\n}}\n")
    return "\n".join(blocks)


def desired_plan(validated: dict, origin_ipv4: str | None, origin_ipv6: str | None = None) -> dict:
    addresses = [a for a in [origin_ipv4, origin_ipv6] if a]
    records = [{"hostname": r["hostname"], "address": a, "proxied": True}
               for r in validated["routes"] for a in addresses]
    return {"project": validated["project"], "environment": validated["environment"],
            "routes": validated["routes"], "records": records,
            "certificate_hostnames": [r["hostname"] for r in validated["routes"]],
            "ssl_mode": "strict"}
