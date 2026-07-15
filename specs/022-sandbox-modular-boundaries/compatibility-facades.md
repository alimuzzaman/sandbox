# Compatibility Facade Ledger

This ledger records feature-022 compatibility paths. They are migration controls,
not extension points: **new consumers must target the bounded service or explicit
registration contract, never these facades.** Removal requires a separately
approved change after repository usage is zero and the listed parity tests pass.

| Facade | Owner | Current consumers / purpose | Rollback | Compatibility evidence | Removal gate |
|---|---|---|---|---|---|
| `sandbox_core.py` config and registry functions | Config / project-registry boundary | Existing CLI, core and MCP callers retain legacy public imports while descriptors and repository implementations own new behavior. | Repoint the facade to retained legacy implementation; registry format and location stay unchanged. | `tests/test_config_facade.py`, `tests/test_registry_repository.py`, `tests/test_sandbox.py` | No in-repository consumers, full config/registry fixture parity, explicit human approval. |
| `sandbox.registry.COMMANDS` | CLI boundary | Existing CLI dispatch remains compatible while `CommandSpec` supplies owner/scope/capability metadata. | Restore legacy parser composition while retaining handlers. | `tests/test_command_composition.py`, `tests/test_cli.py` | Every command has a feature spec or named bridge, inventory parity, no external consumers, approval. |
| `sandbox.core._hermes` via `sandbox.hermes.facade` | Hermes boundary | Existing Hermes CLI functions retain their public import path while state, routing, jobs, gateway and backup planning are independently testable. | Delegate the public facade directly to the retained legacy module. | `tests/test_hermes_state.py`, `tests/test_hermes_routing.py`, `tests/test_hermes_backup.py`, legacy `tests/test_hermes.py` | All public functions delegate through a composed service, remote parity evidence, no new consumers, approval. |
| `mcp/wp-server/app.py` helper namespace | MCP boundary | Existing tool decorators and transport bootstrap remain stable while the tool-group manifest controls deterministic group ownership. | Restore the prior server import list. | `tests/test_mcp_composition.py`, existing MCP subprocess checks | All groups accept explicit dependencies without app-global imports, schema snapshots pass, no new wildcard consumers, approval. |

## Current constraints

### Final consumer audit (2026-07-14)

The enforced production consumer baseline is:

- `sandbox_core.py`: `sandbox/application/context.py`, `sandbox/core/_instances.py`,
  and `mcp/wp-server/app.py`;
- `sandbox.hermes.facade`: `sandbox/commands/hermes.py`;
- `sandbox.registry.COMMANDS`: `sandbox/cli.py` plus the manifest's coverage
  validator;
- MCP `app.py`: the 16 tool-group compatibility wrappers explicitly identified by
  `BUILTIN_TOOL_GROUPS`; `instances` and `hermes` use injected dependencies.

`tests/test_architecture_boundaries.py` fails if the first two consumer sets grow,
and the command/MCP composition suites enforce exact owned inventories (68 commands,
18 groups, 75 tools). Deferred removals remain blocked: the compatibility groups are
not all dependency-injected, live remote/Hermes parity is unavailable, and no removal
has human approval.

- Feature 022 does not authorize facade deletion, restore application, backup
  deletion, state overwrite, or enabling a public route.
- A compatibility failure or externally visible drift blocks downstream feature
  work and requires returning to the listed rollback path.
- Spec 021 and scoped recovery remain blocked until the full automated, live,
  architecture, correctness, and security/data-loss gates are approved.
