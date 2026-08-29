from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from collections.abc import Mapping
from contextlib import contextmanager
import io
import threading
import time
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import (
    BASE, HERD_DB_HOST, HERD_DB_PASSWORD, HERD_DB_PORT, HERD_DB_USER,
    MCP_DIR, MCP_VENV, ROOT, SECRETS_ENV, _autologin_mu_plugin, _bridge_token_for,
    _convert_multisite, _core, _ensure_bridge_server, _ensure_litespeed_htaccess,
    _ensure_proxy_up,
    _herd_db_name, _instance_reachable, _is_herd_instance, _local_yaml,
    _pin_wp_constants_in_config, _remove_obsolete_builder_authoring_assets,
    _tld, _web_services, _write_host_runtime_muplugins,
    _write_dl_cache_muplugin, _write_licensing_muplugin, _write_local_yaml,
    _write_mail_muplugin, _write_loopback_muplugin, _write_ondemand_muplugin,
    _write_snapshot_muplugin,
    active_project_file, compose, compose_file, die, ensure_instance, focus_file,
    info, mcp_server_name, ok, plugins_dir,
    proxy_available, resolve_instances, run, save_local_app_password,
    save_local_autologin_token, save_local_bridge_token, site_url, snapshots_dir,
    runtime_health_lines, wp_dir, wpcli,
    php_extension_status,
)

from sandbox.registry import register
from sandbox.application.context import (
    preflight_instance_capability, runtime_service, wordpress_runtime_dependencies,
)
from sandbox.runtimes.base import OperationError, OperationRequest


WORDPRESS_LATEST_DOWNLOAD_URL = "https://wordpress.org/latest.tar.gz"


def _target_is_remote(target) -> bool:
    """Return whether target resolution selected the remote adapter."""
    return getattr(target, "kind", None) == "remote"


def _remote_ensure_reachability(remote_name: str, remote: dict) -> dict | None:
    """Refuse remote ensure before deploy when one bounded liveness probe fails."""
    from sandbox.core import _remote

    try:
        probe = _remote.check_reachable_diagnostic(remote)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        probe = {"reachable": False, "state": "probe_unavailable", "latency_ms": None}
    if probe.get("reachable") is True:
        return None
    state = probe.get("state")
    if not isinstance(state, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", state):
        state = "unreachable"
    return {
        "ok": False,
        "error": {
            "code": "remote_unreachable",
            "message": (
                f"remote {remote_name!r} is unreachable ({state}); "
                "restore SSH reachability or retry with --local"
            ),
        },
        "reachability": {"state": state, "latency_ms": probe.get("latency_ms")},
        "target": {"remote": remote_name},
    }


def _remote_instance_unavailable(
    message: str,
    *,
    target: Mapping[str, object],
) -> dict | None:
    """Return a typed read-only result when the remote has no instance.

    A remote status observation must not turn a missing project instance into
    an implicit ensure/create request.  The co-located CLI still exits nonzero
    for this normal state, so normalize only its stable missing-instance
    diagnostic at the transport boundary.  Keep the remote path out of the
    public envelope; the selected workspace identity is sufficient evidence.
    """
    if not isinstance(message, str):
        return None
    normalized = " ".join(message.split()).lower()
    if "no sandbox instance for project directory" not in normalized:
        return None
    return {
        "ok": False,
        "status": "unavailable",
        "error": {
            "code": "remote_instance_unavailable",
            "message": "the selected remote workspace has no registered instance",
        },
        "feasibility": {
            "state": "blocked",
            "reason": "remote_instance_missing",
            "read_only": True,
            "mutation_required": True,
        },
        "observation": {
            "freshness": "unavailable",
            "source": "remote_status",
            "stale": True,
        },
        "target": dict(target),
    }


def _compose_up(
    instance: str,
    services: tuple[str, ...] | list[str],
    *,
    quiet: bool = False,
) -> object:
    """Start one managed stack and classify stale-network failures.

    Compose normally streams its own diagnostics, but a missing network can
    leave an old container attached to a network ID that no longer exists.
    Capture this bounded startup result so callers get a stable recovery code
    instead of a raw Docker message.  No cleanup or container mutation is
    attempted here.
    """
    result = compose(
        "up", "-d", "--remove-orphans", *services,
        instance=instance, check=False, capture=True,
    )
    returncode = getattr(result, "returncode", 0)
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        returncode = 0
    if returncode == 0:
        if quiet:
            return result
        for stream, to_stderr in (
            (getattr(result, "stdout", ""), False),
            (getattr(result, "stderr", ""), True),
        ):
            if isinstance(stream, str) and stream:
                print(stream, end="" if stream.endswith("\n") else "\n",
                      file=sys.stderr if to_stderr else sys.stdout)
        return result

    output = "\n".join(
        value for value in (
            getattr(result, "stdout", ""), getattr(result, "stderr", ""),
        ) if isinstance(value, str) and value
    )
    if re.search(r"\bnetwork\b[^\n]{0,240}\bnot found\b", output,
                 flags=re.IGNORECASE):
        quoted = shlex.quote(str(instance))
        die(
            "stale_container_network: the managed Docker network for "
            f"instance {instance!r} is missing; no containers or volumes were "
            "removed. Recover this instance with: "
            f"./sb down --instance {quoted} && ./sb up --instance {quoted}",
            code=returncode,
        )

    detail = " ".join(output.split())[:500]
    suffix = f": {detail}" if detail else ""
    die(f"docker compose up failed with exit code {returncode}{suffix}",
        code=returncode)



def cmd_up(cfg: dict, args) -> None:
    inst = args.resolved_instance
    json_output = bool(getattr(args, "json", False))
    owner = _core().registry_find_instance(inst)
    if owner and owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(owner["root"], "start", label=owner.get("label", "default")))
        if isinstance(result, OperationError):
            die(result.message)
        url = result.data.get("url", "")
        if json_output:
            print(json.dumps({
                "ok": True,
                "command": "up",
                "instance": inst,
                "runtime": "compose",
                "url": url,
            }, sort_keys=True))
        else:
            ok(f"Generic Compose: {url}")
        return
    inst_cfg = resolve_instances(cfg)[inst]
    if inst_cfg.get("server") == "herd":
        # Host-served by Herd — nothing to boot; Herd serves linked sites
        # whenever it's running.
        if wp_dir(inst).exists():
            _write_host_runtime_muplugins(inst)
            _remove_obsolete_builder_authoring_assets(inst)
        url = site_url(inst_cfg)
        if json_output:
            print(json.dumps({
                "ok": True,
                "command": "up",
                "instance": inst,
                "runtime": "herd",
                "url": url,
            }, sort_keys=True))
        else:
            ok(f"WordPress: {url}  (host-served by Herd)")
        return
    # Clean-URL reconciliation is owned by the composed ingress/resolver
    # lifecycle.  Never invoke the unreceipted aggregate proxy on instance up.
    # Reconcile the declared service set on every boot.  In particular, this
    # removes stale sidecars left behind after switching web-server modes
    # (for example an old nginx service), so repeated setup cannot accumulate
    # orphan containers.
    services = _web_services(inst_cfg.get("server", "nginx"))
    if json_output:
        _compose_up(inst, services, quiet=True)
    else:
        _compose_up(inst, services)
    if inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions")) is not None:
        # Verify the image that just started before any project/WP wiring is
        # allowed to run.  The probe is standalone PHP and does not touch the
        # database or uploads; a drift/missing/version error is therefore a
        # safe, actionable startup failure rather than a half-provisioned site.
        extension_status = php_extension_status(inst_cfg, instance=inst)
        if extension_status and extension_status.get("drift", {}).get("state") != "ready":
            issues = extension_status.get("drift", {}).get("issues") or []
            detail = issues[0].get("message", "PHP extension planes are not verified") \
                if isinstance(issues[0], dict) else "PHP extension planes are not verified"
            die(f"PHP extension verification blocked: {detail}")
    # Re-assert the mail-capture mu-plugin on every up so it survives
    # down/up and any wp-content reset. Cheap + idempotent; only touches the
    # shared runtime bind-mount, which exists for any provisioned instance.
    if wp_dir(inst).exists():
        _prepare_mu_plugin_directory(inst)
        _write_mail_muplugin(inst)
        _write_loopback_muplugin(inst)
        _write_dl_cache_muplugin(inst)
        _write_ondemand_muplugin(inst)   # spec 010 — on-demand local plugin sourcing
        _write_host_runtime_muplugins(inst)  # specs 003/007 — host-file runtime tools
        _write_licensing_muplugin(inst)  # spec 013 — cross-instance Pro license activation
        _remove_obsolete_builder_authoring_assets(inst)
        # Re-apply the durable abilities enable-flag (spec 003 T003) so a user's
        # explicit on/off survives recreate / db-reset (which wipes the WP option,
        # default-on). Only touches wpcli when the mirror is explicitly set.
        try:
            from sandbox.core._provision import read_local_abilities_enabled
            _ab = read_local_abilities_enabled(inst)
            if _ab is not None:
                wpcli(["option", "update", "sandbox_abilities_enabled", "1" if _ab else "0"],
                      instance=inst, check=False)
        except Exception:
            pass
        try:  # spec 004 — reap old background-job artifacts (>24h)
            from sandbox.commands.jobs import prune_jobs
            prune_jobs(inst)
        except Exception:
            pass
        # Re-assert the snapshot bridge mu-plugin + ensure the host bridge server
        # is running so Tools → Sandbox Snapshots works after a plain `up` (FR-014).
        # Mint the token if it's missing so `up` self-heals an instance whose
        # token was never created (or was dropped by an older apply, before the
        # _build_instance_block preservation fix). Not on herd (no bridge yet).
        if not _is_herd_instance(inst):
            _tok = _bridge_token_for(inst)
            if not _tok:
                import secrets as _secrets
                _tok = _secrets.token_hex(16)
                save_local_bridge_token(_tok, instance=inst)
            _write_snapshot_muplugin(inst, _tok)
            _ensure_bridge_server()
    url = site_url(inst_cfg)
    mailpit_url = f"http://localhost:{inst_cfg['mailpit_port']}"
    if json_output:
        print(json.dumps({
            "ok": True,
            "command": "up",
            "instance": inst,
            "runtime": "wordpress",
            "url": url,
            "mailpit_url": mailpit_url,
        }, sort_keys=True))
    else:
        ok(f"WordPress: {url}")
        ok(f"Mailpit:   {mailpit_url}")


