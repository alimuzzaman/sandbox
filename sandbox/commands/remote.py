from __future__ import annotations
import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.core._paths import RUNTIME_DIR
from sandbox.registry import register
import sandbox.core._remote as sr
import sandbox.core._proplugins as pro
from sandbox.services.redaction import redact_text


# See docs/remote-hosting.md and specs/014-remote-vps-hosting/ for the full design
# this module implements. `./sb remote add/list/provision/up/down/remove` registers
# and manages a remote VPS target that sandbox can provision and deploy to
# (docs/remote-hosting-prd.md §0's resolved design). Machine-level bookkeeping only
# -- which instances a remote actually has live entirely in THAT VPS's own
# independent registry, never here (see _remote.py's module docstring).

_PROVISION_LOG_SCHEMA_VERSION = 1
_PROVISION_LOG_EVENT_LIMIT = 32
_PROVISION_LOG_DETAIL_LIMIT = 1_000
_RUNTIME_SOURCE_PACKAGE_TIMEOUT = 300
_RUNTIME_SOURCE_UPLOAD_TIMEOUT_DEFAULT = 300
_RUNTIME_SOURCE_UPLOAD_TIMEOUT_MAX = 7200
_RUNTIME_SOURCE_ARCHIVE_EXCLUDES = (
    "skills/speckit-prd-refine/SKILL.md",
    "skills/speckit-prd-validate/SKILL.md",
)


_RUNTIME_SOURCE_EXTRACTION_PROGRAM = r'''import os,pathlib,posixpath,sys,tarfile
archive=pathlib.Path(sys.argv[1]); root=pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(archive,'r:gz') as bundle:
 members=bundle.getmembers()
 if len(members)>100000: raise SystemExit(74)
 names={}; links=set()
 for member in members:
  name=pathlib.PurePosixPath(member.name)
  canonical=posixpath.normpath(member.name)
  if name.is_absolute() or '..' in name.parts or canonical in ('','.','..') or canonical.startswith('../') or canonical in names or member.isdev() or member.isfifo(): raise SystemExit(74)
  names[canonical]=member
  if member.issym() or member.islnk(): links.add(canonical)
 for name,member in names.items():
  if any(str(parent) in links for parent in pathlib.PurePosixPath(name).parents): raise SystemExit(74)
  if not (member.issym() or member.islnk()): continue
  raw=pathlib.PurePosixPath(member.linkname)
  target=posixpath.normpath(posixpath.join(posixpath.dirname(name),member.linkname)) if member.issym() else posixpath.normpath(member.linkname)
  if raw.is_absolute() or target in ('','..') or target.startswith('../'): raise SystemExit(74)
  target_path=pathlib.PurePosixPath(target)
  if target in links or any(str(parent) in links for parent in target_path.parents): raise SystemExit(74)
  if member.islnk():
   target_member=names.get(target)
   if target_member is None or not target_member.isfile() or target_member.issym() or target_member.islnk(): raise SystemExit(74)
   member.linkname=target
 bundle.extractall(root)
for path in sorted(root.rglob('*')):
 if path.is_symlink(): continue
 fd=os.open(path,os.O_RDONLY|(os.O_DIRECTORY if path.is_dir() else 0))
 try: os.fsync(fd)
 finally: os.close(fd)
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)'''


class RemoteRuntimeSourceTimeout(RuntimeError):
    """The bounded runtime-source package or upload did not finish."""

    code = "remote_runtime_source_timeout"


class RemoteRuntimeSourceIndeterminate(RuntimeError):
    """A staged source may have published without durable cleanup proof."""

    code = "remote_runtime_source_indeterminate"


_RUNTIME_SOURCE_PUBLISH_PROGRAM = r'''import ctypes,errno,os,stat,sys
source,target=sys.argv[1:]; parent=os.path.dirname(target); libc=ctypes.CDLL(None,use_errno=True)
sfd=os.open(source,os.O_RDONLY|os.O_DIRECTORY); identity=os.fstat(sfd); os.fsync(sfd); os.close(sfd)
if libc.renameat2(-100,os.fsencode(source),-100,os.fsencode(target),1)!=0:
 error=ctypes.get_errno(); raise OSError(error,os.strerror(error),target)
try:
 pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(pfd)
 finally: os.close(pfd)
except BaseException:
 try:
  current=os.lstat(target)
  if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino) or current.st_uid!=os.geteuid() or stat.S_IMODE(current.st_mode)!=0o700: raise SystemExit(75)
  if libc.renameat2(-100,os.fsencode(target),-100,os.fsencode(source),1)!=0: raise SystemExit(75)
  pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
  try: os.fsync(pfd)
  finally: os.close(pfd)
 except SystemExit: raise
 except BaseException: raise SystemExit(75)
 raise SystemExit(74)'''


_RUNTIME_SOURCE_CLEANUP_PROGRAM = r'''import os,shutil,stat,sys
source,stage,expected=sys.argv[1:]
try:
 dev,ino=(int(value) for value in expected.split(':',1))
 current=os.lstat(source)
 if os.path.lexists(stage) or not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=(dev,ino) or current.st_uid!=os.geteuid() or stat.S_IMODE(current.st_mode)!=0o700: raise SystemExit(75)
 shutil.rmtree(source)
 if os.path.lexists(source): raise SystemExit(75)
 fd=os.open(os.path.dirname(source),os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(fd)
 finally: os.close(fd)
except SystemExit: raise
except BaseException: raise SystemExit(75)'''


