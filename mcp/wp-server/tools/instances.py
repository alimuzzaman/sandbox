from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import *  # noqa: F401,F403



@mcp.tool()
def ensure_instance(project_dir: str) -> dict:
    """Ensure a sandbox WordPress instance exists for `project_dir`, creating it
    on demand, and return {ok, instance, url, ports, status, root, source}.

    project_dir: the plugin's project root (or your cwd). The server reads its
    sandbox.config.* / .wp-env.json, boots an instance keyed by that directory
    (one per worktree), installs WordPress, wires the plugin, and records it.
    **Call this FIRST** — other tools error until an instance exists. Idempotent:
    a ready project returns instantly; a cold boot pulls images + installs WP and
    can take ~1 minute.

    When the clean-URL proxy is already set up (see setup_domains), a fresh
    single-site instance is SECURED AT CREATE — installed directly at its trusted
    https://<name>.<tld> URL (no http-first), and `url` reflects that. Otherwise
    it falls back to http://localhost:<port>.
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "ensure", "--project-dir", project_dir, "--json"],
            capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ensure_instance timed out after 600s"}
    lines = (res.stdout or "").strip().splitlines()
    entry = _safe_json(lines[-1]) if lines else None
    if isinstance(entry, dict) and "instance" in entry:
        entry.setdefault("ok", True)
        return entry
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "ensure failed").strip()[:1000]}

@mcp.tool()
def destroy_instance(project_dir: str) -> dict:
    """Stop and permanently delete the sandbox instance for `project_dir`.

    Removes containers, the DB volume, the wp dir, and the registry entry.
    This is irreversible — all database data and uploads are lost. Call
    ensure_instance(project_dir=...) afterwards to recreate from scratch.

    project_dir: the plugin project whose instance to destroy.
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "instance", "delete", inst, "--yes"],
            capture_output=True, text=True, timeout=120, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "destroy_instance timed out after 120s"}
    if res.returncode == 0:
        return {"ok": True, "instance": inst,
                "message": f"Instance '{inst}' deleted. Call ensure_instance to recreate."}
    return {"ok": False, "error": (res.stderr or res.stdout or "delete failed").strip()[:1000]}

@mcp.tool()
def recreate_instance(project_dir: str) -> dict:
    """Destroy and immediately recreate the sandbox instance for `project_dir`.

    Equivalent to destroy_instance followed by ensure_instance — gives a clean
    WP install (fresh DB, fresh uploads) from the current sandbox.config.*
    without a manual two-step. The recreated instance keeps the SAME port as
    before so bookmarks and tool configs don't change.

    project_dir: the plugin project to recreate.
    """
    sc = _core()
    try:
        root = str(sc.find_project_root(project_dir))
    except Exception as e:
        return {"ok": False, "error": f"invalid project_dir {project_dir!r}: {e}"}
    existing = sc.registry_get(root)
    if not existing or not existing.get("instance"):
        return {"ok": False,
                "error": f"no sandbox instance for project '{root}'. "
                         f"Call ensure_instance(project_dir={project_dir!r}) first."}
    inst = existing["instance"]
    # Snapshot the ports + server so ensure reuses them after destroy.
    saved_ports = {k: existing[k]
                   for k in ("wordpress_port", "db_port", "mailpit_port", "server")
                   if k in existing}

    sb = SANDBOX_ROOT / "sb"
    # destroy
    try:
        res = subprocess.run(
            [str(sb), "instance", "delete", inst, "--yes"],
            capture_output=True, text=True, timeout=120, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "recreate_instance: destroy timed out after 120s"}
    if res.returncode != 0:
        return {"ok": False, "error": (res.stderr or res.stdout or "delete failed").strip()[:1000]}

    # Re-insert a pending registry entry with the same ports so ensure_instance
    # picks them up (skipping _pick_instance_ports) and the URL stays the same.
    sc.registry_put(
        root,
        instance=inst,
        status="pending",
        wordpress_port=saved_ports.get("wordpress_port"),
        db_port=saved_ports.get("db_port"),
        mailpit_port=saved_ports.get("mailpit_port"),
        server=saved_ports.get("server", "apache"),
    )

    # recreate
    try:
        res2 = subprocess.run(
            [str(sb), "ensure", "--project-dir", project_dir, "--json"],
            capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "recreate_instance: ensure timed out after 600s"}
    lines = (res2.stdout or "").strip().splitlines()
    entry = _safe_json(lines[-1]) if lines else None
    if isinstance(entry, dict) and "instance" in entry:
        entry.setdefault("ok", True)
        entry["recreated"] = True
        return entry
    return {"ok": False, "code": res2.returncode,
            "error": (res2.stderr or res2.stdout or "ensure failed after destroy").strip()[:1000]}