def _prepare_mu_plugin_directory(instance: str) -> None:
    """Make the shared mu-plugin directory writable by host and container tools.

    Docker can create a fresh bind-mounted document root as ``www-data``. The
    Sandbox controller writes generated mu-plugins from the host, so grant the
    narrowly scoped directory shared write access before those writes rather
    than depending on image-entrypoint timing or host/container UID parity.
    """
    if _is_herd_instance(instance):
        return
    compose(
        "exec", "-T", "wp", "sh", "-c",
        "mkdir -p /var/www/html/wp-content/mu-plugins && "
        "chown -R www-data:www-data /var/www/html/wp-content/mu-plugins && "
        "chmod -R a+rwX /var/www/html/wp-content/mu-plugins",
        instance=instance, check=True,
    )

def cmd_down(cfg, args) -> None:
    owner = _core().registry_find_instance(args.resolved_instance)
    if owner and owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(owner["root"], "stop", label=owner.get("label", "default")))
        if isinstance(result, OperationError):
            die(result.message)
        ok(f"stopped generic instance '{args.resolved_instance}'")
        return
    if _is_herd_instance(args.resolved_instance):
        info("host-served by Herd — nothing to stop (sites serve while Herd "
             "runs). Remove entirely with: ./sb instance delete "
             f"{args.resolved_instance}")
        return
    compose("down", instance=args.resolved_instance)

def cmd_status(cfg, args) -> None:
    include_stats = bool(getattr(args, "stats", False))
    if include_stats and getattr(args, "remote", None) and not getattr(args, "local", False):
        die("status --stats is local-only; use remote service diagnostics --processes for a remote host")
    remote_result = _remote_lifecycle(cfg, args, "status")
    if remote_result is not None:
        remote_exit = _status_exit_code(remote_result)
        if remote_exit or "exit_code" in remote_result:
            remote_result = dict(remote_result)
            remote_result["exit_code"] = remote_exit
            if remote_exit:
                remote_result["ok"] = False
        if getattr(args, "json", False):
            print(json.dumps(_public_status_json(remote_result), sort_keys=True))
        else:
            print(f"{remote_result.get('label', getattr(args, 'workspace', 'default'))}: "
                  f"{remote_result.get('status', remote_result.get('code', 'unknown'))}")
            if isinstance(remote_result.get("php_extensions"), Mapping):
                public_extensions = _public_status_json(remote_result["php_extensions"])
                if isinstance(public_extensions, Mapping):
                    print(_render_php_extension_text(public_extensions))
        if remote_exit:
            raise SystemExit(remote_exit)
        return
    inst = args.resolved_instance
    if getattr(args, "json", False):
        payload = _status_json_payload(
            cfg, inst, refresh=bool(getattr(args, "refresh", False)),
            include_stats=include_stats)
        print(json.dumps(_public_status_json(payload), sort_keys=True, default=str))
        if _status_exit_code(payload):
            raise SystemExit(_status_exit_code(payload))
        return
    owner = _core().registry_find_instance(inst)
    runtime_data = None
    if owner and owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(
            owner["root"], "status", label=owner.get("label", "default"),
            arguments={"refresh": bool(getattr(args, "refresh", False))},
        ))
        if isinstance(result, OperationError):
            die(result.message)
        data = dict(result.data)
        runtime_data = data
        ok(f"Generic Compose instance: {inst} ({data.get('status')}) at {data.get('url', '')}")
        for line in runtime_health_lines(runtime_data):
            (info if line.startswith("Optional runtime gaps:") else ok)(line)
        if include_stats:
            _render_container_stats(inst)
        return
    if owner and owner.get("root"):
        result = runtime_service(cfg).invoke(OperationRequest(
            project_root=owner["root"],
            operation="status",
            label=owner.get("label", "default"),
            arguments={"refresh": bool(getattr(args, "refresh", False))},
        ))
        if isinstance(result, OperationError):
            die(result.message)
        runtime_data = dict(result.data)
    if _is_herd_instance(inst):
        entry = owner or {}
        up = _instance_reachable(entry)
        ok(f"Instance: {inst}  (host-served by Herd — "
           f"{'reachable' if up else 'NOT reachable'} at {entry.get('url')})")
    else:
        compose("ps", instance=inst)
    if include_stats:
        _render_container_stats(inst)
    apf = active_project_file(inst)
    ff = focus_file(inst)
    srv = mcp_server_name(inst)
    ok(f"Instance: {inst}  (Claude tools: mcp__{srv}__*)")
    ok(f"Server: {resolve_instances(cfg)[inst].get('server', 'nginx')}")
    if owner and owner.get("root"):
        ok(f"Project: {owner['root']}")
    if runtime_data is not None:
        for line in runtime_health_lines(runtime_data):
            (info if line.startswith("Optional runtime gaps:") else ok)(line)
    else:
        info("No project registered (cd into a plugin repo and run ./sb ensure)")
    if ff.exists():
        ok(f"Focused plugin: {ff.read_text().strip()}")
    else:
        info("No focused plugin (run: ./sb focus <slug>)")
    # Keep the wp-admin snapshot bridge reachable for running instances (it only
    # auto-starts on `sb up`, so an already-running instance would otherwise have
    # no bridge — Tools → Sandbox Snapshots would fail to connect).
    if not _is_herd_instance(inst) and _bridge_token_for(inst):
        _ensure_bridge_server()
    extension_data = php_extension_status(resolve_instances(cfg)[inst], instance=inst)
    if extension_data is not None:
        print(_render_php_extension_text(extension_data))
        if _status_exit_code({"php_extensions": extension_data}):
            raise SystemExit(_status_exit_code({"php_extensions": extension_data}))


