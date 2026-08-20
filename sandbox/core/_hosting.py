"""Project-local hosted Compose configuration and safe rendering helpers."""
from __future__ import annotations

import json
import hashlib
import ipaddress
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

from sandbox.core._config import ensure_pyyaml
from sandbox.core._paths import RUNTIME_DIR


class HostingError(ValueError):
    pass


_SERVICE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_BASIC_AUTH_BYPASS_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_BASIC_AUTH_ROUTE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+$")
_BASIC_AUTH_ROUTE_PARAMETER_RE = re.compile(r"\{[A-Za-z][A-Za-z0-9_]*\}$")
_STATE_FILE = RUNTIME_DIR / "hosts.json"
# Served verbatim to crawlers on a route that must never be indexed. `handle`
# (not a bare matcher + respond) so the block is mutually exclusive with the
# proxy handler and no Caddy directive-ordering subtlety can let /robots.txt
# fall through to the origin.
#
# The body is a Caddyfile quoted string spanning real newlines: `respond` emits
# a `\n` escape literally, so the two-line policy has to BE two lines.
ROBOTS_DENY_BODY = "User-agent: *\nDisallow: /\n"
_ROBOTS_DENY_BLOCK = (
    "    handle /robots.txt {\n"
    "        header Content-Type \"text/plain; charset=utf-8\"\n"
    f"        respond \"{ROBOTS_DENY_BODY}\" 200\n"
    "    }\n"
)
_PORT_START = 18000
_PORT_COUNT = 1000
_CLOUDFLARE_PROXY_CIDRS = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22", "2400:cb00::/32",
    "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)


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
    if len(encoded) > 253 or any(len(label) > 63 for label in encoded.split(".")):
        raise HostingError(f"invalid hostname {value!r}")
    return prefix + encoded


def _normalize_redirect_target(value: object) -> str:
    """Return a canonical HTTPS hostname target for a redirect route.

    Caddy appends ``{uri}`` to this value, so allowing a target path or query
    would duplicate or discard a request path/query instead of preserving it.
    Canonicalising the hostname here also ensures that state, DNS planning, and
    the rendered Caddyfile use the same ASCII IDN form.
    """
    if not isinstance(value, str):
        raise HostingError("redirect aliases require an https target")
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise HostingError("redirect aliases require a valid https target") from exc
    if (parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username
            or parsed.password or not parsed.hostname):
        raise HostingError("redirect aliases require an https target")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HostingError("redirect aliases must target a hostname without a path, query, or fragment")
    hostname = normalize_hostname(parsed.hostname, wildcard=False)
    authority = hostname if port is None else f"{hostname}:{port}"
    return f"https://{authority}"


def _reject_redirect_cycles(routes: list[dict]) -> None:
    """Reject cycles among declared redirect aliases before any remote action."""
    redirects = {
        route["hostname"]: normalize_hostname(
            urllib.parse.urlsplit(route["target"]).hostname or "", wildcard=False
        )
        for route in routes if route["mode"] == "redirect"
    }
    for start in redirects:
        visited: set[str] = set()
        current = start
        while current in redirects:
            if current in visited:
                chain = " -> ".join([*visited, current])
                raise HostingError(f"redirect aliases form a cycle ({chain})")
            visited.add(current)
            current = redirects[current]


def _project_root(project_dir: str | Path) -> Path:
    """Return the validated manifest parent, not an arbitrary outer checkout.

    Hosting callers often receive a path from a nested workspace or a file
    picker.  Walk upward only until the nearest manifest so a nested manifest
    owns its Compose/source root; never replace it with the caller's outer Git
    checkout.  Passing the manifest file itself is supported for adapters that
    already resolved the exact path.
    """
    candidate = Path(project_dir).expanduser().resolve()
    if candidate.is_file():
        if candidate.name != "sandbox.hosting.yml":
            raise HostingError(f"expected sandbox.hosting.yml, got {candidate.name}")
        candidate = candidate.parent
    for root in (candidate, *candidate.parents):
        manifest = root / "sandbox.hosting.yml"
        if manifest.is_file():
            return root
    manifest = candidate / "sandbox.hosting.yml"
    raise HostingError(f"missing {manifest}; add a project-local sandbox.hosting.yml")


