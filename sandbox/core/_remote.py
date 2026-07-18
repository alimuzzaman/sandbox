"""Remote VPS hosting for sandbox instances (spec 014).

Lets a developer register an already-running VPS they manage themselves, provision
it with one command, and deploy their current local project state to it on demand
(git push + a replace-not-stack uncommitted-diff apply -- never a continuous sync
daemon). See docs/remote-hosting.md and specs/014-remote-vps-hosting/ for the full
design; docs/remote-hosting-prd.md is the deeper feasibility research this was built
from.

Per-machine config lives in the gitignored secret store (`sandbox.local.yml` under
$SANDBOX_HOME), the same store that already holds the snapshot-bridge token and
pro-license keys. Layout:

    remotes:
      myvps:
        ssh: "ubuntu@203.0.113.10"
        control_transport: "https"                     # https (default) or tailscale
        control_url: "https://sandbox.example.com"      # recorded by `provision`
        tailscale_host: "myvps.tailnet-name.ts.net"     # only for tailscale mode
        mcp_port: 9174                                  # recorded by `provision`
        bearer_token: "<secret, never echoed>"         # minted by `provision`
        provisioned: false

Key architectural decision (see research.md): each machine's own
$SANDBOX_HOME/runtime/registry.json is independently authoritative for that
machine's instances. There is no shared registry field describing "this instance
lives on myvps" -- a remote instance is reached by calling the same tool names
against a SECOND, separately registered MCP server (`sandbox-<remote-name>`), not
by a field on a shared record. This module only tracks how to REACH a remote and
whether it's provisioned -- never what instances it has.
"""
from __future__ import annotations
import os
import re
import secrets
import shlex
import posixpath
import json
import hashlib
import ipaddress
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sandbox.core import *  # noqa: F401,F403
from sandbox.core._config import ensure_pyyaml, _local_yaml
from sandbox.core._paths import CONFIG_LOCAL, RUNTIME_DIR

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# OpenSSH multiplexing removes the repeated TCP/KEX/authentication cost while
# still keeping each command a separately isolated SSH session. Ten minutes is
# long enough to cover a deploy/provision workflow without leaving an idle
# authenticated connection indefinitely (the same bounded pattern commonly
# used by Ansible's native OpenSSH transport).
_CONTROL_PERSIST_SECONDS = 600


def _remote_block() -> dict:
    """The `remotes:` mapping from sandbox.local.yml (empty if unset)."""
    return dict((_local_yaml().get("remotes") or {}))


def _write_remote_block(block: dict) -> None:
    """Persist the `remotes:` mapping back into sandbox.local.yml, preserving
    the rest of the file. Mirrors _licensing.py's _write_licensing_block."""
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    if block:
        local["remotes"] = block
    else:
        local.pop("remotes", None)
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)
    try:
        CONFIG_LOCAL.chmod(0o600)  # secret store stays owner-only
    except OSError:
        pass


def validate_remote_name(name: str) -> str:
    """Same character class as sandbox_core._project_slug: lowercase letters,
    numbers, hyphen, underscore. Raises ValueError -- callers die() with an
    actionable message, this module never guesses or truncates a bad name."""
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid remote name {name!r}; use lowercase letters, numbers, "
            "hyphen, underscore"
        )
    return name


def get_remote(name: str) -> dict | None:
    return _remote_block().get(name)


