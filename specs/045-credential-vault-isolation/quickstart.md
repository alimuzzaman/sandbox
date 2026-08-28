# Quickstart: Credential Vault and Isolation Proof

This is a validation-oriented quickstart for the first implementation. The
explicit broker, upstream, lifecycle, health, audit, and consumer contracts
exist locally, but the capability is not enabled until the live proof gate
closes.

## Preconditions

1. Use a non-`main` checkout on a supported Ubuntu 24.04 host.
2. Confirm the existing managed-native support matrix and proof status. The
   feature must remain blocked while the matrix is
   `implemented_unproven`/`adoptable=false`.
3. Confirm that the operator has an approved opaque source reference. Do not
   paste a secret into a command, environment variable, issue, log, or test
   fixture.

## Existing proof gate

Run the existing read-only checks before attempting any binding:

```sh
./sb native support --json
./sb native preflight --project-dir . --json
```

The existing acceptance harness is the predecessor gate for this feature:

```sh
python3 tests/live_native_acceptance.py --help
```

Run it only on an authorized supported host with its documented instance and
cleanup prerequisites. A partial or missing hostile/grant/revoke/exhaustion/
warm-start/cleanup result is a blocked feature, not a passing result.

## Local contract flow

There is intentionally no public plaintext credential command. An integration
composes `managed_native_credential_broker(...)` with a registered resolver,
exact repository lookup, proof, egress, and an upstream transport. A reviewed
consumer uses `ExplicitCredentialConsumer`; lifecycle uses
`CredentialBrokerSupervisor`, `CredentialHealthMonitor`, and
`CredentialRecoveryService`:

1. Inspect a non-secret capability/proof report.
2. Create a version-one `credential_pending` binding from an existing opaque
   reference and exact request scope.
3. Start or reconcile the broker; observe `credential_pending` until source,
   policy, egress, broker, and effective-isolation proofs match.
4. Send a request through the reviewed guest client contract.
5. Inspect only IDs, digests, state, expiry, decision, and reason codes.
6. Revoke, restart, and clean up; verify no future request is admitted until
   fresh recovery proof completes.

## Standalone service planning boundary

The production authority design is now the strict, non-downgradable
[`contracts/credential-broker-controller-authority-v2.md`](./contracts/credential-broker-controller-authority-v2.md).
It requires one persistent controller per managed-native machine as the sole
binding, source-resolution, proof, egress, authorization, lease-dispatch, and
durable-audit authority. The unprivileged broker separately authenticates the
controller's kernel process identity and verifies the exact operation-bound
authorization and v2 lease before use. Its exact mutual HELLO/ACK,
ACTIVATE/QUIESCE acknowledgement, control, and semantic PRE/POST/ACK audit
schemas now have one pure shared v2 codec and replay-state implementation, but
no controller or broker runtime service is wired in this local increment. v1 is
retained only as T032-T035 history and cannot be
negotiated, translated, or used as a fallback.

The pre-implementation service and transport invariants are recorded in
[`contracts/credential-broker-service-v1.md`](./contracts/credential-broker-service-v1.md).
T032 is complete as a planning artifact only. T033 is also complete as a local
security design review: it selects one sealed anonymous `memfd` transferred
once with `SCM_RIGHTS` over a broker-owned abstract `AF_UNIX` `SOCK_SEQPACKET`
socket, with kernel peer authentication and exact broker-process verification.
The contract defines one unprivileged broker
per managed-native instance, a dedicated instance-bound guest transport, a
separate trusted one-use lease boundary, fixed secret-free helper verbs,
broker-first cleanup, and explicit refusal/evidence rules. This does not start a
service or enable the feature.

The local preparatory chain now has a verified v1 test increment, but is not
complete and must converge on v2 before any production completion claim:

1. T034 is complete locally: 16 fake/local standalone service and transport
   contract tests pass. They do not open real sockets or prove Linux isolation.
