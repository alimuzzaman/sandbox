from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register
from sandbox.application.context import preflight_project_capability
import sandbox.core._remote as sr
import sandbox.core._proplugins as pro


_REMOTE_DEPLOY_CAPABILITIES = {
    "wordpress": "wordpress.remote-deploy",
    "compose": "compose.remote-deploy",
}


# See docs/remote-hosting.md and specs/014-remote-vps-hosting/ for the full design
# this module implements. `./sb deploy` is a ONE-WAY, ON-DEMAND push of local
# project state (committed + uncommitted) to a provisioned remote -- never a
# continuous sync daemon. Every deploy REPLACES the remote's uncommitted layer
# rather than stacking (spec FR-007): reset to the just-pushed commit first,
# then apply the CURRENT local diff fresh.

def _fail(remote_name: str | None, message: str, as_json: bool,
          *, source_ref: str | None = None) -> None:
    if as_json:
        print(json.dumps({
            "ok": False,
            "remote": remote_name,
            "remote_selection": "explicit" if remote_name else None,
            "source_ref": source_ref,
            "resolved_commit": None,
            "error_code": "source_not_immutable" if source_ref else "deploy_failed",
            "pushed_commit": None,
            "uncommitted_files_applied": 0,
            "instance": None,
            "url": None,
            "error": sr.redact_ssh_connection(message),
        }))
        import sys as _sys
        _sys.exit(1)
    die(sr.redact_ssh_connection(message))


def _require_instance_field(instance: dict, field: str):
    value = instance.get(field)
    if value in (None, ""):
        raise RuntimeError(
            f"remote ensure returned no {field!r}; re-run remote provision so "
            "the VPS has the current sandbox runtime"
        )
    return value