_STATUS_SENSITIVE_KEY = re.compile(
    r"(?:login_url|token|password|passphrase|secret|credential|cookie|authorization)",
    re.IGNORECASE,
)
_STATUS_AUTOLOGIN_VALUE = re.compile(
    r"(?i)(sandbox_autologin=)[^&#\s\"']*",
)


def _public_status_json(value):
    """Copy status data while omitting credential-like fields for JSON output."""
    if isinstance(value, Mapping):
        return {
            _public_status_string(str(key)): _public_status_json(child)
            for key, child in value.items()
            if not _STATUS_SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, list):
        return [_public_status_json(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_public_status_json(child) for child in value)
    if isinstance(value, str):
        return _public_status_string(_STATUS_AUTOLOGIN_VALUE.sub(r"\1[REDACTED]", value))
    return value


def _public_status_string(value: str) -> str:
    """Apply shared credential detection/redaction without rewriting safe URLs."""
    from sandbox.services.redaction import argv_contains_credentials, redact_text

    return redact_text(value) if argv_contains_credentials([value]) else value


def _status_exit_code(payload: Mapping[str, object]) -> int:
    """Return the emitted status document's stable process result."""
    extension = payload.get("php_extensions")
    failed = payload.get("ok", True) is False or (
        isinstance(extension, Mapping) and extension.get("ok", True) is False)
    values = [payload.get("exit_code")]
    if isinstance(extension, Mapping):
        values.append(extension.get("exit_code"))
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return max(1, min(value, 255)) if value > 0 else 1
    return 1 if failed else 0


def _render_php_extension_text(report: Mapping[str, object]) -> str:
    """Render the canonical extension report without introducing new data."""
    desired = report.get("desired") if isinstance(report.get("desired"), Mapping) else {}
    catalog = desired.get("catalog") if isinstance(desired.get("catalog"), Mapping) else {}
    readiness = report.get("readiness") if isinstance(report.get("readiness"), Mapping) else {}
    staleness = report.get("staleness") if isinstance(report.get("staleness"), Mapping) else {}
    drift = report.get("drift") if isinstance(report.get("drift"), Mapping) else {}
    lines = ["\nPHP extensions:"]
    lines.append(f"  Profile: {desired.get('profile') or 'none'}")
    lines.append("  Catalog: revision " + str(catalog.get("revision", "unknown"))
                 + " (" + str(catalog.get("digest", "unavailable")) + ")")
    lines.append(f"  Resolution digest: {desired.get('resolution_digest', 'unavailable')}")
    if desired.get("build_digest"):
        lines.append(f"  Build digest: {desired['build_digest']}")
    lines.append("  Readiness: " + str(readiness.get("state", "unknown"))
                 + "; staleness: " + str(staleness.get("state", "unknown"))
                 + "; drift: " + str(drift.get("state", "unknown")))
    observed = report.get("observed") if isinstance(report.get("observed"), Mapping) else {}
    for plane in ("web", "cli", "exec", "phpunit"):
        row = observed.get(plane) if isinstance(observed.get(plane), Mapping) else {}
        detail = str(row.get("state", "unavailable"))
        if row.get("php_version"):
            detail += " (PHP " + str(row["php_version"]) + ")"
        lines.append(f"  {plane}: {detail}")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    for issue in issues:
        if isinstance(issue, Mapping):
            suffix = f" [{issue.get('extension')}]" if issue.get("extension") else ""
            lines.append(f"  ! {issue.get('code', 'plane_drift')}{suffix}: "
                         f"{issue.get('message', '')}")
    return "\n".join(lines)


def _status_json_payload(cfg, inst: str, *, refresh: bool = False,
                         include_stats: bool = False) -> dict:
    """Return one bounded, machine-readable status document.

    The human status path intentionally has progress output and health hints.
    Keep the JSON path independent so Docker's command banner, focus hints,
    and bridge reconciliation can never corrupt a parser's single document.
    """
    owner = _core().registry_find_instance(inst) or {}
    if owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(
            owner.get("root", ""), "status", label=owner.get("label", "default"),
            arguments={"refresh": refresh},
        ))
        if isinstance(result, OperationError):
            return {"ok": False, "instance": inst,
                    "error": {"code": result.code, "message": result.message}}
        payload = {"ok": bool(result.ok), "instance": inst, "kind": "compose",
                   **dict(result.data)}
        if include_stats:
            from sandbox.services.container_stats import local_container_stats
            payload["container_resources"] = local_container_stats(inst)
        return payload

    runtime_data = None
    runtime_error = None
    if owner.get("root"):
        try:
            result = runtime_service(cfg).invoke(OperationRequest(
                owner["root"], "status", label=owner.get("label", "default"),
                arguments={"refresh": refresh},
            ))
            if isinstance(result, OperationError):
                runtime_error = {"code": result.code, "message": result.message}
            else:
                runtime_data = dict(result.data)
        except Exception as exc:
            # A stale registry row must remain observable as a typed, partial
            # result. Status is diagnostic and must not turn a missing checkout
            # into a traceback (or prevent an independent resource snapshot).
            runtime_error = {
                "code": "project_observation_unavailable",
                "message": str(exc).replace("\n", " ")[:240],
            }

    instance_cfg = (resolve_instances(cfg).get(inst) or {})
    containers = []
    if not _is_herd_instance(inst) and instance_cfg:
        ps = compose("ps", "--format", "json", instance=inst,
                     check=False, capture=True)
        for line in (getattr(ps, "stdout", "") or "").splitlines():
            try:
                containers.append(json.loads(line))
            except (TypeError, ValueError):
                continue
    reachable = _instance_reachable(owner) if owner else False
    fallback_url = site_url(instance_cfg) if instance_cfg else None
    payload = {
        "ok": bool(runtime_data is not None or reachable),
        "instance": inst,
        "kind": "wordpress",
        "label": owner.get("label", "default"),
        "root": owner.get("root"),
        "url": owner.get("url") or instance_cfg.get("url") or fallback_url,
        "server": instance_cfg.get("server", owner.get("server", "nginx")),
        "reachable": reachable,
        "containers": containers,
    }
    if runtime_data is not None:
        payload["runtime"] = runtime_data
    if runtime_error is not None:
        payload["error"] = runtime_error
    if include_stats and not _is_herd_instance(inst):
        from sandbox.services.container_stats import local_container_stats
        payload["container_resources"] = local_container_stats(inst)
    extension_data = php_extension_status(instance_cfg, instance=inst)
    if extension_data is not None:
        payload["php_extensions"] = extension_data
        payload["ok"] = bool(payload["ok"] and extension_data.get("ok"))
        payload["exit_code"] = 0 if payload["ok"] else 1
    return payload


def _render_container_stats(instance: str) -> None:
    from sandbox.services.container_stats import local_container_stats

    snapshot = local_container_stats(instance)
    if snapshot["status"] == "unavailable":
        code = (snapshot.get("error") or {}).get("code", "unavailable")
        info(f"Container resources: unavailable ({code})")
        return
    print("\nContainer resources (point-in-time):")
    if not snapshot["rows"]:
        print("  no running containers")
        return
    for row in snapshot["rows"]:
        print(f"  {row['name']}: CPU {row['cpu_percent']:.2f}%  "
              f"memory {row['memory_used_bytes']} bytes ({row['memory_percent']:.2f}%)  "
              f"PIDs {row['pids']}")

def cmd_logs(cfg, args) -> None:
    remote_result = _remote_lifecycle(cfg, args, "logs")
    if remote_result is not None:
        if remote_result.get("output"):
            print(remote_result["output"], end="")
        return
    owner = _core().registry_find_instance(args.resolved_instance)
    if owner and owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(owner["root"], "logs", label=owner.get("label", "default")))
        if isinstance(result, OperationError):
            die(result.message)
        print(result.data.get("output", ""), end="")
        return
    if _is_herd_instance(args.resolved_instance):
        die("no containers on a herd instance — tail the WP debug log instead: "
            f"tail -f runtime/wp-{args.resolved_instance}/wp-content/debug.log")
    lines = _log_tail(args)
    options = ["--no-color", f"--tail={lines}"]
    if getattr(args, "since", None):
        options.append(f"--since={args.since}")
    if getattr(args, "follow", False):
        options.append("-f")
    compose("logs", *options, "wp", "db", instance=args.resolved_instance)


