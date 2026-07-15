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
from getpass import getpass
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import (
    BASE, CONNECT_TARGETS, ENTRY, ROOT, _assign_domains_to_all, _cert_paths,
    _connect_fluentboards, _connect_github, _core, _docker_preflight,
    _ensure_url_proxy, _global_link_dir, _is_server, _onboard_instance,
    _resolve_port_conflicts, _sudo, _tld, _wait_reachable, apply_config, die, info,
    load_config, ok, regen_caddyfile, register_claude_user_scope, reload_proxy,
    resolve_instances, site_url, write_claude_mcp_config,
)

from sandbox.registry import register
from sandbox.application.context import wordpress_runtime_service
from sandbox.runtimes.base import OperationError, OperationRequest



def cmd_connect(cfg, args) -> None:
    """Save credentials for an external integration (FluentBoards, GitHub, Cloudflare).

    Usage:
      ./sb connect fb           # or: fluentboards
      ./sb connect gh           # or: github
      ./sb connect cloudflare   # API token (stored locally only)
      ./sb connect              # list available targets
    """
    target = getattr(args, "target", None)
    if not target:
        print("\nUsage: ./sb connect <target>")
        print("\nAvailable targets:")
        print("  fb, fluentboards   FluentBoards URL + email + app password")
        print("  gh, github         GitHub org/user + gh CLI auth (private repos)")
        print("  cloudflare         Cloudflare API token for managed hosting")
        print()
        return

    canonical = CONNECT_TARGETS.get(target.lower())
    if not canonical:
        die(f"unknown target '{target}'. "
            f"Try: {', '.join(sorted(set(CONNECT_TARGETS)))}")

    ni = getattr(args, "non_interactive", False)
    if canonical == "fluentboards":
        _connect_fluentboards(cfg, non_interactive=ni)
    elif canonical == "github":
        _connect_github(cfg, non_interactive=ni)
    elif canonical == "cloudflare":
        from sandbox.core._cloudflare import save_cloudflare_token
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "") if ni else getpass(
            "Cloudflare API token (stored locally, not echoed): "
        )
        save_cloudflare_token(token)
        ok("Cloudflare token stored in ~/.zshrc.secrets")

