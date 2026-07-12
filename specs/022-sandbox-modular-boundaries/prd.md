# PRD: Sandbox Modular Boundaries

**Status**: Reviewed architecture input
**Priority**: Blocking prerequisite
**Blocks**: `021-generic-project-instances` and scoped production recovery
**Reviewed by**: Hermes Sol architecture pass, informed by Luna repository audit
**Date**: 2026-07-13

## Executive decision

Sandbox has useful directory-level grouping but weak ownership and dependency boundaries. A separate behavior-preserving modularization feature must land before generic project runtimes or scoped recovery are implemented.

The feature establishes durable boundaries for project descriptors, registry storage, runtime dispatch, side-effect services, CLI and MCP composition, and Hermes concerns. It does not add generic runtime behavior, a new recovery policy, or destructive restore operations.

## Problem

The current architecture concentrates unrelated responsibilities:

- `sandbox_core.py` combines project discovery, WordPress normalization, registry persistence, locking, and migration.
- instance resolution and lifecycle assume WordPress, database, Mailpit, and WordPress-specific ports.
- CLI parser definitions and routing policy remain centralized while handlers live in feature modules.
- MCP registration relies on import side effects, a broad shared helper module, and a manually maintained bootstrap list.
- `sandbox.core` back-fills symbols across modules, hiding dependency direction.
- Hermes state, routing, jobs, gateway/public access, and backup/recovery behavior share one large implementation module.

Adding Compose runtimes or project-aware recovery directly would spread project-kind and recovery-policy branches through these shared surfaces.

## Goal

A maintainer can add a project schema, runtime adapter, lifecycle capability, CLI command group, MCP tool group, or Hermes recovery operation through explicit contracts and feature-owned modules without expanding central parser/bootstrap lists or wildcard/back-filled namespaces.

All current WordPress, remote, and Hermes behavior remains compatible throughout the migration.

## Required boundaries

### Project descriptors

- Select project kind before kind-specific defaults and validation.
- Treat omitted kind as WordPress.
- Separate common project identity from WordPress plugin identity.
- Keep descriptor parsing side-effect free.
- Preserve existing config sources and precedence behind a compatibility facade.

### Registry

- Own canonical-root-plus-label identity, locking, atomic writes, migrations, and additive field preservation.
- Provide production and in-memory repositories under one contract.
- Keep runtime defaults and lifecycle execution out of storage.
- Preserve current registry formats without eager rewriting.

### Runtime dispatch

- Register adapters explicitly and deterministically.
- Declare capabilities in adapter code.
- Resolve descriptor and registry identity before adapter selection.
- Reject unsupported capabilities before subprocess, HTTP, proxy, filesystem, or registry mutation.
- Preserve WordPress lifecycle through a compatibility adapter.

### Shared side-effect services

- Expose bounded process, HTTP probe, port, path, and proxy interfaces.
- Use argument arrays, explicit working directories, timeouts, normalized results, and secret-safe diagnostics.
- Keep these services free of WordPress and future Compose policy.

### CLI composition

- Feature modules own parser metadata and handlers through one command contract.
- Commands declare scope, capability needs, ownership, and destructive confirmation metadata.
- Composition is deterministic and rejects duplicate names and aliases.
- Unmigrated commands remain behind a named compatibility bridge.
- New features do not add parser blocks or routing sets to `sandbox/cli.py`.

### MCP composition

- Tool groups register through an explicit package-owned manifest.
- Groups declare dependencies and capability/project scope.
- Composition rejects duplicate groups and tools.
- Existing tool names, schemas, and response contracts remain compatible.
- Registration does not depend on incidental import order or project-provided code.

### Dependency direction

- Domain contracts do not import CLI, MCP, or composition roots.
- Production dependencies are built only at composition roots and passed inward.
- New and migrated modules use explicit imports.
- The legacy back-filled namespace is frozen; no new exports or consumers are allowed.

### Hermes boundaries

