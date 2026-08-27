# Tasks: Managed Credential Vault and Isolation Evidence

**Input**: Design documents in `/specs/045-credential-vault-isolation/`

**Prerequisite**: Do not enable or accept this feature until the existing
managed-native proof gate is closed on an authorized Ubuntu 24.04 host.

## Dependencies and execution order

```text
T001-T002 (setup)
        |
T003-T007 (foundational; T003 is a hard predecessor gate)
        |
   +----+------------------+
   |                       |
 US1 T008-T014       US4 T024-T027
   |
 US2 T015-T018
   |
 US3 T019-T023

T032 -> T033 [complete] -> T034 -> T035 -> T036
                                  |  \
                                  |   -> T037
                                  v
                                T022

T003 + T022 + T037 -> T029 -> T031
```

US1 depends on the foundational contracts and proof gate. US2 depends on the
explicit broker from US1. US3 depends on US1 and the durable binding state. US4
may develop its report model in parallel with US1 but must gate enablement and
release. Existing task IDs remain stable, so the later-added T032-T037 IDs are
append-only even though they are predecessors of T022/T029. The final acceptance
tasks depend on all four stories and the preparatory service/transport work.

## Phase 1: Setup

**Goal**: Establish safe feature-owned fixtures and registration points without
introducing a secret value or changing an existing runtime default.

- [x] T001 [P] Add feature-owned contract-test fixture metadata with only fake references and redacted expected values in `tests/fixtures/credential_vault/README.md`. **DONE:** fixture contains no credential value and remains safe before the live proof gate.
- [x] T002 [P] Register the new capability and contract module names through the existing manifest/registry extension points in `sandbox/isolation/manifest.py` and `sandbox/runtimes/manifest.py`, preserving `implemented_unproven` and `adoptable=false` until evidence exists. **DONE:** `outbound_credential_mediation` is declared for managed-native only; no runtime path is enabled.

## Phase 2: Foundational

**Goal**: Close or explicitly record the predecessor proof gate and establish the
opaque resolver, binding, persistence, and contract seams used by every story.

- [ ] T003 Run the authorized Ubuntu 24.04 managed-native hostile, grant/revoke, exhaustion, warm-start, cleanup, and end-to-end acceptance matrix and update only its evidence records in `specs/039-native-runtime-adoption/evidence/README.md` and `specs/039-native-runtime-adoption/evidence/` (BLOCKED: this checkout has no authorized Ubuntu host evidence; do not substitute local/container runs)
- [x] T004 Define the broker-only opaque reference resolver and one-use lease interface, including registered-source ownership checks and no plaintext-return operation, in `sandbox/isolation/credential_resolver.py`. **DONE:** leases require a ready binding, registered owner-only source, and one callback use; direct `resolve()` is a stable refusal and bytes cannot be returned as the callback result.
- [x] T005 Add immutable exact-scope binding and lifecycle models with canonicalization, state transitions, expiry, and version/CAS invariants in `sandbox/isolation/credential_binding.py`. **DONE:** HTTPS/443, DNS, exact path/method, approved auth forms, digest, state, revoke/expiry, and versioned update invariants are enforced without secret-bearing repr/serialization.
- [x] T006 Add durable binding metadata persistence that stores references/digests/state only and uses the existing repository/locking authority in `sandbox/runtimes/managed/credential_repository.py`. **DONE:** `credential_bindings` is an additive NativeRepository section with owner checks, atomic CAS updates, closed-state removal, and no credential value fields.
- [x] T007 [P] Add foundational unit and contract coverage for resolver refusal, binding canonicalization, secret-free serialization, CAS conflicts, and state transitions in `tests/test_credential_binding.py` and `tests/test_credential_resolver.py`. **DONE:** 11 focused tests pass; existing managed-native/secret/isolation ownership suites also pass.

## Phase 3: User Story 1 — Bind an approved credential to one outbound operation (P1)

**Story goal**: An operator can authorize one exact request scope, and near misses
are denied before credential resolution or upstream connection.

**Independent test**: With a proof-qualified test instance, matching requests
receive bounded upstream results while wrong host/port/method/path/scheme,
unknown-reference, expired, and ambiguous-binding cases are denied before use.

