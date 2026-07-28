# Research: Resource Monitoring and Safe Cleanup

## Decision 1: Add a feature-owned command and explicit MCP group

**Decision**: Introduce one global `resources` command through `CommandSpec` and
one explicit `resources` MCP group. Both adapt the same application service.

**Rationale**: `sandbox.registry.CommandSpec` and the MCP tool manifest already
enforce deterministic ownership, composition order, and collision detection.
The existing CLI and MCP download-cache implementations duplicate logic; this
feature must not repeat that drift.

**Alternatives considered**:

- Extend the legacy `cache` parser and both existing cache handlers. Rejected
  because it would preserve duplicated policy and cannot represent host-wide
  monitoring or stale persistent resources cleanly.
- Add parser and tool definitions directly to central composition files.
  Rejected because new features must register through explicit manifests.

## Decision 2: Keep policy in a shared resource service

**Decision**: Put classification, plan construction, plan validation, and apply
policy in `sandbox.resources.service`. Put host-specific measurement and exact
deletion mechanisms behind local and remote adapters.

**Rationale**: CLI/MCP parity is strongest when both surfaces return the same
service envelope. Adapters own runtime mechanics; the service owns eligibility
policy and can be tested without Docker, SSH, or filesystem mutation.

**Alternatives considered**:

- Implement policy in command handlers. Rejected because MCP would require a
  second implementation.
- Reuse broad legacy facades. Rejected because module boundaries forbid new
  consumers of aggregate compatibility namespaces.

## Decision 3: Reconcile ownership from public lifecycle evidence

**Decision**: Use public project/job lifecycle interfaces, Docker/Compose labels,
Sandbox-owned root boundaries, and live mounts/references. Never treat a name or
age alone as ownership evidence.

**Rationale**: The project registry is authoritative for project ownership and
live runtime state is authoritative for current use. Historical resources can
lack sufficient evidence and must remain unverified.

**Alternatives considered**:

- Read `$SANDBOX_HOME/runtime/registry.json` directly. Rejected by the registry
  boundary and because it bypasses locking and validation.
- Delete resources matching `sandbox-*`. Rejected because names are neither
  authoritative nor sufficient to protect permanent/unmanaged data.

## Decision 4: Use fast and thorough provider budgets

**Decision**: A fast scan performs capacity and cheap bounded observations.
A thorough scan adds expensive directory and engine measurements using an
overall budget plus isolated per-category timeouts. Failed categories become
explicit partial observations.

**Rationale**: The incident showed that `docker system df`, broad `du`, and
dependency trees can hang. Independent timeouts preserve useful results and
make missing evidence visible.

**Alternatives considered**:

- Run one broad shell pipeline and sort at the end. Rejected because one slow
  path withholds all results.
- Represent timeouts as zero. Rejected because it understates used space and
  could make an unsafe cleanup candidate.

## Decision 5: Persist short-lived, target-bound plans

**Decision**: Store atomic plan records beneath
`$SANDBOX_HOME/runtime/resource-plans/`. Plans use a schema version, opaque ID,
stable target identity, scope, creation/expiry times, exact candidates and
evidence digest, and execution state. Default validity is 15 minutes.

**Rationale**: CLI planning and apply are separate invocations. Durable,
time-limited records support target matching, replay protection, auditability,
and crash-safe state transitions.

**Alternatives considered**:

- Keep plans only in memory. Rejected because CLI invocations do not share
  memory and remote operations can outlive a client.
- Accept candidates again on the apply command line. Rejected because the user
  could confirm a different scope from the reviewed plan.

## Decision 6: Remove exact candidates; never invoke broad prune

**Decision**: Safe cache cleanup removes exact Sandbox-owned cache paths and
exact unused engine objects. Stale cleanup removes exact worktrees or named
volumes only with positive ownership and absence of registry, job, backup,
permanent-host, and live-mount protection.

**Rationale**: Broad prune operations can delete unrelated workloads. Exact IDs
and paths allow per-item liveness revalidation and itemized outcomes.

**Alternatives considered**:

- `docker system prune`, `docker volume prune`, or unfiltered image/build-cache
  prune. Rejected because ownership cannot be guaranteed.
- Automatic job retention sweep. Rejected because the feature requires a
  reviewed plan and confirmation.

## Decision 7: Treat named volumes as persistent by default

**Decision**: Named volumes never enter ordinary cache cleanup. They may enter
the stale scope only when authoritative labels/managed-root evidence proves
ownership and current references prove non-use.

**Rationale**: Volumes commonly contain application state. The additional gate
matches the approved PRD and avoids turning "dangling" into "safe."

**Alternatives considered**:

- Treat every engine-reported dangling volume as cache. Rejected because
  dangling describes current mounts, not ownership or retention.

## Decision 8: Use the existing named-remote SSH seam without remote deployment

**Decision**: The remote adapter resolves a configured remote, sends bounded
non-secret inventory requests through the shared SSH process seam, and collects
 compact structured results. Category probes are independent. Confirmed remote
apply records an idempotency receipt before exact item actions and is never
automatically replayed after a timeout.

**Rationale**: Monitoring must not deploy or modify the remote merely to inspect
it. The SSH transport already centralizes connection policy and warns that a
client timeout is ambiguous for stateful operations.

**Alternatives considered**:

- Upload or upgrade the remote Sandbox runtime before every scan. Rejected
  because read-only monitoring would mutate remote state.
- Retry timed-out cleanup automatically. Rejected because the first operation
  may still be running.

## Decision 9: Preserve the existing narrow cache surface

**Decision**: Keep `sb cache` and the existing MCP cache tools compatible.
The new service may call the same download-cache mechanism, but does not remove
or silently change the old commands.

**Rationale**: The constitution requires parity before removal and existing
users rely on the narrow shared download-cache command.

**Alternatives considered**:

- Replace `sb cache` immediately. Rejected because it expands migration risk
  without adding product value.

## Decision 10: Verify contracts, policy, and live behavior separately

**Decision**: Use pure service tests for classification and plan/apply state;
fake adapters for timeouts, drift, partial failures, target mismatch and replay;
manifest/interface tests for CLI/MCP parity; then run live read-only status and
plan commands. Mutating live checks use disposable owned fixtures only.

**Rationale**: Safety policy needs exhaustive deterministic tests, while the
constitution also requires evidence from the running product.

**Alternatives considered**:

- Rely only on mocks. Rejected because real mount namespaces and engine
  behavior caused the original visibility gap.
- Test cleanup against permanent host resources. Rejected as unnecessarily
  destructive.
