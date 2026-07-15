# Feature Specification: Generic Project Instances

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "Plan and specify Docker-backed Sandbox instances outside WordPress, using an Astro site as the immediate case, while checking the modularity of the whole Sandbox project and treating the work as an incremental side project rather than a dedicated rewrite."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Boot an Explicit Generic Project (Priority: P1)

A developer with a non-WordPress repository and an explicit Sandbox project configuration can run `sb ensure` and receive a reachable, project-owned local instance without pretending the project is a WordPress plugin.

**Why this priority**: This directly fixes the reported failure and establishes the smallest useful non-WordPress capability. Explicit opt-in keeps repository code and configuration untrusted until the developer deliberately authorizes local execution.

**Independent Test**: Configure a minimal Compose-based web project whose directory name contains a dot, run `sb ensure` twice, and confirm both calls resolve to the same healthy instance and URL without creating any WordPress services.

**Acceptance Scenarios**:

1. **Given** a generic project with an explicit project kind, Compose file, web service, internal port, and health check, **When** the developer runs `sb ensure`, **Then** Sandbox starts the declared project, registers one project-owned instance, and returns its reachable URL.
2. **Given** the repository directory is named `alimuzzaman.me`, **When** Sandbox derives an instance identifier, **Then** it preserves the display name, creates a safe unique runtime identifier, and does not reject the project for failing WordPress plugin-slug rules.
3. **Given** an already healthy generic instance, **When** the developer runs `sb ensure` again, **Then** the operation is idempotent and reuses the existing instance.
4. **Given** an unconfigured generic repository, **When** the developer runs `sb ensure`, **Then** Sandbox fails with guidance to initialize or configure a generic project and does not execute a discovered Dockerfile or Compose file implicitly.

---

### User Story 2 - Operate Generic Instances Safely (Priority: P2)

A developer or coding agent can inspect and operate a generic instance through shared lifecycle, URL, log, and scoped execution capabilities while WordPress-only operations remain clearly unavailable.

**Why this priority**: Starting a container is insufficient unless the developer can diagnose, stop, reopen, and reconcile it through the same per-project mental model used elsewhere in Sandbox.

**Independent Test**: Against a running generic fixture, exercise status, logs, open/fetch, scoped command execution, stop/start, apply, and destroy behavior through the supported CLI and MCP surfaces; verify WordPress-only tools fail with a capability-specific message and do not mutate the instance.

**Acceptance Scenarios**:

1. **Given** a healthy generic instance, **When** a supported shared lifecycle or diagnostic operation is requested, **Then** it targets the instance resolved from the canonical project root and returns structured project-kind and capability information.
2. **Given** a generic instance, **When** a WordPress-only operation is requested, **Then** Sandbox reports that the project lacks the WordPress capability and identifies an applicable generic operation when one exists.
3. **Given** a changed generic project configuration, **When** the developer applies it, **Then** Sandbox reconciles Sandbox-owned runtime state without deleting project-owned persistent data.
4. **Given** a generic instance is destroyed, **When** cleanup completes, **Then** Sandbox removes its generated runtime state and registry entry but preserves source files and project-owned persistent volumes unless a separately confirmed destructive action is introduced later.

---

### User Story 3 - Initialize an Astro Project (Priority: P2)

An Astro developer can use guided initialization to create explicit generic-project configuration with conventional development defaults, review it, and then boot the site through `sb ensure`.

**Why this priority**: Astro is the immediate real-world case, but it should validate the generic model rather than introduce a framework-specific runtime silo.

**Independent Test**: Start from a representative Astro repository with no Sandbox configuration, run the Astro initialization path, review the generated configuration, run `sb ensure`, and confirm the site is reachable with file changes reflected according to the selected development command.

**Acceptance Scenarios**:

1. **Given** a recognizable Astro repository, **When** the developer explicitly chooses Astro initialization, **Then** Sandbox proposes conventional command, port, mount, and health-check values and writes reviewable project configuration.
2. **Given** the repository uses non-default scripts or package tooling, **When** conventional values cannot be selected safely, **Then** initialization requests or reports the missing value rather than silently guessing an executable command.
3. **Given** an initialized Astro project, **When** the developer runs `sb ensure`, **Then** it follows the same generic instance lifecycle as any other supported web project.

---

### User Story 4 - Extend Runtimes Without Growing Central Monoliths (Priority: P3)

