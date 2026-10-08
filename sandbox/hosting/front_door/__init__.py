"""Remote front-door contract: which web server owns a remote's public 80/443.

The manifest below is the only place a front-door mode is declared. A remote
records its mode once (``front_door`` in its registration, written through
``_remote.put_remote``); every routing caller resolves the mode with
:func:`front_door_mode` and checks a capability with :func:`require_capability`
before it makes any remote change.

``caddy`` is the default and is the unchanged historical path: Sandbox installs
Caddy and lets it own public 80/443. ``nginx`` is opt-in: a host-incumbent nginx
(for example a control panel's) stays the front door and Sandbox only manages
its own ``/etc/nginx/conf.d/sandbox-*.conf`` files. See docs/remote-hosting.md
("nginx front door").
"""
from __future__ import annotations

from dataclasses import dataclass


class FrontDoorError(RuntimeError):
    """A finite, actionable front-door refusal."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class FrontDoorDeclaration:
    mode: str
    default: bool
    capabilities: frozenset
    summary: str


# Routing surfaces a remote front door can serve.
HOST_ROUTES = "host_routes"            # sb host plan/apply
CONTROL_ROUTE = "control_route"        # HTTPS MCP control endpoint (provision/up)
INSTANCE_ROUTES = "instance_routes"    # sb deploy --expose (+ aliases)
PREVIEW_ROUTES = "preview_routes"      # sb preview

CAPABILITIES = (HOST_ROUTES, CONTROL_ROUTE, INSTANCE_ROUTES, PREVIEW_ROUTES)

FRONT_DOOR_MANIFEST: tuple[FrontDoorDeclaration, ...] = (
    FrontDoorDeclaration(
        "caddy", True, frozenset(CAPABILITIES),
        "Sandbox installs Caddy and Caddy owns public 80/443"),
    FrontDoorDeclaration(
        "nginx", False, frozenset({HOST_ROUTES, CONTROL_ROUTE}),
        "a host-incumbent nginx owns 80/443; Sandbox manages only its own "
        "/etc/nginx/conf.d/sandbox-*.conf files"),
)

DEFAULT_FRONT_DOOR = next(item.mode for item in FRONT_DOOR_MANIFEST if item.default)


def declaration(mode: str) -> FrontDoorDeclaration:
    for item in FRONT_DOOR_MANIFEST:
        if item.mode == mode:
            return item
    raise FrontDoorError(
        "front_door_unknown",
        f"unknown front door {mode!r}; choose one of "
        + ", ".join(item.mode for item in FRONT_DOOR_MANIFEST))


def validate_mode(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FrontDoorError("front_door_unknown", "front door must be a mode name")
    return declaration(value.strip().lower()).mode


def front_door_mode(entry: dict | None) -> str:
    """Resolve a registered remote's front door; unrecorded means the default."""
    raw = (entry or {}).get("front_door")
    if raw is None:
        return DEFAULT_FRONT_DOOR
    return validate_mode(raw)


def require_capability(entry: dict | None, capability: str) -> str:
    """Refuse before any side effect when the remote's front door lacks a surface."""
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown front-door capability {capability!r}")
    mode = front_door_mode(entry)
    if capability not in declaration(mode).capabilities:
        raise FrontDoorError(
            "front_door_capability_unavailable",
            f"this remote's {mode} front door does not support {capability} yet; "
            "use a Caddy remote for this surface (see docs/remote-hosting.md, "
            "nginx front door)")
    return mode


def manifest_summary() -> list[dict]:
    return [{"mode": item.mode, "default": item.default,
             "capabilities": sorted(item.capabilities), "summary": item.summary}
            for item in FRONT_DOOR_MANIFEST]
