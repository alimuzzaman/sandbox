from __future__ import annotations
import subprocess
from pathlib import Path

from dependencies import ToolDependencies


# Bound only by register(); importing this group must neither initialize app nor
# register tools.  The compatibility functions below retain their public names.
SANDBOX_ROOT: Path
PROXY_TLD: str
_core: object
_load_sandbox_yml: object
_project_instance: object
_resolve_instance: object
_safe_json: object
_site_url: object
_domain_service: object


def register(server, dependencies: ToolDependencies) -> None:
    """Bind the explicit instance context and register this group's tools."""
    global SANDBOX_ROOT, PROXY_TLD, _core, _load_sandbox_yml
    global _project_instance, _resolve_instance, _safe_json, _site_url, _domain_service
    SANDBOX_ROOT = dependencies.require("sandbox_root")
    PROXY_TLD = dependencies.require("proxy_tld")
    _core = dependencies.require("core")
    _load_sandbox_yml = dependencies.require("load_sandbox_yml")
    _project_instance = dependencies.require("project_instance")
    _resolve_instance = dependencies.require("resolve_instance")
    _safe_json = dependencies.require("safe_json")
    _site_url = dependencies.require("site_url")
    _domain_service = dependencies.require("domain_service")
    for tool in (
        ensure_instance, destroy_instance, recreate_instance, setup_domains,
        secure_instance, apply_config,
    ):
        server.tool()(tool)



def ensure_instance(project_dir: str, label: str = "default", create: bool = False) -> dict:
    """Ensure a configured project instance exists and return its kind-neutral record.

    WordPress projects retain their existing install/wiring behavior. Explicit
    generic Compose projects use the framework-neutral adapter and return
    kind/adapter/service/http_port/capability metadata without WordPress setup.
    **Call this FIRST** — other tools error until an instance exists. Idempotent:
    a ready (project, label) returns instantly; a cold boot pulls images +
    installs WP and can take ~1 minute.

    label: distinguishes multiple SIMULTANEOUS instances of the SAME project
    root (multi-instance-per-root) — e.g. a 'qa' label alongside 'default' to
    test a different WP/PHP version or a zip install side-by-side with dev.
    Leave as 'default' for the common single-instance case (unchanged
    behavior). Minting a brand-new NON-default label requires create=True —
    this guards against a typo'd label silently building a whole extra stack.

    When the clean-URL proxy is already set up (see setup_domains), a fresh
    single-site instance is SECURED AT CREATE — installed directly at its trusted
    https://<name>.<tld> URL (no http-first), and `url` reflects that. Otherwise
    it falls back to http://localhost:<port>.
    """
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "ensure", "--project-dir", project_dir, "--json"]
    if label and label != "default":
        cmd += ["--label", label]
        if create:
            cmd.append("--create")
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
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

def destroy_instance(project_dir: str, label: str | None = None) -> dict:
    """Stop and permanently delete the sandbox instance for `project_dir`
    (+ `label`, when the root owns more than one).

    Removes containers, the DB volume, the wp dir, and the registry entry.
    This is irreversible — all database data and uploads are lost. Call
    ensure_instance(project_dir=..., label=...) afterwards to recreate from
    scratch. Deleting one label leaves any other instance of the same root
    (e.g. its 'default') untouched and still running.

    project_dir: the plugin project whose instance to destroy.
    """
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    try:
        root = str(_core().find_project_root(project_dir))
        owner = _core().registry_get(root, label=label)
    except Exception as exc:
        return {"ok": False, "error": f"could not resolve project owner: {exc}"}
    if owner and owner.get("kind") == "compose":
        try:
            res = subprocess.run(
                [str(SANDBOX_ROOT / "sb"), "instance", "delete", inst, "--yes"],
                capture_output=True, text=True, timeout=120, cwd=str(SANDBOX_ROOT),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "generic destroy timed out after 120s"}
        return {"ok": res.returncode == 0, "instance": inst,
                "kind": "compose", "output": (res.stdout or res.stderr).strip()[:2000]}
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

