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
        provider: "contabo"                            # descriptive lowercase slug
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
import urllib.error
import urllib.request
import subprocess
import time
from pathlib import PurePosixPath
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


def resolve_source_ref(project_root: str | Path, source_ref: str) -> str:
    """Resolve a named ref to one full commit before any remote mutation.

    Immutable deploys intentionally reject every local dirty layer.  The
    caller can therefore prove that the transferred source is exactly the
    resolved commit and that a failed resolution never contacted the remote.
    """
    root = Path(project_root).resolve()
    if not isinstance(source_ref, str) or not source_ref.strip() or "\x00" in source_ref:
        raise ValueError("source_ref is invalid")
    requested = source_ref.strip()
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{requested}^{{commit}}"],
        cwd=str(root), capture_output=True, text=True, check=False,
    )
    commit = (resolved.stdout or "").strip().lower()
    if resolved.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source_ref could not be resolved to a full commit")
    diff_text, untracked = capture_uncommitted(root)
    if diff_text.strip() or untracked:
        raise ValueError(
            "source_ref requires a clean working tree; immutable source was not combined "
            "with local changes"
        )
    return commit


def deploy_exact_working_tree(
    remote: dict,
    project_root: str | Path,
    *,
    source_ref: str | None = None,
    source_root: str | Path | None = None,
) -> dict:
    """Deploy committed, modified, and untracked project state once.

    This is the reusable deploy primitive for remote jobs.  It intentionally
    returns identity metadata rather than a mutable instance record, allowing a
    job to prove which exact working tree it was accepted against.
    """
    root = Path(project_root).resolve()
    resolved_source = resolve_source_ref(root, source_ref) if source_ref is not None else None
    target = ensure_deploy_repo(remote, root)
    branch = current_branch(root) if resolved_source is None else None
    pushed_sha = push_commits(
        remote, root, target, branch,
        source_ref=source_ref, resolved_sha=resolved_source,
        source_root=source_root,
    )
    reset_target_to(remote, target, pushed_sha)
    diff_text, untracked = (capture_uncommitted(root) if resolved_source is None else ("", []))
    applied = apply_uncommitted(remote, target, root, diff_text, untracked) if resolved_source is None else 0
    dirty = hashlib.sha256((diff_text + "\n" + "\n".join(sorted(untracked))).encode()).hexdigest()
    identity = hashlib.sha256(f"{pushed_sha}:{dirty}:{target}".encode()).hexdigest()
    return {"target_path": target, "commit": pushed_sha, "dirty": bool(diff_text or untracked),
            "dirty_digest": dirty, "identity": f"sha256:{identity}",
            "uncommitted_files_applied": applied,
            "source_ref": source_ref,
            # For a nested immutable deploy the pushed commit is the
            # source-root subtree artifact; retain the user's resolved ref as
            # the provenance identity rather than replacing it with the
            # synthetic tree SHA.
            "resolved_commit": resolved_source or pushed_sha,
            "source_immutable": resolved_source is not None}