2. T035 is locally complete and independently accepted by Sol High (local only):
   local fake-driven and closed-first seams cover retained guests,
   authenticated ACTIVATE/QUIESCE and persistent claim ownership,
   one-use descriptor rendezvous, typed execution, terminal SBRS,
   coordinator-owned one-use prepared-attempt tokens, terminal canonical-prefix
   handling for truncated/trailing lease frames, accepted-socket timeouts,
   canonical per-machine root/group-owned config loading, injected controller/
   SCM_RIGHTS endpoints, bounded selector handling, and audit certainty. An
   exact reviewed per-operation abstract v2 lease listener/address derivation,
   one-second controller connect, collision refusal, ancillary prescan, one-use
   socket/descriptor ownership, and reciprocal registry digest/bounds are now
   implemented with injected local tests. The local executable module now also
   owns a strict SBG2 listener and selector service-loop foundation. Reciprocal
   configs seal the guest registry digest and control-plane-derived private-veth
   tuple; listener admission requires exact device, route, namespace, and
   default-deny observation. Typed request, one-resolution egress decision,
   descriptor effect, durable audit/ACK, SBR2 result, deadline, and sticky
   cleanup paths have injected local tests. The reviewed terminal timing now
   uses a fixed 2-second grace after the request deadline, composed from the
   1-second POST-audit and 1-second lease-ACK bounds; connected PRE retry and
   exact deadline mutation probes pass without replay. Reciprocal configs now
   derive rather than accept their process-config digests, cross-pin fixed peer
   unit/UID/config identities, and validate both no-follow canonical role files
   before one-PID start/observe/start sampling. Packet credentials only recheck
   the pinned tuple. Exact unprivileged nft/default-deny topology evidence is
   not yet available; unavailable authority refuses before guest bytes or
   credential resolution and reverses partial construction. DNS is isolated in a
   deadline-bound owned non-daemon process rather than an arbitrary synchronous
   callback. It remains closed and uninstalled. The reviewed foundation also
   supplies a pure strict SBG2/SBR2 guest codec, the fixed private-veth tuple
   and topology observation contract, canonical reciprocal full-set egress
   projection, a pinned hostname/SNI/resolved-IP decision, and a phase-exact
   immutable one-shot authorized-effect boundary. These are
   production-shaped but closed and uninstalled. This satisfies local T035;
   proving its real Ubuntu 24.04 systemd/kernel behavior is separately T022/T029,
   followed by T031 human review.
3. T036 is locally complete and independently accepted by Sol High for
   secret-free fixed helper supervision, cleanup observation/order, and inert
   application wiring with local tests only.
4. T037: add the proof-gated public `./sb` acceptance seam and offline harness
   coverage using only opaque references and non-secret request metadata.