def _log_tail(args) -> int:
    """Validate the bounded log snapshot requested by local/remote callers."""
    lines = getattr(args, "lines", 200)
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 1000:
        die("log lines must be between 1 and 1000", 2)
    since = getattr(args, "since", None)
    if since is not None and (not isinstance(since, str) or len(since) > 64):
        die("log since must be an RFC 3339 timestamp or Unix seconds (max 64 characters)", 2)
    return lines


def _direct_remote_lifecycle(remote_name: str, instance: str,
                             args, action: str) -> dict:
    """Observe one known remote instance without a local project workspace."""
    from sandbox.core import _remote

    remote = _remote.get_remote(remote_name)
    if not isinstance(remote, dict):
        die(
            f"unknown remote {remote_name!r}; run `./sb remote list` "
            "or select another explicit target"
        )
    if not remote.get("provisioned"):
        die(f"remote {remote_name!r} is not provisioned")
    capabilities = remote.get("capabilities")
    if not isinstance(capabilities, (list, tuple, set)) \
            or "job.exec" not in capabilities:
        die(f"remote {remote_name!r} does not advertise 'job.exec'")

    command = [
        _remote.remote_sb_path(remote), action, "--local",
        "--instance", str(instance),
    ]
    if action == "logs":
        command.extend(["--lines", str(_log_tail(args))])
        if getattr(args, "since", None):
            command.extend(["--since", args.since])
        if getattr(args, "follow", False):
            command.append("--follow")
    if action == "status":
        command.append("--json")
        if getattr(args, "refresh", False):
            command.append("--refresh")
    result = _remote.ssh_run(
        remote, __import__("shlex").join(command), timeout=25,
    )
    target = {"remote": remote_name, "instance": str(instance)}
    if action == "logs":
        if result.returncode != 0:
            die((result.stderr or result.stdout or f"remote {action} failed").strip()[:2000])
        return {"ok": True, "action": action, "output": result.stdout or "",
                "target": target}

    try:
        payload = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, Mapping) and result.returncode != 0:
        unavailable = _remote_instance_unavailable(
            result.stderr or result.stdout or "",
            target=target,
        )
        if unavailable is not None:
            return unavailable
        die((result.stderr or result.stdout or f"remote {action} failed").strip()[:2000])
    if isinstance(payload, Mapping) and result.returncode != 0:
        payload = dict(payload)
        payload["ok"] = False
        payload["exit_code"] = result.returncode
    return {**(payload or {"ok": True}), "target": target}


def _remote_lifecycle(cfg, args, action: str) -> dict | None:
    """Run instance lifecycle operations against a selected provisioned remote."""
    remote_name = getattr(args, "remote", None)
    from sandbox.application.context import durable_job_dependencies
    from sandbox.application.target_service import TargetResolutionError
    from sandbox.jobs.models import TargetRequest
    from sandbox.core import _remote
    direct_instance = getattr(args, "instance", None)
    if (
        action in {"status", "logs"}
        and remote_name
        and direct_instance
        and not getattr(args, "project_dir", None)
    ):
        return _direct_remote_lifecycle(remote_name, direct_instance, args, action)
    project_dir = getattr(args, "project_dir", None) or os.getcwd()
    workspace = getattr(args, "workspace", None) or getattr(args, "label", None)
    try:
        target = durable_job_dependencies()["target_service"].resolve(TargetRequest(
            project_dir=project_dir, local=bool(getattr(args, "local", False)), remote=remote_name,
            workspace=workspace, required_capability="job.exec",
            # Instance lifecycle never infers a remote: `sb ensure`/`status`/
            # `logs` with no selector boot and inspect the LOCAL instance, as
            # they did before target inference existed. A remote is used only
            # for --remote NAME or a project whose runtime default is remote.
            allow_inferred_remote=False))
    except TargetResolutionError as exc:
        # Legacy compatibility tests and callers may invoke ensure from a
        # non-project directory without a target selector. Preserve the
        # existing runtime preflight path in that case; explicit remote
        # selection and configured remote errors still fail closed.
        if exc.code == "invalid_project" and not remote_name and not getattr(args, "local", False):
            return None
        die(f"{exc.code}: {exc}")
    if not _target_is_remote(target):
        return None
    remote = target.remote or _remote.get_remote(target.remote_name)
    if action == "ensure":
        refusal = _remote_ensure_reachability(target.remote_name, remote)
        if refusal is not None:
            return refusal
        deployed = _remote.deploy_exact_working_tree(
            remote, target.project_root, remote_name=target.remote_name,
        )
        target_path = _remote.prepare_remote_workspace(
            remote, target.project_root, target.workspace_label,
            deployed_path=deployed["target_path"])
    else:
        deployed = None
        target_path = _remote.remote_workspace_path(
            remote, target.project_root, target.workspace_label)
    sb = _remote.remote_sb_path(remote)
    # The durable workspace label belongs to the outer remote controller.  It
    # selects the staged checkout above; it is not an inner instance label for
    # the co-located CLI.  Let that CLI resolve its registered default (or
    # report a genuine ambiguity) from the staged project root.  `ensure` is
    # intentionally different: it creates the requested reusable inner label,
    # so retain its explicit `--label` + `--create` contract.
    command = [sb, action, "--local", "--project-dir", target_path]
    if action == "logs":
        command.extend(["--lines", str(_log_tail(args))])
        if getattr(args, "since", None):
            command.extend(["--since", args.since])
        if getattr(args, "follow", False):
            command.append("--follow")
    if action == "ensure":
        command.extend(["--label", target.workspace_label, "--create"])
    # `ensure` must report the instance record (url, ports, instance name) the
    # same way the local path does; without --json the remote prints human text,
    # `_last_json` finds nothing, and callers get a bare {"ok": true} with no URL.
    if action in ("status", "ensure"):
        command.append("--json")
    reveal_login = action == "ensure" and bool(getattr(args, "reveal_login", False))
    if reveal_login:
        # The remote redacts its own JSON, so the token has to be asked for on
        # the VPS side too; asking here alone would only reveal a placeholder.
        command.append("--reveal-login")
    shlex_join = __import__("shlex").join
    result = _remote.ssh_run(remote, shlex_join(command), timeout=900 if action == "ensure" else 25)
    if reveal_login and result.returncode != 0 \
            and "--reveal-login" in (result.stderr or "") \
            and "unrecognized arguments" in (result.stderr or ""):
        # A remote staged from an older runtime has no such flag. Losing the
        # token is worth far less than losing the instance, so boot it anyway
        # and let the record carry the redacted placeholder; `sb remote
        # provision` restages the runtime when the token is actually needed.
        info(f"remote '{target.remote_name}' runtime predates --reveal-login; "
             "ensuring without it (run `./sb remote provision` to restage)")
        command.remove("--reveal-login")
        reveal_login = False
        result = _remote.ssh_run(remote, shlex_join(command), timeout=900)
    payload = None
    if action == "status":
        try:
            payload = json.loads((result.stdout or "").strip())
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, Mapping) and result.returncode != 0:
            unavailable = _remote_instance_unavailable(
                result.stderr or result.stdout or "",
                target={"remote": target.remote_name, "workspace": target.workspace_label},
            )
            if unavailable is not None:
                return unavailable
            die((result.stderr or result.stdout or f"remote {action} failed").strip()[:2000])
        if isinstance(payload, Mapping) and result.returncode != 0:
            payload = dict(payload)
            payload["ok"] = False
            payload["exit_code"] = result.returncode
    elif result.returncode != 0:
        die((result.stderr or result.stdout or f"remote {action} failed").strip()[:2000])
    elif action != "logs":
        payload = _remote._last_json(result.stdout or "")
        if payload is None:
            payload = {
                "ok": False,
                "error": {
                    "code": "remote_empty_output",
                    "message": f"remote {action} returned no JSON output",
                },
            }
        if reveal_login and isinstance(payload, dict):
            # Lift ONE field out of the unredacted remote document: the
            # autologin URL the operator explicitly asked for. Everything else
            # stays as the redacted parse produced it.
            raw = _remote._last_json(result.stdout or "", redact=False) or {}
            revealed = raw.get("login_url")
            if isinstance(revealed, str) and "sandbox_autologin=" in revealed:
                payload["login_url"] = revealed
    if action == "logs":
        return {"ok": True, "action": action, "output": result.stdout or "",
                "target": {"remote": target.remote_name, "workspace": target.workspace_label}}
    return {**(payload or {"ok": True}), "target": {"remote": target.remote_name,
            "workspace": target.workspace_label}, "source": deployed}

