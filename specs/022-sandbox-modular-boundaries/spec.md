# Feature Specification: Sandbox Modular Boundaries

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "Modularize Sandbox first as a critical prerequisite, creating durable reusable module boundaries before generic projects and scoped recovery while preserving all current behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Add a Project Schema Independently (Priority: P1)

A Sandbox maintainer can add and test a project-kind schema through an explicit registration contract without changing WordPress normalization, registry persistence, CLI composition, or MCP composition.

**Why this priority**: Selecting project kind before kind-specific defaults is the foundational boundary needed by every future runtime.

**Independent Test**: Register a test-only schema, resolve a descriptor through it, and prove duplicate registration fails while an existing project with no kind still resolves exactly as WordPress.

**Acceptance Scenarios**:

1. **Given** an existing project configuration with no kind, **When** it is resolved through the new boundary, **Then** its normalized WordPress configuration is unchanged.
2. **Given** a test-only project kind with a name invalid as a WordPress plugin slug, **When** its descriptor is resolved, **Then** common validation succeeds without invoking WordPress slug rules.
3. **Given** two schemas with the same kind, **When** composition runs, **Then** registration fails before project state is read or changed.

---

### User Story 2 - Store Registry Identity Without Runtime Policy (Priority: P1)

A maintainer can read, write, migrate, and test project identity records without booting WordPress or applying runtime defaults.

**Why this priority**: Registry compatibility and data preservation affect every Sandbox project and must be proven before lifecycle dispatch changes.

**Independent Test**: Run the same repository contract suite against in-memory and file-backed stores, including legacy fixtures, concurrent access, interrupted writes, and unknown compatible fields.

**Acceptance Scenarios**:

1. **Given** every supported legacy registry fixture, **When** it is read and written, **Then** identity, labels, known fields, and compatible unknown fields are preserved without eager migration.
2. **Given** a write interrupted before atomic replacement, **When** the registry is reopened, **Then** the previous valid state remains recoverable.
3. **Given** a registry operation, **When** it executes, **Then** it does not synthesize WordPress, database, Mailpit, or runtime capability fields.

---

### User Story 3 - Dispatch Runtime Operations by Capability (Priority: P1)

A CLI or MCP caller can request a lifecycle operation through one runtime service and receive either a stable result or an early structured capability error.

**Why this priority**: Generic projects and safe recovery cannot be added while runtime policy is distributed through transport handlers.

**Independent Test**: Register a fake adapter and dependencies, exercise supported and unsupported operations, and assert that rejection occurs with zero side effects.

**Acceptance Scenarios**:

1. **Given** a WordPress project, **When** a supported operation is dispatched, **Then** the WordPress compatibility adapter preserves current behavior and output.
2. **Given** an adapter without a requested capability, **When** CLI or MCP requests it, **Then** both transports return equivalent structured guidance before subprocess, HTTP, proxy, filesystem, or registry mutation.
3. **Given** duplicate or conflicting adapters, **When** composition runs, **Then** startup fails closed.

---

### User Story 4 - Own CLI Commands Within Features (Priority: P2)

A feature module can own its parser definition, handler, scope, capability requirements, help metadata, and destructive confirmation policy without editing centralized parser blocks or routing sets.

**Why this priority**: Spec 021 and future recovery commands must be repeatable Sandbox functions, not special cases added to a central dispatcher.

**Independent Test**: Register a test command and compose the CLI without modifying the central parser, then replay the complete current command inventory and representative help/error behavior.

**Acceptance Scenarios**:

1. **Given** the built-in command manifest, **When** it is composed repeatedly, **Then** command and help ordering is deterministic.
2. **Given** duplicate names or aliases, **When** composition runs, **Then** it fails before handler execution.
3. **Given** an unmigrated command, **When** users invoke it, **Then** the named compatibility bridge preserves its current options, routing, output contract, and exit behavior.

---