def register_workspace_deployment_receipt(
    remote: dict, deployed: dict, project_identity: str,
) -> str:
    """Persist an opaque remote-side receipt for one exact deployed tree.

    Workspace control receives only the receipt ID.  The protected target path
    stays inside the deployment adapter and the remote controller's owner-only
    receipt store; it is never serialized as a workspace CLI argument.
    """
    import base64
    import shlex

    target = deployed.get("target_path")
    commit = deployed.get("commit")
    source_identity = deployed.get("identity")
    if not all(isinstance(item, str) and item for item in (
            target, commit, source_identity, project_identity)):
        raise RuntimeError("exact deployment did not produce a registerable receipt")
    receipt_id = "wdr_" + hashlib.sha256(
        f"{project_identity}\0{source_identity}\0{target}".encode()
    ).hexdigest()
    payload = json.dumps({
        "schema_version": 1,
        "receipt_id": receipt_id,
        "project_identity": project_identity,
        "checkout_locator": target,
        "source_checkout_locator": deployed.get("source_checkout_locator"),
        "source_identity": source_identity,
        "commit": commit,
        "dirty_digest": deployed.get("dirty_digest"),
    }, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.b64encode(payload).decode("ascii")
    receipt_root = resolve_sandbox_home(remote) + "/runtime/workspaces/deployment-receipts"
    program = (
        "import base64,os,pathlib,sys,tempfile;"
        "root=pathlib.Path(sys.argv[1]);rid=sys.argv[2];data=base64.b64decode(sys.argv[3]);"
        "root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(root,0o700);"
        "fd,tmp=tempfile.mkstemp(prefix='.'+rid+'.',dir=root);os.fchmod(fd,0o600);"
        "f=os.fdopen(fd,'wb');f.write(data);f.flush();os.fsync(f.fileno());f.close();"
        "os.replace(tmp,root/(rid+'.json'))"
    )
    command = shlex.join(["python3", "-c", program, receipt_root, receipt_id, encoded])
    result = ssh_run(remote, command, timeout=30)
    if result.returncode != 0:
        raise RuntimeError("could not persist exact deployment receipt")
    return receipt_id


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
    multiplex = True
    try:
        _ensure_ssh_control_dir()
    except OSError:
        multiplex = False
    args = ssh_command_args(remote_or_target, command, multiplex=multiplex)
    is_text = input_data is None or isinstance(input_data, str)
    # A timeout is ambiguous: the remote command may still be running after the
    # local SSH client is terminated. Never replay it automatically, because a
    # second launch can race a stateful operation such as `sb ensure`.
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


def remote_workspace_path(remote: dict, project_root, workspace_label: str) -> str:
    """Derive the deterministic remote copy path used by durable jobs.

    The path remains a valid plugin-project slug when its deployed tree is
    resolved by the co-located CLI. Keep this spelling aligned with
    ``RemoteJobTransport._prepare_workspace`` and remote lifecycle commands.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", workspace_label or ""):
        raise ValueError("invalid remote workspace label")
    suffix = hashlib.sha256(workspace_label.encode()).hexdigest()[:14]
    return f"{deploy_target_path(remote, project_root)}-workspace-{suffix}"


def prepare_remote_workspace(remote: dict, project_root, workspace_label: str,
                             *, deployed_path: str | None = None) -> str:
    """Copy one deployed exact tree into a deterministic isolated workspace."""
    from sandbox.transports.remote_jobs import workspace_refresh_command

    source = deployed_path or deploy_target_path(remote, project_root)
    target = remote_workspace_path(remote, project_root, workspace_label)
    command = workspace_refresh_command(source, target)
    result = ssh_run(remote, command, timeout=120)
    if result.returncode != 0:
        raise RuntimeError("could not prepare remote workspace")
    return target


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


REMOTE_ENSURE_TIMEOUT_SECONDS = 300
REMOTE_ENSURE_KILL_GRACE_SECONDS = 30
REMOTE_ENSURE_CLIENT_TIMEOUT_SECONDS = (
    REMOTE_ENSURE_TIMEOUT_SECONDS + REMOTE_ENSURE_KILL_GRACE_SECONDS + 15
)


def ensure_remote_instance(remote: dict, target_path: str, label: str | None = None) -> dict:
    """Run remote `sb ensure` for the deployed project and parse its JSON
    result. This is the missing second half after code deploy: it creates or
    refreshes the WordPress instance on the VPS itself."""
    sb = remote_sb_path(remote)
    label_arg = f" --label {shlex.quote(label)} --create" if label else ""
    # This command already runs on the selected VPS.  A remote-first project
    # config must not make this nested ensure resolve its named remote again:
    # the VPS owns only its co-located runtime registry.
    ensure = (
        f"{shlex.quote(sb)} ensure --local --project-dir {shlex.quote(target_path)}"
        f"{label_arg} --json"
    )
    # Bound the command on the VPS, not just the local SSH client. `exec`
    # keeps the timeout supervisor in the session's foreground process group;
    # it terminates the child after the grace period even when a client drops.
    cmd = (
        "exec timeout --signal=TERM "
        f"--kill-after={REMOTE_ENSURE_KILL_GRACE_SECONDS}s "
        f"{REMOTE_ENSURE_TIMEOUT_SECONDS}s {ensure}"
    )
    try:
        res = ssh_run(remote, cmd, timeout=REMOTE_ENSURE_CLIENT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"remote instance ensure timed out after {REMOTE_ENSURE_TIMEOUT_SECONDS}s"
        ) from exc
    data = _last_json(res.stdout or "")
    if res.returncode == 124:
        raise RuntimeError(
            f"remote instance ensure timed out after {REMOTE_ENSURE_TIMEOUT_SECONDS}s"
        )
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


def _caddy_proxy_command(
    hostname: str,
    port: int,
    conf_prefix: str,
    *,
    reject_managed_host: bool = False,
    robots: str = "deny",
) -> str:
    # Preview and control routes are publicly resolvable (a real DNS record on a
    # real zone) but are throwaway staging surfaces, often carrying an autologin
    # URL — nothing here should ever reach a search index. So /robots.txt is
    # answered by Caddy with a blanket Disallow ahead of the proxy, in a
    # `handle` so the two are mutually exclusive routes. Callers with a genuinely
    # public route pass robots="allow".
    # The body is a Caddyfile quoted string spanning real newlines: `respond`
    # emits a `\n` escape literally, so the two-line policy has to BE two lines.
    robots_block = (
        "    handle /robots.txt {\n"
        "        header Content-Type \"text/plain; charset=utf-8\"\n"
        "        respond \"User-agent: *\nDisallow: /\n\" 200\n"
        "    }\n"
        if robots == "deny" else ""
    )
    proxy = f"    reverse_proxy 127.0.0.1:{int(port)}\n"
    body = (f"{robots_block}    handle {{\n    {proxy}    }}\n"
            if robots_block else proxy)
    site_q = shlex.quote(f"{hostname} {{\n{body}}}\n")
    file_q = shlex.quote(f"/etc/caddy/conf.d/{conf_prefix}-{hostname}.caddy")
    managed_host_guard = ""
    if reject_managed_host:
        hostname_pattern = shlex.quote(
            rf"^[[:space:]]*{re.escape(hostname)}[[:space:]]*\{{"
        )
        managed_host_guard = (
            f"if $SUDO grep -l -E {hostname_pattern} "
            "/etc/caddy/conf.d/sandbox-host-*.caddy >/dev/null 2>&1; then "
            "echo 'hostname is managed by permanent Sandbox hosting; "
            "use sb host apply instead' >&2; exit 65; fi; "
        )
    return (
        "set -e; "
        "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"{managed_host_guard}"
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
    """Route a preview hostname through Caddy unless permanent hosting owns it."""
    domain = _validate_hostname(domain, "remote instance domain")
    cmd = _caddy_proxy_command(
        domain,
        port,
        "sandbox-instance",
        reject_managed_host=True,
    )
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


def delete_remote_instance_for_label(remote: dict, target_path: str, label: str) -> bool:
    """Delete the remote instance currently registered for one project label.

    A failed `ensure` may create its registry entry before it can return the
    instance record to the caller. Resolve the name through the CLI's JSON
    inventory so preview rollback still removes that partial stack.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", label or ""):
        raise ValueError("invalid remote Sandbox instance label")
    sb = remote_sb_path(remote)
    command = (
        f"{shlex.quote(sb)} instances --project-dir {shlex.quote(target_path)} --json"
    )
    res = ssh_run(remote, command, timeout=30)
    data = _last_json(res.stdout or "")
    if res.returncode != 0 or not isinstance(data, dict):
        raise RuntimeError(
            "could not list remote instances for preview rollback: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )
    rows = data.get("instances")
    if not isinstance(rows, list):
        raise RuntimeError("remote instance inventory returned no instances list")
    instance = next(
        (row.get("name") for row in rows
         if isinstance(row, dict) and row.get("label") == label),
        None,
    )
    if not instance:
        return False
    delete_remote_instance(remote, str(instance))
    return True


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


def _source_tree_commit(project_root, source_root, commit: str) -> tuple[str, Path]:
    """Create the committed tree containing only ``source_root``.

    A Git commit made in an outer checkout always carries the outer tree.  A
    nested hosting manifest must instead push a deterministic subtree commit,
    while the dirty overlay remains relative to the same source root.  Git's
    subtree splitter preserves the source-root history, so a normal mutable
    branch remains fast-forwardable and an immutable source-ref maps to a
    stable SHA-derived artifact without force-pushing.
    """
    source = Path(source_root).resolve()
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    checkout = (result.stdout or "").strip()
    if result.returncode != 0 or not checkout:
        raise RuntimeError("could not determine the Git checkout for source_root")
    checkout_path = Path(checkout).resolve()
    try:
        relative = source.relative_to(checkout_path)
    except ValueError as exc:
        raise ValueError("source_root must stay within the Git checkout") from exc
    if not relative.parts:
        return commit, checkout_path
    prefix = PurePosixPath(*relative.parts).as_posix()
    split = subprocess.run(
        ["git", "subtree", "split", f"--prefix={prefix}", commit],
        cwd=str(checkout_path), capture_output=True, text=True, check=False,
    )
    split_sha = next(
        (line.strip().lower() for line in reversed((split.stdout or "").splitlines())
         if re.fullmatch(r"[0-9a-fA-F]{40}", line.strip())),
        None,
    )
    if split.returncode != 0 or split_sha is None:
        detail = (split.stderr or split.stdout or "").strip()[:500]
        raise RuntimeError(f"could not create source-root commit: {detail}")
    return split_sha, checkout_path


def push_commits(
    remote: dict,
    project_root,
    target_path: str,
    branch: str | None,
    *,
    source_ref: str | None = None,
    resolved_sha: str | None = None,
    source_root: str | Path | None = None,
) -> str:
    """Push the committed source artifact to the deploy-target repo.

    A normal project pushes ``HEAD`` directly.  When ``source_root`` is a
    nested checkout path, Git subtree creates a commit whose root is exactly
    that directory before the push; dirty files are applied separately by the
    caller.  Both paths use the same registered SSH transport and never route
    through another Git remote.
    """
    push_url = git_ssh_url(remote, target_path)
    source_commit = resolved_sha
    push_cwd = Path(project_root)
    if source_root is not None and source_commit is None:
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=False,
        )
        source_commit = (head_res.stdout or "").strip().lower()
        if head_res.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise RuntimeError("could not resolve the committed source tree")
    if source_ref is not None:
        commit = resolved_sha or resolve_source_ref(project_root, source_ref)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("source_ref did not resolve to a full commit")
        # A SHA-derived ref is immutable by construction: repeated deploys
        # address the same object and never move a user-named branch.
        destination = f"refs/heads/sandbox-source-{commit}"
        source_spec = f"{commit}:{destination}"
        source_commit = commit
        resolved_sha = commit
    else:
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("deploy branch is required for a working-tree source")
        source_spec = f"HEAD:refs/heads/{branch}"
    if source_root is not None:
        tree_commit, push_cwd = _source_tree_commit(project_root, source_root, source_commit or "")
        # A subtree commit cannot update a branch previously seeded with the
        # full outer checkout: Git correctly rejects that non-fast-forward
        # update, and force-pushing would destroy an existing deploy history.
        # Every nested source therefore gets its own immutable tree-SHA ref,
        # regardless of whether the caller selected a mutable branch or an
        # immutable source_ref.  The checked-out target is reset to the
        # returned subtree SHA below.
        destination = f"refs/heads/sandbox-source-{tree_commit}"
        source_spec = f"{tree_commit}:{destination}"
    try:
        _ensure_ssh_control_dir()
    except OSError:
        git_ssh = git_ssh_command(remote, multiplex=False)
    else:
        git_ssh = git_ssh_command(remote)
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = git_ssh
    res = subprocess.run(
        ["git", "push", push_url, source_spec],
        cwd=str(push_cwd), env=env, capture_output=True, text=True,
        timeout=120, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"git push to remote failed: "
            f"{(res.stderr or res.stdout or '').strip()[:1000]}"
        )
    if source_ref is not None:
        return tree_commit if source_root is not None else resolved_sha  # already validated as a full SHA above
    if source_root is not None:
        return tree_commit
    sha_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    return (sha_res.stdout or "").strip().lower()


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
    # `--relative` is essential for a source root nested below the Git
    # checkout.  Without it Git emits paths such as `site/compose.yml` while
    # callers treat `project_root` (the site directory) as the transfer root,
    # causing a second `site/` prefix on the remote and silently missing files
    # when the target is expected to be the declared source root.
    diff_res = subprocess.run(
        ["git", "diff", "--relative", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    diff_text = diff_res.stdout or ""
    status_res = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    untracked = []
    for line in (status_res.stdout or "").splitlines():
        if not line.startswith("??"):
            continue
        relative = line[3:].strip()
        # `git status --short` can still report untracked siblings as
        # `../name` when the declared source root is nested in the checkout.
        # They are outside the transfer root and must not be copied (nor later
        # rejected as unsafe paths by apply_uncommitted).
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            continue
        untracked.append(relative)
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
            ["git", "diff", "--name-only", "--relative", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=False,
        )
        if changed_res.returncode != 0:
            raise RuntimeError(
                f"could not list changed tracked files: "
                f"{(changed_res.stderr or changed_res.stdout or '').strip()[:500]}"
            )
        deleted_res = subprocess.run(
            ["git", "diff", "--name-only", "--relative", "--diff-filter=D", "HEAD"],
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


def _remote_mcp_revision_sources(root: Path | None = None) -> tuple[Path, ...]:
    """Return the deterministic shipped CLI/MCP source surface."""
    root = root or Path(__file__).resolve().parents[2]
    parts = [root / "VERSION", root / "sb"]
    for source_root in (root / "sandbox", root / "mcp" / "wp-server"):
        parts.extend(source_root.rglob("*.py"))
    return tuple(sorted(
        (source for source in parts if source.is_file() and
         ".venv" not in source.parts and "__pycache__" not in source.parts),
        key=lambda source: source.relative_to(root).as_posix(),
    ))


def _remote_mcp_runtime_revision() -> str:
    """Return a non-secret identity for the complete staged runtime surface."""
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for source in _remote_mcp_revision_sources(root):
        relative = source.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(source.read_bytes())
    return digest.hexdigest()[:24]


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
        "StartLimitIntervalSec=60",
        "StartLimitBurst=5",
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


def remote_diagnostics(remote: dict, *, timeout: int = 10) -> dict:
    """Read authenticated, non-secret host evidence over the HTTPS control plane.

    This deliberately avoids SSH and does not expose job output, command lines,
    paths, credentials, or process identifiers. It is the fallback diagnosis
    path when a VPS accepts TCP but its SSH daemon is overloaded or unavailable.
    """
    base = remote.get("control_url")
    token = remote.get("bearer_token")
    if not isinstance(base, str) or not base.startswith("https://"):
        raise RuntimeError("remote diagnostics require an HTTPS control URL")
    if not isinstance(token, str) or not token:
        raise RuntimeError("remote diagnostics require a provisioned bearer token")
    url = base.rstrip("/") + "/diagnostics"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"remote diagnostics returned HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("remote diagnostics endpoint is unreachable") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("remote diagnostics returned an invalid payload")
    return payload


REMOTE_DOCKER_ADDRESS_POOLS = (
    # The /12 must use its canonical network boundary. Docker's built-in pools
    # begin at 172.17/16, but the enclosing configurable pool is 172.16/12.
    {"base": "172.16.0.0/12", "size": 24},
    {"base": "10.201.0.0/16", "size": 24},
    {"base": "10.202.0.0/16", "size": 24},
)


def _remote_docker_pool_program(*, confirm: bool, recover_interrupted: bool = False,
                                expected_running: int | None = None,
                                expected_removed: int = 0,
                                recovery_since: str | None = None) -> str:
    """Build the fixed, non-interactive host-pool transaction."""
    desired = json.dumps(list(REMOTE_DOCKER_ADDRESS_POOLS), sort_keys=True)
    return f'''import datetime, fcntl, hashlib, ipaddress, json, os, pathlib, shutil, subprocess, sys, tempfile, time
CONFIG = pathlib.Path("/etc/docker/daemon.json")
LOCK = pathlib.Path("/run/lock/sandbox-docker-pool.lock")
DESIRED = json.loads({desired!r})
APPLY = {confirm!r}
RECOVER_INTERRUPTED = {recover_interrupted!r}
EXPECTED_RUNNING = {expected_running!r}
EXPECTED_REMOVED = {expected_removed!r}
RECOVERY_SINCE = {recovery_since!r}

def run(argv, timeout=60, check=True):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(argv[0] + " failed")
    return result

def running_ids():
    return set(filter(None, run(["docker", "ps", "-q"], timeout=30).stdout.splitlines()))

def recover(expected, budget_seconds=180):
    deadline = time.monotonic() + budget_seconds
    missing = sorted(expected - running_ids())
    for container_id in missing:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        run(["docker", "start", container_id], timeout=min(10, max(1, int(remaining))), check=False)
    settle_deadline = min(deadline, time.monotonic() + 30)
    while time.monotonic() < settle_deadline:
        remaining = expected - running_ids()
        if not remaining:
            return 0
        time.sleep(2)
    return len(expected - running_ids())

def wait_docker(timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run(["docker", "info"], timeout=15, check=False).returncode == 0:
            return True
        time.sleep(2)
    return False

def sync_parent(path):
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def write_candidate(data, mode, uid, gid):
    descriptor, temporary = tempfile.mkstemp(prefix="daemon.json.", dir=str(CONFIG.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        return temporary
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise

def pool_networks():
    networks = []
    for item in DESIRED:
        network = ipaddress.ip_network(item["base"], strict=True)
        size = item["size"]
        if network.version != 4 or not network.is_private or not isinstance(size, int):
            raise ValueError("address pool is invalid")
        if size < network.prefixlen or size > 30:
            raise ValueError("address pool size is invalid")
        networks.append(network)
    if any(left.overlaps(right) for index, left in enumerate(networks)
           for right in networks[index + 1:]):
        raise ValueError("address pools overlap")
    return networks

def unsafe_route_count(networks):
    docker_interfaces = {{"docker0"}}
    links = run(["docker", "network", "ls", "-q"], timeout=30).stdout.splitlines()
    if links:
        details = run(["docker", "network", "inspect", *links], timeout=60)
        for row in json.loads(details.stdout or "[]"):
            options = row.get("Options") if isinstance(row, dict) else None
            bridge = options.get("com.docker.network.bridge.name") if isinstance(options, dict) else None
            if isinstance(bridge, str) and bridge:
                docker_interfaces.add(bridge)
            network_id = row.get("Id") if isinstance(row, dict) else None
            if isinstance(network_id, str) and len(network_id) >= 12:
                docker_interfaces.add("br-" + network_id[:12])
    result = run(["ip", "-j", "route", "show", "table", "all"], timeout=30)
    routes = json.loads(result.stdout or "[]")
    unsafe = 0
    for row in routes:
        destination = row.get("dst")
        device = str(row.get("dev") or "")
        if not destination or destination == "default":
            continue
        try:
            route = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            continue
        if route.version != 4 or not any(route.overlaps(pool) for pool in networks):
            continue
        if device in docker_interfaces:
            continue
        unsafe += 1
    return unsafe

LOCK.parent.mkdir(parents=True, exist_ok=True)
lock_stream = LOCK.open("a+")
try:
    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps({{"ok": False, "code": "docker_pool_busy", "status": "failed",
                      "message": "Docker pool transaction is busy"}}))
    raise SystemExit(2)

if RECOVER_INTERRUPTED:
    if isinstance(EXPECTED_RUNNING, bool) or not isinstance(EXPECTED_RUNNING, int) or not (1 <= EXPECTED_RUNNING <= 500):
        print(json.dumps({{"ok": False, "code": "docker_pool_recovery_evidence_missing",
                          "status": "failed", "message": "Expected running count is required"}}))
        raise SystemExit(2)
    if isinstance(EXPECTED_REMOVED, bool) or not isinstance(EXPECTED_REMOVED, int) or not (0 <= EXPECTED_REMOVED < EXPECTED_RUNNING):
        print(json.dumps({{"ok": False, "code": "docker_pool_recovery_evidence_missing",
                          "status": "failed", "message": "Expected removed count is invalid"}}))
        raise SystemExit(2)
    backups = sorted(CONFIG.parent.glob(CONFIG.name + ".bak-*"),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    asserted_since = None
    if isinstance(RECOVERY_SINCE, str):
        try:
            asserted_since = datetime.datetime.strptime(
                RECOVERY_SINCE, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            asserted_since = None
    if backups and time.time() - backups[0].stat().st_mtime <= 3600:
        evidence_start = backups[0].stat().st_mtime
    else:
        evidence_start = asserted_since
    if evidence_start is None or not (0 <= time.time() - evidence_start <= 3600):
        print(json.dumps({{"ok": False, "code": "docker_pool_recovery_evidence_missing",
                          "status": "failed", "message": "Recent recovery evidence is unavailable"}}))
        raise SystemExit(2)
    since = max(0, int(evidence_start) - 5)
    # Container State timestamps persist across daemon restarts. The bounded
    # twenty-minute interval covers restart plus the interrupted recovery.
    until = min(int(time.time()) + 1, since + 1200)
    try:
        configured = json.loads(CONFIG.read_text()) if CONFIG.exists() else {{}}
    except Exception:
        configured = None
    if not isinstance(configured, dict):
        print(json.dumps({{"ok": False, "code": "docker_pool_recovery_evidence_missing",
                          "status": "failed", "message": "Interrupted rollback state is not proven"}}))
        raise SystemExit(2)
    all_ids = list(filter(None, run(["docker", "ps", "-aq"], timeout=30).stdout.splitlines()))
    rows = json.loads(run(["docker", "inspect", *all_ids], timeout=90).stdout) if all_ids else []
    evidence_ids = set()
    for row in rows:
        state = row.get("State") if isinstance(row, dict) else None
        container_id = row.get("Id") if isinstance(row, dict) else None
        if not isinstance(state, dict) or not isinstance(container_id, str):
            continue
        timestamps = []
        for key in ("StartedAt", "FinishedAt"):
            value = state.get(key)
            if not isinstance(value, str) or value.startswith("0001-"):
                continue
            try:
                timestamps.append(datetime.datetime.fromisoformat(
                    value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                continue
        if any(since <= value <= until for value in timestamps):
            evidence_ids.add(container_id)
    if len(evidence_ids) + EXPECTED_REMOVED != EXPECTED_RUNNING:
        print(json.dumps({{"ok": False, "code": "docker_pool_recovery_evidence_mismatch",
                          "status": "failed", "message": "Restart event evidence does not match baseline",
                          "recovery_expected_count": EXPECTED_RUNNING,
                          "recovery_removed_count": EXPECTED_REMOVED,
                          "recovery_evidence_count": len(evidence_ids)}}))
        raise SystemExit(2)
    candidates = evidence_ids - running_ids()
    recovery_base = {{
        "ok": True, "status": "recovery_planned" if not APPLY else "recovery_complete",
        "requires_confirm": not APPLY, "recovery_candidate_count": len(candidates),
        "recovery_window_seconds": until - since,
        "recovery_expected_count": EXPECTED_RUNNING,
        "recovery_removed_count": EXPECTED_REMOVED,
        "recovery_evidence_count": len(evidence_ids),
    }}
    if not APPLY:
        print(json.dumps(recovery_base, sort_keys=True))
        raise SystemExit(0)
    missing = recover(candidates)
    recovery_base.update({{"requires_confirm": False,
                           "containers_restored": len(candidates) - missing,
                           "containers_missing": missing,
                           "ok": missing == 0,
                           "status": "recovery_complete" if missing == 0 else "failed"}})
    if missing:
        recovery_base.update({{"code": "docker_pool_recovery_failed",
                              "message": "Interrupted transaction recovery was incomplete"}})
    print(json.dumps(recovery_base, sort_keys=True))
    raise SystemExit(0 if missing == 0 else 3)

try:
    networks = pool_networks()
    route_overlap_count = unsafe_route_count(networks)
except Exception:
    print(json.dumps({{"ok": False, "code": "docker_pool_preflight_failed",
                      "status": "failed", "message": "Docker pool preflight failed"}}))
    raise SystemExit(2)

before = running_ids()
network_count = len(list(filter(None, run(
    ["docker", "network", "ls", "--filter", "type=custom", "-q"], timeout=30
).stdout.splitlines())))
restart_policies = []
if before:
    inspected = run([
        "docker", "inspect", "--format", "{{{{.HostConfig.RestartPolicy.Name}}}}", *sorted(before)
    ], timeout=60).stdout.splitlines()
    restart_policies = [value.strip() or "no" for value in inspected]
had_config = CONFIG.exists()
initial_bytes = CONFIG.read_bytes() if had_config else b""
initial_digest = hashlib.sha256(initial_bytes).hexdigest()
initial_stat = CONFIG.stat() if had_config else None
try:
    current = json.loads(initial_bytes.decode()) if had_config else {{}}
    if not isinstance(current, dict):
        raise ValueError("daemon config must be an object")
except Exception:
    print(json.dumps({{"ok": False, "code": "docker_pool_config_invalid",
                      "status": "failed",
                      "message": "Docker daemon configuration is invalid"}}))
    raise SystemExit(2)
current_pools = current.get("default-address-pools")
current_pools_json = json.dumps(current_pools, sort_keys=True, separators=(",", ":"))
base = {{
    "ok": True, "status": "planned" if not APPLY else "unchanged",
    "current_pools_configured": "default-address-pools" in current,
    "current_pool_count": len(current_pools) if isinstance(current_pools, list) else 0,
    "current_pools_digest": "sha256:" + hashlib.sha256(current_pools_json.encode()).hexdigest(),
    "desired_pools": DESIRED,
    "network_count": network_count, "running_container_count": len(before),
    "restart_policy_none_count": sum(value == "no" for value in restart_policies),
    "subnet_capacity": 4608, "requires_confirm": not APPLY,
    "restart_required": current_pools != DESIRED,
    "route_overlap_count": route_overlap_count,
    "apply_safe": route_overlap_count == 0,
}}
if not APPLY or current_pools == DESIRED:
    print(json.dumps(base, sort_keys=True))
    raise SystemExit(0)
if route_overlap_count:
    print(json.dumps({{"ok": False, "code": "docker_pool_route_overlap",
                      "status": "failed", "message": "Docker pool overlaps host routes",
                      "route_overlap_count": route_overlap_count}}))
    raise SystemExit(2)

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
backup = CONFIG.with_name(CONFIG.name + ".bak-" + stamp)
mode = (initial_stat.st_mode & 0o777) if initial_stat else 0o600
uid = initial_stat.st_uid if initial_stat else 0
gid = initial_stat.st_gid if initial_stat else 0
restart_attempted = False
config_replaced = False
failure_stage = "backup_config"
try:
    if had_config:
        backup_tmp = write_candidate(initial_bytes, mode, uid, gid)
        os.replace(backup_tmp, backup)
        sync_parent(backup)
    failure_stage = "write_config"
    updated = dict(current)
    updated["default-address-pools"] = DESIRED
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(updated, indent=2, sort_keys=True) + "\\n").encode()
    temporary = write_candidate(encoded, mode, uid, gid)
    try:
        failure_stage = "validate_config"
        run(["dockerd", "--validate", "--config-file", temporary], timeout=30)
        current_bytes = CONFIG.read_bytes() if CONFIG.exists() else b""
        if hashlib.sha256(current_bytes).hexdigest() != initial_digest:
            raise RuntimeError("daemon configuration changed concurrently")
        failure_stage = "activate_config"
        os.replace(temporary, CONFIG)
        sync_parent(CONFIG)
        config_replaced = True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    restart_attempted = True
    failure_stage = "restart_docker"
    run(["systemctl", "restart", "docker"], timeout=120)
    failure_stage = "wait_for_docker"
    if not wait_docker():
        raise RuntimeError("docker did not become ready")
    failure_stage = "recover_containers"
    missing_after_recovery = recover(before)
    if missing_after_recovery:
        raise RuntimeError("previously running containers did not recover")
except Exception:
    rollback_missing = len(before)
    rollback_succeeded = not config_replaced
    rollback_error = False
    try:
        if config_replaced:
            if had_config:
                restored = write_candidate(initial_bytes, mode, uid, gid)
                try:
                    os.replace(restored, CONFIG)
                    sync_parent(CONFIG)
                finally:
                    if os.path.exists(restored):
                        os.unlink(restored)
            else:
                CONFIG.unlink(missing_ok=True)
                sync_parent(CONFIG)
        if restart_attempted and config_replaced:
            restarted = run(["systemctl", "restart", "docker"], timeout=120, check=False)
            if restarted.returncode == 0 and wait_docker():
                try:
                    rollback_missing = recover(before)
                except Exception:
                    rollback_error = True
            else:
                rollback_error = True
        elif not restart_attempted:
            try:
                rollback_missing = len(before - running_ids())
            except Exception:
                rollback_error = True
        restored_bytes = CONFIG.read_bytes() if CONFIG.exists() else b""
        rollback_succeeded = (not rollback_error and rollback_missing == 0 and
                              hashlib.sha256(restored_bytes).hexdigest() == initial_digest)
    except Exception:
        rollback_error = True
    finally:
        print(json.dumps({{"ok": False, "code": "docker_pool_apply_failed",
                          "status": "failed",
                          "message": "Docker pool update failed during " + failure_stage,
                          "rollback_attempted": True,
                          "rollback_succeeded": rollback_succeeded,
                          "containers_missing": rollback_missing}}))
    raise SystemExit(3)

digest = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
print(json.dumps({{**base, "ok": missing_after_recovery == 0,
                  "status": "complete" if missing_after_recovery == 0 else "degraded",
                  "requires_confirm": False, "restart_performed": True,
                  "containers_restored": len(before) - missing_after_recovery,
                  "containers_missing": missing_after_recovery,
                  "backup_created": had_config,
                  "config_digest": "sha256:" + digest}}, sort_keys=True))
raise SystemExit(0 if missing_after_recovery == 0 else 4)
'''


def remote_docker_pool(remote: dict, *, confirm: bool = False,
                       recover_interrupted: bool = False,
                       expected_running: int | None = None,
                       expected_removed: int = 0,
                       recovery_since: str | None = None,
                       timeout: int = 900) -> dict:
    """Plan or apply the fixed Docker address-pool transaction on one remote."""
    import base64
    program = base64.b64encode(
        _remote_docker_pool_program(
            confirm=confirm, recover_interrupted=recover_interrupted,
            expected_running=expected_running,
            expected_removed=expected_removed,
            recovery_since=recovery_since).encode()).decode()
    command = (
        "sudo -n python3 -c " + shlex.quote(
            "import base64;exec(base64.b64decode(" + repr(program) + "))")
    )
    result = ssh_run(remote, command, timeout=timeout)
    output = (result.stdout or "").strip()
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("remote Docker pool operation returned invalid output") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise RuntimeError("remote Docker pool operation returned an invalid envelope")
    allowed = {
        "ok", "status", "code", "message", "current_pools_configured",
        "current_pool_count", "current_pools_digest", "desired_pools",
        "network_count", "running_container_count", "restart_policy_none_count",
        "subnet_capacity", "requires_confirm", "restart_required",
        "restart_performed", "containers_restored", "containers_missing",
        "backup_created", "config_digest", "rollback_attempted",
        "rollback_succeeded", "route_overlap_count", "apply_safe",
        "recovery_candidate_count", "recovery_window_seconds",
        "recovery_expected_count", "recovery_evidence_count",
        "recovery_removed_count",
    }
    if set(payload) - allowed:
        raise RuntimeError("remote Docker pool operation returned unexpected fields")
    status = payload.get("status")
    if status not in {"planned", "unchanged", "complete", "degraded", "failed",
                      "recovery_planned", "recovery_complete", "recovery_failed"}:
        raise RuntimeError("remote Docker pool operation returned an invalid status")
    code = payload.get("code")
    known_errors = {
        "docker_pool_config_invalid": "Docker daemon configuration is invalid",
        "docker_pool_apply_failed": "Docker pool update failed",
        "docker_pool_busy": "Docker pool transaction is busy",
        "docker_pool_preflight_failed": "Docker pool preflight failed",
        "docker_pool_route_overlap": "Docker pool overlaps host routes",
        "docker_pool_recovery_evidence_missing": "Recent recovery evidence is unavailable",
        "docker_pool_recovery_evidence_mismatch": "Restart event evidence does not match baseline",
        "docker_pool_recovery_failed": "Interrupted transaction recovery was incomplete",
    }
    if payload["ok"] is False:
        if code not in known_errors:
            raise RuntimeError("remote Docker pool operation returned an unknown error")
        payload["message"] = known_errors[code]
    elif code is not None or payload.get("message") is not None:
        raise RuntimeError("remote Docker pool operation returned an invalid success envelope")
    required_by_status = {
        "planned": {
            "ok", "status", "current_pools_configured", "current_pool_count",
            "current_pools_digest", "desired_pools", "network_count",
            "running_container_count", "restart_policy_none_count",
            "subnet_capacity", "requires_confirm", "restart_required",
            "route_overlap_count", "apply_safe",
        },
        "unchanged": {
            "ok", "status", "current_pools_configured", "current_pool_count",
            "current_pools_digest", "desired_pools", "network_count",
            "running_container_count", "restart_policy_none_count",
            "subnet_capacity", "requires_confirm", "restart_required",
            "route_overlap_count", "apply_safe",
        },
        "complete": {
            "ok", "status", "current_pools_configured", "current_pool_count",
            "current_pools_digest", "desired_pools", "network_count",
            "running_container_count", "restart_policy_none_count",
            "subnet_capacity", "requires_confirm", "restart_required",
            "route_overlap_count", "apply_safe", "restart_performed",
            "containers_restored", "containers_missing", "backup_created",
            "config_digest",
        },
        "recovery_planned": {
            "ok", "status", "requires_confirm", "recovery_candidate_count",
            "recovery_window_seconds", "recovery_expected_count",
            "recovery_evidence_count", "recovery_removed_count",
        },
        "recovery_complete": {
            "ok", "status", "requires_confirm", "recovery_candidate_count",
            "recovery_window_seconds", "recovery_expected_count",
            "recovery_evidence_count", "recovery_removed_count",
            "containers_restored", "containers_missing",
        },
    }
    if payload["ok"]:
        if status not in required_by_status or not required_by_status[status].issubset(payload):
            raise RuntimeError("remote Docker pool operation returned incomplete evidence")
        if result.returncode != 0:
            raise RuntimeError("remote Docker pool operation failed without a safe error")
        if status == "planned" and payload.get("requires_confirm") is not True:
            raise RuntimeError("remote Docker pool plan omitted confirmation gate")
        if status == "recovery_planned" and payload.get("requires_confirm") is not True:
            raise RuntimeError("remote Docker recovery plan omitted confirmation gate")
        if status == "unchanged" and payload.get("restart_required") is not False:
            raise RuntimeError("remote Docker pool unchanged receipt is inconsistent")
        if status == "complete" and (
                payload.get("restart_performed") is not True or
                payload.get("containers_missing") != 0):
            raise RuntimeError("remote Docker pool completion receipt is inconsistent")
        if status == "recovery_complete" and payload.get("containers_missing") != 0:
            raise RuntimeError("remote Docker pool recovery receipt is inconsistent")
        if status == "recovery_complete" and payload.get("requires_confirm") is not False:
            raise RuntimeError("remote Docker pool recovery receipt is inconsistent")
    else:
        expected_exit = {
            "docker_pool_config_invalid": 2,
            "docker_pool_busy": 2,
            "docker_pool_preflight_failed": 2,
            "docker_pool_route_overlap": 2,
            "docker_pool_recovery_evidence_missing": 2,
            "docker_pool_recovery_evidence_mismatch": 2,
            "docker_pool_recovery_failed": 3,
            "docker_pool_apply_failed": 3,
        }[code]
        if result.returncode != expected_exit or status != "failed":
            raise RuntimeError("remote Docker pool error receipt is inconsistent")
        failure_required = {"ok", "status", "code", "message"}
        if code == "docker_pool_route_overlap":
            failure_required.add("route_overlap_count")
        if code == "docker_pool_recovery_evidence_mismatch":
            failure_required.update({"recovery_expected_count", "recovery_evidence_count"})
        if code == "docker_pool_apply_failed":
            failure_required.update({"rollback_attempted", "rollback_succeeded",
                                     "containers_missing"})
        if code == "docker_pool_recovery_failed":
            failure_required.update({"recovery_candidate_count", "recovery_window_seconds",
                                     "containers_restored", "containers_missing"})
        if not failure_required.issubset(payload):
            raise RuntimeError("remote Docker pool error omitted required evidence")
    if "desired_pools" in payload and payload["desired_pools"] != list(REMOTE_DOCKER_ADDRESS_POOLS):
        raise RuntimeError("remote Docker pool operation returned unexpected desired pools")
    for field in ("network_count", "running_container_count", "restart_policy_none_count",
                  "subnet_capacity", "current_pool_count", "containers_restored",
                  "containers_missing", "route_overlap_count",
                  "recovery_candidate_count", "recovery_window_seconds",
                  "recovery_expected_count", "recovery_evidence_count",
                  "recovery_removed_count"):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise RuntimeError("remote Docker pool operation returned invalid counts")
    if payload.get("recovery_window_seconds", 0) > 1200:
        raise RuntimeError("remote Docker pool recovery window is unbounded")
    if payload["ok"] and payload.get("recovery_evidence_count") is not None and (
            payload.get("recovery_evidence_count") + payload.get("recovery_removed_count", 0) !=
            payload.get("recovery_expected_count")):
        raise RuntimeError("remote Docker pool recovery evidence is inconsistent")
    if payload.get("containers_restored") is not None and payload.get("recovery_candidate_count") is not None and (
            payload["containers_restored"] + payload.get("containers_missing", 0) !=
            payload["recovery_candidate_count"]):
        raise RuntimeError("remote Docker pool recovery counts are inconsistent")
    for field in ("current_pools_configured", "requires_confirm", "restart_required",
                  "restart_performed", "backup_created", "rollback_attempted",
                  "rollback_succeeded", "apply_safe"):
        value = payload.get(field)
        if value is not None and not isinstance(value, bool):
            raise RuntimeError("remote Docker pool operation returned invalid flags")
    for field in ("current_pools_digest", "config_digest"):
        value = payload.get(field)
        if value is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise RuntimeError("remote Docker pool operation returned an invalid digest")
    return payload


def verify_remote(remote: dict, *, name: str | None = None, timeout: int = 10) -> dict:
    """Perform the supported authenticated, secret-safe remote probe.

    Only the recorded control transport is used.  The bearer token remains in
    the in-memory request header and is never included in the returned
    envelope, exception text, or a subprocess argument.  This helper is
    intentionally read-only and bounded so a CLI/MCP adapter can surface the
    same evidence without opening an SSH shell.
    """
    if not isinstance(remote, dict):
        raise ValueError("remote verification target is invalid")
    base = remote.get("control_url")
    token = remote.get("bearer_token")
    if not isinstance(base, str) or not base.strip():
        raise RuntimeError("auth_verification_unavailable: control endpoint is unavailable")
    if not isinstance(token, str) or not token:
        raise RuntimeError("auth_verification_unavailable: stored authentication is unavailable")
    parsed = urlsplit(base.strip())
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise RuntimeError("auth_verification_unavailable: control endpoint is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("auth_verification_unavailable: control endpoint is invalid") from exc
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/mcp", "", ""))
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )
    started = time.monotonic()
    status = None
    authenticated = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            authenticated = status in {200, 204, 400, 405, 406}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        authenticated = status in {200, 204, 400, 405, 406}
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("remote_auth_failed: authenticated control probe was unavailable")
    elapsed = min(max(time.monotonic() - started, 0.0), float(timeout))
    service = remote.get("mcp_service") if isinstance(remote.get("mcp_service"), dict) else {}
    revision = service.get("runtime_revision") if isinstance(service.get("runtime_revision"), str) else None
    safe_endpoint = {"scheme": parsed.scheme, "host": parsed.hostname}
    if port is not None:
        safe_endpoint["port"] = port
    return {
        "ok": authenticated,
        "remote": name,
        "authenticated": authenticated,
        "endpoint": safe_endpoint,
        "revision": revision,
        "status": status,
        "elapsed_seconds": elapsed,
        "error": None if authenticated else "remote_auth_failed",
    }


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
        f"grep -Fq -- {shlex.quote('--bind ' + bind + ' --port ' + str(port))} "
        f"$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE}; then echo ownership=proven; else echo ownership=ambiguous; fi; "
        if marker else "echo marker=0; "
    )
    listener_probe = (
        f"if command -v ss >/dev/null 2>&1; then listeners=$(ss -H -ltn 'sport = :{port}' 2>/dev/null | awk '{{print $4}}'); "
        f"if printf '%s\\n' \"$listeners\" | grep -Fqx -- {shlex.quote(bind + ':' + str(port))} || printf '%s\\n' \"$listeners\" | grep -Fqx -- {shlex.quote('[' + bind + ']:' + str(port))}; then echo listener=expected; "
        "elif test -z \"$listeners\"; then echo listener=missing; else echo listener=unexpected; fi; "
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
        "\ntry:\n response=urllib.request.urlopen(request, timeout=5); print('auth=ok' if response.status in (200,204,400,405,406) else 'auth=failed')\n"
        "except urllib.error.HTTPError as exc:\n print('auth=ok' if exc.code in (200,204,400,405,406) else 'auth=failed')\n"
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
    pid_probe = (
        f"cgroup=$(systemctl --user show {REMOTE_MCP_SERVICE} -p ControlGroup --value 2>/dev/null || true); "
        "if test -n \"$pid\" && test \"$pid\" != 0 && test -n \"$cgroup\" && "
        "test -r \"/proc/$pid/cgroup\" && grep -Fq \"$cgroup\" \"/proc/$pid/cgroup\"; "
        "then echo pid_ownership=proven; "
        "elif test -n \"$pid\" && test \"$pid\" != 0; then echo pid_ownership=ambiguous; "
        "else echo pid_ownership=not_running; fi; "
    )
    command = (
        "if ! command -v systemctl >/dev/null 2>&1; then echo unavailable; exit 0; fi; "
        f"printf 'enabled='; systemctl --user is-enabled {REMOTE_MCP_SERVICE} 2>/dev/null || true; "
        f"printf 'active='; systemctl --user is-active {REMOTE_MCP_SERVICE} 2>/dev/null || true; "
        f"pid=$(systemctl --user show {REMOTE_MCP_SERVICE} -p MainPID --value 2>/dev/null || true); printf 'pid=%s\\n' \"$pid\"; "
        "printf 'linger='; loginctl show-user \"$USER\" -p Linger --value 2>/dev/null || true; "
        + marker_probe + pid_probe + listener_probe + auth_probe + legacy_probe
    )
    res = ssh_run(remote, command, timeout=20)
    values: dict[str, str] = {}
    for line in (res.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"enabled", "active", "pid", "linger", "ownership", "pid_ownership", "listener", "auth", "legacy_pidfile"}:
            values[key] = value.strip().lower()
    installed = values.get("enabled") not in {"", "not-found", "unknown"}
    active = values.get("active") == "active"
    enabled = values.get("enabled") == "enabled"
    linger = values.get("linger") == "yes"
    unit_owned = expected and installed and values.get("ownership") == "proven"
    pid_owned = values.get("pid_ownership", "unknown")
    ownership = "proven" if unit_owned and (not active or pid_owned == "proven") else "missing" if not installed else "ambiguous"
    return {
        "installed": installed, "enabled": enabled, "active": active,
        "linger": linger, "ownership": ownership,
        "service_name": REMOTE_MCP_SERVICE if expected else None,
        "pid_present": values.get("pid", "0") not in {"", "0"},
        "pid_ownership": pid_owned,
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
            "prepare the staged Sandbox CLI and MCP virtual environments",
            "write owner-only remote credential file", "install Sandbox-owned user unit",
            "reload user manager and enable linger", "enable and verify selected unit",
        ],
        "legacy_pidfile_detected": bool((observed or {}).get("legacy_pidfile") == "present"),
    }


def migrate_remote_mcp_service(remote: dict, bind: str, port: int, token: str,
                               public_url: str | None = None, *, confirm: bool = False,
                               legacy_pidfile: bool = False) -> dict:
    """Install the scoped remote service only after explicit confirmation.

    The token is passed through SSH stdin, never embedded in the unit or command.
    """
    plan = remote_mcp_service_plan(remote, bind, port, public_url,
                                   observed={"legacy_pidfile": "present"} if legacy_pidfile else None)
    if not confirm:
        return plan
    if not isinstance(token, str) or not token:
        raise ValueError("remote MCP token is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("remote MCP token has unsafe characters")
    unit = render_remote_mcp_unit(bind, port, public_url)
    record = remote_mcp_service_record(bind, port, public_url)
    legacy_child = (
        f"echo $$ > {_MCP_PIDFILE}; exec ./sb mcp --transport streamable-http "
        f"--bind {shlex.quote(bind)} --port {port}"
        + (f" --public-url {shlex.quote(public_url)}" if public_url else "")
        + " </dev/null > /tmp/sandbox-mcp-remote.log 2>&1"
    )
    legacy_restart = (
        "( cd \"$HOME/sandbox/sb-src\"; "
        "SANDBOX_REMOTE_MCP_TOKEN=\"$sandbox_remote_mcp_token\" "
        f"setsid -f sh -c {shlex.quote(legacy_child)} )"
    )
    legacy_preflight = (
        f"legacy_pid=''; if test {1 if legacy_pidfile else 0} = 1; then "
        f"test -r {_MCP_PIDFILE}; legacy_pid=$(cat {_MCP_PIDFILE}); "
        "case \"$legacy_pid\" in ''|*[!0-9]*) exit 42;; esac; "
        "test -r \"/proc/$legacy_pid/cmdline\"; legacy_cwd=$(readlink \"/proc/$legacy_pid/cwd\"); "
        "legacy_cmd=$(tr '\\0' ' ' < \"/proc/$legacy_pid/cmdline\"); "
        "case \"$legacy_cwd\" in \"$HOME/sandbox/sb-src\"|\"$HOME/sandbox/sb-src (deleted)\") ;; *) exit 42;; esac; "
        "case \"$legacy_cmd\" in *'--transport streamable-http'*'--bind " + bind + "'*'--port " + str(port) + "'*) ;; *) exit 42;; esac; fi; "
    )
    unit_ownership_preflight = (
        "if test -f \"$unit_path\"; then "
        f"grep -Fqx {shlex.quote('Environment=SANDBOX_REMOTE_MCP_MARKER=' + record['ownership_marker'])} \"$unit_path\" && "
        f"grep -Fq -- {shlex.quote('--bind ' + bind + ' --port ' + str(port))} \"$unit_path\" && "
        "grep -Fqx 'WorkingDirectory=%h/sandbox/sb-src' \"$unit_path\" || exit 43; fi; "
    )
    runtime_preflight = (
        "runtime=$HOME/sandbox/sb-src; test -x \"$runtime/sb\"; "
        "if test ! -x \"$runtime/.cli-venv/bin/python\"; then python3 -m venv \"$runtime/.cli-venv\"; "
        "\"$runtime/.cli-venv/bin/python\" -m pip install --quiet --disable-pip-version-check pyyaml; fi; "
        "( cd \"$runtime\" && ./sb mcp-install >/dev/null ); "
    )
    command = (
        "set -eu; umask 077; mkdir -p $HOME/.sandbox $HOME/.config/systemd/user; chmod 700 $HOME/.sandbox; "
        f"unit_path=$HOME/.config/systemd/user/{REMOTE_MCP_SERVICE}; env_path={_REMOTE_MCP_ENV}; "
        + unit_ownership_preflight +
        "backup=$HOME/.sandbox/mcp-remote-backup-$$; mkdir -p \"$backup\"; "
        "had_unit=0; had_env=0; if test -f \"$unit_path\"; then cp \"$unit_path\" \"$backup/unit\"; had_unit=1; fi; "
        "if test -f \"$env_path\"; then cp \"$env_path\" \"$backup/env\"; had_env=1; fi; "
        + legacy_preflight +
        runtime_preflight +
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
        "if test -n \"$legacy_pid\"; then kill \"$legacy_pid\"; rm -f " + _MCP_PIDFILE + "; fi; "
        "if ! systemctl --user daemon-reload || ! loginctl enable-linger \"$USER\"; then "
        "rollback; if test -n \"$legacy_pid\"; then " + legacy_restart + "; fi; exit 1; fi; "
        # A first install has no loaded unit to reset.  Keep reset narrowly
        # scoped, but do not let that benign condition bypass the required
        # enablement and active-state checks below.
        f"systemctl --user reset-failed {REMOTE_MCP_SERVICE} || true; "
        # `enable --now` does not restart an already-active unit.  The
        # credential EnvironmentFile was replaced above, so explicitly restart
        # the Sandbox-owned unit to ensure its bearer middleware receives the
        # newly minted token rather than retaining an old process environment.
        f"if ! systemctl --user enable {REMOTE_MCP_SERVICE} || ! systemctl --user restart {REMOTE_MCP_SERVICE} || ! systemctl --user is-active --quiet {REMOTE_MCP_SERVICE}; then "
        "rollback; if test -n \"$legacy_pid\"; then " + legacy_restart + "; fi; exit 1; fi; rm -rf \"$backup\""
    )
    try:
        res = ssh_run(remote, command, timeout=300, input_data=token + "\n")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("timed out installing the remote MCP service") from exc
    if res.returncode != 0:
        if res.returncode in {42, 43}:
            raise RuntimeError("remote_service_ownership_unknown")
        detail = redact_ssh_connection((res.stderr or res.stdout or "").strip()[:500], remote)
        if detail:
            raise RuntimeError(f"could not install the remote MCP service: {detail}")
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