A Sandbox maintainer can add or evolve a runtime adapter and its CLI/MCP capabilities through a bounded module with explicit dependencies, without editing unrelated feature dispatch or relying on a process-wide back-filled namespace.

**Why this priority**: The current project has useful feature modules but central parser registration, wildcard imports, and globally back-filled core symbols weaken isolation. The generic runtime will otherwise multiply those costs.

**Independent Test**: Add a test-only runtime adapter and confirm it can register configuration validation, lifecycle capabilities, and command/tool exposure through documented extension points while existing WordPress and Hermes tests remain unchanged and passing.

**Acceptance Scenarios**:

1. **Given** a new runtime adapter module, **When** it is registered, **Then** its project-kind validation and capabilities are discoverable without adding runtime-specific conditionals throughout unrelated modules.
2. **Given** a feature module outside the touched generic-instance path, **When** this work is delivered, **Then** it is not refactored solely to satisfy a repository-wide cleanup goal.
3. **Given** a touched module that currently depends on wildcard or back-filled symbols, **When** changing that dependency is low-risk and covered by tests, **Then** the change replaces it with explicit imports; otherwise the debt is recorded without blocking the generic-instance increment.

### Edge Cases

- Two project roots normalize to the same runtime-safe identifier; identity must remain unique through the canonical root and registry rather than slug alone.
- The declared Compose file or web service does not exist, contains an invalid port, exits early, or never passes its health check.
- The project already runs outside Sandbox and its requested host port conflicts with another process.
- The application binds only to container loopback and is unreachable from the Sandbox proxy.
- The configuration changes project kind after an instance already exists; automatic cross-kind conversion must be rejected with migration guidance.
- A project-defined Compose stack includes multiple services, profiles, dependencies, secrets, or persistent volumes; Sandbox must operate only the declared public service while preserving project ownership.
- A repository path is moved, symlinked, or opened from a worktree; canonical-root identity and existing relocation rules must remain deterministic.
- Docker is unavailable or the current platform lacks a supported local container runtime.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST represent project kind explicitly and MUST treat existing configurations without a kind as WordPress for backward compatibility.
- **FR-002**: Sandbox MUST support an explicitly configured generic Compose web-project kind with a declared Compose file, public service, internal port, and health probe.
- **FR-003**: `sb ensure` and `ensure_instance` MUST resolve both WordPress and generic projects through the same canonical project-root registry without an implicit global or fallback instance.
- **FR-004**: Sandbox MUST separate project display names from runtime-safe identifiers so names containing dots or other non-WordPress characters can be represented without weakening container, domain, or registry validation.
- **FR-005**: Generic project execution MUST require explicit Sandbox configuration or an explicit initialization action; Sandbox MUST NOT execute merely discovered repository manifests as an automatic fallback.
- **FR-006**: Sandbox MUST expose project kind, adapter identity, capabilities, health state, service, and URL in instance status returned to CLI and MCP consumers.
- **FR-007**: Generic adapters MUST support idempotent ensure, status, start, stop, logs, scoped execution, URL probing/opening, configuration reconciliation, and non-destructive cleanup behavior.
- **FR-008**: WordPress-only commands and MCP tools MUST reject generic instances before execution with a structured capability error rather than failing on missing WordPress containers, files, credentials, or endpoints.
- **FR-009**: Generic cleanup MUST preserve source files and project-owned persistent data by default; any future destructive volume purge MUST be separately named and explicitly confirmed.
- **FR-010**: Existing WordPress project configuration, instance naming, CLI behavior, MCP contracts, remote workflows, and live-stack behavior MUST remain backward compatible.
- **FR-011**: Guided Astro initialization MUST write explicit, reviewable generic-project configuration and MUST use Astro only as a preset over the generic adapter.
- **FR-012**: Astro initialization MUST validate or obtain the package command, package manager, internal port, bind behavior, and health path before the first boot.
- **FR-013**: Runtime-specific validation, lifecycle operations, and capabilities MUST be behind an adapter boundary selected by project kind.
- **FR-014**: Feature registration MUST allow touched CLI parser definitions, handlers, and MCP exposure to live with or beside their feature module rather than expanding the central CLI or MCP bootstrap.
- **FR-015**: The implementation MUST inventory every current CLI command group and MCP tool group, classify it as shared, WordPress-only, infrastructure-only, or runtime-neutral candidate, and use that inventory to prevent accidental generic exposure.
- **FR-016**: Modularity cleanup MUST be opportunistic and bounded to modules required by this feature; unrelated large-module decomposition is explicitly deferred.
- **FR-017**: Generated runtime artifacts MUST remain under `$SANDBOX_HOME`; repository configuration may be committed, while secrets and machine-local overrides MUST retain existing protected storage rules.
- **FR-018**: Sandbox MUST treat project manifests and configuration as untrusted input, validate referenced paths and service names, constrain generated paths to Sandbox-owned locations, and disclose the local-code execution boundary before first initialization or boot.
- **FR-019**: The feature MUST include unit, contract, and live validation for an Astro fixture, a minimal generic Compose fixture, legacy WordPress behavior, identifier collision handling, capability errors, and repeated lifecycle operations.
- **FR-020**: Generic local instances MUST support the existing clean-URL and explicit HTTPS flow when their declared application is reachable, without requiring WordPress URL rewriting or WordPress credentials.

