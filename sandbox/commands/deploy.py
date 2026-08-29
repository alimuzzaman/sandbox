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


def _deploy_error_code(error: BaseException, source_ref: str | None) -> str:
    """Map known safe deploy failures to stable machine-readable codes."""
    if isinstance(error, sr.RemoteBranchDiverged):
        return sr.RemoteBranchDiverged.error_code
    if isinstance(error, sr.RemoteHomeResolutionTimeout):
        return sr.RemoteHomeResolutionTimeout.error_code
    return "source_not_immutable" if source_ref else "deploy_failed"


def _require_instance_field(instance: dict, field: str):
    value = instance.get(field)
    if value in (None, ""):
        raise RuntimeError(
            f"remote ensure returned no {field!r}; re-run remote provision so "
            "the VPS has the current sandbox runtime"
        )
    return value


def _failed_ensure_cleanup(
    entry: dict,
    target: str,
    baseline: list[dict],
    *,
    label: str = "default",
) -> str:
    """Remove only a uniquely new instance after a failed remote ensure.

    The remote CLI can register its instance before it emits a usable JSON
    response. Cleanup therefore compares a read-only inventory captured before
    ensure with a second inventory after failure. Never delete a baseline row,
    and never guess when the inventory is unavailable or produces multiple
    candidates.
    """
    try:
        current = sr.list_remote_instances(entry, target)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError):
        return "remote instance cleanup is unverified: could not read post-failure inventory"
    baseline_names = {
        row.get("name") for row in baseline
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    candidates = [
        row.get("name") for row in current
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and row.get("name") not in baseline_names
        and (row.get("label") or "default") == label
    ]
    if not candidates:
        return "remote instance cleanup found no new instance"
    if len(candidates) != 1:
        return "remote instance cleanup is unverified: multiple new instances matched"
    try:
        sr.delete_remote_instance(entry, candidates[0])
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError):
        return "remote instance cleanup failed for the newly created instance"
    return "remote instance cleanup removed the newly created instance"


