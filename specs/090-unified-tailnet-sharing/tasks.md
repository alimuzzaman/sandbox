# Tasks: Unified tailnet sharing

**Input**: Reviewed PRD, spec, plan, research, data model and contracts/sharing.md.
**Execution**: Source definitions/implementation only in this owner checkout. Root runs
all focused tests with synthetic captured environments and <=60s per-command bounds.
No helper, runtime, credential, SDK release or live forwarding authority is implied.
Unchecked means incomplete; no task marks installed acceptance from source progress.

## Phase 1: Setup

- [ ] T001 Reconcile approved receiver-owner policy and requirement trace in specs/090-unified-tailnet-sharing/contracts/sharing.md; preserve human release-review disclosure.
- [ ] T002 Record the real authentication dependency/factory ownership and unavailable-capability behavior in specs/090-unified-tailnet-sharing/research.md; do not copy sibling code or merge without authority.

## Phase 2: Foundational target and actor boundaries

- [ ] T003 Define immutable canonical URL/receiver/node/scope/policy types and fixed refusals in sandbox/hosting/share/tailnet_target.py.
- [ ] T004 Define positive/negative parsing, secrecy and no-public/LAN/metadata target regressions in tests/test_share_tailnet_target.py before implementing parsing; execution is root-owned.
- [ ] T005 Implement strict normalization and node/address binding in sandbox/hosting/share/tailnet_target.py, preserving original Host/TLS identity and private query handling.
- [ ] T006 Define genuine current receipt, forged actor, policy revision drift and no-serialization regressions in tests/test_share_tailnet_actor.py, using the actual ControlGate source dependency after approved integration.
- [ ] T007 Implement the real receipt/policy adapter in sandbox/hosting/share/tailnet_actor.py using require_authenticated/current revalidation; missing dependency refuses, never generates a fake actor.
- [ ] T008 Define bounded trusted-daemon frame, multiple-address, stale/replacement and receiver connectivity regressions in tests/test_share_tailnet_observer.py.
- [ ] T009 Implement bounded receiver observation and pinned non-HTTP connectivity checks in sandbox/hosting/share/tailnet_observer.py, with no raw secret-link probes or parent environment copying.

## Phase 3: US1 - Canonical existing selection (P1)

Goal: Existing stack/recorded entry sharing and root-owned wrapper retain one lifecycle.
Independent root check: existing create/list/revoke/wrapper and 088 recovery positives/negatives.

- [ ] T010 [US1] Define canonical target-union compatibility regressions in tests/test_host_share.py without changing existing target-kind use policies.
- [ ] T011 [US1] Adapt selection in sandbox/commands/host_share.py to a closed target union; preserve current stack/recorded transport and companion fences.
- [ ] T012 [US1] Keep scripts/share.sh and docs/unified-sharing.md integration owner-controlled, forwarding only implemented canonical selections; no URL option until readiness.

## Phase 4: US2 - Verified owner-network URL delegation (P1)

Goal: Genuine current owner admission delegates receiver network scope through canonical issuance.
Independent root check: actual receipt/handler/management boundary and receiver proof, not caller dictionaries.

- [ ] T013 [US2] Define closed request, actual request.state receipt and management failure-boundary regressions in tests/test_share_tailnet_transport.py.
- [ ] T014 [US2] Implement the real authenticated handler adapter in sandbox/hosting/share/tailnet_transport.py; reject client actor/policy overrides and revalidate before effects/publication.
- [ ] T015 [US2] Integrate the genuine middleware/handler/factory in mcp/wp-server/server.py through that file's integration owner, preserving all existing authentication and route protection.
- [ ] T016 [US2] Define versioned target-aware canonical mint/store tests in tests/test_share_gate_runtime.py, proving exact prepared IDs and no extra issuer/remint after unknown effects.
- [ ] T017 [US2] Extend owned canonical grant schema/storage/management in sandbox/hosting/login_service/runtime.py and sandbox/hosting/share/__init__.py with immutable private target and actor/policy bindings.
- [ ] T018 [US2] Implement sensitive foreground URL input through canonical transport in sandbox/commands/host_share.py, refusing query-bearing argv and unsupported capability before effects.
- [ ] T019 [US2] Bind installed origin profile and actual observer/connector/actor ports in the existing gate factory in sandbox/hosting/login_service/runtime.py; do not advertise URL capability from types alone.