def put_remote(name: str, **fields) -> dict:
    """Insert or update one remote's entry. Idempotent by design -- re-adding
    an existing name updates it rather than erroring (spec FR-005's
    idempotency expectation, applied to registration too)."""
    block = _remote_block()
    entry = dict(block.get(name) or {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    block[name] = entry
    _write_remote_block(block)
    return entry


def remove_remote(name: str) -> bool:
    """Forget a remote locally. NEVER touches the VPS itself (spec FR-003) --
    any instance already running there is unaffected by this call."""
    block = _remote_block()
    existed = block.pop(name, None) is not None
    if existed:
        _write_remote_block(block)
    return existed


def list_remotes() -> dict:
    return _remote_block()


def mint_bearer_token() -> str:
    """A fresh, cryptographically random token -- never user-supplied, never
    echoed back once stored (CLAUDE.md secrets rule)."""
    return secrets.token_hex(32)


_SSH_CONNECTION_RE = re.compile(
    r"(?:ssh://)?[^\s/@:]+@(?:\[[^\]\s]+\]|[^\s/:]+)(?::\d+)?"
)


def redact_ssh_connection(value: str, remote: dict | None = None) -> str:
    """Remove SSH connection targets from user-visible CLI/MCP errors."""
    text = str(value or "")
    if remote and remote.get("ssh"):
        text = text.replace(str(remote["ssh"]), "[redacted SSH target]")
        text = text.replace(f"ssh://{remote['ssh']}", "[redacted SSH target]")
    return _SSH_CONNECTION_RE.sub("[redacted SSH target]", text)


def parse_ssh_target(ssh_value: str) -> dict:
    """Normalize ssh://user@host[:port] or user@host[:port] into command parts."""
    raw = (ssh_value or "").strip()
    if not raw:
        raise ValueError("remote has no ssh connection string configured")
    if raw.startswith("ssh://"):
        parsed = urlsplit(raw)
        if not parsed.hostname:
            raise ValueError("invalid SSH connection string")
        user = parsed.username or ""
        host = parsed.hostname
        port = parsed.port
    else:
        user = ""
        hostport = raw
        if "@" in hostport:
            user, hostport = hostport.rsplit("@", 1)
        port = None
        if hostport.startswith("[") and "]" in hostport:
            host = hostport[1:hostport.index("]")]
            rest = hostport[hostport.index("]") + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
        elif ":" in hostport and hostport.rsplit(":", 1)[1].isdigit():
            host, port_s = hostport.rsplit(":", 1)
            port = int(port_s)
        else:
            host = hostport
    if not host:
        raise ValueError("invalid SSH connection string")
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    target = f"{user}@{display_host}" if user else display_host
    return {"target": target, "host": host, "port": port}


def remote_ssh_parts(remote_or_target) -> dict:
    ssh_value = (
        remote_or_target.get("ssh")
        if isinstance(remote_or_target, dict)
        else remote_or_target
    )
    return parse_ssh_target(ssh_value or "")


def _ssh_control_dir() -> Path:
    """Short, per-user directory for OpenSSH multiplexing sockets."""
    return Path(RUNTIME_DIR) / "s"


def _ensure_ssh_control_dir() -> Path:
    """Create the local socket directory before handing it to OpenSSH."""
    directory = _ssh_control_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _ssh_connection_options(multiplex: bool = True) -> list[str]:
    options = [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
    ]
    if multiplex:
        control_path = _ssh_control_dir() / "cm-%C"
        options.extend([
            "-o", "ControlMaster=auto",
            "-o", f"ControlPersist={_CONTROL_PERSIST_SECONDS}",
            "-o", f"ControlPath={control_path}",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
        ])
    return options


def ssh_command_args(remote_or_target, command: str, *, multiplex: bool = True) -> list[str]:
    parts = remote_ssh_parts(remote_or_target)
    args = ["ssh", *_ssh_connection_options(multiplex)]
    if parts["port"]:
        args.extend(["-p", str(parts["port"])])
    args.extend([parts["target"], command])
    return args


def scp_command_args(remote_or_target, local_path: str, remote_path: str,
                     *, multiplex: bool = True) -> list[str]:
    parts = remote_ssh_parts(remote_or_target)
    args = ["scp", *_ssh_connection_options(multiplex)]
    if parts["port"]:
        args.extend(["-P", str(parts["port"])])
    args.extend([local_path, f"{parts['target']}:{remote_path}"])
    return args


def git_ssh_command(remote_or_target, *, multiplex: bool = True) -> str:
    """Shell-safe SSH command for Git's direct VPS transport."""
    parts = remote_ssh_parts(remote_or_target)
    args = ["ssh", *_ssh_connection_options(multiplex)]
    if parts["port"]:
        args.extend(["-p", str(parts["port"])])
    return shlex.join(args)


def git_ssh_url(remote: dict, path: str) -> str:
    parts = remote_ssh_parts(remote)
    push_path = path if path.startswith("/") else f"/{path}"
    if parts["port"]:
        return f"ssh://{parts['target']}:{parts['port']}{push_path}"
    return f"ssh://{parts['target']}{push_path}"


def ssh_host(ssh_value: str) -> str:
    return remote_ssh_parts(ssh_value)["host"]


def ssh_run(remote: dict, command: str, timeout: int = 30,
            input_data=None) -> subprocess.CompletedProcess:
    """Run `command` on the remote over SSH. Shells to the system `ssh` binary
    using the remote's stored connection string -- no new pip dependency, same
    pattern as shelling to `docker`/`wp` elsewhere in this codebase. check=False:
    callers interpret returncode/stdout/stderr themselves, never a bare
    exception on a nonzero remote exit."""
    return ssh_process(remote, command, input_data=input_data, timeout=timeout)


def ssh_run_batch(remote: dict, commands: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run several independent shell commands in one SSH session.

    This is for orchestration steps that do not need individual interactive
    output. Each command remains a separate line under ``set -e``; callers get
    one combined exit status and avoid one SSH channel/process per item.
    """
    if not commands:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    script = "set -e\n" + "\n".join(commands) + "\n"
    return ssh_process(remote, "sh -s", input_data=script, timeout=timeout)


def ssh_process(remote_or_target, command: str, *, input_data=None,
                timeout: int = 30) -> subprocess.CompletedProcess:
    """Run one SSH session through the shared multiplexing policy.

    ``input_data`` supports streamed tar/script uploads without bypassing the
    control socket. It is intentionally a single command/session API; callers
    still get an isolated remote shell and normal SSH exit status.
    """
    try:
        _ensure_ssh_control_dir()
    except OSError:
        args = ssh_command_args(remote_or_target, command, multiplex=False)
    else:
        args = ssh_command_args(remote_or_target, command)
    is_text = input_data is None or isinstance(input_data, str)
    return subprocess.run(
        args, input=input_data, capture_output=True, text=is_text,
        timeout=timeout, check=False,
    )


def scp_run(remote: dict, local_path: str, remote_path: str,
            timeout: int = 60) -> subprocess.CompletedProcess:
    """Copy one file over SCP, bypassing mux only if local setup fails."""
    try:
        _ensure_ssh_control_dir()
    except OSError:
        args = scp_command_args(remote, local_path, remote_path, multiplex=False)
    else:
        args = scp_command_args(remote, local_path, remote_path)
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False,
    )


def check_reachable(remote: dict) -> bool:
    """A quick liveness ping -- true only if SSH can run a trivial command and
    get a zero exit. Never raises; an unreachable remote is a normal, expected
    outcome for `remote list` to report, not an error to propagate."""
    try:
        res = ssh_run(remote, "true", timeout=10)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False


def remote_doctor_checks(remote: dict) -> list[dict]:
    """Return bounded, secret-safe readiness facts for one remote target.

    This is read-only: it checks recorded transport state, SSH reachability,
    and the authenticated streamable-HTTP route without exposing a connection
    string or bearer token. ``doctor`` owns presentation; this module owns the
    remote-specific probe policy.
    """
    checks: list[dict] = []
    ssh_configured = bool(remote.get("ssh"))
    checks.append({"label": "SSH configured", "ok": ssh_configured,
                   "hint": "register it with `./sb remote add <name> <ssh-url>`"})
    if not ssh_configured:
        return checks
    provisioned = bool(remote.get("provisioned"))
    checks.append({"label": "provisioned", "ok": provisioned,
                   "hint": "run `./sb remote provision <name> --control-host <hostname>`"})
    if not provisioned:
        return checks

    transport = remote.get("control_transport")
    control_url = remote.get("control_url")
    token = remote.get("bearer_token")
    transport_ok = transport in {"https", "tailscale"}
    checks.extend([
        {"label": "control transport configured", "ok": transport_ok,
         "hint": "re-provision it to record a supported control transport"},
        {"label": "control URL configured", "ok": isinstance(control_url, str) and bool(control_url),
         "hint": "re-provision it to record the control URL"},
        {"label": "bearer token recorded", "ok": isinstance(token, str) and bool(token),
         "hint": "re-provision it to mint a replacement bearer token"},
    ])
    reachable = check_reachable(remote)
    checks.append({"label": "SSH reachable", "ok": reachable,
                   "hint": "check the VPS network path and SSH service"})
    if not (transport_ok and isinstance(control_url, str) and control_url and token and reachable):
        return checks

    parsed = urlsplit(control_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        checks.append({"label": "MCP endpoint reachable", "ok": False,
                       "hint": "re-provision it to repair the control URL"})
        return checks
    path = parsed.path.rstrip("/")
    endpoint = urlunsplit((parsed.scheme, parsed.netloc,
                           f"{path}/mcp" if path else "/mcp", "", ""))
    try:
        import urllib.error
        import urllib.request
        request = urllib.request.Request(endpoint, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        })
        # Doctor must probe the VPS directly. A developer's ambient HTTP proxy
        # can otherwise transform a healthy MCP response into a false negative.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        # A bare authenticated GET is not a complete MCP session. FastMCP may
        # return 400/405/406 after validating the route and bearer token; all
        # prove the control endpoint is reachable. 401, 404, and 5xx do not.
        endpoint_ok = status in {200, 204, 400, 405, 406}
    except Exception:
        endpoint_ok = False
    checks.append({"label": "MCP endpoint reachable", "ok": endpoint_ok,
                   "hint": "run `./sb remote up <name>` and verify its route"})
    service_record = remote.get("mcp_service")
    if not isinstance(service_record, dict):
        checks.append({"label": "MCP service ownership", "ok": False,
                       "hint": "run `./sb remote service migrate <name> --plan` and review the protected migration"})
        return checks
    try:
        service = remote_mcp_service_status(remote)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        service = {"ownership": "unknown", "enabled": False, "active": False,
                   "linger": False, "listener_expected": False, "authenticated": False}
    checks.extend([
        {"label": "MCP service ownership", "ok": service.get("ownership") == "proven",
         "hint": "review remote service status and run its confirmed migration if ownership is ambiguous"},
        {"label": "MCP reboot recovery", "ok": bool(service.get("enabled") and service.get("linger")),
         "hint": "enable the selected Sandbox user service and user lingering through confirmed migration"},
        {"label": "MCP listener scope", "ok": bool(service.get("listener_expected")),
         "hint": "inspect the selected service bind/port; public or unknown listeners are not accepted"},
        {"label": "MCP service authentication", "ok": bool(service.get("authenticated")),
         "hint": "inspect the selected service credential file and authenticated /mcp route"},
    ])
    return checks


def deploy_target_slug(project_root) -> str:
    """The canonical slug used to derive a project's VPS-side deploy path.
    Uses the EXACT SAME _project_slug resolution sandbox_core already uses
    for legacy plugins:["."] self-entries, so both sides would derive the
    identical path with no extra client<->server path-mapping bookkeeping
    (research.md)."""
    sc = _core()
    root = Path(project_root)
    return sc._project_slug(None, root.name)


def resolve_sandbox_home(remote: dict) -> str:
    """The REAL, expanded absolute path of $SANDBOX_HOME on the remote --
    resolved once via SSH rather than left as a literal shell variable,
    since a git push URL needs a real path, not something only a remote
    shell would expand."""
    res = ssh_run(remote, "echo ${SANDBOX_HOME:-$HOME/sandbox}", timeout=15)
    if res.returncode != 0 or not (res.stdout or "").strip():
        raise RuntimeError(
            f"could not resolve $SANDBOX_HOME on remote: "
            f"{(res.stderr or res.stdout or '').strip()[:500]}"
        )
    return res.stdout.strip()


def deploy_target_path(remote: dict, project_root) -> str:
    """The REAL, resolved absolute VPS-side path for a project's deploy-target
    git repo: <resolved $SANDBOX_HOME>/deploy-src/<canonical-project-slug>."""
    home = resolve_sandbox_home(remote)
    slug = deploy_target_slug(project_root)
    return f"{home}/deploy-src/{slug}"


def remote_sb_path(remote: dict) -> str:
    """Path to the staged sandbox runtime's `sb` on the VPS."""
    return f"{resolve_sandbox_home(remote)}/sb-src/sb"


def ensure_deploy_repo(remote: dict, project_root) -> str:
    """Lazily create the deploy-target git repo on first deploy to this remote
    (NOT during `provision`, which is machine-level, not project-level). Safe
    to call every deploy -- a no-op if the repo already exists. Sets
    receive.denyCurrentBranch=updateInstead so a push directly updates the
    checked-out working tree, no bare-repo indirection needed. Returns the
    resolved absolute path (see deploy_target_path)."""
    target = deploy_target_path(remote, project_root)
    cmd = (
        f"mkdir -p {target} && "
        f"cd {target} && "
        f"if [ ! -d .git ]; then git init -q && "
        f"git config receive.denyCurrentBranch updateInstead; fi"
    )
    res = ssh_run(remote, cmd, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not prepare deploy-target repo on remote: "
            f"{(res.stderr or res.stdout or '').strip()[:500]}"
        )
    return target


def _last_json(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def ensure_remote_instance(remote: dict, target_path: str, label: str | None = None) -> dict:
    """Run remote `sb ensure` for the deployed project and parse its JSON
    result. This is the missing second half after code deploy: it creates or
    refreshes the WordPress instance on the VPS itself."""
    sb = remote_sb_path(remote)
    label_arg = f" --label {shlex.quote(label)} --create" if label else ""
    cmd = f"{shlex.quote(sb)} ensure --project-dir {shlex.quote(target_path)}{label_arg} --json"
    res = ssh_run(remote, cmd, timeout=900)
    data = _last_json(res.stdout or "")
    if res.returncode != 0 or not data:
        raise RuntimeError(
            f"could not ensure remote instance: "
            f"{(res.stderr or res.stdout or '').strip()[:2000]}"
        )
    return data


def activate_remote_plugin(remote: dict, target_path: str, instance: str,
                           plugin_slug: str) -> None:
    """Make the deployed project the active plugin inside the remote instance.

    Projects with a sandbox.config.json may eventually wire this automatically,
    but ad-hoc plugin repos still need a reliable fallback. We symlink the
    deploy target into the instance's plugin directory and activate the slug."""
    home = resolve_sandbox_home(remote)
    wp_plugins = f"{home}/runtime/wp-{instance}/wp-content/plugins"
    plugin_path = f"{wp_plugins}/{plugin_slug}"
    sb = remote_sb_path(remote)
    cmd = (
        "set -e; "
        f"mkdir -p {shlex.quote(wp_plugins)}; "
        f"rm -rf {shlex.quote(plugin_path)}; "
        f"ln -s {shlex.quote(target_path)} {shlex.quote(plugin_path)}; "
        f"cd {shlex.quote(target_path)}; "
        f"{shlex.quote(sb)} wp plugin activate {shlex.quote(plugin_slug)}"
    )
    res = ssh_run(remote, cmd, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not activate remote plugin {plugin_slug}: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def default_instance_domain(label: str, project_slug: str,
                            base_domain: str = "sandbox.asb.bd") -> str:
    """Public per-instance hostname. Uses hyphens only between label and slug
    as requested: default-templately-ai-builder.sandbox.asb.bd."""
    safe_label = re.sub(r"[^a-z0-9-]+", "-", (label or "default").lower()).strip("-")
    safe_slug = re.sub(r"[^a-z0-9-]+", "-", (project_slug or "project").lower()).strip("-")
    safe_label = safe_label or "default"
    safe_slug = safe_slug or "project"
    base = (base_domain or "sandbox.asb.bd").strip().strip(".")
    return f"{safe_label}-{safe_slug}.{base}"


def rewrite_instance_url(original_url: str | None, public_url: str,
                         default_path: str = "/") -> str:
    """Rewrite an instance URL (including query string) onto its public base URL."""
    public = urlsplit(public_url)
    original = urlsplit(original_url or "")
    path = original.path or default_path
    return urlunsplit((
        public.scheme,
        public.netloc,
        path,
        original.query,
        original.fragment,
    ))


def _validate_hostname(hostname: str, what: str) -> str:
    hostname = (hostname or "").strip()
    if (
        not hostname or "/" in hostname or ":" in hostname
        or not re.fullmatch(r"[A-Za-z0-9.-]+", hostname)
        or hostname.startswith(".") or hostname.endswith(".")
    ):
        raise ValueError(f"{what} must be a bare hostname")
    return hostname


def _caddy_proxy_command(hostname: str, port: int, conf_prefix: str) -> str:
    site_q = shlex.quote(
        f"{hostname} {{\n"
        f"    reverse_proxy 127.0.0.1:{int(port)}\n"
        f"}}\n"
    )
    file_q = shlex.quote(f"/etc/caddy/conf.d/{conf_prefix}-{hostname}.caddy")
    return (
        "set -e; "
        "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        "if ! command -v caddy >/dev/null 2>&1; then "
        "$SUDO apt-get update -qq && $SUDO apt-get install -y caddy; "
        "fi; "
        "$SUDO install -d -m 0755 /etc/caddy/conf.d; "
        "if [ ! -f /etc/caddy/Caddyfile ]; then "
        "printf '%s\n' 'import /etc/caddy/conf.d/*.caddy' | "
        "$SUDO tee /etc/caddy/Caddyfile >/dev/null; "
        "elif ! $SUDO grep -q 'import /etc/caddy/conf.d/\\*.caddy' "
        "/etc/caddy/Caddyfile; then "
        "printf '\n%s\n' 'import /etc/caddy/conf.d/*.caddy' | "
        "$SUDO tee -a /etc/caddy/Caddyfile >/dev/null; "
        "fi; "
        f"printf '%s' {site_q} | $SUDO tee {file_q} >/dev/null; "
        "$SUDO caddy validate --config /etc/caddy/Caddyfile; "
        "$SUDO systemctl enable --now caddy; "
        "$SUDO systemctl reload caddy"
    )


def configure_instance_https_route(remote: dict, domain: str, port: int) -> None:
    """Route a public instance hostname through Caddy to the remote WP port."""
    domain = _validate_hostname(domain, "remote instance domain")
    cmd = _caddy_proxy_command(domain, port, "sandbox-instance")
    res = ssh_run(remote, cmd, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not configure remote instance HTTPS route: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def remove_instance_https_route(remote: dict, domain: str) -> None:
    """Remove only Sandbox's Caddy fragment for one public instance route."""
    domain = _validate_hostname(domain, "remote instance domain")
    path = f"/etc/caddy/conf.d/sandbox-instance-{domain}.caddy"
    cmd = (
        "set -e; "
        "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"$SUDO rm -f {shlex.quote(path)}; "
        "$SUDO caddy validate --config /etc/caddy/Caddyfile; "
        "$SUDO systemctl reload caddy"
    )
    res = ssh_run(remote, cmd, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not remove remote instance HTTPS route: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def delete_remote_instance(remote: dict, instance_name: str) -> None:
    """Delete precisely one named remote Sandbox instance and its Docker data."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", instance_name or ""):
        raise ValueError("invalid remote Sandbox instance name")
    sb = remote_sb_path(remote)
    res = ssh_run(remote, f"{shlex.quote(sb)} instance delete {shlex.quote(instance_name)} --yes", timeout=300)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not delete remote Sandbox instance: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def set_remote_instance_url(remote: dict, target_path: str, url: str) -> None:
    """Set WordPress home/siteurl for the remote project."""
    sb = remote_sb_path(remote)
    cmd = (
        f"cd {shlex.quote(target_path)} && "
        f"{shlex.quote(sb)} wp option update home {shlex.quote(url)} && "
        f"{shlex.quote(sb)} wp option update siteurl {shlex.quote(url)}"
    )
    res = ssh_run(remote, cmd, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not set remote instance URL: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def current_branch(project_root) -> str:
    """The local project's current git branch name. Raises on a detached
    HEAD -- deploy needs a named branch to push to."""
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    branch = (res.stdout or "").strip()
    if res.returncode != 0 or not branch or branch == "HEAD":
        raise RuntimeError(
            "could not determine the current git branch (detached HEAD?) -- "
            "deploy needs a named branch checked out"
        )
    return branch


def push_commits(remote: dict, project_root, target_path: str, branch: str) -> str:
    """git push HEAD to the deploy-target repo over SSH -- works even for a
    branch never pushed anywhere else (spec FR-008), since this is a direct
    git-to-git push over the SAME SSH connection already registered, not
    dependent on GitHub/origin at all. Returns the pushed commit SHA."""
    push_url = git_ssh_url(remote, target_path)
    try:
        _ensure_ssh_control_dir()
    except OSError:
        git_ssh = git_ssh_command(remote, multiplex=False)
    else:
        git_ssh = git_ssh_command(remote)
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = git_ssh
    res = subprocess.run(
        ["git", "push", push_url, f"HEAD:refs/heads/{branch}"],
        cwd=str(project_root), env=env, capture_output=True, text=True,
        timeout=120, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"git push to remote failed: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )
    sha_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    return (sha_res.stdout or "").strip()


def reset_target_to(remote: dict, target_path: str, sha: str) -> None:
    """Reset the VPS working tree to the just-pushed commit BEFORE applying
    the uncommitted layer -- this is what makes each deploy REPLACE rather
    than stack (spec FR-007): any diff a previous deploy applied is wiped
    here, never left silently underneath a new one.

    `git reset --hard` alone only rewinds TRACKED files -- it does nothing
    about untracked files a previous deploy transferred (apply_uncommitted's
    untracked-file step). Without also removing those, a file added by
    deploy #1 and later deleted locally would survive on the VPS forever,
    breaking the "replace, not stack" guarantee for exactly that class of
    file. `git clean -fd` (no `-x`) removes untracked-but-not-ignored files
    only -- gitignored build output (node_modules/vendor/etc) is never
    touched, since those were never part of the deploy in the first place."""
    cmd = f"cd {target_path} && git reset --hard {sha} && git clean -fd"
    res = ssh_run(remote, cmd, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not reset the VPS working tree to {sha}: "
            f"{(res.stderr or res.stdout or '').strip()[:500]}"
        )


def capture_uncommitted(project_root) -> tuple[str, list[str]]:
    """Returns (tracked_diff_text, untracked_file_relpaths). Plain `git diff`
    alone misses brand-new untracked files entirely -- captured via a
    separate `git status --porcelain` pass, filtered to `??` entries (already
    .gitignore-aware, since that's exactly what `git status` itself respects,
    so build-artifact trees like node_modules/vendor are never included).

    Uses `--untracked-files=all`: plain `git status --porcelain` COLLAPSES a
    brand-new untracked DIRECTORY to just its directory name (e.g. `subdir/`)
    rather than listing the files inside it -- a real bug caught only by
    live-verifying against an actual remote (a mocked porcelain string in a
    unit test can't reveal this, since the mock has to assume the shape it's
    testing). Without `=all`, a new file inside a new untracked directory
    would be silently skipped entirely by apply_uncommitted's
    `local_path.is_file()` check (a directory is never a file)."""
    diff_res = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    diff_text = diff_res.stdout or ""
    status_res = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    untracked = [
        line[3:] for line in (status_res.stdout or "").splitlines()
        if line.startswith("??")
    ]
    return diff_text, untracked


def apply_uncommitted(remote: dict, target_path: str, project_root,
                       diff_text: str, untracked: list[str]) -> int:
    """Applies the dirty working tree on top of a just-reset clean tree by
    copying exact file bytes for changed tracked files and untracked files.

    This deliberately avoids replaying `git diff` through `git apply`: a live
    deploy against a plugin with a CRLF->LF rewrite in `assets/admin.css`
    proved text patches are too brittle for the promise here. The contract is
    "remote reflects the local working tree", so copying the current file
    content is both simpler and more correct. Deleted tracked files are removed
    explicitly. Returns the total number of paths touched."""
    applied = 0
    to_copy: list[str] = []
    deleted: list[str] = []
    if diff_text.strip():
        changed_res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=False,
        )
        if changed_res.returncode != 0:
            raise RuntimeError(
                f"could not list changed tracked files: "
                f"{(changed_res.stderr or changed_res.stdout or '').strip()[:500]}"
            )
        deleted_res = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=D", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=False,
        )
        if deleted_res.returncode != 0:
            raise RuntimeError(
                f"could not list deleted tracked files: "
                f"{(deleted_res.stderr or deleted_res.stdout or '').strip()[:500]}"
            )
        changed_names = [n for n in (changed_res.stdout or "").splitlines() if n.strip()]
        deleted = [n for n in (deleted_res.stdout or "").splitlines() if n.strip()]
        deleted_set = set(deleted)
        to_copy.extend([n for n in changed_names if n not in deleted_set])
    to_copy.extend(untracked)
    def safe_relpath(value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe relative deploy path: {value!r}")
        return value

    deleted = [safe_relpath(value) for value in deleted]
    to_copy = [safe_relpath(value) for value in to_copy]
    if deleted:
        commands = [
            f"rm -f -- {shlex.quote(target_path.rstrip('/') + '/' + relpath)}"
            for relpath in deleted
        ]
        rm_res = ssh_run_batch(remote, commands, timeout=30)
        if rm_res.returncode != 0:
            raise RuntimeError(
                "could not remove deleted files on remote: "
                f"{(rm_res.stderr or rm_res.stdout or '').strip()[:500]}"
            )
        applied += len(deleted)

    existing = [relpath for relpath in to_copy
                if (Path(project_root) / relpath).is_file()]
    if existing:
        # One compressed archive and one remote shell session replace one
        # mkdir + SCP channel per file. The archive is built from the local
        # project root, so paths remain relative and no local absolute path is
        # ever sent to the remote shell.
        tar_res = subprocess.run(
            ["tar", "-czf", "-", "--", *existing], cwd=str(project_root),
            capture_output=True, check=False,
        )
        if tar_res.returncode != 0:
            raise RuntimeError(
                "could not package dirty files: "
                f"{(tar_res.stderr or b'').decode(errors='replace').strip()[:500]}"
            )
        extract = (
            f"mkdir -p -- {shlex.quote(target_path)} && "
            f"tar -xzf - -C {shlex.quote(target_path)}"
        )
        copy_res = ssh_run(remote, extract, timeout=120,
                           input_data=tar_res.stdout)
        if copy_res.returncode != 0:
            raise RuntimeError(
                "could not transfer dirty files: "
                f"{(copy_res.stderr or copy_res.stdout or '').strip()[:500]}"
            )
        applied += len(existing)
    return applied


DEFAULT_MCP_PORT = 9174
_MCP_PIDFILE = "/tmp/sandbox-mcp-remote.pid"
REMOTE_MCP_SERVICE = "sandbox-mcp-remote.service"
_REMOTE_MCP_ENV = "$HOME/.sandbox/mcp-remote.env"
_REMOTE_MCP_UNIT_ENV = "%h/.sandbox/mcp-remote.env"


def _remote_mcp_bind_allowed(bind: str) -> bool:
    """Return whether ``bind`` is private enough for remote MCP.

    HTTPS mode is loopback-only. Tailscale mode may use an address in the
    shared CGNAT range. Refusing every other literal address makes the public
    exposure invariant enforceable before any remote command is built.
    """
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        return False
    return address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")


def _remote_mcp_marker(bind: str, port: int, public_url: str | None = None) -> str:
    value = "|".join((REMOTE_MCP_SERVICE, bind, str(int(port)), public_url or ""))
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _remote_mcp_runtime_revision() -> str:
    """Return a non-secret local runtime identity that survives staged uploads."""
    source = Path(__file__).read_bytes()
    return hashlib.sha256(source).hexdigest()[:24]


def remote_mcp_service_record(bind: str, port: int, public_url: str | None = None) -> dict:
    if not _remote_mcp_bind_allowed(bind):
        raise ValueError("remote MCP bind must be loopback or a Tailscale address")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("remote MCP port must be between 1 and 65535")
    return {
        "service_name": REMOTE_MCP_SERVICE,
        "transport": "https" if ipaddress.ip_address(bind).is_loopback else "tailscale",
        "bind": bind,
        "port": port,
        "runtime_revision": _remote_mcp_runtime_revision(),
        "ownership_marker": _remote_mcp_marker(bind, port, public_url),
    }


def _validate_remote_mcp_public_url(public_url: str | None) -> str | None:
    if public_url is None:
        return None
    if not isinstance(public_url, str) or any(char in public_url for char in "\r\n\0"):
        raise ValueError("remote MCP public URL is invalid")
    parsed = urlsplit(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("remote MCP public URL must be an HTTP(S) origin without credentials")
    return public_url


def render_remote_mcp_unit(bind: str, port: int, public_url: str | None = None) -> str:
    """Render a non-secret systemd user unit for the remote MCP service."""
    public_url = _validate_remote_mcp_public_url(public_url)
    record = remote_mcp_service_record(bind, port, public_url)
    public_arg = f" --public-url {shlex.quote(public_url)}" if public_url else ""
    return "\n".join((
        "[Unit]",
        "Description=Sandbox remote MCP control plane",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"EnvironmentFile={_REMOTE_MCP_UNIT_ENV}",
        f"Environment=SANDBOX_REMOTE_MCP_MARKER={record['ownership_marker']}",
        f"Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION={record['runtime_revision']}",
        "WorkingDirectory=%h/sandbox/sb-src",
        "ExecStart=%h/sandbox/sb-src/sb mcp --transport streamable-http "
        f"--bind {shlex.quote(bind)} --port {port}{public_arg}",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ))


def remote_mcp_service_status(remote: dict) -> dict:
    """Read only the selected Sandbox unit state; never inspect generic argv."""
    record = dict(remote.get("mcp_service") or {})
    expected = record.get("service_name") == REMOTE_MCP_SERVICE
    marker = record.get("ownership_marker") if isinstance(record.get("ownership_marker"), str) else ""
    revision = record.get("runtime_revision") if isinstance(record.get("runtime_revision"), str) else ""
    bind = record.get("bind") if isinstance(record.get("bind"), str) else ""
    port = record.get("port") if isinstance(record.get("port"), int) else 0
    marker_probe = (
        f"if test -f $HOME/.config/systemd/user/{REMOTE_MCP_SERVICE} && "
        f"grep -Fqx {shlex.quote('Environment=SANDBOX_REMOTE_MCP_MARKER=' + marker)} "
        f"$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE} && "
        f"grep -Fqx {shlex.quote('Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION=' + revision)} "
        f"$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE} && "
        f"grep -Fq {shlex.quote('--bind ' + bind + ' --port ' + str(port))} "
        f"$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE}; then echo ownership=proven; else echo ownership=ambiguous; fi; "
        if marker else "echo marker=0; "
    )
    listener_probe = (
        f"if command -v ss >/dev/null 2>&1; then listener=$(ss -H -ltn 'sport = :{port}' 2>/dev/null | awk 'NR==1 {{print $4}}'); "
        f"case \"$listener\" in {shlex.quote(bind + ':' + str(port))}|{shlex.quote('[' + bind + ']:' + str(port))}) echo listener=expected;; '') echo listener=missing;; *) echo listener=unexpected;; esac; "
        "else echo listener=unknown; fi; "
        if bind and port else "echo listener=unknown; "
    )
    auth_program = (
        "import sys, urllib.error, urllib.request; from pathlib import Path; "
        "lines=Path(sys.argv[3]).read_text().splitlines(); "
        "matches=[line.split('=',1)[1] for line in lines if line.startswith('SANDBOX_REMOTE_MCP_TOKEN=')]; "
        "token=matches[0] if len(matches)==1 else ''; "
        "url='http://%s:%s/mcp' % (sys.argv[1], sys.argv[2]); "
        "request=urllib.request.Request(url, headers={'Authorization':'Bearer '+token}); "
        "\ntry:\n urllib.request.urlopen(request, timeout=5); print('auth=ok')\n"
        "except urllib.error.HTTPError as exc:\n print('auth=ok' if exc.code != 401 else 'auth=failed')\n"
        "except Exception:\n print('auth=unknown')"
    )
    auth_probe = (
        f"if test -r {_REMOTE_MCP_ENV}; then python3 -c {shlex.quote(auth_program)} "
        f"{shlex.quote(bind)} {port} {_REMOTE_MCP_ENV}; else echo auth=unknown; fi; "
        if bind and port else "echo auth=unknown; "
    )
    legacy_probe = (
        f"if test -r {_MCP_PIDFILE}; then legacy_pid=$(cat {_MCP_PIDFILE} 2>/dev/null || true); "
        "case \"$legacy_pid\" in ''|*[!0-9]*) echo legacy_pidfile=invalid;; "
        "*) if kill -0 \"$legacy_pid\" 2>/dev/null; then echo legacy_pidfile=present; else echo legacy_pidfile=stale; fi;; esac; "
        "else echo legacy_pidfile=absent; fi"
    )
    command = (
        "if ! command -v systemctl >/dev/null 2>&1; then echo unavailable; exit 0; fi; "
        f"printf 'enabled='; systemctl --user is-enabled {REMOTE_MCP_SERVICE} 2>/dev/null || true; "
        f"printf 'active='; systemctl --user is-active {REMOTE_MCP_SERVICE} 2>/dev/null || true; "
        f"printf 'pid='; systemctl --user show {REMOTE_MCP_SERVICE} -p MainPID --value 2>/dev/null || true; "
        "printf 'linger='; loginctl show-user \"$USER\" -p Linger --value 2>/dev/null || true; "
        + marker_probe + listener_probe + auth_probe + legacy_probe
    )
    res = ssh_run(remote, command, timeout=20)
    values: dict[str, str] = {}
    for line in (res.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"enabled", "active", "pid", "linger", "ownership", "listener", "auth", "legacy_pidfile"}:
            values[key] = value.strip().lower()
    installed = values.get("enabled") not in {"", "not-found", "unknown"}
    active = values.get("active") == "active"
    enabled = values.get("enabled") == "enabled"
    linger = values.get("linger") == "yes"
    ownership = "proven" if expected and installed and values.get("ownership") == "proven" else "missing" if not installed else "ambiguous"
    return {
        "installed": installed, "enabled": enabled, "active": active,
        "linger": linger, "ownership": ownership,
        "service_name": REMOTE_MCP_SERVICE if expected else None,
        "pid_present": values.get("pid", "0") not in {"", "0"},
        "listener_expected": values.get("listener") == "expected",
        "authenticated": values.get("auth") == "ok",
        "listener_state": values.get("listener", "unknown"),
        "auth_state": values.get("auth", "unknown"),
        "legacy_pidfile": values.get("legacy_pidfile", "unknown"),
        "bind": record.get("bind"), "port": record.get("port"),
    }


def remote_mcp_service_plan(remote: dict, bind: str, port: int,
                            public_url: str | None = None, *, observed: dict | None = None) -> dict:
    """Build a no-write migration plan with no secret-bearing fields."""
    public_url = _validate_remote_mcp_public_url(public_url)
    record = remote_mcp_service_record(bind, port, public_url)
    return {
        "status": "planned", "requires_confirm": True,
        "service": record,
        "steps": [
            "write owner-only remote credential file", "install Sandbox-owned user unit",
            "reload user manager and enable linger", "enable and verify selected unit",
        ],
        "legacy_pidfile_detected": bool((observed or {}).get("legacy_pidfile") == "present"),
    }


def migrate_remote_mcp_service(remote: dict, bind: str, port: int, token: str,
                               public_url: str | None = None, *, confirm: bool = False) -> dict:
    """Install the scoped remote service only after explicit confirmation.

    The token is passed through SSH stdin, never embedded in the unit or command.
    """
    plan = remote_mcp_service_plan(remote, bind, port, public_url)
    if not confirm:
        return plan
    if not isinstance(token, str) or not token:
        raise ValueError("remote MCP token is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("remote MCP token has unsafe characters")
    unit = render_remote_mcp_unit(bind, port, public_url)
    command = (
        "set -eu; umask 077; mkdir -p $HOME/.sandbox $HOME/.config/systemd/user; chmod 700 $HOME/.sandbox; "
        f"unit_path=$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE}; env_path={_REMOTE_MCP_ENV}; "
        "backup=$HOME/.sandbox/mcp-remote-backup-$$; mkdir -p \"$backup\"; "
        "had_unit=0; had_env=0; if test -f \"$unit_path\"; then cp \"$unit_path\" \"$backup/unit\"; had_unit=1; fi; "
        "if test -f \"$env_path\"; then cp \"$env_path\" \"$backup/env\"; had_env=1; fi; "
        "rollback() { if test \"$had_unit\" = 1; then cp \"$backup/unit\" \"$unit_path\"; else rm -f \"$unit_path\"; fi; "
        "if test \"$had_env\" = 1; then cp \"$backup/env\" \"$env_path\"; else rm -f \"$env_path\"; fi; "
        "systemctl --user daemon-reload || true; }; "
        "unit_tmp=$(mktemp \"$unit_path.XXXXXX\"); cat > \"$unit_tmp\" <<'UNIT'\n"
        + unit + "UNIT\n"
        "chmod 600 \"$unit_tmp\"; mv \"$unit_tmp\" \"$unit_path\"; "
        "IFS= read -r sandbox_remote_mcp_token; "
        "env_tmp=$(mktemp \"$env_path.XXXXXX\"); "
        "printf '%s\\n' \"SANDBOX_REMOTE_MCP_TOKEN=$sandbox_remote_mcp_token\" > \"$env_tmp\"; "
        "chmod 600 \"$env_tmp\"; mv \"$env_tmp\" \"$env_path\"; "
        "if ! systemctl --user daemon-reload || ! loginctl enable-linger \"$USER\" || "
        f"! systemctl --user enable --now {REMOTE_MCP_SERVICE} || ! systemctl --user is-active --quiet {REMOTE_MCP_SERVICE}; then "
        "rollback; exit 1; fi; rm -rf \"$backup\""
    )
    try:
        res = ssh_run(remote, command, timeout=60, input_data=token + "\n")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("timed out installing the remote MCP service") from exc
    if res.returncode != 0:
        raise RuntimeError("could not install the remote MCP service")
    return {**plan, "status": "applied", "service": remote_mcp_service_record(bind, port, public_url)}


def resolve_tailscale_ip(remote: dict) -> str:
    """The remote's own Tailscale IPv4 address -- the ONLY address the remote
    MCP server may bind to (spec FR-014, never 0.0.0.0)."""
    res = ssh_run(remote, "tailscale ip -4", timeout=15)
    ip = (res.stdout or "").strip().splitlines()[0] if res.stdout else ""
    if res.returncode != 0 or not ip:
        raise RuntimeError(
            f"could not resolve a Tailscale IP on the remote -- is Tailscale "
            f"installed and joined? {(res.stderr or res.stdout or '').strip()[:500]}"
        )
    return ip


def configure_https_proxy(remote: dict, public_host: str, port: int) -> None:
    """Install/configure Caddy so the public HTTPS control endpoint routes by
    hostname to the loopback-bound MCP server. This intentionally uses a named
    virtual host rather than raw public ports so the VPS can also host Next.js
    or other apps through their own Caddy site blocks."""
    public_host = _validate_hostname(public_host, "public HTTPS control host")
    cmd = _caddy_proxy_command(public_host, port, "sandbox-mcp")
    res = ssh_run(remote, cmd, timeout=180)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not configure HTTPS control proxy: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )


def start_remote_mcp_server(remote: dict, bind: str, port: int, token: str,
                            public_url: str | None = None) -> None:
    """Compatibility wrapper for the confirmed, systemd-owned service path."""
    migrate_remote_mcp_service(remote, bind, port, token, public_url, confirm=True)


def stop_remote_mcp_server(remote: dict) -> None:
    """Stop only the Sandbox-owned unit; legacy PID data is detection-only."""
    status = remote_mcp_service_status(remote)
    if status["ownership"] != "proven":
        raise RuntimeError("remote_service_ownership_unknown")
    res = ssh_run(remote, f"systemctl --user stop {REMOTE_MCP_SERVICE}", timeout=20)
    if res.returncode != 0:
        raise RuntimeError("could not stop the selected remote MCP service")


def reject_herd_projects(pconf: dict) -> None:
    """Spec FR-013: a project configured for sandbox's macOS-native,
    Docker-less Herd runtime has no remote equivalent (Herd relies on a host
    Laravel Herd install + host MySQL -- there is no VPS-side analog). Refuse
    cleanly with an actionable message rather than attempting something that
    cannot work remotely. Raises ValueError; callers die() with the message."""
    if (pconf or {}).get("server") == "herd":
        raise ValueError(
            "this project is configured for Herd (\"server\": \"herd\") -- Herd "
            "is a macOS-native, Docker-less runtime with no remote equivalent. "
            "Remote hosting requires the Docker path -- use a different "
            "`server` value (e.g. \"nginx\") for a project you want to deploy "
            "or run remotely."
        )
