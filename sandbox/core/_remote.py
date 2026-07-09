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
        tailscale_host: "myvps.tailnet-name.ts.net"   # recorded by `provision`
        mcp_port: 9174                                 # recorded by `provision`
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
import re
import secrets
import subprocess
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.core._config import ensure_pyyaml, _local_yaml
from sandbox.core._paths import CONFIG_LOCAL

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


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


def ssh_run(remote: dict, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `command` on the remote over SSH. Shells to the system `ssh` binary
    using the remote's stored connection string -- no new pip dependency, same
    pattern as shelling to `docker`/`wp` elsewhere in this codebase. check=False:
    callers interpret returncode/stdout/stderr themselves, never a bare
    exception on a nonzero remote exit."""
    ssh_target = (remote or {}).get("ssh") or ""
    if not ssh_target:
        raise ValueError("remote has no ssh connection string configured")
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_target, command],
        capture_output=True, text=True, timeout=timeout, check=False,
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
    ssh_target = (remote or {}).get("ssh") or ""
    if not ssh_target:
        raise ValueError("remote has no ssh connection string configured")
    push_path = target_path if target_path.startswith("/") else f"/{target_path}"
    push_url = f"ssh://{ssh_target}{push_path}"
    res = subprocess.run(
        ["git", "push", push_url, f"HEAD:refs/heads/{branch}"],
        cwd=str(project_root), capture_output=True, text=True, timeout=120, check=False,
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
    """Applies the tracked-file diff (via `git apply` fed over SSH) and
    transfers untracked files (via `scp`, creating parent directories first)
    on top of a just-reset clean tree. Returns the total number of files
    touched, for the user-facing confirmation message."""
    ssh_target = (remote or {}).get("ssh") or ""
    if not ssh_target:
        raise ValueError("remote has no ssh connection string configured")
    applied = 0
    if diff_text.strip():
        name_res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=False,
        )
        changed_names = [n for n in (name_res.stdout or "").splitlines() if n.strip()]
        import base64
        encoded = base64.b64encode(diff_text.encode()).decode()
        cmd = f"cd {target_path} && echo {encoded} | base64 -d | git apply -"
        res = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_target, cmd],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"could not apply uncommitted diff on remote: "
                f"{(res.stderr or res.stdout or '').strip()[:500]}"
            )
        applied += len(changed_names)
    for relpath in untracked:
        local_path = Path(project_root) / relpath
        if not local_path.is_file():
            continue
        remote_path = f"{target_path}/{relpath}"
        parent_cmd = f"mkdir -p \"$(dirname '{remote_path}')\""
        mk_res = ssh_run(remote, parent_cmd, timeout=15)
        if mk_res.returncode != 0:
            raise RuntimeError(
                f"could not prepare directory for untracked file {relpath}: "
                f"{(mk_res.stderr or mk_res.stdout or '').strip()[:500]}"
            )
        res = subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             str(local_path), f"{ssh_target}:{remote_path}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"could not transfer untracked file {relpath}: "
                f"{(res.stderr or res.stdout or '').strip()[:500]}"
            )
        applied += 1
    return applied


DEFAULT_MCP_PORT = 9174
_MCP_PIDFILE = "/tmp/sandbox-mcp-remote.pid"


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


def start_remote_mcp_server(remote: dict, tailscale_ip: str, port: int, token: str) -> None:
    """Start `sb mcp --transport streamable-http ...` on the VPS as a
    detached background process, recording its PID in a pidfile so
    stop_remote_mcp_server / a later start can find and manage it. Safe to
    call when already running -- stops any prior instance first (idempotent,
    matches spec FR-005's expectation applied to the server process too)."""
    stop_remote_mcp_server(remote)
    cmd = (
        f"cd $SANDBOX_HOME/sb-src && "
        f"nohup ./sb mcp --transport streamable-http --bind {tailscale_ip} "
        f"--port {port} --token {token} "
        f"> /tmp/sandbox-mcp-remote.log 2>&1 & echo $! > {_MCP_PIDFILE}"
    )
    res = ssh_run(remote, cmd, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(
            f"could not start the remote MCP server: "
            f"{(res.stderr or res.stdout or '').strip()[:500]}"
        )


def stop_remote_mcp_server(remote: dict) -> None:
    """Stop the remote MCP server process if a pidfile from a prior start
    exists and that PID is still alive. A no-op (not an error) if nothing is
    running -- matches spec's `remote down` Edge Cases expectation that
    stopping never affects WordPress instances, only this control-plane
    process."""
    cmd = (
        f"if [ -f {_MCP_PIDFILE} ]; then "
        f"kill \"$(cat {_MCP_PIDFILE})\" 2>/dev/null; rm -f {_MCP_PIDFILE}; fi"
    )
    ssh_run(remote, cmd, timeout=15)


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