### Key Entities

- **Project Descriptor**: The committed declaration of project kind and adapter-specific settings associated with a canonical project root.
- **Runtime Adapter**: A bounded capability provider that validates a project descriptor and implements supported lifecycle operations for a project kind.
- **Instance Record**: The registry-backed identity and current runtime metadata for one project root and optional label, including kind, adapter, safe identifier, health, ports, and URL.
- **Capability Set**: The operations an adapter supports, used by CLI and MCP dispatch to allow shared behavior and reject incompatible tools early.
- **Astro Preset**: An initialization-only source of conventional descriptor values; it is not a separate instance type.
- **Feature Surface Inventory**: The reviewed mapping of existing CLI and MCP groups to shared, WordPress-only, infrastructure-only, or potential generic capabilities.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer can initialize and boot the representative Astro site to a reachable clean URL in no more than two explicit commands after reviewing generated configuration.
- **SC-002**: Repeating ensure, stop/start, apply, and status operations three times each produces one registry identity, no orphaned Sandbox containers, and the same reachable project URL.
- **SC-003**: All existing WordPress automated checks pass unchanged, and a live WordPress smoke scenario produces the same externally visible lifecycle and MCP results as before the feature.
- **SC-004**: Every registered CLI command group and MCP tool group appears exactly once in the feature surface inventory with an ownership classification and generic-support decision.
- **SC-005**: No new runtime-specific branch is added to more than the adapter selection layer and its dedicated adapter module; exceptions are documented and justified in the implementation plan.
- **SC-006**: Generic instances reject 100% of sampled WordPress-only operations with a capability-specific error before any WordPress subprocess, REST, database, or filesystem action begins.
- **SC-007**: Destroying a generic instance in validation leaves project source and project-owned persistent volumes intact while removing all Sandbox-owned runtime records and generated files for that instance.
- **SC-008**: The first independently deliverable increment is limited to explicit local Compose ensure/status behavior; broader lifecycle operations, Astro convenience, and modularity refinements can land later without redesigning the registry contract.

## Assumptions

- The MVP supports local Docker Compose projects; generic Herd, remote hosting/deploy, CI matrices, snapshot semantics, databases, mail capture, and framework presets other than Astro are out of scope.
- Existing WordPress configuration remains the default when the project kind is omitted; migration is opt-in and no current plugin repository must change.
- A generic project owns its application images, Compose services, dependency services, and named volumes. Sandbox owns only its registry record, generated overlay/runtime files, allocated host route, and proxy integration.
- Initialization may inspect common Astro/package metadata but does not execute repository commands until the developer explicitly confirms or runs the resulting boot operation.
- The work is maintained as small, independently verifiable increments. Modularity debt outside files touched by the generic adapter is recorded for future work rather than repaired in this feature.
- One worker is the default execution model for this side project; parallel work is optional only for independent tests or review and does not justify overlapping file ownership.
- A remote generic-service deployment requires a committed service build contract (Dockerfile, Compose service definition, health endpoint, storage mount, and route configuration) before Sandbox may create DNS or proxy routes. Discovery of replay application source alone is insufficient to deploy it.

## Out of Scope

- Becoming a general-purpose replacement for Dev Containers, Docker Compose, or cloud application platforms.
- Automatically running arbitrary Dockerfiles, Compose files, or package scripts discovered in an unconfigured repository.
- Converting existing WordPress instances into generic instances in place.
- Generic production deployment, public hosting, remote previews, databases, mail, snapshots, multisite, Query Monitor, WP-CLI, WordPress REST, or plugin management.
- A dedicated repository-wide modularity rewrite, file-size campaign, or forced decomposition of Hermes and other unrelated features.
