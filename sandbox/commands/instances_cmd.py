from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import (
    CONFIG_LOCAL, ROOT, RUNTIME_DIR, _cert_paths, _core, _herd,
    _herd_tests_db, _hosts_edit, _local_yaml, _provision_test_harness,
    _valet_proxy_active, _write_local_yaml, active_project_file,
    collect_instance_rows, compose, compose_file, die, ensure_instance, ensure_pyyaml,
    focus_file, info, load_config, ok, proxy_available, regen_caddyfile,
    reload_proxy, resolve_instances, snapshots_dir, valet_proxy_remove, wp_dir,
    wpcli,
)

from sandbox.registry import register
from sandbox.application.context import runtime_service, wordpress_runtime_service
from sandbox.runtimes.base import OperationError, OperationRequest



def cmd_focus(cfg, args) -> None:
    inst = args.resolved_instance
    ff = focus_file(inst)
    if args.clear:
        if ff.exists():
            ff.unlink()
        ok("Cleared plugin focus")
        return
    if not args.slug:
        if ff.exists():
            print(ff.read_text().strip())
        else:
            info("No focused plugin set")
        return
    # Singleton focus invariant: a plugin is focused in AT MOST one
    # instance at a time. Before focusing it here, clear the same plugin's
    # focus on every OTHER instance. This is what lets "focus <plugin>"
    # resolve to exactly one instance later — the plugin name, not the
    # instance name, becomes the unambiguous lookup key.
    #   `--here` is the explicit override: focus here WITHOUT stealing it
    #   from other instances (for deliberate A/B / multi-instance work).
    if not getattr(args, "here", False):
        stolen = []
        for fp in ROOT.glob(".focus.*"):
            other = fp.name[len(".focus."):]
            if other == inst:
                continue
            try:
                if fp.read_text().strip() == args.slug:
                    fp.unlink()
                    stolen.append(other)
            except OSError:
                pass
        if stolen:
            info(f"moved focus of '{args.slug}' here from: "
                 f"{', '.join(stolen)}")

    ff.write_text(args.slug)
    ok(f"Focused plugin: {args.slug}")

