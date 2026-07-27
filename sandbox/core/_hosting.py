"""Project-local hosted Compose configuration and safe rendering helpers."""
from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
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
    return {"username": username, "password_secret": password_secret}


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
    if not isinstance(init_services, list) or not all(isinstance(s, str) and _SERVICE_RE.fullmatch(s) for s in init_services):
        raise HostingError("compose.init_services must be a list of service names")
    background_services = compose.get("background_services", [])
    if not isinstance(background_services, list) or not all(isinstance(s, str) and _SERVICE_RE.fullmatch(s) for s in background_services):
        raise HostingError("compose.background_services must be a list of service names")
    declared_services = [str(compose["service"]), *init_services, *background_services]
    if len(declared_services) != len(set(declared_services)):
        raise HostingError("compose service names must not be duplicated across service lists")
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
            "deploy": deploy, "cloudflare": cf, "secrets": _secrets(env),
            "autologin": _autologin(env), "basic_auth": _basic_auth(env)}


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
    """Run a guarded mutation and restore managed remote/DNS state on failure."""
    try:
        apply()
    except Exception:
        try:
            rollback()
        except Exception as rollback_error:
            raise HostingError(f"hosting apply failed and rollback failed: {rollback_error}") from rollback_error
        raise


def caddyfile(validated: dict, port: int, cert_path: str | None = None,
              key_path: str | None = None, basic_auth_hash: str | None = None,
              *, redact_basic_auth: bool = False) -> str:
    served = [r["hostname"] for r in validated["routes"] if r["mode"] == "serve"]
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
            basic = ("    basicauth {\n"
                     f"        {auth['username']} {basic_auth_hash}\n"
                     "    }\n")
    blocks = [f"{', '.join(served)} {{\n{basic}{tls}    reverse_proxy 127.0.0.1:{int(port)}\n}}\n"]
    for route in validated["routes"]:
        if route["mode"] == "redirect":
            blocks.append(f"{route['hostname']} {{\n    redir {route['target']}{{uri}} 308\n}}\n")
    return "\n".join(blocks)


def desired_plan(validated: dict, origin_ipv4: str | None, origin_ipv6: str | None = None) -> dict:
    addresses = [a for a in [origin_ipv4, origin_ipv6] if a]
    records = [{"hostname": r["hostname"], "address": a, "proxied": True,
                "mode": r["mode"], "target": r.get("target")}
               for r in validated["routes"] for a in addresses]
    return {"project": validated["project"], "environment": validated["environment"],
            "routes": validated["routes"], "records": records,
            "certificate_hostnames": [r["hostname"] for r in validated["routes"]],
            "ssl_mode": "strict"}
