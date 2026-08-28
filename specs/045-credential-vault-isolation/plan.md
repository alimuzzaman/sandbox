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
│   ├── credential-broker-service-v1.md  # superseded local v1 design/history
│   └── credential-broker-controller-authority-v2.md # production authority gate
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
reachability with credential authorization. The production standalone service
and instance-bound authority are governed by
`contracts/credential-broker-controller-authority-v2.md`. The superseded v1
contract and current endpoint/coordinator classes remain fake/local-only
T034/T035 history until T043 convergence. The strict v2 guest foundation is
separate: SBG2/SBR2, private-veth topology projection, reciprocal full-set
egress projection with canonical empty-set verification, one immutable
hostname/SNI/resolved-IP decision, and the phase-exact typed one-shot effect contract are
production-shaped local contracts but do not install or enable a broker.

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

1. T032 historically records the v1 standalone service, instance-bound guest transport, trusted
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
   SBRS delivery, and bounded cleanup. T035 now has a local production-shaped,
   closed and uninstalled v2 controller/guest/lease service-loop foundation.
   Reciprocal sealed configs carry the reviewed guest registry digest and exact
   control-plane-derived private-veth projection; the Linux listener verifies
   device readback plus route/namespace/default-deny observation before SBG2.
   The exact per-operation
   93-byte abstract recvmsg/SCM_RIGHTS endpoint, independent controller
   derivation, collision refusal, peer/ancillary gates, and reciprocal registry
   digest/bounds now exist locally. Typed GuestRequestV2 admission, one-resolution
   full-set egress authorization, immutable effect context, durable audit/ACK,
   SBR2 delivery, selector dispatch, deadlines, and sticky cleanup are locally
   implemented with injected tests. The terminal schedule uses the reviewed
   overflow-checked `R+2000` grace. Reciprocal plans now compiler-derive their
   process-config digests, cross-pin fixed peer unit/UID/config identities, and
   validate both canonical role files before fixed-cgroup one-PID
   start/observe/start sampling. Exact unprivileged nft/default-deny topology
   evidence remains unavailable. The executable nevertheless attempts the fixed
   closed-first graph and reverses partial construction; missing authority refuses
   before guest bytes or credential resolution. A typed owned DNS process makes
   the absolute deadline enforceable without daemon leakage. T035 is locally
   complete and independently accepted by Sol High for production-shaped closed
   and uninstalled code only. Authorized Ubuntu/systemd/kernel proof
   belongs to T022/T029 and human evidence review to T031.
   T036 is locally complete and independently accepted by Sol High for its
   secret-free helper supervision, broker-first cleanup, and inert composition
   wiring. It remains closed and uninstalled; T022/T029/T031 live-proof and
   human-review gates remain open.
4. T037 adds a proof-gated public `./sb` acceptance seam and offline harness
   coverage using only opaque references and non-secret metadata.

The dependency order is `T032 -> T033 -> T034 -> T035 -> T036`, followed by
T022 authorized helper/service lifecycle proof; T036 also precedes T037.
T003, T022, T037, and T043 jointly precede T029, and
T003/T022/T029/T043 precede T031.
Local preparation keeps `implemented_unproven`, `adoptable=false`, and the null
evidence ID. The T034 tests and T035 guarded seams cannot substitute for Ubuntu
24.04 live evidence or independent final review.

### Phase 1B — Replace the controller authority with strict v2

The v1 controller/lease design is retained only as local history. T038 is the
independently accepted local production-v2 contract task. It requires exactly one persistent controller per
managed-native machine as the sole binding/repository, registered-source,
proof, egress, operation-authorization, lease-dispatch, and durable-audit
authority. The broker remains a separate enforcement boundary: it independently
authenticates the controller's kernel process identity and verifies the exact
operation/request/binding/digest/expiry authorization before descriptor use.

The protocol is a non-negotiable v2 with exact `CLAIM_NEXT_V2`, `CLAIMED_V2`,
`AUTHORIZE_V2`, `AUTHORIZED_V2`, `REFUSE_V2`, `ACTIVATE_V2`, `QUIESCE_V2`, and
audit PRE/POST/ACK schemas. A v2 lease binds the controller and broker epochs,
operation/request, binding version, fixed authentication form, proof/policy/
egress/broker digests, authorization digest, sequence, and expiry. No v1
downgrade, translation, or fallback is allowed.

