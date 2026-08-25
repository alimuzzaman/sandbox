from __future__ import annotations

import json
import hashlib
import secrets
import subprocess
import base64
import shlex
import time
import urllib.error
import urllib.request
import urllib.parse
from getpass import getpass
from pathlib import Path

from sandbox.core import die, info, ok
from sandbox.registry import register
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare
import sandbox.core._secrets as personal_secrets


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data))
        return
    print(f"{data['project']} / {data['environment']}")
    for route in data.get("routes", []):
        suffix = f" -> {route['target']}" if route.get("target") else ""
        print(f"  {route['mode']}: {route['hostname']}{suffix}")


def _zone_for_hostname(client, hostname: str) -> dict:
    """Find the closest Cloudflare zone without assuming a public suffix list."""
    labels = hostname.removeprefix("*.").split(".")
    errors = []
    for offset in range(len(labels) - 1):
        candidate = ".".join(labels[offset:])
        try:
            return client.zone(candidate)
        except cloudflare.CloudflareError as exc:
            errors.append(str(exc))
    raise cloudflare.CloudflareError(errors[-1] if errors else f"no zone found for {hostname}")


def _cloudflare_drift(plan: dict) -> dict:
    """Read only the exact declared records; unrelated DNS is never queried for mutation."""
    if not cloudflare.cloudflare_token():
        return {"configured": False, "records": [], "ssl": None}
    try:
        client = cloudflare.Client()
        zones = {}
        records = []
        for wanted in plan["records"]:
            hostname = wanted["hostname"]
            desired_proxy = wanted["proxied"]
            zone = zones.get(hostname)
            if zone is None:
                zone = _zone_for_hostname(client, hostname)
                zones[hostname] = zone
            record_type = "AAAA" if ":" in wanted["address"] else "A"
            existing = [record for record in client.records(zone["id"], hostname)
                        if record.get("type") == record_type]
            cname_ok = False
            if desired_proxy and wanted.get("mode") == "redirect" and wanted.get("target"):
                target_host = hosting.normalize_hostname(urllib.parse.urlsplit(wanted["target"]).hostname or "", wildcard=False)
                cname_ok = any(record.get("type") == "CNAME" and record.get("proxied") is desired_proxy and
                               hosting.normalize_hostname(str(record.get("content") or ""), wildcard=False) == target_host
                               for record in client.records(zone["id"], hostname))
            records.append({
                "hostname": hostname,
                "type": record_type,
                "desired_address": wanted["address"],
                "proxied": desired_proxy,
                "exists": cname_ok or any(record.get("content") == wanted["address"] and
                                             record.get("proxied") is desired_proxy for record in existing),
            })
        ssl = None
        if any(record["proxied"] for record in plan["records"]):
            ssl = {zone["name"]: client.current_ssl_mode(zone["id"])
                   for zone in {entry["id"]: entry for entry in zones.values()}.values()}
        return {"configured": True, "records": records, "ssl": ssl}
    except cloudflare.CloudflareError as exc:
        # Planning remains useful when Cloudflare credentials expire; do not
        # turn this read-only diagnostic into a mutation prerequisite.
        return {"configured": True, "records": [], "ssl": None, "error": str(exc)}


def _secret_status(validated: dict) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    missing: list[str] = []
    mappings = {**validated["secrets"]["required"], **validated["secrets"]["generated"]}
    source_keys = set(mappings.values())
    basic_auth = validated.get("basic_auth") or {}
    if basic_auth:
        source_keys.add(basic_auth["password_secret"])
    for source_key in sorted(source_keys):
        value = personal_secrets.resolve_secret(source_key)
        if value:
            values[source_key] = value
        else:
            missing.append(source_key)
    return values, missing


def _declared_secret_sources(validated: dict) -> set[str]:
    mappings = {**validated["secrets"]["required"], **validated["secrets"]["generated"]}
    sources = set(mappings.values())
    basic_auth = validated.get("basic_auth") or {}
    if basic_auth:
        sources.add(basic_auth["password_secret"])
    return sources


def _cmd_host_secrets(validated: dict, args) -> None:
    values, missing = _secret_status(validated)
    generated = set(validated["secrets"]["generated"].values())
    set_key = getattr(args, "set_secret", None)
    if set_key:
        declared = _declared_secret_sources(validated)
        if set_key not in declared:
            die(f"'{set_key}' is not declared by this hosting environment")
        value = getpass(f"Value for {set_key}: ")
        if not value:
            die("secret value cannot be empty")
        personal_secrets.write_secret(set_key, value)
        values[set_key] = value
        missing = [key for key in missing if key != set_key]
    if getattr(args, "generate_secrets", False):
        for key in sorted(generated & set(missing)):
            personal_secrets.write_secret(key, personal_secrets.generate_secret())
            values[key] = "[generated]"
        missing = [key for key in missing if key not in generated]
    result = {
        "ok": not missing,
        "project": validated["project"],
        "environment": validated["environment"],
        "required": sorted(_declared_secret_sources(validated)),
        "present": sorted(values),
        "missing": missing,
    }
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{result['project']} / {result['environment']}")
        print("  present: " + (", ".join(result["present"]) or "none"))
        print("  missing: " + (", ".join(result["missing"]) or "none"))


# Markers that identify the line a remote command actually failed on. Compose
# writes build progress to stderr, so the head of the stream is noise; without
# this the reported error is a list of "Image ... Building" lines.
_FAILURE_MARKERS = (
    "failed to solve", "ERROR:", "error:", "error during connect",
    "no such file or directory", "permission denied", "not found",
)

