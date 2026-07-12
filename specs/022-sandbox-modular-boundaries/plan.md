# Implementation Plan: Sandbox Modular Boundaries

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-sandbox-modular-boundaries/spec.md`

## Summary

Establish behavior-preserving module boundaries before generic runtimes and scoped recovery. The implementation extracts descriptor/schema resolution, registry persistence, capability-aware runtime dispatch, side-effect services, CLI composition, MCP composition, and Hermes concerns behind explicit contracts and compatibility facades. Migration is phase-gated: characterize current behavior, add contracts, move one ownership boundary at a time, replay focused and live baselines, and retain a rollback path until parity is proven.

## Technical Context

**Language/Version**: Python 3.11+ as currently supported by Sandbox

**Primary Dependencies**: Python standard library (`argparse`, `dataclasses`, `pathlib`, `typing`, `subprocess`, `fcntl`), PyYAML, MCP Python SDK/FastMCP v1 compatibility, existing Docker Compose and Caddy command integrations

**Storage**: JSON registry under `$SANDBOX_HOME/runtime/registry.json`, YAML machine configuration, Hermes JSON/YAML/SQLite state already owned by the installed agent, filesystem artifacts under `$SANDBOX_HOME`

**Testing**: Existing `unittest` suite, subprocess CLI tests, MCP registration/contract tests, focused fake-based contract tests, `./sb selftest`, live WordPress/remote/Hermes smoke checks

**Target Platform**: macOS and Linux development machines plus the configured Linux remote Sandbox/Hermes host

**Project Type**: Python CLI and MCP server with local/remote runtime orchestration

**Performance Goals**: No material regression in warm command dispatch, registry lookup, CLI startup, MCP startup, or WordPress ensure/status; deterministic composers complete within the current startup budget

**Constraints**: Preserve the single polyglot `sb` entry file; no eager state rewrite; no new wildcard/back-filled dependencies; all destructive/state-sensitive checks require rollback evidence; no generic runtime or scoped recovery behavior in this feature

**Scale/Scope**: Current 67-command and 51-tool audited surfaces, all supported registry/config variants, current WordPress runtime, current remote hosting/Hermes behavior, and five bounded Hermes concerns

## Constitution Check

*GATE: Passed before Phase 0 research; re-checked after Phase 1 design.*

- **Per-project model**: PASS. Descriptor and registry contracts retain canonical project-root ownership and do not introduce a global fallback.
- **Registry source of truth**: PASS. The file remains authoritative; extraction changes ownership, not precedence or location.
- **Single entry/modular package**: PASS. `sb` remains unchanged as the entry file. Feature logic moves into importable package modules and registered feature specifications.
- **Live-stack verification**: PASS. Every stateful migration phase includes focused tests plus live WordPress/remote/Hermes replay.
- **Idempotency/docs-with-code**: PASS. Atomic storage and reversible facades are explicit requirements; README, AGENTS, architecture, and evidence updates are tasks in the same feature.
- **Feature parity before removal**: PASS. Facades remain until usage is zero and parity plus human approval supports removal.
- **Secrets and state safety**: PASS. Side-effect contracts require redaction, bounded environments, path policy, snapshots/fixtures, and no eager rewrite.
- **Spec-Kit workflow**: PASS. This feature was specified, clarified, planned, tasked, and analyzed before implementation.

Post-design re-check: PASS. Contracts preserve existing public surfaces, keep storage formats compatible, and provide phase rollback without introducing a competing implementation.

## Architecture

```text
CLI feature modules ──> CommandSpec manifest ──> CLI composer ─┐
MCP tool groups ──────> ToolGroupSpec manifest ─> MCP composer ├─> application services
                                                               │
                         DescriptorService ─> SchemaRegistry ──┤
                         RegistryRepository ───────────────────┤
                         RuntimeService ─────> AdapterRegistry ┤
                                                               │
                              Process / HTTP / Port / Path / Proxy services
                                                               │
                         HermesService ─────────────────────────┤
                           ├─ state
                           ├─ routing
                           ├─ jobs
                           ├─ gateway
                           └─ backup planning