def _local_git_revision() -> str:
    """Resolve the controller checkout SHA before a remote bootstrap.

    Runtime source archives intentionally exclude ``.git``. Pass the exact
    controller identity to the bootstrap so helper manifests remain bound to
    the uploaded source instead of assuming the remote archive is a checkout.
    """
    clean_env = {key: value for key, value in os.environ.items()
                 if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ("git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"),
            capture_output=True, text=True, timeout=10, check=False,
            env=clean_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not resolve the local Sandbox revision") from exc
    raw = result.stdout
    revision = raw.decode(errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("local Sandbox revision is unavailable or invalid")
    return revision


def _provision_log_root(name: str) -> Path:
    """Return the owner-only per-user journal directory for one safe remote name."""
    return Path(RUNTIME_DIR) / "remote-provision" / sr.validate_remote_name(name)


def _provision_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_provision_log(log: dict) -> None:
    """Atomically persist bounded, secret-redacted provision milestones."""
    name = sr.validate_remote_name(str(log["remote"]))
    log_id = str(log["log_id"])
    if not re.fullmatch(r"[a-f0-9]{32}", log_id):
        raise ValueError("provision log id is invalid")
    root = _provision_log_root(name)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    target = root / f"{log_id}.json"
    fd, temporary = tempfile.mkstemp(prefix=f".{log_id}.", suffix=".tmp", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(log, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_provision_event(log: dict, stage: str, *, status: str | None = None,
                            detail: str | None = None) -> None:
    """Append one durable, secret-safe milestone before returning control."""
    if not isinstance(stage, str) or not stage:
        raise ValueError("provision log stage is invalid")
    if status is not None:
        log["status"] = status
    event = {"at": _provision_now(), "stage": stage}
    if detail:
        event["detail"] = redact_text(detail)[:_PROVISION_LOG_DETAIL_LIMIT]
    events = log.setdefault("events", [])
    if not isinstance(events, list):
        raise ValueError("provision log events are invalid")
    events.append(event)
    del events[:-_PROVISION_LOG_EVENT_LIMIT]
    log["updated_at"] = event["at"]
    _write_provision_log(log)


def _new_provision_log(name: str, control_transport: str) -> dict:
    now = _provision_now()
    log = {
        "schema_version": _PROVISION_LOG_SCHEMA_VERSION,
        "log_id": uuid.uuid4().hex,
        "remote": sr.validate_remote_name(name),
        "control_transport": control_transport,
        "status": "in_progress",
        "started_at": now,
        "updated_at": now,
        "events": [],
    }
    _record_provision_event(log, "started")
    return log


def _provision_log_summary(log: dict | None) -> dict | None:
    """Return only public recovery metadata; journal events stay machine-local."""
    if not isinstance(log, dict):
        return None
    log_id = log.get("log_id")
    status = log.get("status")
    if (not isinstance(log_id, str) or not re.fullmatch(r"[a-f0-9]{32}", log_id)
            or status not in {"in_progress", "complete", "failed", "interrupted"}):
        return None
    return {"log_id": log_id, "status": status, "updated_at": log.get("updated_at")}


def _latest_provision_log(name: str) -> dict | None:
    root = _provision_log_root(name)
    try:
        candidates = sorted(root.glob("[0-9a-f]" * 32 + ".json"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return None
    if not candidates:
        return None
    try:
        value = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def cmd_remote(cfg, args) -> None:
    """`./sb remote <add|list|provision|up|down|remove|set-origin|docker-pool|domains> [name] [ssh_url] [--json]`
    -- register and manage remote VPS targets. See docs/remote-hosting.md."""
    action = args.action
    as_json = bool(getattr(args, "json", False))
    dispatch = {
        "add": _cmd_add,
        "list": _cmd_list,
        "provision": _cmd_provision,
        "up": _cmd_up,
        "down": _cmd_down,
        "remove": _cmd_remove,
        "set-origin": _cmd_set_origin,
        "docker-pool": _cmd_docker_pool,
        "domains": _cmd_domains,
        "service": _cmd_service,
        "plugins": _cmd_plugins,
        "ssh": _cmd_ssh,
    }
    dispatch[action](args, as_json)


def _cmd_ssh(args, as_json: bool) -> None:
    """Explicit operator escape hatch. Internal workflows must never call it."""
    name = _require_name(args)
    command = _arg_str(args, "command")
    if not command:
        die("usage: ./sb remote ssh <name> --command <command>")
    if not _arg_true(args, "confirm"):
        die("direct SSH requires --confirm")
    reason = _arg_str(args, "reason")
    if not reason:
        die("direct SSH requires --reason <text>")
    if len(command) > 4096 or "\x00" in command:
        die("direct SSH command is invalid or too long")
    if len(reason) > 256 or "\x00" in reason:
        die("direct SSH reason is invalid or too long")
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        result = sr.ssh_process(entry, command, timeout=3600)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        die(f"direct SSH command failed: {sr.redact_ssh_connection(str(exc), entry)}")
    stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout or "")
    stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr or "")
    if as_json:
        print(json.dumps({"ok": result.returncode == 0, "name": name,
                          "exit_code": int(result.returncode),
                          "stdout": stdout, "stderr": stderr}))
    else:
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
    if result.returncode != 0:
        raise SystemExit(int(result.returncode))


def _cmd_plugins(args, as_json: bool) -> None:
    """`./sb remote plugins <name> [--force] [--dry-run]` -- mirror the local
    pro-plugin store onto the host and register its slugs in the REMOTE
    user-global catalog, so every instance there offers them on demand."""
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        data = pro.sync(entry, name, cfg=load_config(),
                        force=bool(getattr(args, "force", False)),
                        dry_run=bool(getattr(args, "dry_run", False)))
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        data = {"ok": False, "error": sr.redact_ssh_connection(str(exc), entry)}
    if as_json:
        print(json.dumps(data, sort_keys=True))
        if not data.get("ok"):
            raise SystemExit(1)
        return
    if not data.get("ok"):
        die(data.get("error", "pro-plugin mirror failed"))
    if data.get("skipped") == "no_local_store":
        print("no local pro-plugin store — set `defaults.pro_plugins_home` to enable")
        return
    if data.get("skipped") == "empty_store":
        print(f"no plugin directories in {data['store']} — nothing to mirror")
        return
    if data.get("skipped") == "dry_run":
        verb = "would push" if data.get("would_push") else "unchanged, would skip"
        print(f"{verb}: {len(data['slugs'])} plugin(s), "
              f"{data['bytes'] // (1024 * 1024)} MiB -> {data['remote_store']}")
        print("  " + ", ".join(data["slugs"]))
        return
    if data.get("skipped") == "unchanged":
        ok(f"{name} already mirrors this store ({len(data['slugs'])} plugin(s))")
        return
    ok(f"mirrored {len(data['slugs'])} pro plugin(s) to {data['remote_store']}")
    print(f"  catalog: {data.get('catalog')}")
    print("  on-demand: " + ", ".join(data.get("registered") or []))
    if data.get("unregistered"):
        print("  removed: " + ", ".join(data["unregistered"]))
    if data.get("conflicts"):
        print("  left alone (host configures these itself): "
              + ", ".join(data["conflicts"]))


def _cmd_domains(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        data = sr.remote_domain_inventory(entry)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        data = {"ok": False, "code": "remote_domain_inventory_failed",
                "message": sr.redact_ssh_connection(str(exc), entry)}
    if as_json:
        print(json.dumps(data, sort_keys=True))
        if not data.get("ok"): raise SystemExit(1)
        return
    if not data.get("ok"):
        die(data.get("message", "remote domain inventory failed"))
    for item in data["domains"]:
        owners = ", ".join(item["owners"]) or "unattributed"
        print(f"{item['domain']}  ({owners})")


def _cmd_docker_pool(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        data = sr.remote_docker_pool(
            entry, confirm=_arg_true(args, "confirm"),
            recover_interrupted=_arg_true(args, "recover_interrupted"),
            expected_running=getattr(args, "expected_running", None),
            expected_removed=getattr(args, "expected_removed", 0),
            recovery_since=getattr(args, "recovery_since", None))
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        data = {"ok": False, "code": "docker_pool_unavailable",
                "message": sr.redact_ssh_connection(str(exc), entry)}
    payload = {
        "ok": data.get("ok") is True,
        "name": name,
        "status": data.get("status", "failed"),
        "data": data,
        "error": None if data.get("ok") is True else {
            "code": data.get("code", "docker_pool_failed"),
            "message": data.get("message", "remote Docker pool operation failed"),
        },
    }
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        if not payload["ok"]:
            raise SystemExit(1)
        return
    if payload["ok"]:
        if data.get("requires_confirm"):
            print(f"'{name}' Docker pool update is planned; re-run with --confirm")
        else:
            ok(f"'{name}' Docker address pools: {data.get('status')}")
        return
    die(f"{payload['error']['code']}: {payload['error']['message']}")


def _cmd_service(args, as_json: bool) -> None:
    """`sb remote service <status|diagnostics|migrate|stop> <name>` service contract."""
    operation = getattr(args, "name", None)
    name = getattr(args, "ssh_url", None)
    # Keep the common read-only probe ergonomic: when ``service`` receives a
    # single positional remote name, treat it as ``service status <name>``.
    # Mutating and diagnostic operations still require their explicit verb so
    # an incomplete command cannot silently change remote state.
    if name is None and operation not in {None, "status", "diagnostics", "migrate", "stop", "capability"}:
        name = operation
        operation = "status"
    if operation not in {"status", "diagnostics", "migrate", "stop", "capability"} or not name:
        die("usage: ./sb remote service <status|diagnostics|migrate|stop|capability> <name> [--plan|--confirm]")
    if operation == "diagnostics" and _arg_true(args, "ssh"):
        die("--ssh diagnostics are no longer supported; use the authenticated remote service")
    if _arg_true(args, "processes") and operation != "diagnostics":
        die("--processes requires `remote service diagnostics <name>`")
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    confirmed = _arg_true(args, "confirm")
    try:
        if operation == "capability":
            from sandbox.owned_storage_lifecycle.service import build_authority_lifecycle_service
            lifecycle_service = build_authority_lifecycle_service()
            cap = lifecycle_service.evaluate_capability(remote_identity=name)
            payload = {"ok": True, "name": name, "status": "observed",
                       "data": cap, "error": None}
        elif operation == "status":
            payload = {"ok": True, "name": name, "status": "observed",
                       "data": sr.remote_mcp_service_status(entry), "error": None}
        elif operation == "diagnostics":
            diagnostics = sr.remote_diagnostics(
                entry, include_processes=_arg_true(args, "processes")
            )
            payload = {"ok": True, "name": name, "status": "observed",
                       "data": diagnostics, "error": None}
        elif operation == "migrate":
            transport = entry.get("control_transport") or "https"
            bind = (entry.get("tailscale_host") if transport == "tailscale" else "127.0.0.1")
            if not isinstance(bind, str) or not bind:
                raise ValueError("remote service bind is unavailable; provision its control transport first")
            token = entry.get("bearer_token")
            if not isinstance(token, str) or not token:
                raise ValueError("remote service token is unavailable; provision the remote first")
            # Tailscale is a direct private bind, not a public reverse-proxy
            # origin. Passing its control URL as ``public_url`` changes the
            # service ownership marker and makes a valid existing unit look
            # foreign during a routine migration.
            public_url = entry.get("control_url") if transport == "https" else None
            observed = sr.remote_mcp_service_status(entry)
            source_revision = None
            if confirmed:
                source_revision = _local_git_revision()
                upload_timeout = _runtime_source_upload_timeout_arg(args)
                staged_source = _upload_runtime_source(
                    entry["ssh"], source_revision=source_revision,
                    upload_timeout=upload_timeout
                )
                _assert_clean_source_revision(source_revision)
            plan = sr.migrate_remote_mcp_service(
                entry, bind, int(entry.get("mcp_port") or sr.DEFAULT_MCP_PORT), token,
                public_url, confirm=confirmed,
                legacy_pidfile=observed.get("legacy_pidfile") == "present",
                source_revision=source_revision,
                staged_source=staged_source if confirmed else None,
            )
            plan["observed"] = observed
            plan["legacy_pidfile_detected"] = observed.get("legacy_pidfile") == "present"
            if confirmed:
                sr.put_remote(name, mcp_service=plan["service"])
            payload = {"ok": True, "name": name, "status": plan["status"], "data": plan, "error": None}
        else:
            if not confirmed:
                payload = {"ok": True, "name": name, "status": "planned",
                           "data": {"requires_confirm": True, "action": "stop"}, "error": None}
            else:
                sr.stop_remote_mcp_server(entry)
                payload = {"ok": True, "name": name, "status": "stopped", "data": {}, "error": None}
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        if isinstance(exc, (RemoteRuntimeSourceTimeout, RemoteRuntimeSourceIndeterminate)):
            code = exc.code
        else:
            code = (str(exc) if str(exc) in {
                "remote_service_ownership_unknown", "remote_service_rollback_indeterminate"}
                else "remote_service_failed")
        payload = {"ok": False, "name": name, "status": "degraded", "data": {},
                   "error": {"code": code, "message": sr.redact_ssh_connection(str(exc), entry)}}
    if as_json:
        print(json.dumps(payload))
    elif payload["ok"]:
        print(f"remote service {name}: {payload['status']}")
    else:
        die(f"{payload['error']['code']}: {payload['error']['message']}")


def _require_name(args) -> str:
    name = getattr(args, "name", None)
    if not name:
        die("a remote name is required for this action, e.g. "
            "`./sb remote add myvps <ssh-connection>`")
    return name


def _cmd_add(args, as_json: bool) -> None:
    name = _require_name(args)
    ssh_url = getattr(args, "ssh_url", None)
    if not ssh_url:
        die("`./sb remote add <name> <ssh_url>` requires an ssh_url, "
            "e.g. `./sb remote add myvps <ssh-connection>`")
    try:
        name = sr.validate_remote_name(name)
    except ValueError as e:
        die(str(e))
    try:
        ssh_target = sr.remote_ssh_parts(ssh_url)["target"]
        port = sr.remote_ssh_parts(ssh_url)["port"]
    except ValueError as e:
        die(str(e))
    if port:
        ssh_target = f"{ssh_target}:{port}"
    entry = sr.put_remote(name, ssh=ssh_target, provisioned=False)
    result = {"ok": True, "name": name, "ssh_configured": bool(entry.get("ssh")), "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"registered remote '{name}'")
        print("  next: ./sb remote provision " + name)


def _provider_label(entry: dict) -> str:
    """Return only provider metadata safe for public CLI output."""
    provider = entry.get("provider")
    if not isinstance(provider, str):
        return "unknown"
    try:
        return sr.validate_remote_name(provider)
    except ValueError:
        return "unknown"


def _cmd_list(args, as_json: bool) -> None:
    remotes = sr.list_remotes()
    rows = []
    for name, entry in sorted(remotes.items()):
        reachability = sr.check_reachable_diagnostic(entry)
        rows.append({
            "name": name,
            "ssh_configured": bool(entry.get("ssh")),
            "reachable": bool(reachability.get("reachable")),
            "reachability": {
                "state": reachability.get("state", "probe_unavailable"),
                "latency_ms": reachability.get("latency_ms"),
            },
            "provisioned": bool(entry.get("provisioned")),
            "provider": _provider_label(entry),
        })
    if as_json:
        print(json.dumps({"ok": True, "remotes": rows, "error": None}))
        return
    if not rows:
        info("no remotes registered — add one with `./sb remote add <name> <ssh_url>`")
        return
    for r in rows:
        reach = "reachable" if r["reachable"] else "unreachable"
        prov = "provisioned" if r["provisioned"] else "not provisioned"
        state = r["reachability"]["state"]
        print(f"  {r['name']}  {reach} ({state}), {prov}, provider {r['provider']}")


def _cmd_set_origin(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    ipv4 = getattr(args, "ipv4", None)
    ipv6 = getattr(args, "ipv6", None)
    if not ipv4 and not ipv6:
        die("remote set-origin requires --ipv4 and/or --ipv6")
    entry = sr.put_remote(name, origin_ipv4=ipv4, origin_ipv6=ipv6)
    result = {"ok": True, "name": name, "origin_ipv4_configured": bool(entry.get("origin_ipv4")),
              "origin_ipv6_configured": bool(entry.get("origin_ipv6")), "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"stored public origin address for '{name}'")


def _cmd_remove(args, as_json: bool) -> None:
    name = _require_name(args)
    existed = sr.remove_remote(name)
    result = {"ok": True, "name": name, "removed": existed, "error": None}
    if as_json:
        print(json.dumps(result))
        return
    if existed:
        ok(f"forgot remote '{name}' locally — any instance already running on "
           f"that VPS is UNAFFECTED; tear it down there yourself if you no "
           f"longer need it")
    else:
        info(f"no remote named '{name}' was registered")


def _normalize_runtime_source_upload_timeout(value: object) -> int:
    if (type(value) is not int
            or not 1 <= value <= _RUNTIME_SOURCE_UPLOAD_TIMEOUT_MAX):
        raise ValueError(
            "runtime source upload timeout must be an integer from 1 to "
            f"{_RUNTIME_SOURCE_UPLOAD_TIMEOUT_MAX} seconds"
        )
    return value


def _runtime_source_upload_timeout_arg(args) -> int:
    values = vars(args) if hasattr(args, "__dict__") else {}
    value = values.get(
        "upload_timeout", _RUNTIME_SOURCE_UPLOAD_TIMEOUT_DEFAULT
    )
    return _normalize_runtime_source_upload_timeout(value)


def _assert_clean_source_revision(source_revision: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise RuntimeError("local Sandbox revision is unavailable or invalid")
    clean_env = {key: value for key, value in os.environ.items()
                 if not key.startswith("GIT_")}
    for argv, dirty_check in (
            (("git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"), False),
            (("git", "-C", str(ROOT), "status", "--porcelain=v1", "-z",
              "--untracked-files=all"), True)):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=10, check=False,
                                    env=clean_env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("could not verify the local Sandbox source") from exc
        output = result.stdout or b""
        if isinstance(output, str):
            output = output.encode()
        if result.returncode != 0:
            raise RuntimeError("could not verify the local Sandbox source")
        if dirty_check and output:
            raise RuntimeError("local Sandbox source is dirty; refusing remote runtime upload")
        if not dirty_check and output.decode(errors="replace").strip() != source_revision:
            raise RuntimeError("local Sandbox revision changed during remote runtime upload")


def _read_exact_source_file(source_revision: str, relative_path: str) -> bytes:
    if (re.fullmatch(r"[0-9a-f]{40}", source_revision) is None
            or relative_path not in {"scripts/install-remote.sh"}):
        raise RuntimeError("exact Sandbox source file identity is invalid")
    clean_env = {key: value for key, value in os.environ.items()
                 if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ("git", "-C", str(ROOT), "show", f"{source_revision}:{relative_path}"),
            capture_output=True, timeout=10, check=False, env=clean_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not read the exact Sandbox source file") from exc
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("could not read the exact Sandbox source file")
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


def _upload_runtime_source(
        ssh_target: str, *, source_revision: str,
        upload_timeout: int = _RUNTIME_SOURCE_UPLOAD_TIMEOUT_DEFAULT,
) -> dict[str, str]:
    """Stage one clean exact Git object without reading mutable worktree bytes."""
    upload_timeout = _normalize_runtime_source_upload_timeout(upload_timeout)
    _assert_clean_source_revision(source_revision)
    runtime_revision = sr._remote_mcp_runtime_revision()
    if re.fullmatch(r"[0-9a-f]{24}", runtime_revision) is None:
        raise RuntimeError("local Sandbox runtime record revision is invalid")
    archive_cmd = (
        "git", "-C", str(ROOT), "archive", "--format=tar.gz", source_revision,
        "--", ".",
        *(f":(exclude,top,literal){path}"
          for path in _RUNTIME_SOURCE_ARCHIVE_EXCLUDES),
    )
    print(
        f"staging exact Sandbox runtime source {source_revision} archive "
        f"(bounded {_RUNTIME_SOURCE_PACKAGE_TIMEOUT}s)...",
        file=sys.stderr,
    )
    try:
        archive_res = subprocess.run(
            archive_cmd, capture_output=True,
            timeout=_RUNTIME_SOURCE_PACKAGE_TIMEOUT, check=False,
            env={key: value for key, value in os.environ.items()
                 if not key.startswith("GIT_")},
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteRuntimeSourceTimeout(
            "runtime source packaging timed out after "
            f"{_RUNTIME_SOURCE_PACKAGE_TIMEOUT}s; the remote was not contacted"
        ) from exc
    if archive_res.returncode != 0:
        raise RuntimeError(
            f"could not package the local sandbox runtime: "
            f"{archive_res.stderr.decode(errors='replace').strip()[:500]}"
        )
    archive = archive_res.stdout or b""
    # Recheck after packaging and before first remote contact. The archive is
    # already bound to the immutable object, while this guard rejects a dirty
    # or moved controller checkout rather than mislabelling operator intent.
    _assert_clean_source_revision(source_revision)
    archive_digest = hashlib.sha256(archive).hexdigest()
    stage_name = f".sb-src-stage-{source_revision[:12]}-{uuid.uuid4().hex}"
    stage_receipt = json.dumps({"schema_version": 1, "source_revision": source_revision,
                                "runtime_revision": runtime_revision,
                                "archive_digest": f"sha256:{archive_digest}"},
                               sort_keys=True, separators=(",", ":"))
    remote_cmd = (
        "set -eu; umask 077; phase=preflight; temporary=; stage=; publish_identity=; "
        "finish() { rc=$?; trap - EXIT; "
        "if [ \"$phase\" = publish ] && [ \"$rc\" -eq 75 ]; then :; "
        "elif [ \"$phase\" = publish ] && [ \"$rc\" -eq 74 ]; then "
        "if python3 -c " + shlex.quote(_RUNTIME_SOURCE_CLEANUP_PROGRAM) + " \"$temporary\" \"$stage\" \"$publish_identity\"; then temporary=; else rc=75; fi; "
        "elif [ -n \"$temporary\" ]; then rm -rf -- \"$temporary\" || :; fi; "
        "if [ \"$rc\" -ne 0 ]; then printf 'SB_RUNTIME_UPLOAD_ERROR phase=%s code=%s\\n' \"$phase\" \"$rc\" >&2; fi; "
        "exit \"$rc\"; }; trap finish EXIT; "
        "sandbox_home=${SANDBOX_HOME:-$HOME/sandbox}; "
        "mkdir -p \"$sandbox_home\"; test -d \"$sandbox_home\"; test ! -L \"$sandbox_home\"; "
        "python3 -c 'import os,stat,sys; i=os.lstat(sys.argv[1]); "
        "raise SystemExit(0 if stat.S_ISDIR(i.st_mode) and i.st_uid==os.geteuid() "
        "and not stat.S_IMODE(i.st_mode)&2 else 69)' \"$sandbox_home\"; "
        f"stage_name={shlex.quote(stage_name)}; expected_digest={archive_digest}; "
        "stage=$sandbox_home/$stage_name; test ! -e \"$stage\"; "
        "temporary=$(mktemp -d \"$sandbox_home/.sb-src-stage-tmp.XXXXXX\"); phase=receive; "
        "archive=$temporary/archive.tar.gz; cat > \"$archive\"; "
        "phase=digest; "
        "actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],\"rb\").read()).hexdigest())' \"$archive\"); "
        "test \"$actual\" = \"$expected_digest\"; mkdir -m 0700 \"$temporary/tree\"; "
        "phase=extract; "
        "python3 -c " + shlex.quote(_RUNTIME_SOURCE_EXTRACTION_PROGRAM) + " \"$archive\" \"$temporary/tree\"; "
        "phase=validate; "
        "rm -f -- \"$archive\"; test -f \"$temporary/tree/sb\"; "
        "test ! -L \"$temporary/tree/sb\"; "
        "test -f \"$temporary/tree/sandbox/hosting/images/staging_helper.py\"; "
        "phase=receipt; printf '%s' " + shlex.quote(stage_receipt) + " > \"$temporary/receipt.json\"; "
        "chmod 0600 \"$temporary/receipt.json\"; "
        "python3 -c 'import os,sys; fd=os.open(sys.argv[1],os.O_RDONLY); os.fsync(fd); os.close(fd)' "
        "\"$temporary/receipt.json\"; "
        "phase=publish; publish_identity=$(python3 -c 'import os,sys; i=os.lstat(sys.argv[1]); print(f\"{i.st_dev}:{i.st_ino}\")' \"$temporary\"); "
        "python3 -c " + shlex.quote(_RUNTIME_SOURCE_PUBLISH_PROGRAM) + " \"$temporary\" \"$stage\"; "
        "temporary=; phase=complete; trap - EXIT; "
    )
    print(
        "runtime source archive ready "
        f"({len(archive)} bytes); uploading to remote "
        f"(bounded {upload_timeout}s)...",
        file=sys.stderr,
    )
    try:
        ssh_res = sr.ssh_process(
            ssh_target, remote_cmd, input_data=archive,
            timeout=upload_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteRuntimeSourceTimeout(
            "runtime source upload timed out after "
            f"{upload_timeout}s; remote completion is unknown; "
            "inspect the remote before retrying"
        ) from exc
    if ssh_res.returncode != 0:
        detail = (ssh_res.stderr or ssh_res.stdout or b"").decode(errors="replace")
        marker = re.search(
            r"SB_RUNTIME_UPLOAD_ERROR phase=(preflight|receive|digest|extract|validate|receipt|publish) code=([0-9]{1,3})(?:\n|$)",
            detail[:2000],
        )
        phase = marker.group(1) if marker else "remote"
        code = marker.group(2) if marker else str(max(1, min(255, abs(ssh_res.returncode))))
        if phase == "publish" and code == "75":
            raise RemoteRuntimeSourceIndeterminate(
                "runtime source publication cleanup is indeterminate; "
                f"stage_id={stage_name}; inspect the remote before retrying"
            )
        raise RuntimeError(
            f"could not upload sandbox runtime: phase={phase} code={code}"
        )
    return {"stage_name": stage_name, "source_revision": source_revision,
            "runtime_revision": runtime_revision,
            "archive_digest": f"sha256:{archive_digest}", "receipt": stage_receipt}


def _arg_str(args, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) and value.strip() else None


def _arg_true(args, name: str) -> bool:
    return getattr(args, name, None) is True


def _choose_control_transport(args, as_json: bool) -> str:
    explicit = _arg_str(args, "control")
    if explicit in {"https", "tailscale"}:
        return explicit
    if as_json or _arg_true(args, "yes") or not sys.stdin.isatty():
        return "https"
    ans = input("Use Tailscale private control plane instead of public HTTPS? [y/N] ")
    return "tailscale" if ans.strip().lower() in {"y", "yes"} else "https"


def _ssh_host(ssh_target: str) -> str:
    return sr.ssh_host(ssh_target)


def _control_host(args, entry: dict, ssh_target: str, as_json: bool) -> str:
    host = _arg_str(args, "control_host") or entry.get("control_host")
    if isinstance(host, str) and host.strip():
        return host.strip()
    if as_json or _arg_true(args, "yes") or not sys.stdin.isatty():
        die("public HTTPS control requires --control-host, e.g. "
            "`./sb remote provision myvps --control-host sandbox.example.com`")
    default = _ssh_host(ssh_target)
    prompt = f"Public HTTPS hostname for this remote [{default}]: "
    entered = input(prompt).strip()
    return entered or default


def _cmd_provision(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}' — register it first with "
            f"`./sb remote add {name} <ssh-connection>`")
    ssh_target = entry.get("ssh") or ""
    if not ssh_target:
        die(f"remote '{name}' has no ssh connection string configured")
    control_transport = _choose_control_transport(args, as_json)
    public_host = None
    if control_transport == "https":
        public_host = _control_host(args, entry, ssh_target, as_json)
    if not _arg_true(args, "confirm"):
        prior = _provision_log_summary(_latest_provision_log(name))
        result = {
            "ok": True, "name": name, "status": "planned", "provisioned": bool(entry.get("provisioned")),
            "control_transport": control_transport, "control_host": public_host,
            "data": {"requires_confirm": True, "action": "provision"}, "error": None,
        }
        if prior is not None:
            result["previous_provision_log"] = prior
        if as_json:
            print(json.dumps(result))
        else:
            print(f"'{name}' provisioning is planned; re-run with --confirm to install and start its MCP service")
        return
    try:
        upload_timeout = _runtime_source_upload_timeout_arg(args)
    except ValueError as exc:
        die(str(exc))
    source_revision = _local_git_revision()
    _assert_clean_source_revision(source_revision)
    script = _read_exact_source_file(source_revision, "scripts/install-remote.sh")
    # bash -s reads the script from stdin; ssh_run's helper only runs a single
    # command string (no stdin piping), so transfer the exact committed script
    # inline as base64 over the SSH argument to avoid quoting issues.
    import base64
    encoded = base64.b64encode(script).decode()
    journal = _new_provision_log(name, control_transport)
    try:
        _record_provision_event(journal, "runtime_staging")
        staged_source = _upload_runtime_source(
            ssh_target, source_revision=source_revision, upload_timeout=upload_timeout)
        _record_provision_event(journal, "runtime_staged")
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        _record_provision_event(journal, "runtime_staging_failed", status="failed", detail=str(e))
        error_code = (f"{e.code}: " if isinstance(
            e, RemoteRuntimeSourceIndeterminate) else "")
        die(f"{error_code}could not stage the sandbox runtime on '{name}': "
            f"{sr.redact_ssh_connection(str(e), entry)}")
    cmd = (
        f"echo {encoded} | base64 -d | "
        f"SANDBOX_CONTROL_TRANSPORT={shlex.quote(control_transport)} "
        "SANDBOX_DEFER_RUNTIME_ACTIVATION=1 "
        f"SANDBOX_RUNTIME_REVISION={shlex.quote(source_revision)} bash -s"
    )
    try:
        _record_provision_event(journal, "bootstrap_running")
        res = subprocess.run(
            sr.ssh_command_args(ssh_target, cmd),
            capture_output=True, text=True, timeout=1800, check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        _record_provision_event(journal, "bootstrap_interrupted", status="interrupted", detail=str(e))
        die(f"provisioning '{name}' failed: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
    if res.returncode != 0:
        detail = sr.redact_ssh_connection(
            (res.stderr or res.stdout or "").strip()[:1000], entry
        )
        _record_provision_event(journal, "bootstrap_failed", status="failed", detail=detail)
        die(f"provisioning '{name}' failed: {detail}")
    _record_provision_event(journal, "bootstrap_complete")
    token = sr.mint_bearer_token()
    port = sr.DEFAULT_MCP_PORT
    entry = sr.get_remote(name)
    try:
        _record_provision_event(journal, "control_service_starting")
        if control_transport == "tailscale":
            tailscale_ip = sr.resolve_tailscale_ip(entry)
            control_url = f"http://{tailscale_ip}:{port}"
            bind = tailscale_ip
            _assert_clean_source_revision(source_revision)
            applied = sr.start_remote_mcp_server(
                entry, bind, port, token, source_revision=source_revision,
                staged_source=staged_source)
            sr.put_remote(name, control_transport="tailscale",
                          control_url=control_url, tailscale_host=tailscale_ip,
                          mcp_port=port, bearer_token=token, provisioned=True,
                          capabilities=["job.exec", "job.execution-policy.v1"],
                          mcp_service=applied["service"])
        else:
            control_url = f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            _assert_clean_source_revision(source_revision)
            applied = sr.start_remote_mcp_server(
                entry, "127.0.0.1", port, token, public_url=control_url,
                source_revision=source_revision, staged_source=staged_source)
            sr.put_remote(name, control_transport="https",
                          control_host=public_host, control_url=control_url,
                          mcp_port=port, bearer_token=token, provisioned=True,
                          capabilities=["job.exec", "job.execution-policy.v1"],
                          mcp_service=applied["service"])
            tailscale_ip = None
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        _record_provision_event(journal, "control_service_failed", status="failed", detail=str(e))
        die(f"could not start the remote MCP server on '{name}': "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    # The credential remains in the owner-only local secret store and is sent
    # to the remote only on stdin while its service credential file is created.
    # Do not return or print it: JSON output, terminals, and shell history are
    # all inappropriate credential transports.
    _record_provision_event(journal, "complete", status="complete")
    result = {"ok": True, "name": name, "provisioned": True,
             "control_transport": control_transport, "control_url": control_url,
             "tailscale_host": tailscale_ip, "mcp_port": port,
             "provision_log": _provision_log_summary(journal), "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' provisioned and its MCP server is reachable at {control_url}")
        print("  credential retained in the owner-only Sandbox secret store; "
              "see docs/remote-hosting.md for client configuration")


def _cmd_up(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry or not entry.get("provisioned"):
        die(f"remote '{name}' is not provisioned yet — run "
            f"`./sb remote provision {name}` first")
    control_transport = entry.get("control_transport") or (
        "tailscale" if entry.get("tailscale_host") else "https"
    )
    control_url = entry.get("control_url")
    port = entry.get("mcp_port") or sr.DEFAULT_MCP_PORT
    token = entry.get("bearer_token")
    if not token:
        die(f"remote '{name}' is missing recorded connection details — "
            f"re-run `./sb remote provision {name}`")
    if not _arg_true(args, "confirm"):
        result = {"ok": True, "name": name, "status": "planned",
                  "data": {"requires_confirm": True, "action": "start"}, "error": None}
        if as_json:
            print(json.dumps(result))
        else:
            print(f"'{name}' MCP service start is planned; re-run with --confirm")
        return
    try:
        upload_timeout = _runtime_source_upload_timeout_arg(args)
        source_revision = _local_git_revision()
        staged_source = _upload_runtime_source(
            entry["ssh"], source_revision=source_revision, upload_timeout=upload_timeout)
        observed = sr.remote_mcp_service_status(entry)
        if control_transport == "tailscale":
            tailscale_ip = entry.get("tailscale_host") or sr.resolve_tailscale_ip(entry)
            control_url = control_url or f"http://{tailscale_ip}:{port}"
            _assert_clean_source_revision(source_revision)
            plan = sr.migrate_remote_mcp_service(
                entry, tailscale_ip, int(port), token, confirm=True,
                legacy_pidfile=observed.get("legacy_pidfile") == "present",
                source_revision=source_revision,
                staged_source=staged_source,
            )
            sr.put_remote(name, mcp_service=plan["service"])
        else:
            public_host = entry.get("control_host")
            if not public_host:
                die(f"remote '{name}' is missing its HTTPS control host — "
                    f"re-run `./sb remote provision {name} --control-host <host>`")
            control_url = control_url or f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            _assert_clean_source_revision(source_revision)
            plan = sr.migrate_remote_mcp_service(
                entry, "127.0.0.1", int(port), token, public_url=control_url,
                confirm=True, legacy_pidfile=observed.get("legacy_pidfile") == "present",
                source_revision=source_revision,
                staged_source=staged_source,
            )
            sr.put_remote(name, mcp_service=plan["service"])
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        error_code = (f"{e.code}: " if isinstance(
            e, RemoteRuntimeSourceIndeterminate) else "")
        die(f"{error_code}could not start '{name}''s MCP server: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    result = {"ok": True, "name": name, "control_transport": control_transport,
             "control_url": control_url, "mcp_port": port, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server is up at {control_url}")


def _cmd_down(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    if not _arg_true(args, "confirm"):
        result = {"ok": True, "name": name, "status": "planned",
                  "data": {"requires_confirm": True, "action": "stop"}, "error": None}
        if as_json:
            print(json.dumps(result))
        else:
            print(f"'{name}' MCP service stop is planned; re-run with --confirm")
        return
    try:
        sr.stop_remote_mcp_server(entry)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        die(f"could not stop '{name}''s MCP server: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    result = {"ok": True, "name": name, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server stopped — running WordPress instances there "
           f"are unaffected")


register({'remote': cmd_remote})
