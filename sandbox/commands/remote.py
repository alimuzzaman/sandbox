from __future__ import annotations
import json
import os

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register
import sandbox.core._remote as sr


# See docs/remote-hosting.md and specs/014-remote-vps-hosting/ for the full design
# this module implements. `./sb remote add/list/provision/up/down/remove` registers
# and manages a remote VPS target that sandbox can provision and deploy to
# (docs/remote-hosting-prd.md §0's resolved design). Machine-level bookkeeping only
# -- which instances a remote actually has live entirely in THAT VPS's own
# independent registry, never here (see _remote.py's module docstring).

def cmd_remote(cfg, args) -> None:
    """`./sb remote <add|list|provision|up|down|remove> [name] [ssh_url] [--json]`
    -- register and manage remote VPS targets. See docs/remote-hosting.md."""
    action = args.action
    as_json = bool(getattr(args, "json", False))
    dispatch = {
        "add": _cmd_add,
        "list": _cmd_list,
        "provision": _cmd_provision,
        "up": _cmd_up,
        "down": _cmd_down,
        "remove": _cmd_remove,
    }
    dispatch[action](args, as_json)


def _require_name(args) -> str:
    name = getattr(args, "name", None)
    if not name:
        die("a remote name is required for this action, e.g. "
            "`./sb remote add myvps ssh://user@host`")
    return name


def _cmd_add(args, as_json: bool) -> None:
    name = _require_name(args)
    ssh_url = getattr(args, "ssh_url", None)
    if not ssh_url:
        die("`./sb remote add <name> <ssh_url>` requires an ssh_url, "
            "e.g. `./sb remote add myvps ssh://ubuntu@203.0.113.10`")
    try:
        name = sr.validate_remote_name(name)
    except ValueError as e:
        die(str(e))
    ssh_target = ssh_url.removeprefix("ssh://")
    entry = sr.put_remote(name, ssh=ssh_target, provisioned=False)
    result = {"ok": True, "name": name, "ssh": entry.get("ssh"), "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"registered remote '{name}' ({entry.get('ssh')})")
        print("  next: ./sb remote provision " + name)


def _cmd_list(args, as_json: bool) -> None:
    remotes = sr.list_remotes()
    rows = []
    for name, entry in sorted(remotes.items()):
        reachable = sr.check_reachable(entry)
        rows.append({
            "name": name,
            "ssh": entry.get("ssh"),
            "reachable": reachable,
            "provisioned": bool(entry.get("provisioned")),
        })
    if as_json:
        print(json.dumps({"ok": True, "remotes": rows, "error": None}))
        return
    if not rows:
        info("no remotes registered — add one with `./sb remote add <name> <ssh_url>`")
        return
    for r in rows:
        reach = "reachable" if r["reachable"] else "unreachable"
        prov = "provisioned" if r["provisioned"] else "not provisioned"
        print(f"  {r['name']}  ({r['ssh']})  {reach}, {prov}")


def _cmd_remove(args, as_json: bool) -> None:
    name = _require_name(args)
    existed = sr.remove_remote(name)
    result = {"ok": True, "name": name, "removed": existed, "error": None}
    if as_json:
        print(json.dumps(result))
        return
    if existed:
        ok(f"forgot remote '{name}' locally — any instance already running on "
           f"that VPS is UNAFFECTED; tear it down there yourself if you no "
           f"longer need it")
    else:
        info(f"no remote named '{name}' was registered")


def _cmd_provision(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}' — register it first with "
            f"`./sb remote add {name} <ssh_url>`")
    script_path = os.path.join(ROOT, "scripts", "install-remote.sh")
    with open(script_path) as f:
        script = f.read()
    # bash -s reads the script from stdin; ssh_run's helper only runs a single
    # command string (no stdin piping), so transfer the script inline as
    # base64 over the SSH argument to avoid quoting issues with its content.
    import base64
    import subprocess
    encoded = base64.b64encode(script.encode()).decode()
    ssh_target = entry.get("ssh") or ""
    if not ssh_target:
        die(f"remote '{name}' has no ssh connection string configured")
    cmd = f"echo {encoded} | base64 -d | bash -s"
    res = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_target, cmd],
        capture_output=True, text=True, timeout=1800, check=False,
    )
    if res.returncode != 0:
        die(f"provisioning '{name}' failed: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}")
    entry = sr.get_remote(name)
    try:
        tailscale_ip = sr.resolve_tailscale_ip(entry)
    except RuntimeError as e:
        die(f"'{name}' installed OK but Tailscale isn't joined yet: {e}\n"
            f"join it manually (see scripts/install-remote.sh's "
            f"TAILSCALE_AUTHKEY note), then re-run `./sb remote provision {name}`")
        return
    token = sr.mint_bearer_token()
    port = sr.DEFAULT_MCP_PORT
    try:
        sr.start_remote_mcp_server(entry, tailscale_ip, port, token)
    except RuntimeError as e:
        die(f"could not start the remote MCP server on '{name}': {e}")
        return
    sr.put_remote(name, provisioned=True, bearer_token=token,
                  tailscale_host=tailscale_ip, mcp_port=port)
    # The token is shown here ONCE, at mint time only -- same pattern as an
    # AWS access key or GitHub PAT. It's never echoed again after this: not
    # by `remote list`, not by any other command that reads the stored
    # entry back (real bug caught by /speckit-analyze -- this success
    # message used to claim "bearer token minted above" while never actually
    # printing it, leaving no way to complete the second-MCP-server setup).
    result = {"ok": True, "name": name, "provisioned": True,
             "tailscale_host": tailscale_ip, "mcp_port": port,
             "bearer_token": token, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' provisioned and its MCP server is running at "
           f"{tailscale_ip}:{port}")
        print(f"  bearer token (shown once, save it now): {token}")
        print(f"  register it in Claude Code as a second MCP server "
              f"(transport: http, url: http://{tailscale_ip}:{port}) "
              f"— see docs/remote-hosting.md")


def _cmd_up(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry or not entry.get("provisioned"):
        die(f"remote '{name}' is not provisioned yet — run "
            f"`./sb remote provision {name}` first")
    tailscale_ip = entry.get("tailscale_host")
    port = entry.get("mcp_port") or sr.DEFAULT_MCP_PORT
    token = entry.get("bearer_token")
    if not tailscale_ip or not token:
        die(f"remote '{name}' is missing recorded connection details — "
            f"re-run `./sb remote provision {name}`")
    try:
        sr.start_remote_mcp_server(entry, tailscale_ip, port, token)
    except RuntimeError as e:
        die(f"could not start '{name}''s MCP server: {e}")
        return
    result = {"ok": True, "name": name, "tailscale_host": tailscale_ip,
             "mcp_port": port, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server is up at {tailscale_ip}:{port}")


def _cmd_down(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        sr.stop_remote_mcp_server(entry)
    except (RuntimeError, ValueError) as e:
        die(f"could not stop '{name}''s MCP server: {e}")
        return
    result = {"ok": True, "name": name, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server stopped — running WordPress instances there "
           f"are unaffected")


register({'remote': cmd_remote})