### User Story 5 - Compose MCP Tool Groups Deterministically (Priority: P2)

A tool group can register through an explicit package-owned manifest with declared dependencies and without wildcard-importing a broad application namespace.

**Why this priority**: Future runtime and recovery tools need isolated registration, testing, and capability enforcement.

**Independent Test**: Compose all built-in groups twice with fake dependencies, verify the same public tool inventory and schemas, and prove duplicate group/tool registration fails.

**Acceptance Scenarios**:

1. **Given** the current MCP tool groups, **When** the new composer starts, **Then** existing tool names, required parameters, and response contracts remain compatible.
2. **Given** a test-only group, **When** it is registered, **Then** no central server bootstrap edit or incidental import side effect is needed.
3. **Given** duplicate tool ownership, **When** composition runs, **Then** startup fails closed.

---

### User Story 6 - Test Side Effects Through Explicit Services (Priority: P2)

A maintainer can test process, HTTP, port, path, and proxy behavior through injected interfaces without monkey-patching the back-filled core namespace or requiring a live stack for every failure path.

**Why this priority**: Runtime and recovery orchestration require deterministic timeout, rollback, redaction, and failure-isolation tests.

**Independent Test**: Exercise timeouts, secret-bearing environments, port collisions, HTTP failures, proxy rollback, and path rejection using fakes, then replay the current live WordPress behavior.

**Acceptance Scenarios**:

1. **Given** a process timeout or failure, **When** it is normalized, **Then** diagnostics are bounded and contain no secret values.
2. **Given** a proxy apply failure, **When** rollback runs, **Then** the prior route remains intact or the failure is reported without expanding mutation scope.
3. **Given** an untrusted path outside approved roots, **When** a shared service validates it, **Then** the operation is rejected before execution.

---

### User Story 7 - Change One Hermes Concern Independently (Priority: P2)

A maintainer can test Hermes state, routing, jobs, gateway/public access, or backup planning independently while current Hermes CLI, MCP, public access, and job behavior remain compatible.

**Why this priority**: Scoped recovery must become a reusable Sandbox feature without being coupled to unrelated Hermes operations.

**Independent Test**: Run focused contract suites for each concern with fake dependencies, then replay current remote Hermes status, job lifecycle, gateway/public-access, and existing backup behavior.

**Acceptance Scenarios**:

1. **Given** persisted Hermes state, **When** state tests run, **Then** gateway and job providers are not initialized.
2. **Given** a routing request, **When** it is evaluated, **Then** no process, network, or persistence side effect occurs.
3. **Given** backup metadata, **When** integrity validation and restore planning run, **Then** no restore, deletion, or overwrite is applied.
4. **Given** current public functions, **When** callers use them after decomposition, **Then** they delegate through the Hermes service with compatible results and authorization ordering.

---

### User Story 8 - Preserve Existing Users During Migration (Priority: P1)

WordPress, remote, and Hermes users continue using existing project configurations, commands, tools, and state without required edits or unexplained behavior drift.

**Why this priority**: Modularization affects shared production paths and is acceptable only as a behavior-preserving migration.

**Independent Test**: Capture and replay configuration, registry, CLI, MCP, live WordPress, remote, and Hermes baselines after every migration phase.

**Acceptance Scenarios**:

1. **Given** an existing WordPress project and registry, **When** each phase lands, **Then** ensure, status, WP-CLI, REST, tests, domains, HTTPS, snapshot, and lifecycle behavior remain compatible.
2. **Given** current remote and Hermes configuration, **When** representative operations run, **Then** protocol, authorization, endpoint, secret handling, and public-access behavior remain compatible.
3. **Given** unexplained output or state drift, **When** a phase is reviewed, **Then** downstream work remains blocked and the phase can return to its previous facade path.

### Edge Cases

