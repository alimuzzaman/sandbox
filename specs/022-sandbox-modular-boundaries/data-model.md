# Data Model: Sandbox Modular Boundaries

## ProjectDescriptor

- `root`: canonical project root
- `label`: validated optional instance label
- `kind`: selected project kind; defaults to `wordpress`
- `common`: kind-neutral committed configuration
- `settings`: validated kind-specific configuration
- `source_files`: ordered config layers used for diagnostics

Validation: descriptor parsing is side-effect free; root is allowed; kind is registered; common fields do not use WordPress identity rules.

## SchemaSpec

- `kind`: unique stable identifier
- `owner`: feature/module owner
- `resolve`: validation/normalization callback
- `version`: contract version

State: registered once during composition; duplicates fail startup.

## RegistryRecord

- canonical root and label identity
- instance/runtime identifier
- project kind and adapter diagnostics
- common URL/port/status metadata
- additive runtime-specific metadata
- compatible unknown fields

State: read from supported legacy/current schemas; updated atomically; not eagerly rewritten.

## RegistryRepository

Operations: get, list, put, remove, migrate/read, lock transaction.

Implementations: JSON file-backed and in-memory. Both run the same behavioral contract suite.

## RuntimeCapability

Stable operation identifier such as ensure, status, start, stop, logs, exec, apply, destroy, URL/proxy, or a WordPress-only capability.

Capabilities are declared by adapter code and checked before dispatch. Persisted copies are diagnostic only.

## AdapterSpec

- `adapter_id` and `version`
- supported kinds
- declared capabilities
- factory receiving runtime dependencies
- structured operation methods

State: registered once; conflicting kind/adapter ownership fails composition.

## OperationRequest / OperationResult / OperationError

Request contains project identity, operation, validated arguments, and request context. Result contains stable status, identity, and operation-specific data. Errors include code, project kind, requested capability, available capabilities, and actionable guidance without secret-bearing internals.

## CommandSpec

- name and aliases
- owner/category/order
- parser builder
- handler
- global/project/instance scope
- required capability
- destructive confirmation policy
- compatibility identifier

State: composed deterministically; each public command appears exactly once through a spec or bridge.

## ToolGroupSpec

- group identifier and owner
- registration callback
- declared dependency keys
- project scope and capability metadata
- order and compatibility aliases

State: composed deterministically; duplicate group/tool ownership fails.

## ServiceDependencies

Immutable references to process runner, HTTP probe, port allocator, path policy, proxy manager, registry repository, clock, and feature services. Production dependencies are created only at composition roots; tests supply fakes.

## HermesState

Validated persisted state and schema version, run/job identifiers, public-route references, and backup metadata references. State repository owns locking, atomic replacement, and corruption reporting only.

## HermesRoute

Resolved local/remote target and policy decision. Route evaluation is side-effect free.

## HermesJob

Job identity, target/worktree, lifecycle state, timestamps, exit/status evidence, and cleanup metadata. Job service owns process/worktree behavior through injected dependencies.

## HermesGatewayPlan

Desired endpoint/tunnel/route/auth-related configuration plus reversible apply/remove evidence. Gateway service does not own general jobs or backup policy.

## HermesBackupArtifact / RestorePlan

Artifact identity, scope reference, checksums/integrity metadata, created time, storage reference, and compatibility version. Restore plan describes validation and ordered actions but cannot apply, delete, or overwrite state in this feature.

## CompatibilityFacade

- public entry point
- owner
- delegated service
- compatibility tests
- new-consumer prohibition
- removal prerequisite
- rollback route

Lifecycle: active compatibility -> zero internal consumers -> separately reviewed removal candidate.