def load_manifest(project_dir: str | Path) -> tuple[Path, dict]:
    root = _project_root(project_dir)
    ensure_pyyaml()
    import yaml
    manifest_path = root / "sandbox.hosting.yml"
    try:
        data = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise HostingError(f"invalid sandbox.hosting.yml: {exc}") from exc
    if not isinstance(data, dict):
        raise HostingError("sandbox.hosting.yml must contain a mapping")
    # Internal provenance is carried alongside the validated result so the
    # CLI/MCP result can show both paths without making callers rediscover the
    # manifest.  It is not a user-configurable field and is never serialized
    # into the project manifest.
    data = {**data, "_manifest_path": str(manifest_path), "_manifest_root": str(root)}
    return root, data


def _git_root(path: Path) -> Path | None:
    """Return the checkout boundary that may contain a nested manifest.

    A hosting manifest may intentionally point at a sibling/parent source
    directory (for example ``config/sandbox.hosting.yml`` with
    ``source_root: ../site``), but it must not be able to broaden deployment
    to an arbitrary directory outside the checkout.  Git is the authoritative
    boundary for a project checkout; when there is no checkout boundary we
    retain the safer historical restriction to the manifest directory.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return Path(value).resolve() if value else None


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


def _environment_values(value: object, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HostingError(f"secrets.{name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _ENV_RE.fullmatch(key):
            raise HostingError(f"secrets.{name} contains an invalid environment key")
        if not isinstance(item, (str, int, float, bool)):
            raise HostingError(f"secrets.{name}.{key} must be a scalar")
        result[key] = str(item)
    return result


def _secrets(env: dict) -> dict:
    raw = env.get("secrets")
    if raw is None:
        return {"values": {}, "required": {}, "generated": {}}
    if not isinstance(raw, dict):
        raise HostingError("secrets must be a mapping")
    values = _environment_values(raw.get("values"), "values")
    required = _environment_values(raw.get("required"), "required")
    generated = _environment_values(raw.get("generated"), "generated")
    duplicate = (set(values) & set(required)) | (set(values) & set(generated)) | (set(required) & set(generated))
    if duplicate:
        raise HostingError(f"secret environment keys may only appear once: {', '.join(sorted(duplicate))}")
    for source in [*required.values(), *generated.values()]:
        if not _ENV_RE.fullmatch(source):
            raise HostingError("secret source names must be environment variable names")
    return {"values": values, "required": required, "generated": generated}


def _autologin(env: dict) -> dict | None:
    """Validate an opt-in one-time WordPress login-link integration."""
    raw = env.get("autologin")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HostingError("autologin must be a mapping")
    user = str(raw.get("user") or "").strip()
    if not user or len(user) > 60 or any(ord(char) < 33 or ord(char) > 126 for char in user):
        raise HostingError("autologin.user must be a printable WordPress login name")
    path = str(raw.get("container_path") or "").strip()
    candidate = Path(path)
    if not path.startswith("/") or ".." in candidate.parts or candidate.suffix != ".php":
        raise HostingError("autologin.container_path must be an absolute PHP file path")
    ttl = raw.get("ttl_seconds", 900)
    if not isinstance(ttl, int) or not 60 <= ttl <= 3600:
        raise HostingError("autologin.ttl_seconds must be an integer from 60 to 3600")
    return {"user": user, "container_path": path, "ttl_seconds": ttl}


def _basic_auth(env: dict) -> dict | None:
    """Validate an optional origin Basic Auth gate.

    The password is deliberately represented only by a secret-store reference;
    the manifest must never contain the credential itself or a generated hash.
    """
    raw = env.get("basic_auth")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HostingError("basic_auth must be a mapping")
    username = str(raw.get("username") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username):
        raise HostingError("basic_auth.username must be 1-64 alphanumeric, dot, underscore, or hyphen characters")
    password_secret = str(raw.get("password_secret") or "").strip()
    if not _ENV_RE.fullmatch(password_secret):
        raise HostingError("basic_auth.password_secret must be an environment variable name")
    result = {"username": username, "password_secret": password_secret}
    bypass_ips = raw.get("bypass_ips", [])
    if not isinstance(bypass_ips, list):
        raise HostingError("basic_auth.bypass_ips must be a list")
    normalized_ips: list[str] = []
    for bypass_ip in bypass_ips:
        try:
            parsed_bypass_ip = ipaddress.ip_address(str(bypass_ip).strip())
        except ValueError as exc:
            raise HostingError("basic_auth.bypass_ips must contain valid IP addresses") from exc
        if not parsed_bypass_ip.is_global:
            raise HostingError("basic_auth.bypass_ips must contain public IP addresses")
        candidate_ip = str(parsed_bypass_ip)
        if candidate_ip in normalized_ips:
            raise HostingError("basic_auth.bypass_ips must not contain duplicates")
        normalized_ips.append(candidate_ip)
    if normalized_ips:
        result["bypass_ips"] = normalized_ips
    bypass_paths = raw.get("bypass_paths", [])
    if not isinstance(bypass_paths, list):
        raise HostingError("basic_auth.bypass_paths must be a list")
    normalized_paths: list[str] = []
    for path in bypass_paths:
        candidate = str(path).strip()
        if (
            not candidate.startswith("/")
            or candidate == "/"
            or ".." in candidate
            or any(character in candidate for character in "\r\n*?{}")
        ):
            raise HostingError("basic_auth.bypass_paths must contain exact non-root URL paths")
        if candidate in normalized_paths:
            raise HostingError("basic_auth.bypass_paths must not contain duplicates")
        normalized_paths.append(candidate)
    if normalized_paths:
        result["bypass_paths"] = normalized_paths
    bypass_routes = raw.get("bypass_routes", [])
    if not isinstance(bypass_routes, list) or len(bypass_routes) > 64:
        raise HostingError("basic_auth.bypass_routes must be a list of at most 64 routes")
    normalized_routes: list[dict] = []
    seen_routes: set[tuple[str, str]] = set()
    for route in bypass_routes:
        if not isinstance(route, dict) or not route or set(route) - {"path", "path_template", "methods"}:
            raise HostingError("basic_auth.bypass_routes entries must contain only path or path_template plus methods")
        has_path = "path" in route
        has_template = "path_template" in route
        if has_path == has_template:
            raise HostingError("basic_auth.bypass_routes entries require exactly one of path or path_template")
        methods = route.get("methods")
        if not isinstance(methods, list) or not methods:
            raise HostingError("basic_auth.bypass_routes methods must be a non-empty list")
        normalized_methods: list[str] = []
        for method in methods:
            candidate_method = str(method).strip().upper()
            if candidate_method not in _BASIC_AUTH_BYPASS_METHODS:
                raise HostingError("basic_auth.bypass_routes methods must use standard HTTP methods")
            if candidate_method in normalized_methods:
                raise HostingError("basic_auth.bypass_routes methods must not contain duplicates")
            normalized_methods.append(candidate_method)
        field = "path" if has_path else "path_template"
        candidate_path = str(route[field]).strip()
        segments = candidate_path.removeprefix("/").split("/")
        if (
            not candidate_path.startswith("/")
            or candidate_path == "/"
            or not segments
            or any(not segment for segment in segments)
        ):
            raise HostingError("basic_auth.bypass_routes paths must be non-root absolute URL paths")
        has_parameter = False
        for segment in segments:
            if _BASIC_AUTH_ROUTE_PARAMETER_RE.fullmatch(segment):
                has_parameter = True
            elif segment in {".", ".."} or not _BASIC_AUTH_ROUTE_SEGMENT_RE.fullmatch(segment):
                raise HostingError("basic_auth.bypass_routes paths contain an unsafe segment")
        if has_template != has_parameter:
            raise HostingError("basic_auth.bypass_routes path_template must contain at least one {parameter}")
        route_key = (field, candidate_path)
        if route_key in seen_routes:
            raise HostingError("basic_auth.bypass_routes must not contain duplicates")
        seen_routes.add(route_key)
        normalized_routes.append({field: candidate_path, "methods": normalized_methods})
    if normalized_routes:
        result["bypass_routes"] = normalized_routes
    return result


def _basic_auth_route_pattern(path_template: str) -> str:
    segments = path_template.removeprefix("/").split("/")
    rendered = [
        "[^/]+" if _BASIC_AUTH_ROUTE_PARAMETER_RE.fullmatch(segment) else re.escape(segment)
        for segment in segments
    ]
    return "^/" + "/".join(rendered) + "$"


def validate_manifest(project_dir: str | Path, environment: str | None = None) -> dict:
    root, manifest = load_manifest(project_dir)
    if manifest.get("version") != 1:
        raise HostingError("manifest version must be 1")
    project = str(manifest.get("project") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project):
        raise HostingError("project must use lowercase letters, numbers, and hyphens")
    env_name, env = _environment(manifest, environment)
    # A manifest may explicitly name a source root relative to its own parent
    # (for example ``source_root: ../site``).  The default is the manifest
    # parent; importantly, the caller's outer checkout is never substituted.
    # Resolve and constrain the declaration before interpreting any Compose
    # paths.  An ancestor source root is allowed only when it remains inside
    # the Git checkout that owns the manifest, so the declaration is exact
    # without becoming an arbitrary path traversal primitive.
    declared_source_root = manifest.get("source_root", manifest.get("project_root", "."))
    if not isinstance(declared_source_root, str) or not declared_source_root.strip():
        raise HostingError("source_root must be a non-empty relative directory")
    if Path(declared_source_root).is_absolute():
        raise HostingError("source_root must be a non-empty relative directory")
    source_root_path = (root / declared_source_root).resolve()
    if source_root_path != root.resolve():
        checkout_root = _git_root(root)
        if checkout_root is None:
            raise HostingError("source_root must stay within the manifest project root")
        try:
            source_root_path.relative_to(checkout_root)
        except ValueError as exc:
            raise HostingError("source_root must stay within the manifest checkout") from exc
    else:
        checkout_root = _git_root(source_root_path)
    source_root_nested = bool(checkout_root and source_root_path != checkout_root)
    if not source_root_path.is_dir():
        raise HostingError(f"source_root does not exist: {declared_source_root}")
    source_root = source_root_path
    compose = env.get("compose") or {}
    files = compose.get("files") or []
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        raise HostingError("compose.files must list one or more compose files")
    compose_paths: list[str] = []
    for file_name in files:
        file_path = Path(file_name)
        if file_path.is_absolute():
            raise HostingError("compose files must be relative to the source root")
        resolved_file = (source_root / file_path).resolve()
        try:
            resolved_file.relative_to(source_root.resolve())
        except ValueError as exc:
            raise HostingError("compose files must stay within the source root") from exc
        if not resolved_file.is_file():
            raise HostingError(f"compose file does not exist: {file_name}")
        # Keep the manifest's declared relative spelling for compatibility;
        # source_root is explicit in the validated envelope and consumers join
        # these paths against that root.
        compose_paths.append(file_name)
    if not str(compose.get("service") or "").strip() or not isinstance(compose.get("container_port"), int):
        raise HostingError("compose.service and integer compose.container_port are required")
    if not _SERVICE_RE.fullmatch(str(compose["service"])):
        raise HostingError("compose.service contains unsupported characters")
    if not 1 <= compose["container_port"] <= 65535:
        raise HostingError("compose.container_port must be between 1 and 65535")
    init_services = compose.get("init_services", [])
    if not isinstance(init_services, list) or not all(isinstance(s, str) and _SERVICE_RE.fullmatch(s) for s in init_services):
        raise HostingError("compose.init_services must be a list of service names")
    background_services = compose.get("background_services", [])
    if not isinstance(background_services, list) or not all(isinstance(s, str) and _SERVICE_RE.fullmatch(s) for s in background_services):
        raise HostingError("compose.background_services must be a list of service names")
    declared_services = [str(compose["service"]), *init_services, *background_services]
    if len(declared_services) != len(set(declared_services)):
        raise HostingError("compose service names must not be duplicated across service lists")
    # An environment whose image build is too slow for the deploy timeout can
    # opt out of rebuilding. Compose still builds a service whose image is
    # missing, so this skips the rebuild, never the first build.
    build = compose.get("build", True)
    if not isinstance(build, bool):
        raise HostingError("compose.build must be true or false")
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
            target = _normalize_redirect_target(target)
            if normalize_hostname(urllib.parse.urlsplit(target).hostname or "", wildcard=False) == hostname:
                raise HostingError(f"redirect alias {hostname} cannot target itself")
        routes.append({"hostname": hostname, "mode": mode, "target": target, "primary": False})
        seen.add(hostname)
    _reject_redirect_cycles(routes)
    deploy = env.get("deploy") or {}
    if not isinstance(deploy.get("allowed_branches") or [], list) or not deploy["allowed_branches"] or not isinstance(deploy.get("require_clean"), bool):
        raise HostingError("deploy.allowed_branches and deploy.require_clean are required")
    cf = env.get("cloudflare") or {}
    proxied_origin_ca = (
        cf.get("proxied") is True
        and cf.get("tls") == "origin-ca"
        and cf.get("ssl_mode") == "strict"
    )
    public_acme = (
        cf.get("proxied") is False
        and cf.get("tls") == "acme"
        and "ssl_mode" not in cf
    )
    if not (proxied_origin_ca or public_acme):
        raise HostingError(
            "Cloudflare must use either proxied Origin CA with strict SSL "
            "or DNS-only public ACME"
        )
    if public_acme and any(route["hostname"].startswith("*.") for route in routes):
        raise HostingError("public ACME does not support wildcard routes without a DNS challenge")
    basic_auth = _basic_auth(env)
    if public_acme and basic_auth and basic_auth.get("bypass_ips"):
        raise HostingError("basic_auth.bypass_ips requires Cloudflare proxied hosting")
    # `robots: deny` makes Caddy answer /robots.txt with `Disallow: /` for every
    # served hostname of this environment — for a staging environment that is
    # publicly resolvable but must never be indexed. Default `allow` leaves the
    # application's own robots.txt in charge.
    robots = env.get("robots", "allow")
    if robots not in {"allow", "deny"}:
        raise HostingError("robots must be allow or deny")
    normalized_compose = {**compose, "files": compose_paths, "build": build}
    return {"project_root": str(source_root), "source_root": str(source_root),
            "manifest_root": str(root),
            "source_root_nested": source_root_nested,
            "manifest_path": manifest.get("_manifest_path", str(root / "sandbox.hosting.yml")),
            "project": project, "environment": env_name,
            "compose": normalized_compose, "healthcheck": healthcheck, "routes": routes,
            "deploy": deploy, "cloudflare": cf, "secrets": _secrets(env),
            "autologin": _autologin(env), "basic_auth": basic_auth,
            "robots": robots}


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


def render_env_file(validated: dict, source_values: dict[str, str]) -> str:
    """Render the exact environment passed to Compose, without persisting secrets locally."""
    values = dict(validated["secrets"]["values"])
    for container_key, source_key in {**validated["secrets"]["required"],
                                      **validated["secrets"]["generated"]}.items():
        value = source_values.get(source_key)
        if not value:
            raise HostingError(f"required hosting secret is missing: {source_key}")
        values[container_key] = value
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(values.items()))


def _php_squote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_autologin_mu_plugin(token_hash: str, user: str, expires_at: int) -> str:
    """Render a one-use, time-limited WordPress MU-plugin.

    The random token itself is never written to the server. Each issued link
    replaces the previous plugin and can set an auth cookie once only.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", token_hash):
        raise HostingError("autologin token hash must be a SHA-256 hex digest")
    if expires_at <= int(time.time()):
        raise HostingError("autologin expiry must be in the future")
    return f"""<?php
/** Generated by Sandbox hosting. This file contains a hash, never the login token. */
defined( 'ABSPATH' ) || exit;
define( 'SANDBOX_HOST_AUTOLOGIN_HASH', {_php_squote(token_hash)} );
define( 'SANDBOX_HOST_AUTOLOGIN_EXPIRES_AT', {int(expires_at)} );
define( 'SANDBOX_HOST_AUTOLOGIN_USER', {_php_squote(user)} );

add_action( 'init', static function () {{
    if ( empty( $_GET['sandbox_autologin'] ) ) {{
        return;
    }}
    $token = (string) wp_unslash( $_GET['sandbox_autologin'] );
    if ( time() > SANDBOX_HOST_AUTOLOGIN_EXPIRES_AT
        || ! hash_equals( SANDBOX_HOST_AUTOLOGIN_HASH, hash( 'sha256', $token ) ) ) {{
        status_header( 403 );
        wp_die( 'This Sandbox login link is invalid or has expired.' );
    }}
    $used_key = 'sandbox_host_autologin_' . substr( SANDBOX_HOST_AUTOLOGIN_HASH, 0, 40 );
    if ( ! add_site_option( $used_key, time() ) ) {{
        status_header( 410 );
        wp_die( 'This Sandbox login link has already been used.' );
    }}
    $user = get_user_by( 'login', SANDBOX_HOST_AUTOLOGIN_USER );
    if ( ! $user ) {{
        delete_site_option( $used_key );
        status_header( 403 );
        wp_die( 'The configured Sandbox login user is unavailable.' );
    }}
    wp_set_current_user( $user->ID );
    wp_set_auth_cookie( $user->ID, false, is_ssl() );
    wp_safe_redirect( is_multisite() ? network_admin_url() : admin_url() );
    exit;
}}, 1 );
"""


