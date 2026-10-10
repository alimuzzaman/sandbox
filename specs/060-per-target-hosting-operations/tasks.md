# Tasks: Per-Target Hosting Operations

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/coordination.md, quickstart.md
**Tests**: required (tests first within each story).
**Coordination**: hosting/delivery/remote modules are shared with the host-apply observability work; land after its open changes. Shared conversion and fixtures with 062 are owned here.

## Phase 1: Setup

- [x] T001 Create packages `sandbox/hosting/coordination/__init__.py` and `sandbox/hosting/state_partition/__init__.py`; register remote store and controller per-target state through the state contract checked by `tests/test_architecture_boundaries.py`
- [x] T002 [P] Two-controller test harness (two temp `SANDBOX_HOME`s, local `sh -c` remote stand-in sharing one remote home, `multiprocessing` runners) in `tests/hosting_coordination_support.py`

## Phase 2: Foundational

- [x] T003 [P] Program tests run for real against a temp remote home: admit/renew/release, FIFO queue with deadlines, fencing token monotonic, expiry with phase → `fenced_pending_cessation`, hold claim/renew/release/break, build slots, shared lease 60 s, list bounds, read-only list creates nothing, modes 0700/0600, capability marker absent → typed refusal in `tests/test_hosting_coordination_program.py`
- [x] T004 Implement `sandbox/hosting/coordination/program.py`
- [x] T005 [P] Client tests: 15 s bound, invalid output rejected, SSH failure/timeout/missing marker → `lease_authority_unavailable` with migrate/repin remedy, no secret in errors in `tests/test_hosting_coordination_client.py`
- [x] T006 Implement `sandbox/hosting/coordination/client.py`; mark `hosting_coordination` capability in `remote service migrate`
- [ ] T007 [P] Per-target state tests: per-file read/write, corrupt file affects only its target, `iter_target_states` skips with per-target error, parity of record contents with today's `hosts.json` on 051/054 fixtures in `tests/test_hosting_state_partition.py`
- [ ] T008 Implement `sandbox/hosting/state_partition/layout.py` and accessors in `sandbox/core/_hosting.py`; move callers off `load_host_state`/`save_host_state`; narrow `RecoveryRepository.state_lock` to conversion/legacy use
- [ ] T009 Add program payloads to `SHAPE_SOURCES`, bump `CONTROL_PROTOCOL_SPOKEN` (coordinate number with 063 T006), re-record `control_shapes.json`

## Phase 3: US1 Unrelated projects deploy at the same time (P1)

- [ ] T010 [P] [US1] Concurrency tests: two targets on one remote and one controller to two remotes run concurrently; no controller-wide lock held across build/transfer/delivery/verification (instrumented lock tracer); login-url on idle target returns while another applies in `tests/test_hosting_concurrency.py`
- [ ] T011 [US1] Implement `sandbox/hosting/coordination/lease.py` (TargetLease, renewal thread, lost flag, token passing)
- [ ] T012 [US1] Route every target mutation in `sandbox/commands/hosting.py` (apply, sync, login-url, edge-continue, recover, retire-delivery, image stage/provision/activate/adopt/rollback/recover/settle) through TargetLease; registration lock only around registration reads
- [ ] T013 [P] [US1] Shared-lease tests: edge/DNS/ingress reload only, 60 s bound, never across build or verification, rollback re-acquires in `tests/test_hosting_concurrency.py`
- [ ] T014 [US1] Implement `sandbox/hosting/coordination/shared.py` (064 seam) and wrap edge, DNS and ingress reload steps in `sandbox/commands/hosting.py`

## Phase 4: US2 Same-target serialization with caller-chosen wait (P1)

- [ ] T015 [P] [US2] Tests: `--wait` range and default, 0 refuses within 2 s, waiting reports holder within 2 s, start within 5 s of release, FIFO, interrupted waiter leaves no entry, two controllers never overlap in `tests/test_hosting_target_lease.py`
- [ ] T016 [US2] Implement wait/no-wait admission loop and progress lines; `--wait` flag and `--lock-wait` alias in `sandbox/cli.py`; MCP `wait_seconds`
- [ ] T017 [P] [US2] Retained refusal tests: busy/cap/authority/mixed/predecessor refusals appear in `sb delivery inspect` with holder identity, no generation advance, nothing to retire in `tests/test_hosting_retained_refusals.py`
- [ ] T018 [US2] Record refusals through `sandbox/delivery/admission.py` pre-admission recorder

