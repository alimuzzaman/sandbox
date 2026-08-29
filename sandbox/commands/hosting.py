from __future__ import annotations

import json
import hashlib
import os
import re
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
from sandbox.core._paths import RUNTIME_DIR
from sandbox.registry import register
from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService
from sandbox.sync.models import failure_envelope, validate_sync_envelope
from sandbox.transports.remote_sync import HostSourceSyncTransport
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare
import sandbox.core._secrets as personal_secrets


_HOST_SYNC_WATCH_EXCLUDES = frozenset({
    ".git", ".sandbox", ".cache", ".pytest_cache", ".mypy_cache",
    "node_modules", "vendor", "build", "dist", "out", "coverage",
    "runtime", "cache", "caches", "logs", "tmp", "temp", "uploads",
    "storage", "__pycache__", ".venv", "venv",
})

_HOST_OBSERVATION_MAX_SERVICES = 16
_HOST_OBSERVATION_MAX_KEYS = 16
_HOST_OBSERVATION_MAX_ROWS = 64
_HOST_OBSERVATION_MAX_CONFIGURED_SERVICES = 64
_HOST_OBSERVATION_MAX_PHASES = 2 + _HOST_OBSERVATION_MAX_SERVICES
_HOST_OBSERVATION_MAX_OUTPUT_BYTES = 64 * 1024
_HOST_OBSERVATION_MAX_RECEIPT_BYTES = 128 * 1024
_HOST_SOURCE_SNAPSHOT_MAX_FILES = 4096
_HOST_SOURCE_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024


def _host_sync_watch_signature(source_root: str) -> str:
    """Metadata-only fingerprint; content screening stays in SyncService."""
    digest = hashlib.sha256()
    count = 0
    root = Path(source_root).resolve(strict=True)
    for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(
            name for name in directories if name.lower() not in _HOST_SYNC_WATCH_EXCLUDES
        )
        for name in sorted(filenames):
            path = Path(directory) / name
            try:
                stat_result = path.lstat()
            except OSError:
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8", "surrogateescape"))
            digest.update(
                f"\0{stat_result.st_mode}\0{stat_result.st_size}\0"
                f"{stat_result.st_mtime_ns}\0{stat_result.st_ino}".encode()
            )
            count += 1
            if count > 20_000:
                raise hosting.HostingError("host sync watch source exceeds the 20000-file watch bound")
    digest.update(f"\0{count}".encode())
    return digest.hexdigest()


def _host_sync_context(validated: dict, remote_name: str) -> tuple[str, str, dict]:
    source_root = str(Path(validated["source_root"]).resolve())
    project = str(validated["project"])
    environment = str(validated["environment"])
    digest = hashlib.sha256(
        f"{source_root}\0{remote_name}\0{project}\0{environment}".encode()
    ).hexdigest()
    workspace_id = "host-" + hashlib.sha256(
        f"{project}\0{environment}".encode()
    ).hexdigest()[:32]
    return workspace_id, source_root, {"identity": f"host:{digest}", "root": source_root}


def _host_sync_service(validated: dict, remote_name: str, entry: dict):
    workspace_id, source_root, identity = _host_sync_context(validated, remote_name)
    service = SyncService(
        repository=SyncRepository(RUNTIME_DIR / "sync" / "journal.json"),
        transport_factory=lambda: HostSourceSyncTransport(
            remote_lookup=lambda _name: entry,
            ssh_run=remote.ssh_run,
            ssh_process=remote.ssh_process,
            resolve_home=remote.resolve_sandbox_home,
            project_slug=validated["project"],
        ),
        identity_resolver=lambda _root, *, remote: identity,
    )
    return service, workspace_id, source_root


def _host_sync_request_id(args, suffix: str | None = None) -> str:
    explicit = getattr(args, "request_id", None)
    base = explicit.strip() if isinstance(explicit, str) and explicit.strip() else (
        f"host-sync-{int(time.time() * 1000)}"
    )
    if suffix:
        base = f"{base}-{suffix}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", base):
        raise hosting.HostingError(
            "--request-id must use letters, numbers, dots, underscores, colons, or hyphens"
        )
    return base


def _host_sync_emit(result: dict, args, *, watch: bool = False) -> dict:
    try:
        result = validate_sync_envelope(result)
    except (KeyError, TypeError, ValueError):
        result = _host_sync_boundary_failure()
    if getattr(args, "json", False):
        payload = dict(result)
        if watch:
            payload["watch"] = True
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        return result
    generation = result.get("generation") or {}
    generation_id = generation.get("id") if isinstance(generation, dict) else None
    if result.get("ok"):
        suffix = f" generation={generation_id}" if generation_id else ""
        print(f"host sync {result.get('status', 'complete')}{suffix}", flush=True)
    else:
        print(
            f"host sync failed: {result.get('message', 'synchronization failed')} "
            f"({result.get('code', 'sync_failed')})", flush=True,
        )
    return result


def _host_sync_boundary_failure() -> dict:
    """Return a fixed envelope; never accept exception diagnostics as input."""
    return failure_envelope(
        code="remote_unavailable", status="failed",
        relationship_id="host_sync_boundary", remote_name="redacted-remote",
        request_id="host_sync_request", retryable=False,
    )