Legacy public facades ─────────────────────────────────────────> application services
```

### Dependency rules

1. Domain contracts never import CLI, MCP, command modules, or composition roots.
2. Storage implementations implement repository contracts and do not resolve runtime policy.
3. Adapters receive service dependencies; they do not instantiate subprocess, proxy, or registry implementations.
4. Runtime-kind branching occurs only at schema and adapter selection, with reviewed compatibility exceptions tracked in the facade ledger.
5. CLI and MCP are transport/composition layers and use the same application services.
6. Hermes bounded modules communicate through contracts or the composition service, not each other's implementation internals.
7. Compatibility facades depend inward on services; new services never import facades.
8. The existing `sandbox.core` back-fill remains for untouched callers but is frozen.

## Architecture Decisions

### AD-001 — Separate blocking feature

Specs 021 and scoped recovery may prepare research and fixtures but may not integrate production behavior until this feature's exit gate passes.

### AD-002 — Protocols and plain dependency bundles

Use small structural contracts and immutable dependency containers. Do not introduce a dependency-injection framework. Python protocols permit test doubles without forcing inheritance, matching the project's incremental compatibility needs.

### AD-003 — Explicit built-in manifests

Schemas, adapters, commands, and MCP groups are composed from explicit package-owned manifests. Filesystem discovery and project-provided Python loading are excluded because trust, ordering, and version negotiation are not defined.

### AD-004 — Compatibility facades as rollback controls

Existing entry points delegate to migrated services one boundary at a time. A facade cannot gain new consumers and is removed only under a later approved gate.

### AD-005 — Preserve storage formats

The registry and Hermes state keep compatible formats. File-backed repositories write a sibling temporary file, flush it, and atomically replace the destination on the same filesystem. Unknown compatible fields round-trip; unsupported future versions fail closed.

### AD-006 — Runtime service owns capability preflight

CLI and MCP do not each implement runtime-kind checks. They request operations through one service that selects the adapter, validates capability, and only then invokes side effects.

### AD-007 — Mechanism-only shared services

Process, HTTP, port, path, and proxy modules expose bounded mechanisms. WordPress policy remains in the WordPress adapter/legacy implementation; future Compose policy belongs to Spec 021.

### AD-008 — Deterministic CLI and MCP composition

Command/tool ownership, aliases, dependencies, and ordering are data in contracts. Duplicate ownership fails startup/tests. Existing public invocation/schema contracts are characterized before migration.

### AD-009 — Hermes decomposition follows side effects

Extract state first, then routing, jobs, gateway, and backup planning. Keep the public service/facade stable and do not introduce recovery scope, retention, deletion, or applied restore.

### AD-010 — Structural guards, not cosmetic metrics

Automated guards enforce dependency direction, explicit registration, no-new-back-fill, approved kind-branch locations, and no direct registry access. File length and global wildcard counts are informational, not completion targets.

## Migration Phases

### Phase 0 — Baseline and inventory

Capture command/tool schemas, config and registry fixtures, import/coupling inventory, live WordPress behavior, and representative remote/Hermes behavior. Add boundary tests that expose current coupling without changing production behavior.

**Rollback**: Documentation/tests only.

### Phase 1 — Contracts and composition skeletons

Add descriptor/schema, registry, runtime/capability, side-effect, command, tool-group, and Hermes contracts plus fake implementations. Production paths remain unchanged.

**Rollback**: Remove unused contracts.

### Phase 2 — Descriptor and WordPress schema

Separate common discovery/identity from WordPress normalization and select kind before defaults. Route the legacy loader through the descriptor service while leaving registry persistence unchanged.

**Rollback**: Restore legacy loader delegation.

### Phase 3 — Registry repository

Extract JSON locking/atomic persistence and the in-memory repository after descriptor ownership is separated; route legacy registry functions through the facade.

**Rollback**: Point the facade back to the legacy implementation; format is unchanged.

### Phase 4 — Shared services and WordPress adapter

Introduce process/HTTP/port/path/proxy services, adapter registry, runtime service, capability errors, and the WordPress compatibility adapter.

**Rollback**: Transport facades call legacy WordPress handlers directly.

### Phase 5 — CLI composition

Introduce command specifications and deterministic composition. Migrate instance/lifecycle/config groups first; represent all remaining commands through owned specs or explicit bridge entries.

**Rollback**: Restore the legacy CLI composer while retaining handlers.

### Phase 6 — MCP composition

Introduce tool-group specifications, explicit dependencies, package-owned manifest, duplicate detection, and compatibility wrappers. Migrate shared/runtime and Hermes groups first, then inventory every group.

**Rollback**: Restore the prior bootstrap imports.

### Phase 7 — Hermes bounded modules

Extract state, routing, jobs, gateway/public access, backup planning, and the composition service sequentially. Run focused and remote regression gates after each extraction.

**Rollback**: Per-concern facade delegates to the corresponding legacy `_hermes.py` path; state formats remain compatible.

### Phase 8 — Enforcement and downstream handoff

Enable boundary guards, complete facade ledger/docs, run the full test/live matrix, obtain architecture/security/data-loss review, and replan Spec 021.

**Rollback**: A guard may receive a reviewed compatibility exception without rolling back production behavior.

## Project Structure

### Documentation

```text
specs/022-sandbox-modular-boundaries/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── command-spec.md
│   ├── hermes-services.md
│   ├── registry-repository.md
│   ├── runtime-service.md
│   └── tool-group-spec.md
├── checklists/requirements.md
├── tasks.md
└── implementation-evidence.md
```

### Source Code

```text
sandbox/
├── application/
│   ├── context.py
│   └── runtime_service.py
├── config/
│   ├── descriptors.py
│   ├── facade.py
│   ├── registry.py
│   └── wordpress.py
├── project_registry/
│   ├── base.py
│   ├── json.py
│   └── memory.py
├── runtimes/
│   ├── base.py
│   ├── registry.py
│   └── wordpress.py
├── services/
│   ├── http.py
│   ├── paths.py
│   ├── ports.py
│   ├── process.py
│   └── proxy.py
├── commands/
│   ├── manifest.py
│   └── existing feature modules
├── hermes/
│   ├── backup.py
│   ├── facade.py
│   ├── gateway.py
│   ├── jobs.py
│   ├── routing.py
│   ├── service.py
│   └── state.py
├── cli.py
└── registry.py              # command-spec registry compatibility module

