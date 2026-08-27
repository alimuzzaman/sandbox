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
        |
T028-T031 (polish, evidence, review)
```

US1 depends on the foundational contracts and proof gate. US2 depends on the
explicit broker from US1. US3 depends on US1 and the durable binding state. US4
may develop its report model in parallel with US1 but must gate enablement and
release. The final acceptance tasks depend on all four stories.

## Phase 1: Setup

**Goal**: Establish safe feature-owned fixtures and registration points without
introducing a secret value or changing an existing runtime default.

- [ ] T001 [P] Add feature-owned contract-test fixture metadata with only fake references and redacted expected values in `tests/fixtures/credential_vault/README.md`
- [ ] T002 [P] Register the new capability and contract module names through the existing manifest/registry extension points in `sandbox/isolation/manifest.py` and `sandbox/runtimes/manifest.py`, preserving `implemented_unproven` and `adoptable=false` until evidence exists

## Phase 2: Foundational

**Goal**: Close or explicitly record the predecessor proof gate and establish the
opaque resolver, binding, persistence, and contract seams used by every story.

- [ ] T003 Run the authorized Ubuntu 24.04 managed-native hostile, grant/revoke, exhaustion, warm-start, cleanup, and end-to-end acceptance matrix and update only its evidence records in `specs/039-native-runtime-adoption/evidence/README.md` and `specs/039-native-runtime-adoption/evidence/`
- [ ] T004 Define the broker-only opaque reference resolver and one-use lease interface, including registered-source ownership checks and no plaintext-return operation, in `sandbox/isolation/credential_resolver.py`
- [ ] T005 Add immutable exact-scope binding and lifecycle models with canonicalization, state transitions, expiry, and version/CAS invariants in `sandbox/isolation/credential_binding.py`
- [ ] T006 Add durable binding metadata persistence that stores references/digests/state only and uses the existing repository/locking authority in `sandbox/runtimes/managed/credential_repository.py`
- [ ] T007 [P] Add foundational unit and contract coverage for resolver refusal, binding canonicalization, secret-free serialization, CAS conflicts, and state transitions in `tests/test_credential_binding.py` and `tests/test_credential_resolver.py`

## Phase 3: User Story 1 — Bind an approved credential to one outbound operation (P1)

**Story goal**: An operator can authorize one exact request scope, and near misses
are denied before credential resolution or upstream connection.

**Independent test**: With a proof-qualified test instance, matching requests
receive bounded upstream results while wrong host/port/method/path/scheme,
unknown-reference, expired, and ambiguous-binding cases are denied before use.

- [ ] T008 [P] [US1] Write request-contract tests for exact scope, header policy, redirect denial, canonicalization, and pre-resolution refusal in `tests/test_credential_broker_contract.py`
- [ ] T009 [US1] Implement the per-instance unprivileged explicit request broker with validation-before-resolution and bounded error responses in `sandbox/isolation/credential_request_broker.py`
- [ ] T010 [US1] Implement verified upstream HTTPS connection, DNS/IP pin checks, certificate validation, bearer/API-key header application, and redirect refusal in `sandbox/isolation/credential_upstream.py`
- [ ] T011 [US1] Implement binding-to-egress intersection checks without widening existing default-deny grants in `sandbox/isolation/credential_policy.py`
- [ ] T012 [US1] Implement one-use broker lease transfer and process-lifetime cleanup without placing bytes in argv, environment, unit text, staging paths, or the fixed helper protocol in `sandbox/isolation/credential_resolver.py` and `sandbox/isolation/credential_supervisor.py`
- [ ] T013 [US1] Wire binding, resolver, policy, broker, and proof dependencies through the application context and managed-native lifecycle in `sandbox/application/context.py` and `sandbox/runtimes/managed/adapter.py`
- [ ] T014 [US1] Add managed-native integration coverage for one successful exact request and all pre-upstream near-miss denials in `tests/test_credential_broker_integration.py`

## Phase 4: User Story 2 — Use an approved service without receiving the credential (P1)

**Story goal**: A reviewed workload client can use the broker while hostile
inspection finds no credential on enumerated guest/control/output surfaces.

**Independent test**: Run a fake upstream and hostile probes inside a proof-
qualified managed-native instance; verify bounded responses, no literal or
transformed credential leakage in the declared surfaces, and stable failures for
unsupported/oversized inputs.

- [ ] T015 [P] [US2] Add hostile no-leak probes for environment, argv, mounts, guest files, snapshots, policy/registry/audit records, control channels, and retained output in `tests/test_credential_no_leak.py`
- [ ] T016 [P] [US2] Add bounded request/response, concurrency, cancellation, timeout, unsupported-method/content, duplicate-header, transformed-response reflection, and safe-error tests for the v1 limits in `tests/test_credential_broker_bounds.py`
- [ ] T017 [US2] Document the reviewed guest request client contract, safe response fields, and explicit non-goals in `docs/credential-vault.md`
- [ ] T018 [US2] Integrate one real non-secret first consumer through the explicit broker contract and add its end-to-end test in `sandbox/runtimes/managed/credential_consumer.py` and `tests/test_credential_consumer.py`

## Phase 5: User Story 3 — Revoke, expire, and recover a binding safely (P1)

**Story goal**: Expiry, revoke, restart, cleanup, and audit indeterminate
outcomes fail closed and remain observable.

**Independent test**: Exercise active use, revoke, expiry, broker restart,
machine restart, stale digests, and cleanup; verify new-use refusal, bounded
session closure, `credential_pending` recovery, and no replay after audit error.

- [ ] T019 [P] [US3] Add lifecycle state-machine tests for create, pending, ready, revoke, expiry, blocked, restart, and recovery transitions in `tests/test_credential_lifecycle.py`
- [ ] T020 [US3] Implement monotonic expiry/revoke admission closure and bounded active-session draining in `sandbox/isolation/credential_request_broker.py` and `sandbox/isolation/credential_supervisor.py`
- [ ] T021 [US3] Implement restart reconciliation that enters `credential_pending`, recreates a fresh lease, and re-verifies policy/egress/broker/effective-isolation digests in `sandbox/runtimes/managed/credential_recovery.py`
- [ ] T022 [US3] Add fixed-verb service supervision and cleanup observation for the unprivileged credential broker without passing credential bytes to the root helper in `tools/native-helper/native-helper.py` and `sandbox/runtimes/managed/services.py`
- [ ] T023 [US3] Add audit-safe lifecycle records and indeterminate-outcome handling that never retries a credential-bearing request after an append failure in `sandbox/isolation/credential_audit.py` and `tests/test_credential_audit.py`

## Phase 6: User Story 4 — Verify capability, proof, and lifecycle state (P2)

**Story goal**: Operators and reviewers can distinguish declared support from
effective proof, and missing or drifted gates block the capability.

**Independent test**: Compare reports for proven, unproven, missing-prerequisite,
stale-digest, and drifted-runtime cases; only the proven matching case admits
credential use.

- [ ] T024 [P] [US4] Define secret-free capability/proof report models for support tier, evidence identity, prerequisites, effective observations, digests, binding states, and refusal reasons in `sandbox/isolation/capability_report.py`
- [ ] T025 [US4] Expose capability and binding status through the existing command/manifest extension points in `sandbox/commands/native.py` and `sandbox/commands/manifest.py`
- [ ] T026 [US4] Add pre-start and bounded periodic lifecycle hooks that close credential admission on proof drift without weakening unrelated default-deny network controls in `sandbox/runtimes/managed/adapter.py` and `sandbox/runtimes/managed/credential_health.py`
- [ ] T027 [US4] Add report/refusal/health tests for `implemented_unproven`, missing evidence, drift, stale policy, unsupported runtime, and proven effective state in `tests/test_credential_capability_report.py`

## Phase 7: Polish and cross-cutting acceptance

**Goal**: Verify regression behavior, evidence quality, documentation, and the
release decision without staging unrelated user work.

- [ ] T028 [P] Run the focused resolver, binding, broker, lifecycle, report, secret, and isolation unittest suites and record commands/results in `specs/045-credential-vault-isolation/quickstart.md`
- [ ] T029 Extend the authorized live native acceptance harness with exact binding, hostile no-leak, revoke, restart, exhaustion, cleanup, and timing checks in `tests/live_native_acceptance.py`
- [ ] T030 Update the managed-native capability, isolation, and operator documentation with the explicit refusal boundaries and at-rest residual risk in `docs/native-runtime-isolation.md`, `docs/sandbox-config-reference.md`, and `docs/credential-vault.md`
- [ ] T031 Complete an independent security/source/evidence review of the implementation against `specs/045-credential-vault-isolation/contracts/`, update the evidence ID and support tier only if every predecessor and feature gate passes, and record the decision in `specs/045-credential-vault-isolation/quickstart.md`

## Phase 9: Authorized-proof harness preparation

**Goal**: Make the future authorized Ubuntu 24.04 run for T022 and T029
deterministic, replay-safe, bounded, secret-safe, and independently reviewable.
These IDs are append-only. Nothing in this phase executes a live check, and no
item here changes `implemented_unproven`, `adoptable=false`, or the null
evidence identity.

- [x] T038 Add the versioned acceptance manifest, canonical encoding, plan digest, revision gate, and no-leak scanner in `tests/credential_vault_proof/manifest.py` and `tests/credential_vault_proof/scanner.py`. **DONE:** exact-key schema, bounded strings/lists/files, forbidden-key and secret-shape refusal, digest-stable canonical JSON, and a revision mismatch that refuses before any test action.
- [x] T039 Add the replay-safe proof-run ledger, live probe command model, evidence bundle validator, and cleanup verifier in `tests/credential_vault_proof/ledger.py`, `probes.py`, `bundle.py`, and `cleanup.py`. **DONE:** one request identity per run, ledger-first retry, `acceptance_unknown` for empty/malformed acceptance, cleanup overriding success, allowlisted argv-only probes with bounded redacted parsing, manifest-bound expectation semantics, in-window event times, exact artifact schemas, and a bundle validator that refuses stale, copied, mixed-revision, contradictory, incomplete, or fake-marked evidence.
- [x] T040 Add the offline runner, deterministic report, runbook, and local test suites in `tests/credential_vault_proof/cli.py`, `report.py`, `docs/credential-vault-proof-harness.md`, and `tests/test_credential_vault_*.py`. **DONE:** seven fixed verbs with bounded error codes and no execution path, a report that separates local harness behaviour from live evidence unless a matching bundle was validated, and 125 offline tests that need no Linux, root, systemd, socket, or network access.

The T038-T040 range is reserved for this preparation phase. Any later review
hardening must receive a new follow-up task range on the archive branch; do not
sync this file wholesale with the merge branch because their progress records
diverge.

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

## Implementation strategy

1. **MVP gate**: Complete T003–T014 and prove one exact request with no guest
   credential exposure. If the predecessor proof remains incomplete, stop at
   `implemented_unproven` and do not enable the broker.
2. **Safety increment**: Complete T015–T023 so no-leak, bounded responses,
   revoke, expiry, restart, and cleanup behavior are independently observable.
3. **Operator increment**: Complete T024–T027 so capability/proof status cannot
   overclaim readiness and lifecycle hooks fail closed.
4. **Release gate**: Complete T028–T031 with authorized live evidence and an
   independent review. Transparent MITM, unsupported runtimes, multi-tenancy,
   HA, snapshots, and at-rest encryption remain deferred rather than silently
   entering the MVP.

## Format validation

All implementation tasks use the required `- [ ] T###` checklist form. Story
tasks carry `[US1]`–`[US4]`; setup, foundational, and polish tasks do not. Every
task names at least one exact repository path.