def autologin_url(validated: dict, token: str, expires_at: int) -> str:
    primary = next(route["hostname"] for route in validated["routes"] if route.get("primary"))
    query = urllib.parse.urlencode({"sandbox_autologin": token, "expires": int(expires_at)})
    return f"https://{primary}/?{query}"


def render_compose_command(validated: dict, source_dir: str, override_path: str) -> str:
    """Render a shell-safe remote Compose command for the declared services only."""
    compose = validated["compose"]
    files = ["docker", "compose", "-p", compose_project_name(validated)]
    for file_name in compose["files"]:
        files.extend(["-f", str(Path(source_dir) / file_name)])
    files.extend(["-f", override_path, "up", "-d", "--remove-orphans"])
    files.extend(compose.get("init_services", []))
    files.append(compose["service"])
    files.extend(compose.get("background_services", []))
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
        "caddyfile": caddyfile(validated, port, redact_basic_auth=True),
        "basic_auth_enabled": bool(validated.get("basic_auth")),
        "routes": validated["routes"],
        "records": [],
        "certificate_hostnames": [route["hostname"] for route in validated["routes"]],
        "healthcheck": validated["healthcheck"],
    }


def apply_with_rollback(apply, rollback) -> None:
    """Run a guarded mutation and attempt every rollback step on failure.

    A routing restore must not be skipped merely because an earlier DNS restore
    failed.  Preserve the original apply exception when rollback succeeds; if
    it does not, report the original failure plus every rollback failure.
    """
    try:
        apply()
    except Exception as apply_error:
        steps = (rollback,) if callable(rollback) else tuple(rollback)
        failures: list[Exception] = []
        for step in steps:
            try:
                step()
            except Exception as rollback_error:
                failures.append(rollback_error)
        if failures:
            details = "; ".join(str(error) or type(error).__name__ for error in failures)
            raise HostingError(
                f"hosting apply failed: {apply_error}; rollback failures: {details}"
            ) from apply_error
        raise


