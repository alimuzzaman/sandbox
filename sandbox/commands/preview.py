"""Disposable public remote Sandbox previews for WordPress projects."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sandbox.core import die, ok
import sandbox.core as core
from sandbox.registry import register
from sandbox.application.context import preflight_project_capability
from sandbox.core._paths import RUNTIME_DIR
import sandbox.core._cloudflare as cloudflare
import sandbox.core._remote as remote


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


def _zone_for_hostname(client, hostname: str) -> dict:
    labels = hostname.split(".")
    errors = []
    for offset in range(len(labels) - 1):
        try:
            return client.zone(".".join(labels[offset:]))
        except cloudflare.CloudflareError as exc:
            errors.append(str(exc))
    raise cloudflare.CloudflareError(errors[-1] if errors else f"no Cloudflare zone found for {hostname}")


def _destroy(entry: dict, state: dict, preview_id: str) -> None:
    record = state["previews"].get(preview_id)
    if not record:
        raise ValueError(f"unknown remote preview '{preview_id}'")
    errors = []
    try:
        remote.remove_instance_https_route(entry, record["domain"])
    except RuntimeError as exc:
        errors.append(str(exc))
    try:
        remote.delete_remote_instance(entry, record["instance"])
    except RuntimeError as exc:
        errors.append(str(exc))
    try:
        cloudflare.Client().delete_record(record["zone_id"], record["dns_record_id"])
    except cloudflare.CloudflareError as exc:
        errors.append(str(exc))
    if errors:
        raise RuntimeError("; ".join(errors))
    del state["previews"][preview_id]
    _save_state(state)


def cmd_preview(cfg, args) -> None:
    action = args.action
    state = _load_state()
    if action == "list":
        rows = sorted(state["previews"].values(), key=lambda row: row["expires_at"])
        if args.json:
            print(json.dumps({"ok": True, "previews": rows}))
        else:
            for row in rows:
                print(f"{row['id']}  {row['url']}  expires {row['expires_at']}")
        return
    if action in {"destroy", "cleanup"} and not args.confirm:
        die("remote preview deletion requires --confirm")
    if action == "destroy":
        if not args.remote or not args.id:
            die("preview destroy requires --remote and --id")
        entry = remote.get_remote(args.remote)
        if not entry or not entry.get("provisioned"):
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
        entry = remote.get_remote(args.remote)
        if not entry or not entry.get("provisioned"):
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
    entry = remote.get_remote(args.remote)
    if not entry or not entry.get("provisioned"):
        die("a provisioned --remote is required")
    if not entry.get("origin_ipv4"):
        die("remote has no origin IPv4; run `./sb remote set-origin`")
    record = None
    instance = None
    target = None
    label = None
    domain = None
    zone = None
    route_configured = False
    try:
        project = core._core().load_project_config(args.project_dir or os.getcwd())
        root = Path(project["root"])
        capability_error = preflight_project_capability(
            cfg, str(root), "wordpress.remote-preview"
        )
        if capability_error is not None:
            raise ValueError(capability_error.message)
        branch = remote.current_branch(root)
        preview_id, label = preview_identity(str(root), branch, args.name)
        if preview_id in state["previews"]:
            die(f"remote preview '{preview_id}' already exists")
        target = remote.ensure_deploy_repo(entry, root)
        sha = remote.push_commits(entry, root, target, branch)
        remote.reset_target_to(entry, target, sha)
        dirty, untracked = remote.capture_uncommitted(root)
        remote.apply_uncommitted(entry, target, root, dirty, untracked)
        instance = remote.ensure_remote_instance(entry, target, label)
        remote.activate_remote_plugin(entry, target, instance["instance"], project.get("slug") or remote.deploy_target_slug(root))
        domain = preview_domain(preview_id, remote.deploy_target_slug(root), args.base_domain)
        client = cloudflare.Client()
        zone = _zone_for_hostname(client, domain)
        if client.records(zone["id"], domain):
            raise ValueError(f"refusing to reuse existing DNS hostname {domain}")
        record = client.upsert_address(zone["id"], domain, entry["origin_ipv4"])
        remote.configure_instance_https_route(entry, domain, int(instance["wordpress_port"]))
        route_configured = True
        url = f"https://{domain}"
        remote.set_remote_instance_url(entry, target, url)
        login_url = remote.rewrite_instance_url(instance.get("login_url"), url) if instance.get("login_url") else ""
    except (RuntimeError, ValueError, cloudflare.CloudflareError, KeyError) as exc:
        # Best-effort rollback is deliberately restricted to resources whose
        # generated identifiers are known, never broad DNS or Caddy cleanup.
        if route_configured and domain:
            try:
                remote.remove_instance_https_route(entry, domain)
            except RuntimeError:
                pass
        if record and zone:
            try:
                client.delete_record(zone["id"], record["id"])
            except cloudflare.CloudflareError:
                pass
        if instance:
            try:
                remote.delete_remote_instance(entry, instance["instance"])
            except (RuntimeError, KeyError):
                pass
        elif target and label:
            try:
                remote.delete_remote_instance_for_label(entry, target, label)
            except (RuntimeError, ValueError):
                pass
        die(str(exc))
    expiry = datetime.now(timezone.utc) + timedelta(hours=args.ttl_hours)
    row = {"id": preview_id, "remote": args.remote, "project_root": str(root),
           "instance": instance["instance"], "domain": domain, "url": url,
           "login_url": login_url,
           "zone_id": zone["id"], "dns_record_id": record["id"],
           "expires_at": expiry.isoformat()}
    state["previews"][preview_id] = row
    _save_state(state)
    if args.json:
        print(json.dumps({"ok": True, "preview": row}))
    else:
        ok(f"remote preview ready: {url} (expires {row['expires_at']})")


register({"preview": cmd_preview})
