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


def mcp_server_name(instance: str = "") -> str:
    """The Claude MCP server name. Per-project rewrite: ONE 'sandbox' server
    routes every project by `project_dir`, so this is a constant now — the old
    per-instance `sandbox-<name>` scheme is gone. Kept as a function so the
    existing call sites don't all need editing."""
    return MCP_SERVER_NAME


def _build_mcp_entry(cfg: dict | None = None) -> dict:
    """The SINGLE 'sandbox' MCP server registration entry. One server for all
    projects: it takes NO per-instance env — every tool routes by `project_dir`
    and resolves the instance from the on-disk registry per call. Launched via
    `<sb> mcp` (the stdio entrypoint), which execs the venv server.py.

    Prefer the PATH-resolved `sb` name (set by `./sb global`) so the
    registration survives the repo being moved or re-cloned — same as how
    @wordpress/env uses a PATH-based npm bin. Fall back to the absolute path
    when `sb` isn't on PATH yet."""
    sb_on_path = shutil.which("sb")
    command = "sb" if sb_on_path else str(ROOT / "sb")
    entry = {"command": command, "args": ["mcp"]}
    # Spec 009: if the base is explicitly overridden, bake it into the MCP
    # registration so the (separate) server process resolves the SAME base as
    # the CLI. When unset, both default to ~/sandbox — no env needed (FR-006/C4).
    home = os.environ.get("SANDBOX_HOME")
    if home:
        entry["env"] = {"SANDBOX_HOME": str(Path(home).expanduser().resolve())}
    return entry


def _stale_mcp_servers(claude_bin: str) -> list[str]:
    """Per-instance server names (`sandbox-<name>`) left by pre-rewrite
    registrations, so the single-server migration can clean them up."""
    res = subprocess.run([claude_bin, "mcp", "list"],
                         capture_output=True, text=True)
    out = []
    for line in (res.stdout or "").splitlines():
        tok = line.split(":")[0].strip()
        if tok.startswith("sandbox-"):
            out.append(tok)
    return out