def caddyfile(validated: dict, port: int, cert_path: str | None = None,
              key_path: str | None = None, basic_auth_hash: str | None = None,
              *, redact_basic_auth: bool = False) -> str:
    """Render the served + redirect site blocks.

    `robots: "deny"` in the host config prepends a `/robots.txt` handler that
    answers `Disallow: /` for every served hostname, ahead of the proxy. It is
    opt-in here because permanent hosting fronts real production sites that
    want to be indexed; ephemeral preview routes deny by default instead (see
    `_remote._caddy_proxy_command`)."""
    served = [r["hostname"] for r in validated["routes"] if r["mode"] == "serve"]
    robots = _ROBOTS_DENY_BLOCK if validated.get("robots") == "deny" else ""
    tls = f"    tls {cert_path} {key_path}\n" if cert_path and key_path else ""
    basic = ""
    auth = validated.get("basic_auth")
    if auth:
        if basic_auth_hash is None:
            if not redact_basic_auth:
                raise HostingError("basic_auth requires a generated Caddy password hash")
        elif not basic_auth_hash.startswith("$"):
            raise HostingError("basic_auth_hash must be a Caddy password hash")
        else:
            auth_block = ("        basicauth {\n"
                          f"            {auth['username']} {basic_auth_hash}\n"
                          "        }\n")
            bypass_handlers = ""
            if auth.get("bypass_paths"):
                public_paths = " ".join(auth["bypass_paths"])
                bypass_handlers += ("    @basic_auth_public_paths {\n"
                                    "        method GET\n"
                                    f"        path {public_paths}\n"
                                    "    }\n"
                                    "    handle @basic_auth_public_paths {\n"
                                    f"        reverse_proxy 127.0.0.1:{int(port)}\n"
                                    "    }\n")
            for index, route in enumerate(auth.get("bypass_routes", [])):
                matcher_name = f"basic_auth_public_route_{index}"
                methods = " ".join(route["methods"])
                if "path" in route:
                    path_matcher = f"        path {route['path']}\n"
                else:
                    pattern = _basic_auth_route_pattern(route["path_template"])
                    path_matcher = f"        path_regexp {matcher_name} {pattern}\n"
                bypass_handlers += (f"    @{matcher_name} {{\n"
                                    f"        method {methods}\n"
                                    f"{path_matcher}"
                                    "    }\n"
                                    f"    handle @{matcher_name} {{\n"
                                    f"        reverse_proxy 127.0.0.1:{int(port)}\n"
                                    "    }\n")
            for index, bypass_ip in enumerate(auth.get("bypass_ips", [])):
                trusted_proxies = " ".join(_CLOUDFLARE_PROXY_CIDRS)
                bypass_handlers += (f"    @basic_auth_bypass_{index} {{\n"
                                    f"        remote_ip {trusted_proxies}\n"
                                    f"        header CF-Connecting-IP {bypass_ip}\n"
                                    "    }\n"
                                    f"    handle @basic_auth_bypass_{index} {{\n"
                                    f"        reverse_proxy 127.0.0.1:{int(port)}\n"
                                    "    }\n")
            if bypass_handlers:
                basic = (bypass_handlers + "    handle {\n"
                         f"{auth_block}"
                         f"        reverse_proxy 127.0.0.1:{int(port)}\n"
                         "    }\n")
            else:
                basic = ("    basicauth {\n"
                         f"        {auth['username']} {basic_auth_hash}\n"
                         "    }\n")
    proxy = f"    reverse_proxy 127.0.0.1:{int(port)}\n"
    if robots:
        # Everything but the site-level `tls` moves inside a catch-all handle,
        # so the robots handler and the rest are mutually exclusive routes
        # rather than two candidates for the same request.
        body = "".join(f"    {line}\n" if line else "\n"
                       for line in (basic + proxy).splitlines())
        blocks = [f"{', '.join(served)} {{\n{tls}{robots}    handle {{\n{body}    }}\n}}\n"]
    else:
        blocks = [f"{', '.join(served)} {{\n{basic}{tls}{proxy}}}\n"]
    for route in validated["routes"]:
        if route["mode"] == "redirect":
            blocks.append(f"{route['hostname']} {{\n    redir {route['target']}{{uri}} 308\n}}\n")
    return "\n".join(blocks)


def desired_plan(validated: dict, origin_ipv4: str | None, origin_ipv6: str | None = None) -> dict:
    addresses = [a for a in [origin_ipv4, origin_ipv6] if a]
    proxied = validated["cloudflare"]["proxied"]
    records = [{"hostname": r["hostname"], "address": a, "proxied": proxied,
                "mode": r["mode"], "target": r.get("target")}
               for r in validated["routes"] for a in addresses]
    return {"project": validated["project"], "environment": validated["environment"],
            "routes": validated["routes"], "records": records,
            "certificate_hostnames": [r["hostname"] for r in validated["routes"]],
            "ssl_mode": "strict" if proxied else None}