def _cmd_host_sync(validated: dict, entry: dict, remote_name: str, args) -> None:
    service = None
    source_root = None
    workspace_id = None
    started = False
    failure = None
    failure_emitted = False
    try:
        if not entry.get("provisioned"):
            raise RuntimeError("remote is not provisioned")
        service, workspace_id, source_root = _host_sync_service(validated, remote_name, entry)
        common = {
            "project_dir": source_root, "remote": remote_name,
            "workspace_id": workspace_id,
            "explicit_includes": tuple(getattr(args, "include", None) or ()),
        }
        request_id = _host_sync_request_id(args)
        if not getattr(args, "watch", False):
            result = service.once(**common, request_id=request_id)
            result = _host_sync_emit(result, args)
            if not result.get("ok"):
                raise SystemExit(1)
            return

        seconds = getattr(args, "watch_seconds", 3600)
        interval = getattr(args, "interval", 0.25)
        debounce = getattr(args, "debounce", 0.5)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 86400:
            raise hosting.HostingError("--watch-seconds must be between 1 and 86400")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.1 <= interval <= 10:
            raise hosting.HostingError("--interval must be between 0.1 and 10 seconds")
        if isinstance(debounce, bool) or not isinstance(debounce, (int, float)) or not 0.1 <= debounce <= 10:
            raise hosting.HostingError("--debounce must be between 0.1 and 10 seconds")

        watch_started_at = time.monotonic()
        service.start(source_root, remote=remote_name, workspace_id=workspace_id, mode="live")
        started = True
        last_signature = None
        attempted_signature = None
        quiet_since = watch_started_at - float(debounce)
        sequence = 0
        while time.monotonic() - watch_started_at < seconds:
            now = time.monotonic()
            signature = _host_sync_watch_signature(source_root)
            if signature != last_signature:
                last_signature = signature
                quiet_since = now
                attempted_signature = None
            if now - quiet_since >= float(debounce) and attempted_signature != signature:
                result = service.once(
                    **common, request_id=_host_sync_request_id(args, f"{sequence:08d}")
                )
                result = _host_sync_emit(result, args, watch=True)
                if not result.get("ok"):
                    failure = result
                    failure_emitted = True
                    break
                attempted_signature = signature
            time.sleep(float(interval))
            sequence += 1
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception:
        failure = _host_sync_boundary_failure()
    finally:
        stopped = None
        if started and service is not None and source_root is not None and workspace_id is not None:
            try:
                stopped = service.stop(
                    source_root, remote=remote_name, workspace_id=workspace_id,
                )
            except Exception:
                if failure is None:
                    failure = _host_sync_boundary_failure()
        if failure is not None and not failure_emitted:
            _host_sync_emit(failure, args, watch=bool(getattr(args, "watch", False)))
        elif failure is None and stopped is not None:
            _host_sync_emit(stopped, args, watch=True)
    if failure is not None:
        raise SystemExit(1)


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data))
        return
    print(f"{data['project']} / {data['environment']}")
    for route in data.get("routes", []):
        suffix = f" -> {route['target']}" if route.get("target") else ""
        print(f"  {route['mode']}: {route['hostname']}{suffix}")


def _validate_all_environments(project_dir: str | Path) -> list[dict]:
    """Validate every declared environment without contacting a remote.

    Each result keeps its own success/error state so callers can fix one
    environment without losing the evidence for the others.  Validation is
    intentionally still performed one environment at a time through the
    canonical manifest validator.
    """
    results: list[dict] = []
    for name in hosting.environment_names(project_dir):
        try:
            results.append({"ok": True, **hosting.validate_manifest(project_dir, name)})
        except hosting.HostingError as exc:
            results.append({"ok": False, "environment": name, "error": str(exc)})
    return results


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
_CADDY_LOCK_WAIT_SECONDS = 30
_CADDY_PHASE_TIMEOUT_SECONDS = 30
_CADDY_TRANSACTION_TIMEOUT_SECONDS = 150
_CADDY_LOCK_PATH = "/run/lock/sandbox-hosting-caddy.lock"


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


def _caddy_transaction_command(path: str, temporary: str | None, digest: str,
                               *, operation: str, remove: bool) -> str:
    """Build one locked Caddy transaction with bounded observable phases.

    One remote shell owns the host-global flock for the entire fragment
    install, aggregate validation, reload, and service observation sequence.
    Keeping the phases in one session prevents another host apply from
    interleaving between validation and reload while each external command is
    still independently bounded by ``timeout``.
    """
    script = f"""set -eu
path=$1
temporary=$2
desired_digest=$3
operation=$4
remove_fragment=$5
phase() {{
  printf '[Sandbox] caddy phase=%s state=%s digest=%s\\n' "$1" "$2" "$desired_digest"
}}
finish() {{
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$operation" = rollback ]; then
    phase rollback rollback_incomplete
  fi
  trap - EXIT
  exit "$rc"
}}
trap finish EXIT
install -d -m 0755 /etc/caddy/conf.d
import_changed=0
if [ ! -f /etc/caddy/Caddyfile ]; then
  printf '%s\\n' 'import /etc/caddy/conf.d/*.caddy' > /etc/caddy/Caddyfile
  import_changed=1
elif ! grep -q 'import /etc/caddy/conf.d/\\*.caddy' /etc/caddy/Caddyfile; then
  printf '\\n%s\\n' 'import /etc/caddy/conf.d/*.caddy' >> /etc/caddy/Caddyfile
  import_changed=1
fi
current_digest=absent
if [ -f "$path" ]; then
  current_digest=$(sha256sum "$path" | awk '{{print $1}}')
fi
if [ "$remove_fragment" = 1 ]; then
  desired_state=absent
else
  desired_state=$desired_digest
fi
if [ "$current_digest" = "$desired_state" ]; then
  phase digest unchanged
else
  phase digest changed
fi
if [ "$current_digest" = "$desired_state" ] && [ "$import_changed" = 0 ]; then
  if [ "$temporary" != - ]; then rm -f "$temporary"; fi
  if [ "$operation" = apply ]; then
    phase noop unchanged
  fi
  if ! timeout {_CADDY_PHASE_TIMEOUT_SECONDS} systemctl is-active --quiet caddy; then
    phase observe failed
    exit 72
  fi
  phase observe active
  if [ "$operation" = rollback ]; then
    phase rollback rollback_complete
  fi
  exit 0
fi
if [ "$remove_fragment" = 1 ]; then
  rm -f "$path"
else
  install -m 0644 "$temporary" "$path"
  rm -f "$temporary"
fi
phase install passed
if ! timeout {_CADDY_PHASE_TIMEOUT_SECONDS} caddy validate --config /etc/caddy/Caddyfile; then
  phase validate failed
  exit 70
fi
phase validate passed
if ! timeout {_CADDY_PHASE_TIMEOUT_SECONDS} systemctl reload caddy; then
  phase reload failed
  exit 71
fi
phase reload passed
if ! timeout {_CADDY_PHASE_TIMEOUT_SECONDS} systemctl is-active --quiet caddy; then
  phase observe failed
  exit 72
fi
phase observe active
if [ "$operation" = apply ]; then
  phase complete changed
else
  phase rollback rollback_complete
fi
"""
    temporary_value = temporary or "-"
    return (
        "set -eu; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        "$SUDO install -d -m 0755 /run/lock; "
        f"$SUDO flock -w {_CADDY_LOCK_WAIT_SECONDS} {shlex.quote(_CADDY_LOCK_PATH)} "
        f"sh -c {shlex.quote(script)} sandbox-caddy "
        f"{shlex.quote(path)} {shlex.quote(temporary_value)} {shlex.quote(digest)} "
        f"{shlex.quote(operation)} {'1' if remove else '0'}"
    )


def _configure_host_caddy(entry: dict, name: str, content: str,
                          previous: str | None = None, *,
                          log_path: str | None = None) -> dict:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    digest = hashlib.sha256(content.encode()).hexdigest()
    temporary = f"/tmp/{name}.{digest}.caddy"
    _write_remote_text(entry, temporary, content, "0644")
    output = _remote_checked(
        entry,
        _caddy_transaction_command(
            path, temporary, digest, operation="apply", remove=False,
        ),
        timeout=_CADDY_TRANSACTION_TIMEOUT_SECONDS,
        log_path=log_path,
    )
    state = "unchanged" if "phase=noop state=unchanged" in output else "changed"
    return {"state": state, "digest": digest}