mcp/wp-server/
├── composition.py
├── dependencies.py
├── server.py
└── tools/
    ├── manifest.py
    └── existing tool groups

tests/
├── test_architecture_boundaries.py
├── test_command_composition.py
├── test_config_descriptors.py
├── test_hermes_*.py
├── test_mcp_composition.py
├── test_registry_repository.py
├── test_runtime_service.py
└── existing compatibility/live suites
```

**Structure Decision**: Add cohesive packages alongside existing modules and preserve `sandbox_core.py`, `sandbox/core/`, CLI, MCP, and Hermes public facades during migration. No package is introduced solely to reduce line count; each owns a contract or side-effect boundary required by downstream features.

## Verification Plan

- Run contract suites against fake and production implementations.
- Replay registry/config fixtures after each storage/config phase.
- Compare the exact command inventory, representative help/error/exit behavior, MCP tool names and schemas after composition phases.
- Assert unsupported capability requests produce no recorded side effects.
- Run focused WordPress tests and live ensure/status/WP-CLI/REST/domain/HTTPS/lifecycle checks after runtime and transport phases.
- Run focused Hermes tests plus remote status/job/gateway/public-access/backup checks after every Hermes extraction.
- Run `python3 -m unittest discover -s tests -v`, `./sb selftest`, `git diff --check`, boundary guards, and the full quickstart before handoff.
- Obtain independent correctness/regression and security/data-loss review before unblocking downstream features.

## Complexity Tracking

No constitution violation is planned. Temporary compatibility facades and a legacy command bridge add short-term structure, but they are required rollback controls under the constitution's parity-before-removal rule. Each has an owner, no-new-consumer guard, and removal prerequisite.
