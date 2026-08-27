# Implementation Plan: Managed Credential Vault and Isolation Evidence

**Branch**: `latest` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/045-credential-vault-isolation/spec.md`

## Summary

Add a managed-native-only outbound credential mediation seam that keeps real
credential bytes outside the workload and applies an approved bearer/API-key
header only at an explicit, bounded application-layer request broker. Reuse
Sandbox's existing digest/CAS policy, default-deny egress, fixed helper, and
effective-state verification, while adding a broker-only reference resolver,
immutable credential bindings, lifecycle/revocation state, and honest
capability/proof reporting. Transparent HTTPS MITM, Compose, Kubernetes,
multi-tenancy, HA, snapshots, and at-rest encryption remain separate gates.

## Technical Context

**Language/Version**: Python 3.9+ (existing CLI/core convention); small native
helper/service entry points remain Python-compatible with the current project
runtime.

**Primary Dependencies**: Existing standard-library services, `systemd-nspawn`,
Bubblewrap, AppArmor, seccomp, nftables, veth networking, and the fixed native
helper. Avoid a new third-party dependency unless Phase 0 proves one is needed
for bounded HTTP/TLS handling.

**Storage**: Existing machine-local registry/file/SQLite repositories for opaque
binding metadata, digests, CAS versions, and audit-safe lifecycle records. No
credential bytes in durable feature state. The existing owner-only plaintext
native store is a recorded residual risk, not a new claim of encryption.

**Testing**: Standard-library `unittest` contract/unit/integration tests,
existing isolation/secret/managed-native suites, and the authorized live native
acceptance harness with hostile, grant/revoke, exhaustion, warm-start, and
cleanup evidence.

**Target Platform**: Managed-native Ubuntu 24.04 only after the existing native
proof gate closes. Unsupported runtimes hard-refuse the capability.

**Project Type**: Python CLI/runtime orchestration with a native Linux helper,
per-instance broker, and documentation contracts.

**Performance Goals**: Preserve existing preflight/status bounds. The initial
broker contract targets 64 KiB request headers, 1 MiB request bodies, 4 MiB
responses, 16 concurrent requests, 5-second connect, 30-second total, and
5-second idle limits; callers may lower but not raise them. Revocation closes
active broker sessions within the documented bound. No throughput or latency
claim is accepted without measured evidence.

**Constraints**: Default-deny egress; exact instance/policy/egress/broker
digests; no plaintext CLI/MCP/API return; no guest env/argv/file/snapshot/output
credential; no root-helper access to credential bytes; no transparent MITM in
v1; single-host/single-control-plane ownership.

**Scale/Scope**: One binding and one managed-native instance at a time for the
MVP consumer; bounded concurrent requests per broker. No multi-tenant control
plane, HA replica story, shared pools, or cross-host credential store.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Constitution gate | Status | Evidence/plan consequence |
|---|---|---|
| I. Per-project instance boundary | PASS | Binding and lease always carry a verified instance identity. |
| II. Registry/state authority | PASS | Use existing repositories and CAS/version seams; no direct registry JSON consumers. |
| III. Single entry + modular package | PASS | Route entry points through the managed isolation gateway and three narrow contracts. |
| IV. Live-stack proof | CONDITIONAL | Existing native evidence is incomplete; Phase 0 is a predecessor gate and blocks enablement. |
| V. Idempotency/docs-with-code | PASS | Reconcile/revoke/recover are idempotent and contracts/quickstart ship with implementation. |
| VI. Parity before removal | PASS | Existing secret, guest-file, Compose, and non-native behaviors remain explicit; no removal. |
| Boundaries/secrets | PASS | No `runtime/wp/` or `vendor/` changes; no secret values in files, output, or fixtures. |
| Branch/shipping | PASS | Work remains on non-`main` `latest`; unrelated dirty files are not staged. |

**Post-design gate**: Do not promote `implemented_unproven` to adoptable or
enable the capability until the live native matrix and this feature's hostile
request/revoke/restart evidence are independently reviewed.

## Project Structure

### Documentation (this feature)

```text
specs/045-credential-vault-isolation/
├── prd.md
├── spec.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── broker-request-v1.md
│   ├── capability-report-v1.md
│   ├── credential-binding-v1.md
│   └── credential-broker-service-v1.md  # pre-implementation service/transport gate
├── plan.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── application/context.py                 # register resolver/binding/broker dependencies
├── secrets/                                # source policy; no public plaintext seam
├── isolation/
│   ├── credentials.py                      # existing native store/injector; classify, do not widen
│   ├── egress_broker.py                    # existing CONNECT transport; keep separate from HTTP broker
│   ├── models.py                           # policy/grant/digest model extensions only through contracts
│   ├── network.py                          # default-deny grant reconciliation
│   ├── verification.py                     # effective proof gate
│   └── ...                                 # launcher, bwrap, nspawn, AppArmor, cleanup
├── runtimes/managed/adapter.py             # managed-native lifecycle and capability refusal
└── commands/                               # CLI/report registration through manifests
tools/native-helper/native-helper.py        # fixed helper verbs; never receives credential bytes
tools/native-helper/native-credential-broker.py # planned unprivileged standalone broker
tests/
├── test_credential_binding*.py              # model, CAS, expiry, revoke, persistence
├── test_credential_broker*.py               # request contract, bounds, TLS/error behavior
├── test_credential_broker_service_contract.py # planned standalone service/transport contract
├── test_credential_lifecycle*.py            # restart/recovery/cleanup state machine
├── test_capability_report*.py               # proven/unproven/drift/refusal semantics
├── test_isolation_*.py                      # regression for existing isolation seams
└── live_native_acceptance.py                # authorized hostile/e2e proof extension
```

**Structure Decision**: Keep policy and domain contracts in the existing
`sandbox/isolation`/`sandbox/secrets` deep modules, wire them through the
application context and managed adapter, and keep the native helper limited to
fixed privileged operations. The explicit HTTP broker is a separate component
from the existing opaque CONNECT broker so callers cannot confuse transport
reachability with credential authorization. The standalone service and
instance-bound transport remain governed by
`contracts/credential-broker-service-v1.md`; T033 accepted its sealed anonymous
descriptor/peer-authenticated seqpacket design for T034 contract tests only.

## Delivery phases

### Phase 0 — Close the predecessor proof gate

Before enabling any credential-bearing code path, reproduce or complete the
existing managed-native evidence for hostile isolation, grant/revoke behavior,
resource exhaustion, warm start, cleanup, and end-to-end quickstart on Ubuntu
24.04. Keep the support matrix unproven/adoptable=false if any required result
is missing. Capture evidence identity, host/runtime versions, bounded timings,
and cleanup results. This phase may block implementation acceptance; it does
not authorize a weaker fallback.

### Phase 1 — Establish contracts and trust boundaries

1. Define opaque `SecretReferenceResolver`/lease behavior using registered source
   policy without exposing a plaintext-return API or arbitrary file access.
2. Define immutable credential binding records, exact scope normalization,
   policy/egress/broker digest relationship, CAS, expiry, revoke, and lifecycle
   states.
3. Define capability/proof output and refusal rules, including the distinction
   between declared support and effective evidence.
4. Decide and measure request/response/concurrency/deadline limits before any
   consumer is enabled.

### Phase 1A — Prepare the standalone service and acceptance seams

This is the append-only T032-T037 preparation chain added after the original
task IDs were established:

1. T032 records the standalone service, instance-bound guest transport, trusted
   lease, fixed helper, lifecycle, cleanup, and refusal invariants in
   `contracts/credential-broker-service-v1.md`. This planning artifact is
   complete, but it is not service implementation or proof.
2. T033 is complete: the spec clarifies the trusted-only lease exception and
   the contract selects one sealed anonymous `memfd` transferred once with
   `SCM_RIGHTS` over a broker-owned abstract `AF_UNIX` `SOCK_SEQPACKET` socket,
   with kernel peer checks and exact broker-process verification before send.
3. T034 adds the passing fake/local service and transport contracts. T035 now
   has a guarded fake-driven coordinator that retains one guest through claim,
   one-use descriptor rendezvous, existing typed-broker execution, terminal
   SBRS delivery, and bounded cleanup. T035 remains open for the production
   controller AF_UNIX/SOCK_SEQPACKET listener/event loop, recvmsg/SCM_RIGHTS
   endpoint, kernel peer observer, cross-process config/entrypoint, guest
   disconnect/deadline loop, and lifecycle/audit observer. Authorized host
   proof belongs to T022/T029/T031, not T035 completion.
   T036 adds secret-free helper supervision, broker-first cleanup, and inert
   composition wiring.
4. T037 adds a proof-gated public `./sb` acceptance seam and offline harness
   coverage using only opaque references and non-secret metadata.

The dependency order is `T032 -> T033 -> T034 -> T035 -> T036`, followed by
T022 authorized helper/service lifecycle proof; T036 also precedes T037.
T003, T022, and T037 jointly precede T029, and T003/T022/T029 precede T031.
Local preparation keeps `implemented_unproven`, `adoptable=false`, and the null
evidence ID. The T034 tests and T035 guarded seams cannot substitute for Ubuntu
24.04 live evidence or independent final review.

### Phase 2 — Implement the explicit application-layer broker

1. Add a per-instance unprivileged broker that receives only the documented
   request shape and cannot access project/home mounts or helper control sockets.
2. Validate binding and proof before secret resolution or upstream connection.
3. Originate a verified HTTPS connection to the exact pinned destination and add
   only the approved bearer/API-key header. Reject redirects and unsupported
   methods/content/protocols rather than guessing.
4. Bound bodies, responses, time, concurrency, cancellation, and error output;
   treat upstream responses as untrusted and redact only defensively.
5. Deliver resolved material through a one-use broker launch channel and replace
   the process on revoke/expiry; make best-effort cleanup explicit.

### Phase 3 — Lifecycle, revocation, and recovery

1. Reconcile binding desired state and network grant state with CAS and idempotent
   cleanup.
2. Enter `credential_pending` after restart or drift and refuse use until fresh
   resolver, broker, egress, policy, and effective-isolation proofs match.
3. Close new admission before active-session draining; report bounded timeout or
   indeterminate audit outcomes without replaying the request.
4. Add pre-start and bounded periodic hooks that cannot weaken isolation.

### Phase 4 — One reviewed consumer and operator proof

1. Select one first consumer that can use the explicit request contract; do not
   retrofit arbitrary command-line clients.
2. Add status/inspect output with references, scopes, digests, states, expiry,
   evidence ID, and reason codes only.
3. Run the full acceptance matrix and existing regression suites, then have a
   security reviewer inspect the source/contract/evidence before enabling.

### Deferred phases (separate design gates)

- Transparent HTTPS MITM/CA injection and arbitrary client compatibility.
- Hardened Docker/Compose adapter, Kubernetes multi-tenancy, pool isolation,
  pause/snapshot reinjection, VM runtimes, external secret manager, at-rest
  encryption, distributed state, and HA.

## Security and operational gates

- The current root helper stays fixed-verb and secret-free; the credential-bearing
  process is unprivileged and separately supervised.
- No request scope is accepted before exact canonicalization and binding match.
- No credential resolution occurs on a rejected request.
- No unproven host, unsupported runtime, stale digest, or ambiguous state may
  reach the broker.
- All logs, status, audit, fixtures, and error paths are reviewed for literal,
  transformed, and length-derived secret leakage.
- Revocation, restart, cleanup, and audit-failure behavior are tested as
  separate outcomes; no upstream rollback claim is made.

## Complexity Tracking

No constitution violation is proposed. The explicit broker is an intentionally
narrow additional trust boundary, justified because extending the opaque CONNECT
broker into transparent request transformation would create a materially larger
security and compatibility surface.
