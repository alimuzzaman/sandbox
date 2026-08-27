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

The local preparatory chain now has a verified test increment, but is not
complete:

1. T034 is complete locally: 16 fake/local standalone service and transport
   contract tests pass. They do not open real sockets or prove Linux isolation.
2. T035 remains open: guarded transport/descriptor seams and a local fake-driven
   coordinator retain one guest through authenticated claim state, one-use
   descriptor rendezvous, the existing typed request broker, terminal SBRS
   delivery, and bounded cleanup. Production controller AF_UNIX/SOCK_SEQPACKET
   listening/event-loop code, recvmsg/SCM_RIGHTS integration, kernel peer
   observation, cross-process config/entrypoint, guest disconnect/deadline
   processing, and lifecycle/audit observation remain incomplete. Authorized
   host proof remains a separate T022/T029/T031 gate.
3. T036: add secret-free fixed helper supervision, cleanup observation/order,
   and inert application wiring with local tests.
4. T037: add the proof-gated public `./sb` acceptance seam and offline harness
   coverage using only opaque references and non-secret request metadata.

These tasks are preparation, not live proof. T022 remains blocked until the
local seams and authorized Ubuntu helper/service lifecycle proof pass. T029
remains blocked on T003, T022, T037, and the authorized live feature matrix.
T031 remains blocked until the exact clean source, contracts, live results, and
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

T032 records the standalone credential-broker service/transport planning
contract, T033 records the accepted local security design, and T034 records
the passing fake/local contract suite. T035 has a fake-driven retained-guest,
claim, descriptor, typed-broker, and terminal-result coordinator, but remains
open for production controller and SCM_RIGHTS endpoints, the cross-process
service entrypoint, guest disconnect/deadline processing, and lifecycle/audit
observation. Authorized host
evidence remains under T022/T029/T031; T036-T037 remain open preparatory
work. The helper/service lifecycle
(T022), authorized live extension (T029), and independent release review (T031)
remain blocked. No support tier or evidence ID may be promoted from this local
result.
