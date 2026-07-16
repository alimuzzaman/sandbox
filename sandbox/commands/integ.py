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



def cmd_mcp_install(cfg, args) -> None:
    MCP_DIR.mkdir(parents=True, exist_ok=True)
    if MCP_VENV.exists():
        # Rebuild if existing venv is on too-old Python (caused install failure earlier).
        py_in_venv = MCP_VENV / "bin" / "python"
        try:
            v = subprocess.check_output(
                [str(py_in_venv), "-c", "import sys;print(sys.version_info[:2])"], text=True
            ).strip()
            if eval(v) < (3, 10):
                info("Existing venv uses Python <3.10; rebuilding…")
                shutil.rmtree(MCP_VENV)
        except Exception:
            shutil.rmtree(MCP_VENV)
    if not MCP_VENV.exists():
        py = find_modern_python()
        info(f"Creating mcp/wp-server/.venv (using {py}) …")
        _make_venv(py, MCP_VENV)
    vpy = MCP_VENV / "bin" / "python"
    # Use `python -m pip` (not the pip shim) — works even when pip was just
    # bootstrapped via ensurepip into a --without-pip venv.
    run([str(vpy), "-m", "pip", "install", "--upgrade", "--quiet", "pip"])
    run([str(vpy), "-m", "pip", "install", "--quiet", "-r",
         str(MCP_DIR / "requirements.txt")])
    ok("wp-mcp dependencies installed")

def cmd_claude(cfg, args) -> None:
    """Launch `claude` with the sandbox + focused plugin context guaranteed loaded.

    Without this wrapper, devs who run `claude` from anywhere except the
    sandbox root miss the sandbox CLAUDE.md and the focused plugin's
    CLAUDE.md / skills. Forcing --add-dir for both makes the context
    load deterministic regardless of cwd.

    `./sb claude --write-config` retains the old behavior (rewrite
    .mcp.json) for setup flows.
    """
    if args.write_config:
        if not (MCP_VENV / "bin" / "python").exists():
            info("wp-mcp venv not built yet. Run: ./sb mcp-install")
        path, created = write_claude_mcp_config(cfg)
        ok(f"{'Wrote' if created else 'Updated'} {path}")
        return

    claude_bin = shutil.which("claude")
    if not claude_bin:
        die("claude CLI not found in PATH — install Claude Code first.")

    inst = args.resolved_instance
    ff = focus_file(inst)
    plug_dir = plugins_dir(inst)
    add_dirs = [str(ROOT)]
    if ff.exists():
        slug = ff.read_text().strip()
        link = plug_dir / slug
        if link.exists():
            plugin_src = str(link.resolve())
            if plugin_src not in add_dirs:
                add_dirs.append(plugin_src)
            info(f"focus: {slug}  ({plugin_src})")
        else:
            info(f"focus: {slug}  (not linked in this instance)")
    else:
        info("focus: (none — set with `./sb focus <slug>`)")

    cmd = [claude_bin, "--add-dir", *add_dirs, *args.passthrough]
    info(f"launching: claude --add-dir {' '.join(add_dirs)}")
    os.execv(claude_bin, cmd)

def cmd_mcp(cfg, args) -> None:
    """Run the sandbox MCP server. Default: stdio, ONE server for all local
    projects — every tool resolves its target instance per call from
    `project_dir`.

    Register once (then `cd` into any plugin and use the tools):
        claude mcp add --scope user sandbox -- ./sb mcp

    `--transport streamable-http` (spec 014, remote VPS hosting) is started by
    `./sb remote provision` on a VPS, never invoked directly for local use —
    it binds to `--bind` (which MUST be a Tailscale interface address, never
    `0.0.0.0` — spec FR-014) on `--port`, requiring `--token` on every request.
    """
    py = MCP_VENV / "bin" / "python"
    server = MCP_DIR / "server.py"
    if not py.exists():
        die("MCP venv not built — run `./sb mcp-install` first.")
    if not server.exists():
        die(f"MCP server not found at {server}")
    argv = [str(py), str(server)]
    transport = getattr(args, "transport", "stdio")
    if transport == "streamable-http":
        bind = getattr(args, "bind", None)
        port = getattr(args, "port", None)
        token = getattr(args, "token", None)
        if not bind or bind == "0.0.0.0":
            die("--bind is required for --transport=streamable-http and must "
                "be a specific Tailscale interface address, never 0.0.0.0 "
                "(spec FR-014 — never expose this to the public internet)")
        if not port:
            die("--port is required for --transport=streamable-http")
        if not token:
            die("--token is required for --transport=streamable-http")
        argv += ["--transport", "streamable-http", "--bind", bind,
                 "--port", str(port), "--token", token]
        public_url = getattr(args, "public_url", None)
        if public_url:
            argv += ["--public-url", public_url]
    # Replace this process with the server (FastMCP mcp.run()).
    os.execv(str(py), argv)

register({
    'mcp': cmd_mcp,
    'claude': cmd_claude,
    'mcp-install': cmd_mcp_install,
})
