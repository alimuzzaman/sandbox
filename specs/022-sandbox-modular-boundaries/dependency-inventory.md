# Dependency and Ownership Baseline

**Audit point**: `e52eb8d`

## Core/config/registry

- `sandbox_core.py` owns project discovery, common/WordPress defaults, plugin normalization, registry paths, locking, v1→v2 migration, CRUD, and project locks.
- `sandbox/core/_instances.py` consumes registry/config and constructs WordPress-shaped runtime records and lifecycle behavior.
- Registry v1 keys are canonical roots; v2 keys are `<root>::<label>`. Current reads may migrate v1 eagerly.
- Current writes use a sibling `.json.tmp` and `os.replace`, but do not expose an independently testable repository contract.

## CLI

- `sandbox/cli.py` imports all feature modules, owns every parser, and owns central project/instance routing sets.
- `sandbox/registry.py` stores handler mappings only and silently overwrites duplicate keys.
- Feature handlers and parser semantics therefore have separate owners.

## MCP

- `mcp/wp-server/server.py` manually imports 17 tool groups.
- `mcp/wp-server/app.py` owns server object, project/registry resolution, WordPress helpers, subprocess helpers, and transport-adjacent state.
- Tool registration depends on import side effects; duplicate ownership is not an explicit composition failure.

## Wildcard/back-filled namespace

- 40 shipped files contain wildcard imports from `sandbox.core` or MCP `app` at baseline.
- `sandbox/core/__init__.py` exports and back-fills a broad symbol union into implementation modules.
- New/migrated modules must not increase these counts or consume back-filled symbols.

## Runtime and side effects

- `_instances.py`, `_docker.py`, `_domains.py`, `_provision.py`, and MCP `app.py` mix runtime policy with process/HTTP/port/path/proxy mechanisms.
- Capability rejection is not a single shared preflight across CLI and MCP.

## Hermes ownership

- `sandbox/core/_hermes.py` owns release/state sync, routing profile setup, Drive backup/restore, update backups, repos/worktrees/jobs, gateway, public dashboard/access/tunnel integration, and acceptance checks.
- CLI and MCP public functions are stable compatibility surfaces.
- Scoped recovery would currently modify the same module as unrelated jobs and gateway behavior.

## Approved target boundaries

- Descriptor/schema selection.
- Project registry repository.
- Runtime adapter/capability service.
- Process/HTTP/port/path/proxy services.
- Command and MCP group manifests.
- Hermes state/routing/jobs/gateway/backup/service.
- Compatibility facades with no-new-consumer guards.

No large module is split solely to meet a line-count target.