- A project omits kind while using legacy global, project, override, label, or `.wp-env.json` layers.
- A descriptor contains an unknown kind, duplicate schema, malformed path, or future fields.
- A registry file is locked, partially written, corrupt, future-versioned, or relocated.
- A command or tool identifier collides through an alias or compatibility bridge.
- An unsupported capability is requested through CLI and MCP concurrently.
- A subprocess times out while holding no state lock, or returns output containing secret values.
- A port becomes occupied between allocation and process start.
- Proxy application succeeds partially and rollback also fails.
- Hermes job cancellation races with completion or state persistence.
- Gateway teardown fails while the authenticated public route is active.
- Backup integrity metadata is missing, invalid, or references an unavailable artifact.
- A legacy external caller imports a compatibility symbol that has not yet migrated.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST determine project kind before applying kind-specific defaults or validation.
- **FR-002**: Existing configurations without an explicit kind MUST resolve as WordPress with compatible normalized results.
- **FR-003**: Common descriptor validation MUST NOT invoke WordPress plugin identity rules.
- **FR-004**: Descriptor parsing MUST NOT execute project code, subprocesses, network calls, or state mutations.
- **FR-005**: Registry access MUST use one repository contract with production file-backed and in-memory implementations.
- **FR-006**: Registry persistence MUST retain canonical-root-plus-label identity, locking, atomic replacement, supported migrations, and compatible unknown-field preservation.
- **FR-007**: Existing registry files MUST remain readable without an eager rewrite.
- **FR-008**: Registry storage MUST NOT apply runtime defaults, infer capabilities, select adapters, or execute lifecycle behavior.
- **FR-009**: Runtime operations shared by CLI and MCP MUST dispatch through one capability-aware runtime service.
- **FR-010**: Capability rejection MUST occur before subprocess, HTTP, proxy, filesystem, registry-write, database, mail, or WordPress-specific side effects.
- **FR-011**: The WordPress adapter MUST preserve existing lifecycle behavior through delegation until parity permits separately approved migration.
- **FR-012**: Schema, adapter, command, alias, MCP group, and MCP tool registration MUST be explicit, deterministic, and reject duplicates.
- **FR-013**: Shared process operations MUST use argument lists, explicit working directories, bounded execution, normalized results, and secret-safe diagnostics.
- **FR-014**: Shared HTTP, port, path, and proxy services MUST expose runtime-neutral behavior and reversible failure handling without WordPress policy.
- **FR-015**: Every CLI command MUST be represented by a feature-owned command specification or a named compatibility bridge entry.
- **FR-016**: New CLI features MUST NOT add parser definitions or routing sets directly to the central CLI module.
- **FR-017**: Every built-in MCP tool group MUST be represented exactly once in an explicit package-owned manifest.
- **FR-018**: MCP tool groups MUST declare dependencies and be testable without broad mutable application globals.
- **FR-019**: Existing CLI command names/options/output contracts and MCP tool names/required parameters/response contracts MUST remain compatible.
- **FR-020**: New and migrated modules MUST use explicit imports and receive side-effect dependencies from composition roots.
- **FR-021**: No new symbols or consumers MAY be added to the legacy back-filled core namespace.
- **FR-022**: Runtime-kind branching MUST be confined to schema and adapter selection except for reviewed, documented compatibility code.
- **FR-023**: Hermes state, routing, jobs, gateway/public access, backup planning, and composition MUST expose separately testable ownership boundaries.
- **FR-024**: Hermes backup behavior in this feature MUST stop at artifact creation/listing, integrity validation, retention hooks, and non-mutating restore planning.
- **FR-025**: This feature MUST NOT automatically restore, delete backups, overwrite state, expand cleanup scope, or enable a new public route.
- **FR-026**: Compatibility facades MUST have a named owner, compatibility tests, removal prerequisites, rollback path, and prohibition on new consumers.
- **FR-027**: Existing WordPress, remote, and Hermes behavior MUST be captured before migration and replayed after every stateful phase.
- **FR-028**: An unexplained externally visible or persisted-state drift MUST block the active phase and downstream features.
- **FR-029**: All registered CLI and MCP surfaces MUST be classified by feature owner, scope, and capability.
- **FR-030**: Spec 021 and scoped recovery MUST remain implementation-blocked until this feature passes automated, live, architecture, security, and data-loss gates.