def _restore_host_caddy(entry: dict, name: str, previous: str | None, *,
                        log_path: str | None = None) -> dict:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    digest = "absent"
    temporary = None
    if previous is not None:
        digest = hashlib.sha256(previous.encode()).hexdigest()
        temporary = f"/tmp/{name}.rollback.{digest}.caddy"
        _write_remote_text(entry, temporary, previous, "0644")
    try:
        _remote_checked(
            entry,
            _caddy_transaction_command(
                path, temporary, digest, operation="rollback",
                remove=previous is None,
            ),
            timeout=_CADDY_TRANSACTION_TIMEOUT_SECONDS,
            log_path=log_path,
        )
    except Exception as exc:
        raise hosting.HostingError(f"rollback_incomplete: Caddy restore failed: {exc}") from exc
    return {"state": "rollback_complete", "digest": digest}


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
                 runtime: dict, progress=None, apply_log: str | None = None,
                 *, force_recreate: bool = True,
                 runtime_convergence_proof: dict | None = None) -> None:
    proof = runtime_convergence_proof or {}
    revisions = [proof.get(key) for key in (
        "requested_revision", "recorded_revision", "observed_runtime_revision",
    )]
    targeted_proven = (
        len(set(revisions)) == 1
        and isinstance(revisions[0], str)
        and re.fullmatch(r"[0-9a-f]{40}", revisions[0]) is not None
        and isinstance(proof.get("requested_config_digest"), str)
        and proof.get("requested_config_digest") == proof.get("recorded_config_digest")
        and proof.get("topology_state") == "ready"
        and proof.get("health_state") == "ready"
        and proof.get("source_revision_state") == "ready"
    )
    if not force_recreate and not targeted_proven:
        raise RuntimeError(
            "targeted Compose convergence requires exact source, config, and "
            "topology proof with ready health"
        )
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
            f"Compose {'build/recreate' if force_recreate else 'targeted convergence'} started "
            f"(timeout {build_timeout}s; "
            f"build={'enabled' if build else 'disabled'})"
        )
    # Replace the image's anonymous application volume on each deployment so
    # code/config changes are not shadowed by a previous container. Persistent
    # data must be declared as named volumes (for WordPress: database/uploads).
    converge_flags = (
        f"{build_flag} --force-recreate --renew-anon-volumes"
        if force_recreate else ""
    )
    command = f"{prefix} up -d{converge_flags} --remove-orphans {service_args}"
    if force_recreate:
        _build_checked(
            entry, prefix, command, service_args, timeout=build_timeout,
            progress=progress, log_path=apply_log,
        )
    else:
        _remote_checked(
            entry, command, timeout=build_timeout,
            progress=progress, log_path=apply_log,
        )
    if progress is not None:
        progress(f"Compose {'build/recreate' if force_recreate else 'targeted convergence'} completed")
    for init_service in (
            validated["compose"].get("init_services", []) if force_recreate else []):
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


def _verify_remote_derived_environment(entry: dict, validated: dict,
                                       source_dir: str, runtime_dir: str,
                                       expected_sha: str, progress=None,
                                       log_path: str | None = None,
                                       *, observation: dict | None = None) -> dict:
    """Fail closed when a running service did not receive the pushed revision."""
    derived = validated["deploy"].get("derived_environment", {})
    if not derived:
        return {"state": "not_declared", "checks": []}
    observation = observation or _observe_host_runtime(
        validated, entry, source_dir, runtime_dir,
    )
    classified = _classify_host_observation(validated, observation, expected_sha)
    source = classified["source_revision"]
    if source["state"] != "ready":
        failed = next((item for item in source["checks"] if item["state"] != "match"), {})
        raise RuntimeError(
            f"deployed service {failed.get('service', 'unknown')} source revision check failed "
            f"for {failed.get('key', 'unknown')} (state={failed.get('state', 'unavailable')})"
        )
    return classified


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
        entry, f"{prefix} --profile {shlex.quote('*')} config --services", timeout=60,
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


