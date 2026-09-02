from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import secrets
import subprocess
import base64
import shlex
import stat
import time
import urllib.error
import urllib.request
import urllib.parse
from contextlib import contextmanager, nullcontext
from getpass import getpass
from pathlib import Path

from sandbox.core import die, info, ok
from sandbox.core._paths import RUNTIME_DIR
from sandbox.registry import CommandSpec, register_specs
from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService
from sandbox.sync.models import failure_envelope, validate_sync_envelope
from sandbox.transports.remote_sync import HostSourceSyncTransport
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare
import sandbox.core._secrets as personal_secrets
from sandbox.hosting.recovery.models import (
    MAX_RECEIPT_BYTES, RecoveryAction, RecoveryRequest, TargetIdentity,
    canonical_digest, validate_edge_intent,
)
from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.hosting.recovery.service import RecoveryAuthorityError, RecoveryService


_HOST_SYNC_WATCH_EXCLUDES = frozenset({
    ".git", ".sandbox", ".cache", ".pytest_cache", ".mypy_cache",
    "node_modules", "vendor", "build", "dist", "out", "coverage",
    "runtime", "cache", "caches", "logs", "tmp", "temp", "uploads",
    "storage", "__pycache__", ".venv", "venv",
})

# Hosted applications may declare a primary service plus a larger worker set.
# Keep the observer bounded, but allow the current production topology to be
# inspected instead of failing before any read-only evidence is collected.
_HOST_OBSERVATION_MAX_SERVICES = 32
_HOST_OBSERVATION_MAX_KEYS = 16
_HOST_OBSERVATION_MAX_ROWS = 64
_HOST_OBSERVATION_MAX_CONFIGURED_SERVICES = 64
_HOST_OBSERVATION_MAX_CONFIG_DIGESTS = 64
_HOST_OBSERVATION_MAX_PHASES = 6 + _HOST_OBSERVATION_MAX_SERVICES
_HOST_OBSERVATION_MAX_OUTPUT_BYTES = 64 * 1024
_HOST_OBSERVATION_MAX_RECEIPT_BYTES = 128 * 1024
_HOST_POST_COMPOSE_OBSERVATION_DEADLINE_SECONDS = 90.0
_HOST_POST_COMPOSE_OBSERVATION_ATTEMPT_SECONDS = 10
_HOST_POST_COMPOSE_OBSERVATION_INITIAL_BACKOFF_SECONDS = 1.0
_HOST_POST_COMPOSE_OBSERVATION_MAX_BACKOFF_SECONDS = 4.0
_HOST_NO_BUILD_CONFIG_MAX_BYTES = 1_048_576
_HOST_SOURCE_SNAPSHOT_MAX_FILES = 4096
_HOST_SOURCE_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
_SOURCE_STATE_IDENTITY_VERSION = 2


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


def _no_build_image_preflight_command(prefix: str, services: list[str]) -> str:
    """Render one bounded, read-only check for explicit local service images."""
    config_command = f"{prefix} --profile '*' config --format json"
    program = "\n".join((
        "import json,os,selectors,signal,subprocess,sys,time",
        f"MAX_OUTPUT={_HOST_NO_BUILD_CONFIG_MAX_BYTES};end=time.monotonic()+60",
        "def fail():",
        " print('no-build image preflight failed: every declared target/init service must resolve to an explicit existing image',file=sys.stderr)",
        " raise SystemExit(1)",
        "command=sys.argv[1];declared=sys.argv[2:]",
        "if not declared:fail()",
        "q=subprocess.Popen(command,shell=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)",
        "selector=selectors.DefaultSelector();selector.register(q.stdout,selectors.EVENT_READ)",
        "raw=bytearray();invalid=False",
        "while True:",
        " remaining=end-time.monotonic()",
        " if remaining<=0:invalid=True;break",
        " ready=selector.select(remaining)",
        " if not ready:invalid=True;break",
        " chunk=os.read(q.stdout.fileno(),65536)",
        " if not chunk:break",
        " raw.extend(chunk)",
        " if len(raw)>MAX_OUTPUT:invalid=True;break",
        "selector.close()",
        "if invalid:",
        " try:os.killpg(q.pid,signal.SIGKILL)",
        " except ProcessLookupError:pass",
        "q.wait()",
        "if invalid or q.returncode:fail()",
        "try:document=json.loads(bytes(raw).decode('utf-8'))",
        "except Exception:fail()",
        "configured=document.get('services') if isinstance(document,dict) else None",
        "if not isinstance(configured,dict):fail()",
        "images=[]",
        "for name in declared:",
        " service=configured.get(name)",
        " image=service.get('image') if isinstance(service,dict) else None",
        " if not isinstance(image,str) or not image.strip():fail()",
        " if service.get('pull_policy') == 'build':fail()",
        " images.append(image.strip())",
        "for image in images:",
        " remaining=end-time.monotonic()",
        " if remaining<=0:fail()",
        " try:result=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=min(10,remaining),check=False)",
        " except (OSError,subprocess.SubprocessError):fail()",
        " if result.returncode or not (result.stdout or '').strip():fail()",
        "print(json.dumps({'ok':True,'services':len(declared)},separators=(',',':')))",
    ))
    return shlex.join(["python3", "-c", program, config_command, *services])


def _preflight_no_build_images(entry: dict, prefix: str, services: list[str],
                               *, progress=None) -> None:
    """Refuse a no-build apply unless each declared image already exists."""
    _remote_checked(
        entry, _no_build_image_preflight_command(prefix, services), timeout=65,
    )
    if progress is not None:
        progress("No-build image preflight passed")


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
    build = validated["compose"].get("build", True)
    build_timeout = validated["compose"].get("build_timeout_seconds", 900)
    if not build:
        declared_services = [
            validated["compose"]["service"],
            *validated["compose"].get("background_services", []),
            *validated["compose"].get("init_services", []),
        ]
        _preflight_no_build_images(
            entry, prefix, declared_services, progress=progress,
        )
    build_flag = " --build" if force_recreate and build else " --no-build"
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
        if force_recreate else " --no-build" if not build else ""
    )
    command = f"{prefix} up -d{converge_flags} --remove-orphans {service_args}"
    if force_recreate and build:
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
            entry,
            f"{prefix} --profile jobs run --rm --pull never"
            f" {shlex.quote(init_service)}",
            timeout=900, progress=progress, log_path=apply_log,
        )
    _remote_checked(
        entry, f"{prefix} up -d{' --no-build' if not build else ''} {service_args}",
        timeout=300,
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
                              revision_keys: list[str], deadline_seconds: int,
                              *, config_paths: tuple[str, ...] = (),
                              source_dir: str = "") -> str:
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
        "config_paths": list(config_paths),
        "source_dir": source_dir,
    }, separators=(",", ":")).encode()).decode()
    program = "\n".join((
        "import base64,hashlib,json,os,selectors,signal,subprocess,sys,time",
        f"MAX_OUTPUT_BYTES={_HOST_OBSERVATION_MAX_OUTPUT_BYTES}",
        f"MAX_ROWS={_HOST_OBSERVATION_MAX_ROWS}",
        f"MAX_CONFIGURED={_HOST_OBSERVATION_MAX_CONFIGURED_SERVICES}",
        f"MAX_PHASES={_HOST_OBSERVATION_MAX_PHASES}",
        "p=json.loads(base64.b64decode(sys.argv[1]));end=time.monotonic()+p['deadline_seconds']",
        "r={'schema_version':1,'complete':False,'configured_services':[],'rows':[],'revision_checks':[],'phases':[],'images':[],'config_digests':[],'source_head':None,'source_branch':None,'source_clean':False,'epoch_start':None,'epoch_end':None,'bounded':True}",
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
        "quoted=' '.join(__import__('shlex').quote(x) for x in p['config_paths'])",
        "source=__import__('shlex').quote(p['source_dir'])",
        "git='env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C LC_ALL=C GIT_OPTIONAL_LOCKS=0 GIT_CONFIG_NOSYSTEM=1 git -c core.fsmonitor=false -c core.untrackedCache=false'",
        "epoch_command=\"(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true); docker info --format '{{.ID}}' 2>/dev/null; \"+git+\" -C \"+source+\" rev-parse HEAD; \"+git+\" -C \"+source+\" branch --show-current; \"+git+\" -C \"+source+\" status --porcelain; for f in \"+quoted+\"; do test -f \\\"$f\\\" && sha256sum -- \\\"$f\\\" || printf 'missing\\n'; done; \"+p['prefix']+\" ps --format json; \"+p['prefix']+\" --profile '*' images --format json\"",
        "marker=run('epoch_start',epoch_command)",
        "if marker is not None:r['epoch_start']='sha256:'+hashlib.sha256(marker.encode()).hexdigest()",
        "configured=None if expired else run('compose_config',p['prefix']+\" --profile '*' config --services\")",
        "if configured is not None:r['configured_services']=[x.strip() for x in configured.splitlines() if x.strip()][:MAX_CONFIGURED]",
        "git_state=None if expired else run('source_git',git+\" -C \"+source+\" rev-parse HEAD && \"+git+\" -C \"+source+\" branch --show-current && \"+git+\" -C \"+source+\" status --porcelain\")",
        "if git_state is not None:",
        " lines=git_state.splitlines();r['source_head']=lines[0] if lines else None;r['source_branch']=lines[1] if len(lines)>1 else None;r['source_clean']=len(lines)==2",
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
        "images=None if expired else run('compose_images',p['prefix']+' --profile \\'*\\' images --format json')",
        "if images is not None:",
        " for line in images.splitlines():",
        "  if len(r['images'])>=32:break",
        "  try:item=json.loads(line)",
        "  except Exception:continue",
        "  if isinstance(item,dict):",
        "   service=item.get('Service') or item.get('Name');image_id=item.get('ID')",
        "   if service and image_id:r['images'].append({'name':str(service)[:128],'id':str(image_id)[:160]})",
        "if p['config_paths'] and not expired:",
        " command='for f in '+quoted+\"; do test -f \\\"$f\\\" && sha256sum -- \\\"$f\\\" || printf 'missing  %s\\n' \\\"$f\\\"; done\"",
        " digests=run('config_digests',command)",
        " if digests is not None:",
        "  for index,line in enumerate(digests.splitlines()[:len(p['config_paths'])]):",
        "   value=line.split()[0] if line.split() else ''",
        "   if len(value)==64:r['config_digests'].append({'name':str(index),'digest':'sha256:'+value})",
        "for probe in p['revision_commands']:",
        " if expired or not p['revision_keys']:break",
        " service=probe['service'];output=run('source_revision:'+service,probe['command'])",
        " values={}",
        " if output is not None:",
        "  for line in output.splitlines():",
        "   key,sep,value=line.partition('=')",
        "   if sep and key in p['revision_keys']:values[key]=value",
        " for key in p['revision_keys']:r['revision_checks'].append({'service':service,'key':key,'observed':values.get(key)})",
        "marker=None if expired else run('epoch_end',epoch_command)",
        "if marker is not None:r['epoch_end']='sha256:'+hashlib.sha256(marker.encode()).hexdigest()",
        "r['complete']=bool(r['phases']) and not expired and all(x['state']=='complete' for x in r['phases'])",
        "print(json.dumps(r,separators=(',',':')))",
    ))
    return shlex.join(["python3", "-c", program, payload])