# BuildKit's snapshotter metadata can retain an active snapshot entry whose
# on-disk directory is gone. Every cache-reusing build then fails to stat it,
# with the same snapshot id every run. `docker builder prune` does not clear
# this -- it lives in containerd-overlayfs/metadata_v2.db, not cache.db.
_STALE_SNAPSHOT_MARKER = "failed to stat active key during commit"
_HOST_APPLY_ROLLBACK_RESERVE_MB = 32


def _remote_failure_message(text: str, limit: int = 2000) -> str:
    """Report the decisive failure line rather than the head of the stream."""
    text = remote.redact_text(text or "")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "remote command failed"
    decisive = [line for line in lines if any(marker in line for marker in _FAILURE_MARKERS)]
    message = "\n".join(decisive[-6:] if decisive else lines)
    if len(message) > limit:
        # Truncate the head: the cause lands at the tail of a build log.
        message = "... " + message[-(limit - 4):]
    return message


def _decode_timeout_output(value: object) -> str:
    """Normalize partial ``TimeoutExpired`` output without leaking bytes repr."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def _logged_remote_command(command: str, log_path: str) -> str:
    """Tee one remote command to a protected apply log while preserving rc."""
    log = shlex.quote(log_path)
    status = f"{log}.status.$$"
    return (
        "set +e; "
        f"{{ printf '%s\\n' '[Sandbox] apply command started'; {command}; "
        "rc=$?; printf '[Sandbox] apply command exit %s\\n' \"$rc\"; "
        f"printf '%s' \"$rc\" > {status}; exit \"$rc\"; }} "
        f"2>&1 | tee -a {log}; "
        f"rc=$(cat {status} 2>/dev/null || printf '125'); rm -f {status}; exit \"$rc\""
    )


def _remote_checked(entry: dict, command: str, timeout: int = 180, *,
                   progress=None, log_path: str | None = None) -> str:
    if log_path:
        command = _logged_remote_command(command, log_path)
    try:
        if progress is not None or log_path:
            result = remote.ssh_stream(entry, command, timeout=timeout,
                                       on_line=progress)
        else:
            result = remote.ssh_run(entry, command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = "\n".join(
            value for value in (
                _decode_timeout_output(getattr(exc, "stdout", None)),
                _decode_timeout_output(getattr(exc, "stderr", None)),
            ) if value
        )
        detail = _remote_failure_message(partial)
        suffix = f"; partial output:\n{detail}" if detail != "remote command failed" else ""
        raise RuntimeError(
            f"remote command timed out after {timeout} seconds{suffix}"
        ) from None
    if result.returncode != 0:
        raise RuntimeError(_remote_failure_message(result.stderr or result.stdout))
    return result.stdout or ""


def _disk_free_mb(value: object) -> int | None:
    """Return a bounded integer disk metric without accepting booleans or floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 1_000_000_000 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if 0 <= parsed <= 1_000_000_000 else None
    return None


def _remote_disk_free_mb(entry: dict, home: str) -> int:
    """Read free space for the filesystem containing the Sandbox home.

    Provisioned remotes expose the authenticated diagnostics endpoint. The SSH
    ``df`` fallback keeps hosting usable for older SSH-only registrations, while
    still failing closed if neither read-only observation path works.
    """
    diagnostics_error = None
    if entry.get("control_url") and entry.get("bearer_token"):
        try:
            diagnostics = remote.remote_diagnostics(entry)
            free = _disk_free_mb(diagnostics.get("disk_free_mb"))
            if free is not None:
                return free
            diagnostics_error = "remote diagnostics omitted disk_free_mb"
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            diagnostics_error = str(exc) or type(exc).__name__
    if entry.get("ssh"):
        result = remote.ssh_run(
            entry,
            f"set -eu; df -Pm {shlex.quote(home)} | awk 'NR == 2 {{print $4; exit}}'",
            timeout=30,
        )
        if result.returncode == 0:
            free = _disk_free_mb((result.stdout or "").strip())
            if free is not None:
                return free
        ssh_error = _remote_failure_message(result.stderr or result.stdout)
        if diagnostics_error:
            raise hosting.HostingError(
                f"could not read remote disk availability ({diagnostics_error}; {ssh_error})"
            )
        raise hosting.HostingError(f"could not read remote disk availability ({ssh_error})")
    if diagnostics_error:
        raise hosting.HostingError(f"could not read remote disk availability ({diagnostics_error})")
    raise hosting.HostingError("host apply requires a provisioned remote disk diagnostic")


def _prepare_host_apply(entry: dict, home: str, validated: dict) -> str | None:
    """Reject low-disk applies and reserve rollback space before mutation.

    The reservation is released on success and before rollback. Deleting it
    first gives Caddy/DNS rollback a bounded amount of filesystem headroom even
    when a Compose build consumes the remaining space after preflight.
    """
    # Empty entries are used by offline unit tests and fail later on their first
    # real remote operation. Every registered remote has one of these identities.
    if not entry or not (entry.get("ssh") or (entry.get("control_url") and entry.get("bearer_token"))):
        return None
    minimum = int(validated["deploy"].get("min_free_disk_mb", 1024))
    free = _remote_disk_free_mb(entry, home)
    required = minimum + _HOST_APPLY_ROLLBACK_RESERVE_MB
    if free < required:
        raise hosting.HostingError(
            f"insufficient remote disk for host apply: {free} MiB free, "
            f"requires at least {required} MiB (including rollback reserve)"
        )
    reservation = (
        f"{home}/.sandbox/host-apply-"
        f"{validated['project']}-{validated['environment']}.rollback.reserve"
    )
    parent = str(__import__("posixpath").dirname(reservation))
    command = (
        f"set -eu; mkdir -p {shlex.quote(parent)}; "
        f"if command -v fallocate >/dev/null 2>&1 && "
        f"fallocate -l {_HOST_APPLY_ROLLBACK_RESERVE_MB}M {shlex.quote(reservation)}; then :; "
        f"else dd if=/dev/zero of={shlex.quote(reservation)} "
        f"bs=1048576 count={_HOST_APPLY_ROLLBACK_RESERVE_MB} conv=fsync status=none; fi; "
        f"chmod 0600 {shlex.quote(reservation)}"
    )
    _remote_checked(entry, command, timeout=60)
    return reservation