def _host_observation_command(prefix: str, services: list[str],
                              revision_keys: list[str], deadline_seconds: int) -> str:
    """Build one remote observer with one monotonic total deadline."""
    if len(services) > _HOST_OBSERVATION_MAX_SERVICES:
        raise ValueError("host observation exceeds the declared service limit")
    if len(revision_keys) > _HOST_OBSERVATION_MAX_KEYS:
        raise ValueError("host observation exceeds the revision-key limit")
    revision_commands = []
    for service in services:
        script = "; ".join(
            f"printf '%s=%s\\n' {shlex.quote(key)} \"${{{key}-}}\""
            for key in revision_keys
        )
        revision_commands.append({
            "service": service,
            "command": (
                f"{prefix} exec -T {shlex.quote(service)} sh -c {shlex.quote(script)}"
            ),
        })
    payload = base64.b64encode(json.dumps({
        "prefix": prefix, "services": services,
        "revision_keys": revision_keys, "deadline_seconds": deadline_seconds,
        "revision_commands": revision_commands,
    }, separators=(",", ":")).encode()).decode()
    program = "\n".join((
        "import base64,json,os,selectors,signal,subprocess,sys,time",
        f"MAX_OUTPUT_BYTES={_HOST_OBSERVATION_MAX_OUTPUT_BYTES}",
        f"MAX_ROWS={_HOST_OBSERVATION_MAX_ROWS}",
        f"MAX_CONFIGURED={_HOST_OBSERVATION_MAX_CONFIGURED_SERVICES}",
        f"MAX_PHASES={_HOST_OBSERVATION_MAX_PHASES}",
        "p=json.loads(base64.b64decode(sys.argv[1]));end=time.monotonic()+p['deadline_seconds']",
        "r={'schema_version':1,'complete':False,'configured_services':[],'rows':[],'revision_checks':[],'phases':[],'bounded':True}",
        "expired=False",
        "def stop(q):",
        " try:os.killpg(q.pid,signal.SIGKILL)",
        " except ProcessLookupError:pass",
        "def run(phase,command):",
        " global expired",
        " remaining=end-time.monotonic()",
        " if remaining<=0:",
        "  expired=True",
        "  if len(r['phases'])<MAX_PHASES:r['phases'].append({'phase':phase,'state':'deadline'})",
        "  return None",
        " q=subprocess.Popen(command,shell=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)",
        " selector=selectors.DefaultSelector();selector.register(q.stdout,selectors.EVENT_READ)",
        " raw=bytearray();truncated=False;timed_out=False",
        " while True:",
        "  remaining=end-time.monotonic()",
        "  if remaining<=0:",
        "   timed_out=True;expired=True;stop(q);break",
        "  ready=selector.select(remaining)",
        "  if not ready:",
        "   timed_out=True;expired=True;stop(q);break",
        "  chunk=os.read(q.stdout.fileno(),65536)",
        "  if not chunk:break",
        "  space=max(0,MAX_OUTPUT_BYTES-len(raw));raw.extend(chunk[:space])",
        "  if len(chunk)>space:truncated=True",
        " selector.close()",
        " if not timed_out:",
        "  remaining=end-time.monotonic()",
        "  if remaining<=0:timed_out=True;expired=True;stop(q)",
        "  else:",
        "   try:q.wait(timeout=remaining)",
        "   except subprocess.TimeoutExpired:timed_out=True;expired=True;stop(q)",
        " q.wait()",
        " if timed_out:",
        "  if len(r['phases'])<MAX_PHASES:r['phases'].append({'phase':phase,'state':'timeout'})",
        "  return None",
        " output=bytes(raw).decode('utf-8','replace');encoded=output.encode('utf-8')",
        " if len(encoded)>MAX_OUTPUT_BYTES:truncated=True",
        " output=encoded[:MAX_OUTPUT_BYTES].decode('utf-8','ignore')",
        " state='partial' if truncated else ('unavailable' if q.returncode else 'complete')",
        " if len(r['phases'])<MAX_PHASES:r['phases'].append({'phase':phase,'state':state,'bytes':len(output.encode('utf-8')),'truncated':truncated})",
        " return output",
        "configured=run('compose_config',p['prefix']+\" --profile '*' config --services\")",
        "if configured is not None:r['configured_services']=[x.strip() for x in configured.splitlines() if x.strip()][:MAX_CONFIGURED]",
        "rows=None if expired else run('compose_runtime',p['prefix']+' ps --format json')",
        "if rows is not None:",
        " try:parsed=json.loads(rows)",
        " except Exception:parsed=None",
        " if isinstance(parsed,list):r['rows'].extend([x for x in parsed if isinstance(x,dict)][:MAX_ROWS])",
        " elif isinstance(parsed,dict):r['rows'].append(parsed)",
        " else:",
        "  for line in rows.splitlines():",
        "   if len(r['rows'])>=MAX_ROWS:break",
        "   try:item=json.loads(line)",
        "   except Exception:continue",
        "   if isinstance(item,dict):r['rows'].append(item)",
        "for probe in p['revision_commands']:",
        " if expired or not p['revision_keys']:break",
        " service=probe['service'];output=run('source_revision:'+service,probe['command'])",
        " values={}",
        " if output is not None:",
        "  for line in output.splitlines():",
        "   key,sep,value=line.partition('=')",
        "   if sep and key in p['revision_keys']:values[key]=value",
        " for key in p['revision_keys']:r['revision_checks'].append({'service':service,'key':key,'observed':values.get(key)})",
        "r['complete']=bool(r['phases']) and not expired and all(x['state']=='complete' for x in r['phases'])",
        "print(json.dumps(r,separators=(',',':')))",
    ))
    return shlex.join(["python3", "-c", program, payload])