5. T038 is complete after independent Sol High acceptance of the revised exact
   local contract and mutation/schema-table tests. This is not the T031 human
   release/evidence review. T039 is complete locally with focused tests that
   pin the reviewed registry digest, canonical JSON and digest
   vectors, both fixed binary layouts, mutation refusals, temporal bounds, and
   replay/authorization state, monotonic clock/mandatory-cap enforcement, and
   bounded mismatch tombstones without I/O or runtime wiring. The registry
   retains at most 16 total active/tombstoned identities for the full epoch
   pair and never prunes an operation ID at expiry. After 16 distinct IDs the
   epoch fails closed for capacity. Exactly one registry is pinned to the
   machine, epoch pair, authenticated connection owner, and connection; it is
   never reset, replaced, or reconstructed while that pair lives. Only a
   genuinely changed authenticated epoch pair may construct a new registry and
   accept more work. T040 must enforce that lifecycle. This is an intentional 16-total-operation throughput
   bound, not a concurrency-only claim.
   T040 has an inert persistent controller service and
   isolated broker v2 listener/connection. It proves injected mutual process
   authentication, handshake/sequence/epoch/terminal lifecycle, and exactly one
   pinned T039 registry per authenticated connection without enabling a runtime,
   and has independent Sol High local acceptance.
   T041 now provides the isolated operation authorization and exact v2 lease
   admission seam. The controller waits for `AUTHORIZED_V2` before resolution,
   sends one exact 732-byte lease with one sealed memfd, then wipes and closes
   its local material. The broker keeps a private 16-total-operation epoch
   registry, projects no guest header values or body, consumes authorization
   before descriptor inspection, and stops at `lease_bound`. It has independent
   Sol High local acceptance. T042 has independent Sol High local acceptance:
   locally: the inert controller audit authority durably commits secret-free
   semantic PRE/POST records before acknowledgement, recovers unclosed PRE records
   as indeterminate/possible before activation, and refuses conflicting replay.
   The broker-side local seam executes one typed injected effect only after PRE,
   permits one bounded transport retry, commits POST before the exact 444-byte
   same-socket lease acknowledgement, and never retries after effect entry.
   Immutable secret-free config plans and injected lifecycle tests pin
   controller-first/broker-second start, exact ACTIVATE/QUIESCE acknowledgement,
   broker-first/controller-second stop, and ownership-safe cleanup observation.
   These modules have no public upstream, application, default, or runtime
   composition. The accepted T043 implementation uses one connected authenticated
   session graph rather than the rejected stitched fixture, and its connected
   hostile matrix plus fresh broad local matrix pass. T043 is locally complete
   and independently accepted by Sol High. Historical v1 endpoint/coordinator classes remain fake/local-only.
   Guest composition additionally requires a frozen one-use capability minted
   by the exact authenticated broker session and bound to its private nonce,
   purpose, machine, epochs, config, and object identity. The production v1
   broker factory and consumer are fixed refusals; their historical behavior is
   test-only. Managed/public composition accepts only the exact v2 protocol and
   verbs; the executable accepts only the canonical derived v2 broker config;
   there is no runtime-reachable v1 handle/invoke fallback. This is local
   injected evidence only. Completed T043 is a satisfied predecessor; the live
   T022/T029/T031 gates remain blocked on their other stated requirements.

   Threat-model note: the controller and application Python processes are
   trusted. Same-process reflection, monkeypatching, closure inspection,
   low-level object construction/mutation, or module-global mutation is process
   compromise and is not claimed to be prevented by Python bridge types. Those
   types and one-use receipts prevent ordinary public-API misuse. The untrusted
   guest never receives or serializes a bridge and cannot execute Python in the
   trusted processes; it can send only the exact guest wire document over the
   kernel-authenticated cross-process socket. That document has no import,
   callback, Python-object, controller-path, validator, clock, session, or
   legacy-handler selector.

These tasks are preparation, not live proof. T022 remains blocked until the
local seams, T043 v2 convergence, and authorized Ubuntu helper/service lifecycle
proof pass. T037 and T043 are locally satisfied predecessors; T029 remains
blocked on T003, T022, and the authorized live feature matrix. T031 remains
blocked until T043, the exact clean source, contracts, live results, and
cleanup evidence receive an independent final review. Support remains
`implemented_unproven` with `adoptable=false` and no evidence ID.

## Acceptance matrix

The implementation quickstart must exercise at least:

| Case | Expected result |
|---|---|
| Exact scheme/host/port/method/path and active binding | Bounded upstream result or bounded upstream error |
| Wrong host/port/method/path/scheme | Refused before credential resolution/use |
| Unknown or stale source reference | Refused; no plaintext output |
| Expired/revoked binding | Refused; active sessions closed within bound |
| Redirect or DNS pin drift | Refused unless separately bound and proven |
| Oversized/unsupported request or response | Stable bounded error |
| Broker/instance restart | `credential_pending`, then ready only after fresh proof |
| Missing or drifted native proof | Capability reports blocked/unproven; no workload entry |
| Hostile guest inspection | Zero credential bytes on enumerated exposure surfaces |
| Upstream reflection/transformation | Broker never deliberately emits authorization; bounded reviewed redaction runs, but arbitrary transformed-response confinement remains unproven |