def cmd_shell(cfg, args) -> None:
    error = preflight_instance_capability(cfg, args.resolved_instance, "wordpress.exec")
    if error is not None:
        die(error.message)
    from sandbox.application.context import managed_native_instance_selected
    if managed_native_instance_selected(args.resolved_instance) is not None:
        die("managed-native interactive shell requires an adapter-owned terminal; "
            "Compose/host fallback is disabled")
    if _is_herd_instance(args.resolved_instance):
        die("no containers on a herd instance — the WP install is on the host "
            f"at runtime/wp-{args.resolved_instance}/")
    compose("exec", "wp", "bash", instance=args.resolved_instance)


def _download_wordpress_core(instance: str, args: list[str]) -> None:
    """Install core as the same unprivileged user used by the web tier.

    The official image can seed a bind-mounted document root as the host user.
    Reconcile that ownership before forcing a versioned core download; otherwise
    tar emits one permission error per core file and a remote bootstrap can
    appear to hang while its output pipe fills.
    """
    if not _is_herd_instance(instance):
        deadline = time.monotonic() + 30
        while True:
            seeded = compose(
                "exec", "-T", "wp", "sh", "-c",
                "test -f /var/www/html/wp-includes/version.php && "
                "{ test -f /var/www/html/wp-includes/Requests/src/Requests.php "
                "|| test -f /var/www/html/wp-includes/Requests/Requests.php; }",
                instance=instance, check=False, capture=True, timeout=5,
            )
            if getattr(seeded, "returncode", 1) == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "WordPress image bootstrap did not finish within 30s; "
                    "the document root is still incomplete"
                )
            time.sleep(0.25)
        compose(
            "exec", "-T", "wp", "chown", "-R", "www-data:www-data",
            "/var/www/html", instance=instance, check=True,
        )
    # `wp core download` operates on archive files and does not bootstrap the
    # currently mounted WordPress tree, which may be incomplete on first boot.
    wpcli(args, instance=instance, check=True)


def cmd_install(cfg, args) -> None:
    error = preflight_instance_capability(cfg, args.resolved_instance, "wordpress.cli")
    if error is not None:
        die(error.message)
    inst = args.resolved_instance
    inst_cfg = resolve_instances(cfg)[inst]
    adm = inst_cfg["admin"]
    port = inst_cfg["wordpress_port"]
    server = inst_cfg.get("server", "nginx")

    # WordPress bootstrap, made robust for EVERY server:
    #  - The image is PHP-only (wordpress:php8.1) — WP core is downloaded here,
    #    following @wordpress/env's approach. This avoids 'manifest unknown'
    #    errors from non-existent patch-level image tags (e.g. 6.9.4-php8.1).
    #  - When a wp_version is pinned, always force-download it so the container
    #    runs the exact requested version, not whatever ships in the base image.
    #  - A fresh bind mount can contain a partial image-seeded core. Download
    #    before invoking any command that bootstraps WordPress, rather than
    #    probing that partial tree with `core version` or `check-update`.
    wp_v = inst_cfg.get("wp_version")
    if wp_v:
        info(f"downloading WordPress {wp_v}…")
        _download_wordpress_core(
            inst, ["core", "download", "--force", f"--version={wp_v}"]
        )
    else:
        info("downloading WordPress core (latest)…")
        # Avoid WP-CLI's version-check API for the unpinned path.  The direct
        # official archive is stable, supports the same en_US tarball, and
        # lets WP-CLI verify the downloaded archive without a second
        # version-check request that commonly times out on disposable hosts.
        _download_wordpress_core(
            inst, ["core", "download", WORDPRESS_LATEST_DOWNLOAD_URL, "--force"]
        )
    chk = wpcli(["config", "path"], instance=inst, check=False, capture=True)
    if chk.returncode != 0:
        info("generating wp-config.php…")
        if server == "herd":
            # Host MySQL (DBngin) with a per-instance database — every herd
            # instance shares the one host server, unlike docker's per-stack DB.
            wpcli([
                "config", "create",
                f"--dbhost={HERD_DB_HOST}:{HERD_DB_PORT}",
                f"--dbname={_herd_db_name(inst)}",
                f"--dbuser={HERD_DB_USER}", f"--dbpass={HERD_DB_PASSWORD}",
                "--skip-check", "--force",
            ], instance=inst, check=False)
        else:
            wpcli([
                "config", "create",
                "--dbhost=db:3306", "--dbname=wp",
                "--dbuser=wp", "--dbpass=wp",
                "--skip-check", "--force",
            ], instance=inst, check=False)
    if server == "herd":
        # Idempotent: errors when the DB already exists, which is fine.
        wpcli(["db", "create"], instance=inst, check=False, capture=True)

    wpcli([
        "core", "install",
        f"--url={site_url(inst_cfg)}",
        f"--title={adm.get('site_title', 'Sandbox')}",
        f"--admin_user={adm.get('user', 'admin')}",
        f"--admin_password={adm.get('password', 'admin')}",
        f"--admin_email={adm.get('email', 'admin@example.com')}",
        "--skip-email",
    ], instance=inst)
    wpcli(["rewrite", "structure", "/%postname%/"], instance=inst)

    # Every sandbox instance is a disposable dev/staging site — several are
    # publicly resolvable once exposed (`sb preview create`, `sb deploy
    # --expose`), and a preview URL usually carries an autologin token. Search
    # engines must never index one. blog_public=0 makes WordPress serve a
    # `Disallow: /` robots.txt AND emit `noindex,nofollow` on every page; the
    # meta tag is the part that matters, since a robots.txt disallow alone still
    # permits URL-only indexing from inbound links. Caddy denies /robots.txt at
    # the exposed edge too (`_remote._caddy_proxy_command`) — this is the
    # in-application half of the same guarantee.
    wpcli(["option", "update", "blog_public", "0"], instance=inst, check=False)

    # Core download repairs the document root as www-data, which also resets
    # the bind-mounted mu-plugin directory. Reapply its narrowly scoped shared
    # write mode before generating the autologin, snapshot, and mail plugins.
    _prepare_mu_plugin_directory(inst)
    _write_host_runtime_muplugins(inst)

    # The OpenLiteSpeed image's vhost template does `autoLoadHtaccess`, but
    # WordPress only writes a physical .htaccess under Apache — under OLS it
    # writes none, so pretty permalinks + /wp-json/ 404. Drop the canonical
    # WP rewrite .htaccess into the docroot so OLS's autoload picks it up.
    if server == "litespeed":
        _ensure_litespeed_htaccess(inst)
    if server in ("litespeed", "herd"):
        # No WORDPRESS_CONFIG_EXTRA env on these runtimes — lsphp can't read
        # the container env, and herd has no container at all. Pin the
        # project's wp-config constants as literals (the host/OLS wp-config
        # is stable: nothing regenerates it, so literals persist).
        _pin_wp_constants_in_config(inst, inst_cfg)

    # Multisite (sandbox.config.json `multisite`): convert the fresh install,
    # write the marker that activates the network constants, and lay down the
    # network rewrite rules. Must run before plugin/theme wiring so those
    # activate against the converted site.
    _convert_multisite(inst, inst_cfg)

    # Auto-provision an Application Password for the MCP server so wp_rest
    # works out of the box. Idempotent: deletes any prior 'wp-mcp' password
    # and re-creates so the value lands in sandbox.local.yml.
    info("Provisioning Application Password for wp-mcp…")
    wpcli([
        "user", "application-password", "delete",
        adm.get("user", "admin"), "wp-mcp", "--all",
    ], instance=inst, check=False, capture=True)
    res = wpcli([
        "user", "application-password", "create",
        adm.get("user", "admin"), "wp-mcp", "--porcelain",
    ], instance=inst, check=False, capture=True)
    app_pw = (res.stdout or "").strip().splitlines()[-1] if res.returncode == 0 else ""
    if app_pw:
        save_local_app_password(app_pw, instance=inst)
        ok("Application Password saved to sandbox.local.yml")
    else:
        info("(Could not auto-create Application Password — set it manually "
             f"in sandbox.local.yml under instances.{inst}.app_password)")

    # The OpenLiteSpeed image pre-installs the LiteSpeed Cache plugin (LSCWP)
    # + server-level lscache. That competes with xSpeed, so leaving it active
    # would pollute any xSpeed cache/benchmark test on a litespeed instance.
    # Deactivate it (idempotent — no-op if not present).
    if server == "litespeed":
        info("litespeed: deactivating LiteSpeed Cache (LSCWP) so it doesn't "
             "shadow xSpeed's page cache…")
        wpcli(["plugin", "deactivate", "litespeed-cache"],
              instance=inst, check=False, capture=True)

    # One-click autologin URL — token embedded in the mu-plugin file so it
    # survives container restarts (the WP Docker entrypoint regenerates
    # wp-config.php from env-vars on every start, wiping `wp config set` values).
    import secrets as _secrets
    autologin_token = _secrets.token_hex(16)
    mu_dir = wp_dir(inst) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    (mu_dir / "00-sandbox-autologin.php").write_text(_autologin_mu_plugin(autologin_token))
    save_local_autologin_token(autologin_token, instance=inst)

    # Dashboard snapshots mu-plugin (Tools → Sandbox Snapshots) — calls the host
    # `sb web` bridge. Docker only; snapshots aren't supported on herd yet.
    if server != "herd":
        bridge_token = _secrets.token_hex(16)
        _write_snapshot_muplugin(inst, bridge_token)
        save_local_bridge_token(bridge_token, instance=inst)

    # Capture all PHP mail in the Mailpit container (the image has no working
    # sendmail; without this wp_mail() silently fails). Visible to web + wpcli
    # tiers via the shared bind-mount. NOT on herd — there is no `mailpit`
    # host there, and the mu-plugin would break host mail instead.
    if server != "herd":
        _write_mail_muplugin(inst)
        _write_loopback_muplugin(inst)
        # Cache plugin/theme zip downloads (Templately FSI etc.) — the cache dir
        # is only mounted on docker tiers, so the mu-plugin no-ops on herd anyway.
        _write_dl_cache_muplugin(inst)
        _write_ondemand_muplugin(inst)   # spec 010 — on-demand local plugin sourcing
        _write_licensing_muplugin(inst)  # spec 013 — cross-instance Pro license activation
        _remove_obsolete_builder_authoring_assets(inst)
    elif wp_dir(inst).exists():
        _remove_obsolete_builder_authoring_assets(inst)

    base = site_url(inst_cfg)  # https://<name>.<tld> when secured, else localhost:<port>
    ok(f"Admin: {base}/wp-admin"
       f"  •  Login: {base}/?sandbox_autologin={autologin_token}")


