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



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register



def cmd_uninstall(cfg, args) -> None:
    from sandbox.commands.config_setup import cmd_global
    """Tear down the whole sandbox install: stop + remove every instance's
    containers + DB volumes, remove the HTTPS proxy + domain config, deregister
    the MCP servers from Claude, and (optionally) delete this install directory.
    Reversible only by reinstalling. Asks once unless --yes."""
    instances = list(resolve_instances(cfg).keys())
    print()
    print("  This will REMOVE the entire sandbox:")
    print(f"    • containers + DB volumes for: {', '.join(instances)}")
    print(f"    • the HTTPS proxy + *.tst domain config (if set up)")
    print(f"    • the MCP servers registered with Claude")
    print(f"    • optionally, this folder: {ROOT}")
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            die("refusing to uninstall non-interactively without --yes.")
        try:
            ans = input("\n  Type 'remove' to confirm: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if ans != "remove":
            info("cancelled."); return

    # 1. Stop + wipe every instance (containers + volumes).
    for name in instances:
        print(f"\n▸ Removing instance '{name}' (containers + volume)…")
        compose("down", "-v", instance=name, check=False)

    # 2. Tear down the HTTPS proxy + domains (untrust CA, dnsmasq, alias).
    if proxy_available() or _proxy_container_running():
        print("\n▸ Tearing down the HTTPS proxy + domains…")
        try:
            proxy_teardown(cfg)
        except Exception as e:
            info(f"proxy teardown had issues (continuing): {e}")

    # 3. Deregister the MCP server from Claude (user scope). One 'sandbox'
    #    server now, plus any stale per-instance (`sandbox-<name>`) + legacy
    #    `wp-sandbox` registrations from older versions.
    claude_bin = shutil.which("claude")
    if claude_bin:
        print("\n▸ Deregistering MCP server from Claude…")
        for srv in [MCP_SERVER_NAME, "wp-sandbox", *_stale_mcp_servers(claude_bin)]:
            subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", srv],
                           capture_output=True, text=True)
            info(f"removed {srv}")

    # 4. Remove the hosts-helper sudoers rule if present (best-effort).
    if SUDOERS_FILE.exists():
        subprocess.run(["sudo", "-n", "rm", "-f", str(SUDOERS_FILE)],
                       capture_output=True, text=True)

    # 5. Remove the global `sb` command if it points at this install.
    try:
        import types as _types
        cmd_global(cfg, _types.SimpleNamespace(remove=True))
    except Exception:
        pass

    ok("Sandbox uninstalled (containers, volumes, proxy, MCP servers removed).")

    # 5. Optionally delete the install directory itself.
    remove_dir = getattr(args, "purge", False)
    if not remove_dir and sys.stdin.isatty() and not getattr(args, "yes", False):
        try:
            ans = input(f"\n  Also delete the folder {ROOT}? [y/N]: ").strip().lower()
            remove_dir = ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
    if remove_dir:
        # Don't rm the cwd out from under us — print the command instead if we
        # can't safely self-delete.
        print(f"\n  To remove the folder, run:\n    rm -rf {ROOT}")
    else:
        info(f"left the folder in place: {ROOT}")

register({
    'uninstall': cmd_uninstall,
})