## Foundational and broker local evidence

The contract-only slice is available for review without enabling the managed
runtime or resolving a real credential:

```sh
python3 -m unittest tests.test_credential_capability_report \
  tests.test_credential_binding tests.test_credential_resolver
python3 -m unittest tests.test_native_ownership tests.test_native_network_reservation \
  tests.test_managed_plan tests.test_managed_native_adapter tests.test_isolation_credentials \
  tests.test_native_destroy tests.test_native_recovery
```

The focused Credential Vault command currently passes 62 tests, including the
request broker, pinned HTTPS seam, egress intersection, resolver, consumer,
lease transfer, lifecycle/recovery, health, audit, no-leak, capability, and
repository contracts. The existing managed-native/secret/isolation regression
commands also pass in this checkout. These are local model/repository/source
policy checks only. They do not close the Ubuntu 24.04 predecessor proof gate,
start a broker service, or authorize credential-bearing use. The capability
remains `implemented_unproven` with `adoptable=false` until T003 and the later
hostile live matrix are independently verified.

## Regression evidence

Run the narrow unit/contract suites for the new resolver, binding, broker,
lifecycle, report, and hostile-probe seams, followed by the existing secret,
isolation, managed-native, and CLI suites. The final acceptance report must
include commands, host/runtime identity, evidence ID, elapsed bounds, and cleanup
result. It must not substitute local Compose tests for managed-native proof.

T032 records the historical v1 standalone credential-broker service/transport
planning contract, T033 records the accepted local security design, and T034 records
the passing fake/local contract suite. T035 is locally complete and independently
accepted by Sol High (local only): its closed/uninstalled cross-process service
entrypoint, real controller/lease/guest listener composition, guest disconnect/
deadline processing, lifecycle/audit observation, strict v2 guest topology,
egress projection, and one-shot effect foundations are implemented with local
injected tests. Missing Linux authority still refuses before guest bytes or
credential resolution. Authorized Ubuntu proof remains under T022/T029 and
human review under T031. T036 now has a reciprocal
plan-bound fixed-verb executor, exact bounded ownership/absence status parsing,
broker-first exhaustive cleanup, and exact-type inert dependency wiring. It
now also has a distinct fixed non-root controller entrypoint and shared no-follow
config/process runtime. The controller validates reciprocal plans, pins itself,
boundedly waits for the broker-owned listening abstract seqpacket row, pins the
broker, verifies SO_PASSCRED on its one outgoing socket, and makes one HELLO
attempt. It then remains persistently closed until signal or broker EOF when
application authority or evidence is absent; connect is never retried and
cleanup is sticky and exact-once.
This closed and uninstalled local code is independently accepted by Sol High
for local-only behavior; the eight helper verbs still return code 69 and Ubuntu
unit/ownership proof remains T022. T037 is
locally complete and independently accepted by Sol High (local
only): its authenticated public
projector consumes the existing T040 receipt, pins same-session T041/T042
authorities, validates exact secret-free status/binding/egress projections, and
keeps stale, mismatch, downgrade, unavailable, and indeterminate outcomes
closed. The focused public/T040-T043/native set passed 131 tests, full credential
discovery passed 312 tests, and adjacent native/application coverage passed 55 tests on
this local macOS checkout. These are injected/offline results, not Linux kernel
or installed-service proof. T039 is complete locally; T040 and T041 have independent local acceptance,
T042 has independent local acceptance. The connected T043 implementation, opaque
bridge provenance, exhaustive v2 config-loader coverage, and fresh full matrix
are locally complete and independently accepted by Sol High. The helper/service
lifecycle
(T022), authorized live extension (T029), and independent release review (T031)
remain blocked. No support tier or evidence ID may be promoted from this local
result.
