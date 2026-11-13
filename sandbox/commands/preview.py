"""Disposable public remote Sandbox previews for WordPress projects."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sandbox.core import die, ok
import sandbox.core as core
from sandbox.registry import CommandSpec, register_specs
from sandbox.application.context import preflight_project_capability
from sandbox.core._paths import RUNTIME_DIR
import sandbox.core._cloudflare as cloudflare
import sandbox.core._remote as remote
from sandbox.delivery.exposure import ExposureAttempt, ExposureReplay
from sandbox.delivery.context import source_commit, registered_host_digest
from sandbox.delivery.routes import prepare_route_verification, observe_routes
from sandbox.hosting.server_cli_target import is_owner_target, can_operate_host, push_host_commits
from sandbox.commands.preview_server import (dispatch_preview_if_selected, preview_target,
    preview_registry_lock, PreviewProvider, prepare_preview_source)


_STATE_PATH = RUNTIME_DIR / "remote-previews.json"
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"version": 1, "previews": {}}
    try:
        state = json.loads(_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid remote preview state: {exc}") from exc
    if state.get("version") != 1 or not isinstance(state.get("previews"), dict):
        raise ValueError("invalid remote preview state")
    return state


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(_STATE_PATH)
    _STATE_PATH.chmod(0o600)


def preview_identity(project_root: str, branch: str, name: str | None = None) -> tuple[str, str]:
    """Return a DNS-safe public id and Sandbox label, scoped to this project/branch."""
    raw = (name or branch or "preview").lower()
    stem = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:20] or "preview"
    digest = hashlib.sha256(f"{Path(project_root).resolve()}:{branch}:{name or ''}".encode()).hexdigest()[:8]
    return f"{stem}-{digest}", f"preview-{digest}"


def preview_domain(preview_id: str, project_slug: str, base_domain: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", project_slug.lower()).strip("-") or "project"
    base = base_domain.strip().strip(".").lower()
    if not _NAME_RE.fullmatch(preview_id) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+[a-z0-9]", base):
        raise ValueError("invalid preview name or base domain")
    return f"{preview_id[:31]}-{slug[:30]}.{base}"


def _ready_message(row: dict, label: str) -> str:
    return (
        f"remote preview ready: {row['url']} "
        f"(instance {row['instance']}, label {label}, expires {row['expires_at']})"
    )


def _zone_for_hostname(client, hostname: str) -> dict:
    labels = hostname.split(".")
    errors = []
    for offset in range(len(labels) - 1):
        try:
            return client.zone(".".join(labels[offset:]))
        except cloudflare.CloudflareError as exc:
            errors.append(str(exc))
    raise cloudflare.CloudflareError(errors[-1] if errors else f"no Cloudflare zone found for {hostname}")


_DNS_CUSTODY_FIELDS = ('id', 'name', 'type', 'content', 'proxied', 'comment')


def _verify_dns(client, zone_id, domain, expected):
    rows = client.records(zone_id, domain)
    record = next((row for row in rows if row.get('id') == expected.get('id')), None)
    if record is None or any(record.get(key) != expected.get(key) for key in _DNS_CUSTODY_FIELDS):
        raise ValueError('preview_dns_custody_mismatch')


def _destroy(entry, state: dict, preview_id: str) -> None:
    record = state['previews'].get(preview_id)
    if not record:
        raise ValueError(f"unknown remote preview '{preview_id}'")
    from sandbox.server_config.models import validate_creation_context, validate_creation_receipt, creation_digest
    alias = entry.remote_name if is_owner_target(entry) else entry.get('_remote_name')
    if (not alias or record.get('remote') != alias
            or record.get('host_identity') != registered_host_digest(entry, alias)):
        raise ValueError('preview_owner_alias_or_machine_mismatch')
    # Legacy rows remain visible but are not upgraded into deletion grants.
    context = validate_creation_context(record.get('creation_context'))
    receipt = validate_creation_receipt(record.get('creation_receipt'))
    target = record.get('target_path')
    if (not isinstance(target, str) or not Path(target).is_absolute()
            or context['project_root_digest'] != creation_digest(str(Path(target).resolve()))
            or receipt['instance_id'] != record.get('instance')
            or receipt['relation'] != 'created' or receipt['completion'] != 'succeeded'
            or record.get('delete_phase') in {'unknown', 'route_unknown', 'dns_unknown'}):
        raise ValueError('preview_custody_unavailable_or_delete_unknown')
    if is_owner_target(entry):
        current = entry.require_current()
        base = Path(current.owner.sandbox_home) / 'deploy-src' / 'hosts'
        relative = Path(target).relative_to(base)
        if len(relative.parts) != 1 or not relative.name.startswith('preview-'):
            raise ValueError('preview_source_destination_mismatch')
    port = record.get('route_port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('preview_route_custody_unavailable')
    client = PreviewProvider(entry)
    _verify_dns(client, record['zone_id'], record['domain'], record.get('dns_record') or {})
    if record.get('delete_phase') != 'route_deleted':
        remote.verify_instance_https_route(entry, record['domain'], port,
            incarnation=receipt['instance_incarnation_id'])
    if record.get('delete_phase') not in {'instance_deleted', 'route_deleted'}:
        proof = remote.read_remote_creation_receipt(entry, target, context['label'],
            creation_context=context, expected_incarnation=receipt['instance_incarnation_id'])
        if proof.get('ok') is not True or proof.get('creation_receipt') != receipt:
            raise ValueError('preview_instance_custody_mismatch')
        # Persist before dispatch. A lost response can never trigger a blind retry.
        record['delete_phase'] = 'unknown'
        _save_state(state)
        remote.delete_remote_instance(entry, record['instance'], creation_context=context,
            expected_incarnation=receipt['instance_incarnation_id'])
        record['delete_phase'] = 'instance_deleted'
        _save_state(state)
    if record.get('delete_phase') != 'route_deleted':
        record['delete_phase'] = 'route_unknown'
        _save_state(state)
        remote.remove_instance_https_route(entry, record['domain'], expected_port=port,
            incarnation=receipt['instance_incarnation_id'])
        record['delete_phase'] = 'route_deleted'
        _save_state(state)
    # Reobserve provider identity after runtime/route teardown, before DNS deletion.
    _verify_dns(client, record['zone_id'], record['domain'], record['dns_record'])
    record['delete_phase'] = 'dns_unknown'
    _save_state(state)
    client.delete_record(record['zone_id'], record['dns_record_id'])
    del state['previews'][preview_id]
    _save_state(state)


# What `preview list` may print. A field added to a saved row stays private until named here.
_PUBLIC_ROW_FIELDS = ("id", "remote", "project_root", "instance", "domain",
                      "zone_id", "dns_record_id", "expires_at")


def _public_row(row: dict) -> dict:
    public = {key: row[key] for key in _PUBLIC_ROW_FIELDS if key in row}
    # A preview's address is always https://<domain> (see create). Rebuilding it means whatever
    # the saved url holds, a path, a query or a malformed value, is neither printed nor parsed.
    domain = row.get("domain")
    if isinstance(domain, str) and domain:
        public["url"] = f"https://{domain}"
    return public


def _cmd_preview(cfg, args) -> None:
    action = args.action
    state = _load_state()
    if action == "list":
        rows = sorted((row for row in state["previews"].values()
                       if not args.remote or row.get("remote") == args.remote),
                      key=lambda row: str(row.get("expires_at") or ""))
        rows = [_public_row(row) for row in rows]
        if args.json:
            print(json.dumps({"ok": True, "previews": rows}))
        else:
            for row in rows:
                print(f"{row['id']}  {row.get('url', '')}  expires {row.get('expires_at', 'unknown')}")
        return
    if action in {"destroy", "cleanup"} and not args.confirm:
        die("remote preview deletion requires --confirm")
    if action == "destroy":
        if not args.remote or not args.id:
            die("preview destroy requires --remote and --id")
        entry = preview_target(args.remote)
        if not can_operate_host(entry):
            die("a provisioned --remote is required")
        try:
            _destroy(entry, state, args.id)
        except (RuntimeError, ValueError) as exc:
            die(str(exc))
        ok(f"destroyed remote preview '{args.id}'")
        return
    if action == "cleanup":
        if not args.remote:
            die("preview cleanup requires --remote")
        now = datetime.now(timezone.utc)
        entries = [row for row in state["previews"].values()
                   if row["remote"] == args.remote and datetime.fromisoformat(row["expires_at"]) <= now]
        entry = preview_target(args.remote)
        if not can_operate_host(entry):
            die("a provisioned --remote is required")
        for row in entries:
            _destroy(entry, state, row["id"])
        ok(f"removed {len(entries)} expired remote preview(s)")
        return

    # create: an explicit confirmation is required because it deploys code,
    # creates a VPS container, and writes one Cloudflare DNS record.
    if not args.confirm:
        die("remote preview creation requires --confirm")
    if args.ttl_hours < 1 or args.ttl_hours > 168:
        die("--ttl-hours must be between 1 and 168")
    if not args.remote:
        die("preview create requires --remote")
    entry = preview_target(args.remote)
    if not can_operate_host(entry):
        die("a provisioned --remote is required")
    if not entry.get("origin_ipv4"):
        die("remote has no origin IPv4; run `./sb remote set-origin`")
    from sandbox.hosting.front_door import FrontDoorError, require_capability
    try:
        require_capability(entry, "preview_routes")
    except FrontDoorError as exc:
        die(str(exc))
    record = None
    instance = None
    target = None
    label = None
    domain = None
    zone = None
    route_configured = False
    attempt = None
    delivery_summary = None
    route_scope = None
    dns_scope = None
    try:
        project = core._core().load_project_config(args.project_dir or os.getcwd())
        root = Path(project["root"])
        capability_error = preflight_project_capability(
            cfg, str(root), "wordpress.remote-preview"
        )
        if capability_error is not None:
            raise ValueError(capability_error.message)
        branch = remote.current_branch(root, allow_detached=is_owner_target(entry)) or args.name or 'preview'
        preview_id, label = preview_identity(str(root), branch, args.name)
        if preview_id in state["previews"]:
            die(f"remote preview '{preview_id}' already exists")
        domain = preview_domain(preview_id, remote.deploy_target_slug(root), args.base_domain)
        commit = source_commit(root)
        prepared_routes = prepare_route_verification(project.get('delivery'),
            runtime_kind='wordpress', primary_hostname=domain,
            application_commit=commit, verify_timeout=getattr(args, 'verify_timeout', None))
        supported = remote.remote_creation_capability(entry, '.', label, kind='wordpress', runtime_mode='compose')
        if supported.get('ok') is not True:
            raise ValueError('unsupported_creation_capability')
        dirty, untracked = remote.capture_uncommitted(root)
        untracked = list(dict.fromkeys([
            *untracked, *remote.deploy_project_descriptor_files(root),
        ]))
        snapshot = remote.snapshot_dirty_overlay(root, dirty, untracked,
            max_files=remote.DEPLOY_SNAPSHOT_MAX_FILES, max_bytes=remote.DEPLOY_SNAPSHOT_MAX_BYTES)
        attempt = ExposureAttempt(project, args.remote, entry, label=label,
            kind='preview_creation', commit=commit, dirty_digest=snapshot['identity'],
            prepared=prepared_routes, request_id=getattr(args, 'request_id', None))
        attempt.save(phase='source')
        attempt.effect('source_publish', {'commit': commit}, 'unknown')
        target = prepare_preview_source(entry, root, commit)
        sha = push_host_commits(entry, root, target, branch, resolved_sha=commit,
            allow_detached=is_owner_target(entry))
        remote.update_target_to(entry, target, sha, project_root=root,
            diff_text=dirty, untracked=untracked, overlay_snapshot=snapshot)
        attempt.effect('source_publish', {'commit': commit})
        instance = attempt.ensure(entry, target, label=label, transport=remote)
        if attempt.receipt['relation'] != 'created':
            raise ValueError('preview_requires_original_created_instance')
        reconciled = remote.reconcile_remote_instance(entry, target, label,
            creation_context=attempt.context, expected_incarnation=attempt.receipt['instance_incarnation_id'])
        if reconciled.get('instance') != instance.get('instance'):
            raise RuntimeError('instance_incarnation_changed')
        instance.update(reconciled)
        remote.activate_remote_plugin(entry, target, instance["instance"], project.get("slug") or remote.deploy_target_slug(root))
        domain = preview_domain(preview_id, remote.deploy_target_slug(root), args.base_domain)
        client = PreviewProvider(entry)
        zone = _zone_for_hostname(client, domain)
        if client.records(zone["id"], domain):
            raise ValueError(f"refusing to reuse existing DNS hostname {domain}")
        attempt.evidence('runtime')
        dns_scope = {'hostname': domain, 'zone_id': zone['id']}
        attempt.effect('preview_dns', dns_scope, 'unknown')
        record = client.create_address(zone["id"], domain, entry["origin_ipv4"],
            comment='Sandbox preview ' + attempt.receipt['instance_incarnation_id'])
        observed_dns = client.records(zone['id'], domain)
        if len(observed_dns) != 1 or observed_dns[0].get('id') != record.get('id'):
            raise ValueError('preview_dns_custody_mismatch')
        attempt.effect('preview_dns', dns_scope)
        route_scope = {'hostname': domain, 'incarnation': attempt.receipt['instance_incarnation_id']}
        attempt.effect('route_primary', route_scope, 'unknown')
        remote.configure_instance_https_route(entry, domain, int(instance["wordpress_port"]),
            incarnation=attempt.receipt["instance_incarnation_id"])
        route_configured = True
        remote.verify_instance_https_route(entry, domain, int(instance['wordpress_port']),
            incarnation=attempt.receipt['instance_incarnation_id'])
        attempt.effect('route_primary', route_scope)
        url = f"https://{domain}"
        url_result = remote.set_remote_instance_url(entry, target, instance['instance'], url,
            label=label, creation_context=attempt.context,
            expected_incarnation=attempt.receipt['instance_incarnation_id'])
        for option, status in url_result['writes'].items():
            attempt.effect('wordpress_url_' + option, {'incarnation': attempt.receipt['instance_incarnation_id']},
                           'configured' if status == 'succeeded' else 'unknown')
        if url_result['result_code'] != 'remote_instance_url_verified':
            raise ValueError('remote_instance_url_incomplete')
        route_result = observe_routes(prepared_routes)
        attempt.evidence('routes', result='passed' if route_result['result'] == 'verified' else 'failed',
                         state='partial' if route_result['result'] == 'incomplete' else 'known',
                         route_observation=route_result)
        if route_result['result'] != 'verified':
            raise ValueError('delivery_exposure_unverified')
        login_url = remote.rewrite_instance_url(instance.get("login_url"), url) if instance.get("login_url") else ""
    except ExposureReplay as replay:
        payload = replay.response()
        print(json.dumps(payload, sort_keys=True) if args.json else
              'Original delivery ' + replay.operation_id + ': ' + replay.status)
        if not payload['ok']:
            raise SystemExit(1)
        return
    except (RuntimeError, ValueError, cloudflare.CloudflareError, KeyError, OSError, subprocess.SubprocessError) as exc:
        # Best-effort rollback is deliberately restricted to resources whose
        # generated identifiers are known, never broad DNS or Caddy cleanup.
        if route_configured and domain:
            try:
                remote.remove_instance_https_route(entry, domain,
                    expected_port=int(instance['wordpress_port']),
                    incarnation=attempt.receipt['instance_incarnation_id'])
                attempt.effect('route_primary', route_scope, 'removed')
            except (RuntimeError, ValueError, OSError):
                try:
                    attempt.effect('route_primary', route_scope, 'failed')
                except (RuntimeError, ValueError, OSError):
                    pass
        if record and zone:
            try:
                _verify_dns(client, zone['id'], domain, record)
                client.delete_record(zone["id"], record["id"])
                attempt.effect('preview_dns', dns_scope, 'removed')
            except (cloudflare.CloudflareError, ValueError, OSError):
                try:
                    attempt.effect('preview_dns', dns_scope, 'failed')
                except (RuntimeError, ValueError, OSError):
                    pass
        # Ensure can reuse an existing labelled instance, or lose its response
        # after creating one. Neither result gives this caller deletion authority.
        # Keep its data and identity available for the next explicit operation.
        if attempt is not None:
            try:
                delivery_summary = attempt.finish(False, phase=attempt.operation['phase'])
            except (RuntimeError, ValueError, OSError):
                delivery_summary = {'operation_id': attempt.operation['operation_id'],
                                    'reason': 'delivery_record_incomplete', 'delivery_succeeded': False}
        identity = {
            "label": label,
            "instance": instance.get("instance") if isinstance(instance, dict) else None,
        }
        message = (
            "remote preview create failed "
            f"(label={identity['label'] or 'unknown'}, "
            f"instance={identity['instance'] or 'not-returned'}): {exc}; "
            "instance state retained for explicit reconciliation"
        )
        if args.json:
            print(json.dumps({"ok": False, "preview": identity, "error": message,
                              "delivery": delivery_summary,
                              "effects": attempt.operation['effects'] if attempt else []}))
            raise SystemExit(1)
        die(message)
    expiry = datetime.now(timezone.utc) + timedelta(hours=args.ttl_hours)
    row = {"id": preview_id, "remote": args.remote, "project_root": str(root),
           "instance": instance["instance"], "domain": domain, "url": url,
           "login_url": login_url,
           "zone_id": zone["id"], "dns_record_id": record["id"],
           "expires_at": expiry.isoformat(),
           "target_path": target, "source_commit": commit,
           "creation_context": attempt.context, "creation_receipt": attempt.receipt,
           "host_identity": registered_host_digest(entry, args.remote),
           "route_port": int(instance['wordpress_port']),
           "dns_record": {key: record.get(key) for key in _DNS_CUSTODY_FIELDS}}
    state["previews"][preview_id] = row
    try:
        _save_state(state)
        delivery_summary = attempt.finish(True)
    except (RuntimeError, ValueError, OSError):
        # Runtime/edge may already be usable; an unretained result is not success.
        if args.json:
            print(json.dumps({'ok': False, 'error': 'delivery_record_incomplete',
                              'operation_id': attempt.operation['operation_id'],
                              'effects': attempt.operation['effects']}))
            raise SystemExit(1)
        die('delivery_record_incomplete; inspect the original delivery operation')
    if not delivery_summary['delivery_succeeded']:
        die('delivery_evidence_incomplete')
    if args.json:
        print(json.dumps({"ok": True, "preview": row, "delivery": delivery_summary}))
    else:
        ok(_ready_message(row, label))


def cmd_preview(cfg, args):
    if getattr(args, 'remote', None):
        # Validate selected owner even for inventory; never read controller state
        # after a retained alias/owner failure.
        preview_target(args.remote)
    with preview_registry_lock():
        return _cmd_preview(cfg, args)


register_specs([CommandSpec('preview', cmd_preview, owner=__name__, scope='global',
    legacy_id='preview', preconfiguration_dispatch=dispatch_preview_if_selected)])
