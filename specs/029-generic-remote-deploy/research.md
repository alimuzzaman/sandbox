# Research: Generic Remote Deploy

## Decision: Reuse the existing remote transfer and ensure primitives

**Rationale**: `ensure_deploy_repo`, direct Git push, reset, uncommitted-state apply,
and `ensure_remote_instance` receive only a project root and already run remote `sb
ensure`; they are generic by construction.

**Alternatives considered**: A separate generic deploy transport would duplicate SSH,
dirty-file, redaction, and error handling without user benefit.

## Decision: Capability selection follows project kind

**Rationale**: The current failure occurs before transfer because `deploy` always
requires `wordpress.remote-deploy`. The WordPress adapter must retain that capability;
the Compose adapter receives an analogous generic deploy capability.

**Alternatives considered**: Giving Compose all WordPress capability would permit
unrelated WordPress operations and violate the adapter boundary.

## Decision: Expose the returned generic HTTP port; never mutate app URLs

**Rationale**: Generic `ensure` returns `http_port` and `url`, whereas WordPress
returns `wordpress_port` and requires home/siteurl updates. Caddy is a shared route
mechanism, but URL mutation and plugin activation are WordPress policy.

**Alternatives considered**: A single `wordpress_port` fallback would hide bad generic
instance data; a universal URL setter would invoke WordPress CLI against generic apps.

## Decision: Keep optional plugin-slug accepted but reject it for generic projects

**Rationale**: Retaining CLI/MCP argument compatibility avoids a breaking schema.
Silently ignoring an explicit generic `plugin_slug` would mislead the caller; a local,
actionable validation makes the boundary clear.

**Alternatives considered**: Removing the argument breaks WordPress callers; using it
for arbitrary app commands is unsafe discovery.