- [x] T008 [P] [US1] Write request-contract tests for exact scope, header policy, redirect denial, canonicalization, and pre-resolution refusal in `tests/test_credential_broker_contract.py`. **DONE:** broker and pinned-upstream contract tests cover exact scope, canonicalization, guest security headers, redirects, DNS/address validation, and no pre-resolution near-miss path.
- [x] T009 [US1] Implement the per-instance unprivileged explicit request broker with validation-before-resolution and bounded error responses in `sandbox/isolation/credential_request_broker.py`. **DONE:** `CredentialRequestBroker` validates the fixed request envelope before resolver use, enforces limits/concurrency, redacts bounded responses, and has no default upstream/proof/egress admission.
- [x] T010 [US1] Implement verified upstream HTTPS connection, DNS/IP pin checks, certificate validation, bearer/API-key header application, and redirect refusal in `sandbox/isolation/credential_upstream.py`. **DONE:** `VerifiedHttpsUpstream` pins public IPv4 resolution, uses SNI/certificate validation on the default connector, applies only registered auth profiles, rejects redirects, and bounds request/response/time.
- [x] T011 [US1] Implement binding-to-egress intersection checks without widening existing default-deny grants in `sandbox/isolation/credential_policy.py`. **DONE:** exact grant-set digest/base-policy/instance ownership, grant expiry/revocation, hostname HTTPS, and public-CIDR address coverage are fail-closed.
- [x] T012 [US1] Implement one-use broker lease transfer and process-lifetime cleanup without placing bytes in argv, environment, unit text, staging paths, or the fixed helper protocol in `sandbox/isolation/credential_resolver.py` and `sandbox/isolation/credential_supervisor.py`. **DONE:** `BrokerLeaseTransfer` delegates only one-use callbacks; supervisor closes admission, invalidates transfers, drains within five seconds, and registers process-exit cleanup without accepting credential bytes.
- [x] T013 [US1] Wire binding, resolver, policy, broker, and proof dependencies through the application context and managed-native lifecycle in `sandbox/application/context.py` and `sandbox/runtimes/managed/adapter.py`. **DONE:** explicit broker factory, optional dependency seams, adapter request/recovery refusals, and owner-scoped repository lookup are wired without changing default runtime selection.
- [x] T014 [US1] Add managed-native integration coverage for one successful exact request and all pre-upstream near-miss denials in `tests/test_credential_broker_integration.py`. **DONE:** real registered-source resolver, repository CAS state, egress intersection, verified upstream seam, and five near-miss denials pass locally.

## Phase 4: User Story 2 — Use an approved service without receiving the credential (P1)

**Story goal**: A reviewed workload client can use the broker while hostile
inspection finds no credential on enumerated guest/control/output surfaces.

**Independent test**: Run a fake upstream and hostile probes inside a proof-
qualified managed-native instance; verify bounded responses, no literal or
transformed credential leakage in the declared surfaces, and stable failures for
unsupported/oversized inputs.

- [x] T015 [P] [US2] Add hostile no-leak probes for environment, argv, mounts, guest files, snapshots, policy/registry/audit records, control channels, and retained output in `tests/test_credential_no_leak.py`. **DONE:** local caller-visible surfaces and defensive response reflection are checked; live guest surfaces remain part of T029.
- [x] T016 [P] [US2] Add bounded request/response, concurrency, cancellation, timeout, unsupported-method/content, duplicate-header, transformed-response reflection, and safe-error tests for the v1 limits in `tests/test_credential_broker_bounds.py`. **DONE:** request/response ceilings, concurrency closure, redirect, timeout, duplicate/security headers, unsupported method, and no-raw-diagnostic cases pass.
- [x] T017 [US2] Document the reviewed guest request client contract, safe response fields, and explicit non-goals in `docs/credential-vault.md`. **DONE:** explicit consumer shape, safe status/result fields, residual at-rest risk, unsupported runtimes, and non-goals are documented.
- [x] T018 [US2] Integrate one real non-secret first consumer through the explicit broker contract and add its end-to-end test in `sandbox/runtimes/managed/credential_consumer.py` and `tests/test_credential_consumer.py`. **DONE:** `ExplicitCredentialConsumer` can only construct binding-derived requests and returns the broker envelope; guest auth headers and oversized bodies are refused.

## Phase 5: User Story 3 — Revoke, expire, and recover a binding safely (P1)

**Story goal**: Expiry, revoke, restart, cleanup, and audit indeterminate
outcomes fail closed and remain observable.