def _release_host_apply_reservation(entry: dict, reservation: str | None) -> None:
    if reservation:
        _remote_checked(entry, f"rm -f {shlex.quote(reservation)}", timeout=30)


def _remote_basic_auth_hash(entry: dict, password: str) -> str:
    """Hash a Basic Auth password on the remote without placing it in argv."""
    result = remote.ssh_run(entry, "caddy hash-password", timeout=60,
                            input_data=password + "\n")
    value = (result.stdout or "").strip()
    if result.returncode != 0 or not value or "\n" in value or not value.startswith("$"):
        raise RuntimeError("remote Caddy could not generate the Basic Auth password hash")
    return value


def _write_remote_text(entry: dict, path: str, text: str, mode: str = "0600") -> None:
    payload = base64.b64encode(text.encode()).decode()
    parent = str(__import__("posixpath").dirname(path))
    command = (
        f"mkdir -p {shlex.quote(parent)} && "
        f"printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(path)} && "
        f"chmod {mode} {shlex.quote(path)}"
    )
    _remote_checked(entry, command)


def _compose_prefix(validated: dict, source_dir: str, override_path: str, env_path: str) -> str:
    parts = [f"SANDBOX_HOST_ENV_FILE={env_path}", "docker", "compose", "--env-file", env_path,
             "-p", hosting.compose_project_name(validated)]
    for file_name in validated["compose"]["files"]:
        parts.extend(["-f", str(__import__("pathlib").Path(source_dir) / file_name)])
    parts.extend(["-f", override_path])
    return " ".join(shlex.quote(part) for part in parts)


def _configure_host_caddy(entry: dict, name: str, content: str, previous: str | None = None) -> None:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    temporary = f"/tmp/{name}.caddy"
    _write_remote_text(entry, temporary, content, "0644")
    command = (
        "set -e; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        "$SUDO install -d -m 0755 /etc/caddy/conf.d; "
        "if [ ! -f /etc/caddy/Caddyfile ]; then "
        "printf '%s\\n' 'import /etc/caddy/conf.d/*.caddy' | $SUDO tee /etc/caddy/Caddyfile >/dev/null; "
        "elif ! $SUDO grep -q 'import /etc/caddy/conf.d/\\*.caddy' /etc/caddy/Caddyfile; then "
        "printf '\\n%s\\n' 'import /etc/caddy/conf.d/*.caddy' | $SUDO tee -a /etc/caddy/Caddyfile >/dev/null; fi; "
        f"$SUDO install -m 0644 {shlex.quote(temporary)} {shlex.quote(path)}; "
        "$SUDO caddy validate --config /etc/caddy/Caddyfile; $SUDO systemctl reload caddy"
    )
    _remote_checked(entry, command)


def _restore_host_caddy(entry: dict, name: str, previous: str | None) -> None:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    if previous is None:
        command = (
            "set -e; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
            f"$SUDO rm -f {shlex.quote(path)}; $SUDO caddy validate --config /etc/caddy/Caddyfile; "
            "$SUDO systemctl reload caddy"
        )
        _remote_checked(entry, command)
        return
    _configure_host_caddy(entry, name, previous)


def _read_remote_optional(entry: dict, path: str) -> str | None:
    result = remote.ssh_run(entry, f"test -f {shlex.quote(path)} && cat {shlex.quote(path)}", timeout=30)
    return result.stdout if result.returncode == 0 else None


def _origin_certificate(entry: dict, validated: dict, runtime: dict, state_entry: dict,
                        client: cloudflare.Client, home: str) -> tuple[str, str, dict]:
    name = f"sandbox-host-{validated['project']}-{validated['environment']}"
    base = f"/etc/caddy/certs/{name}"
    cert_path, key_path, csr_path = f"{base}/origin.pem", f"{base}/origin.key", f"{base}/origin.csr"
    certificate = state_entry.get("certificate") if isinstance(state_entry, dict) else None
    present = remote.ssh_run(entry, f"test -s {shlex.quote(cert_path)} -a -s {shlex.quote(key_path)}", timeout=30)
    if present.returncode == 0:
        return cert_path, key_path, certificate or {"id": None, "hostnames": runtime["certificate_hostnames"]}
    primary = next(route["hostname"] for route in validated["routes"] if route.get("primary"))
    command = (
        "set -e; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"$SUDO install -d -o root -g caddy -m 0750 {shlex.quote(base)}; "
        f"if [ ! -s {shlex.quote(key_path)} ]; then $SUDO openssl ecparam -name prime256v1 -genkey -noout -out {shlex.quote(key_path)}; $SUDO chown root:caddy {shlex.quote(key_path)}; $SUDO chmod 0640 {shlex.quote(key_path)}; fi; "
        f"$SUDO openssl req -new -key {shlex.quote(key_path)} -subj {shlex.quote('/CN=' + primary)} -out {shlex.quote(csr_path)}; "
        f"$SUDO cat {shlex.quote(csr_path)}"
    )
    csr = _remote_checked(entry, command, timeout=60)
    issued = client.create_origin_certificate(csr, runtime["certificate_hostnames"])
    certificate_text = issued.get("certificate")
    if not isinstance(certificate_text, str) or not certificate_text.strip():
        raise cloudflare.CloudflareError("Cloudflare did not return an Origin CA certificate")
    temporary = f"/tmp/{name}-origin.pem"
    _write_remote_text(entry, temporary, certificate_text, "0644")
    _remote_checked(entry, (
        "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"$SUDO install -o root -g caddy -m 0644 {shlex.quote(temporary)} {shlex.quote(cert_path)}; rm -f {shlex.quote(temporary)}"
    ))
    return cert_path, key_path, {"id": issued.get("id"), "hostnames": runtime["certificate_hostnames"]}


