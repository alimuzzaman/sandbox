# Implementation Plan: Generic Project Instances

> **Status (2026-07-14): implementation-blocked.** Feature 022 established the
> descriptor/schema registry, project-registry repository, capability-aware runtime
> service, WordPress adapter, bounded side-effect services, and explicit CLI/MCP
> manifests. Replan this feature against those owners before implementation. Do not
> reintroduce `sandbox_core.py` config/registry logic, central parser edits, MCP
> `app.py` dependencies, or direct proxy/process policy. Unblocking requires the
> feature-022 final live parity/review gates and explicit human approval.

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/021-generic-project-instances/spec.md`

## Summary

Add explicit, local Docker Compose project support to the existing per-project instance model, with Astro as the first guided preset. Preserve every WordPress default and contract, route runtime-specific behavior through adapters, expose only declared capabilities, and use the work to establish modular extension seams without undertaking a repository-wide refactor.

## Technical Context

**Language/Version**: Python 3.10+; JSON/YAML project configuration; Compose YAML generated only for Sandbox-owned overlays and explicit presets

**Primary Dependencies**: Existing Docker Compose v2 integration, per-project registry in `sandbox_core.py`, argparse CLI, FastMCP, Caddy clean-URL proxy, existing HTTP/browser probes

**Storage**: Committed `sandbox.config.*` and optional preset Compose file; additive registry metadata in `$SANDBOX_HOME/runtime/registry.json`; generated adapter state under `$SANDBOX_HOME/runtime/projects/<instance>/`

**Testing**: Existing stdlib `unittest`; contract fixtures for config/registry/CLI/MCP; live local Compose fixture and live WordPress regression smoke

**Target Platform**: Existing Docker-supported local platforms (macOS, Linux, Windows through WSL2); Herd and generic remote hosting deferred

**Project Type**: Python CLI plus MCP server controlling local development runtimes

**Performance Goals**: Warm `ensure` and status resolution complete within 5 seconds before application probing; cold boot reaches the declared health endpoint within a configurable 120-second default; no additional startup work for legacy WordPress projects beyond one project-kind dispatch

**Constraints**: Backward-compatible WordPress schema and registry reads; no automatic execution of discovered manifests; no source/volume deletion by generic cleanup; one-worker side-project delivery; new feature logic stays outside the shared back-filled namespace

**Scale/Scope**: One or more labelled instances per project root; MVP validates one public Compose service plus dependency services; 67 CLI commands and 51 MCP tools require ownership classification, not generic parity

## Constitution Check

| Principle | Status | Plan evidence |
|---|---|---|
| I. Per-project only | Pass | Generic instances require an explicit descriptor and use the canonical project root plus label. No implicit fallback instance is introduced. |
| II. Registry source of truth | Pass | The registry gains additive `kind`, `adapter`, `http_port`, and artifact metadata; resolution precedence remains unchanged. |
| III. Single entry, modular package | Pass with bounded legacy debt | New runtime logic lives in `sandbox/runtimes/`; touched command parser registration becomes feature-owned. Existing wildcard/back-fill and unrelated monoliths are recorded in [modularity-audit.md](modularity-audit.md), not expanded. |
| IV. Live-stack proof | Pass with required gate | Completion requires both an Astro/generic Compose live scenario and an unchanged WordPress live smoke scenario. |
| V. Idempotency and docs | Pass | Repeated lifecycle checks, config reference updates, MCP guidance, and README changes are explicit tasks. |
| VI. Parity before removal | Pass | The WordPress adapter initially delegates to existing behavior; no old field or path is removed in this feature. |

Post-design re-check: the adapter contract, additive data model, explicit capability errors, and incremental tasks preserve all six principles. No constitutional exception is required for new code. Pre-existing modularity debt remains a tracked residual risk.

## Project Structure

### Responsibilities moved by feature 022

| Original 021 responsibility | Current owner to target during replan |
|---|---|
| kind-before-default config | `sandbox/config/` descriptors, registry, and schemas |
| registry locking/migration | `sandbox/project_registry/` and public facade |
| capability dispatch | `sandbox/application/runtime_service.py` + `sandbox/runtimes/` |
| process/HTTP/port/path/proxy mechanisms | `sandbox/services/` |
| command ownership | `sandbox/commands/manifest.py` + `CommandSpec` |
| MCP group ownership | `mcp/wp-server/tools/manifest.py` + `ToolGroupSpec` |

The generic Compose adapter and Astro preset are now implemented incrementally;
remaining work is lifecycle/MCP parity, capability preflight coverage, and the
full fixture/evidence matrix.

### Documentation (this feature)

```text
specs/021-generic-project-instances/
├── spec.md
├── plan.md
├── research.md
├── modularity-audit.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── adapter-protocol.md
│   ├── cli-mcp.md
│   └── project-config.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox_core.py                       # compatibility facade: project config/registry contract
sandbox/
├── cli.py                            # legacy parser host; touched parsers migrate out
├── registry.py                       # command-spec registration
├── commands/
│   ├── instances_cmd.py              # init/ensure/instance parser + handlers
│   └── lifecycle.py                  # shared lifecycle parser + handlers
└── runtimes/
    ├── __init__.py                   # explicit adapter registry
    ├── base.py                       # protocol, result/error, capabilities
    ├── wordpress.py                  # compatibility adapter over current core
    ├── compose.py                    # generic Compose adapter
    └── presets/
        ├── __init__.py
        └── astro.py                  # initialization-only preset

