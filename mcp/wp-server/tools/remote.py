from __future__ import annotations
import json
import subprocess

from app import *  # noqa: F401,F403


@mcp.tool()
def remote_deploy(project_dir: str, remote: str) -> dict:
    """Deploy the local project's current state (committed HEAD + uncommitted
    changes, including untracked files) to a registered, provisioned remote VPS
    target on demand. One-way, on-demand only — never a continuous sync; the
    remote reflects exactly the working tree as of THIS call. See
    docs/remote-hosting.md, specs/014-remote-vps-hosting/.

    Every deploy REPLACES the remote's uncommitted layer rather than stacking
    on a previous one — a stale diff from an earlier deploy never silently
    survives underneath a new one.

    project_dir: the project to deploy.
    remote: which registered, provisioned remote to deploy to (see `./sb remote
      list`) — required, no default is inferred.

    Returns {ok, remote, pushed_commit, uncommitted_files_applied, error}.
    """
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "deploy", "--project-dir", project_dir, "--remote", remote, "--json"]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "remote": remote,
            "pushed_commit": None,
            "uncommitted_files_applied": 0,
            "error": "remote_deploy timed out after 300s",
        }
    lines = (res.stdout or "").strip().splitlines()
    result = _safe_json(lines[-1]) if lines else None
    if isinstance(result, dict) and "remote" in result:
        return result
    return {
        "ok": False,
        "remote": remote,
        "pushed_commit": None,
        "uncommitted_files_applied": 0,
        "error": (res.stderr or res.stdout or "deploy failed").strip()[:2000],
    }