The dependency order is `T038 -> T039 -> T040 -> T041 -> T042 -> T043`. T038
passed independent Sol High local design review; this does not replace the T031
human release/source/evidence review.
T039 now supplies only the pure reviewed registry, exact JSON/binary codecs,
digest builders, temporal validators, and bounded replay state; it performs no
I/O and wires no runtime path.
The independently accepted local T040 implementation supplies an inert persistent controller service plus isolated broker
v2 listener/connection classes. Explicit injected start owns one process epoch,
mutual kernel/process authentication, HELLO/ACK, independent sequences,
disconnect terminalization, and exactly one non-reconstructable T039 registry
per authenticated connection. It adds no application/runtime composition or
authorization, lease, audit, repository, proof, or egress authority.
The independently accepted local T041 implementation supplies only isolated operation-bound authorization and lease
admission. Eight fixed injected interfaces own binding, registered-source,
scope, proof, egress, activation, expiry, and resolution decisions. Resolution
starts only after the exact `AUTHORIZED_V2` acknowledgement and makes one
capped lease dispatch attempt. The broker creates operations only after
canonical guest validation, emits only the reviewed secret-free claim
projection, verifies every authorization field against local request and
sealed expectations, consumes authorization before descriptor inspection, and
retains one exact sealed memfd only at `lease_bound`. Revocation, expiry,
quiesce, disconnect, and mutation failures clean the exact owned state. T041
adds no audit, adapter entry, upstream effect, application composition, or
runtime activation. The independently accepted local T042 implementation
supplies the inert durable controller-audit
authority, broker-side PRE/effect/POST/lease-ACK sequencing, exact
ACTIVATE/QUIESCE lifecycle messages, immutable secret-free derived config
plans, fixed lifecycle verbs, ownership observation, and injected
start/stop/cleanup ordering. Durable projections exclude operation, request,
lease, authorization, and credential-bearing facts; semantic retries reuse one
commit while conflicts fail closed. The one typed effect executor has no public
upstream wiring and is never replayed after effect entry. This local
implementation installs no service, composes no application/default path, and
does not promote support. The independently Sol High-accepted T043 implementation provides one connected inert full-flow v2 harness,
exact v2 managed/public bridges, canonical v2 executable config input, fixed
helper verbs, terminal guest projection, reverse lifecycle cleanup, and explicit
v1 runtime-fallback refusal. The guest bridge requires a broker-minted one-use
capability bound to the exact broker object, private nonce, purpose, machine,
epochs, and config; the production v1 factory is a fixed refusal. This is a
trusted controller/application-process composition check, not an anti-reflection
security boundary: arbitrary same-process Python reflection, monkeypatching,
closure inspection, low-level object construction/mutation, or module-global
mutation is process compromise and out of scope. Untrusted plugins have no
Python execution in those processes and receive only the canonical guest socket
schema; they cannot select an import, callback, Python object, controller path,
validator, clock, session, or legacy handler. The enforced boundary is the
authenticated cross-process socket and kernel identity. It drives the real T040-T042 objects and session
transports through activation, claim, authorization, lease, durable audit,
effect, terminal result, quiesce receipt, managed reverse stop, and cleanup; its
connected hostile matrix and fresh broad local matrix pass. The first
stitched-fixture candidate was rejected by final Sol High; the replacement is
locally complete and independently accepted. Completed T043 remains a satisfied
predecessor of the still-blocked live T022, T029, and T031 gates. Historical v1 test classes
remain fake/local-only and cannot be installed or treated as authority.
T022 and T029 still require authorized Ubuntu 24.04 evidence, and T031 still requires
independent human security/source/evidence review. Contract completion alone
does not change `implemented_unproven`, `adoptable=false`, or `evidence_id=null`.

### Phase 2 — Implement the explicit application-layer broker

1. Add a per-instance unprivileged broker that receives only the documented
   request shape and cannot access project/home mounts or helper control sockets.
2. Validate binding and proof before secret resolution or upstream connection.
3. Originate a verified HTTPS connection to the exact pinned destination and add
   only the approved bearer/API-key header. Reject redirects and unsupported
   methods/content/protocols rather than guessing.
4. Bound bodies, responses, time, concurrency, cancellation, and error output;
   treat upstream responses as untrusted and redact only defensively. Never
   deliberately emit authorization, but make no universal confinement claim
   for arbitrary upstream transformations of reflected credential material.
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