- State: schemas, validation, atomic persistence, corruption reporting, and backup metadata references.
- Routing: target resolution and policy evaluation without persistence or gateway mutation.
- Jobs: process/worktree lifecycle, status, cancellation, and cleanup through injected services.
- Gateway: public endpoint, tunnel/route, and authorization-related configuration with reversible apply/remove.
- Backup: artifact creation/listing, integrity metadata, retention hooks, restore validation, and non-mutating restore plans.
- Service: composition root preserving current CLI/MCP/public functions.

This feature establishes the backup boundary but does not introduce scoped production profiles, retention policy, deletion, or restore application.

### Compatibility facades

Each facade has an owner, compatibility tests, removal prerequisites, and a prohibition on new consumers. Facades include `sandbox_core.py`, selected `sandbox.core` exports, the current CLI/MCP public surfaces, and Hermes public functions.

## Architecture decisions

1. Modularization blocks Spec 021 and scoped recovery.
2. Behavior is preserved through facades, not duplicate implementations.
3. Project kind is selected before normalization.
4. Registry is an identity store, not a runtime resolver.
5. Capabilities are adapter-owned and dispatcher-enforced.
6. Built-in composition uses explicit deterministic manifests.
7. Plain protocols, immutable dependency bundles, and constructors are sufficient; no DI framework is introduced.
8. Shared primitives expose mechanism, while adapters own policy.
9. Hermes decomposition follows state ownership and side-effect boundaries.
10. Registry and Hermes state are not eagerly rewritten.
11. Compatibility removal requires zero usage, parity evidence, and separate human approval.
12. Existing remote protocol, authorization, endpoint, and secret behavior remains unchanged.

## Migration sequence

1. Capture inventories, fixtures, public contracts, and live baselines.
2. Add contracts, structured results/errors, and dependency bundles without switching production paths.
3. Extract project descriptors and the WordPress schema behind the compatibility facade.
4. Extract registry storage after descriptor ownership no longer depends on persistence policy.
5. Add shared side-effect services, dispatcher, and WordPress adapter.
6. Migrate CLI composition and represent all commands through owned specs or the bridge.
7. Migrate MCP composition and explicit tool-group dependencies.
8. Extract Hermes state, routing, jobs, gateway, backup planning, and service composition in that order.
9. Enforce boundaries, replay live baselines, review security/data-loss risks, and explicitly unblock downstream features.

Every phase has a rollback to its previous facade path and stops on unexplained behavior or state drift.

## Quality gates

- All audited CLI commands and MCP tools are represented exactly once.
- Test-only schema, adapter, command, and tool group register without central edits.
- Duplicate registrations fail closed.
- Unsupported-capability tests record zero side effects.
- Existing config and registry fixtures round-trip without eager migration or field loss.
- New production modules use no wildcard or back-filled dependencies.
- Each Hermes concern is independently testable.
- Full tests, self-test, import-boundary checks, and live WordPress/remote/Hermes regressions pass.
- Architecture, security, and data-loss review approve the handoff.

## Non-goals

- Generic Compose or Astro execution.
- Scoped production backup policy or applied restore.
- New remote, dashboard, CI/E2E, snapshot, database, or mail capabilities.
- Replacement of argparse, FastMCP, Docker Compose, Caddy, or registry file format.
- Dynamic third-party extension loading.
- Repository-wide wildcard removal or file splitting based only on size.
- Removing compatibility facades before all callers migrate.

## Relationship to Spec 021

Spec 021 remains the product specification for generic projects but is implementation-blocked. Its descriptor/schema, registry repository, adapter/dispatcher, shared service, CLI composition, MCP composition, dependency injection, and modularity-guard work moves into this feature. After this feature passes, Spec 021 must be replanned to implement only Compose/Astro behavior through the established contracts.

## Relationship to scoped recovery

Scoped recovery begins only after Hermes backup/recovery ownership is isolated. The later feature will add reusable control-plane and production profiles, consistency-aware data capture, encrypted Drive storage, retention, restore drills, and recurring schedules through Sandbox-owned commands and modules.
