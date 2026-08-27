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

T038 [accepted] -> T039 [complete] -> T040 [accepted] -> T041 [accepted] -> T042 [accepted] -> T043 [accepted]
                                                            |    |    |
                                                            v    v    v
                                                           T022 T029 T031

T003 + T022 + T037 + T043 -> T029 -> T031
```

US1 depends on the foundational contracts and proof gate. US2 depends on the
explicit broker from US1. US3 depends on US1 and the durable binding state. US4
may develop its report model in parallel with US1 but must gate enablement and
release. Existing task IDs remain stable, so the later-added T032-T043 IDs are
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
- [ ] T022 [US3] Add fixed-verb service supervision and cleanup observation for the unprivileged credential broker without passing credential bytes to the root helper in `tools/native-helper/native-helper.py` and `sandbox/runtimes/managed/services.py` (BLOCKED: requires T032-T036, completed v2 convergence through T043, and authorized Ubuntu root-helper/service lifecycle proof; current v1 endpoint/coordinator seams are fake/local-only and no local seam may be promoted as T022 completion)
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
- [ ] T029 Extend the authorized live native acceptance harness with exact binding, hostile no-leak, revoke, restart, exhaustion, cleanup, and timing checks in `tests/live_native_acceptance.py` (BLOCKED: requires T003, T022, T037, completed v2 convergence through T043, and an authorized Ubuntu 24.04 host; offline harness tests are preparation only)
- [x] T030 Update the managed-native capability, isolation, and operator documentation with the explicit refusal boundaries and at-rest residual risk in `docs/native-runtime-isolation.md`, `docs/sandbox-config-reference.md`, and `docs/credential-vault.md`. **DONE:** status command, unsupported runtimes, exact request boundary, residual at-rest risk, and proof-gated refusal rules are documented.
- [ ] T031 Complete an independent security/source/evidence review of the implementation against `specs/045-credential-vault-isolation/contracts/`, update the evidence ID and support tier only if every predecessor and feature gate passes, and record the decision in `specs/045-credential-vault-isolation/quickstart.md` (BLOCKED: requires T003, T022, T029, completed v2 convergence through T043, a clean exact source revision, live feature evidence, and independent human review; support remains `implemented_unproven`/`adoptable=false` with null evidence ID)

## Phase 8: Preparatory work to unblock T022 and T029

**Goal**: Prepare the standalone service, instance-bound transport, cleanup, and
public acceptance seams locally without enabling credential use or claiming live
proof. These IDs are append-only to preserve references to T022/T029/T031.

- [x] T032 [US3] Define the pre-implementation standalone service, instance-bound guest transport, trusted one-use lease channel, fixed helper verbs, and cleanup invariants in `specs/045-credential-vault-isolation/contracts/credential-broker-service-v1.md`. **DONE:** the contract records required boundaries and explicit refusals; it does not select an unreviewed lease mechanism, enable a runtime path, or change evidence/support state.
- [x] T033 [US3] Complete an independent security design review that selects one concrete trusted one-use lease mechanism, reconciles the FR-008/SC-002 control-channel wording through `specs/045-credential-vault-isolation/spec.md` clarification if required, and records accepted peer authentication, descriptor/socket ownership, replay refusal, and no-secret surfaces in `specs/045-credential-vault-isolation/contracts/credential-broker-service-v1.md`. **DONE:** selected one sealed anonymous `memfd` transferred once with `SCM_RIGHTS` over a broker-owned abstract `AF_UNIX` `SOCK_SEQPACKET` socket; kernel peer checks, exact broker-process verification, terminal consumption, cleanup, residual trusted-owner assumption, and all no-secret surfaces are explicit. This design review is local only and does not satisfy T031.
- [x] T034 [US3] Add offline standalone service and transport contract tests, including cross-instance denial, broker-epoch rotation, lease one-use, bounded status, and argv/environment/unit/config/output no-leak checks, in `tests/test_credential_broker_service_contract.py`. **DONE:** 16 fake/local tests pass; they exercise validation, frame bounds, private-veth and peer gates, descriptor cleanup, terminal acknowledgements, lifecycle limits, and no-secret surfaces. They do not open real sockets or prove Linux isolation.
- [ ] T035 [US3] Implement the reviewed unprivileged standalone broker executable and its instance-bound request/trusted-lease endpoints in `tools/native-helper/native-credential-broker.py`, then satisfy `tests/test_credential_broker_service_contract.py` without adding a default or adoptable runtime path. **FOUNDATION AND EXACT LEASE ENDPOINT ADDED; INTEGRATION STILL OPEN (LOCAL ONLY):** the strict SBG2/SBR2 and typed-effect foundation remains closed. The broker now derives and arms the reviewed per-operation 93-byte abstract v2 lease address before `AUTHORIZED_V2`; the controller derives it independently and makes one one-second connection; listener tests cover collision, exact peer/ancillary rules, all-rights prescan, one-use ownership, and exact cleanup. Reciprocal configs pin the immutable lease endpoint registry digest and bounds without changing the 732-byte lease or 444-byte ACK. T035 still requires the executable-owned real guest listener and continuous controller/guest/lease disconnect/deadline and lifecycle/audit loop. This local work is not T022/T029 Ubuntu 24.04 kernel/systemd evidence or T031 review. T035 stays incomplete; v1 fallback, default activation, support promotion, and live claims remain forbidden.
- [ ] T036 [US3] Add secret-free digest-bound broker plans, fixed helper lifecycle verbs, broker-first cleanup observation/order, and inert dependency wiring in `sandbox/runtimes/managed/services.py`, `tools/native-helper/native-helper.py`, `sandbox/runtimes/managed/adapter.py`, and `sandbox/application/context.py`, with local coverage in `tests/test_managed_services.py`, `tests/test_native_cleanup_observation.py`, and `tests/test_credential_wiring.py` (LOCAL ONLY: the plan compiler, fixed supervisor argv/status schema, broker-first cleanup seam, and inert composition exist. The helper verbs deliberately refuse because reviewed unit/config installation and ownership observation are not integrated; T036 remains open and T022 remains blocked.)
- [x] T037 [US1] Add a proof-gated public `./sb` acceptance surface that accepts only opaque source references and exact non-secret binding/request/revoke metadata in `sandbox/commands/native.py`, then add offline public-command and harness coverage in `tests/test_native_cli.py` and `tests/test_live_native_acceptance_harness.py`. **LOCALLY COMPLETE; INDEPENDENTLY ACCEPTED BY SOL HIGH (LOCAL ONLY):** the exact tagged codec and public projector now consume one authenticated T040 `public_acceptance` receipt and pin the same-session T041 operation and T042 lifecycle authorities. Controller-process interfaces provide exact current status, binding/CAS, egress, and bind/request/revoke operations without exposing repository, source, protocol, audit, or credential mechanisms. One lifecycle-authority-minted per-action receipt provides a fresh post-action generation/state check; a locked authority-owned reservation registry admits at most sixteen concurrent requests using observed active count plus outstanding reservations and releases each receipt exactly once on every terminal/cleanup path. Bind requires zero active operations, while revoke can close active use only through a drained exact QUIESCE acknowledgement. Every egress scope field and digest matches the binding projection, and success/refusal reason allowlists are disjoint. Stale/mismatched/v1/unknown/unavailable paths are bounded refusals; action uncertainty stays indeterminate and is not retried. Revoke is not blocked on egress health. Default unsealed or absent-service composition remains closed, support stays `implemented_unproven`/`adoptable=false` with null evidence, and offline tests are not T022/T029/T031 live evidence.

## Phase 9: Authorized-proof harness preparation

**Goal**: Make the future authorized Ubuntu 24.04 run for T022 and T029
deterministic, replay-safe, bounded, secret-safe, and independently reviewable.
These IDs are append-only. Nothing in this phase executes a live check, and no
item here changes `implemented_unproven`, `adoptable=false`, or the null
evidence identity.

- [x] T038 Add the versioned acceptance manifest, canonical encoding, plan digest, revision gate, and no-leak scanner in `tests/credential_vault_proof/manifest.py` and `tests/credential_vault_proof/scanner.py`. **DONE:** exact-key schema, bounded strings/lists/files, forbidden-key and secret-shape refusal, digest-stable canonical JSON, and a revision mismatch that refuses before any test action.
- [x] T039 Add the replay-safe proof-run ledger, live probe command model, evidence bundle validator, and cleanup verifier in `tests/credential_vault_proof/ledger.py`, `probes.py`, `bundle.py`, and `cleanup.py`. **DONE:** one request identity per run, ledger-first retry, `acceptance_unknown` for empty/malformed acceptance, cleanup overriding success, allowlisted argv-only probes with bounded redacted parsing, and a bundle validator that refuses stale, copied, mixed-revision, contradictory, incomplete, or fake-marked evidence.
- [x] T040 Add the offline runner, deterministic report, runbook, and local test suites in `tests/credential_vault_proof/cli.py`, `report.py`, `docs/credential-vault-proof-harness.md`, and `tests/test_credential_vault_*.py`. **DONE:** seven fixed verbs with bounded error codes and no execution path, a report that separates local harness behaviour from live evidence, and 107 offline tests that need no Linux, root, systemd, socket, or network access.

## Phase 10: Controller authority v2 replacement

**Goal**: Replace the historical v1 controller/lease design with one persistent
per-machine controller authority and a strict, independently enforced v2
protocol. These local tasks do not close the live or human-review gates.

- [x] T038 [US3] Define the sole controller authority, broker enforcement trust boundary, exact non-downgradable controller/audit/lease v2 schemas, authorization digest, operation state machine, replay/rotation/restart rules, module/config ownership, lifecycle, no-secret surfaces, and review gate in `specs/045-credential-vault-isolation/contracts/credential-broker-controller-authority-v2.md`; mark v1 as superseded and add independently pinned mutation/schema-table contract coverage in `tests/test_credential_broker_controller_authority_contract.py`. **DONE:** independent Sol High review accepted the revised local contract, including mutual handshake, lifecycle and same-socket lease acknowledgements, schema/temporal bounds, binary layouts, audit semantic replay, sealed-proof expectations, response-confinement limits, and v1/v2 dependency corrections. This accepts the local design only and does not satisfy the T031 human release/evidence review.
- [x] T039 [US3] Implement one shared exact controller/lease v2 codec and fixed allowlists in a new deep isolation protocol module, including canonical encoding, exact-key/size/type bounds, authorization digest, v1/unknown-version refusal, and mutation/replay tests; do not wire a runtime path. **DONE:** `sandbox/isolation/credential_controller_protocol_v2.py` freezes the reviewed registry and digest, validates canonical controller JSON and injected-time bounds, encodes/decodes the exact 732-byte lease and 444-byte same-socket ACK, and provides bounded directional/lease/authorization replay state. Exact operation IDs remain tombstoned for the full epoch pair; the combined 16-ID bound intentionally fails closed after 16 total operations until a genuinely changed epoch pair constructs its one new registry. Each registry is immutable-pinned to machine, both epochs, and authenticated connection owner. Focused golden-vector, mixed-identity, bounded-constructor, malformed-input, temporal rollback, mutation, boundary, state, 1,000-attempt boundedness, and no-I/O tests pass without runtime wiring.
- [x] T040 [US3] Implement the persistent per-machine controller service and authenticated broker connection with injected local kernel observers, exact UID/GID/PID-start/executable/unit checks, independent directional sequences, epoch rotation, disconnect terminalization, and closed startup; keep repository/source/proof/egress/audit ownership out of the broker. Enforce exactly one T039 authorization registry per authenticated connection and machine/epoch pair; never reset, replace, or reconstruct it while that pair lives, including after the 16-operation capacity closes it. **ACCEPTED BY INDEPENDENT SOL HIGH (LOCAL ONLY):** the inert implementation pre-scans all ancillary records, reports rights-cleanup failure, transfers each socket to exactly one idempotent owner before guarded handshake work, pins one rollback observer per connection, and requires mandatory `SO_PASSCRED` plus a zero-argument current-process reader with start/observe/start comparison to the exact sealed config. Injected epoch/clock/registry/socket/observer failures become fixed bounded codes; direct packet-observer failures are secret-free, and the first terminal cleanup failure survives every repeated service/session/connection/listener operation without an unsafe retry. Exact registry-disconnect failure survives permanent quiesce and top-level listener cleanup. Thirty-one focused injected tests pass. No application composition, public command, managed activation, helper verb, repository/source/proof/egress decision, lease, adapter, or durable-audit path exists. T022/T029/T031 and Ubuntu/systemd/kernel evidence remain blocked.
- [x] T041 [US3] Implement the broker's exact operation-bound v2 authorization state machine and controller's decision/lease dispatch, including the fixed `authorization_bearer`/`x_api_key` allowlist, authorization digest, `AUTHORIZED_V2` gate, exact lease binding, atomic one-use consumption, expiry/revoke/quiesce refusal, and no v1 fallback. **ACCEPTED BY INDEPENDENT SOL HIGH (LOCAL ONLY):** `credential_controller_authority_v2.py` exposes only eight injected controller authorities, sends and records its own bounded `CLAIM_NEXT_V2`, evaluates only the exact one-use `CLAIMED_V2` reply accepted by that session, resolves only after the corresponding exact v2 authorization acknowledgement, and performs one capped 732-byte/one-sealed-memfd dispatch with wipe/close cleanup. The broker creates its private 16-total-operation/tombstone registry only after canonical guest and machine validation, preserves the original guest receipt privately for the real `CLAIMED_V2` temporal proof, keeps one exact request/binding claim anchor connection-local, emits only exact secret-free claimed/no-pending projections, terminalizes that recorded claim on every identifiable authorization or refusal mismatch without trusting a crossed operation ID or touching unrelated operations, recomputes the authorization digest and every request/binding/sealed identity field before insertion, sends `AUTHORIZED_V2` before lease eligibility, consumes the endpoint-bound authorization before descriptor inspection, admits exactly one race winner, and stops at `lease_bound`. A central terminal guard makes activation expiry, malformed delivery, expiry, refusal, revoke, irreversible epoch quiesce, disconnect, descriptor/sequence/capacity/injected failure all fail closed with bounded first-sticky codes, immediate pinned-registry disconnect, and exhaustive exact-owned cleanup. T041 itself added no lifecycle wire handling, v1 fallback, public wiring, managed activation, application composition, upstream, or live-proof claim.
- [x] T042 [US3] Implement the persistent PRE/POST/ACK audit flow, durable idempotent controller audit authority, effect-certainty/indeterminate rules, secret-free projections, derived config ownership, and controller-first/broker-second start plus broker-first/controller-second stop lifecycle with bounded cleanup observation. **ACCEPTED BY INDEPENDENT SOL HIGH (LOCAL ONLY):** `credential_controller_audit_v2.py` owns exact semantic fingerprints, durable secret-free PRE/POST tombstones, identical replay/same-commit behavior, conflicting replay refusal, and crash recovery of an unclosed PRE before activation. The broker uses one typed injected effect executor only after durable PRE acknowledgement, permits one transport retry inside the one-second audit bound, never retries after effect entry, commits POST before one exact 444-byte acknowledgement on the T040-session-issued one-use authenticated lease connection, closes that connection exactly once on every terminal path, and records possible/completed indeterminacy when certainty is lost. The controller's outbound lease socket is likewise kernel-peer observed, session-registered, one-use, and closed independently of descriptor cleanup; caller exchange/close callbacks are not transport authority. `credential_controller_lifecycle_v2.py` derives immutable canonical secret-free controller/broker config plans with reciprocal executable/config and all shared sealed-expectation checks, verifies no-follow root/group ownership observations, exposes only fixed v2 lifecycle verbs, and proves injected controller-first start, broker-first stop authorized by one opaque session/epoch/plan-bound QUIESCE receipt, and exhaustive detailed exact-owned cleanup observation. Exact ACTIVATE/QUIESCE digests and terminal acknowledgements replace production-style admission injection; `set_admission_v2` remains explicitly test-only for T041. Sol High additionally verified session-owned kernel-peer lease sockets, exact one-use opaque socket receipts, reciprocal plan checks, quiesce binding, sticky cleanup, and 214 focused/adjacent tests. No public/application/default/upstream composition, v1 fallback, support promotion, live proof, or adoption was added. T042 convergence is complete locally; T022/T029/T031 gates remain open.
- [x] T043 [US3] Add offline end-to-end v2 controller/broker tests and converge the current fake/local-only v1 endpoint/coordinator classes and remaining T035-T037 seams on v2, then run the full local credential/native/isolation regression matrix. **LOCALLY COMPLETE; INDEPENDENTLY ACCEPTED BY SOL HIGH:** `credential_controller_integration_v2.py` owns one inert graph using the actual T040 controller/broker handshake sessions, exact session-sequenced T042 ACTIVATE/QUIESCE, guest submission, controller-owned CLAIM_NEXT and received CLAIMED, the injected T041 authorities, received AUTHORIZE/AUTHORIZED, a controller-session-owned authenticated lease socket, broker endpoint and sealed descriptor, real durable controller PRE/POST authority over the same control transport, one typed effect, exact 444-byte same-socket acknowledgement, terminal guest projection, opaque quiesce receipt, fixed-verb managed reverse stop, and exact-once cleanup. It never calls `set_admission_v2` or fabricates an audit receiver. Connected negatives cover peer/packet/rights identity, reconnect/epochs, v1/unknown, claim/authorization/lease replay and cross-binding, expiry/quiesce/revoke races, the 16-operation ceiling, audit retry/PRE failure/crash recovery/POST uncertainty, disconnect, descriptor failure, and idempotent cleanup. Guest composition requires a broker-minted frozen one-use receipt bound to the exact broker object/type/private nonce/purpose/machine/epochs/config; spoof, replay, wrong session, controller receipt, and epoch drift refuse. The production v1 broker factory and consumer are fixed refusals, while historical behavior lives only in a test helper. The connected graph exposed and closed pending-as-indeterminate guest projection and unreachable audit-retry transport paths. Fresh credential, native, managed, isolation, CLI, config, contract, compile, and diff matrices pass; exact counts belong in the run report rather than this durable task contract. T037 is now locally complete and review-pending; T035-T036 remain unfinished. T022, T029, and T031 remain blocked; support remains `implemented_unproven`, `adoptable=false`, and `evidence_id=null`.

T043 threat model: controller/application Python processes are trusted. Exact
bridge/capability types and receipts prevent ordinary public-API laundering,
not arbitrary same-process reflection or monkeypatching; those are process
compromise and out of scope. The untrusted guest never receives the bridge or
executes Python in those processes. Its enforced boundary is the authenticated
cross-process socket and exact data-only request schema, which cannot select
imports, callbacks, Python objects, controller paths, validators, clocks,
sessions, or legacy handlers.

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

T003 + T022 + T037 + T043 -> T029 (authorized live feature matrix)
T003 + T022 + T029 + T043 -> T031 (independent final evidence/support review)
```

The production controller-authority replacement follows this strict local
order before any unfinished service seam can be called production-ready:

```text
T038 (exact v2 contract; accepted)
  -> T039 (exact codecs; complete)
  -> T040 (persistent controller and authenticated connection)
  -> T041 (authorization state and lease v2)
  -> T042 (persistent audit and lifecycle)
  -> T043 (offline integration and v1 convergence; precedes T022/T029/T031)
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
5. **Blocked-service increment**: Complete T032-T043 as local preparation, then
   return to T022 for authorized helper/service proof and T029 for the live
   feature matrix. Local preparation never changes the support tier or evidence
   identity.

## Format validation

All implementation tasks use the required `- [ ] T###` checklist form. Story
tasks carry `[US1]`–`[US4]`; setup, foundational, and polish tasks do not. Every
task names at least one exact repository path.
