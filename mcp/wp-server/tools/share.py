"""MCP tools for hosted share links (spec 081) and the front-door refresh (spec 084).

They call the same code as ``sb host share`` and ``sb host refresh-front-door`` in-process. MCP has no terminal, so a
target whose manifest lacks ``share.agent_mint: true`` refuses with
``agent_mint_not_enabled``. A created link is returned once, in the result.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def register(server: Any, dependencies: Any = None) -> None:
    for tool in (host_share_create, host_share_list, host_share_revoke, host_front_door_refresh):
        server.tool()(tool)


def _call(function, *args, **kwargs) -> Dict[str, Any]:
    from sandbox.hosting.share import ShareRefusal
    try:
        return function(*args, **kwargs)
    except ShareRefusal as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}
    except SystemExit:
        return {"ok": False, "code": "share_failed", "message": "the share command stopped"}
    except Exception as exc:  # noqa: BLE001 -- a tool answers, it never raises
        return {"ok": False, "code": "share_failed", "message": type(exc).__name__}


def host_share_create(remote: str, project_dir: Optional[str] = None, environment: Optional[str] = None,
                      target: Optional[str] = None, name: Optional[str] = None,
                      stack: Optional[str] = None, entry: Optional[str] = None,
                      path: Optional[str] = None, ttl: Optional[str] = None,
                      uses: Optional[int] = None, email: Optional[str] = None) -> Dict[str, Any]:
    """Make a share link for a private hosted site, one page or folder of it (`path`), or a
    stack page (`stack`). Returns the link once: hand it to the person. Default lifetime 7d,
    at most 30d; `uses` (1-20) limits how many browsers may open it. Needs the target's
    manifest to set `share.agent_mint: true`."""
    from sandbox.commands import host_share
    return _call(host_share.create_share, remote, stack=stack, target=target, name=name,
                 project_dir=project_dir, environment=environment, entry=entry, path=path,
                 ttl=ttl, uses=uses, email=email, caller="mcp")


def host_share_list(remote: str, project_dir: Optional[str] = None, environment: Optional[str] = None,
                    target: Optional[str] = None, name: Optional[str] = None,
                    stack: Optional[str] = None, entry: Optional[str] = None) -> Dict[str, Any]:
    """List live share links and sessions of a hosted target: ids, scopes, times and use
    counts, never a link or token."""
    from sandbox.commands import host_share
    return _call(host_share.list_shares, remote, target=target, name=name, stack=stack,
                 project_dir=project_dir, environment=environment, entry=entry)


def host_share_revoke(remote: str, link_id: str, project_dir: Optional[str] = None,
                      environment: Optional[str] = None, target: Optional[str] = None,
                      name: Optional[str] = None, stack: Optional[str] = None,
                      entry: Optional[str] = None) -> Dict[str, Any]:
    """Revoke one share link (l-...) with its sessions, one session (s-...), or `all`."""
    from sandbox.commands import host_share
    return _call(host_share.revoke_share, remote, link_id, target=target, name=name, stack=stack,
                 project_dir=project_dir, environment=environment, entry=entry)


def host_front_door_refresh(remote: str, target: Optional[str] = None, name: Optional[str] = None,
                            project_dir: Optional[str] = None, environment: Optional[str] = None,
                            confirm: bool = False) -> Dict[str, Any]:
    """Re-render and reload only a hosted target's front-door route with the current Sandbox
    renderer (for example to pick up the default noindex header), from what its last
    successful delivery recorded. No container, Compose, DNS or certificate change. Without
    `confirm` it returns the plan; a private target never becomes public, and a failure puts
    the previous route back. Select the target by `target` (P/E), stack `name`, or
    `project_dir`."""
    from sandbox.commands import hosting
    return hosting.front_door_refresh_request(
        remote, target=target, name=name, project_dir=project_dir, environment=environment,
        confirm=confirm)