mcp/wp-server/
├── app.py                            # existing shared server helpers; no new runtime logic
├── server.py                         # tool-group loading only
└── tools/
    ├── instances.py                  # kind-neutral lifecycle wrappers
    └── runtime.py                    # generic status/log/exec capabilities

tests/
├── fixtures/generic-compose/
├── fixtures/astro/
├── test_project_config.py
├── test_runtime_adapters.py
├── test_generic_compose.py
├── test_cli.py
└── test_mcp.py
```

**Structure Decision**: Add one explicit runtime package and preserve current WordPress internals behind a compatibility adapter. Only touched parser definitions move beside their handlers; unrelated command and core modules remain unchanged. `sandbox_core.py` retains its public import surface during this feature, but new generic runtime behavior is not added to its global namespace beyond the minimal additive project descriptor and registry fields.

## Architecture Decisions

### AD-001 — Explicit project kind, WordPress by default

Add `kind: "compose"` for generic projects. Common aliases (`generic`, `docker`,
`php`, `node`, `javascript`, `laravel`, `laravel-sail`, and `astro`) normalize to
the same adapter. Missing `kind` remains `wordpress`, including `.wp-env.json`
imports. Configuration loading determines kind before applying defaults so
generic projects never inherit plugin, PHP, database, or mail settings.

### AD-002 — Compose is the generic MVP contract

The project declares a Compose file, one public service, an internal HTTP port, and a health path. Sandbox invokes the project Compose file with a generated overlay that adds only Sandbox-owned routing metadata and a host port. Project services, images, volumes, and secrets remain project-owned.

### AD-003 — Separate identity from display and WordPress slug

The canonical project root plus label remains the registry identity. A display name may contain dots. A collision-safe runtime ID is normalized for Docker/domain use and suffixed deterministically when normalization collides. WordPress plugin slug validation remains inside the WordPress descriptor only.

### AD-004 — Capability-based dispatch

Adapters expose a fixed capability set. Shared CLI/MCP operations dispatch through the adapter; WordPress tools require `wordpress.*` capabilities and fail before subprocess or network work. Capabilities are derived from the adapter implementation, while `kind` and adapter version are persisted for diagnostics.

### AD-005 — Additive registry migration

Add `kind`, `adapter`, `http_port`, `display_name`, and `artifact_dir`. Existing records are read as WordPress and continue using `wordpress_port`; shared URL resolution reads `http_port` first and falls back to `wordpress_port`. Do not rewrite the full registry eagerly.

### AD-006 — Astro is an explicit preset

`sb init --type astro` inspects package metadata, proposes values, and writes `sandbox.config.json` plus a reviewable `sandbox.compose.yml` when the repository does not already supply one. It never creates a separate Astro adapter and never runs package scripts during detection.

### AD-007 — Touch-driven modularity improvement

Extend command registration to accept parser configuration beside handlers and migrate only instance/lifecycle commands changed by this feature. Keep existing commands on the legacy parser path. New adapters use explicit imports and dependency objects; they do not join `sandbox.core` back-fill. MCP tool loading is centralized behind one package loader so `server.py` no longer grows one import per group.

### AD-008 — Safe generic deletion semantics

Generic destroy stops/removes the adapter's Compose project and Sandbox-generated artifacts/registry entry but does not pass volume-removal flags. A future purge operation requires a separate specification and confirmation contract.

## Incremental Delivery Strategy

1. **Foundation**: land the project-kind schema, additive registry fields, adapter protocol, WordPress compatibility adapter, and contract tests. Stop if legacy WordPress config or resolution changes.
2. **MVP (US1)**: implement explicit Compose ensure/status and identifier handling with a minimal fixture. Live-verify repeated boot and cleanup before proceeding.
3. **Operations (US2)**: add shared lifecycle, logs, scoped exec, capability errors, proxy/HTTPS integration, and MCP contracts. Re-run WordPress smoke.
4. **Astro convenience (US3)**: generate explicit preset configuration and validate the real site. This increment may be deferred without weakening the MVP.
5. **Extension seam (US4)**: finish only the parser/tool loading changes needed for adapters and document remaining modularity debt. Do not decompose Hermes or unrelated large modules.

Each increment has a stop-and-review checkpoint. No commit, push, release, or deployment is part of this plan without separate approval.

## Verification Plan

- Run focused configuration, registry, adapter, CLI, and MCP unit/contract tests after each increment.
- Run the quickstart against minimal Compose and Astro fixtures; repeat ensure/status/apply/stop/start three times and inspect registry, generated artifacts, containers, and URL.
- Exercise representative WordPress-only MCP tools against the generic fixture and prove they return capability errors before subprocess execution.
- Exercise the existing live WordPress instance through ensure, status, WP-CLI, REST, clean URL, and test harness after shared dispatch changes.
- Run the complete stdlib test suite, `./sb selftest`, `git diff --check`, and a file/import-boundary check before handoff.
- Obtain fresh human review before implementation is considered complete because the registry and lifecycle paths affect every project.

## Complexity Tracking

No new constitutional violation is planned. Existing wildcard imports, back-filled symbols, central parser ownership, and unrelated feature monoliths are documented debt; this feature must not expand them.