def cmd_ensure(cfg, args) -> None:
    """`./sb ensure [--project-dir DIR]` — boot the instance for a project
    directory (create-if-missing) and print its URL. The MCP server's
    ensure_instance tool wraps this; also usable by hand."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    label = getattr(args, "label", None)
    create = getattr(args, "create", False)
    try:
        result = wordpress_runtime_service(cfg).invoke(OperationRequest(
            project_root=pd,
            operation="ensure",
            label=label or "default",
            arguments={"create": create},
        ))
    except sc.ConfigError as e:
        die(str(e))
    if isinstance(result, OperationError):
        die(result.message)
    entry = dict(result.data)
    if getattr(args, "json", False):
        # Compact single line as the LAST stdout line so the MCP server can
        # parse it past any boot/progress output above.
        public_entry = dict(entry)
        public_entry.pop("login_url", None)
        public_entry.pop("autologin_token", None)
        print(json.dumps(public_entry))
    else:
        ok(f"instance '{entry['instance']}' ready at {entry['url']}")
        print(f"  project: {entry['root']}")
        if entry.get("kind") == "compose":
            print(f"  kind:    compose  service={entry.get('service')} http={entry.get('http_port')}")
        else:
            print(f"  ports:   WP={entry['wordpress_port']} "
                  f"DB={entry['db_port']} mail={entry['mailpit_port']}")
            print(f"  server:  {entry['server']}  (source: {entry.get('source')})")

def cmd_init(cfg, args) -> None:
    """`./sb init [--project-dir DIR] [--force] [--no-test-harness]` — turn a
    plugin checkout into a sandbox project in one command: write a native config
    (scaffold sandbox.config.json, or convert an existing .wp-env.json; --force
    regenerates the same native file), boot its per-directory instance
    (create-if-missing), and provision the phpunit test harness. From a bare
    checkout to a running, testable stack."""
    pd = getattr(args, "project_dir", None) or os.getcwd()
    sc = _core()
    requested_type = getattr(args, "type", None)
    project_root = Path(pd).expanduser().resolve()
    if requested_type and not any((project_root / name).exists() for name in sc.CONFIG_BASENAMES):
        if requested_type == "astro":
            from sandbox.runtimes.presets import propose_astro
            propose_astro(project_root)
            ok("wrote reviewable Astro Compose and Sandbox configuration")
            requested_type = None
        compose_file = next((project_root / name for name in ("compose.yaml", "compose.yml", "docker-compose.yml") if (project_root / name).exists()), None)
        if requested_type and compose_file is None:
            die("generic initialization requires an existing compose.yaml, compose.yml, or docker-compose.yml; no project command was guessed")
        if requested_type:
            import yaml
            document = yaml.safe_load(compose_file.read_text()) or {}
            services = document.get("services") if isinstance(document, dict) else None
            if not isinstance(services, dict) or not services:
                die("generic initialization found no Compose services")
            preferred = ("web", "app", "laravel.test", "node", "frontend")
            service = next((name for name in preferred if name in services), next(iter(services)))
            service_doc = services.get(service) or {}
            ports = service_doc.get("expose") or service_doc.get("ports") or []
            internal_port = 80
            if ports:
                raw = ports[0]
                raw = raw.get("target") if isinstance(raw, dict) else str(raw).split(":")[-1].split("/")[0]
                try:
                    internal_port = int(raw)
                except (TypeError, ValueError):
                    pass
            config = {"kind": "compose", "framework": requested_type,
                      "compose": {"file": compose_file.name, "service": service,
                                   "internal_port": internal_port, "health_path": "/"}}
            (project_root / "sandbox.config.json").write_text(json.dumps(config, indent=2) + "\n")
            ok(f"wrote sandbox.config.json for generic {requested_type} project")
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = Path(pconf["root"])
    base_source = pconf["source"].split("+")[0]   # drop any "+override" suffix
    has_native = base_source in sc.CONFIG_BASENAMES
    force = getattr(args, "force", False)

    if pconf.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(
            project_root=str(root), operation="ensure", label="default",
            arguments={"create": True},
        ))
        if isinstance(result, OperationError):
            die(result.message)
        entry = dict(result.data)
        ok(f"generic instance '{entry['instance']}' ready at {entry['url']}")
        print(f"  project: {entry['root']}\n  service: {entry['service']}\n  framework: {entry.get('framework') or 'compose'}")
        return

    # 1. Ensure a native config exists (don't clobber unless --force).
    if has_native and not force:
        info(f"config exists: {base_source} (use --force to regenerate)")
    else:
        # The resolved pconf already carries the canonical schema (defaults
        # merged, .wp-env.json mapped). Persist exactly the schema keys.
        data = {k: pconf.get(k, v) for k, v in sc.DEFAULTS.items()}
        # Regenerate the SAME native file (preserving an existing .yml/.yaml);
        # scaffold/convert to sandbox.config.json. Writing a fresh .json beside
        # an existing .yml would shadow it (json wins load order) and silently
        # orphan the user's edit surface.
        dest = root / (base_source if has_native else "sandbox.config.json")
        if dest.suffix in (".yml", ".yaml"):
            ensure_pyyaml()
            import yaml
            dest.write_text(yaml.safe_dump(data, default_flow_style=False,
                                           sort_keys=False))
        else:
            dest.write_text(json.dumps(data, indent=2) + "\n")
        note = ("converted from .wp-env.json" if base_source == ".wp-env.json"
                else "regenerated" if has_native else "scaffolded")
        ok(f"wrote {dest.name} ({note})")

    # 2. Boot the instance for this project (idempotent).
    try:
        entry = ensure_instance(cfg, str(root))
    except sc.ConfigError as e:
        die(str(e))
    inst = entry["instance"]
    ok(f"instance '{inst}' ready at {entry['url']}")

    # 3. Provision the test harness so `./sb test` runs immediately.
    if not getattr(args, "no_test_harness", False):
        info("Provisioning test harness (cached)…")
        _provision_test_harness(inst, sc.load_project_config(str(root)))
        ok("test harness ready")

    print()
    ok(f"{root.name} is a sandbox project.")
    print(f"  admin:  {entry['url']}/wp-admin")
    print(f"  test:   ./sb test --project-dir {root}")

def cmd_instance(cfg, args) -> None:
    """Delete a sandbox instance end-to-end.

    `./sb instance delete <name>` stops the stack, removes the volume, deletes
    runtime/wp-<name>/, removes the block from sandbox.local.yml, drops the
    registry mapping, and removes per-instance state. Refuses to delete `main`.

    Per-project model: instances are CREATED by `./sb init` / `./sb ensure` in a
    plugin dir (keyed to the project root) — not by name here.
    """
    action = args.action
    name = args.name

    if action != "delete":
        die("`./sb instance` only supports `delete` now. Create an instance by "
            "cd-ing into a plugin repo and running `./sb init` (or `./sb ensure`).")
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,30}$", name or ""):
        die("instance name must be lowercase, start with [a-z0-9], "
            "and contain only [a-z0-9_-] (max 31 chars)")

    owner = _core().registry_find_instance(name)
    if owner and owner.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(
            owner["root"], "destroy", label=owner.get("label", "default"),
        ))
        if isinstance(result, OperationError):
            die(result.message)
        ok(f"Generic instance '{name}' deleted without removing project volumes.")
        return

    # delete
    instances = resolve_instances(cfg)
    if name not in instances:
        die(f"unknown instance '{name}'. Defined: "
            f"{', '.join(instances.keys())}")

    if not args.yes:
        print(f"\n  This will:")
        print(f"    • stop + remove all containers for instance '{name}'")
        print(f"    • delete the DB volume")
        print(f"    • delete runtime/wp-{name}/ and runtime/snapshots/{name}/")
        print(f"    • remove instances.{name} from sandbox.local.yml")
        print(f"    • delete .focus.{name} / .active-project.{name}")
        print()
        ans = input(f"  Type the instance name '{name}' to confirm: ").strip()
        if ans != name:
            info("cancelled")
            return

    # 1. Stop + remove the runtime. Docker: containers + volume. Herd: drop
    #    the host DBs (while wp-config still exists), then unsecure + unlink.
    args.resolved_instance = name
    if instances[name].get("server") == "herd":
        info(f"herd teardown for '{name}': dropping DBs + unlink + unsecure")
        wpcli(["db", "query",
               f"DROP DATABASE IF EXISTS {_herd_tests_db(name)}"],
              instance=name, check=False, capture=True)
        wpcli(["db", "drop", "--yes"], instance=name, check=False, capture=True)
        # Drop the PHP isolation entry so a stale row doesn't linger in
        # `herd isolated` after the site is gone.
        _herd("unisolate", "--site", name)
        _herd("unsecure", name)
        _herd("unlink", name, cwd=wp_dir(name) if wp_dir(name).exists() else None)
    else:
        info(f"stopping + removing containers + volume for '{name}'")
        compose("down", "-v", instance=name, check=False)

    # 2. Remove WP install dir + snapshots (+ MCP pinned-PHP shims, if any)
    for path in (wp_dir(name), snapshots_dir(name),
                 RUNTIME_DIR / "herd-shims" / name):
        if path.exists():
            info(f"removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # 3. Remove state files
    for path in (focus_file(name), active_project_file(name)):
        if path.exists():
            info(f"removing {path}")
            path.unlink()

    # 4. Remove compose file
    cf = compose_file(name)
    if cf.exists():
        cf.unlink()

    # 5. Remove block from sandbox.local.yml (only — never touch sandbox.yml)
    local = _local_yaml()
    if name in (local.get("instances") or {}):
        del local["instances"][name]
        if not local["instances"]:
            del local["instances"]
        _write_local_yaml(local)
        info(f"removed instances.{name} from {CONFIG_LOCAL.name}")

    # 5b. Drop the project→instance mapping from the registry (the source of
    #     truth for per-directory instances), else `ensure_instance` for that
    #     project would later find a stale "ready" record pointing at a
    #     now-deleted stack.
    sc = _core()
    owner = sc.registry_find_instance(name)
    if owner and owner.get("root"):
        sc.registry_remove(owner["root"], label=owner.get("label"))
        info(f"deregistered '{name}' from the instance registry")

    # 7. Tear down its custom domain. Primary: drop its route from the HTTPS
    #    proxy (regenerate the Caddyfile from the now-reduced config + reload).
    #    Fallbacks: a legacy Valet proxy and/or an /etc/hosts mapping. All are
    #    no-ops if not present. (The wp-dir removal already deleted the mu-plugin.)
    dom = instances.get(name, {}).get("domain")
    if dom:
        if proxy_available():
            # Drop the cert + route, then regenerate from the reduced config.
            for p in _cert_paths(dom):
                p.unlink(missing_ok=True)
            regen_caddyfile(load_config())
            reload_proxy()
            info(f"removed HTTPS proxy route + cert for {dom}")
        if _valet_proxy_active(dom):
            valet_proxy_remove(dom)
            info(f"removed valet proxy for {dom}")
        okh, msg = _hosts_edit("remove", dom)
        if okh:
            info(f"removed domain {dom} from /etc/hosts")

    ok(f"Instance '{name}' deleted.")

def cmd_instances(cfg, args) -> None:
    """List defined instances + their status and ports. --project-dir filters
    to one project root — useful once a root owns more than one labelled
    instance (multi-instance-per-root)."""
    rows = collect_instance_rows(cfg)
    project_dir = getattr(args, "project_dir", None)
    if project_dir:
        sc = _core()
        try:
            root = str(sc.find_project_root(project_dir))
        except Exception as e:
            die(f"invalid --project-dir {project_dir!r}: {e}")
        owned = {e["instance"] for e in sc.registry_list_for_root(root)}
        rows = [r for r in rows if r["name"] in owned]
    print()
    print(f"  {'STATUS':<10} {'NAME':<10} {'LABEL':<10} {'URL':<26} {'SERVER':<10} "
          f"{'MCP SERVER':<18} {'PROJECT':<12} FOCUS")
    for r in rows:
        status = "● running" if r["running"] else "○ stopped"
        print(f"  {status:<10} {r['name']:<10} {r['label']:<10} {r['url']:<26} {r['server']:<10} "
              f"{r['mcp_server']:<18} {r['project']:<12} {r['focus']}")
    print()
    print(f"  Claude tools per instance: mcp__<MCP SERVER>__*  "
          f"(e.g. mcp__sandbox__focus_get)")
    print()

register({
    'init': cmd_init,
    'ensure': cmd_ensure,
    'instances': cmd_instances,
    'instance': cmd_instance,
    'focus': cmd_focus,
})