def cmd_setup(cfg, args) -> None:
    from sandbox.commands.lifecycle import cmd_up, cmd_install, cmd_doctor, wp_is_installed
    from sandbox.commands.integ import cmd_mcp_install
    from sandbox.commands.ui_dash import cmd_web
    """One command: boot the stack, install WP, build MCP, wire Claude.

    Runs preflight (Docker + Python) but does NOT prompt for personal secrets.
    Users who need FluentBoards/GitHub creds run `./sb onboard`
    separately — keeps `setup` non-interactive for everyone else.

    Multi-instance: iterates every instance defined in sandbox.yml,
    booting + installing each one. Without an `instances:` block, this
    behaves identically to the pre-multi-instance flow against `main`.
    """
    server = _is_server(args)
    if server:
        print("  (server mode — headless: localhost only, no proxy/Claude/browser)")
    problems = _docker_preflight()
    sys.stdout.flush()
    if problems:
        print()
        print(f"  ✗ {problems} prerequisite(s) above must be fixed before the")
        print("    sandbox can run. Follow the → hint next to each failure,")
        print("    then re-run:  ./sb setup")
        print()
        sys.exit(1)

    # Auto-pick free ports if the configured ones collide with something else
    # already on the machine (e.g. another sandbox, or a reinstall) — so a fresh
    # install never silently lands on a busy port.
    cfg = _resolve_port_conflicts(cfg)

    # --no-instances: just prepare the CLI + MCP venv + Claude wiring. No
    # WordPress is booted — the user creates their first site afterwards by
    # running `./sb init` in a plugin repo. Keeps a fresh install fast.
    no_instances = getattr(args, "no_instances", False)

    instances = {} if no_instances else resolve_instances(cfg)
    import types
    for inst_name, inst_cfg in instances.items():
        print(f"\n▸ Instance '{inst_name}': booting docker stack (wp + db + mailpit)…")
        sub_args = types.SimpleNamespace(**vars(args), resolved_instance=inst_name) \
            if not hasattr(args, 'resolved_instance') \
            else types.SimpleNamespace(**{**vars(args), "resolved_instance": inst_name})
        cmd_up(cfg, sub_args)

        # Wait for WP to be reachable before `wp core install` to avoid races.
        # Use the canonical URL: secured/multisite instances do not resolve
        # correctly through localhost:<port>.
        _wait_reachable(inst_cfg)

        if wp_is_installed(inst_name):
            # setup is intentionally safe to repeat: installation rotates
            # credentials and tokens, and can re-download pinned core.  Those
            # actions belong to an explicit recreate/apply flow, not a normal
            # stack reconciliation.
            ok(f"Instance '{inst_name}': WordPress already installed — skipping installation")
        else:
            print(f"\n▸ Instance '{inst_name}': installing WordPress + provisioning app password…")
            cmd_install(cfg, sub_args)

    # Clean URLs: give every instance a http://<name>.tst (no port) via the URL
    # proxy. Local + interactive + Docker only. Skipped in server mode (the box
    # is reached over an SSH tunnel to localhost:<port>, so a .tst proxy is moot).
    #
    # We set the proxy up even with --no-instances (no site booted yet): the
    # one-time sudo password can ONLY be collected here, while setup still has a
    # TTY. If we waited until the first instance, that's often created from the
    # web dashboard (no TTY) — _ensure_url_proxy can't prompt there, silently
    # falls back to localhost:<port>, and the user gets a port (or, with a stale
    # domain, a hang) instead of the clean http://<name>.tst they wanted. Setting
    # it up now means every later instance — CLI or dashboard — gets a clean URL.
    if not server and sys.stdin.isatty() and not getattr(args, "no_domain", False):
        print("\n▸ Setting up clean URLs (http://<name>.tst, no port)…")
        up, cfg = _ensure_url_proxy(cfg)
        if up:
            cfg = _assign_domains_to_all(cfg)
            regen_caddyfile(cfg)
            reload_proxy()
            ok("clean URLs ready — instances serve http://<name>.tst")
        else:
            info("staying on http://localhost:<port> (clean URLs are optional).")

    print("\n▸ Building wp-mcp Python venv…")
    cmd_mcp_install(cfg, args)

    # Wire Claude Code. The project .mcp.json is always written (harmless, used
    # if anyone runs Claude in this dir). User-scope registration is skipped in
    # server mode / when there's no claude CLI (nothing to register against).
    print("\n▸ Wiring Claude Code…")
    cfg = load_config()  # reload so the freshly-written app password is picked up
    path, created = write_claude_mcp_config(cfg)
    ok(f"{'Wrote' if created else 'Updated'} {path}")
    if not server and shutil.which("claude"):
        register_claude_user_scope(cfg)
    else:
        info("skipped user-scope MCP registration (no claude CLI / server mode).")

    # Verify the running stack — but only if we actually booted instances.
    # With --no-instances there's nothing to check yet (doctor would just print
    # a wall of "not running" noise for a site the user hasn't created).
    if not no_instances and instances:
        # Doctor the first booted instance. (Per-project model: there's no
        # implicit `main`, so args.resolved_instance is None here — pass an
        # explicit instance or resolve_instances(cfg)[None] would KeyError.)
        first = next(iter(instances))
        doctor_args = types.SimpleNamespace(**{**vars(args), "resolved_instance": first})
        print("\n▸ Verifying…")
        try:
            cmd_doctor(cfg, doctor_args)
        except SystemExit:
            pass  # doctor exits 1 on problems; keep going to print next steps

    print()
    ok("Setup complete.")
    print()

    # Empty-start: nothing booted. The caller (installer or user) decides what's
    # next — create a site from the web UI or `./sb instance create <name>`. Skip
    # the plugin-picker + "Next:" epilogue, which assume a running instance.
    if no_instances:
        info("No site yet. Create one:")
        print("    ./sb instance create <name>   # create + boot a WordPress site")
        print("    ./sb web                      # or open the dashboard")
        return

    # Per-project model: set up a plugin by cd-ing into its repo and running
    # `./sb init` (scaffolds sandbox.config.json + boots its own instance).
    if not getattr(args, "no_pick", False) and not server:
        print("  Next: cd into a plugin repo and run `./sb init` "
              "(or `./sb ensure`) to boot + test it.")

    if server:
        # Headless: tell the user how to reach it over an SSH tunnel from their
        # laptop (the box binds localhost only). Use the actual ports.
        insts = resolve_instances(cfg)
        wp_ports = sorted({ic["wordpress_port"] for ic in insts.values()})
        try:
            host = subprocess.check_output(["hostname"], text=True).strip()
        except Exception:
            host = "<server>"
        tunnels = " ".join(f"-L {p}:127.0.0.1:{p}" for p in wp_ports)
        print("  Reach it from your laptop over an SSH tunnel:")
        print(f"    ssh -L 8765:127.0.0.1:8765 {tunnels} <user>@{host}")
        print(f"    # then on the server:  cd {ROOT} && ./sb web")
        print(f"    # and open on your laptop:  http://localhost:8765")
        for p in wp_ports:
            print(f"    #   WordPress: http://localhost:{p}")
        return

    print("  Next:")
    print(f"    ./sb web                       # dashboard (manage instances in the browser)")
    print(f"    cd <plugin-repo> && ./sb init  # boot + test a plugin (per-project)")
    print(f"    cd {ROOT} && claude            # run Claude Code here — .mcp.json auto-loads")
    print()
    print("  Optional — connect external integrations on demand:")
    print("    ./sb connect fb     # FluentBoards (standup/report skills)")
    print("    ./sb connect gh     # GitHub (auto-detects gh CLI auth)")

    # Direct `./sb setup` on a terminal: offer to open the dashboard now. The
    # one-line installer passes --no-pick and launches the UI itself, so this
    # won't double-open there.
    if sys.stdin.isatty() and not getattr(args, "no_pick", False):
        try:
            ans = input("\n  Open the dashboard now? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"; print()
        if ans in ("", "y", "yes"):
            import types as _t
            cmd_web(load_config(), _t.SimpleNamespace(port=None, open=True))
    print()

def cmd_global(cfg, args) -> None:
    """Install (or remove) a global `sb` command so you can run it from anywhere
    instead of `cd <dir> && ./sb`.

    It's a symlink to the `sb` entry file (ENTRY). `sb` resolves its own location
    via the symlink and imports the sandbox package next to it, so it still finds
    THIS install — no wrapper, no hardcoded paths. `./sb global --remove` deletes
    the link."""
    self_path = ENTRY
    remove = getattr(args, "remove", False)
    node_ca = getattr(args, "node_ca", False)

    if node_ca:
        _configure_node_extra_ca()
        return

    if remove:
        # Remove any `sb` on PATH that points back at this install.
        removed = []
        for p in os.environ.get("PATH", "").split(os.pathsep):
            link = Path(p) / "sb"
            try:
                if link.is_symlink() and link.resolve() == self_path:
                    if os.access(link.parent, os.W_OK):
                        link.unlink()
                    else:
                        _sudo(["rm", "-f", str(link)],
                              reason="Sandbox is removing its global `sb` command.",
                              capture_output=True, text=True)
                    removed.append(str(link))
            except OSError:
                continue
        if removed:
            ok("Removed global `sb`: " + ", ".join(removed))
        else:
            info("No global `sb` symlink pointing at this install was found.")
        return

    target_dir, needs_sudo = _global_link_dir()
    target = target_dir / "sb"

    # Already linked here and pointing at us? Done.
    if target.is_symlink():
        try:
            if target.resolve() == self_path:
                ok(f"`sb` is already global → {target}")
                return
        except OSError:
            pass
    if target.exists() and not target.is_symlink():
        die(f"{target} already exists and isn't a symlink. Move it aside, then "
            f"re-run, or pick another PATH dir.")

    target_dir.mkdir(parents=True, exist_ok=True)
    if needs_sudo:
        info(f"Linking {target} → {self_path} (needs your password once).")
        r = _sudo(["ln", "-sf", str(self_path), str(target)],
                  reason="Sandbox is installing a global `sb` command in "
                         "/usr/local/bin so you can run it from any folder.",
                  capture_output=True, text=True)
        if r.returncode != 0:
            die(f"failed to create symlink: {(r.stderr or '').strip()}")
    else:
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(self_path)

    ok(f"Installed global `sb` → {target}")

    # If the chosen dir isn't on PATH, tell the user how to add it.
    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if target_dir not in path_dirs:
        shell = os.environ.get("SHELL", "")
        rc = "~/.zshrc" if shell.endswith("zsh") else "~/.bashrc"
        info(f"{target_dir} isn't on your PATH yet. Add it:")
        print(f'    echo \'export PATH="{target_dir}:$PATH"\' >> {rc}')
        print(f"    source {rc}")
        print("  Then `sb` works from anywhere. (New terminals pick it up "
              "automatically.)")
    else:
        print("  Now run `sb` from any folder — e.g. `sb web`, `sb instances`.")


def _node_ca_sources() -> list[Path]:
    """Candidate local CA roots for Node-based MCP proxies.

    Sandbox .tst domains use mkcert; Herd/Valet .test domains use Herd's local
    CA. Node's NODE_EXTRA_CA_CERTS accepts one file, so we combine any roots
    present on this machine into a generated bundle.
    """
    out: list[Path] = []
    if shutil.which("mkcert"):
        r = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True)
        if r.returncode == 0 and (r.stdout or "").strip():
            out.append(Path(r.stdout.strip()) / "rootCA.pem")
    out.append(Path.home() / "Library" / "Application Support" / "Herd" /
               "config" / "valet" / "CA" / "LaravelValetCASelfSigned.pem")
    return [p for p in out if p.exists()]