## Phase 5: US4 - Durable lifetime/recovery (P1)

Goal: Finite admission and original-identity cleanup survive uncertainty without unrelated effects.
Independent root check: concurrent allowance, lost replies, policy/target replacement and stream closure.

- [ ] T020 [US4] Define unknown-acceptance/restart/replacement/current-policy and per-actor scoped cleanup regressions in tests/test_share_tailnet_recovery.py.
- [ ] T021 [US4] Extend owned durable descriptors/state transitions in sandbox/hosting/share/attempts.py and canonical recovery orchestration in sandbox/hosting/share/fanout.py without raw private URLs or receipt serialization.
- [ ] T022 [US4] Implement finite URL admissions, policy/expiry checks and closeable connection lease revocation in sandbox/hosting/login_service/runtime.py; incomplete cessation remains cleanup_pending.
- [ ] T023 [US4] Preserve bounded safe URL list/revoke/error projections in sandbox/commands/host_share.py and sandbox/commands/ui_dash.py, including nonzero partial CLI status and original IDs.

## Phase 6: US3 - Supported browser behavior (P2)

Goal: Actual receiver serves scoped apps without app-auth or sibling-authority bypass.
Independent root check: real scoped HTTP/HTTPS/assets/forms/cookies/redirect/WebSocket matrix.

- [ ] T024 [US3] Define scope, Host/TLS pinning, cookie/domain, external redirect, secret query and streaming regressions in tests/test_share_tailnet_proxy.py.
- [ ] T025 [US3] Implement per-grant origin dispatch and guarded HTTP/HTTPS/WebSocket forwarding in sandbox/hosting/login_service/runtime.py through trusted pinned target/connection ports.
- [ ] T026 [US3] Define compatibility/readiness projections and refusals in tests/test_web_share.py; preserve existing stack UI behavior and honest unsupported application states.
- [ ] T027 [US3] Integrate safe capability/origin/profile status in sandbox/commands/ui_dash.py and src/web/src/pages/share.ts only after server contracts are present; generated bundle rebuild is root-owned if source changes require it.

## Phase 7: Cross-cutting gates

- [ ] T028 Record exact owned source commits, missing capabilities, counts/logs and unresolved review gaps in specs/090-unified-tailnet-sharing/quickstart.md and task handback; no parent progress messages before work is ready.
- [ ] T029 Obtain root-run <=60s source/shared-boundary shards covering tests/test_share_tailnet_*.py and existing 088 target/claim/fanout/panel paths; no full/soak suite or repeated build.
- [ ] T030 Obtain fresh independent personal Sol high source/auth review of the exact complete 088/090 integration, including receiver-owner delegation, in specs/090-unified-tailnet-sharing/quickstart.md evidence references; no release claimed from PRD PASS.
- [ ] T031 Complete separately authorized real receiver/non-tailnet browser/stream/cleanup acceptance from specs/090-unified-tailnet-sharing/quickstart.md, preserving neighbors and parked-login constraints.
- [ ] T032 Obtain mandatory human authentication/release approval for receiver-owner bearer delegation and exact installed capability before any URL enablement; retain approval evidence in the final handback, not a fabricated READY response.

## Dependencies and execution

T001-T009 precede generic admission. US1 can preserve existing behavior independently.
US2 depends on foundational real actor/observer types and integration-owner T015.
US4 depends on canonical target schema/issuance; US3 depends on live grant/lease semantics.
No URL capability is enabled before US2+US4+US3, current root checks and required reviews.
T030-T032 are release/acceptance gates, not authority supplied by this task list.

Potential parallel source work: target types/tests, actor adapter and receiver observer
have separate owned modules after policy is fixed. Test execution remains serialized
under root; no new helper permission is implied. Shared runtime/UI files have one
integration owner and must not be edited concurrently.

## Implementation strategy

Preserve existing canonical paths first, then implement the complete target/actor/
receiver foundations. Add one canonical management/storage flow, lifecycle fencing
and guarded forwarding, then integrate actual receiver profiles. Do not substitute
an unavailable parser-only seam for the requested finished URL-sharing feature.
Source milestones remain incomplete until root-run regression and real acceptance
evidence cover the full requested scope. No task is checked on generation.