def _build_checked(entry: dict, prefix: str, command: str, service_args: str,
                   timeout: int = 900, *, progress=None,
                   log_path: str | None = None) -> str:
    """Run a building compose command, recovering from a stale BuildKit snapshot.

    A single `--no-cache` build regenerates the affected layer as a valid
    committed snapshot; cache-reusing builds hit the good one afterwards. This
    is preferred over clearing the snapshotter metadata, which needs dockerd
    stopped and so takes every container on the host down with it.
    """
    try:
        return _remote_checked(entry, command, timeout=timeout,
                               progress=progress, log_path=log_path)
    except RuntimeError as error:
        if _STALE_SNAPSHOT_MARKER not in str(error):
            raise
        message = "stale BuildKit snapshot on the remote; rebuilding without cache"
        if progress is None:
            info(message)
        else:
            progress(message)
        _remote_checked(entry, f"{prefix} build --no-cache {service_args}",
                        timeout=timeout * 2, progress=progress, log_path=log_path)
        return _remote_checked(entry, command, timeout=timeout,
                               progress=progress, log_path=log_path)


def _run_compose(entry: dict, validated: dict, source_dir: str, runtime_dir: str,
                 runtime: dict, progress=None, apply_log: str | None = None) -> None:
    override = f"{runtime_dir}/compose.override.yml"
    env_file = f"{runtime_dir}/environment.env"
    _write_remote_text(entry, override, runtime["compose_override"], "0600")
    _write_remote_text(entry, env_file, runtime["environment"], "0600")
    if apply_log:
        _remote_checked(
            entry,
            f"umask 077; : >> {shlex.quote(apply_log)}; chmod 0600 {shlex.quote(apply_log)}",
            timeout=30,
        )
    prefix = _compose_prefix(validated, source_dir, override, env_file)
    runtime_services = [
        shlex.quote(validated["compose"]["service"]),
        *(shlex.quote(service) for service in validated["compose"].get("background_services", [])),
    ]
    service_args = " ".join(runtime_services)
    # `compose.build: false` deploys config, secrets and routing onto whatever
    # image the remote already has. Compose still builds a service with no
    # image at all, so a first deploy works either way.
    build = validated["compose"].get("build", True)
    build_timeout = validated["compose"].get("build_timeout_seconds", 900)
    build_flag = " --build" if build else ""
    if progress is not None:
        progress(
            f"Compose build/recreate started (timeout {build_timeout}s; "
            f"build={'enabled' if build else 'disabled'})"
        )
    # Replace the image's anonymous application volume on each deployment so
    # code/config changes are not shadowed by a previous container. Persistent
    # data must be declared as named volumes (for WordPress: database/uploads).
    _build_checked(
        entry, prefix,
        f"{prefix} up -d{build_flag} --force-recreate --renew-anon-volumes --remove-orphans {service_args}",
        service_args, timeout=build_timeout, progress=progress, log_path=apply_log,
    )
    if progress is not None:
        progress("Compose build/recreate completed")
    for init_service in validated["compose"].get("init_services", []):
        # `compose up --build <web>` does not build a distinct image tagged for
        # a one-shot job service. Build it explicitly so an updated initializer
        # is never run from a previous deployment's image.
        if build:
            if progress is not None:
                progress(f"Init service {init_service} build started")
            _build_checked(
                entry, prefix, f"{prefix} build {shlex.quote(init_service)}",
                shlex.quote(init_service), timeout=build_timeout,
                progress=progress, log_path=apply_log,
            )
            if progress is not None:
                progress(f"Init service {init_service} build completed")
        _remote_checked(
            entry, f"{prefix} --profile jobs run --rm {shlex.quote(init_service)}",
            timeout=900, progress=progress, log_path=apply_log,
        )
    _remote_checked(
        entry, f"{prefix} up -d {service_args}", timeout=300,
        progress=progress, log_path=apply_log,
    )


def _read_host_logs(validated: dict, entry: dict, *, lines: int) -> str:
    if not 1 <= lines <= 1000:
        raise hosting.HostingError("--lines must be between 1 and 1000")
    home = remote.resolve_sandbox_home(entry)
    source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    prefix = _compose_prefix(
        validated,
        source_dir,
        f"{runtime_dir}/compose.override.yml",
        f"{runtime_dir}/environment.env",
    )
    services = [
        validated["compose"]["service"],
        *validated["compose"].get("background_services", []),
    ]
    # Compose exits with "no such service" when a manifest still names a
    # background service that is absent from the deployed compose files. That
    # should not hide logs from the services that are present, nor turn a
    # topology-drift observation into a generic command failure. Read the
    # declared service names first, then request logs only for that intersection
    # and emit a bounded diagnostic for every missing declaration.
    declared = _remote_checked(
        entry, f"{prefix} config --services", timeout=60,
    )
    available = {
        line.strip() for line in (declared or "").splitlines()
        if line.strip()
    }
    present = [service for service in services if service in available]
    missing = [service for service in services if service not in available]
    chunks = [
        f"[missing service: {service}]\n" for service in missing
    ]
    if present:
        service_args = " ".join(shlex.quote(service) for service in present)
        chunks.append(_remote_checked(
            entry,
            f"{prefix} logs --no-color --tail {lines} {service_args}",
            timeout=60,
        ))
    if not chunks:
        chunks.append("[no declared services found in deployed compose configuration]\n")
    return "".join(chunks)