def wp_is_installed(instance: str) -> bool:
    """Return whether WordPress has completed its database install.

    ``core version`` only proves that files exist; the base image can contain
    those files before the database is initialized.  ``core is-installed`` is
    the idempotency gate needed by ``sb setup``.
    """
    result = wpcli(["core", "is-installed"], instance=instance,
                   check=False, capture=True)
    return result.returncode == 0


def _probe_mcp_server() -> tuple[bool, str]:
    """Check the MCP server import boundary without starting a server."""
    python = MCP_VENV / "bin" / "python"
    if not python.exists():
        return False, "MCP venv is missing"
    probe = (
        "import server; "
        "from app import _require_project_capability; "
        "_require_project_capability('/tmp/sandbox-doctor-probe', None, 'wordpress.cli')"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", probe], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "import failed").strip()[:500]
    return True, ""


def _project_declares_plugin_check(project_root: str | Path) -> bool:
    """Return whether the resolved project config opts into Plugin Check."""
    try:
        project = _core().load_project_config(str(project_root))
    except Exception:
        return False
    plugins = project.get("plugins_resolved") or project.get("plugins") or {}
    return isinstance(plugins, dict) and "plugin-check" in plugins


def _storage_pressure_doctor_checks() -> list[tuple[str, bool, str]]:
    """Read normalized offline storage-pressure evidence for ``sb doctor``.

    The storage monitor owns record/configuration parsing and deliberately does
    no network, subprocess, or host-probe work for this surface.  This adapter
    keeps the lifecycle command fail-closed if that evidence is unavailable or
    violates the small public ``{label, ok, hint}`` contract.
    """
    try:
        from sandbox.resources.monitor import storage_doctor_checks
        rows = storage_doctor_checks()
    except Exception:
        return [(
            "storage monitor evidence available",
            False,
            "storage monitor evidence could not be read; refresh it with sb resources monitor --json",
        )]

    if not isinstance(rows, list) or not rows:
        return [(
            "storage monitor evidence available",
            False,
            "storage monitor evidence is invalid; refresh it with sb resources monitor --json",
        )]

    checks: list[tuple[str, bool, str]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("label"), str) \
                or not isinstance(row.get("ok"), bool) \
                or not isinstance(row.get("hint"), str):
            return [(
                "storage monitor evidence available",
                False,
                "storage monitor evidence is invalid; refresh it with sb resources monitor --json",
            )]
        checks.append((row["label"], row["ok"], row["hint"]))
    return checks


def cmd_doctor(cfg, args) -> None:
    """Audit the whole stack and report what's broken."""
    inst = args.resolved_instance
    json_mode = bool(getattr(args, "json", False))
    report_only = bool(getattr(args, "report_only", False))
    if report_only and not json_mode:
        die("--report-only requires --json; no command was executed")
    error = preflight_instance_capability(cfg, inst, "wordpress.cli")
    if error is not None:
        if json_mode:
            payload = {"ok": False, "exit_code": 1, "instance": inst,
                       "checks": [{"section": "preflight", "label": error.message,
                                   "ok": False}]}
            print(json.dumps(_public_status_json(payload), sort_keys=True))
            raise SystemExit(1)
        die(error.message)
    inst_cfg = resolve_instances(cfg)[inst]
    adm = inst_cfg["admin"]
    port = inst_cfg["wordpress_port"]
    problems = 0
    checks: list[dict] = []
    current_section = "summary"
    extension_data = None

    def section(label: str) -> None:
        nonlocal current_section
        current_section = label
        if not json_mode:
            print(f"\n{label}:")

    def note(message: str) -> None:
        if not json_mode:
            info(message)

    # Spec 009: nudge (once, non-disruptive) if machine-state is still in the repo
    # and a per-user base hasn't been adopted. Everything works via fallback until
    # then; this is purely discoverability for `./sb migrate`.
    if (ROOT / "runtime" / "registry.json").exists() and \
            not (BASE / "runtime" / "registry.json").exists():
        note(f"Machine-state is still in the repo. Relocate it under {BASE} with "
             f"`./sb migrate --apply` (spec 009). Harmless to defer.")

    if not json_mode:
        print(f"\nInstance: {inst}  (http://localhost:{port})")

    def check(label: str, ok_: bool, hint: str = "", *, emit: bool = True) -> None:
        nonlocal problems
        row = {"section": current_section, "label": label, "ok": bool(ok_)}
        if not ok_ and hint:
            row["hint"] = hint
        checks.append(row)
        mark = "✓" if ok_ else "✗"
        line = f"  {mark} {label}"
        if not ok_:
            problems += 1
            if hint:
                line += f"\n      → {hint}"
        if emit and not json_mode:
            print(line)

    section("Runtime")
    owner = _core().registry_find_instance(inst)
    runtime_data = None
    runtime_error = ""
    if owner and owner.get("root"):
        runtime_result = runtime_service(cfg).invoke(OperationRequest(
            owner["root"], "status", label=owner.get("label", "default")))
        if isinstance(runtime_result, OperationError):
            runtime_error = runtime_result.message
        else:
            runtime_data = dict(runtime_result.data)
    if runtime_data is None:
        check("runtime status available", False,
              runtime_error or "register this project with ./sb ensure")
    else:
        state = runtime_data.get("state") or runtime_data.get("status")
        healthy = bool(runtime_result.ok) and state not in {"blocked", "drifted",
                                                            "cleanup_incomplete"}
        check("runtime and isolation health", healthy,
              "inspect ./sb status and native recovery state before running payloads")
        for line in runtime_health_lines(runtime_data):
            note("  " + line)

    # PHP extension intent is checked independently of WordPress bootstrap.
    # A configured project must show desired state and all four observation
    # planes; unavailable observations are an honest doctor failure rather than
    # an implicit pass based on a stale cache.
    extension_data = php_extension_status(inst_cfg, instance=inst)
    if extension_data is not None:
        current_section = "PHP extensions"
        check("PHP extension readiness", bool(extension_data.get("ok")),
              hint="inspect the structured extension issues and reconcile every plane",
              emit=False)
        if not json_mode:
            print(_render_php_extension_text(extension_data).lstrip("\n"))

    section("Containers")
    ps = compose("ps", "--format", "json", instance=inst,
                 check=False, capture=True)
    containers = []
    for ln in (ps.stdout or "").splitlines():
        try:
            containers.append(json.loads(ln))
        except ValueError:
            pass
    services = {c.get("Service"): c.get("State") for c in containers}
    for svc in ("wp", "db", "mailpit"):
        check(f"{svc} running", services.get(svc) == "running",
              hint=f"./sb up --instance {inst}   (currently: {services.get(svc, 'missing')})")

    section("WordPress")
    r = wpcli(["core", "is-installed"], instance=inst,
              check=False, capture=True)
    check("core installed", r.returncode == 0,
          hint=f"./sb install --instance {inst}")

    section("MCP wiring")
    app_pw = ((cfg.get("instances", {}) or {}).get(inst, {}) or {}
              ).get("app_password", "")
    check("application_password set", bool(app_pw),
          hint=f"./sb install --instance {inst}   (auto-provisions)")
    if app_pw:
        import base64, urllib.request, urllib.error, time as _t
        token = base64.b64encode(
            f"{adm.get('user', 'admin')}:{app_pw}".encode()).decode()

        import ssl as _ssl
        _noverify = _ssl.create_default_context()
        _noverify.check_hostname = False
        _noverify.verify_mode = _ssl.CERT_NONE

        def _rest_probe(url):
            req = urllib.request.Request(url + "/wp-json/wp/v2/users/me")
            req.add_header("Authorization", f"Basic {token}")
            # Local probe — don't fail on the mkcert cert not being in Python's
            # trust store (it's a connectivity/auth check, not a security gate).
            ctx = _noverify if url.startswith("https") else None
            try:
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
            except Exception:
                return 0

        # Probe BOTH the canonical site URL and localhost:port. A custom domain
        # makes WP redirect localhost→https://<domain>, which can drop the auth
        # header (→ 403). Auth is fine if EITHER endpoint accepts it. Retry once
        # to ride out the brief window right after the password is provisioned.
        urls = []
        canon = site_url(resolve_instances(cfg).get(inst, {}))
        if canon:
            urls.append(canon)
        urls.append(f"http://localhost:{port}")
        status = 0
        for attempt in range(2):
            for u in urls:
                status = _rest_probe(u)
                if 200 <= status < 300:
                    break
            if 200 <= status < 300:
                break
            _t.sleep(1.5)
        check(f"REST auth works (status {status})", 200 <= status < 300,
              hint=f"rerun ./sb install --instance {inst} to refresh the password")
    check("wp-mcp venv built", (MCP_VENV / "bin" / "python").exists(),
          hint="./sb mcp-install")
    mcp_ok, mcp_detail = _probe_mcp_server()
    mcp_hint = "./sb mcp-install, then restart the MCP client"
    if mcp_detail:
        mcp_hint += f" ({mcp_detail})"
    check("MCP server importable", mcp_ok,
          hint=mcp_hint)

    section("State")
    # Per-project model: the instance maps to a project root in the registry.
    proj_root = owner.get("root") if owner else None
    check(f"project: {proj_root or '—'}", True,
          hint="cd into a plugin repo and run `./sb ensure` to register one")
    if proj_root and _project_declares_plugin_check(proj_root):
        installed = wpcli(["plugin", "is-installed", "plugin-check"],
                          instance=inst, check=False, capture=True)
        active = wpcli(["plugin", "is-active", "plugin-check"],
                       instance=inst, check=False, capture=True)
        check("Plugin Check available", installed.returncode == 0 and active.returncode == 0,
              hint=f"./sb apply --project-dir {proj_root} or remove plugin-check from project config")
    ff = focus_file(inst)
    focus = ff.read_text().strip() if ff.exists() else None
    check(f"focused plugin: {focus or '—'}", True,
          hint="(optional — set with ./sb focus <slug>)")

    section("Domains / proxy")
    from sandbox.core._domains import proxy_health_checks
    proxy_checks = proxy_health_checks(cfg)
    if not proxy_checks:
        note("  (no proxy-managed domains)")
    for pc in proxy_checks:
        check(pc["label"], pc["ok"], hint=pc["hint"])

    from sandbox.core._remote import list_remotes, remote_doctor_checks
    remotes = list_remotes()
    section("Remote targets")
    if not remotes:
        note("  (none configured)")
    for name, remote in sorted(remotes.items()):
        for remote_check in remote_doctor_checks(remote):
            check(f"{name}: {remote_check['label']}", remote_check["ok"],
                  hint=remote_check["hint"])

    section("Storage pressure")
    for label, pressure_ok, pressure_hint in _storage_pressure_doctor_checks():
        check(label, pressure_ok, hint=pressure_hint)

    section("Linked plugins")
    plug_dir = plugins_dir(inst)
    if plug_dir.exists():
        linked = []
        for entry in sorted(plug_dir.iterdir()):
            if entry.is_symlink():
                linked.append(entry.name)
                tgt = entry.resolve()
                check(f"{entry.name} → {tgt}", tgt.exists(),
                      hint="source missing — re-run `./sb ensure` for the project")
        if not linked:
            note("  (no source-symlinked plugins)")

    section("Credentials")
    local = _local_yaml()
    # FluentBoards — optional but warn if URL saved but unreachable.
    fb = local.get("fluentboards", {}) or {}
    fb_url = fb.get("url", "").rstrip("/")
    if fb_url:
        import urllib.request as _ur, urllib.error as _ue, ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        try:
            req = _ur.Request(fb_url, method="HEAD")
            with _ur.urlopen(req, timeout=5, context=_ctx):
                fb_ok = True
        except Exception:
            fb_ok = False
        check(f"FluentBoards reachable ({fb_url})", fb_ok,
              hint="check the URL in sandbox.local.yml or re-run `./sb connect fb`")
    else:
        note("  FluentBoards not configured (optional — ./sb connect fb)")

    # GitHub org
    gh_org = (local.get("defaults", {}) or {}).get("github_org", "").strip()
    check("github_org set", bool(gh_org),
          hint="run `./sb connect gh` to pick your org (WPDevelopers for team members)")

    # .env.local exists and is chmod 600
    if SECRETS_ENV.exists():
        mode = oct(SECRETS_ENV.stat().st_mode)[-3:]
        check(f".env.local permissions ({mode})", mode == "600",
              hint=f"run: chmod 600 {SECRETS_ENV}")
    else:
        note("  .env.local not yet created (run `./sb connect` to populate)")

    if json_mode:
        payload = {"ok": problems == 0, "exit_code": 0 if problems == 0 else 1,
                   "instance": inst, "checks": checks}
        if extension_data is not None:
            payload["php_extensions"] = extension_data
        print(json.dumps(_public_status_json(payload), sort_keys=True, default=str))
        if problems and not report_only:
            raise SystemExit(1)
        return
    print()
    if problems:
        info(f"{problems} issue(s) — see → hints above")
        sys.exit(1)
    ok("All checks passed.")

def cmd_smoke(cfg, args) -> None:
    """Self-test: boot a temporary instance, verify WP + REST, tear down.

    Validates the full create-install-probe cycle without touching any
    project instance. Takes ~60s on a cold Docker image, ~20s warm.
    """
    import tempfile, time as _t, urllib.request as _ur, urllib.error as _ue

    print("\nSandbox smoke test")
    print("══════════════════")
    problems = 0

    def _check(label: str, ok_: bool, hint: str = "") -> None:
        nonlocal problems
        mark = "✓" if ok_ else "✗"
        line = f"  {mark} {label}"
        if not ok_:
            problems += 1
            if hint:
                line += f"\n      → {hint}"
        print(line)

    # 1. Boot a fresh instance from a throw-away project dir under $HOME
    #    (the project-root allowlist rejects system temp dirs like /var/folders).
    smoke_base = Path.home() / ".sandbox" / "smoke"
    smoke_base.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="sb-smoke-", dir=smoke_base))
    import json as _json
    (tmpdir / "sandbox.config.json").write_text(
        _json.dumps({"slug": "smoke-test", "plugins": []}))

    print(f"\nTemp project dir: {tmpdir}")
    print("\nBoot:")
    t0 = _t.time()
    try:
        entry = ensure_instance(cfg, str(tmpdir))
        inst = entry["instance"]
        port = entry["wordpress_port"]
        elapsed = _t.time() - t0
        _check(f"instance '{inst}' booted ({elapsed:.0f}s)", True)
    except Exception as exc:
        _check("boot", False, str(exc))
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.exit(1)

    print("\nWordPress:")
    r = wpcli(["core", "is-installed"], instance=inst, check=False, capture=True)
    _check("WP installed", r.returncode == 0,
           hint="check `./sb logs` for install errors")

    print("\nREST:")
    rest_url = f"http://localhost:{port}/wp-json/"
    try:
        with _ur.urlopen(rest_url, timeout=8) as resp:
            rest_ok = resp.status == 200
    except Exception:
        rest_ok = False
    _check(f"GET {rest_url}", rest_ok,
           hint="check pretty-permalinks / AllowOverride in the container")

    print("\nTeardown:")
    try:
        compose("down", "-v", instance=inst, check=False)
        for path in (wp_dir(inst), snapshots_dir(inst)):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        for path in (focus_file(inst), active_project_file(inst), compose_file(inst)):
            if path.exists():
                path.unlink()
        local = _local_yaml()
        if inst in (local.get("instances") or {}):
            del local["instances"][inst]
            if not local["instances"]:
                del local["instances"]
            _write_local_yaml(local)
        _core().registry_remove(str(tmpdir))
        shutil.rmtree(tmpdir, ignore_errors=True)
        _check(f"instance '{inst}' deleted", True)
    except Exception as exc:
        _check("cleanup", False, str(exc))

    print()
    if problems:
        die(f"{problems} check(s) failed — smoke test red")
    ok("Smoke test green.")

