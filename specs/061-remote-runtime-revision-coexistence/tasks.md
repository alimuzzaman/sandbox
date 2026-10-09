# Tasks: Remote Runtime Revision Coexistence

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/runtime-compatibility.md
**Tests**: required (tests first within each story).

## Phase 1: Setup

- [x] T001 Create package `sandbox/remote_runtime/__init__.py` and register it in the architecture boundary allowlist if required by `tests/test_architecture_boundaries.py`

## Phase 2: Foundational

- [x] T002 [P] Tests for protocol constants, unit-env encode/parse and rejection of malformed values in `tests/test_remote_runtime_verdict.py`
- [x] T003 [P] Tests for every verdict state (compatible, protocol_newer, protocol_too_old, exact_only equal/different, unknown, strict) in `tests/test_remote_runtime_verdict.py`
- [x] T004 Implement `sandbox/remote_runtime/protocol.py` (spoken/oldest served, encode/parse)
- [x] T005 Implement `sandbox/remote_runtime/verdict.py` (`compatibility()`), the single rule
- [x] T006 [P] Tests that every remedy builder renders a command the real CLI parser accepts and contains no placeholder in `tests/test_remote_runtime_refusal.py`
- [x] T007 Implement `sandbox/remote_runtime/refusal.py` (shared shape, remedy builders)

## Phase 3: US1 Compatible checkouts share one remote (P1)

- [x] T008 [US1] Test: status probe parses the protocol env line; legacy unit yields `exact_only`; status JSON carries `control_protocol` and `compatibility` in `tests/test_remote.py`
- [x] T009 [US1] Status probe reads `SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL`; `remote_mcp_service_status` adds `control_protocol` and `compatibility` in `sandbox/core/_remote.py`
- [x] T010 [US1] Migrate writes the protocol env line into the unit in `sandbox/core/_remote.py`
- [x] T011 [US1] Tests: each FR-003 consumer accepts `compatible` at a different revision and refuses `protocol_newer` in the shared shape (workspace preflight, hosted apply eligibility, recovery, Postgres recovery, resources context, host memory)
- [x] T012 [US1] Route FR-003 consumers through the verdict: `sandbox/application/workspace_service.py`, `sandbox/commands/hosting.py`, `sandbox/transports/remote_recovery.py`, `sandbox/transports/remote_postgres_recovery.py`, `sandbox/resources/context.py`, `sandbox/resources/host_memory/remote.py`
- [x] T013 [US1] Test that artifact-binding checks, remote WP-CLI signatures and cleanup-routine enable still refuse a revision difference

## Phase 4: US2 Strict mode and pins (P1)

- [x] T014 [P] [US2] Tests for holder identity, pin validation, expiry cap, register/renew/list/release/break against a local fake remote home in `tests/test_remote_runtime_pins.py`
- [x] T015 [US2] Implement `sandbox/remote_runtime/pins.py` (remote program, ssh transport, parse/validate)
- [x] T016 [US2] Strict gate in the verdict entry point: exact revision, pin register/renew, `strict_pin_unverifiable`, broken-pin report; `--strict-runtime` and `SANDBOX_STRICT_RUNTIME=1` in `sandbox/cli.py`
- [x] T017 [US2] `remote pin list|release` with `--break-pin` for non-holders in `sandbox/commands/remote.py` and `sandbox/cli.py`

## Phase 5: US3 Migrate shows and protects pins (P2)

- [x] T018 [US3] Tests: plan/dry run list pins and the protocol line; confirm without acknowledgment refuses with zero writes; with `--break-pin` marks broken; pins re-read at apply
- [x] T019 [US3] Implement in `sandbox/commands/remote.py` migrate path

## Phase 6: US4 Refusals and remedies (P2)

- [x] T020 [US4] Replace emitted mismatch hints (`--remote NAME`, `<name>` placeholder, "sync the remote runtime revision") with refusal remedies; `unknown` verdict remedies name status and diagnostics
- [x] T021 [US4] Recovery create reports the shared mismatch shape instead of `materialization_observe_failed`

## Phase 7: US5 Per-remote registration lock (P3)

- [x] T022 [P] [US5] Tests: lock on A does not block B or list; same-remote wait bounded and reports holder; concurrent writes to different remotes both persist in `tests/test_remote_registration_lock.py`
- [x] T023 [US5] Implement named lock, registry write lock and holder record in `sandbox/core/_remote.py`; pass the remote name at every call site (`sandbox/commands/hosting.py`, `preview.py`, `deploy.py`); busy error reports holder in `sandbox/commands/remote.py`

## Phase 8: Polish

- [x] T024 Docs: `docs/remote-hosting.md`, `docs/remote-job-runtime.md`, CLAUDE.md gotcha 23, CHANGELOG.md
- [ ] T025 Run `./sb selftest` and the architecture test
- [ ] T026 Live proof per quickstart on a disposable remote (needs remote install protocol; Lenzora pin bump needs owner approval)

## Dependencies

Phase 2 before all stories. US1 before US2 (strict gate lives in the verdict entry point). US3 needs US2 pins. US4 can follow US1. US5 is independent.