def recreate_instance(project_dir: str, label: str | None = None) -> dict:
    """Destroy and immediately recreate the sandbox instance for `project_dir`
    (+ `label`, when the root owns more than one).

    Equivalent to destroy_instance followed by ensure_instance — gives a clean
    WP install (fresh DB, fresh uploads) from the current sandbox.config.*
    without a manual two-step. The recreated instance keeps the SAME port and
    label as before so bookmarks and tool configs don't change.

    project_dir: the plugin project to recreate.
    """
    sc = _core()
    try:
        root = str(sc.find_project_root(project_dir))
    except Exception as e:
        return {"ok": False, "error": f"invalid project_dir {project_dir!r}: {e}"}
    existing = sc.registry_get(root, label=label)
    if not existing or not existing.get("instance"):
        return {"ok": False,
                "error": f"no sandbox instance for project '{root}'. "
                         f"Call ensure_instance(project_dir={project_dir!r}) first."}
    inst = existing["instance"]
    resolved_label = existing["label"]
    if existing.get("kind") == "compose":
        destroyed = destroy_instance(project_dir, resolved_label)
        if not destroyed.get("ok"):
            return destroyed
        recreated = ensure_instance(project_dir, resolved_label, create=True)
        recreated["recreated"] = bool(recreated.get("ok"))
        return recreated
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

    # Re-insert a pending registry entry with the same ports + label so
    # ensure_instance picks them up (skipping _pick_instance_ports) and the
    # URL/label stay the same.
    sc.registry_put(
        root,
        label=resolved_label,
        instance=inst,
        status="pending",
        wordpress_port=saved_ports.get("wordpress_port"),
        db_port=saved_ports.get("db_port"),
        mailpit_port=saved_ports.get("mailpit_port"),
        server=saved_ports.get("server", "apache"),
    )

    # recreate
    cmd2 = [str(sb), "ensure", "--project-dir", project_dir, "--json"]
    if resolved_label != "default":
        cmd2 += ["--label", resolved_label]
    try:
        res2 = subprocess.run(
            cmd2, capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
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

def setup_domains(tld: str = "", project_dir: str = ".",
                  label: str = "default") -> dict:
    """Compatibility name for project-scoped domain adoption.

    The MCP transport never prompts for consent or privilege. Configure a TLD in
    the project/machine domain policy; the legacy ``tld`` argument is reported
    but cannot silently rewrite project identity.
    """
    value = _domain_service().apply(
        project_dir, label=label, interactive=False,
    )
    payload = value.to_dict() if hasattr(value, "to_dict") else dict(value)
    payload.setdefault("operation", "apply")
    if tld:
        payload["legacy_tld"] = tld
        payload.setdefault("notices", []).append(
            "The legacy MCP tld argument no longer rewrites project identity; "
            "configure domains.tld explicitly.",
        )
    return payload

def secure_instance(project_dir: str, label: str | None = None) -> dict:
    """Give the project's instance a trusted https://<name>.<tld> URL without a
    recreate — assigns its domain (if missing), mints the cert, wires the proxy
    TLS route, and points WP at https. Use for an instance that came up on
    localhost (e.g. created before the proxy was set up, or multisite). Wraps
    `./sb domains setup` (idempotent; only the missing pieces are added) and
    returns the instance's resulting URL.

    label: which of project_dir's instances, when it owns more than one.
    """
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    try:
        root = str(_core().find_project_root(project_dir))
        owner = _core().registry_get(root, label=label)
    except Exception:
        owner = None
    if owner and owner.get("kind") == "compose":
        try:
            ok, result = _core().secure_generic_instance(inst)
        except Exception as exc:
            return {"ok": False, "code": "proxy_error", "error": str(exc)}
        if not ok:
            return {"ok": False, "code": "proxy_error", "error": result}
        return {"ok": True, "instance": inst, "kind": "compose", "url": result}
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

def apply_config(project_dir: str, label: str | None = None) -> dict:
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
    label: which of project_dir's instances, when it owns more than one.
    """
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "apply", "--project-dir", project_dir, "--json"]
    if label:
        cmd += ["--label", label]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
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