def cmd_update(cfg, args) -> None:
    """`git pull --ff-only` the project repo this instance tracks (per-project
    model: the registry maps the instance to a project root, which IS the
    plugin's git checkout)."""
    inst = args.resolved_instance
    owner = _core().registry_find_instance(inst)
    root = owner.get("root") if owner else None
    if not root:
        # Graceful (like cmd_status/cmd_doctor) — `main` is never registered,
        # and bare `./sb update` resolves to main.
        info(f"no project registered for instance '{inst}' — cd into a plugin "
             f"repo and run `./sb ensure` first, or pass --instance <project>.")
        return
    src = Path(root)
    if not (src / ".git").exists():
        info(f"{src} is not a git repo — nothing to pull")
        return
    print(f"▸ {src.name}  ({src})")
    run(["git", "-C", str(src), "pull", "--ff-only"], check=False)
    ok("Done.")

def cmd_open(cfg, args) -> None:
    owner = _core().registry_find_instance(args.resolved_instance)
    if owner and owner.get("kind") == "compose":
        if args.what == "mail":
            die("generic Compose instances do not provide a mailpit capability")
        url = owner.get("url") or f"http://127.0.0.1:{owner.get('http_port')}"
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        run([opener, url], check=False)
        return
    inst_cfg = resolve_instances(cfg)[args.resolved_instance]
    mailpit = inst_cfg["mailpit_port"]

    base_url = site_url(inst_cfg)
    if owner:
        admin_url = owner.get("login_url") or owner.get("admin_url") or f"{base_url}/wp-admin/"
        site_url_val = owner.get("url") or base_url
    else:
        admin_url = f"{base_url}/wp-admin/"
        site_url_val = base_url

    targets = {
        "admin": admin_url,
        "site": site_url_val,
        "mail": f"http://localhost:{mailpit}",
    }
    url = targets.get(args.what or "admin")
    if not url:
        die(f"unknown target '{args.what}'. Try: admin | site | mail")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    run([opener, url], check=False)