def _host_runtime_status(validated: dict, entry: dict, remote_name: str,
                         state: dict) -> dict:
    """Read deployed revision and bounded Compose health without mutation."""
    key = hosting.state_key(remote_name, validated)
    recorded = dict((state.get("hosts") or {}).get(key) or {})
    services = [
        validated["compose"]["service"],
        *validated["compose"].get("background_services", []),
    ]
    result = {
        "project": validated["project"],
        "environment": validated["environment"],
        "remote": remote_name,
        "deployed_revision": recorded.get("commit"),
        "state_record": "present" if recorded else "missing",
        "services": [],
        "health": {"state": "unavailable", "reason": "remote status not observed"},
    }
    if not entry.get("provisioned"):
        result["health"] = {"state": "unavailable", "reason": "remote is not provisioned"}
        return result
    try:
        home = remote.resolve_sandbox_home(entry)
        source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
        runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
        prefix = _compose_prefix(
            validated, source_dir,
            f"{runtime_dir}/compose.override.yml",
            f"{runtime_dir}/environment.env",
        )
        raw = _remote_checked(entry, f"{prefix} ps --format json", timeout=60)
        rows = []
        for line in (raw or "").splitlines():
            try:
                item = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                rows.append(item)
        observed = {str(item.get("Service")): item for item in rows
                    if item.get("Service")}
        for service in services:
            item = observed.get(service) or {}
            result["services"].append({
                "service": service,
                "state": item.get("State") or "unknown",
                "health": item.get("Health") or "unknown",
            })
        if not rows:
            result["health"] = {"state": "unknown", "reason": "Compose returned no service rows"}
        elif all(item["state"] == "running" and item["health"] in {"healthy", "unknown"}
                 for item in result["services"]):
            result["health"] = {"state": "ready"}
        else:
            result["health"] = {"state": "degraded", "reason": "one or more services are not running/healthy"}
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        result["health"] = {"state": "unavailable", "reason": remote.redact_text(str(exc))[:500]}
    return result


def _issue_host_autologin(validated: dict, entry: dict, remote_name: str,
                          state: dict, ttl_seconds: int | None) -> dict:
    config = validated.get("autologin")
    if not config:
        raise hosting.HostingError("this hosting environment does not declare autologin")
    ttl = config["ttl_seconds"] if ttl_seconds is None else ttl_seconds
    if not isinstance(ttl, int) or not 60 <= ttl <= 3600:
        raise hosting.HostingError("--ttl-seconds must be between 60 and 3600")
    key = hosting.state_key(remote_name, validated)
    host_state = state.get("hosts", {}).get(key)
    if not isinstance(host_state, dict):
        raise hosting.HostingError("host is not deployed; run `./sb host apply` before issuing a login URL")
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + ttl
    plugin = hosting.render_autologin_mu_plugin(
        hashlib.sha256(token.encode()).hexdigest(), config["user"], expires_at,
    )
    home = remote.resolve_sandbox_home(entry)
    source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    prefix = _compose_prefix(validated, source_dir, f"{runtime_dir}/compose.override.yml",
                             f"{runtime_dir}/environment.env")
    payload = base64.b64encode(plugin.encode()).decode()
    target = config["container_path"]
    install = (
        f"set -eu; mkdir -p {shlex.quote(str(__import__('posixpath').dirname(target)))}; "
        f"cat > {shlex.quote(target)}; chmod 0644 {shlex.quote(target)}"
    )
    service = shlex.quote(validated["compose"]["service"])
    _remote_checked(entry, (
        f"printf %s {shlex.quote(payload)} | base64 -d | {prefix} exec -T {service} "
        f"sh -c {shlex.quote(install)}"
    ), timeout=60)
    _remote_checked(entry, f"{prefix} exec -T {service} test -s {shlex.quote(target)}", timeout=30)
    host_state["autologin"] = {"user": config["user"], "expires_at": expires_at}
    hosting.save_host_state(state)
    return {"url": hosting.autologin_url(validated, token, expires_at), "expires_at": expires_at,
            "one_time": True, "user": config["user"]}


def _ensure_host_source(entry: dict, home: str, project: str) -> str:
    """Create a remote Git worktree for a hosted Compose project, not a WP plugin."""
    target = f"{home}/deploy-src/hosts/{project}"
    command = (
        f"mkdir -p {shlex.quote(target)}; cd {shlex.quote(target)}; "
        "if [ ! -d .git ]; then git init -q; git config receive.denyCurrentBranch updateInstead; fi"
    )
    _remote_checked(entry, command, timeout=60)
    return target


def _verify_remote_health(entry: dict, runtime: dict, progress=None) -> None:
    port = runtime["loopback_port"]
    path = runtime["healthcheck"]["path"]
    minimum, maximum = min(runtime["healthcheck"]["statuses"]), max(runtime["healthcheck"]["statuses"])
    command = (
        "curl -fsS --max-time 15 -o /dev/null -w '%{http_code}' "
        f"http://127.0.0.1:{port}{shlex.quote(path)}"
    )
    last_error = "no response"
    # A recreated Compose service can reset its loopback connection between
    # `up -d` returning and its healthcheck becoming green. Treat that short
    # startup window as pending, not as a failed deployment.
    for attempt in range(30):
        result = remote.ssh_run(entry, command, timeout=30)
        output = (result.stdout or "").strip()
        if result.returncode == 0:
            try:
                code = int(output)
            except ValueError:
                last_error = "remote healthcheck returned a non-status response"
            else:
                if minimum <= code <= maximum:
                    return
                last_error = f"remote healthcheck returned {code}, expected {minimum}-{maximum}"
        else:
            last_error = (result.stderr or output or "remote healthcheck command failed").strip()[:500]
        if attempt == 0 or (attempt + 1) % 5 == 0:
            message = (
                f"remote healthcheck pending ({min((attempt + 1) * 2, 60)}s/60s): "
                f"{remote.redact_text(last_error)}"
            )
            if progress is None:
                info(message)
            else:
                progress(message)
        time.sleep(2)
    raise RuntimeError(
        f"remote healthcheck did not return {minimum}-{maximum} within 60 seconds: {last_error}"
    )