**Independent test**: Exercise active use, revoke, expiry, broker restart,
machine restart, stale digests, and cleanup; verify new-use refusal, bounded
session closure, `credential_pending` recovery, and no replay after audit error.

- [x] T019 [P] [US3] Add lifecycle state-machine tests for create, pending, ready, revoke, expiry, blocked, restart, and recovery transitions in `tests/test_credential_lifecycle.py`. **DONE:** repository CAS tests cover pending/ready/revoking/revoked/expired/blocked and closed-state removal; recovery tests cover restart and stale proof.
- [x] T020 [US3] Implement monotonic expiry/revoke admission closure and bounded active-session draining in `sandbox/isolation/credential_request_broker.py` and `sandbox/isolation/credential_supervisor.py`. **DONE:** broker checks expiry at admission, closes binding/global admission before drain, enforces active-session ceilings, and supervisor reports bounded drain outcomes.
- [x] T021 [US3] Implement restart reconciliation that enters `credential_pending`, recreates a fresh lease, and re-verifies policy/egress/broker/effective-isolation digests in `sandbox/runtimes/managed/credential_recovery.py`. **DONE:** recovery invalidates old leases, persists pending before gates, requires matching digests/proof/egress/report, and only then CAS-promotes a fresh ready version.
- [ ] T022 [US3] Add fixed-verb service supervision and cleanup observation for the unprivileged credential broker without passing credential bytes to the root helper in `tools/native-helper/native-helper.py` and `sandbox/runtimes/managed/services.py` (BLOCKED: requires T032-T036 plus authorized Ubuntu root-helper/service lifecycle proof; local supervisor intentionally remains an in-process contract and no local seam may be promoted as T022 completion)
- [x] T023 [US3] Add audit-safe lifecycle records and indeterminate-outcome handling that never retries a credential-bearing request after an append failure in `sandbox/isolation/credential_audit.py` and `tests/test_credential_audit.py`. **DONE:** append-only validated records reject sensitive fields; pre-effect append failure blocks; post-effect append failure returns indeterminate/no-replay.

## Phase 6: User Story 4 — Verify capability, proof, and lifecycle state (P2)

**Story goal**: Operators and reviewers can distinguish declared support from
effective proof, and missing or drifted gates block the capability.

**Independent test**: Compare reports for proven, unproven, missing-prerequisite,
stale-digest, and drifted-runtime cases; only the proven matching case admits
credential use.

- [x] T024 [P] [US4] Define secret-free capability/proof report models for support tier, evidence identity, prerequisites, effective observations, digests, binding states, and refusal reasons in `sandbox/isolation/capability_report.py`. **DONE:** proven/unproven/blocked/unavailable tiers, evidence identity, status-only binding projections, digest checks, and fail-closed admission are modeled without source references or values.
- [x] T025 [US4] Expose capability and binding status through the existing command/manifest extension points in `sandbox/commands/native.py` and `sandbox/commands/manifest.py`. **DONE:** `sb native credential-status --json` is a non-mutating, fail-closed report; it reads existing binding metadata only through the repository authority and never creates/migrates state.
- [x] T026 [US4] Add pre-start and bounded periodic lifecycle hooks that close credential admission on proof drift without weakening unrelated default-deny network controls in `sandbox/runtimes/managed/adapter.py` and `sandbox/runtimes/managed/credential_health.py`. **DONE:** health monitor requires matching policy/egress/broker digests, ready/nonexpired state, admissible proof, allowed egress, and optional capability report; drift invokes bounded broker revocation only.
- [x] T027 [US4] Add report/refusal/health tests for `implemented_unproven`, missing evidence, drift, stale policy, unsupported runtime, and proven effective state in `tests/test_credential_capability_report.py`. **DONE:** report tests cover blocked/unavailable tiers, missing proof, stale/drifted health observations, unsupported runtime identity, and proven round-trip admission.

## Phase 7: Polish and cross-cutting acceptance

**Goal**: Verify regression behavior, evidence quality, documentation, and the
release decision without staging unrelated user work.