def _configure_node_extra_ca() -> None:
    sources = _node_ca_sources()
    if not sources:
        die("no local CA roots found. Run `./sb domains setup` for mkcert, "
            "or `herd secure` for Herd/Valet, then retry.")

    BASE.mkdir(parents=True, exist_ok=True)
    bundle = BASE / "node-extra-ca-certs.pem"
    text = []
    for src in sources:
        text.append(f"# {src}\n")
        text.append(src.read_text())
        text.append("\n")
    bundle.write_text("".join(text))

    if sys.platform == "darwin":
        # `launchctl setenv` persists this for GUI apps launched from the Dock/
        # Spotlight, which don't inherit a shell's env the way a terminal-
        # launched process does. `launchctl` doesn't exist at all on Linux —
        # calling it there raises FileNotFoundError regardless of check=False
        # (that flag only covers a nonzero exit, not a missing executable).
        subprocess.run(["launchctl", "setenv", "NODE_EXTRA_CA_CERTS", str(bundle)],
                       check=False)
    os.environ["NODE_EXTRA_CA_CERTS"] = str(bundle)
    ok(f"NODE_EXTRA_CA_CERTS → {bundle}")
    for src in sources:
        print(f"  included: {src}")
    if sys.platform == "darwin":
        print("  Restart GUI MCP clients/Codex/Cursor/VS Code so they inherit it.")
    else:
        print("  Add to your shell rc so GUI-launched apps inherit it too:")
        print(f'    export NODE_EXTRA_CA_CERTS="{bundle}"')