def _observe_host_runtime(validated: dict, entry: dict, source_dir: str,
                          runtime_dir: str, *, deadline_seconds: int = 60) -> dict:
    if not isinstance(deadline_seconds, int) or isinstance(deadline_seconds, bool) \
            or not 1 <= deadline_seconds <= 300:
        raise ValueError("host observation deadline must be between 1 and 300 seconds")
    services = [validated["compose"]["service"],
                *validated["compose"].get("background_services", [])]
    revision_keys = sorted(
        key for key, provider in validated["deploy"].get("derived_environment", {}).items()
        if provider == "pushed_commit_sha"
    )
    prefix = _compose_prefix(validated, source_dir,
                             f"{runtime_dir}/compose.override.yml",
                             f"{runtime_dir}/environment.env")
    raw = _remote_checked(
        entry, _host_observation_command(prefix, services, revision_keys, deadline_seconds),
        timeout=deadline_seconds + 5,
    )
    if len((raw or "").encode("utf-8", "replace")) > _HOST_OBSERVATION_MAX_RECEIPT_BYTES:
        return {"schema_version": 1, "complete": False, "bounded": True,
                "configured_services": [], "rows": [], "revision_checks": [],
                "phases": [{"phase": "observation", "state": "receipt_too_large"}]}
    try:
        receipt = json.loads((raw or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        receipt = {"schema_version": 1, "complete": False,
                   "configured_services": [], "rows": [], "revision_checks": [],
                   "phases": [{"phase": "observation", "state": "unavailable"}]}
    if not isinstance(receipt, dict):
        raise RuntimeError("remote host observation returned an invalid receipt")
    for key, default in (("complete", False), ("configured_services", []),
                         ("rows", []), ("revision_checks", []), ("phases", [])):
        receipt.setdefault(key, default)
    def bounded_list(value, limit):
        return list(value)[:limit] if isinstance(value, list) else []
    receipt["configured_services"] = bounded_list(
        receipt["configured_services"], _HOST_OBSERVATION_MAX_CONFIGURED_SERVICES,
    )
    receipt["rows"] = bounded_list(receipt["rows"], _HOST_OBSERVATION_MAX_ROWS)
    receipt["revision_checks"] = bounded_list(
        receipt["revision_checks"],
        _HOST_OBSERVATION_MAX_SERVICES * _HOST_OBSERVATION_MAX_KEYS,
    )
    receipt["phases"] = bounded_list(receipt["phases"], _HOST_OBSERVATION_MAX_PHASES)
    receipt["bounded"] = True
    return receipt


def _classify_host_observation(validated: dict, observation: dict,
                               expected_revision: str | None = None) -> dict:
    services = [validated["compose"]["service"],
                *validated["compose"].get("background_services", [])]
    configured_values = observation.get("configured_services", [])
    configured_values = configured_values if isinstance(configured_values, list) else []
    row_values = observation.get("rows", [])
    row_values = row_values if isinstance(row_values, list) else []
    check_values = observation.get("revision_checks", [])
    check_values = check_values if isinstance(check_values, list) else []
    phase_values = observation.get("phases", [])
    phase_values = phase_values if isinstance(phase_values, list) else []
    configured_all = {str(item) for item in configured_values if item}
    configured = [service for service in services if service in configured_all]
    rows = [item for item in row_values[:_HOST_OBSERVATION_MAX_ROWS]
            if isinstance(item, dict)]
    observed = {str(item.get("Service")): item for item in rows if item.get("Service")}
    running = [service for service in services
               if (observed.get(service) or {}).get("State") == "running"]
    missing_from_compose = sorted(set(services).difference(configured))
    missing_from_runtime = sorted(set(services).difference(running))
    topology = {"state": "degraded" if missing_from_compose or missing_from_runtime else "ready",
                "declared_services": services, "compose_services": configured,
                "running_services": running, "missing_from_compose": missing_from_compose,
                "missing_from_runtime": missing_from_runtime}
    service_rows = [{"service": service,
                     "state": (observed.get(service) or {}).get("State") or "unknown",
                     "health": (observed.get(service) or {}).get("Health") or "unknown"}
                    for service in services]
    if not observation.get("complete"):
        health = {"state": "unavailable", "reason": "remote observation was partial"}
    elif not configured or not rows:
        health = {"state": "unknown", "reason": "Compose returned incomplete service evidence"}
    elif topology["state"] != "ready":
        health = {"state": "degraded", "reason": "declared service topology differs from Compose/runtime"}
    elif any(item["state"] != "running" for item in service_rows):
        health = {"state": "degraded", "reason": "one or more services are not running"}
    elif any(item["health"] == "unknown" for item in service_rows):
        health = {"state": "unverified", "reason": "one or more running services have unknown health"}
    elif any(item["health"] != "healthy" for item in service_rows):
        health = {"state": "degraded", "reason": "one or more services are not healthy"}
    else:
        health = {"state": "ready"}
    checks = []
    for raw in check_values[
            :_HOST_OBSERVATION_MAX_SERVICES * _HOST_OBSERVATION_MAX_KEYS]:
        if not isinstance(raw, dict):
            continue
        observed_revision = raw.get("observed")
        item = {"service": raw.get("service"), "key": raw.get("key"),
                "provider": "pushed_commit_sha",
                "expected": expected_revision}
        item["state"] = ("match" if expected_revision and observed_revision == expected_revision
                         else "missing" if not observed_revision else "mismatch")
        checks.append(item)
    expected_pairs = {
        (service, key)
        for service in services
        for key, provider in validated["deploy"].get("derived_environment", {}).items()
        if provider == "pushed_commit_sha"
    }
    actual_pairs = {(item["service"], item["key"]) for item in checks}
    source_ready = (bool(expected_pairs) and actual_pairs == expected_pairs
                    and len(checks) == len(expected_pairs) and all(
        item["state"] == "match" for item in checks
    ))
    return {"complete": bool(observation.get("complete")), "services": service_rows,
            "topology": topology, "health": health,
            "source_revision": {"state": "not_declared" if not expected_pairs else
                                "ready" if source_ready
                                else "degraded", "checks": checks},
            "observed_runtime_revision": expected_revision if source_ready else None,
            "phases": phase_values[:_HOST_OBSERVATION_MAX_PHASES]}


def _host_config_digest(validated: dict, runtime: dict) -> str:
    payload = {
        "compose": validated["compose"],
        "compose_override": runtime.get("compose_override"),
        "environment": runtime.get("environment"),
        "services": [validated["compose"]["service"],
                     *validated["compose"].get("background_services", [])],
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_revision_evidence_ready(validated: dict, classified: dict,
                                    revision: str) -> bool:
    expected = {
        (service, key)
        for service in [validated["compose"]["service"],
                        *validated["compose"].get("background_services", [])]
        for key, provider in validated["deploy"].get("derived_environment", {}).items()
        if provider == "pushed_commit_sha"
    }
    source = classified.get("source_revision") or {}
    checks = source.get("checks") if isinstance(source.get("checks"), list) else []
    actual = {(item.get("service"), item.get("key")) for item in checks
              if isinstance(item, dict)}
    return (
        bool(expected)
        and source.get("state") == "ready"
        and actual == expected
        and len(checks) == len(expected)
        and all(
            item.get("state") == "match"
            and item.get("expected") == revision
            for item in checks if isinstance(item, dict)
        )
    )


def _runtime_apply_decision(*, previous: dict, requested_revision: str,
                            config_digest: str, source_state_identity: str,
                            source_state_clean: bool,
                            exact_runtime_proven: bool) -> str:
    """Choose only full convergence, proven edge replay, or refusal."""
    recorded = previous.get("recorded_revision") or previous.get("commit")
    staged = previous.get("staged_revision")
    observed = previous.get("observed_runtime_revision")
    runtime_state = (previous.get("runtime") or {}).get("state")
    same_source_state = previous.get("source_state_identity") == source_state_identity
    recorded_identity = recorded == requested_revision and observed == requested_revision
    staged_identity = staged == requested_revision and runtime_state in {
        "pending", "unverified",
    }
    same_config = previous.get("config_digest") == config_digest
    if source_state_clean and same_source_state and same_config and exact_runtime_proven \
            and (recorded_identity or staged_identity):
        return "edge_only"
    if same_source_state and same_config and (recorded_identity or staged_identity):
        return "refuse"
    if same_config and staged_identity and previous.get("source_state_identity") is None:
        return "refuse"
    if same_source_state and same_config and recorded == requested_revision:
        return "refuse"
    return "full_recreate"


def _legacy_source_replay_must_refuse(previous: dict, requested_revision: str,
                                      config_digest: str) -> bool:
    recorded = previous.get("recorded_revision") or previous.get("commit")
    staged = previous.get("staged_revision")
    unresolved = ((previous.get("edge") or {}).get("state") == "pending"
                  or (previous.get("runtime") or {}).get("state") in {
                      "pending", "unverified",
                  })
    return (
        previous.get("source_state_clean") is not True
        and previous.get("config_digest") == config_digest
        and requested_revision in {recorded, staged}
        and unresolved
    )


def _safe_source_revision_receipt(source: dict | None) -> dict:
    source = source if isinstance(source, dict) else {}
    state = source.get("state")
    if state not in {"ready", "degraded", "not_declared", "unavailable"}:
        state = "unavailable"
    checks = []
    for raw in source.get("checks", []) if isinstance(source.get("checks"), list) else []:
        if not isinstance(raw, dict):
            continue
        item = {
            "service": str(raw.get("service") or "")[:128],
            "key": str(raw.get("key") or "")[:128],
            "provider": "pushed_commit_sha",
            "state": raw.get("state") if raw.get("state") in {
                "match", "missing", "mismatch",
            } else "mismatch",
        }
        expected = raw.get("expected")
        if isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{40}", expected):
            item["expected"] = expected
        checks.append(item)
        if len(checks) >= _HOST_OBSERVATION_MAX_SERVICES * _HOST_OBSERVATION_MAX_KEYS:
            break
    return {"state": state, "checks": checks}


def _persist_runtime_observation(state: dict, key: str, classified: dict,
                                 *, runtime_state: str) -> None:
    record = state["hosts"][key]
    record["observed_runtime_revision"] = classified.get("observed_runtime_revision")
    record["runtime"] = {
        "state": runtime_state,
        "services": list(classified.get("services") or [])[:_HOST_OBSERVATION_MAX_SERVICES],
        "health": classified.get("health") or {"state": "unavailable"},
        "topology": classified.get("topology") or {"state": "unavailable"},
        "source_revision": _safe_source_revision_receipt(
            classified.get("source_revision"),
        ),
        "observation": list(classified.get("phases") or [])[:_HOST_OBSERVATION_MAX_PHASES],
    }
    record["edge"] = {"state": "pending"}
    hosting.save_host_state(state)


def _reconcile_exact_runtime_state(validated: dict, remote_name: str, state: dict,
                                   revision: str, config_digest: str, *,
                                   entry: dict | None = None,
                                   source_dir: str = "/unresolved/source",
                                   runtime_dir: str = "/unresolved/runtime",
                                   observation: dict | None = None) -> bool:
    """Repair only the local receipt when exact runtime evidence is complete."""
    key = hosting.state_key(remote_name, validated)
    record = state.setdefault("hosts", {}).get(key)
    if not isinstance(record, dict) or record.get("config_digest") != config_digest \
            or record.get("source_state_clean") is not True:
        return False
    if observation is None:
        observation = _observe_host_runtime(
            validated, entry or {}, source_dir, runtime_dir,
        )
    if "topology" in observation and "health" in observation:
        classified = observation
    else:
        classified = _classify_host_observation(validated, observation, revision)
    proven = (
        classified.get("complete") is True
        and (classified.get("topology") or {}).get("state") == "ready"
        and (classified.get("health") or {}).get("state") == "ready"
        and classified.get("observed_runtime_revision") == revision
        and _source_revision_evidence_ready(validated, classified, revision)
    )
    if not proven:
        return False
    record.update({
        "commit": revision,
        "recorded_revision": revision,
        "observed_runtime_revision": revision,
        "runtime": {
            "state": "ready",
            "services": list(classified.get("services") or [])[
                :_HOST_OBSERVATION_MAX_SERVICES
            ],
            "health": classified.get("health"),
            "topology": classified.get("topology"),
            "source_revision": _safe_source_revision_receipt(
                classified.get("source_revision"),
            ),
            "observation": list(classified.get("phases") or [])[
                :_HOST_OBSERVATION_MAX_PHASES
            ],
        },
    })
    record.setdefault("edge", {"state": "pending"})
    hosting.save_host_state(state)
    return True


def _host_runtime_status(validated: dict, entry: dict, remote_name: str,
                         state: dict) -> dict:
    """Read deployed revision and bounded Compose health without mutation."""
    key = hosting.state_key(remote_name, validated)
    recorded = dict((state.get("hosts") or {}).get(key) or {})
    recorded_runtime = dict(recorded.get("runtime") or {"state": "unknown"})
    if "source_revision" in recorded_runtime:
        recorded_runtime["source_revision"] = _safe_source_revision_receipt(
            recorded_runtime.get("source_revision"),
        )
    services = [
        validated["compose"]["service"],
        *validated["compose"].get("background_services", []),
    ]
    result = {
        "project": validated["project"],
        "environment": validated["environment"],
        "remote": remote_name,
        "deployed_revision": recorded.get("commit"),
        "requested_revision": recorded.get("requested_revision"),
        "staged_revision": recorded.get("staged_revision"),
        "recorded_revision": recorded.get("recorded_revision") or recorded.get("commit"),
        "observed_runtime_revision": recorded.get("observed_runtime_revision"),
        "runtime": recorded_runtime,
        "edge": recorded.get("edge") or {"state": "unknown"},
        "state_record": "present" if recorded else "missing",
        "services": [],
        "topology": {
            "state": "unavailable",
            "declared_services": services,
            "compose_services": [],
            "running_services": [],
            "missing_from_compose": services,
            "missing_from_runtime": services,
        },
        "health": {"state": "unavailable", "reason": "remote status not observed"},
    }
    if not entry.get("provisioned"):
        result["health"] = {"state": "unavailable", "reason": "remote is not provisioned"}
        return result
    try:
        home = remote.resolve_sandbox_home(entry)
        source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
        runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
        observation = _observe_host_runtime(validated, entry, source_dir, runtime_dir)
        classified = _classify_host_observation(
            validated, observation, recorded.get("commit") or recorded.get("staged_revision"),
        )
        result.update({key: classified[key] for key in (
            "services", "topology", "health", "source_revision",
            "observed_runtime_revision", "phases",
        )})
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        result["health"] = {"state": "unavailable", "reason": remote.redact_text(str(exc))[:500]}
    return result


def _host_runtime_diagnose(validated: dict, entry: dict, remote_name: str,
                           state: dict) -> dict:
    """Collect one read-only deployment explanation without exposing secrets."""
    result = _host_runtime_status(validated, entry, remote_name, state)
    result["disk"] = {"state": "unavailable", "free_mb": None}
    result["images"] = []
    result["image_state"] = {"state": "unavailable", "reason": "image metadata not observed"}
    result.setdefault("source_revision", {"state": "not_declared", "checks": []})
    result["apply_log"] = None
    if not entry.get("provisioned"):
        result["disk"]["reason"] = "remote is not provisioned"
        result["image_state"] = {"state": "unavailable", "reason": "remote is not provisioned"}
        return result

    try:
        home = remote.resolve_sandbox_home(entry)
        runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
        result["apply_log"] = f"{runtime_dir}/apply.log"
        try:
            result["disk"] = {"state": "ready", "free_mb": _remote_disk_free_mb(entry, home)}
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            result["disk"] = {
                "state": "unavailable",
                "free_mb": None,
                "reason": remote.redact_text(str(exc))[:500],
            }

        source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
        prefix = _compose_prefix(
            validated, source_dir,
            f"{runtime_dir}/compose.override.yml",
            f"{runtime_dir}/environment.env",
        )
        try:
            raw_images = _remote_checked(entry, f"{prefix} images --format json", timeout=60)
            for line in (raw_images or "").splitlines():
                try:
                    item = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(item, dict):
                    result["images"].append({
                        key: item.get(key)
                        for key in ("Service", "Name", "Image", "ID", "Created", "Size")
                        if item.get(key) is not None
                    })
            result["image_state"] = {"state": "ready" if result["images"] else "unknown"}
            if not result["images"]:
                result["image_state"]["reason"] = "Compose returned no image rows"
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            result["image_state"] = {
                "state": "unavailable",
                "reason": remote.redact_text(str(exc))[:500],
            }

        # Source-revision and service-health evidence already came from the
        # single bounded status observer.  Diagnose adds disk and image facts;
        # it must not multiply SSH calls by service and environment key.
    except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        result["health"] = {
            "state": "unavailable",
            "reason": remote.redact_text(str(exc))[:500],
        }
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


def _verify_remote_health(entry: dict, runtime: dict, progress=None) -> dict:
    port = runtime["loopback_port"]
    path = runtime["healthcheck"]["path"]
    minimum, maximum = min(runtime["healthcheck"]["statuses"]), max(runtime["healthcheck"]["statuses"])
    payload = base64.b64encode(json.dumps({
        "url": f"http://127.0.0.1:{port}{path}",
        "minimum": minimum, "maximum": maximum, "deadline": 60,
    }, separators=(",", ":")).encode()).decode()
    program = "\n".join((
        "import base64,json,subprocess,sys,time",
        "p=json.loads(base64.b64decode(sys.argv[1]));end=time.monotonic()+p['deadline'];attempt=0",
        "r={'schema_version':1,'complete':False,'status':None,'phases':[]}",
        "while time.monotonic()<end:",
        " remaining=end-time.monotonic()",
        " if remaining<=0:break",
        " attempt+=1",
        " try:q=subprocess.run(['curl','-fsS','--max-time',str(min(15,remaining)),'-o','/dev/null','-w','%{http_code}',p['url']],text=True,capture_output=True,timeout=remaining,check=False)",
        " except subprocess.TimeoutExpired:r['phases'].append({'phase':'health','state':'timeout','attempt':attempt});break",
        " try:code=int((q.stdout or '').strip())",
        " except ValueError:code=None",
        " if q.returncode==0 and code is not None and p['minimum']<=code<=p['maximum']:",
        "  r['complete']=True;r['status']=code;r['phases'].append({'phase':'health','state':'ready','attempt':attempt});break",
        " r['phases'].append({'phase':'health','state':'pending','attempt':attempt})",
        " remaining=end-time.monotonic()",
        " if remaining<=0:break",
        " time.sleep(min(2,remaining))",
        "print(json.dumps(r,separators=(',',':')))",
    ))
    result = remote.ssh_run(
        entry, shlex.join(["python3", "-c", program, payload]), timeout=65,
    )
    try:
        receipt = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        receipt = {"schema_version": 1, "complete": False, "status": None,
                   "phases": [{"phase": "health", "state": "unavailable"}]}
    if receipt.get("complete") is not True:
        raise RuntimeError(
            f"remote healthcheck did not return {minimum}-{maximum} within 60 seconds"
        )
    if progress is not None:
        progress("remote healthcheck passed")
    return receipt


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
            env=remote.git_environment(), capture_output=True, text=True, check=False,
        )
        if status.returncode != 0:
            raise RuntimeError("could not inspect the deployment working tree")
        if status.stdout.strip():
            raise hosting.HostingError(
                f"{validated['environment']} requires a clean working tree"
            )
    return branch


def _resolve_host_source_commit(project_root: str) -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_root,
        env=remote.git_environment(), capture_output=True, text=True, check=False,
    )
    commit = (head.stdout or "").strip().lower()
    if head.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RuntimeError("could not resolve the hosting source commit")
    return commit


def _apply_host(validated: dict, entry: dict, remote_name: str, runtime: dict,
                state: dict, allow_zone_ssl_change: bool, branch: str,
                progress=None) -> dict:
    secret_values, missing = _secret_status(validated)
    if missing:
        raise hosting.HostingError("missing hosting secrets: " + ", ".join(missing))
    base_commit = _resolve_host_source_commit(validated["project_root"])
    diff, untracked = remote.capture_uncommitted(validated["project_root"])
    require_clean = validated["deploy"]["require_clean"]
    if require_clean and (diff or untracked):
        raise hosting.HostingError(
            f"{validated['environment']} working tree changed before source staging"
        )
    source_snapshot = remote.snapshot_dirty_overlay(
        validated["project_root"], diff, untracked,
        max_files=_HOST_SOURCE_SNAPSHOT_MAX_FILES,
        max_bytes=_HOST_SOURCE_SNAPSHOT_MAX_BYTES,
    )
    source_state_identity = source_snapshot["identity"]
    source_state_clean = not bool(diff or untracked)
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
        resolved_sha=base_commit,
        source_root=source_root if nested_source else None,
    )
    runtime["environment"] = hosting.render_env_file(
        validated, secret_values, pushed_commit_sha=sha,
    )
    key = runtime["key"]
    previous_entry = dict(state["hosts"].get(key) or {})
    legacy_clean = "sha256:" + hashlib.sha256(
        b"sandbox-dirty-overlay-v1\0"
    ).hexdigest()
    if (previous_entry.get("source_state_clean") is not True
            and previous_entry.get("source_state_identity") == legacy_clean
            and source_state_clean):
        # v1's empty-overlay digest is explicit historical clean proof.
        previous_entry["source_state_identity"] = source_state_identity
        previous_entry["source_state_clean"] = True
    config_digest = _host_config_digest(validated, runtime)
    if _legacy_source_replay_must_refuse(previous_entry, sha, config_digest):
        try:
            _release_host_apply_reservation(entry, reservation)
        except Exception as cleanup_error:
            raise hosting.HostingError(
                "legacy source receipt is not safe to replay and rollback-space "
                f"cleanup failed: {cleanup_error}"
            ) from cleanup_error
        raise RuntimeError(
            "legacy dirty or unknown source identity cannot be replayed at the same "
            "revision/config; refusing before target reset, Compose, or initializer mutation"
        )
    client = cloudflare.Client()
    remote.update_target_to(
        entry, target, sha,
        project_root=None if require_clean else validated["project_root"],
        diff_text=diff, untracked=untracked,
        overlay_snapshot=source_snapshot,
    )
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    apply_log = f"{runtime_dir}/apply.log"
    state["hosts"][key] = {
        **previous_entry,
        "requested_revision": sha,
        "staged_revision": sha,
        "source_state_identity": source_state_identity,
        "source_state_clean": source_state_clean,
        "config_digest": config_digest,
        "runtime": {"state": "pending"},
        "edge": {"state": "pending"},
    }
    hosting.save_host_state(state)
    stream_progress = None
    if progress is not None:
        def stream_progress(message: str) -> None:
            safe = str(message)
            for secret in secret_values.values():
                if secret:
                    safe = safe.replace(secret, "[REDACTED]")
            progress(safe)
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
            _restore_host_caddy(
                entry, caddy_name, previous_caddy, log_path=apply_log,
            )
        except Exception as exc:
            failures.append(f"Caddy restore: {exc}")
        if failures:
            raise hosting.HostingError("; ".join(failures))

    def apply() -> None:
        nonlocal reservation
        if stream_progress is not None:
            stream_progress(f"source reset to {sha}")
            stream_progress(f"apply log: {apply_log}")
        classified = None
        evidence_error = None
        recorded_before = previous_entry.get("recorded_revision") or previous_entry.get("commit")
        staged_before = previous_entry.get("staged_revision")
        previous_runtime_state = (previous_entry.get("runtime") or {}).get("state")
        previous_source_clean = previous_entry.get("source_state_clean") is True
        same_source_state = (
            previous_entry.get("source_state_identity") == source_state_identity
        )
        may_replay = (
            previous_entry.get("config_digest") == config_digest
            and same_source_state
            and previous_source_clean
            and (
                (recorded_before == sha
                 and previous_entry.get("observed_runtime_revision") == sha)
                or (staged_before == sha
                    and previous_runtime_state in {"pending", "unverified"}
                    and previous_source_clean)
            )
        )
        exact_runtime_proven = False
        if may_replay:
            try:
                observation = _observe_host_runtime(
                    validated, entry, target, runtime_dir,
                )
                classified = _classify_host_observation(validated, observation, sha)
                exact_runtime_proven = (
                    classified.get("complete") is True
                    and classified["topology"]["state"] == "ready"
                    and classified["health"]["state"] == "ready"
                    and classified.get("observed_runtime_revision") == sha
                    and _source_revision_evidence_ready(validated, classified, sha)
                )
            except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
                evidence_error = exc
        decision = _runtime_apply_decision(
            previous=previous_entry,
            requested_revision=sha,
            config_digest=config_digest,
            source_state_identity=source_state_identity,
            source_state_clean=source_state_clean,
            exact_runtime_proven=exact_runtime_proven,
        )
        if decision == "refuse":
            if classified is not None:
                _persist_runtime_observation(
                    state, key, classified, runtime_state="unverified",
                )
            detail = f": {evidence_error}" if evidence_error is not None else ""
            raise RuntimeError(
                "existing runtime identity/topology is not fully proven; refusing "
                f"Compose or initializer replay{detail}"
            )
        if decision == "edge_only":
            if not _reconcile_exact_runtime_state(
                    validated, remote_name, state, sha, config_digest,
                    observation=classified):
                raise RuntimeError("exact runtime reconciliation evidence was rejected")
        else:
            _run_compose(
                entry, validated, target, runtime_dir, runtime,
                stream_progress, apply_log,
                force_recreate=True,
            )
            health_receipt = _verify_remote_health(entry, runtime, stream_progress)
            try:
                observation = _observe_host_runtime(validated, entry, target, runtime_dir)
            except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
                observation = {
                    "schema_version": 1,
                    "complete": False,
                    "bounded": True,
                    "configured_services": [],
                    "rows": [],
                    "revision_checks": [],
                    "phases": [{"phase": "observation", "state": "unavailable"}],
                }
                classified = _classify_host_observation(validated, observation, sha)
                _persist_runtime_observation(
                    state, key, classified, runtime_state="unverified",
                )
                raise RuntimeError(f"runtime observation failed: {exc}") from exc
            classified = _classify_host_observation(validated, observation, sha)
            runtime_ready = (
                classified["topology"]["state"] == "ready"
                and classified["health"]["state"] == "ready"
            )
            source_required = bool(validated["deploy"].get("derived_environment"))
            source_ready = (
                not source_required
                or _source_revision_evidence_ready(validated, classified, sha)
            )
            if not runtime_ready or not source_ready:
                _persist_runtime_observation(
                    state, key, classified, runtime_state="unverified",
                )
                raise RuntimeError(
                    "remote runtime source/topology/health is not fully proven ready"
                )
            _persist_runtime_observation(state, key, classified, runtime_state="ready")
            record = state["hosts"][key]
            record.update({"commit": sha, "recorded_revision": sha})
            record["runtime"]["loopback_health"] = health_receipt
            hosting.save_host_state(state)
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
        _configure_host_caddy(
            entry, caddy_name, runtime["caddyfile"], previous_caddy,
            log_path=apply_log,
        )
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
        state["hosts"][key].update({
            "loopback_port": runtime["loopback_port"],
            "compose_project": runtime["compose_project"],
            "certificate": certificate,
            "records": changes,
            "commit": sha,
            "recorded_revision": sha,
            "caddy_name": caddy_name,
            "edge": {"state": "ready"},
        })
        hosting.save_host_state(state)

    hosting.apply_with_rollback(apply, rollback)
    result = {
        "commit": sha,
        "requested_revision": sha,
        "staged_revision": sha,
        "recorded_revision": state["hosts"][key].get("recorded_revision"),
        "observed_runtime_revision": state["hosts"][key].get("observed_runtime_revision"),
        "runtime": state["hosts"][key].get("runtime"),
        "edge": state["hosts"][key].get("edge"),
        "derived_environment": runtime.get("derived_environment", []),
    }
    if progress is not None:
        result["apply_log"] = apply_log
    return result