def _observe_host_runtime(validated: dict, entry: dict, source_dir: str,
                          runtime_dir: str, *, deadline_seconds: int = 60,
                          transport_grace_seconds: int = 5) -> dict:
    if not isinstance(deadline_seconds, int) or isinstance(deadline_seconds, bool) \
            or not 1 <= deadline_seconds <= 300:
        raise ValueError("host observation deadline must be between 1 and 300 seconds")
    if (not isinstance(transport_grace_seconds, int)
            or isinstance(transport_grace_seconds, bool)
            or not 0 <= transport_grace_seconds <= 5):
        raise ValueError("host observation transport grace must be between 0 and 5 seconds")
    services = [validated["compose"]["service"],
                *validated["compose"].get("background_services", [])]
    revision_keys = sorted(
        key for key, provider in validated["deploy"].get("derived_environment", {}).items()
        if provider == "pushed_commit_sha"
    )
    prefix = _compose_prefix(validated, source_dir,
                             f"{runtime_dir}/compose.override.yml",
                             f"{runtime_dir}/environment.env")
    compose_paths = tuple(
        f"{source_dir}/{name}" for name in validated["compose"]["files"])
    if len(compose_paths) + 3 > _HOST_OBSERVATION_MAX_CONFIG_DIGESTS:
        raise ValueError("host observation exceeds the Compose config digest limit")
    raw = _remote_checked(
        entry, _host_observation_command(
            prefix, services, revision_keys, deadline_seconds,
            config_paths=(*compose_paths,
                          f"{runtime_dir}/compose.override.yml",
                          f"{runtime_dir}/environment.env",
                          f"{runtime_dir}/recovery-phases.json"),
            source_dir=source_dir,
        ),
        timeout=deadline_seconds + transport_grace_seconds,
    )
    if len((raw or "").encode("utf-8", "replace")) > _HOST_OBSERVATION_MAX_RECEIPT_BYTES:
        return {"schema_version": 1, "complete": False, "bounded": True,
                "configured_services": [], "rows": [], "revision_checks": [],
                "images": [], "config_digests": [],
                "phases": [{"phase": "observation", "state": "receipt_too_large"}]}
    try:
        receipt = json.loads((raw or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        receipt = {"schema_version": 1, "complete": False,
                   "configured_services": [], "rows": [], "revision_checks": [],
                   "images": [], "config_digests": [],
                   "phases": [{"phase": "observation", "state": "unavailable"}]}
    if not isinstance(receipt, dict):
        raise RuntimeError("remote host observation returned an invalid receipt")
    for key, default in (("complete", False), ("configured_services", []),
                         ("rows", []), ("revision_checks", []), ("phases", []),
                         ("images", []), ("config_digests", [])):
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
    receipt["images"] = bounded_list(receipt["images"], 32)
    receipt["config_digests"] = bounded_list(
        receipt["config_digests"], _HOST_OBSERVATION_MAX_CONFIG_DIGESTS)
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


class _HostRuntimeObservationNotReady(RuntimeError):
    """Bounded post-Compose observation failure with safe last evidence."""

    def __init__(self, message: str, *, observation: dict, classified: dict,
                 stable: bool = False) -> None:
        super().__init__(message)
        self.observation = observation
        self.classified = classified
        self.stable = stable


def _unavailable_host_observation() -> dict:
    return {
        "schema_version": 1,
        "complete": False,
        "bounded": True,
        "configured_services": [],
        "rows": [],
        "revision_checks": [],
        "images": [],
        "config_digests": [],
        "phases": [{"phase": "observation", "state": "unavailable"}],
    }


def _host_observation_is_exact_ready(validated: dict, classified: dict,
                                     expected_revision: str) -> bool:
    source_required = bool(validated["deploy"].get("derived_environment"))
    return (
        classified.get("complete") is True
        and classified["topology"]["state"] == "ready"
        and classified["health"]["state"] == "ready"
        and (
            not source_required
            or _source_revision_evidence_ready(validated, classified, expected_revision)
        )
    )


def _host_observation_has_stable_contradiction(classified: dict) -> bool:
    source_checks = classified.get("source_revision", {}).get("checks", [])
    if any(item.get("state") == "mismatch" for item in source_checks
           if isinstance(item, dict)):
        return True
    topology = classified.get("topology", {})
    return (
        classified.get("complete") is True
        and bool(topology.get("missing_from_compose"))
    )


def _poll_post_compose_host_observation(
        validated: dict, entry: dict, source_dir: str, runtime_dir: str,
        expected_revision: str, *,
        deadline_seconds: float = _HOST_POST_COMPOSE_OBSERVATION_DEADLINE_SECONDS,
        initial_backoff_seconds: float =
        _HOST_POST_COMPOSE_OBSERVATION_INITIAL_BACKOFF_SECONDS,
        max_backoff_seconds: float =
        _HOST_POST_COMPOSE_OBSERVATION_MAX_BACKOFF_SECONDS) -> tuple[dict, dict]:
    """Poll only read-only whole-runtime evidence after Compose has completed."""
    if (not isinstance(deadline_seconds, (int, float))
            or isinstance(deadline_seconds, bool)
            or not 0 < deadline_seconds <= 120):
        raise ValueError("post-Compose observation deadline must be between 0 and 120 seconds")
    if (not isinstance(initial_backoff_seconds, (int, float))
            or isinstance(initial_backoff_seconds, bool)
            or not 0 < initial_backoff_seconds <= 10):
        raise ValueError("post-Compose observation backoff must be between 0 and 10 seconds")
    if (not isinstance(max_backoff_seconds, (int, float))
            or isinstance(max_backoff_seconds, bool)
            or not initial_backoff_seconds <= max_backoff_seconds <= 10):
        raise ValueError("post-Compose maximum backoff must be between initial backoff and 10 seconds")

    end = time.monotonic() + float(deadline_seconds)
    backoff = float(initial_backoff_seconds)
    last_observation = _unavailable_host_observation()
    last_classified = _classify_host_observation(
        validated, last_observation, expected_revision)
    while True:
        remaining = end - time.monotonic()
        if remaining < 1:
            break
        attempt_deadline = max(
            1,
            min(_HOST_POST_COMPOSE_OBSERVATION_ATTEMPT_SECONDS,
                int(remaining) if remaining >= 1 else 1),
        )
        try:
            last_observation = _observe_host_runtime(
                validated, entry, source_dir, runtime_dir,
                deadline_seconds=attempt_deadline,
                transport_grace_seconds=0,
            )
            last_classified = _classify_host_observation(
                validated, last_observation, expected_revision)
        except (RuntimeError, ValueError, subprocess.SubprocessError, OSError):
            # A read-only transport/parse failure may be transient. Retain the
            # last successful evidence, or the bounded unavailable receipt.
            pass
        else:
            if _host_observation_is_exact_ready(
                    validated, last_classified, expected_revision):
                return last_observation, last_classified
            if _host_observation_has_stable_contradiction(last_classified):
                raise _HostRuntimeObservationNotReady(
                    "remote runtime observation contains stable contradictory evidence",
                    observation=last_observation, classified=last_classified,
                    stable=True,
                )
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(backoff, remaining))
        backoff = min(float(max_backoff_seconds), backoff * 2)

    raise _HostRuntimeObservationNotReady(
        "remote runtime source/topology/health did not become fully ready before deadline",
        observation=last_observation, classified=last_classified,
    )


def _host_config_digest(validated: dict, runtime: dict, *, binding_key: bytes | None = None) -> str:
    environment = str(runtime.get("environment") or "")
    if binding_key is None:
        # Compatibility callers without rendered secrets retain a deterministic
        # non-secret shape. Public apply always supplies the owner key.
        environment_identity = canonical_digest({
            "keys": sorted(line.partition("=")[0] for line in environment.splitlines()
                           if line and "=" in line)})
    else:
        environment_identity = "sha256:" + hmac.new(
            binding_key, b"hosting-config-environment-v1\0" + environment.encode(),
            hashlib.sha256).hexdigest()
    payload = {
        "compose": validated["compose"],
        "compose_override": runtime.get("compose_override"),
        "environment_identity": environment_identity,
        "services": [validated["compose"]["service"],
                     *validated["compose"].get("background_services", [])],
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _registered_host_identity(entry: dict, remote_name: str, home: str) -> str:
    """Bind the non-secret registered target without persisting private values."""
    ssh = remote.remote_ssh_parts(entry)
    control_url = entry.get("control_url")
    if isinstance(control_url, str):
        control_url = control_url.strip().rstrip("/")
    transport = entry.get("control_transport") or (
        "tailscale" if entry.get("tailscale_host") else "https")
    identity = {
        "remote": remote_name,
        "ssh": {"target": ssh["target"], "host": ssh["host"],
                "port": ssh.get("port")},
        "control_transport": transport,
        "control_url": control_url,
        "tailscale_host": (
            str(entry.get("tailscale_host")).strip().lower()
            if entry.get("tailscale_host") else None),
        "mcp_port": int(entry.get("mcp_port") or remote.DEFAULT_MCP_PORT),
        "runtime_home": str(home).rstrip("/") or "/",
    }
    return canonical_digest(identity)


def _desired_edge_intent(validated: dict, entry: dict) -> dict:
    """Return the canonical non-secret edge and DNS intent for one registration."""
    plan = hosting.desired_plan(
        validated, entry.get("origin_ipv4"), entry.get("origin_ipv6"))
    return validate_edge_intent({
        "records": sorted(({
            "hostname": item.get("hostname"), "address": item.get("address"),
            "proxied": item.get("proxied"), "mode": item.get("mode"),
            "target": item.get("target"),
        } for item in plan["records"]),
            key=lambda item: (str(item["hostname"]), str(item["address"]))),
        "routes": list(validated.get("routes") or []),
        "certificate_hostnames": sorted(
            {str(item.get("hostname")) for item in validated.get("routes", [])}),
        "proxied": bool((validated.get("cloudflare") or {}).get("proxied")),
        "healthcheck_path": (validated.get("healthcheck") or {}).get("path"),
        "basic_auth": {
            "enabled": bool(validated.get("basic_auth")),
            "username": (validated.get("basic_auth") or {}).get("username"),
        },
    })


def _guarded_host_apply_plan(validated: dict, entry: dict, remote_name: str,
                             *, allow_zone_ssl_change: bool) -> dict:
    """Recompute every registration-derived apply precondition under its guard."""
    if not entry.get("provisioned"):
        raise hosting.HostingError("registered remote changed before host apply")
    if not entry.get("origin_ipv4"):
        raise hosting.HostingError(
            f"remote '{remote_name}' has no public origin address; "
            "run `./sb remote set-origin`")
    plan = hosting.desired_plan(
        validated, entry.get("origin_ipv4"), entry.get("origin_ipv6"))
    plan["remote"] = remote_name
    plan["remote_selection"] = "explicit"
    plan["cloudflare"] = _cloudflare_drift(plan)
    ssl = (plan["cloudflare"].get("ssl") if
           isinstance(plan.get("cloudflare"), dict) else None)
    if ssl and not allow_zone_ssl_change:
        non_strict = [zone for zone, mode in ssl.items() if mode != "strict"]
        if non_strict:
            raise hosting.HostingError(
                "these zones require --allow-zone-ssl-change: " +
                ", ".join(non_strict))
    return plan


def _authenticated_machine_identity(remote_name: str) -> str:
    """Read Feature 046's authenticated stable host projection."""
    from sandbox.resources.context import host_memory_status_projection

    try:
        projection = host_memory_status_projection(remote_name, budget_seconds=15)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RecoveryAuthorityError(
            "stable machine identity is unavailable") from exc
    identity = getattr(projection, "target_identity", None)
    evidence_state = getattr(projection, "evidence_state", None)
    if (not isinstance(identity, str) or not identity or len(identity) > 128 or
            evidence_state != "known"):
        raise RecoveryAuthorityError("stable machine identity is unavailable")
    return identity


@contextmanager
def _registered_recovery_authority(validated: dict, remote_name: str, operation: dict,
                                   authority: dict):
    """Hold supported registration stable from validation through commit."""
    with remote.registered_remote_lock():
        current = remote.get_remote(remote_name)
        if not isinstance(current, dict) or not current.get("provisioned"):
            raise RecoveryAuthorityError("registered remote is unavailable")
        home = remote.resolve_sandbox_home(current)
        expected = operation.get("evidence") or {}
        if (_registered_host_identity(current, remote_name, home) !=
                expected.get("host_identity")):
            raise RecoveryAuthorityError("registered remote changed")
        machine_identity = _authenticated_machine_identity(remote_name)
        if machine_identity != expected.get("machine_identity"):
            raise RecoveryAuthorityError("stable machine identity changed")
        try:
            current_edge_intent = _desired_edge_intent(validated, current)
            expected_edge_intent = validate_edge_intent(expected.get("edge_intent"))
        except ValueError:
            raise RecoveryAuthorityError("registered edge intent is invalid") from None
        if (canonical_digest(current_edge_intent) !=
                expected.get("edge_intent_digest") or
                current_edge_intent != expected_edge_intent):
            raise RecoveryAuthorityError("registered edge intent changed")
        authority["entry"] = current
        authority["machine_identity"] = machine_identity
        authority["edge_intent"] = current_edge_intent
        try:
            yield
        finally:
            authority.clear()


def _nonsecret_host_intent(validated: dict) -> str:
    declared_secrets = validated.get("secrets") or {}
    secret_shape = {
        name: sorted((declared_secrets.get(name) or {}).keys())
        for name in ("values", "required", "generated")
        if isinstance(declared_secrets.get(name), dict)
    }
    return canonical_digest({
        "project": validated.get("project"),
        "environment": validated.get("environment"),
        "compose": validated.get("compose"),
        "deploy": validated.get("deploy"),
        "routes": validated.get("routes"),
        "healthcheck": validated.get("healthcheck"),
        "cloudflare": validated.get("cloudflare"),
        "basic_auth": {key: value for key, value in
                       (validated.get("basic_auth") or {}).items()
                       if key != "password"},
        "secret_shape": secret_shape,
    })


def _durable_host_context() -> dict | None:
    fields = {
        "job_id": os.environ.get("SANDBOX_DURABLE_JOB_ID"),
        "request_id": os.environ.get("SANDBOX_DURABLE_REQUEST_ID"),
        "project_identity": os.environ.get("SANDBOX_DURABLE_PROJECT_IDENTITY"),
        "project_root_digest": os.environ.get("SANDBOX_DURABLE_PROJECT_ROOT_DIGEST"),
        "source_identity": os.environ.get("SANDBOX_DURABLE_SOURCE_IDENTITY"),
        "source_commit": os.environ.get("SANDBOX_DURABLE_SOURCE_COMMIT"),
        "source_dirty_digest": os.environ.get("SANDBOX_DURABLE_SOURCE_DIRTY_DIGEST"),
    }
    required = ("job_id", "request_id", "project_identity", "project_root_digest",
                "source_identity", "source_commit")
    if any(not isinstance(fields[key], str) or not fields[key] for key in required):
        return None
    return fields


def _accept_hosting_operation(state: dict, key: str, *, validated: dict,
                              entry: dict, remote_name: str, home: str,
                              source_state_identity: str, source_clean: bool,
                              source_commit: str, source_branch: str, config_digest: str,
                              secret_values: dict[str, str], save_state=None,
                              binding_key: bytes | None = None,
                              key_version: str | None = None,
                              machine_identity: str | None = None,
                              edge_intent: dict | None = None,
                              broker_locked: bool = False,
                              publish_binding_key: bool = False) -> dict | None:
    """Persist current-contract apply authority before the first host effect."""
    context = _durable_host_context()
    if context is None:
        return None
    try:
        edge_intent = validate_edge_intent(edge_intent)
    except ValueError:
        return None
    if (not machine_identity or
            not source_clean or context.get("source_dirty_digest") or
            context.get("source_commit") != source_commit):
        return None
    save_state = save_state or hosting.save_host_state
    record = (state.get("hosts") or {}).get(key) or {}
    generation = record.get("generation", 0)
    operation = {
        "schema_version": 1,
        "accepted_before_effects": True,
        "job_id": context["job_id"],
        "request_id": context["request_id"],
        "project_identity": context["project_identity"],
        "project_root_digest": context["project_root_digest"],
        "target": {"remote": remote_name, "project": validated["project"],
                   "environment": validated["environment"]},
        "compose_file_count": len(validated["compose"]["files"]),
        "expected_persistent_services": [
            validated["compose"]["service"],
            *validated["compose"].get("background_services", [])],
        "expected_initializer_services": list(
            validated["compose"].get("init_services", [])),
        "expected_one_shot_phases": [
            f"init:{name}" for name in validated["compose"].get("init_services", [])],
        "starting_generation": generation,
        "accepted_at": int(time.time()),
        "source": {"clean": True, "commit": source_commit,
                   "identity": context["source_identity"],
                   "state_identity": source_state_identity},
        "evidence": {
            "host_identity": _registered_host_identity(entry, remote_name, home),
            "machine_identity": machine_identity,
            "edge_intent": edge_intent,
            "edge_intent_digest": canonical_digest(edge_intent),
            "runtime_identity": canonical_digest({
                "project": validated["project"],
                "environment": validated["environment"],
                "compose_project": hosting.compose_project_name(validated),
            }),
            "source_identity": source_state_identity,
            "source_revision": source_commit,
            "source_branch": source_branch,
            "source_clean": True,
            "config_digest": config_digest,
            "manifest_digest": _nonsecret_host_intent(validated),
            "topology": [validated["compose"]["service"],
                         *validated["compose"].get("background_services", []),
                         *validated["compose"].get("init_services", [])],
            "images": [],
            "config_file_digests": [],
            "phase_receipt_digest": None,
            "one_shot_phases": [
                {"phase": f"init:{name}", "state": "pending"}
                for name in validated["compose"].get("init_services", [])
            ],
            "pending_phases": ["edge"],
        },
        "phases": [],
    }
    guard = nullcontext() if broker_locked else personal_secrets.hosting_binding_broker_lock()
    with guard:
        if binding_key is None or key_version is None:
            try:
                binding_key, key_version = personal_secrets.hosting_binding_key(
                    create=False)
            except ValueError:
                binding_key, key_version = personal_secrets.prepare_hosting_binding_key()
                publish_binding_key = True
        prospective = personal_secrets.prospective_hosting_binding_reference(
            key, secret_values, key=binding_key, key_version=key_version)
        preflight = json.loads(json.dumps(operation))
        preflight["evidence"].update({
            "secret_binding_metadata_id": prospective["metadata_id"],
            "secret_binding_revision": prospective["revision"],
            "secret_binding_key_version": prospective["key_version"],
        })
        preflight["digest"] = canonical_digest(preflight)
        if len(json.dumps(
                preflight, sort_keys=True, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            return None
        if publish_binding_key:
            published_key, published_version = personal_secrets.hosting_binding_key(
                prepared=(binding_key, key_version))
            if published_key != binding_key or published_version != key_version:
                raise RuntimeError("hosting recovery binding key changed before publication")
        binding_metadata = personal_secrets.write_hosting_binding_metadata(
            key, secret_values, key=binding_key, key_version=key_version,
            prepared=prospective)
        if (binding_metadata.get("revision") != prospective["revision"] or
                binding_metadata.get("key_version") != prospective["key_version"]):
            raise RuntimeError("hosting secret binding revision changed")
        record = state.setdefault("hosts", {}).setdefault(key, {})
        record.setdefault("generation", generation)
        record.pop("consumed_observation_authority", None)
        operation["evidence"].update({
            "secret_binding_metadata_id": binding_metadata["metadata_id"],
            "secret_binding_revision": binding_metadata["revision"],
            "secret_binding_key_version": key_version,
        })
        operation["digest"] = canonical_digest(operation)
        if len(json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            return None
        record["hosting_operation"] = operation
        save_state(state)
    return operation


def _assert_no_active_host_operation(state: dict, key: str) -> None:
    record = state.setdefault("hosts", {}).setdefault(key, {})
    if record.get("active_operation") is not None:
        raise hosting.HostingError("another host apply or recovery owns this target")
    if record.get("recovery_uncertainty") is not None:
        raise hosting.HostingError("uncertain host recovery state fences this target")
    activation = record.get("image_activation")
    if isinstance(activation, dict) and activation.get("active") is not None:
        raise hosting.HostingError("host image activation or recovery owns this target")


def _opaque_recovery_config_digests(observation: dict, compose_file_count: int,
                                    binding_key: bytes) -> list[dict]:
    """Blind only environment.env; never persist its raw content digest."""
    env_index = str(compose_file_count + 1)
    result = []
    for item in list(observation.get("config_digests") or [])[:
                     _HOST_OBSERVATION_MAX_CONFIG_DIGESTS]:
        if not isinstance(item, dict):
            continue
        name, digest = item.get("name"), item.get("digest")
        if name == env_index:
            digest = personal_secrets.opaque_hosting_digest(
                digest, key=binding_key, label="environment.env")
        result.append({"name": name, "digest": digest})
    return sorted(result, key=lambda item: str(item.get("name")))


def _with_host_writer_lock(validated: dict, remote_name: str, callback):
    """Serialize same-target writers with apply/recovery and reload their state."""
    repository = RecoveryRepository()
    key = hosting.state_key(remote_name, validated)
    with repository.target_mutation_port("login-url").target_mutation_transaction(key):
        with repository.state_lock():
            state = hosting.load_host_state()
            _assert_no_active_host_operation(state, key)
            return callback(state, repository._write)


def _with_host_effect_lease(validated: dict, remote_name: str, callback):
    """Hold target effect ownership while releasing shared state after validation."""
    repository = RecoveryRepository()
    key = hosting.state_key(remote_name, validated)
    with repository.target_mutation_port("sync").target_mutation_transaction(key):
        with repository.state_lock():
            state = hosting.load_host_state()
            _assert_no_active_host_operation(state, key)
        return callback(state)


def _refresh_hosting_operation(state: dict, key: str, *, classified: dict,
                               observation: dict, save_state=None) -> None:
    operation = (state["hosts"].get(key) or {}).get("hosting_operation")
    if not isinstance(operation, dict):
        return
    candidate_operation = json.loads(json.dumps(operation))
    evidence = candidate_operation.get("evidence")
    if not isinstance(evidence, dict):
        return
    save_state = save_state or hosting.save_host_state
    with personal_secrets.hosting_binding_broker_lock():
        binding_metadata = personal_secrets.read_hosting_binding_metadata(key)
        if (binding_metadata.get("metadata_id") !=
                evidence.get("secret_binding_metadata_id") or
                binding_metadata.get("revision") != evidence.get("secret_binding_revision")):
            raise RuntimeError("hosting secret binding authority changed during apply")
        binding_key, key_version = personal_secrets.hosting_binding_key(create=False)
        if key_version != evidence.get("secret_binding_key_version"):
            raise RuntimeError("hosting secret binding key changed during apply")
        evidence["images"] = sorted(
            list(observation.get("images") or [])[:32],
            key=lambda item: str(item.get("name")) if isinstance(item, dict) else "",
        )
        evidence["config_file_digests"] = _opaque_recovery_config_digests(
            observation, candidate_operation.get("compose_file_count", 0), binding_key)
        evidence["phase_receipt_digest"] = next((
            item.get("digest") for item in evidence["config_file_digests"]
            if isinstance(item, dict) and item.get("name") ==
            str(candidate_operation.get("compose_file_count", 0) + 2)
        ), None)
        evidence["topology"] = sorted(
            str(item) for item in observation.get("configured_services", []) if item)
        candidate_operation["evidence"]["source_revision"] = (
            candidate_operation.get("source", {}).get("commit")
            if (classified.get("source_revision", {}).get("state") == "ready" and
                observation.get("source_head") == candidate_operation.get("source", {}).get("commit") and
                observation.get("source_branch") == evidence.get("source_branch") and
                observation.get("source_clean") is True) else None
        )
        candidate_operation["phases"] = list(
            classified.get("phases") or [])[:_HOST_OBSERVATION_MAX_PHASES]
        candidate_operation["digest"] = canonical_digest({
            name: value for name, value in candidate_operation.items() if name != "digest"})
        if len(json.dumps(candidate_operation, sort_keys=True, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            raise RuntimeError("hosting operation exceeds its persistence bound")
        candidate_state = json.loads(json.dumps(state))
        candidate_state["hosts"][key]["hosting_operation"] = candidate_operation
        save_state(candidate_state)
        state.clear()
        state.update(candidate_state)


def _mark_hosting_init_complete(state: dict, key: str, *, entry: dict,
                                runtime_dir: str, save_state=None) -> None:
    operation = (state["hosts"].get(key) or {}).get("hosting_operation")
    if not isinstance(operation, dict):
        return
    candidate_operation = json.loads(json.dumps(operation))
    evidence = candidate_operation.get("evidence")
    phases = evidence.get("one_shot_phases") if isinstance(evidence, dict) else None
    if not isinstance(phases, list):
        return
    save_state = save_state or hosting.save_host_state
    evidence["one_shot_phases"] = [
        {"phase": item.get("phase"), "state": "complete"}
        for item in phases if isinstance(item, dict)
    ]
    candidate_operation["digest"] = canonical_digest({name: value for name, value in candidate_operation.items()
                                             if name != "digest"})
    if len(json.dumps(candidate_operation, sort_keys=True, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
        raise RuntimeError("hosting operation exceeds its persistence bound")
    _write_remote_text(
        entry, f"{runtime_dir}/recovery-phases.json",
        json.dumps({"schema_version": 1,
                    "phases": evidence["one_shot_phases"]},
                   sort_keys=True, separators=(",", ":")) + "\n",
        "0600",
    )
    candidate_state = json.loads(json.dumps(state))
    candidate_state["hosts"][key]["hosting_operation"] = candidate_operation
    save_state(candidate_state)
    state.clear()
    state.update(candidate_state)


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


def _source_replay_must_refuse(previous: dict, requested_revision: str,
                               config_digest: str, source_state_identity: str,
                               source_state_clean: bool) -> bool:
    """Refuse unprovable or unchanged dirty replay before runtime mutation."""
    recorded = previous.get("recorded_revision") or previous.get("commit")
    staged = previous.get("staged_revision")
    requested = previous.get("requested_revision")
    same_deployment = (
        previous.get("config_digest") == config_digest
        and requested_revision in {recorded, staged, requested}
    )
    if not same_deployment:
        return False
    previous_identity = previous.get("source_state_identity")
    known_current_identity = (
        previous.get("source_state_identity_version") == _SOURCE_STATE_IDENTITY_VERSION
        and isinstance(previous_identity, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", previous_identity) is not None
        and isinstance(previous.get("source_state_clean"), bool)
    )
    if not known_current_identity:
        return True
    return (
        previous.get("source_state_clean") is False
        and source_state_clean is False
        and previous_identity == source_state_identity
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
                                 *, runtime_state: str, save_state=None) -> None:
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
    (save_state or hosting.save_host_state)(state)


def _reconcile_exact_runtime_state(validated: dict, remote_name: str, state: dict,
                                   revision: str, config_digest: str, *,
                                   entry: dict | None = None,
                                   source_dir: str = "/unresolved/source",
                                   runtime_dir: str = "/unresolved/runtime",
                                   observation: dict | None = None,
                                   save_state=None) -> bool:
    """Repair only the local receipt when exact runtime evidence is complete."""
    key = hosting.state_key(remote_name, validated)
    record = state.setdefault("hosts", {}).get(key)
    if not isinstance(record, dict) or record.get("config_digest") != config_digest \
            or record.get("source_state_clean") is not True \
            or record.get("source_state_identity_version") != _SOURCE_STATE_IDENTITY_VERSION:
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
    (save_state or hosting.save_host_state)(state)
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
        "generation": recorded.get("generation", 0),
        "latest_recovery": _latest_recovery_summary(recorded),
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


def _latest_recovery_summary(record: dict) -> dict | None:
    attempts = record.get("recovery_attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    latest = attempts[-1]
    if not isinstance(latest, dict):
        return None
    return {key: latest.get(key) for key in (
        "schema_version", "request_id", "action", "result_family",
        "result_class", "effect_scope", "generation",
    )}


def _recovery_job_lookup(job_id: str) -> dict:
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import JobRepository

    repository = JobRepository(RUNTIME_DIR / "jobs" / "registry.sqlite3")
    try:
        value = repository.snapshot(job_id)
        value["submission"] = repository.submission_snapshot(job_id)
        return value
    finally:
        repository.close()


def _recovery_source_check(validated: dict, operation: dict) -> bool:
    source = operation.get("source") if isinstance(operation, dict) else None
    if not isinstance(source, dict) or source.get("clean") is not True:
        return False
    try:
        project_root = validated["project_root"]
        child_environment = {
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        def probe(*arguments):
            return subprocess.run(
                ["git", "-c", "core.fsmonitor=false", "-c",
                 "core.untrackedCache=false", "-C", str(project_root), *arguments], check=True,
                capture_output=True, text=True, env=child_environment).stdout
        branch = probe("branch", "--show-current").strip()
        if branch != (operation.get("evidence") or {}).get("source_branch"):
            return False
        commit = probe("rev-parse", "HEAD").strip()
        dirty = probe("status", "--porcelain=v1", "--untracked-files=all")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return not dirty and commit == source.get("commit")


def _recovery_observer(validated: dict, entry: dict, remote_name: str,
                       request: RecoveryRequest, operation: dict,
                       machine_identity: str | None = None,
                       edge_intent: dict | None = None) -> dict:
    target_key = hosting.state_key(remote_name, validated)
    expected_evidence = operation.get("evidence") or {}
    try:
        binding_metadata = personal_secrets.read_hosting_binding_metadata(target_key)
        binding_key, binding_key_version = personal_secrets.hosting_binding_key(
            create=False)
        if (binding_metadata.get("metadata_id") !=
                expected_evidence.get("secret_binding_metadata_id") or
                binding_metadata.get("revision") !=
                expected_evidence.get("secret_binding_revision") or
                binding_key_version !=
                expected_evidence.get("secret_binding_key_version")):
            raise ValueError("hosting secret binding metadata changed")
    except ValueError:
        # Validate broker authority before the first remote observation.
        raise RuntimeError("hosting secret binding metadata is unavailable or stale") from None
    home = remote.resolve_sandbox_home(entry)
    source_dir = f"{home}/deploy-src/hosts/{validated['project']}"
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    raw = _observe_host_runtime(validated, entry, source_dir, runtime_dir)
    fresh_machine_identity = _authenticated_machine_identity(remote_name)
    if not machine_identity:
        raise RecoveryAuthorityError("stable machine identity is unavailable")
    revision = (operation.get("source") or {}).get("commit")
    classified = _classify_host_observation(validated, raw, revision)
    declared = [validated["compose"]["service"],
                *validated["compose"].get("background_services", []),
                *validated["compose"].get("init_services", [])]
    configured = sorted(str(item) for item in raw.get("configured_services", []))
    images = sorted(
        ({"name": str(item.get("name")), "id": str(item.get("id"))}
         for item in raw.get("images", []) if isinstance(item, dict)
         and item.get("name") and item.get("id")),
        key=lambda item: item["name"],
    )
    services = [{"service": item["service"],
                 "state": "ready" if item.get("state") == "running"
                 and item.get("health") == "healthy" else "unavailable"}
                for item in classified.get("services", [])]
    return {
        "schema_version": 1,
        "complete": bool(raw.get("complete")) and sorted(declared) == configured,
        "bounded": raw.get("bounded") is True,
        "epoch_start": raw.get("epoch_start"),
        "epoch_end": raw.get("epoch_end"),
        "host_identity": _registered_host_identity(entry, remote_name, home),
        "machine_identity": fresh_machine_identity,
        "edge_intent_digest": canonical_digest(edge_intent),
        "runtime_identity": canonical_digest({
            "project": validated["project"], "environment": validated["environment"],
            "compose_project": hosting.compose_project_name(validated),
        }),
        "source_revision": (raw.get("source_head") if
                            classified.get("source_revision", {}).get("state") == "ready"
                            and raw.get("source_head") == revision else None),
        "source_branch": (raw.get("source_branch") if
                          raw.get("source_branch") in validated["deploy"]["allowed_branches"]
                          or "*" in validated["deploy"]["allowed_branches"] else None),
        "source_clean": raw.get("source_clean") is True,
        "topology": configured,
        "images": images,
        "config_file_digests": _opaque_recovery_config_digests(
            raw, len(validated["compose"]["files"]), binding_key),
        "phase_receipt_digest": next((
            item.get("digest") for item in raw.get("config_digests", [])
            if isinstance(item, dict) and item.get("name") ==
            str(len(validated["compose"]["files"]) + 2)
        ), None),
        "manifest_digest": _nonsecret_host_intent(validated),
        "secret_binding_key_version": binding_key_version,
        "secret_binding_metadata_id": binding_metadata["metadata_id"],
        "secret_binding_revision": binding_metadata["revision"],
        "pending_phases": ["edge"] if (
            ((hosting.load_host_state().get("hosts") or {}).get(
                hosting.state_key(remote_name, validated)) or {}).get("edge", {}).get("state")
            == "pending") else [],
        "one_shot_phases": list(
            (operation.get("evidence") or {}).get("one_shot_phases") or []),
        "services": services,
        "phases": [{"phase": str(item.get("phase")),
                    "state": "complete" if item.get("state") == "complete" else "unavailable"}
                   for item in raw.get("phases", []) if isinstance(item, dict)],
    }


def _cmd_host_recover(validated: dict, entry: dict, remote_name: str, args) -> None:
    required = {
        "--job-id": getattr(args, "job_id", None),
        "--original-request-id": getattr(args, "original_request_id", None),
        "--request-id": getattr(args, "request_id", None),
        "--expected-generation": getattr(args, "expected_generation", None),
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        _recovery_cli_refusal(validated, remote_name, args,
                              "host recover requires " + ", ".join(missing))
    action = (RecoveryAction.CONTINUE_EDGE if getattr(args, "continue_edge", False)
              else RecoveryAction.OBSERVE_RECONCILE)
    try:
        request = RecoveryRequest(
            action=action, request_id=args.request_id, job_id=args.job_id,
            original_request_id=args.original_request_id,
            target=TargetIdentity(remote_name, validated["project"], validated["environment"]),
            expected_generation=args.expected_generation,
            observation_request_id=getattr(args, "observation_request_id", None),
            evidence_id=getattr(args, "evidence_id", None),
            confirmed=bool(getattr(args, "confirm", False)),
        )
    except ValueError as exc:
        _recovery_cli_refusal(validated, remote_name, args, str(exc))
    authority = {}

    recovery_repository = RecoveryRepository()
    service = RecoveryService(
        repository=recovery_repository, job_lookup=_recovery_job_lookup,
        source_check=lambda operation: _recovery_source_check(validated, operation),
        observer=lambda recovery_request, operation: _recovery_observer(
            validated, authority["entry"], remote_name, recovery_request, operation,
            authority["machine_identity"], authority["edge_intent"]),
        edge_adapter=lambda recovery_request, operation: _continue_host_edge_only(
            validated, authority["entry"], remote_name, recovery_request, operation),
        governance_check=lambda _request: _recovery_governance_authorized(
            validated, remote_name),
        broker_guard=lambda _request: personal_secrets.hosting_binding_broker_lock(),
        authority_guard=lambda _request, operation: _registered_recovery_authority(
            validated, remote_name, operation, authority),
    )
    result = service.recover(request)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"host recovery {result['result_class']} "
              f"(generation {result['generation']['resulting']}, "
              f"scope={result['effect_scope']})")
    if not result.get("ok"):
        raise SystemExit(1)


def _recovery_cli_refusal(validated: dict, remote_name: str, args,
                          message: str) -> None:
    if not getattr(args, "json", False):
        die(message)
    action = "continue_edge" if getattr(args, "continue_edge", False) else "observe_reconcile"
    expected = getattr(args, "expected_generation", None)
    payload = {
        "ok": False, "schema_version": 1, "action": action,
        "result_family": "refused", "result_class": "binding_mismatch",
        "request_id": getattr(args, "request_id", None),
        "original": {"job_id": getattr(args, "job_id", None),
                     "request_id": getattr(args, "original_request_id", None)},
        "target": {"remote": remote_name, "project": validated["project"],
                   "environment": validated["environment"]},
        "generation": {"expected": expected, "resulting": expected},
        "effect_scope": "edge_only" if action == "continue_edge" else "receipt_only",
        "evidence": {"id": None, "complete": False, "expires_at": None},
        "phases": [], "error": message[:500],
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    raise SystemExit(1)


def _recovery_selector_refusal(args, missing: list[str]) -> None:
    """Emit one bounded refusal before a recovery target can be inferred."""
    action = ("continue_edge" if getattr(args, "continue_edge", False)
              else "observe_reconcile")
    expected = getattr(args, "expected_generation", None)
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        expected = 0
    payload = {
        "ok": False, "schema_version": 1, "action": action,
        "result_family": "refused", "result_class": "binding_mismatch",
        "request_id": "unresolved",
        "original": {"job_id": "unresolved", "request_id": "unresolved"},
        "target": {"remote": "unresolved", "project": "unresolved",
                   "environment": "unresolved"},
        "generation": {"expected": expected, "resulting": expected},
        "effect_scope": "edge_only" if action == "continue_edge" else "receipt_only",
        "evidence": {"id": None, "complete": False, "expires_at": None},
        "phases": [],
        "error": ("host recover requires explicit " + " and ".join(missing))[:500],
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    raise SystemExit(1)


def _recovery_governance_authorized(validated: dict, remote_name: str) -> bool:
    # Reviewed Feature 047 publishes an operation-local immutable-image edge
    # journal, not a host-recovery governance projection. Never reinterpret
    # that journal, image state, or hand-edited hosting state as authority.
    # Keep this adapter seam inactive until a canonical verifier is supplied.
    del validated, remote_name
    return False


def _continue_host_edge_only(validated: dict, entry: dict, remote_name: str,
                             _request: RecoveryRequest, operation: dict) -> dict:
    """Continue only the declared Caddy/Cloudflare edge for a proven runtime."""
    secret_values, missing = _secret_status(validated)
    if missing:
        raise hosting.HostingError("missing hosting secrets: " + ", ".join(missing))
    state = hosting.load_host_state()
    key = hosting.state_key(remote_name, validated)
    recorded = dict((state.get("hosts") or {}).get(key) or {})
    if (recorded.get("hosting_operation") or {}).get("digest") != operation.get("digest"):
        raise RuntimeError("hosting operation changed before edge continuation")
    port = recorded.get("loopback_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RuntimeError("edge continuation requires a proven existing loopback port")
    home = remote.resolve_sandbox_home(entry)
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    apply_log = f"{runtime_dir}/apply.log"
    try:
        edge_intent = validate_edge_intent(
            (operation.get("evidence") or {}).get("edge_intent"))
    except ValueError:
        raise RuntimeError("edge continuation intent is unavailable") from None
    if (
            canonical_digest(edge_intent) !=
            (operation.get("evidence") or {}).get("edge_intent_digest")):
        raise RuntimeError("edge continuation intent is unavailable")
    client = cloudflare.Client()
    caddy_name = f"sandbox-host-{validated['project']}-{validated['environment']}"
    previous_caddy = _read_remote_optional(
        entry, f"/etc/caddy/conf.d/{caddy_name}.caddy")
    changes: list[dict] = []

    def rollback() -> None:
        failures = []
        for change in reversed(changes):
            try:
                client.restore_record(
                    change["zone_id"], change["previous"], change["created_id"])
            except Exception as exc:
                failures.append(f"DNS restore: {exc}")
        try:
            _restore_host_caddy(entry, caddy_name, previous_caddy, log_path=apply_log)
        except Exception as exc:
            failures.append(f"Caddy restore: {exc}")
        if failures:
            raise hosting.HostingError("; ".join(failures))

    result: dict = {}

    def apply() -> None:
        proxied = edge_intent["proxied"]
        cert_path = key_path = certificate = None
        runtime = {"loopback_port": port, "records": edge_intent["records"],
                   "certificate_hostnames": edge_intent["certificate_hostnames"]}
        if proxied:
            cert_path, key_path, certificate = _origin_certificate(
                entry, validated, runtime, recorded, client, home)
        basic_hash = None
        if validated.get("basic_auth"):
            password_secret = validated["basic_auth"]["password_secret"]
            basic_hash = _remote_basic_auth_hash(entry, secret_values[password_secret])
        content = hosting.caddyfile(validated, port, cert_path, key_path, basic_hash)
        _configure_host_caddy(
            entry, caddy_name, content, previous_caddy, log_path=apply_log)
        zones: dict[str, dict] = {}
        for wanted in edge_intent["records"]:
            hostname = wanted["hostname"]
            zone = zones.setdefault(hostname, _zone_for_hostname(client, hostname))
            if proxied and client.current_ssl_mode(zone["id"]) != "strict":
                raise RuntimeError("edge recovery requires an already-strict zone policy")
            kind = "AAAA" if ":" in wanted["address"] else "A"
            records = client.records(zone["id"], hostname)
            cname = next((item for item in records if item.get("type") == "CNAME"), None)
            if cname:
                raise RuntimeError(
                    f"declared hostname {hostname} has a conflicting CNAME; edge recovery does not replace it")
            previous = next((item for item in records if item.get("type") == kind), None)
            created = client.upsert_address(
                zone["id"], hostname, wanted["address"], proxied=proxied)
            changes.append({"zone_id": zone["id"], "previous": previous,
                            "created_id": created.get("id")})
        credentials = None
        if validated.get("basic_auth"):
            auth = validated["basic_auth"]
            credentials = (auth["username"], secret_values[auth["password_secret"]])
        kwargs = {"healthcheck_path": edge_intent["healthcheck_path"],
                  "basic_auth_enabled": edge_intent["basic_auth"]["enabled"]}
        if credentials is not None:
            kwargs["basic_auth_credentials"] = credentials
        _verify_edge(edge_intent["routes"], **kwargs)
        result["record"] = {
            "certificate": certificate, "records": changes,
            "caddy_name": caddy_name, "edge": {"state": "ready"},
        }

    hosting.apply_with_rollback(apply, rollback)
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
                          state: dict, ttl_seconds: int | None,
                          save_state=None) -> dict:
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
    service = shlex.quote(config.get("service") or validated["compose"]["service"])
    _remote_checked(entry, (
        f"printf %s {shlex.quote(payload)} | base64 -d | {prefix} exec -T {service} "
        f"sh -c {shlex.quote(install)}"
    ), timeout=60)
    _remote_checked(entry, f"{prefix} exec -T {service} test -s {shlex.quote(target)}", timeout=30)
    host_state["autologin"] = {"user": config["user"], "expires_at": expires_at}
    (save_state or hosting.save_host_state)(state)
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
                progress=None, recovery_repository=None) -> dict:
    durable_save = (recovery_repository._write if recovery_repository is not None
                    else hosting.save_host_state)
    broker_guard = (personal_secrets.hosting_binding_broker_lock()
                    if recovery_repository is not None else nullcontext())
    with broker_guard:
        secret_values, missing = _secret_status(validated)
        if missing:
            raise hosting.HostingError("missing hosting secrets: " + ", ".join(missing))
        if recovery_repository is not None:
            try:
                binding_key, key_version = personal_secrets.hosting_binding_key(
                    create=False)
                publish_binding_key = False
            except ValueError:
                binding_key, key_version = personal_secrets.prepare_hosting_binding_key()
                publish_binding_key = True
        else:
            # Internal unit seams never authorize recovery or touch broker state.
            binding_key, key_version = b"\0" * 32, "test-unavailable"
            publish_binding_key = False
        base_commit = _resolve_host_source_commit(validated["project_root"])
        diff, untracked = remote.capture_uncommitted(validated["project_root"])
        require_clean = validated["deploy"]["require_clean"]
        if require_clean and (diff or untracked):
            raise hosting.HostingError(
                f"{validated['environment']} working tree changed before source staging")
        source_snapshot = remote.snapshot_dirty_overlay(
            validated["project_root"], diff, untracked,
            max_files=_HOST_SOURCE_SNAPSHOT_MAX_FILES,
            max_bytes=_HOST_SOURCE_SNAPSHOT_MAX_BYTES)
        source_state_identity = source_snapshot["identity"]
        source_state_clean = not bool(diff or untracked)
        home = remote.resolve_sandbox_home(entry)
        runtime["environment"] = hosting.render_env_file(
            validated, secret_values, pushed_commit_sha=base_commit)
        key = runtime["key"]
        _assert_no_active_host_operation(state, key)
        config_digest = _host_config_digest(
            validated, runtime, binding_key=binding_key)
        try:
            machine_identity = _authenticated_machine_identity(remote_name)
        except RecoveryAuthorityError:
            # Applying remains supported, but no recoverable authority may be
            # minted without the authenticated stable-host projection.
            machine_identity = None
        operation = _accept_hosting_operation(
            state, key, validated=validated, entry=entry, remote_name=remote_name,
            home=home, source_state_identity=source_state_identity,
            source_clean=source_state_clean, source_commit=base_commit,
            source_branch=branch, config_digest=config_digest,
            secret_values=secret_values, save_state=durable_save,
            binding_key=binding_key, key_version=key_version,
            machine_identity=machine_identity,
            edge_intent=_desired_edge_intent(validated, entry),
            broker_locked=True, publish_binding_key=publish_binding_key)
        if operation is None:
            record = state["hosts"].setdefault(key, {})
            record.pop("hosting_operation", None)
            record.pop("recovery_uncertainty", None)
            record.pop("consumed_observation_authority", None)
            generation = record.get("generation", 0)
            record["generation"] = generation + 1 if isinstance(generation, int) else 1
            durable_save(state)
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
    previous_entry = dict(state["hosts"].get(key) or {})
    legacy_clean = "sha256:" + hashlib.sha256(
        b"sandbox-dirty-overlay-v1\0"
    ).hexdigest()
    if (previous_entry.get("source_state_clean") is not True
            and previous_entry.get("source_state_identity") == legacy_clean
            and previous_entry.get("source_state_identity_version") is None
            and source_state_clean):
        # v1's empty-overlay digest is explicit historical clean proof.
        previous_entry["source_state_identity"] = source_state_identity
        previous_entry["source_state_clean"] = True
        previous_entry["source_state_identity_version"] = _SOURCE_STATE_IDENTITY_VERSION
    config_digest = _host_config_digest(
        validated, runtime, binding_key=binding_key)
    if _source_replay_must_refuse(
            previous_entry, sha, config_digest,
            source_state_identity, source_state_clean):
        try:
            _release_host_apply_reservation(entry, reservation)
        except Exception as cleanup_error:
            raise hosting.HostingError(
                "source receipt is not safe to replay and rollback-space "
                f"cleanup failed: {cleanup_error}"
            ) from cleanup_error
        raise RuntimeError(
            "source identity cannot be safely replayed at the same revision/config; "
            "refusing before target reset, observation, Compose, or initializer mutation"
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
        "source_state_identity_version": _SOURCE_STATE_IDENTITY_VERSION,
        "config_digest": config_digest,
        "runtime": {"state": "pending"},
        "edge": {"state": "pending"},
    }
    durable_save(state)
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
                    save_state=durable_save,
                )
            detail = f": {evidence_error}" if evidence_error is not None else ""
            raise RuntimeError(
                "existing runtime identity/topology is not fully proven; refusing "
                f"Compose or initializer replay{detail}"
            )
        if decision == "edge_only":
            if not _reconcile_exact_runtime_state(
                    validated, remote_name, state, sha, config_digest,
                    observation=classified, save_state=durable_save):
                raise RuntimeError("exact runtime reconciliation evidence was rejected")
        else:
            _run_compose(
                entry, validated, target, runtime_dir, runtime,
                stream_progress, apply_log,
                force_recreate=True,
            )
            _mark_hosting_init_complete(
                state, key, entry=entry, runtime_dir=runtime_dir,
                save_state=durable_save)
            health_receipt = _verify_remote_health(entry, runtime, stream_progress)
            try:
                observation, classified = _poll_post_compose_host_observation(
                    validated, entry, target, runtime_dir, sha,
                )
            except _HostRuntimeObservationNotReady as exc:
                observation = exc.observation
                classified = exc.classified
                _persist_runtime_observation(
                    state, key, classified, runtime_state="unverified",
                    save_state=durable_save,
                )
                raise RuntimeError(str(exc)) from exc
            _persist_runtime_observation(
                state, key, classified, runtime_state="ready",
                save_state=durable_save)
            _refresh_hosting_operation(
                state, key, classified=classified, observation=observation,
                save_state=durable_save,
            )
            record = state["hosts"][key]
            record.update({"commit": sha, "recorded_revision": sha})
            record["runtime"]["loopback_health"] = health_receipt
            durable_save(state)
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
        durable_save(state)

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


def _cmd_host_stage(args) -> None:
    """Static Feature 050 dispatch with no manifest/Compose/runtime preflight."""
    required = {
        "--project-dir": getattr(args, "project_dir", None),
        "--environment": getattr(args, "environment", None),
        "--remote": getattr(args, "remote", None),
        "--request-id": getattr(args, "request_id", None),
        "--verified-plan": getattr(args, "verified_plan", None),
    }
    missing = [name for name, value in required.items()
               if not isinstance(value, str) or not value.strip()]
    if missing or getattr(args, "expected_generation", None) is None:
        if getattr(args, "expected_generation", None) is None: missing.append("--expected-generation")
        die("host stage requires explicit " + ", ".join(missing) + "; no staging state was opened")
    status_only = getattr(args, "stage_status", False)
    if not status_only and not getattr(args, "confirm", False):
        die("host stage is protected; pass --confirm after reviewing the exact verified plan")
    try:
        from sandbox.core._paths import ENV_LOCAL, RUNTIME_DIR
        from sandbox.hosting.images import validate_verified_image_plan
        from sandbox.hosting.images.staging_models import StageRequest, StagingPolicy
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.isolation.credential_binding import CredentialBinding
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.service import GHCRStagingCredentialAdapter
        from sandbox.secrets.sources import SourceRegistry
        from sandbox.secrets.writer import load_revision_key
        from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport

        project_root = Path(args.project_dir).expanduser().resolve(strict=True)
        raw_plan = json.loads(Path(args.verified_plan).expanduser().read_text())
        plan = validate_verified_image_plan(raw_plan)
        scope = plan.delivery_identity_projection.target_scope
        if (scope.remote, scope.environment) != (args.remote, args.environment):
            raise ValueError("verified plan target scope does not match explicit stage selectors")
        scope_id = hashlib.sha256(
            f"{args.remote}\0{scope.project}\0{args.environment}".encode()).hexdigest()
        policy_path = RUNTIME_DIR / "hosting" / "image-staging" / "policies" / f"{scope_id}.json"
        private = json.loads(policy_path.read_text())
        if type(private) is not dict or set(private) != {"policy", "binding", "secret_sources"}:
            raise ValueError("machine staging policy is invalid")
        policy = StagingPolicy.from_mapping(private["policy"])
        binding = CredentialBinding.from_dict(private["binding"])
        request = StageRequest.create(
            request_id=args.request_id, expected_generation=args.expected_generation,
            plan=plan, staging_policy_digest=policy.policy_digest, target=policy.target,
            confirmed=True,
        )
        repository = StageRepository()
        if status_only:
            result = ImageStagingService(
                repository=repository, broker=None, worker=None).status(request)
        else:
            registry = SourceRegistry(
                project_root, private["secret_sources"], personal_path=ENV_LOCAL,
                project_scope=str(project_root),
            )
            resolver = SecretReferenceResolver(registry, owner=binding.owner)
            revision_key = load_revision_key(RUNTIME_DIR / "secrets" / "revision.key")
            broker = GHCRStagingCredentialAdapter(
                resolver, binding, recipient=policy.broker_recipient,
                credential_reference_revision=policy.credential_reference_revision,
                revision_key=revision_key,
            )
            service = ImageStagingService(
                repository=repository, broker=broker,
                worker=StageWorker(RegisteredRemoteImageTransport()),
            )
            recovery_repository = RecoveryRepository()
            with recovery_repository.target_mutation_port(
                    "image-stage").target_mutation_transaction(request.target.target_identity):
                result = service.stage(request, policy)
    except (OSError, json.JSONDecodeError, TypeError, ValueError, RuntimeError):
        # Private paths, remote diagnostics, helper output, and broker details
        # never cross the public stage envelope.
        payload = {"schema_version": 1, "ok": False, "result_class": "refused",
                   "code": "policy_mismatch", "request_id": str(args.request_id)[:256],
                   "generation": int(args.expected_generation)}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        raise SystemExit(1)
    payload = result.as_mapping()
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if not result.ok and result.result_class != "in_progress": raise SystemExit(1)


class _HostImageEdgeAdapter:
    """Immutable edge-readiness request over the existing public-route verifier."""

    def __init__(self, validated: dict, *, activation_repository=None,
                 target_identity: str | None = None) -> None:
        self.validated = validated
        self.activation_repository = activation_repository
        self.target_identity = target_identity
        self.results = {}

    def observe_plan(self):
        from sandbox.hosting.images.activation.models import activation_digest
        _verify_edge(self.validated["routes"],
                     healthcheck_path=self.validated["healthcheck"]["path"],
                     basic_auth_enabled=bool(self.validated.get("basic_auth")))
        health_path = self.validated["healthcheck"]["path"]
        routes = sorted(({
            "hostname": item["hostname"], "mode": item["mode"],
            "target": item.get("target"), "primary": item.get("primary") is True,
            "healthcheck_path": health_path if item.get("mode") == "serve" else "/",
        } for item in self.validated["routes"] if not item["hostname"].startswith("*.")),
            key=lambda item: (item["hostname"], item["mode"], str(item["target"])))
        return {"routes": routes, "route_digest": activation_digest(
            "sandbox.hosting.images.activation-edge-routes.v1", routes)}

    def lookup(self, request_id: str, request_digest: str):
        if self.activation_repository is not None and self.target_identity is not None:
            state = self.activation_repository.snapshot(self.target_identity)
            active = state.get("active")
            durable = active.get("edge_result") if isinstance(active, dict) else None
            if isinstance(durable, dict):
                if durable.get("request_digest") != request_digest:
                    return "ambiguous"
                if durable.get("phase") == "prepared" and durable.get("terminal") is False:
                    return "not_entered"
                return durable
        current = self.results.get(request_id)
        if current is None: return "not_entered"
        return current if current.get("request_digest") == request_digest else "ambiguous"

    def apply(self, request_id: str, request_digest: str, **_evidence) -> dict:
        from sandbox.hosting.images.activation.models import ActivationContractError
        # The route verifier proves reachability only. It cannot identify the
        # activated runtime generation, so it is never activation authority.
        raise ActivationContractError("edge_incomplete")


def _host_image_machine_bundle(args, plan) -> dict:
    scope = plan.delivery_identity_projection.target_scope
    identity = hashlib.sha256(
        f"{args.remote}\0{scope.project}\0{args.environment}".encode()).hexdigest()
    policy_root = Path(RUNTIME_DIR)
    for component in ("hosting", "image-activation", "policies"):
        policy_root = policy_root / component
        directory = policy_root.lstat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid() \
                or stat.S_IMODE(directory.st_mode) & 0o077:
            raise ValueError("machine activation policy directory is unsafe")
    path = policy_root / f"{identity}.json"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() \
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) & 0o077 \
                or before.st_size < 2 or before.st_size > 1024 * 1024:
            raise ValueError("machine activation policy is unsafe")
        data = bytearray()
        while len(data) <= 1024 * 1024:
            chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if len(data) > 1024 * 1024 or (before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns, before.st_mode, before.st_uid,
                before.st_nlink) != (after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns, after.st_mode, after.st_uid,
                after.st_nlink):
            raise ValueError("machine activation policy changed during read")
    finally:
        os.close(descriptor)
    raw = json.loads(bytes(data))
    required = {"policy", "binding", "rollback_subject", "rollback_grant",
                "compose_files", "compose_project", "configuration_digest",
                "init_data_contract_digest", "edge_required",
                "rollback_grant_public_key"}
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("machine activation policy is invalid")
    return raw


def _host_image_target_configuration_key(
        master_key: bytes, machine_identity: str, target_identity: str) -> bytes:
    if type(master_key) is not bytes or len(master_key) != 32 \
            or type(machine_identity) is not str or not machine_identity \
            or type(target_identity) is not str or not target_identity:
        raise ValueError("activation configuration binding key is invalid")
    scope = json.dumps({"machine_identity": machine_identity,
                        "target_identity": target_identity},
                       sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(master_key,
                    b"sandbox-feature-051-target-configuration-key-v1\0" + scope,
                    hashlib.sha256).digest()


def _host_image_argv_runner(entry):
    def invoke(*, argv, environment, private_environment, private_environment_source,
               redact_environment_keys, timeout_seconds, max_output_bytes):
        allowed = {"PATH", "LANG", "LC_ALL"}
        allowed.update(name for name in environment if name.startswith("SANDBOX_ACTIVATION_IMAGE_"))
        allowed.update(name for name in environment
                       if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name))
        if set(environment) - allowed:
            raise ValueError("activation subprocess environment is not closed")
        if type(private_environment) is not dict or any(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) is None
                or type(value) is not str for key, value in private_environment.items()):
            raise ValueError("activation private input is invalid")
        if type(private_environment_source) is not dict \
                or (redact_environment_keys is not None and type(redact_environment_keys) is not tuple):
            raise ValueError("activation private source is invalid")
        if private_environment_source:
            init_fields = {"compose_files", "project_name", "project_directory", "environment",
                           "render_digest", "runtime_epoch", "service", "keys"}
            effect_fields = {"kind", "compose_files", "project_name", "project_directory",
                             "environment", "render_digest", "runtime_epoch", "services"}
            if set(private_environment_source) not in {frozenset(init_fields), frozenset(effect_fields)} \
                    or type(private_environment_source["compose_files"]) not in {list, tuple} \
                    or type(private_environment_source["project_name"]) is not str \
                    or type(private_environment_source["project_directory"]) is not str \
                    or type(private_environment_source["environment"]) is not dict \
                    or re.fullmatch(r"sha256:[0-9a-f]{64}",
                                    private_environment_source["render_digest"]) is None:
                raise ValueError("activation private source is invalid")
            if type(private_environment_source["runtime_epoch"]) is not str \
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}",
                                    private_environment_source["runtime_epoch"]) is None:
                raise ValueError("activation private source is invalid")
            if set(private_environment_source) == effect_fields:
                private_source_type = private_environment_source["kind"]
                if private_source_type != "compose_replace_v1" \
                        or type(private_environment_source["services"]) not in {list, tuple} \
                        or not private_environment_source["services"]:
                    raise ValueError("activation private source is invalid")
            elif type(private_environment_source["service"]) is not str \
                    or type(private_environment_source["keys"]) not in {list, tuple}:
                raise ValueError("activation private source is invalid")
        frame = json.dumps({"environment": {**environment, **private_environment},
                            "redact": list(private_environment.values()),
                            "source": private_environment_source,
                            "redact_keys": (None if redact_environment_keys is None
                                            else list(redact_environment_keys))},
                           sort_keys=True, separators=(",", ":"))
        if len(frame.encode()) > 1024 * 1024:
            raise ValueError("activation private input exceeds bound")
        program = "\n".join((
            "import base64,hashlib,hmac,json,re,subprocess,sys",
            "def public_projection(c):",
            " out={'services':{}};services=c.get('services',{})",
            " if not isinstance(services,dict):services={}",
            " for name,svc in services.items():",
            "  if not isinstance(svc,dict):out['services'][name]={'invalid':True};continue",
            "  env=svc.get('environment');deps=svc.get('depends_on');labels=svc.get('labels')",
            "  image=svc.get('image');image=image if isinstance(image,str) and re.fullmatch(r'[a-z0-9.]+/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}',image) else None",
            "  pull=svc.get('pull_policy');pull=pull if pull in ('never','missing-refused') else None",
            "  platform=svc.get('platform');platform=platform if isinstance(platform,str) and re.fullmatch(r'[a-z0-9]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?',platform) else None",
            "  topology=labels.get('org.sandbox.application-topology.v1') if isinstance(labels,dict) else None;topology=topology if isinstance(topology,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',topology) else None",
            "  out['services'][name]={'image':image,'build':None if svc.get('build') is None else {'present':True},'pull_policy':pull,'platform':platform,'depends_on':{key:{} for key in deps} if isinstance(deps,dict) else {'__invalid__':{}},'labels':{'org.sandbox.application-topology.v1':topology},'x-sandbox-environment-keys':sorted(env) if isinstance(env,dict) else []}",
            " out['x-sandbox-has-configs']=bool(c.get('configs'))",
            " out['x-sandbox-has-secrets']=bool(c.get('secrets'))",
            " networks=c.get('networks',{})",
            " out['x-sandbox-has-external-networks']=not isinstance(networks,dict) or any(not isinstance(item,dict) or item.get('external') not in (None,False) for item in networks.values())",
            " return out",
            "def strings(v):",
            " if isinstance(v,str):return [v]",
            " if isinstance(v,dict):return [item for key,value in v.items() for item in (([key] if isinstance(key,str) else [])+strings(value))]",
            " if isinstance(v,list):return [item for value in v for item in strings(value)]",
            " return []",
            "p=json.load(sys.stdin);e=p['environment'];s=p['source']",
            "encoded_key=e.pop('SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY',None)",
            "try:configuration_key=base64.b64decode(encoded_key,validate=True) if encoded_key else None",
            "except Exception:configuration_key=None",
            "if configuration_key is not None and len(configuration_key)!=32:configuration_key=None",
            "def configuration_identity(raw):return 'sha256:'+hmac.new(configuration_key,b'sandbox-feature-051-compose-v1\\0'+raw,hashlib.sha256).hexdigest()",
            "def config_hash_identity(name,value):",
            " if not isinstance(name,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',name) is None or not isinstance(value,str) or re.fullmatch(r'[0-9a-f]{64}',value) is None:raise ValueError('compose_config_hash_unavailable')",
            " return 'sha256:'+hmac.new(configuration_key,b'sandbox-feature-051-compose-config-hash-v1\\0'+name.encode()+b'\\0'+value.encode(),hashlib.sha256).hexdigest()",
            "def compose_hashes(raw,c):",
            " a=sys.argv[2:];directory=a[a.index('--project-directory')+1];project=a[a.index('--project-name')+1]",
            " base=['docker','compose','--file','-','--project-directory',directory,'--project-name',project]",
            " out={}",
            " for name in sorted(c.get('services',{})):",
            "  h=subprocess.run(base+['config','--hash',name],env=e,input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  parts=h.stdout.decode().strip().split()",
            "  if h.returncode!=0 or h.stderr or len(parts)!=2 or parts[0]!=name or re.fullmatch(r'[0-9a-f]{64}',parts[1]) is None:raise ValueError('compose_config_hash_unavailable')",
            "  out[name]=config_hash_identity(name,parts[1])",
            " return out",
            "def observe_running(project,names):",
            " if configuration_key is None:raise ValueError('configuration_binding_unavailable')",
            " p=subprocess.run(['docker','ps','--filter','label=com.docker.compose.project='+project,'--format','{{.ID}}'],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            " if p.returncode!=0 or p.stderr:raise ValueError('runtime_mismatch')",
            " out=[]",
            " container_ids=p.stdout.decode().split()",
            " if len(container_ids)>len(names):raise ValueError('runtime_mismatch')",
            " for container_id in container_ids:",
            "  x=subprocess.run(['docker','inspect',container_id],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  try:raw=json.loads(x.stdout)[0]",
            "  except Exception:raise ValueError('runtime_mismatch')",
            "  cfg=raw.get('Config') or {};labels=cfg.get('Labels') or {};name=labels.get('com.docker.compose.service')",
            "  if x.returncode!=0 or x.stderr or labels.get('com.docker.compose.project')!=project or name not in names:continue",
            "  image_id=raw.get('Image');ii=subprocess.run(['docker','image','inspect',image_id],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  try:ir=json.loads(ii.stdout)[0]",
            "  except Exception:raise ValueError('runtime_mismatch')",
            "  if ii.returncode!=0 or ii.stderr or ir.get('Id')!=image_id:raise ValueError('runtime_mismatch')",
            "  platform={'os':ir.get('Os'),'architecture':ir.get('Architecture')}",
            "  if ir.get('Variant'):platform['variant']=ir['Variant']",
            "  out.append({'service':name,'runtime_identity':raw.get('Id'),'compose_project':project,'declared_image':cfg.get('Image'),'repository_digest':cfg.get('Image'),'local_image_id':image_id,'config_digest':image_id,'platform':platform,'topology_identity':labels.get('org.sandbox.application-topology.v1'),'compose_config_hash':config_hash_identity(name,labels.get('com.docker.compose.config-hash')),'healthy':(raw.get('State') or {}).get('Health',{}).get('Status')=='healthy'})",
            " return out",
            "vals=[];render=None",
            "if s:",
            " if configuration_key is None:sys.stderr.write('configuration_binding_unavailable');sys.exit(91)",
            " e.update(s['environment'])",
            " before=subprocess.run(['docker','info','--format','{{.ID}}'],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            " if before.returncode!=0 or before.stderr or before.stdout.decode().strip()!=s['runtime_epoch']:sys.stderr.write('compose_daemon_mismatch');sys.exit(91)",
            " a=['docker','compose']",
            " for f in s['compose_files']:a+=['--file',f]",
            " a+=['--project-directory',s['project_directory'],'--project-name',s['project_name'],'config','--format','json']",
            " q=subprocess.run(a,env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            " if q.returncode!=0 or q.stderr:sys.stderr.write('compose_source_refused');sys.exit(91)",
            " try:c=json.loads(q.stdout)",
            " except Exception:sys.stderr.write('compose_source_malformed');sys.exit(91)",
            " vals.extend(strings(c))",
            " got=configuration_identity(q.stdout)",
            " if got!=s['render_digest']:sys.stderr.write('compose_source_mismatch');sys.exit(91)",
            " if s.get('kind')=='compose_replace_v1':",
            "  for svc in c.get('services',{}).values():vals.extend((svc.get('environment') or {}).values())",
            "  render=q.stdout",
            " else:",
            "  env=(c['services'][s['service']].get('environment') or {})",
            "  selected={k:env[k] for k in s['keys']};e.update(selected);vals.extend(selected.values())",
            "if sys.argv[2:3]==['sandbox-activation-observe-running']:",
            " try:r=subprocess.CompletedProcess([],0,json.dumps(observe_running(sys.argv[3],set(sys.argv[4:])),separators=(',',':')).encode(),b'')",
            " except Exception:r=subprocess.CompletedProcess([],91,b'',b'runtime_projection_failed')",
            "else:r=subprocess.run(sys.argv[2:],env=e,input=render,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "if s and s.get('kind')=='compose_replace_v1' and r.returncode==0:",
            " base=['docker','compose','--file','-','--project-directory',s['project_directory'],'--project-name',s['project_name']]",
            " for svc in s['services']:",
            "  h=subprocess.run(base+['config','--hash',svc],env=e,input=q.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  ids=subprocess.run(base+['ps','--quiet',svc],env=e,input=q.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  rows=ids.stdout.split()",
            "  if h.returncode!=0 or ids.returncode!=0 or len(rows)!=1:r=subprocess.CompletedProcess([],92,b'',b'compose_runtime_config_unproven');break",
            "  actual=subprocess.run(['docker','inspect','--format','{{index .Config.Labels \"com.docker.compose.config-hash\"}}',rows[0]],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            "  expected=h.stdout.strip().split()[-1:]",
            "  if actual.returncode!=0 or len(expected)!=1 or actual.stdout.strip()!=expected[0]:r=subprocess.CompletedProcess([],92,b'',b'compose_runtime_config_mismatch');break",
            "if s and r.returncode==0:",
            " after=subprocess.run(['docker','info','--format','{{.ID}}'],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE)",
            " if after.returncode!=0 or after.stderr or after.stdout.decode().strip()!=s['runtime_epoch']:r=subprocess.CompletedProcess([],92,b'',b'compose_daemon_changed')",
            "o=r.stdout;d=r.stderr",
            "if 'compose' in sys.argv[2:] and 'config' in sys.argv[2:] and r.returncode==0:",
            " raw=o",
            " if configuration_key is None:r=subprocess.CompletedProcess([],91,b'',b'configuration_binding_unavailable');o=b'';d=r.stderr",
            " else:",
            "  try:parsed=json.loads(raw);hashes=compose_hashes(raw,parsed);c=public_projection(parsed)",
            "  except Exception:r=subprocess.CompletedProcess([],91,b'',b'compose_config_hash_unavailable');o=b'';d=r.stderr",
            "  else:c['x-sandbox-configuration-digest']=configuration_identity(raw);c['x-sandbox-compose-config-hashes']=hashes;o=json.dumps(c,separators=(',',':')).encode()",
            "if 'compose' in sys.argv[2:] and 'config' in sys.argv[2:] and r.returncode!=0:o=b'';d=b'compose_config_failed'",
            "if p['redact_keys'] is not None and r.returncode==0:",
            " z=json.loads(o);z=[z] if isinstance(z,dict) else z",
            " for x in z:",
            "  m={i.split('=',1)[0]:i.split('=',1)[1] for i in (x.get('Config',{}).get('Env') or []) if '=' in i}",
            "  x['DeclaredEnvironmentKeys']=sorted(p['redact_keys']);x['DeclaredEnvironmentMatch']=all(m.get(k)==e.get(k) for k in p['redact_keys'])",
            "  x.get('Config',{}).pop('Env',None)",
            " o=json.dumps(z[0] if len(z)==1 else z,separators=(',',':')).encode()",
            "if p['redact_keys'] is not None and r.returncode!=0:o=b'';d=b'inspect_failed'",
            "for x in sorted({v.encode() for v in list(p['redact'])+vals if v},key=len,reverse=True):o=o.replace(x,b'[redacted]');d=d.replace(x,b'[redacted]')",
            "sys.stdout.buffer.write(o);sys.stderr.buffer.write(d);sys.exit(r.returncode)",
        ))
        command = ["python3", "-c", program, "--", *argv]
        result = remote.ssh_run(entry, shlex.join(command), timeout=timeout_seconds,
                                input_data=frame)
        stdout = str(getattr(result, "stdout", "")); stderr = str(getattr(result, "stderr", ""))
        if len(stdout.encode()) + len(stderr.encode()) > max_output_bytes:
            raise RuntimeError("activation remote output exceeded bound")
        return {"returncode": int(getattr(result, "returncode", 1)),
                "stdout": stdout, "stderr": stderr, "terminated": True}
    return invoke


def _cmd_host_image(validated: dict, args) -> None:
    action = getattr(args, "image_action", None)
    common = {"--project-dir": getattr(args, "project_dir", None),
              "--environment": getattr(args, "environment", None),
              "--remote": getattr(args, "remote", None),
              "--request-id": getattr(args, "request_id", None)}
    missing = [name for name, value in common.items()
               if not isinstance(value, str) or not value.strip()]
    if action not in {"activate", "adopt", "rollback", "recover"}:
        missing.append("image action")
    if getattr(args, "expected_generation", None) is None:
        missing.append("--expected-generation")
    if missing:
        die("host image requires explicit " + ", ".join(missing) + "; no state was opened")
    if not getattr(args, "confirm", False):
        die("host image is protected; pass --confirm after reviewing the exact request")
    try:
        from sandbox.hosting.images.activation.models import (
            ActivationAuthorityBinding, ActivationPolicy, ActivationRequest,
            ForwardRollbackSubject, RollbackCompatibilityGrant,
        )
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        from sandbox.hosting.images.activation.service import ActivationService
        from sandbox.hosting.images.activation.policy import SshRollbackGrantVerifier
        from sandbox.hosting.images.staging_models import validate_staged_image_proof
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.recovery.models import ActivationTransitionProjection
        from sandbox.hosting.recovery.service import ActivationTransitionObserver
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport

        recovery_repository = RecoveryRepository()
        host_port = recovery_repository.activation_host_state_port()
        mutation_port = recovery_repository.target_mutation_port(
            "image-recover" if action == "recover" else action)
        activation_repository = ActivationRepository(
            host_state_port=host_port, stage_repository=StageRepository(),
            target_mutation_port=mutation_port)
        if action == "recover":
            transaction_digest = getattr(args, "activation_transaction", None)
            if not isinstance(transaction_digest, str) or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", transaction_digest):
                raise ValueError("activation transaction digest is required")
            target_key = hosting.state_key(args.remote, validated)
            request_digest = canonical_digest({"schema_version": 1, "action": "image_recover",
                "request_id": args.request_id, "transaction_digest": transaction_digest,
                "expected_generation": args.expected_generation, "confirmed": True})
            with activation_repository.operation_transaction(target_key), \
                    remote.registered_remote_lock():
                entry = remote.get_remote(args.remote)
                if not entry:
                    raise ValueError("registered remote is unavailable")
                if target_key != hosting.state_key(args.remote, validated):
                    raise ValueError("registered target identity changed")
                state = activation_repository.snapshot(target_key)
                replay = state.get("recovery_results", {}).get(args.request_id)
                if isinstance(replay, dict):
                    if replay.get("request_digest") != request_digest:
                        raise ValueError("recovery request conflicts with stored result")
                    print(json.dumps(replay, sort_keys=True, separators=(",", ":")))
                    if replay.get("ok") is not True:
                        raise SystemExit(1)
                    return
                active = state.get("active")
                if not isinstance(active, dict) or active.get("transaction_digest") != transaction_digest:
                    raise ValueError("activation transaction is unavailable")
                candidate = active.get("candidate_generation") or {}
                prior = state.get("current") or {}
                context = active.get("recovery_context") or {}
                authority_target = context.get("target")
                compose_project = context.get("compose_project")
                selected_services = context.get("selected_services")
                if not isinstance(authority_target, dict) \
                        or authority_target.get("target_identity") != target_key \
                        or not isinstance(compose_project, str) or not compose_project \
                        or not isinstance(selected_services, list) or not selected_services \
                        or (candidate and (candidate.get("target") != authority_target \
                            or candidate.get("compose_project") != compose_project)) \
                        or (prior and (prior.get("target") != authority_target \
                            or prior.get("compose_project") != compose_project)):
                    raise ValueError("activation generation authority is unavailable")
                projection = ActivationTransitionProjection(
                    transaction_digest=transaction_digest,
                    request_digest=active["request_digest"], operation=active["operation"],
                    phase=active["phase"], effect_entered=active["effect_entered"],
                    expected_generation=args.expected_generation,
                    new_generation_digest=candidate.get("generation_digest"),
                    prior_generation_digest=prior.get("generation_digest"),
                    target=authority_target,
                    new_services=tuple(candidate.get("service_projection") or ()),
                    prior_services=tuple(prior.get("service_projection") or ()))
                configuration_binding_master, _configuration_binding_key_version = \
                    personal_secrets.hosting_binding_key(create=False)
                configuration_binding_key = _host_image_target_configuration_key(
                    configuration_binding_master, authority_target["machine_identity"],
                    authority_target["target_identity"])
                transport = RegisteredRemoteActivationTransport(
                    argv_runner=_host_image_argv_runner(entry),
                    target_identity_observer=lambda: {
                        "machine_identity": _authenticated_machine_identity(args.remote),
                        "target_identity": target_key},
                    configuration_binding_key=configuration_binding_key)
                services = tuple(selected_services)
                target = authority_target
                def reader(_projection):
                    observed = transport.observe_running(
                        target=target, services=services,
                        compose_project=compose_project)
                    rows = observed.get("services") or []
                    def exact(generation):
                        expected = tuple((generation or {}).get("service_projection") or ())
                        return bool(generation) and tuple(sorted(rows, key=lambda item: item.get("service", ""))) == \
                            tuple(sorted(expected, key=lambda item: item.get("service", "")))
                    candidate_exact = exact(candidate) if candidate else False
                    prior_exact = exact(prior) if prior else not rows
                    generation_digest = (candidate.get("generation_digest")
                                         if candidate_exact and not prior_exact
                                         else prior.get("generation_digest")
                                         if prior_exact and not candidate_exact else None)
                    return {"target_epoch_start": observed["target_epoch_start"],
                            "target_epoch_end": observed["target_epoch_end"],
                            "target_identity_start": observed["target_identity_start"],
                            "target_identity_end": observed["target_identity_end"],
                            "runtime_epoch_start": observed["runtime_epoch_start"],
                            "runtime_epoch_end": observed["runtime_epoch_end"],
                            "generation_digest": generation_digest, "services": rows}
                observer = ActivationTransitionObserver(reader)
                payload = activation_repository.recover(
                    target_key, request_id=args.request_id, request_digest=request_digest,
                    expected_generation=args.expected_generation,
                    observer=lambda: observer.observe(projection), ownership_held=True)
        else:
            for name in ("verified_plan", "staged_proof", "admission_deadline"):
                if not isinstance(getattr(args, name, None), str) or not getattr(args, name).strip():
                    raise ValueError(f"--{name.replace('_', '-')} is required")
            from sandbox.hosting.images import validate_verified_image_plan
            plan = validate_verified_image_plan(json.loads(Path(args.verified_plan).read_text()))
            proof = validate_staged_image_proof(json.loads(Path(args.staged_proof).read_text()))
            bundle = _host_image_machine_bundle(args, plan)
            policy = ActivationPolicy.from_mapping(bundle["policy"])
            binding = ActivationAuthorityBinding.from_mapping(bundle["binding"])
            subject = ForwardRollbackSubject(**bundle["rollback_subject"])
            grant_raw = dict(bundle["rollback_grant"])
            grant_raw["subject"] = ForwardRollbackSubject(**grant_raw["subject"])
            grant = RollbackCompatibilityGrant(**grant_raw)
            request = ActivationRequest.create(
                request_id=args.request_id, operation=action,
                expected_generation=args.expected_generation,
                policy_digest=policy.policy_digest, plan=plan, proof=proof,
                authority_binding_digest=binding.binding_digest,
                rollback_subject_digest=subject.subject_digest,
                rollback_grant_digest=grant.grant_digest,
                confirmed=True)
            with activation_repository.operation_transaction(proof.target.target_identity), \
                    remote.registered_remote_lock():
                entry = remote.get_remote(args.remote)
                if not entry or proof.target.target_identity != hosting.state_key(args.remote, validated) \
                        or proof.target.machine_identity != _authenticated_machine_identity(args.remote):
                    raise ValueError("registered target identity changed")
                configuration_binding_master, _configuration_binding_key_version = \
                    personal_secrets.hosting_binding_key(create=False)
                configuration_binding_key = _host_image_target_configuration_key(
                    configuration_binding_master, proof.target.machine_identity,
                    proof.target.target_identity)
                transport = RegisteredRemoteActivationTransport(
                    argv_runner=_host_image_argv_runner(entry),
                    target_identity_observer=lambda: {
                        "machine_identity": _authenticated_machine_identity(args.remote),
                        "target_identity": hosting.state_key(args.remote, validated)},
                    configuration_binding_key=configuration_binding_key)
                service = ActivationService(
                    repository=activation_repository, runtime_adapter=transport,
                    runtime_observer=RuntimeObserver(transport),
                    edge_adapter=_HostImageEdgeAdapter(
                        validated, activation_repository=activation_repository,
                        target_identity=proof.target.target_identity),
                    rollback_grant_verifier=SshRollbackGrantVerifier(
                        bundle["rollback_grant_public_key"],
                        binding.rollback_grant_authority_id))
                payload = service.execute(
                    request, policy, binding, rollback_subject=subject,
                    rollback_grant=grant, admission_deadline=args.admission_deadline,
                    compose_files=tuple(bundle["compose_files"]),
                    compose_project=bundle["compose_project"],
                    configuration_digest=bundle["configuration_digest"],
                    init_data_contract_digest=bundle["init_data_contract_digest"],
                    edge_required=bundle["edge_required"], ownership_held=True)
    except (OSError, json.JSONDecodeError, TypeError, ValueError, RuntimeError):
        payload = {"schema_version": 1, "ok": False, "result_class": "refused",
                   "code": "policy_mismatch", "operation": action,
                   "request_id": str(getattr(args, "request_id", ""))[:256],
                   "starting_generation": int(args.expected_generation),
                   "resulting_generation": int(args.expected_generation)}
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if payload.get("ok") is not True: raise SystemExit(1)


def cmd_host(cfg, args) -> None:
    if args.action == "stage":
        _cmd_host_stage(args)
        return
    if args.action == "image":
        required = {
            "--project-dir": getattr(args, "project_dir", None),
            "--environment": getattr(args, "environment", None),
            "--remote": getattr(args, "remote", None),
            "--request-id": getattr(args, "request_id", None),
        }
        missing = [name for name, value in required.items()
                   if not isinstance(value, str) or not value.strip()]
        if getattr(args, "image_action", None) not in {
                "activate", "adopt", "rollback", "recover"}:
            missing.append("image action")
        if getattr(args, "expected_generation", None) is None:
            missing.append("--expected-generation")
        if missing:
            die("host image requires explicit " + ", ".join(missing) +
                "; no manifest or state was opened")
    if args.action == "recover":
        missing = []
        if not isinstance(getattr(args, "project_dir", None), str) or not args.project_dir.strip():
            missing.append("--project-dir")
        if not isinstance(getattr(args, "environment", None), str) or not args.environment.strip():
            missing.append("--environment")
        if missing:
            if getattr(args, "json", False):
                _recovery_selector_refusal(args, missing)
            die("host recover requires explicit " + " and ".join(missing) +
                "; no manifest, target, or writer was opened")
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
    if args.action == "image":
        if not args.remote:
            die("--remote is required for host image actions")
        _cmd_host_image(validated, args)
        return
    if args.action == "validate":
        _emit({"ok": True, **validated}, args.json)
        return
    if not args.remote:
        die("--remote is required for host plan, status, diagnose, apply, recover, logs, sync, and login-url")
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
    if args.action == "recover":
        _cmd_host_recover(validated, entry, args.remote, args)
        return
    if args.action == "sync":
        try:
            _with_host_effect_lease(
                validated, args.remote,
                lambda _state: _cmd_host_sync(validated, entry, args.remote, args))
        except TimeoutError:
            die("another host apply or recovery owns this target")
        except hosting.HostingError as exc:
            die(str(exc))
        return
    if args.action == "status":
        result = _host_runtime_status(validated, entry, args.remote, state)
        if args.json:
            print(json.dumps({"ok": True, **result}, sort_keys=True))
        else:
            print(f"{result['project']} / {result['environment']} ({result['remote']})")
            print(f"  deployed revision: {result['deployed_revision'] or 'unknown'}")
            print(f"  health: {result['health']['state']}")
            print(f"  generation: {result['generation']}")
            if result.get("latest_recovery"):
                print(f"  latest recovery: {result['latest_recovery']['result_class']}")
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
            result = _with_host_writer_lock(
                validated, args.remote,
                lambda current, durable_save: _issue_host_autologin(
                    validated, entry, args.remote, current,
                    getattr(args, "ttl_seconds", None),
                    save_state=durable_save))
        except TimeoutError:
            die("another host apply or recovery owns this target")
        except (hosting.HostingError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
            die(str(exc))
        if args.json:
            print(json.dumps({"ok": True, **result}))
        else:
            print(result["url"])
        return
    if args.action == "plan":
        plan = hosting.desired_plan(
            validated, entry.get("origin_ipv4"), entry.get("origin_ipv6"))
        # Planning is read-only. Apply separately rebuilds this entire shape
        # from the entry resolved under registration ownership.
        plan["remote"] = args.remote
        plan["remote_selection"] = "explicit"
        plan["runtime"] = hosting.desired_runtime(validated, args.remote, state)
        plan["runtime"]["records"] = plan["records"]
        _, missing = _secret_status(validated)
        plan["secrets"] = {
            "missing": missing,
            "required": sorted(_declared_secret_sources(validated)),
        }
        plan["basic_auth"] = {
            "enabled": bool(validated.get("basic_auth")),
            "username": (validated.get("basic_auth") or {}).get("username"),
        }
        plan["cloudflare"] = _cloudflare_drift(plan)
        _emit({"ok": True, **plan}, args.json)
        return
    progress = (lambda _message: None) if args.json else (
        lambda message: info(f"host apply: {message}")
    )
    try:
        recovery_repository = RecoveryRepository()
        target_key = hosting.state_key(args.remote, validated)
        with recovery_repository.target_mutation_port(
                "apply").target_mutation_transaction(target_key), \
                recovery_repository.state_lock():
            with remote.registered_remote_lock():
                # Resolve registration only after target ownership. Supported
                # re-registration cannot repoint it before durable authority
                # and effects finish.
                current_entry = remote.get_remote(args.remote)
                if not isinstance(current_entry, dict):
                    raise hosting.HostingError("registered remote changed before host apply")
                current_plan = _guarded_host_apply_plan(
                    validated, current_entry, args.remote,
                    allow_zone_ssl_change=bool(getattr(
                        args, "allow_zone_ssl_change", False)))
                # Apply and recovery share one owner. Reload after acquisition so
                # the generation/receipt view cannot be stale at the first effect.
                state = hosting.load_host_state()
                runtime = hosting.desired_runtime(validated, args.remote, state)
                runtime["records"] = current_plan["records"]
                result = _apply_host(
                    validated, current_entry, args.remote, runtime, state,
                    bool(getattr(args, "allow_zone_ssl_change", False)), branch, progress,
                    recovery_repository=recovery_repository,
                )
    except TimeoutError:
        die("another host apply or recovery owns this target")
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


def _host_predispatch_policy(args) -> bool:
    """Keep observation recovery ahead of compatibility state writers."""
    return getattr(args, "action", None) in {"recover", "stage", "image"}


register_specs((CommandSpec(
    "host", cmd_host, owner=__name__, scope="global", legacy_id="host",
    predispatch_policy=_host_predispatch_policy,
),))