def configure_parser(sub) -> None:
    """Register the lifecycle command parsers owned by this module.

    The CLI keeps a compatibility bridge for commands whose historical
    parsers have not moved yet.  Lifecycle is the first touched surface to
    own both its handlers and parser definitions, so future lifecycle flags
    do not grow the central composition root.
    """
    up = sub.add_parser("up", help="Boot the docker stack")
    up.add_argument(
        "--json", action="store_true",
        help="emit one machine-readable success envelope",
    )
    sub.add_parser("down", help="Stop the stack")
    status = sub.add_parser("status", help="Show container + project status")
    status.add_argument(
        "--refresh", action="store_true",
        help="force a fresh runtime observation (never use a cached snapshot)",
    )
    status.add_argument(
        "--stats", action="store_true",
        help="include a bounded point-in-time CPU, memory, and PID snapshot (local only)",
    )
    logs = sub.add_parser("logs", help="Read a bounded WP + DB log snapshot")
    for parser in (status, logs):
        parser.add_argument("--project-dir", default=None)
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--local", action="store_true")
        target.add_argument("--remote")
        parser.add_argument("--workspace")
        parser.add_argument("--json", action="store_true")
    logs.add_argument("--lines", "--tail", dest="lines", type=int, default=200,
                      help="number of recent log lines (1-1000; --tail is an alias; default 200)")
    logs.add_argument("--since", default=None,
                      help="only logs since an RFC 3339 timestamp or Unix seconds")
    logs.add_argument("--follow", action="store_true",
                      help="keep streaming after the bounded initial snapshot")
    sub.add_parser("shell", help="Bash into the WP container")
    sub.add_parser("install", help="Install WP + create admin user")
    doctor = sub.add_parser(
        "doctor",
        help="Audit one local instance plus controller health (use --instance/--label)",
        epilog=(
            "Doctor runs on the local controller. It supports --instance, --label, and "
            "--json; it does not accept --project-dir, --local, or --remote. For a "
            "specific project, run from that directory or resolve its instance with "
            "`sb instances --project-dir DIR --json` and pass --instance NAME."
        ),
    )
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--report-only", action="store_true",
        help="with --json, keep completed findings in the document but exit 0; preflight failures still fail",
    )
    sub.add_parser("smoke", help="Self-test: boot a fresh instance, REST probe, tear down")
    sub.add_parser("update", help="git pull the project repo this instance tracks")
    op = sub.add_parser("open", help="Open admin / site / mailpit in browser")
    op.add_argument("what", nargs="?", default="admin",
                    choices=["admin", "site", "mail"])

register({
    'up': cmd_up,
    'down': cmd_down,
    'status': cmd_status,
    'logs': cmd_logs,
    'shell': cmd_shell,
    'install': cmd_install,
    'smoke': cmd_smoke,
    'doctor': cmd_doctor,
    'update': cmd_update,
    'open': cmd_open,
})