def _ensure_remote_instance_transactional(entry: dict, target: str) -> dict:
    """Ensure the default remote instance without leaving an orphan on failure."""
    try:
        baseline = sr.list_remote_instances(entry, target)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(
            "could not establish the remote instance baseline before ensure; "
            "refusing remote mutation"
        ) from exc
    try:
        instance = sr.ensure_remote_instance(entry, target)
        if not isinstance(instance, dict):
            raise RuntimeError("remote ensure did not return an instance object")
        _require_instance_field(instance, "instance")
        return instance
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        cleanup = _failed_ensure_cleanup(entry, target, baseline)
        raise RuntimeError(f"{exc}; {cleanup}") from exc


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
    raw_deploy_timeout = getattr(args, "deploy_timeout", None)
    # Hand-built adapters/tests may omit the argparse field; only an explicit
    # integer changes the default instead of treating a mock attribute as input.
    if isinstance(raw_deploy_timeout, bool) or not isinstance(raw_deploy_timeout, int):
        raw_deploy_timeout = None
    try:
        deploy_timeout = sr.normalize_remote_push_timeout(
            sr.REMOTE_PUSH_TIMEOUT_DEFAULT_SECONDS
            if raw_deploy_timeout is None else raw_deploy_timeout
        )
    except ValueError as exc:
        _fail(remote_name, str(exc), as_json, source_ref=source_ref)
    explicit_instance = getattr(args, "instance", None)
    if isinstance(explicit_instance, str) and explicit_instance.strip():
        # ``--instance`` is a global selector for observation/control commands,
        # but deploy is project-scoped. Silently ignoring it can push the
        # current checkout while the operator believes another instance was
        # selected, so refuse before remote lookup or staging.
        _fail(
            remote_name,
            "deploy is project-scoped and cannot target --instance NAME; use "
            "--project-dir DIR for the intended checkout (or omit --instance)",
            as_json,
            source_ref=source_ref,
        )
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

    include_paths = getattr(args, "include", None)
    if not isinstance(include_paths, (list, tuple)):
        include_paths = []
    try:
        include_paths = sr.validate_deploy_include_paths(root, include_paths)
    except (ValueError, OSError) as exc:
        _fail(remote_name, str(exc), as_json, source_ref=source_ref)

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
        target = sr.ensure_deploy_repo(
            entry, root, home_timeout=deploy_timeout
        )
        branch = (
            sr.current_branch(root, allow_detached=True)
            if resolved_source is None else None
        )
        pushed_sha = sr.push_commits(
            entry, root, target, branch,
            source_ref=source_ref, resolved_sha=resolved_source,
            push_timeout=deploy_timeout,
            allow_detached=resolved_source is None and branch is None,
        )
        descriptor_files = sr.deploy_project_descriptor_files(root)
        if resolved_source is None:
            diff_text, untracked = sr.capture_uncommitted(root)
            # The canonical project descriptor is runtime intent, not a
            # machine override. Carry it even when a checkout keeps it out of
            # Git; without it a ready remote fast-path cannot reconstruct the
            # deployed plugin bind mount.
            untracked = list(dict.fromkeys([
                *untracked, *descriptor_files, *include_paths,
            ]))
            applied = sr.update_target_to(
                entry, target, pushed_sha, project_root=root,
                diff_text=diff_text, untracked=untracked,
            )
        else:
            # The commit remains immutable; the primary Sandbox descriptor is
            # a separately declared runtime-intent layer. Projects commonly
            # keep it gitignored, but the remote still needs it to reconstruct
            # the exact bind mounts before activation.
            applied = sr.update_target_to(
                entry, target, pushed_sha,
                project_root=root if descriptor_files or include_paths else None,
                untracked=[*descriptor_files, *include_paths],
            )
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
            instance = _ensure_remote_instance_transactional(entry, target)
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
            # Aliases are declared per project so they travel with it; --alias
            # overrides for a one-off. The primary domain is configured first
            # so a failing alias never leaves the instance unreachable on its
            # own hostname.
            declared = getattr(args, "alias", None)
            # argparse gives a list or None; adapters that build their own args
            # object may give neither, and a project declaration is the default.
            if not isinstance(declared, (list, tuple)):
                declared = pconf.get("aliases")
            aliases = normalize_aliases(declared, primary=domain, strict=True)
            sr.configure_instance_https_route(entry, domain, port)
            for alias in aliases:
                sr.configure_instance_https_route(entry, alias, port)
            # Routes are per-hostname files, so a renamed domain leaves the old
            # one serving. Report it always; only delete when asked, because
            # this reads the whole host and another checkout may own a route
            # this project cannot see in its own config.
            prune = getattr(args, "prune_routes", False) is True
            stale, pruned = [], []
            try:
                stale = [h for h in sr.instance_route_hosts(entry, port)
                         if h != domain and h not in aliases]
            except (RuntimeError, ValueError, subprocess.SubprocessError, OSError):
                # The instance is already exposed and serving at this point.
                # An unreadable route inventory is a reporting gap, not a
                # deploy failure -- unless the caller asked us to act on it.
                if prune:
                    raise
                stale = []
            if stale and prune:
                for host in stale:
                    sr.remove_instance_https_route(entry, host)
                    pruned.append(host)
            if is_wordpress:
                sr.set_remote_instance_url(entry, target, public_url)
            instance["url"] = public_url
            instance["aliases"] = aliases
            instance["alias_urls"] = [f"https://{a}" for a in aliases]
            instance["stale_routes"] = [h for h in stale if h not in pruned]
            instance["pruned_routes"] = pruned
            if is_wordpress:
                instance["login_url"] = sr.rewrite_instance_url(
                    instance.get("login_url"), public_url
                ) if instance.get("login_url") else ""
                instance["admin_url"] = f"{public_url}/wp-admin/"
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        result = {"ok": False, "remote": remote_name,
                 "remote_selection": "explicit",
                 "source_ref": source_ref, "resolved_commit": None,
                 "source_mode": "immutable" if source_ref else None,
                 "error_code": _deploy_error_code(e, source_ref),
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
             "source_mode": (
                 "immutable" if resolved_source is not None
                 else "detached" if branch is None else "branch"
             ),
             "pushed_commit": pushed_sha,
             "source_immutable": resolved_source is not None,
             "uncommitted_files_applied": applied, "instance": instance,
             "included_paths": include_paths,
             "pro_plugins": pro_plugins,
             "url": public_url, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        print(f"Deploying to '{remote_name}'...")
        if source_ref is not None:
            print(f"  pushed source-ref {source_ref!r} ({pushed_sha[:12]}) -> {remote_name}")
        else:
            source_label = "detached HEAD" if branch is None else "HEAD"
            print(f"  pushed {source_label} ({pushed_sha[:7]}) -> {remote_name}")
            print(f"  applied {applied} uncommitted file(s)")
        if include_paths:
            print(f"  explicitly included {len(include_paths)} ignored artifact file(s)")
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
        for alias_url in (instance or {}).get("alias_urls") or []:
            print(f"  alias URL: {alias_url}")
        stale_routes = (instance or {}).get("stale_routes") or []
        if stale_routes:
            print(f"  stale routes still serving this instance: "
                  f"{', '.join(stale_routes)} (remove with --prune-routes)")
        for host in (instance or {}).get("pruned_routes") or []:
            print(f"  removed stale route: {host}")
        source_label = (
            f"immutable source {source_ref!r} ({pushed_sha[:12]})"
            if source_ref is not None else "working tree"
        )
        ok(f"Deployed. {remote_name} now reflects {source_label} as of this command.")


register({'deploy': cmd_deploy})