## Phase 5: US3 Uncertain predecessors keep the target fenced (P1)

- [ ] T019 [P] [US3] Tests: lost lease → no forward effects, `effect_unknown` (`lease_lost`); stale token refused by remote phase launcher; expired holder with running phase → `predecessor_phase_running` from every controller; ceased phase still refused by 054/062 fences; Compose-entered terminal failure stays fenced; interrupted target leaves others byte-identical in `tests/test_hosting_target_lease.py`
- [ ] T020 [US3] `phase-report` calls around each remote phase; token check in the remote phase launcher (`sandbox/transports/remote_*` delivery entry); cessation probe in admit; `effect_unknown` recording on lost lease

## Phase 6: US4 Explicit holds (P2)

- [ ] T021 [P] [US4] Tests for every hold rule (default 1 h, >4 h refused, renew cap with remaining allowance, new identity after expiry, same-controller session without id refused, `SANDBOX_HOLD_ID`, break requires reason and records breaker, expiry removes only hold) in `tests/test_hosting_holds.py`
- [ ] T022 [US4] Implement `sandbox/hosting/coordination/holds.py`; `host hold claim|renew|release` in `sandbox/cli.py` and MCP `host_hold`

## Phase 7: US5 Operator listing (P2)

- [ ] T023 [P] [US5] Listing tests: four states, bounds, same output from two controllers, secret-free, CLI/MCP parity in `tests/test_hosting_coordination_program.py`
- [ ] T024 [US5] Implement `sandbox/hosting/coordination/listing.py`; `host operations` CLI and MCP `host_operations`

## Phase 8: US6 Build cap (P2)

- [ ] T025 [P] [US6] Tests: third build never starts with cap 2, wait shows cap and holders, no-wait refusal retained, cap change requires `--confirm` and records who/when, controller record never overrides in `tests/test_hosting_build_cap.py`
- [ ] T026 [US6] Build slot acquire/release around the build phase in `sandbox/commands/hosting.py`; `remote build-cap` in `sandbox/commands/remote.py`, `sandbox/cli.py`, MCP

## Phase 9: US7 Upgrade safety (P2)

- [ ] T027 [P] [US7] Conversion tests on 051/052/054 + 062 fixtures: every outcome/receipt/generation queryable with same request id; interrupted conversion resumes; mixed state refuses on this and on a second unconverted controller; older controller gets `protocol_too_old` with zero writes in `tests/test_hosting_conversion.py`
- [ ] T028 [US7] Implement `sandbox/hosting/state_partition/conversion.py` including the 062 scope-key step; `host convert-state` CLI/MCP; mixed-state guard in every hosting mutation entry

## Phase 10: Polish

- [ ] T029 Reclaim yields to shared lease and edge/pool locks (`host_reclaim_busy`) in `sandbox/resources/`; test in `tests/test_hosting_concurrency.py`
- [ ] T030 Docs: rewrite lock section of `docs/remote-hosting.md` (per target, remote authority, waits, holds, cap, conversion), CLAUDE.md gotcha, CHANGELOG.md, MCP tool docs
- [ ] T031 Run `./sb selftest` and the architecture test
- [ ] T032 Live proof per quickstart on a disposable remote; `xcloud-london` after the remote install protocol and owner approval

## Dependencies

Phase 2 before all stories. T008 before T012. T011 before T012, T016, T020, T022. T014 provides 064's lease seam. T028 must ship before any remote is marked coordination-enabled in production. US4-US6 depend on US1 lease; US7 depends on T004/T008.

## Parallel examples

- T002, T003, T005, T007 together.
- T010, T013, T015, T017, T019 written together after T011.
- T021, T023, T025, T027 together.

## MVP

US1 + US2 + US3 (T001-T020) with T027/T028 conversion: concurrent unrelated deploys, serialized same-target deploys across controllers, fenced uncertainty.
