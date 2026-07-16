from __future__ import annotations
import re

from app import SANDBOX_ROOT, _run_sandbox_json, mcp


def _capability_error(project_dir: str, capability: str):
    """Keep the legacy module-import test harness compatible with old app fakes."""
    try:
        from app import _require_project_capability
    except ImportError:
        return None
    return _require_project_capability(project_dir, None, capability)


_SSH_CONNECTION_RE = re.compile(
    r"(?:ssh://)?[^\s/@:]+@(?:\[[^\]\s]+\]|[^\s/:]+)(?::\d+)?"
)


def _redact_ssh_connection(value: str) -> str:
    return _SSH_CONNECTION_RE.sub("[redacted SSH target]", value or "")


@mcp.tool()
def remote_deploy(project_dir: str, remote: str, ensure: bool = True,
                  expose: bool = True, domain: str | None = None,
                  plugin_slug: str | None = None) -> dict:
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

    ensure: when true, boot/refresh the remote WordPress instance after deploy.
    expose: when true, add/update the public HTTPS route and set WP home/siteurl.
    domain: optional public hostname, e.g.
      default-templately-ai-builder.sandbox.asb.bd. If omitted with expose=true,
      CLI defaults to default-<project-slug>.sandbox.asb.bd.
    plugin_slug: optional slug to activate after ensure. Defaults to project slug.

    Returns {ok, remote, pushed_commit, uncommitted_files_applied, instance, url, error}.
    """
    capability_error = _capability_error(project_dir, "wordpress.remote-deploy")
    if capability_error:
        return capability_error
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "deploy", "--project-dir", project_dir, "--remote", remote, "--json"]
    if ensure:
        cmd.append("--ensure")
    if expose:
        cmd.append("--expose")
    if domain:
        cmd.extend(["--domain", domain])
    if plugin_slug:
        cmd.extend(["--plugin-slug", plugin_slug])
    res = _run_sandbox_json(cmd, 1200)
    if res["timed_out"]:
        return {
            "ok": False,
            "remote": remote,
            "pushed_commit": None,
            "uncommitted_files_applied": 0,
            "instance": None,
            "url": None,
            "error": "remote_deploy timed out after 1200s",
        }
    result = res["payload"]
    if isinstance(result, dict) and "remote" in result:
        if isinstance(result.get("error"), str):
            result["error"] = _redact_ssh_connection(result["error"])
        return result
    return {
        "ok": False,
        "remote": remote,
        "pushed_commit": None,
        "uncommitted_files_applied": 0,
        "instance": None,
        "url": None,
        "error": _redact_ssh_connection(
            (res["stderr"] or res["stdout"] or "deploy failed").strip()[:2000]
        ),
    }