def _verify_edge(
    routes: list[dict],
    *,
    healthcheck_path: str = "/",
    basic_auth_enabled: bool = False,
    basic_auth_credentials: tuple[str, str] | None = None,
) -> None:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        """Treat a redirect response as a successful reachable route.

        The deploy verifier validates edge reachability, not an application's
        redirect destination. Following a WordPress network redirect can loop
        before content has been configured and would mask a healthy edge.
        """
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    for route in routes:
        if route["hostname"].startswith("*."):
            continue
        path = healthcheck_path if route.get("mode") == "serve" else "/"
        endpoint = f"https://{route['hostname']}{path}"
        request_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Sandbox hosting verification)",
        }
        request = urllib.request.Request(endpoint, method="GET", headers=request_headers)
        last_error = None
        # Cloudflare can need several minutes to activate edge certificates after
        # proxying a previously DNS-only alias for the first time.
        for _ in range(30):
            try:
                with opener.open(request, timeout=15) as response:
                    if 200 <= response.status < 400:
                        last_error = None
                        break
            except urllib.error.HTTPError as exc:
                if (300 <= exc.code < 400
                        or (basic_auth_enabled and route.get("mode") == "serve" and exc.code == 401)):
                    last_error = None
                    break
                last_error = exc
            except Exception as exc:  # Edge propagation is external and transient.
                last_error = exc
            time.sleep(10)
        if last_error:
            # Do not stringify an exception carrying a request object: some
            # urllib implementations include its headers in ``repr``.  The
            # public error is intentionally bounded and credential-free.
            detail = (
                f"HTTP {getattr(last_error, 'code', 'error')}"
                if isinstance(last_error, urllib.error.HTTPError)
                else "route unavailable"
            )
            raise RuntimeError(
                f"edge verification failed for {route['hostname']}: {detail}"
            )
        if basic_auth_credentials and route.get("mode") == "serve":
            username, password = basic_auth_credentials
            if not isinstance(username, str) or not isinstance(password, str):
                raise RuntimeError("Basic Auth verification credentials are unavailable")
            # The credential is obtained only through _secret_status() (the
            # registered personal secret broker) and exists in memory for this
            # request.  It is never part of a URL, subprocess argv, or log.
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            auth_request = urllib.request.Request(
                endpoint,
                method="GET",
                headers={**request_headers, "Authorization": f"Basic {encoded}"},
            )
            auth_error = None
            try:
                with opener.open(auth_request, timeout=15) as response:
                    if 200 <= response.status < 400:
                        auth_error = None
                    else:
                        auth_error = f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                auth_error = f"HTTP {exc.code}"
            except Exception:
                auth_error = "route unavailable"
            if auth_error:
                raise RuntimeError(
                    f"authenticated edge verification failed for {route['hostname']}: {auth_error}"
                )


def _validate_apply_source(validated: dict) -> str:
    """Reject an ineligible deployment entirely on the local machine."""
    branch = remote.current_branch(validated["project_root"])
    policy = validated["deploy"]
    if branch not in policy["allowed_branches"] and "*" not in policy["allowed_branches"]:
        allowed = ", ".join(policy["allowed_branches"])
        raise hosting.HostingError(
            f"branch '{branch}' is not allowed for {validated['environment']} "
            f"(allowed: {allowed})"
        )
    if policy["require_clean"]:
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=validated["project_root"],
            capture_output=True, text=True, check=False,
        )
        if status.returncode != 0:
            raise RuntimeError("could not inspect the deployment working tree")
        if status.stdout.strip():
            raise hosting.HostingError(
                f"{validated['environment']} requires a clean working tree"
            )
    return branch