def cmd_deploy(cfg, args) -> None:
    """`./sb deploy --project-dir DIR --remote NAME [--json]` -- push the local
    project's current state (committed HEAD + uncommitted changes, including
    untracked files) to a provisioned remote. See docs/remote-hosting.md."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    remote_name = getattr(args, "remote", None)
    source_ref = getattr(args, "source_ref", None)
    if not isinstance(source_ref, str):
        source_ref = None
    # Keep compatibility with direct command adapters that historically called
    # this selector ``ref`` while the documented CLI name is ``--source-ref``.
    if source_ref is None:
        source_ref = getattr(args, "ref", None)
        if not isinstance(source_ref, str):
            source_ref = None
    as_json = bool(getattr(args, "json", False))
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        _fail(remote_name, str(e), as_json, source_ref=source_ref)
    try:
        sr.reject_herd_projects(pconf)
    except ValueError as e:
        _fail(remote_name, str(e), as_json, source_ref=source_ref)
    root = Path(pconf["root"])
    project_kind = pconf.get("kind", "wordpress")
    capability = _REMOTE_DEPLOY_CAPABILITIES.get(project_kind)
    if capability is None:
        _fail(remote_name, f"project kind {project_kind!r} does not support remote deployment", as_json,
              source_ref=source_ref)
    is_wordpress = capability == "wordpress.remote-deploy"
    capability_error = preflight_project_capability(cfg, str(root), capability)
    if capability_error is not None:
        _fail(remote_name, capability_error.message, as_json, source_ref=source_ref)
    if not is_wordpress and getattr(args, "plugin_slug", None):
        _fail(remote_name, "--plugin-slug is only supported for WordPress projects", as_json,
              source_ref=source_ref)

    if not remote_name:
        _fail(
            remote_name,
            "--remote NAME is required -- which registered remote should this deploy to?",
            as_json, source_ref=source_ref,
        )

    entry = sr.get_remote(remote_name)
    if not entry:
        _fail(remote_name, f"no remote named '{remote_name}' — register it first with "
              f"`./sb remote add {remote_name} <ssh-connection>`", as_json,
              source_ref=source_ref)
    if not entry.get("provisioned"):
        _fail(remote_name, f"remote '{remote_name}' is not provisioned yet — run "
              f"`./sb remote provision {remote_name}` first", as_json,
              source_ref=source_ref)

    try:
        # Resolve and validate an immutable source before creating a remote
        # deploy repository.  A bad ref or dirty-tree combination therefore
        # cannot leave any remote-side mutation behind.
        resolved_source = (
            sr.resolve_source_ref(root, source_ref)
            if source_ref is not None else None
        )
        target = sr.ensure_deploy_repo(entry, root)
        branch = sr.current_branch(root) if resolved_source is None else None
        pushed_sha = sr.push_commits(
            entry, root, target, branch,
            source_ref=source_ref, resolved_sha=resolved_source,
        )
        sr.reset_target_to(entry, target, pushed_sha)
        if resolved_source is None:
            diff_text, untracked = sr.capture_uncommitted(root)
            # The canonical project descriptor is runtime intent, not a
            # machine override. Carry it even when a checkout keeps it out of
            # Git; without it a ready remote fast-path cannot reconstruct the
            # deployed plugin bind mount.
            untracked = list(dict.fromkeys([
                *untracked, *sr.deploy_project_descriptor_files(root),
            ]))
            applied = sr.apply_uncommitted(entry, target, root, diff_text, untracked)
        else:
            applied = 0
        # Pro plugins are a HOST-level catalog, not project state: mirror them
        # before the instance boots so its On-Demand page lists the same slugs
        # the local machine offers. Fail-soft — a project deploy stays valid
        # even when the shared store cannot be pushed.
        pro_plugins = None
        if getattr(args, "pro_plugins", True):
            try:
                pro_plugins = pro.sync(entry, remote_name, cfg=cfg)
            except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
                pro_plugins = {"ok": False,
                               "error": sr.redact_ssh_connection(str(exc), entry)}
        instance = None
        public_url = None
        if getattr(args, "ensure", False) or getattr(args, "expose", False):
            instance = sr.ensure_remote_instance(entry, target)
            if not isinstance(instance, dict):
                raise RuntimeError("remote ensure did not return an instance object")
            instance_name = _require_instance_field(instance, "instance")
            if is_wordpress:
                reconciled = sr.reconcile_remote_instance(entry, target)
                reconciled_name = _require_instance_field(reconciled, "instance")
                if reconciled_name != instance_name:
                    raise RuntimeError(
                        "remote apply selected a different instance than ensure"
                    )
                instance.update(reconciled)
                plugin_slug = (
                    getattr(args, "plugin_slug", None)
                    or pconf.get("slug")
                    or sr.deploy_target_slug(root)
                )
                sr.activate_remote_plugin(entry, target, instance_name, plugin_slug)
        if getattr(args, "expose", False):
            label = instance.get("label") or "default"
            domain = (
                getattr(args, "domain", None)
                or sr.default_instance_domain(label, sr.deploy_target_slug(root))
            )
            public_url = f"https://{domain}"
            port_field = "wordpress_port" if is_wordpress else "http_port"
            port = int(_require_instance_field(instance, port_field))
            if not 1 <= port <= 65535:
                raise RuntimeError(f"remote ensure returned invalid {port_field!r}")
            sr.configure_instance_https_route(entry, domain, port)
            if is_wordpress:
                sr.set_remote_instance_url(entry, target, public_url)
            instance["url"] = public_url
            if is_wordpress:
                instance["login_url"] = sr.rewrite_instance_url(
                    instance.get("login_url"), public_url
                ) if instance.get("login_url") else ""
                instance["admin_url"] = f"{public_url}/wp-admin/"
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        result = {"ok": False, "remote": remote_name,
                 "remote_selection": "explicit",
                 "source_ref": source_ref, "resolved_commit": None,
                 "error_code": "source_not_immutable" if source_ref else "deploy_failed",
                 "pushed_commit": None, "uncommitted_files_applied": 0,
                 "instance": None, "url": None,
            "error": sr.redact_ssh_connection(str(e), entry)}
        if as_json:
            print(json.dumps(result))
            import sys as _sys
            _sys.exit(1)
        die(str(e))
        return

    result = {"ok": True, "remote": remote_name,
             "remote_selection": "explicit",
             "source_ref": source_ref, "resolved_commit": pushed_sha,
             "pushed_commit": pushed_sha,
             "source_immutable": resolved_source is not None,
             "uncommitted_files_applied": applied, "instance": instance,
             "pro_plugins": pro_plugins,
             "url": public_url, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        print(f"Deploying to '{remote_name}'...")
        if source_ref is not None:
            print(f"  pushed source-ref {source_ref!r} ({pushed_sha[:12]}) -> {remote_name}")
        else:
            print(f"  pushed HEAD ({pushed_sha[:7]}) -> {remote_name}")
            print(f"  applied {applied} uncommitted file(s)")
        if pro_plugins and not pro_plugins.get("ok"):
            print(f"  pro plugins: NOT mirrored — {pro_plugins.get('error')}")
        elif pro_plugins and pro_plugins.get("skipped") in (None, "unchanged"):
            state = "already current" if pro_plugins.get("skipped") else "mirrored"
            print(f"  pro plugins: {state} "
                  f"({len(pro_plugins.get('slugs') or [])} on-demand slug(s))")
        if instance:
            print(f"  remote instance: {instance.get('instance')}")
        if public_url:
            print(f"  public URL: {public_url}")
        source_label = (
            f"immutable source {source_ref!r} ({pushed_sha[:12]})"
            if source_ref is not None else "working tree"
        )
        ok(f"Deployed. {remote_name} now reflects {source_label} as of this command.")


register({'deploy': cmd_deploy})
