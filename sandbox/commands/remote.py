from __future__ import annotations
import json
import os
import subprocess
import sys

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
            "`./sb remote add myvps <ssh-connection>`")
    return name


def _cmd_add(args, as_json: bool) -> None:
    name = _require_name(args)
    ssh_url = getattr(args, "ssh_url", None)
    if not ssh_url:
        die("`./sb remote add <name> <ssh_url>` requires an ssh_url, "
            "e.g. `./sb remote add myvps <ssh-connection>`")
    try:
        name = sr.validate_remote_name(name)
    except ValueError as e:
        die(str(e))
    try:
        ssh_target = sr.remote_ssh_parts(ssh_url)["target"]
        port = sr.remote_ssh_parts(ssh_url)["port"]
    except ValueError as e:
        die(str(e))
    if port:
        ssh_target = f"{ssh_target}:{port}"
    entry = sr.put_remote(name, ssh=ssh_target, provisioned=False)
    result = {"ok": True, "name": name, "ssh_configured": bool(entry.get("ssh")), "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"registered remote '{name}'")
        print("  next: ./sb remote provision " + name)


def _cmd_list(args, as_json: bool) -> None:
    remotes = sr.list_remotes()
    rows = []
    for name, entry in sorted(remotes.items()):
        reachable = sr.check_reachable(entry)
        rows.append({
            "name": name,
            "ssh_configured": bool(entry.get("ssh")),
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
        print(f"  {r['name']}  {reach}, {prov}")


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


def _upload_runtime_source(ssh_target: str) -> None:
    """Stage this checkout onto the VPS so provisioning never depends on
    GitHub reachability or repo visibility. Fresh VPS validation caught that
    cloning templately/sandbox anonymously can fail for private/internal repos."""
    excludes = [
        ".git",
        ".cli-venv",
        "mcp/wp-server/.venv",
        "runtime",
        "tmp",
        "__pycache__",
        ".pytest_cache",
    ]
    tar_cmd = ["tar"]
    for item in excludes:
        tar_cmd.extend(["--exclude", item])
    tar_cmd.extend(["-czf", "-", "."])
    remote_cmd = (
        "set -e; sandbox_home=${SANDBOX_HOME:-$HOME/sandbox}; "
        "rm -rf \"$sandbox_home/sb-src\"; "
        "mkdir -p \"$sandbox_home/sb-src\"; "
        "tar -xzf - -C \"$sandbox_home/sb-src\""
    )
    tar_res = subprocess.run(
        tar_cmd, cwd=str(ROOT), capture_output=True, timeout=300, check=False,
    )
    if tar_res.returncode != 0:
        raise RuntimeError(
            f"could not package the local sandbox runtime: "
            f"{tar_res.stderr.decode(errors='replace').strip()[:500]}"
        )
    ssh_res = subprocess.run(
        sr.ssh_command_args(ssh_target, remote_cmd),
        input=tar_res.stdout, capture_output=True, text=False, timeout=300, check=False,
    )
    if ssh_res.returncode != 0:
        detail = (ssh_res.stderr or ssh_res.stdout or b"").decode(errors="replace")
        raise RuntimeError(f"could not upload sandbox runtime: {detail.strip()[:500]}")


def _arg_str(args, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) and value.strip() else None


def _arg_true(args, name: str) -> bool:
    return getattr(args, name, None) is True


def _choose_control_transport(args, as_json: bool) -> str:
    explicit = _arg_str(args, "control")
    if explicit in {"https", "tailscale"}:
        return explicit
    if as_json or _arg_true(args, "yes") or not sys.stdin.isatty():
        return "https"
    ans = input("Use Tailscale private control plane instead of public HTTPS? [y/N] ")
    return "tailscale" if ans.strip().lower() in {"y", "yes"} else "https"


def _ssh_host(ssh_target: str) -> str:
    return sr.ssh_host(ssh_target)


def _control_host(args, entry: dict, ssh_target: str, as_json: bool) -> str:
    host = _arg_str(args, "control_host") or entry.get("control_host")
    if isinstance(host, str) and host.strip():
        return host.strip()
    if as_json or _arg_true(args, "yes") or not sys.stdin.isatty():
        die("public HTTPS control requires --control-host, e.g. "
            "`./sb remote provision myvps --control-host sandbox.example.com`")
    default = _ssh_host(ssh_target)
    prompt = f"Public HTTPS hostname for this remote [{default}]: "
    entered = input(prompt).strip()
    return entered or default


def _cmd_provision(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}' — register it first with "
            f"`./sb remote add {name} <ssh-connection>`")
    script_path = os.path.join(ROOT, "scripts", "install-remote.sh")
    with open(script_path) as f:
        script = f.read()
    # bash -s reads the script from stdin; ssh_run's helper only runs a single
    # command string (no stdin piping), so transfer the script inline as
    # base64 over the SSH argument to avoid quoting issues with its content.
    import base64
    encoded = base64.b64encode(script.encode()).decode()
    ssh_target = entry.get("ssh") or ""
    if not ssh_target:
        die(f"remote '{name}' has no ssh connection string configured")
    control_transport = _choose_control_transport(args, as_json)
    public_host = None
    if control_transport == "https":
        public_host = _control_host(args, entry, ssh_target, as_json)
    try:
        _upload_runtime_source(ssh_target)
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        die(f"could not stage the sandbox runtime on '{name}': "
            f"{sr.redact_ssh_connection(str(e), entry)}")
    cmd = (
        f"echo {encoded} | base64 -d | "
        f"SANDBOX_CONTROL_TRANSPORT={control_transport} bash -s"
    )
    try:
        res = subprocess.run(
            sr.ssh_command_args(ssh_target, cmd),
            capture_output=True, text=True, timeout=1800, check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        die(f"provisioning '{name}' failed: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
    if res.returncode != 0:
        detail = sr.redact_ssh_connection(
            (res.stderr or res.stdout or "").strip()[:1000], entry
        )
        die(f"provisioning '{name}' failed: {detail}")
    token = sr.mint_bearer_token()
    port = sr.DEFAULT_MCP_PORT
    entry = sr.get_remote(name)
    try:
        if control_transport == "tailscale":
            tailscale_ip = sr.resolve_tailscale_ip(entry)
            control_url = f"http://{tailscale_ip}:{port}"
            bind = tailscale_ip
            sr.start_remote_mcp_server(entry, bind, port, token)
            sr.put_remote(name, control_transport="tailscale",
                          control_url=control_url, tailscale_host=tailscale_ip,
                          mcp_port=port, bearer_token=token, provisioned=True)
        else:
            control_url = f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            sr.start_remote_mcp_server(entry, "127.0.0.1", port, token,
                                       public_url=control_url)
            sr.put_remote(name, control_transport="https",
                          control_host=public_host, control_url=control_url,
                          mcp_port=port, bearer_token=token, provisioned=True)
            tailscale_ip = None
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        die(f"could not start the remote MCP server on '{name}': "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    # The token is shown here ONCE, at mint time only -- same pattern as an
    # AWS access key or GitHub PAT. It's never echoed again after this: not
    # by `remote list`, not by any other command that reads the stored
    # entry back (real bug caught by /speckit-analyze -- this success
    # message used to claim "bearer token minted above" while never actually
    # printing it, leaving no way to complete the second-MCP-server setup).
    result = {"ok": True, "name": name, "provisioned": True,
             "control_transport": control_transport, "control_url": control_url,
             "tailscale_host": tailscale_ip, "mcp_port": port,
             "bearer_token": token, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' provisioned and its MCP server is reachable at {control_url}")
        print(f"  bearer token (shown once, save it now): {token}")
        print(f"  register it in Claude Code as a second MCP server "
              f"(transport: http, url: {control_url}) "
              f"— see docs/remote-hosting.md")


def _cmd_up(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry or not entry.get("provisioned"):
        die(f"remote '{name}' is not provisioned yet — run "
            f"`./sb remote provision {name}` first")
    control_transport = entry.get("control_transport") or (
        "tailscale" if entry.get("tailscale_host") else "https"
    )
    control_url = entry.get("control_url")
    port = entry.get("mcp_port") or sr.DEFAULT_MCP_PORT
    token = entry.get("bearer_token")
    if not token:
        die(f"remote '{name}' is missing recorded connection details — "
            f"re-run `./sb remote provision {name}`")
    try:
        if control_transport == "tailscale":
            tailscale_ip = entry.get("tailscale_host") or sr.resolve_tailscale_ip(entry)
            control_url = control_url or f"http://{tailscale_ip}:{port}"
            sr.start_remote_mcp_server(entry, tailscale_ip, port, token)
        else:
            public_host = entry.get("control_host")
            if not public_host:
                die(f"remote '{name}' is missing its HTTPS control host — "
                    f"re-run `./sb remote provision {name} --control-host <host>`")
            control_url = control_url or f"https://{public_host}"
            sr.configure_https_proxy(entry, public_host, port)
            sr.start_remote_mcp_server(entry, "127.0.0.1", port, token,
                                       public_url=control_url)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        die(f"could not start '{name}''s MCP server: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    result = {"ok": True, "name": name, "control_transport": control_transport,
             "control_url": control_url, "mcp_port": port, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server is up at {control_url}")


def _cmd_down(args, as_json: bool) -> None:
    name = _require_name(args)
    entry = sr.get_remote(name)
    if not entry:
        die(f"no remote named '{name}'")
    try:
        sr.stop_remote_mcp_server(entry)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as e:
        die(f"could not stop '{name}''s MCP server: "
            f"{sr.redact_ssh_connection(str(e), entry)}")
        return
    result = {"ok": True, "name": name, "error": None}
    if as_json:
        print(json.dumps(result))
    else:
        ok(f"'{name}' MCP server stopped — running WordPress instances there "
           f"are unaffected")


register({'remote': cmd_remote})