- [x] T028 [P] Run the focused resolver, binding, broker, lifecycle, report, secret, and isolation unittest suites and record commands/results in `specs/045-credential-vault-isolation/quickstart.md`. **DONE:** 62 Credential Vault contract/lifecycle/no-leak/broker tests pass locally; existing isolation/secret/managed-native regression commands also pass. Full discovery remains limited by pre-existing environment-dependent tests.
- [ ] T029 Extend the authorized live native acceptance harness with exact binding, hostile no-leak, revoke, restart, exhaustion, cleanup, and timing checks in `tests/live_native_acceptance.py` (BLOCKED: requires T003, T022, T037, and an authorized Ubuntu 24.04 host; offline harness tests are preparation only)
- [x] T030 Update the managed-native capability, isolation, and operator documentation with the explicit refusal boundaries and at-rest residual risk in `docs/native-runtime-isolation.md`, `docs/sandbox-config-reference.md`, and `docs/credential-vault.md`. **DONE:** status command, unsupported runtimes, exact request boundary, residual at-rest risk, and proof-gated refusal rules are documented.
- [ ] T031 Complete an independent security/source/evidence review of the implementation against `specs/045-credential-vault-isolation/contracts/`, update the evidence ID and support tier only if every predecessor and feature gate passes, and record the decision in `specs/045-credential-vault-isolation/quickstart.md` (BLOCKED: requires T003, T022, T029, a clean exact source revision, live feature evidence, and independent implementation review; support remains `implemented_unproven`/`adoptable=false`)

## Phase 8: Preparatory work to unblock T022 and T029

**Goal**: Prepare the standalone service, instance-bound transport, cleanup, and
public acceptance seams locally without enabling credential use or claiming live
proof. These IDs are append-only to preserve references to T022/T029/T031.

- [x] T032 [US3] Define the pre-implementation standalone service, instance-bound guest transport, trusted one-use lease channel, fixed helper verbs, and cleanup invariants in `specs/045-credential-vault-isolation/contracts/credential-broker-service-v1.md`. **DONE:** the contract records required boundaries and explicit refusals; it does not select an unreviewed lease mechanism, enable a runtime path, or change evidence/support state.
- [x] T033 [US3] Complete an independent security design review that selects one concrete trusted one-use lease mechanism, reconciles the FR-008/SC-002 control-channel wording through `specs/045-credential-vault-isolation/spec.md` clarification if required, and records accepted peer authentication, descriptor/socket ownership, replay refusal, and no-secret surfaces in `specs/045-credential-vault-isolation/contracts/credential-broker-service-v1.md`. **DONE:** selected one sealed anonymous `memfd` transferred once with `SCM_RIGHTS` over a broker-owned abstract `AF_UNIX` `SOCK_SEQPACKET` socket; kernel peer checks, exact broker-process verification, terminal consumption, cleanup, residual trusted-owner assumption, and all no-secret surfaces are explicit. This design review is local only and does not satisfy T031.
- [x] T034 [US3] Add offline standalone service and transport contract tests, including cross-instance denial, broker-epoch rotation, lease one-use, bounded status, and argv/environment/unit/config/output no-leak checks, in `tests/test_credential_broker_service_contract.py`. **DONE:** 16 fake/local tests pass; they exercise validation, frame bounds, private-veth and peer gates, descriptor cleanup, terminal acknowledgements, lifecycle limits, and no-secret surfaces. They do not open real sockets or prove Linux isolation.
- [ ] T035 [US3] Implement the reviewed unprivileged standalone broker executable and its instance-bound request/trusted-lease endpoints in `tools/native-helper/native-credential-broker.py`, then satisfy `tests/test_credential_broker_service_contract.py` without adding a default or adoptable runtime path (LOCAL ONLY: guarded library seams are present, but T035 remains open for the full guest request/result protocol, runnable coordinator, cross-process rendezvous, exact broker/upstream integration, and live-proof separation; code presence is not T022 proof)
- [ ] T036 [US3] Add secret-free digest-bound broker plans, fixed helper lifecycle verbs, broker-first cleanup observation/order, and inert dependency wiring in `sandbox/runtimes/managed/services.py`, `tools/native-helper/native-helper.py`, `sandbox/runtimes/managed/adapter.py`, and `sandbox/application/context.py`, with local coverage in `tests/test_managed_services.py`, `tests/test_native_cleanup_observation.py`, and `tests/test_credential_wiring.py` (LOCAL ONLY: keep T022 blocked until authorized host proof)
- [ ] T037 [US1] Add a proof-gated public `./sb` acceptance surface that accepts only opaque source references and exact non-secret binding/request/revoke metadata in `sandbox/commands/native.py`, then add offline public-command and harness coverage in `tests/test_native_cli.py` and `tests/test_live_native_acceptance_harness.py` (LOCAL ONLY: keep T029 blocked until the authorized live matrix runs)