def register_claude_user_scope(cfg: dict) -> None:
    """Register the SINGLE 'sandbox' MCP server at user scope so every `claude`
    session reaches it from any directory. The server routes by `project_dir`,
    so one registration serves every project — no per-instance servers.

    Idempotent (wipe-then-add). Also removes legacy registrations: the
    pre-rename `wp-sandbox`, and any `sandbox-<name>` per-instance servers from
    the pre-rewrite multi-instance model.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        info("claude CLI not in PATH — skipping user-scope MCP registration.")
        return

    # Clean up legacy / stale registrations (pre-rename + per-instance).
    for stale in ["wp-sandbox", *_stale_mcp_servers(claude_bin)]:
        subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", stale],
                       capture_output=True, text=True)

    entry = _build_mcp_entry(cfg)
    subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", MCP_SERVER_NAME],
                   capture_output=True, text=True)
    env_flags = []
    for k, v in (entry.get("env") or {}).items():
        env_flags += ["-e", f"{k}={v}"]
    cmd = [claude_bin, "mcp", "add", "--scope", "user", MCP_SERVER_NAME,
           *env_flags, "--", entry["command"], *entry.get("args", [])]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        ok(f"Registered MCP server '{MCP_SERVER_NAME}' at user scope "
           f"(tools: mcp__{MCP_SERVER_NAME}__*; routes by project_dir).")
    else:
        info(f"MCP registration failed: {res.stderr.strip()}")
        info("Project-local .mcp.json still works when cwd is the sandbox.")


def write_claude_mcp_config(cfg: dict) -> tuple[Path, bool]:
    """Write the project-local .mcp.json with the SINGLE 'sandbox' server.

    Claude Code auto-loads .mcp.json from the working directory; the user-scope
    registration (register_claude_user_scope) is what makes it reachable outside
    the sandbox folder. Drops any stale `sandbox-<name>` entries from the old
    per-instance model.
    """
    existing = {}
    created = not PROJECT_MCP_JSON.exists()
    if not created:
        try:
            existing = json.loads(PROJECT_MCP_JSON.read_text()) or {}
        except json.JSONDecodeError:
            backup = PROJECT_MCP_JSON.with_suffix(".json.bak")
            PROJECT_MCP_JSON.rename(backup)
            info(f"existing .mcp.json was invalid JSON — backed up to {backup}")
            existing = {}
            created = True
    servers = existing.setdefault("mcpServers", {})
    # Drop stale per-instance entries from the pre-rewrite model.
    for name in list(servers):
        if name.startswith("sandbox-"):
            del servers[name]
    servers[MCP_SERVER_NAME] = _build_mcp_entry(cfg)
    PROJECT_MCP_JSON.write_text(json.dumps(existing, indent=2) + "\n")
    return PROJECT_MCP_JSON, created


def _connect_fluentboards(cfg, non_interactive: bool = False) -> None:
    local = _local_yaml()
    fb = local.setdefault("fluentboards", {})

    if non_interactive:
        # Read from environment; require at least URL + app password.
        url = os.environ.get("FLUENTBOARDS_URL", "").strip()
        email = os.environ.get("FLUENTBOARDS_EMAIL", "").strip()
        pw = os.environ.get("FLUENTBOARDS_APP_PASSWORD", "").strip()
        if not url or not pw:
            die("--non-interactive requires FLUENTBOARDS_URL and "
                "FLUENTBOARDS_APP_PASSWORD to be set in the environment.")
        fb["url"] = url
        fb["email"] = email
        fb["app_password"] = pw
        _write_local_yaml(local)
        _refresh_env_local()
        ok(f"FluentBoards credentials saved (non-interactive) to "
           f"{CONFIG_LOCAL.name} + {SECRETS_ENV.name}")
        return

    print("\nFluentBoards")
    print("  Used by the standup/report skills to read your My Day cards.")
    print("  Generate an Application Password at:")
    print("    https://projects.startise.com/wp-admin/profile.php#application-passwords-section")
    print("  Press Enter at any prompt to skip / keep existing value.")
    fb["url"] = _prompt("Site URL",
                        fb.get("url") or "https://projects.startise.com")
    fb["email"] = _prompt("Login email", fb.get("email", ""))
    fb["app_password"] = _prompt("Application password",
                                 fb.get("app_password", ""), secret=True)
    _write_local_yaml(local)
    _refresh_env_local()
    ok(f"Saved to {CONFIG_LOCAL.name} + {SECRETS_ENV.name}")


def _gh_cli_user() -> str | None:
    """Return the GitHub username if `gh` is installed AND authenticated."""
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "auth", "status"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    # `gh api user` is the cleanest source-of-truth for the logged-in handle.
    r = subprocess.run(["gh", "api", "user", "-q", ".login"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def _gh_cli_orgs() -> list[str]:
    """Return GitHub orgs the gh-authenticated user belongs to (or [] if none/no-gh)."""
    if not shutil.which("gh"):
        return []
    r = subprocess.run(["gh", "api", "user/orgs", "--jq", ".[].login"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def _connect_github(cfg, non_interactive: bool = False) -> None:
    local = _local_yaml()
    defaults = local.setdefault("defaults", {})
    cur = (defaults.get("github_org")
           or (cfg.get("defaults", {}) or {}).get("github_org") or "")

    if non_interactive:
        org = os.environ.get("GITHUB_ORG", "").strip()
        if not org:
            die("--non-interactive requires GITHUB_ORG to be set in the environment.")
        defaults["github_org"] = org
        _write_local_yaml(local)
        _refresh_env_local()
        ok(f"github_org='{org}' saved (non-interactive) to "
           f"{CONFIG_LOCAL.name} + {SECRETS_ENV.name}")
        return

    print("\nGitHub")
    print("  Sets defaults.github_org + ensures the `gh` CLI can read private")
    print("  repos (Pro plugins, private mappings) during git/composer operations.")
    print()

    gh_user = _gh_cli_user()
    orgs = _gh_cli_orgs() if gh_user else []

    if gh_user:
        ok(f"`gh` CLI authenticated as: {gh_user}")

    # Build a numbered menu when we have anything to suggest. Otherwise
    # fall back to a free-form prompt. The current value (if any) is
    # always offered as one of the choices, never silently re-confirmed —
    # so a wrong saved value (a common state for new WPDev hires whose
    # gh login is their personal handle) can't shadow the right answer.
    choices: list[str] = []
    if "WPDevelopers" in orgs:
        choices.append("WPDevelopers")
    for o in orgs:
        if o not in choices:
            choices.append(o)
    if gh_user and gh_user not in choices:
        choices.append(gh_user)
    if cur and cur not in choices:
        choices.append(cur)

    entered = ""
    if choices:
        print()
        print("  Pick a GitHub org/user (default repo resolution falls back here):")
        for i, c in enumerate(choices, 1):
            marker = "  (current)" if c == cur else ("  (recommended for WPDev team)"
                                                    if c == "WPDevelopers" else "")
            print(f"    {i})  {c}{marker}")
        print(f"    {len(choices)+1})  other (type a value)")
        try:
            raw = input("  Pick: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(choices):
                entered = choices[idx - 1]
            elif idx == len(choices) + 1:
                entered = _prompt("Enter org/user", "").strip()
        else:
            entered = raw  # treat as a typed org name
    else:
        entered = _prompt("GitHub org/user", cur).strip()

    if not entered:
        info("(left blank — set later with `./sb connect gh`)")
        return

    defaults["github_org"] = entered
    _write_local_yaml(local)
    _refresh_env_local()
    ok(f"Saved github_org='{entered}' to {CONFIG_LOCAL.name} + {SECRETS_ENV.name}")


def _global_link_dir() -> tuple[Path, bool]:
    """Pick where to drop the global `sb` symlink. Returns (dir, needs_sudo).

    Preference order, first that's on PATH:
      1. a user dir we can write without sudo  (~/.local/bin, ~/bin)
      2. Homebrew bin                          (/opt/homebrew/bin, /usr/local/bin)
      3. /usr/local/bin with sudo
    Falling back to ~/.local/bin (creating it) if nothing on PATH is writable —
    the caller then tells the user to add it to PATH."""
    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    on_path = lambda d: d in path_dirs
    home = Path.home()
    # 1. user-writable dirs already on PATH (no sudo)
    for d in (home / ".local" / "bin", home / "bin",
              Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if on_path(d) and os.access(d, os.W_OK):
            return d, False
    # 2. /usr/local/bin on PATH but not writable → sudo
    ulb = Path("/usr/local/bin")
    if on_path(ulb) and ulb.exists():
        return ulb, True
    # 3. last resort: ~/.local/bin (create it), warn about PATH
    return home / ".local" / "bin", False


def _git_q(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def _install_alias_launchd() -> None:
    """Install a LaunchDaemon that re-adds the lo0 alias on boot (the alias is
    not persistent). Best-effort — failure just means `./sb domains up` (or the
    lazy alias-up in cmd_up) restores it after a reboot."""
    # Idempotent: this install needs an INTERACTIVE sudo (it's not covered by the
    # passwordless proxy-helper rule), so skip it once the plist exists —
    # otherwise every `ensure` (now secured at create) re-prompts for the Mac
    # password. .exists() needs no read permission on the root-owned file.
    if LAUNCHD_PLIST.exists():
        return
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sandbox.lo0alias</string>
  <key>RunAtLoad</key><true/>
  <key>ProgramArguments</key>
  <array>
    <string>/sbin/ifconfig</string><string>lo0</string>
    <string>alias</string><string>{PROXY_BIND_IP}</string><string>up</string>
  </array>
</dict>
</plist>
"""
    tmp = RUNTIME_DIR / "com.sandbox.lo0alias.plist"
    tmp.write_text(plist)
    _LAUNCHD_REASON = (
        "Sandbox would like to keep your clean URLs working after a reboot. It "
        "adds a small startup item that re-enables local *.tst sites. You can "
        "remove it anytime with ./sb uninstall.")
    res = _sudo(
        ["install", "-m", "0644", "-o", "root", "-g",
         "wheel" if sys.platform == "darwin" else "root",
         str(tmp), str(LAUNCHD_PLIST)], reason=_LAUNCHD_REASON,
        capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if res.returncode == 0:
        _sudo(["launchctl", "load", "-w", str(LAUNCHD_PLIST)],
              reason=_LAUNCHD_REASON, capture_output=True, text=True)
        ok("installed boot-time loopback-alias LaunchDaemon")
    else:
        info("skipped LaunchDaemon (alias restored by `./sb domains up` after "
             "reboot)")
