from __future__ import annotations
import json
import os
import re
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
    }
    dispatch[action](args, as_json)


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
    if operation not in {"status", "diagnostics", "migrate", "stop"} or not name:
        die("usage: ./sb remote service <status|diagnostics|migrate|stop> <name> [--plan|--confirm]")
    if operation == "diagnostics" and _arg_true(args, "ssh"):
        die("--ssh diagnostics are no longer supported; use the authenticated remote service")
    if _arg_true(args, "processes") and operation != "diagnostics":
        die("--processes requires `remote service diagnostics <name>`")
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    confirmed = _arg_true(args, "confirm")
    try:
        if operation == "status":
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
            if confirmed:
                _upload_runtime_source(entry["ssh"])
            plan = sr.migrate_remote_mcp_service(
                entry, bind, int(entry.get("mcp_port") or sr.DEFAULT_MCP_PORT), token,
                public_url, confirm=confirmed,
                legacy_pidfile=observed.get("legacy_pidfile") == "present",
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
        code = "remote_service_ownership_unknown" if str(exc) == "remote_service_ownership_unknown" else "remote_service_failed"
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
        reachable = sr.check_reachable(entry)
        rows.append({
            "name": name,
            "ssh_configured": bool(entry.get("ssh")),
            "reachable": reachable,
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
        print(f"  {r['name']}  {reach}, {prov}, provider {r['provider']}")


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


def _upload_runtime_source(ssh_target: str) -> None:
    """Stage this checkout onto the VPS so provisioning never depends on
    GitHub reachability or repo visibility. Fresh VPS validation caught that
    cloning alimuzzaman/sandbox anonymously can fail for private/internal repos."""
    excludes = [
        ".git",
        ".cli-venv",
        "mcp/wp-server/.venv",
        "runtime",
        "tmp",
        "__pycache__",
        ".pytest_cache",
    ]
    # Keep the runtime archive's sidecar policy identical to dirty-overlay
    # deployment: match only ``._*`` basenames at any depth, preserving normal
    # dotfiles such as ``.env``.  Count-only diagnostics never include paths or
    # file contents.
    tar_excludes = [*excludes, *sr.appledouble_tar_exclude_patterns()]
    tar_cmd = ["tar"]
    for item in tar_excludes:
        tar_cmd.extend(["--exclude", item])
    tar_cmd.extend(["-czf", "-", "."])
    remote_cmd = (
        "set -e; sandbox_home=${SANDBOX_HOME:-$HOME/sandbox}; "
        "mkdir -p \"$sandbox_home/sb-src\"; "
        "tar -xzf - -C \"$sandbox_home/sb-src\""
    )
    skipped = sr.count_appledouble_files(ROOT, excluded_roots=excludes)
    sr.emit_appledouble_skip_diagnostic(skipped, context="runtime-source")
    tar_res = subprocess.run(
        tar_cmd, cwd=str(ROOT), capture_output=True, timeout=300, check=False,
        # BSD tar synthesizes AppleDouble members for macOS metadata unless
        # this environment switch is set. GNU tar ignores it.
        env={**os.environ, "COPYFILE_DISABLE": "1"},
    )
    if tar_res.returncode != 0:
        raise RuntimeError(
            f"could not package the local sandbox runtime: "
            f"{tar_res.stderr.decode(errors='replace').strip()[:500]}"
        )
    ssh_res = sr.ssh_process(
        ssh_target, remote_cmd, input_data=tar_res.stdout, timeout=300,
    )
    if ssh_res.returncode != 0:
        detail = (ssh_res.stderr or ssh_res.stdout or b"").decode(errors="replace")
        raise RuntimeError(f"could not upload sandbox runtime: {detail.strip()[:500]}")


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
    script_path = os.path.join(ROOT, "scripts", "install-remote.sh")
    with open(script_path) as f:
        script = f.read()
    # bash -s reads the script from stdin; ssh_run's helper only runs a single
    # command string (no stdin piping), so transfer the script inline as
    # base64 over the SSH argument to avoid quoting issues with its content.
    import base64
    encoded = base64.b64encode(script.encode()).decode()
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
    journal = _new_provision_log(name, control_transport)
    try:
        _record_provision_event(journal, "runtime_staging")
        _upload_runtime_source(ssh_target)
        _record_provision_event(journal, "runtime_staged")
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        _record_provision_event(journal, "runtime_staging_failed", status="failed", detail=str(e))
        die(f"could not stage the sandbox runtime on '{name}': "
            f"{sr.redact_ssh_connection(str(e), entry)}")
    cmd = (
        f"echo {encoded} | base64 -d | "
        f"SANDBOX_CONTROL_TRANSPORT={control_transport} bash -s"
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
            sr.start_remote_mcp_server(entry, bind, port, token)
            sr.put_remote(name, control_transport="tailscale",
                          control_url=control_url, tailscale_host=tailscale_ip,
                          mcp_port=port, bearer_token=token, provisioned=True,
                          capabilities=["job.exec", "job.execution-policy.v1"],
                          mcp_service=sr.remote_mcp_service_record(bind, port))
        else:
            control_url = f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            sr.start_remote_mcp_server(entry, "127.0.0.1", port, token,
                                       public_url=control_url)
            sr.put_remote(name, control_transport="https",
                          control_host=public_host, control_url=control_url,
                          mcp_port=port, bearer_token=token, provisioned=True,
                          capabilities=["job.exec", "job.execution-policy.v1"],
                          mcp_service=sr.remote_mcp_service_record("127.0.0.1", port, control_url))
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
        _upload_runtime_source(entry["ssh"])
        observed = sr.remote_mcp_service_status(entry)
        if control_transport == "tailscale":
            tailscale_ip = entry.get("tailscale_host") or sr.resolve_tailscale_ip(entry)
            control_url = control_url or f"http://{tailscale_ip}:{port}"
            plan = sr.migrate_remote_mcp_service(
                entry, tailscale_ip, int(port), token, confirm=True,
                legacy_pidfile=observed.get("legacy_pidfile") == "present",
            )
            sr.put_remote(name, mcp_service=plan["service"])
        else:
            public_host = entry.get("control_host")
            if not public_host:
                die(f"remote '{name}' is missing its HTTPS control host — "
                    f"re-run `./sb remote provision {name} --control-host <host>`")
            control_url = control_url or f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            plan = sr.migrate_remote_mcp_service(
                entry, "127.0.0.1", int(port), token, public_url=control_url,
                confirm=True, legacy_pidfile=observed.get("legacy_pidfile") == "present",
            )
            sr.put_remote(name, mcp_service=plan["service"])
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        die(f"could not start '{name}''s MCP server: "
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