## Phase 9: Authorized-proof harness preparation

**Goal**: Make the future authorized Ubuntu 24.04 run for T022 and T029
deterministic, replay-safe, bounded, secret-safe, and independently reviewable.
These IDs are append-only. Nothing in this phase executes a live check, and no
item here changes `implemented_unproven`, `adoptable=false`, or the null
evidence identity.

- [x] T038 Add the versioned acceptance manifest, canonical encoding, plan digest, revision gate, and no-leak scanner in `tests/credential_vault_proof/manifest.py` and `tests/credential_vault_proof/scanner.py`. **DONE:** exact-key schema, bounded strings/lists/files, forbidden-key and secret-shape refusal, digest-stable canonical JSON, and a revision mismatch that refuses before any test action.
- [x] T039 Add the replay-safe proof-run ledger, live probe command model, evidence bundle validator, and cleanup verifier in `tests/credential_vault_proof/ledger.py`, `probes.py`, `bundle.py`, and `cleanup.py`. **DONE:** one request identity per run, ledger-first retry, `acceptance_unknown` for empty/malformed acceptance, cleanup overriding success, allowlisted argv-only probes with bounded redacted parsing, and a bundle validator that refuses stale, copied, mixed-revision, contradictory, incomplete, or fake-marked evidence.
- [x] T040 Add the offline runner, deterministic report, runbook, and local test suites in `tests/credential_vault_proof/cli.py`, `report.py`, `docs/credential-vault-proof-harness.md`, and `tests/test_credential_vault_*.py`. **DONE:** seven fixed verbs with bounded error codes and no execution path, a report that separates local harness behaviour from live evidence, and 107 offline tests that need no Linux, root, systemd, socket, or network access.

## Parallel execution examples

### After foundational work

```text
T007 (resolver/binding tests)
T008 (broker contract tests)
T024 (capability report model)
```

These touch separate files and can be prepared in parallel, but no story can be
enabled before T003 and the foundational contracts are complete.

### Within User Story 2

```text
T015 (no-leak probes)   T016 (bounds/error tests)
              \          /
               T017 (consumer contract docs) -> T018 (first consumer)
```

### Within User Story 4

```text
T024 (report model) -> T025 (CLI/manifest) -> T026 (health hooks)
T027 (report tests) can proceed alongside T025 after T024 is stable.
```

### To unblock the standalone service and live acceptance

```text
T032 (service/transport invariants; complete)
  -> T033 (independent lease-channel design review; complete)
  -> T034 (failing-first local service/transport contracts)
  -> T035 (standalone unprivileged service)
  -> T036 (fixed helper supervision, cleanup, inert wiring)
       +-> T022 (authorized helper/service lifecycle proof)
       +-> T037 (proof-gated public acceptance seam)

T003 + T022 + T037 -> T029 (authorized live feature matrix)
T003 + T022 + T029 -> T031 (independent final evidence/support review)
```

T034 and any unrelated non-service local regression preparation may run in
parallel only after T033 accepts the concrete lease mechanism. No item in this
chain changes `implemented_unproven`, `adoptable=false`, or the null evidence ID.

## Implementation strategy

1. **MVP gate**: Complete T003–T014 and prove one exact request with no guest
   credential exposure. If the predecessor proof remains incomplete, stop at
   `implemented_unproven` and do not enable the broker.
2. **Safety increment**: Complete T015–T023 so no-leak, bounded responses,
   revoke, expiry, restart, and cleanup behavior are independently observable.
3. **Operator increment**: Complete T024–T027 so capability/proof status cannot
   overclaim readiness and lifecycle hooks fail closed.
4. **Release gate**: Complete T028-T031 with authorized live evidence and an
   independent review. Transparent MITM, unsupported runtimes, multi-tenancy,
   HA, snapshots, and at-rest encryption remain deferred rather than silently
   entering the MVP.
5. **Blocked-service increment**: Complete T032-T037 as local preparation, then
   return to T022 for authorized helper/service proof and T029 for the live
   feature matrix. Local preparation never changes the support tier or evidence
   identity.

## Format validation

All implementation tasks use the required `- [ ] T###` checklist form. Story
tasks carry `[US1]`–`[US4]`; setup, foundational, and polish tasks do not. Every
task names at least one exact repository path.