def _apply_host(validated: dict, entry: dict, remote_name: str, runtime: dict,
                state: dict, allow_zone_ssl_change: bool, branch: str,
                progress=None) -> dict:
    secret_values, missing = _secret_status(validated)
    if missing:
        raise hosting.HostingError("missing hosting secrets: " + ", ".join(missing))
    home = remote.resolve_sandbox_home(entry)
    reservation = _prepare_host_apply(entry, home, validated)
    try:
        target = _ensure_host_source(entry, home, validated["project"])
    except Exception:
        try:
            _release_host_apply_reservation(entry, reservation)
        except Exception as cleanup_error:
            raise hosting.HostingError(
                f"host staging failed; rollback-space cleanup failed: {cleanup_error}"
            ) from cleanup_error
        raise
    manifest_root = validated.get("manifest_root")
    source_root = validated.get("source_root")
    nested_source = (
        bool(validated.get("source_root_nested"))
        or (
            source_root and manifest_root
            and Path(source_root).resolve() != Path(manifest_root).resolve()
        )
    )
    sha = remote.push_commits(
        entry, validated["project_root"], target, branch,
        source_root=source_root if nested_source else None,
    )
    diff, untracked = remote.capture_uncommitted(validated["project_root"])
    require_clean = validated["deploy"]["require_clean"]
    if require_clean and (diff or untracked):
        raise hosting.HostingError(
            f"{validated['environment']} working tree changed while the source was being pushed"
        )
    runtime["environment"] = hosting.render_env_file(
        validated, secret_values, pushed_commit_sha=sha,
    )
    client = cloudflare.Client()
    remote.reset_target_to(entry, target, sha)
    if not require_clean:
        remote.apply_uncommitted(entry, target, validated["project_root"], diff, untracked)
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    apply_log = f"{runtime_dir}/apply.log"
    stream_progress = None
    if progress is not None:
        def stream_progress(message: str) -> None:
            safe = str(message)
            for secret in secret_values.values():
                if secret:
                    safe = safe.replace(secret, "[REDACTED]")
            progress(safe)
    key = runtime["key"]
    previous_entry = dict(state["hosts"].get(key) or {})
    caddy_name = f"sandbox-host-{validated['project']}-{validated['environment']}"
    caddy_path = f"/etc/caddy/conf.d/{caddy_name}.caddy"
    previous_caddy = _read_remote_optional(entry, caddy_path)
    changes: list[dict] = []
    ssl_previous: dict[str, str | None] = {}

    def rollback() -> None:
        nonlocal reservation
        failures: list[str] = []
        try:
            _release_host_apply_reservation(entry, reservation)
            reservation = None
        except Exception as exc:
            failures.append(f"rollback-space cleanup: {exc}")
        for change in reversed(changes):
            try:
                client.restore_record(change["zone_id"], change["previous"], change["created_id"])
            except Exception as exc:
                failures.append(f"DNS restore for {change['zone_id']}: {exc}")
        for zone_id, mode in ssl_previous.items():
            try:
                if mode and mode != "strict":
                    client.ssl_mode(zone_id, mode)
            except Exception as exc:
                failures.append(f"SSL mode restore for {zone_id}: {exc}")
        try:
            _restore_host_caddy(entry, caddy_name, previous_caddy)
        except Exception as exc:
            failures.append(f"Caddy restore: {exc}")
        if failures:
            raise hosting.HostingError("; ".join(failures))

    def apply() -> None:
        nonlocal reservation
        if stream_progress is not None:
            stream_progress(f"source reset to {sha}")
            stream_progress(f"apply log: {apply_log}")
        _run_compose(
            entry, validated, target, runtime_dir, runtime,
            stream_progress, apply_log,
        )
        _verify_remote_health(entry, runtime, stream_progress)
        if stream_progress is not None:
            stream_progress("remote healthcheck passed")
        proxied = validated["cloudflare"]["proxied"]
        cert_path = key_path = None
        certificate = None
        if proxied:
            cert_path, key_path, certificate = _origin_certificate(
                entry, validated, runtime, previous_entry, client, home,
            )
        basic_hash = None
        if validated.get("basic_auth"):
            password_secret = validated["basic_auth"]["password_secret"]
            basic_hash = _remote_basic_auth_hash(entry, secret_values[password_secret])
        runtime["caddyfile"] = hosting.caddyfile(
            validated, runtime["loopback_port"], cert_path, key_path, basic_hash,
        )
        _configure_host_caddy(entry, caddy_name, runtime["caddyfile"])
        zones: dict[str, dict] = {}
        for wanted in runtime["records"]:
            hostname = wanted["hostname"]
            zone = zones.get(hostname)
            if zone is None:
                zone = _zone_for_hostname(client, hostname)
                zones[hostname] = zone
            if proxied:
                current = client.current_ssl_mode(zone["id"])
                if current != "strict":
                    if not allow_zone_ssl_change:
                        raise RuntimeError(f"zone {zone['name']} is {current or 'unset'}; pass --allow-zone-ssl-change")
                    ssl_previous[zone["id"]] = current
                    client.ssl_mode(zone["id"], "strict")
            kind = "AAAA" if ":" in wanted["address"] else "A"
            all_records = client.records(zone["id"], hostname)
            cname = next((record for record in all_records if record.get("type") == "CNAME"), None)
            if cname and not proxied:
                raise RuntimeError(
                    f"declared hostname {hostname} has a conflicting CNAME; "
                    "DNS-only hosting requires an A or AAAA record at the Sandbox origin"
                )
            if cname and wanted.get("mode") == "redirect" and wanted.get("target"):
                target_host = hosting.normalize_hostname(urllib.parse.urlsplit(wanted["target"]).hostname or "", wildcard=False)
                cname_target = hosting.normalize_hostname(str(cname.get("content") or ""), wildcard=False)
                if cname_target == target_host:
                    updated = client.update_record(zone["id"], cname, proxied=proxied)
                    changes.append({"zone_id": zone["id"], "previous": cname, "created_id": updated.get("id")})
                    continue
                raise RuntimeError(f"declared hostname {hostname} has a conflicting CNAME to {cname_target}")
            previous = next((record for record in all_records if record.get("type") == kind), None)
            created = client.upsert_address(zone["id"], hostname, wanted["address"], proxied=proxied)
            changes.append({"zone_id": zone["id"], "previous": previous, "created_id": created.get("id")})
        basic_credentials = None
        if validated.get("basic_auth"):
            auth = validated["basic_auth"]
            basic_credentials = (auth["username"], secret_values[auth["password_secret"]])
        verify_kwargs = {
            "healthcheck_path": runtime["healthcheck"]["path"],
            "basic_auth_enabled": bool(validated.get("basic_auth")),
        }
        if basic_credentials is not None:
            verify_kwargs["basic_auth_credentials"] = basic_credentials
        _verify_edge(validated["routes"], **verify_kwargs)
        _release_host_apply_reservation(entry, reservation)
        reservation = None
        state["hosts"][key] = {"loopback_port": runtime["loopback_port"], "compose_project": runtime["compose_project"],
                               "certificate": certificate, "records": changes, "commit": sha,
                               "caddy_name": caddy_name}
        hosting.save_host_state(state)

    hosting.apply_with_rollback(apply, rollback)
    result = {
        "commit": sha,
        "derived_environment": runtime.get("derived_environment", []),
    }
    if progress is not None:
        result["apply_log"] = apply_log
    return result