@mcp.tool()
def setup_domains(tld: str = "") -> dict:
    """Set up clean, trusted HTTPS for the sandbox: assign every instance a
    <name>.<tld> domain, start the Caddy proxy, mint per-instance certs, and
    switch WP to https://<name>.<tld>. Wraps `./sb domains setup [tld]`.

    This is the global one-time bring-up of the clean-URL proxy. After it runs,
    new instances are secured automatically at create (see ensure_instance).
    `tld` defaults to the project default ("tst"); a project's own `tld` config
    overrides it. NOTE: the FIRST run on a machine installs a sudoers rule + a
    local CA and needs an interactive terminal + one sudo; once set up, repeat
    runs are non-interactive. Returns {ok, code, output}.
    """
    sb = SANDBOX_ROOT / "sb"
    args = [str(sb), "domains", "setup"]
    if tld:
        args.append(tld)
    try:
        res = subprocess.run(
            args, capture_output=True, text=True, timeout=300, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "setup_domains timed out after 300s"}
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    return {"ok": res.returncode == 0, "code": res.returncode, "output": out[:2000]}

@mcp.tool()
def secure_instance(project_dir: str) -> dict:
    """Give the project's instance a trusted https://<name>.<tld> URL without a
    recreate — assigns its domain (if missing), mints the cert, wires the proxy
    TLS route, and points WP at https. Use for an instance that came up on
    localhost (e.g. created before the proxy was set up, or multisite). Wraps
    `./sb domains setup` (idempotent; only the missing pieces are added) and
    returns the instance's resulting URL.
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    sb = SANDBOX_ROOT / "sb"
    tld = (_load_sandbox_yml().get("instances", {}).get(inst, {}) or {}).get("tld") or PROXY_TLD
    try:
        res = subprocess.run(
            [str(sb), "domains", "setup", tld],
            capture_output=True, text=True, timeout=300, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "secure_instance timed out after 300s"}
    out = ((res.stdout or "") + (res.stderr or "")).strip()[:2000]
    if res.returncode != 0:
        return {"ok": False, "code": res.returncode, "error": out}
    return {"ok": True, "instance": inst, "url": _site_url(_resolve_instance(inst)),
            "output": out}

@mcp.tool()
def apply_config(project_dir: str) -> dict:
    """Reconcile a RUNNING instance with its current project config WITHOUT
    dropping the database or uploads — the non-destructive alternative to
    recreate_instance.

    Use this after editing sandbox.config.* (e.g. toggling TEMPLATELY_DEV_API /
    WP_DEBUG, adding a plugin or theme, enabling multisite). It re-renders the
    compose file, recreates only the web tier (constants survive via
    WORDPRESS_CONFIG_EXTRA), re-syncs plugin/theme symlinks + installs, and runs
    multisite-convert if multisite was newly enabled. The DB volume is untouched,
    so all data is preserved.

    Caveats: a changed wp_version is reported but NOT applied (core swaps under a
    live DB are left to an explicit recreate_instance); switching an existing
    multisite between subdirectory and subdomain also needs a recreate.

    project_dir: the plugin project to reconcile (call ensure_instance first).
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "apply", "--project-dir", project_dir, "--json"],
            capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "apply_config timed out after 600s"}
    lines = (res.stdout or "").strip().splitlines()
    entry = _safe_json(lines[-1]) if lines else None
    if isinstance(entry, dict) and "instance" in entry:
        entry.setdefault("ok", True)
        entry["reconciled"] = True
        return entry
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "apply failed").strip()[:1000]}
