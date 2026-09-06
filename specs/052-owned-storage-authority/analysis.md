# Independent Planning Analysis: Owned Storage Authority

**Date**: 2026-09-04

**Verdict**: **PLANNING REPAIRED — OPTION 2 AUTHORIZED**

**Implementation authorization**: **GATED (Source implementation requires task approval; live qualification requires separate authorization)**

## Resolution of the public port blocker

On 2026-09-04, the operator explicitly authorized **Option 2**: amending FR-058
to decouple owned-storage lifecycle persistence from OCI hosting infrastructure
(`sandbox/hosting/**` and `hosts.json`).

### Key Decisions in the Repaired Architecture

1. **Dedicated Durable Lifecycle Owner**:
   - `sandbox/owned_storage_lifecycle/` durably persists review, promotion,
     finalization, revocation, and capability evidence in a dedicated, crash-safe
     `StorageAuthorityLifecycleRepository` (e.g. `runtime/storage_authority/lifecycle.json`).
   - Concurrency is managed via advisory file locking (`fcntl.flock`), atomic replacement
     via temporary files and fsync, and generation-based CAS.
   - It does not touch, import, or extend `sandbox/hosting/**`, `hosts.json`, or
     `TARGET_MUTATION_CAPABILITIES`.
2. **Preservation of the Prepared-Binding Handshake**:
   - Cross-repository promotion uses one lifecycle semantic owner plus an exact
     non-authorizing prepared storage binding in the storage authority service.
   - Normal mutation requires both the current lifecycle promotion and active
     matching binding; missing, mixed, stale, revoked, or unknown state fails closed.
3. **Strict Boundary Preservation**:
   - Features 048–051 and `sandbox/hosting/**` remain 100% immutable with zero diffs.
   - Remote hosting transports and hosting specs remain untouched.
   - Live qualification and production rollout remain separate human-authorized gates.

## Prior Analysis History (2026-09-02)

The previous independent analysis had correctly flagged that FR-058's requirement to
store lifecycle state as a nested value inside `RecoveryRepository.target_mutation_port()`
was blocked because `TARGET_MUTATION_CAPABILITIES` in `sandbox/core/_hosting.py` had no
owned-storage member, and `activation_host_state_port()` only reads/writes `image_activation`.
That blocker is now resolved by the Option 2 amendment.

## Readiness for Task Generation

With FR-058 amended across `spec.md`, `plan.md`, `data-model.md`, `contracts/capability-evidence-v1.md`,
and `research.md`:
- Specification and planning are consistent and unblocked.
- Next step: Generate an actionable, dependency-ordered `tasks.md` following the
  Spec Kit workflow, followed by cross-artifact consistency analysis (`speckit-analyze`).
- Implementation, service installation, and live qualification remain gated until tasks
  are reviewed and explicitly authorized.
