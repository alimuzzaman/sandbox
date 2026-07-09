from __future__ import annotations
import json
import os
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register
import sandbox.core._remote as sr


# See docs/remote-hosting.md and specs/014-remote-vps-hosting/ for the full design
# this module implements. `./sb deploy` is a ONE-WAY, ON-DEMAND push of local
# project state (committed + uncommitted) to a provisioned remote -- never a
# continuous sync daemon. Every deploy REPLACES the remote's uncommitted layer
# rather than stacking (spec FR-007): reset to the just-pushed commit first,
# then apply the CURRENT local diff fresh.

def cmd_deploy(cfg, args) -> None:
    """`./sb deploy --project-dir DIR --remote NAME [--json]` -- push the local
    project's current state (committed HEAD + uncommitted changes, including
    untracked files) to a provisioned remote. See docs/remote-hosting.md."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    try:
        sr.reject_herd_projects(pconf)
    except ValueError as e:
        die(str(e))
    root = Path(pconf["root"])
    remote_name = getattr(args, "remote", None)
    as_json = bool(getattr(args, "json", False))

    if not remote_name:
        die("--remote NAME is required -- which registered remote should this deploy to?")

    entry = sr.get_remote(remote_name)
    if not entry:
        die(f"no remote named '{remote_name}' — register it first with "
            f"`./sb remote add {remote_name} <ssh_url>`")
    if not entry.get("provisioned"):
        die(f"remote '{remote_name}' is not provisioned yet — run "
            f"`./sb remote provision {remote_name}` first")

    try:
        target = sr.ensure_deploy_repo(entry, root)
        branch = sr.current_branch(root)
        pushed_sha = sr.push_commits(entry, root, target, branch)
        sr.reset_target_to(entry, target, pushed_sha)
        diff_text, untracked = sr.capture_uncommitted(root)
        applied = sr.apply_uncommitted(entry, target, root, diff_text, untracked)
        instance = None
        public_url = None
        if getattr(args, "ensure", False) or getattr(args, "expose", False):
            instance = sr.ensure_remote_instance(entry, target)
            plugin_slug = (
                getattr(args, "plugin_slug", None)
                or pconf.get("slug")
                or sr.deploy_target_slug(root)
            )
            sr.activate_remote_plugin(entry, target, instance["instance"], plugin_slug)
        if getattr(args, "expose", False):
            label = instance.get("label") or "default"
            domain = (
                getattr(args, "domain", None)
                or sr.default_instance_domain(label, sr.deploy_target_slug(root))
            )
            public_url = f"https://{domain}"
            sr.configure_instance_https_route(entry, domain, int(instance["wordpress_port"]))
            sr.set_remote_instance_url(entry, target, public_url)
            instance["url"] = public_url
            instance["login_url"] = None
            instance["admin_url"] = f"{public_url}/wp-admin/"
    except (RuntimeError, ValueError) as e:
        result = {"ok": False, "remote": remote_name, "pushed_commit": None,
                 "uncommitted_files_applied": 0, "instance": None, "url": None,
                 "error": str(e)}
        if as_json:
            print(json.dumps(result))
            import sys as _sys
            _sys.exit(1)
        die(str(e))
        return

    result = {"ok": True, "remote": remote_name, "pushed_commit": pushed_sha,
             "uncommitted_files_applied": applied, "instance": instance,
             "url": public_url, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        print(f"Deploying to '{remote_name}'...")
        print(f"  pushed HEAD ({pushed_sha[:7]}) -> {remote_name}")
        print(f"  applied {applied} uncommitted file(s)")
        if instance:
            print(f"  remote instance: {instance.get('instance')}")
        if public_url:
            print(f"  public URL: {public_url}")
        ok(f"Deployed. {remote_name} now reflects your working tree as of this command.")


register({'deploy': cmd_deploy})