def cmd_host(cfg, args) -> None:
    if getattr(args, "all", False):
        if args.action != "validate":
            die("--all is only valid with `host validate`; no command was executed")
        if getattr(args, "environment", None):
            die("--all cannot be combined with --environment; no command was executed")
        try:
            results = _validate_all_environments(args.project_dir or ".")
        except hosting.HostingError as exc:
            die(str(exc))
        all_ok = all(result.get("ok") is True for result in results)
        payload = {"ok": all_ok, "action": "validate", "environments": results}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"validated {len(results)} hosting environments")
            for result in results:
                name = result.get("environment", "<unknown>")
                if result.get("ok"):
                    print(f"  {name}: ok")
                else:
                    print(f"  {name}: invalid ({result.get('error', 'validation failed')})")
        if not all_ok:
            raise SystemExit(1)
        return
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
        die("--remote is required for host plan, status, diagnose, apply, logs, sync, and login-url")
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
    if args.action == "sync":
        _cmd_host_sync(validated, entry, args.remote, args)
        return
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
    if args.action == "diagnose":
        result = _host_runtime_diagnose(validated, entry, args.remote, state)
        if args.json:
            print(json.dumps({"ok": True, **result}, sort_keys=True))
        else:
            print(f"{result['project']} / {result['environment']} ({result['remote']})")
            print(f"  deployed revision: {result['deployed_revision'] or 'unknown'}")
            print(f"  health: {result['health']['state']}")
            print(f"  disk: {result['disk']['free_mb']} MiB free ({result['disk']['state']})")
            print(f"  images: {len(result['images'])} ({result['image_state']['state']})")
            source = result["source_revision"]
            print(f"  source revision: {source['state']}")
            for check in source.get("checks", []):
                print(f"    {check['key']}: {check['state']}")
            for service in result["services"]:
                print(f"  {service['service']}: {service['state']} ({service['health']})")
            if result["health"].get("reason"):
                print(f"  reason: {result['health']['reason']}")
            if result["apply_log"]:
                print(f"  apply log: {result['apply_log']}")
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