def cmd_onboard(cfg, args) -> None:
    """Run the guided onboarding against an existing instance (default: main).
    The same flow as `./sb instance create` offers — pick plugins/projects,
    enable trusted https, set Claude focus, seed content — so a freshly
    installed sandbox can be configured without recreating it. Used by the
    installer after `./sb setup`, and runnable anytime: `./sb onboard`."""
    name = args.resolved_instance
    if name not in resolve_instances(cfg):
        die(f"unknown instance '{name}'. Run: ./sb instances")
    print(f"\n▸ Onboarding '{name}' — set up plugins, a custom domain, and focus.")
    _onboard_instance(cfg, name, args)

    # The instance already serves a clean http://<name>.tst (no port, no cert).
    # HTTPS is optional — just mention it, don't run the cert flow here.
    inst = resolve_instances(cfg)[name]
    dom = inst.get("domain")
    if dom and dom.endswith(f".{_tld(inst)}"):
        cert, _ = _cert_paths(dom)
        if not cert.exists():
            info(f"want https? run:  ./sb secure {name}   "
                 f"(one-time, trusts a local cert)")

    url = site_url(resolve_instances(cfg)[name])
    print()
    ok(f"'{name}' is set up — {url}/wp-admin  (admin / admin)")
    print(f"  Tell Claude in chat:  focus <plugin>")

def cmd_apply_config(cfg, args) -> None:
    """`./sb apply --project-dir DIR` — reconcile a running instance with its
    project config in place (no DB/uploads loss). The MCP server's apply_config
    tool wraps this."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    label = getattr(args, "label", None)
    try:
        result = wordpress_runtime_service(cfg).invoke(OperationRequest(
            project_root=pd,
            operation="apply",
            label=label or "default",
        ))
    except sc.ConfigError as e:
        die(str(e))
    if isinstance(result, OperationError):
        die(result.message)
    entry = dict(result.data)
    if getattr(args, "json", False):
        print(json.dumps(entry))
    else:
        ok(f"instance '{entry['instance']}' reconciled in place "
           f"(no data loss) at {entry.get('url', '')}")

register({
    'setup': cmd_setup,
    'apply': cmd_apply_config,
    'onboard': cmd_onboard,
    'global': cmd_global,
    'connect': cmd_connect,
})