### Key Entities

- **Project Descriptor**: Side-effect-free declaration of common project identity, project kind, and kind-specific settings.
- **Schema Provider**: Registered validator and normalizer for one project kind.
- **Registry Record**: Persisted canonical project identity and additive runtime metadata, independent of runtime policy.
- **Registry Repository**: Contract for lock-safe, atomic, migration-aware registry access.
- **Runtime Adapter**: Registered owner of capabilities and runtime-specific lifecycle behavior.
- **Runtime Capability**: Stable operation identifier checked before dispatch.
- **Command Specification**: Feature-owned CLI parser, handler, scope, capability, and confirmation metadata.
- **Tool Group Specification**: Package-owned MCP registration callback, dependencies, scope, and capability metadata.
- **Service Dependency**: Explicit process, HTTP, port, path, proxy, clock, or storage interface supplied at composition.
- **Hermes State/Routing/Job/Gateway/Backup Service**: Separately owned Hermes concern coordinated by a compatibility service.
- **Compatibility Facade**: Temporary stable public entry point delegating to migrated services with a documented exit gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every currently registered CLI command appears exactly once in the command inventory and remains invocable through compatibility tests.
- **SC-002**: Every currently registered MCP tool appears exactly once in the tool inventory with compatible public name and input contract.
- **SC-003**: Test-only schema, adapter, command, and MCP group implementations can each be registered without editing central parser or bootstrap modules.
- **SC-004**: Duplicate identifiers for every registration type are rejected in automated tests.
- **SC-005**: Unsupported-capability tests record zero subprocess, HTTP, proxy, filesystem-write, and registry-write calls.
- **SC-006**: Every supported registry fixture round-trips without identity or compatible-field loss, and interrupted-write tests retain the previous valid state.
- **SC-007**: New production files contain zero wildcard imports from legacy shared namespaces, and migrated modules contain zero dependency on back-filled symbols.
- **SC-008**: Each Hermes bounded service passes an isolated test suite without initializing unrelated side-effect providers.
- **SC-009**: Existing WordPress focused tests and live lifecycle checks pass with no unexplained behavior or output drift.
- **SC-010**: Existing remote and Hermes focused tests and representative live checks pass with no authorization, protocol, state, or public-access drift.
- **SC-011**: Full automated tests, self-test, import-boundary checks, registration inventories, and live regression gates all pass before downstream features are unblocked.
- **SC-012**: A maintainer can add the later Compose adapter and scoped recovery modules only through the established contracts, without editing central parser/bootstrap lists or unrelated Hermes concerns.

## Assumptions

- All current `sandbox_core.py` functions used by shipped code or tests remain compatibility-supported until repository usage is zero and removal is separately approved.
- Registry v1 and v2 fixtures remain supported; compatible unknown fields round-trip, while unsupported future schema versions fail safely without rewriting.
- Exact CLI command names, options, exit behavior, and grouped help remain compatible; deterministic ordering may replace incidental source order when no semantic contract exists.
- MCP name/schema compatibility is required; registration order is deterministic but not a public ordering contract.
- Existing remote Scaleway and Hermes environments provide the live smoke targets; destructive tests use disposable state and existing snapshot/rollback controls.
- Existing proxy route planning/apply/remove operations are the only candidates for shared primitives; WordPress URL mutation remains adapter-owned.
- The legacy core and MCP facades remain available for unknown external consumers, but documentation forbids new use.
- Human approval remains required before any destructive state migration, compatibility-facade removal, commit, push, release, or production deployment.
- The implementation uses plain Python contracts and dependency bundles; introducing a dependency-injection framework is outside scope.
