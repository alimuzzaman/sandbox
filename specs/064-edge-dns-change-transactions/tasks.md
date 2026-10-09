# Tasks: Transactional Edge and DNS Changes

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/edge-transaction.md, quickstart.md
**Tests**: required (tests first within each story).

## Phase 1: Setup

- [ ] T001 Create package `sandbox/edge_txn/__init__.py` with `EdgeTxnError(code, data)`; register the package and the controller journal state root wherever `tests/test_architecture_boundaries.py` and the state contract require
- [ ] T002 [P] Fake provider (records with comment/read_only/proxied/ttl/modified_on, per-call failure injection, zone SSL setting, nameservers) and fake edge/DNS servers (local UDP DNS answering A/AAAA; HTTPS stub with 530-then-200, 404, 401 and origin-certificate modes) in `tests/edge_txn_fakes.py`

## Phase 2: Foundational

- [ ] T003 [P] Tests for full-field listing, create/update/delete by id, nameservers and marker comment in `tests/test_cloudflare_client.py`
- [ ] T004 Extend `sandbox/core/_cloudflare.py` with `list_records`, `create_record`, `update_record`, `delete_record`, `zone_nameservers`; marker `managed by Sandbox hosting; target=<project>/<environment>`
- [ ] T005 [P] Parity tests running today's apply scenarios (declared-only updates, DNS-only TTL 60, SSL refusal without flag, redirect CNAME proxied flip, Caddy/nginx restore order) against the current path, recording provider end state, in `tests/test_edge_txn_hosting_parity.py`
- [ ] T006 [P] Journal tests: fsync per entry, 0700/0600 modes, interrupted detection (no `end`), leftovers computation, 512-entry bound, no secret-shaped values in `tests/test_edge_txn_journal.py`
- [ ] T007 Implement `sandbox/edge_txn/journal.py`
- [ ] T008 Lease seam (`acquire(bound_s)`, `release()`) backed by the existing host-global edge lock in `sandbox/edge_txn/executor.py`

## Phase 3: US1 Plan shows ownership before any change (P1)

- [ ] T009 [P] [US1] Desired-state tests: IPv4-only origin yields AAAA absent, DNS-only TTL 60, proxied TTL auto, wildcard → `skipped_wildcard`, redirect target in `tests/test_edge_txn_desired.py`
- [ ] T010 [US1] Implement `sandbox/edge_txn/desired.py`
- [ ] T011 [P] [US1] Classification tests for every rule in research R2 (read_only, provider_managed, uncovered type left alone, owned record changed to uncovered type → foreign_record, preview_owned, foreign marker, conflicting_cname, adoptable CNAME, unmarked, drifted fields, ambiguous_records) in `tests/test_edge_txn_ownership.py`
- [ ] T012 [US1] Implement `sandbox/edge_txn/ownership.py`
- [ ] T013 [P] [US1] Plan verdict tests: any refusal or unadopted record → `would_refuse` with zero provider calls; leftovers block; SSL mode action reported in `tests/test_edge_txn_plan.py`
- [ ] T014 [US1] Implement `sandbox/edge_txn/plan.py`; return `data.dns` from `host plan` in `sandbox/commands/hosting.py`

## Phase 4: US2 Failed apply rolls back what it changed (P1)

- [ ] T015 [P] [US2] Executor tests: journal before each call; failure on change N restores 1..N-1 field for field; live-differs → `rollback_conflict` naming owner; SSL restore conflict when another target's proxied marked record exists; `restore_failed` reason; lease re-acquire timeout → `rollback_incomplete` with leftovers; 60 s bound in `tests/test_edge_txn_executor.py`
- [ ] T016 [US2] Implement apply and rollback in `sandbox/edge_txn/executor.py`
- [ ] T017 [US2] Replace both in-memory `rollback()` closures in `sandbox/commands/hosting.py` (full apply and edge continuation) with the executor; front-door adapters ordered inside the transaction; `data.edge_transaction` result; parity tests T005 stay green
- [ ] T018 [US2] Crash recovery: interrupted transaction surfaces in plan and blocks apply until adopted or cleaned in `sandbox/edge_txn/plan.py` and `sandbox/commands/hosting.py`; add read-only `host edge-journal` in `sandbox/cli.py`

## Phase 5: US3 Stale address family is removed (P1)

- [ ] T019 [P] [US3] Tests: owned AAAA removed when origin lacks IPv6; unowned AAAA refused; rollback re-creates removed record in `tests/test_edge_txn_executor.py`
- [ ] T020 [US3] Remove action wired through plan and executor

## Phase 6: US4 Propagation-aware verification (P1)

- [ ] T021 [P] [US4] Verify tests: authoritative resolution only (system resolver patched to raise), per-address SNI, classification table R5, one shared 300 s budget, 3 confirmations 5 s apart for real failures, evidence fields in `tests/test_edge_txn_verify.py`
- [ ] T022 [US4] Implement `sandbox/edge_txn/verify.py` (minimal DNS query UDP with TCP fallback)
- [ ] T023 [US4] Replace `_verify_edge` in `sandbox/commands/hosting.py`; verification runs after lease release; failure triggers rollback under re-acquired lease

## Phase 7: US5 Adoption after upgrade (P2)

- [ ] T024 [P] [US5] Tests: legacy-comment records listed `unmarked`; apply without `--adopt-records` changes nothing; `--confirm` never adopts; one `--adopt-records` adopts all and rewrites the marker in `tests/test_edge_txn_plan.py`
- [ ] T025 [US5] Add `--adopt-records` in `sandbox/cli.py` and the MCP hosting tools; preview writes its own marker in the preview module

## Phase 8: US6 Proxied control endpoint at provision (P2)

- [ ] T026 [P] [US6] Tests: proxied control hostname refused (1010 body or any non-success) → `control_endpoint_refused_by_proxy` with offered modes; never `reachable`; mode switch needs `--confirm` in `tests/test_edge_txn_provision.py`
- [ ] T027 [US6] Implement `sandbox/edge_txn/provision_check.py` and call it from provision in `sandbox/commands/remote.py`; add `--control-dns-only`

## Phase 9: US7 nginx parity (P3)

- [ ] T028 [P] [US7] Run executor and verify tests against the nginx front-door adapter; identical per-record results in `tests/test_edge_txn_hosting_parity.py`

## Phase 10: Polish

- [ ] T029 Docs: `docs/remote-hosting.md` (ownership marker, plan reasons, rollback, verification classes, adoption, provision check), CLAUDE.md gotcha, CHANGELOG.md
- [ ] T030 Run `./sb selftest` and the architecture test
- [ ] T031 Live proof per quickstart on a disposable zone and remote (production zones need owner approval)

## Dependencies

Phase 2 before all stories; T005 before T017 and T023. US2 needs US1 (plan items). US3 needs US2. US4 is independent of US2 except T023's rollback call. US5 needs US1. US6 is independent. US7 needs US2 and US4.

## Parallel examples

- T002, T003, T005, T006 together.
- T009, T011, T013 together; then T015, T021, T026 together.

## MVP

US1 + US2 (T001-T018): ownership-checked plan and journaled rollback.