def cmd_host(cfg, args) -> None:
    try:
        validated = hosting.validate_manifest(args.project_dir or ".", args.environment)
    except hosting.HostingError as exc:
        die(str(exc))
    if args.action == "secrets":
        _cmd_host_secrets(validated, args)
        return
    if args.action == "validate":
        _emit({"ok": True, **validated}, args.json)
        return
    if not args.remote:
        die("--remote is required for host plan, status, apply, logs, and login-url")
    branch = None
    if args.action == "apply":
        if not args.confirm:
            die("host apply is protected; review `./sb host plan` then pass --confirm")
        try:
            branch = _validate_apply_source(validated)
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            die(str(exc))
    entry = remote.get_remote(args.remote)
    if not entry:
        die(f"no remote named '{args.remote}'")
    state = hosting.load_host_state()
    if args.action == "status":
        result = _host_runtime_status(validated, entry, args.remote, state)
        if args.json:
            print(json.dumps({"ok": True, **result}, sort_keys=True))
        else:
            print(f"{result['project']} / {result['environment']} ({result['remote']})")
            print(f"  deployed revision: {result['deployed_revision'] or 'unknown'}")
            print(f"  health: {result['health']['state']}")
            for service in result["services"]:
                print(f"  {service['service']}: {service['state']} ({service['health']})")
            if result["health"].get("reason"):
                print(f"  reason: {result['health']['reason']}")
        return
    if args.action == "logs":
        if not entry.get("provisioned"):
            die(f"remote '{args.remote}' is not provisioned")
        try:
            if getattr(args, "apply_log", False):
                home = remote.resolve_sandbox_home(entry)
                path = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}/apply.log"
                output = _remote_checked(
                    entry,
                    f"test -f {shlex.quote(path)} && tail -n {int(args.lines)} {shlex.quote(path)}",
                    timeout=60,
                )
            else:
                output = _read_host_logs(validated, entry, lines=args.lines)
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            die(str(exc))
        if args.json:
            print(json.dumps({"ok": True, "project": validated["project"],
                              "environment": validated["environment"], "output": output}))
        else:
            print(output, end="" if output.endswith("\n") or not output else "\n")
        return
    if args.action == "login-url":
        if not args.confirm:
            die("host login-url is protected; pass --confirm to issue a one-time admin link")
        if not entry.get("provisioned"):
            die(f"remote '{args.remote}' is not provisioned")
        try:
            result = _issue_host_autologin(validated, entry, args.remote, state,
                                           getattr(args, "ttl_seconds", None))
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            die(str(exc))
        if args.json:
            print(json.dumps({"ok": True, **result}))
        else:
            print(result["url"])
        return
    plan = hosting.desired_plan(validated, entry.get("origin_ipv4"), entry.get("origin_ipv6"))
    # Host operations currently require an explicit remote.  Surface that
    # choice in both plan and apply evidence so a future inferred-target path
    # cannot silently change machines.
    plan["remote"] = args.remote
    plan["remote_selection"] = "explicit"
    plan["runtime"] = hosting.desired_runtime(validated, args.remote, state)
    plan["runtime"]["records"] = plan["records"]
    _, missing = _secret_status(validated)
    plan["secrets"] = {"missing": missing, "required": sorted(_declared_secret_sources(validated))}
    plan["basic_auth"] = {"enabled": bool(validated.get("basic_auth")),
                           "username": (validated.get("basic_auth") or {}).get("username")}
    plan["cloudflare"] = _cloudflare_drift(plan)
    if args.action == "plan":
        _emit({"ok": True, **plan}, args.json)
        return
    if not entry.get("provisioned"):
        die(f"remote '{args.remote}' is not provisioned")
    if not entry.get("origin_ipv4"):
        die(f"remote '{args.remote}' has no public origin address; run `./sb remote set-origin`")
    ssl = plan["cloudflare"].get("ssl") if isinstance(plan.get("cloudflare"), dict) else None
    if ssl and not getattr(args, "allow_zone_ssl_change", False):
        non_strict = [zone for zone, mode in ssl.items() if mode != "strict"]
        if non_strict:
            die("these zones require --allow-zone-ssl-change: " + ", ".join(non_strict))
    progress = (lambda _message: None) if args.json else (
        lambda message: info(f"host apply: {message}")
    )
    try:
        result = _apply_host(
            validated, entry, args.remote, plan["runtime"], state,
            bool(getattr(args, "allow_zone_ssl_change", False)), branch, progress,
        )
    except (hosting.HostingError, cloudflare.CloudflareError, RuntimeError,
            subprocess.SubprocessError, OSError) as exc:
        die(str(exc))
    evidence = {
        "ok": True,
        "project": validated["project"],
        "environment": validated["environment"],
        "remote": args.remote,
        "remote_selection": "explicit",
        "commit": result["commit"],
        "derived_environment": result["derived_environment"],
    }
    if result.get("apply_log"):
        evidence["apply_log"] = result["apply_log"]
    if args.json:
        print(json.dumps(evidence))
    else:
        ok(
            f"applied {validated['project']} / {validated['environment']} to {args.remote} "
            f"at {result['commit']} (remote_selection=explicit)"
        )


register({"host": cmd_host})
