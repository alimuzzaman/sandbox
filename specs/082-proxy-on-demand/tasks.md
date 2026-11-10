# Tasks: Clean-URL proxy on demand and remote-only controllers

**Input**: Design documents from `specs/082-proxy-on-demand/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: requested by the owner's brief (types first, then tests, then code). Tests run
only on xspeed-dev through `sb job-start --remote xspeed-dev`, compared with an
`origin/latest` baseline.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Record the `origin/latest` baseline for the touched test modules on xspeed-dev (scratch root `baseline.txt`)

## Phase 2: Foundational (types)

- [x] T002 [P] Add the pure local-runtime selection in sandbox/config/local_runtime.py (`LocalRuntimeSelection`, `normalize_local_runtime`, `machine_local_runtime`, `resolve_local_runtime`, `remote_only_message`)
- [x] T003 [P] Add the pure proxy-demand decision in sandbox/application/proxy_demand.py (`ProxyDemandFacts`, `ProxyDemandDecision`, `decide`)
- [x] T004 Register `controller.local_runtime` in `MACHINE_CONFIG_PROVIDERS` and `apply_machine_config` in sandbox/config/manifest.py
- [x] T005 [P] Unit tests for T002 and T004 in tests/test_local_runtime.py
- [x] T006 [P] Unit tests for the T003 decision table in tests/test_proxy_on_demand.py

## Phase 3: User Story 1 - The proxy runs only while something needs it (P1)

**Goal**: no self-restart; `up` starts a stopped provisioned proxy; `down`/`delete` stop an idle one.
**Independent Test**: tests in tests/test_proxy_on_demand.py and tests/test_setup_idempotency.py; quickstart live scenarios 1-5.

### Tests

- [x] T007 [P] [US1] Test `render_proxy_compose()` declares `restart: "no"` and that a changed compose recreates the container through `proxy_apply` in tests/test_proxy_on_demand.py
- [x] T008 [P] [US1] Tests for `stop_proxy_if_idle`: stops when idle, keeps with a running `sandbox-*` project, keeps with wake routes, keeps on engine error, no-op when stopped, keeps when a start holds the lease, never stops an adopted ingress, silent without `docker`, in tests/test_proxy_on_demand.py
- [x] T009 [P] [US1] Tests that `_ensure_proxy_up` does nothing when `disabled`, adoption or remote-only is selected, in tests/test_proxy_on_demand.py
- [x] T010 [US1] Replace `test_up_never_mutates_unreceipted_legacy_proxy_state` with the new contract (starts a stopped default proxy through `_ensure_proxy_up`, never `proxy.plan/apply`) in tests/test_setup_idempotency.py
- [x] T011 [P] [US1] Tests that `cmd_down` and instance delete call `stop_proxy_if_idle` in tests/test_proxy_on_demand.py

### Implementation

- [x] T012 [US1] `restart: "no"` in `render_proxy_compose()` in sandbox/core/_domains.py
- [x] T013 [US1] Proxy lock (`proxy_start_lease`, exclusive non-blocking stop) and `stop_proxy_if_idle(cfg)` with `_running_sandbox_projects()` and `_wake_route_count(cfg)` in sandbox/core/_domains.py
- [x] T014 [US1] Gate `_ensure_proxy_up` and `_ensure_url_proxy` on `disabled` / adoption / remote-only in sandbox/core/_domains.py
- [x] T015 [US1] `cmd_up`: hold the start lease and call `_ensure_proxy_up` for a clean-domain instance (WordPress and generic Compose) in sandbox/commands/lifecycle.py
- [x] T016 [US1] `cmd_down`: call `stop_proxy_if_idle` after a successful stop in sandbox/commands/lifecycle.py
- [x] T017 [US1] Instance delete: call `stop_proxy_if_idle` after the route refresh in sandbox/commands/instances_cmd.py

## Phase 4: User Story 2 - Remote-only and Docker-less controllers (P1)

**Goal**: remote commands never need Docker; the switch refuses local Docker work; doctor reports the mode.
**Independent Test**: tests/test_local_runtime.py; quickstart remote-only steps.

### Tests

- [x] T018 [P] [US2] Tests: `compose()` refuses before `run` when remote-only; `docker_daemon_preflight` returns `local_runtime_none`; `_instance_running` and `_proxy_container_running` make no subprocess call when remote-only; `_proxy_container_running` is False without `docker`, in tests/test_local_runtime.py
- [x] T019 [P] [US2] Tests: doctor in remote-only mode needs no instance and reports the mode (text and JSON); doctor without `docker` reports `Docker CLI available` failing instead of raising, in tests/test_local_runtime.py
- [x] T020 [P] [US2] Test: `proxy_down` returns success without `docker`, in tests/test_local_runtime.py

### Implementation

- [x] T021 [US2] `local_runtime_selection()` facade reading `sandbox.local.yml` and the environment in sandbox/core/_docker.py
- [x] T022 [US2] Remote-only refusal in `compose()` and `docker_daemon_preflight()` in sandbox/core/_docker.py
- [x] T023 [US2] No engine call in `_instance_running` when remote-only in sandbox/core/_instances.py
- [x] T024 [US2] `_proxy_container_running`: False when remote-only or `docker` missing, OSError-safe; `proxy_down` safe without `docker`, in sandbox/core/_domains.py
- [x] T025 [US2] Doctor: `Local runtime` section, remote-only path (remote targets, storage, credentials), missing-`docker` check, JSON `local_runtime`, in sandbox/commands/lifecycle.py
- [x] T026 [US2] Doctor resolves without an instance when remote-only in sandbox/cli.py

- [x] T034 [US2] Generic Compose adapter refuses every operation when remote-only in sandbox/runtimes/compose.py (found in self-review: it runs `docker compose` through its own process runner, not `compose()`)
- [x] T035 [US2] MCP proxy probe uses the gated core probe with no raw `docker` fallback, and MCP `_compose` refuses when remote-only, in mcp/wp-server/app.py (Luna review finding)
- [x] T036 [US1] Patch the idle check in the `cmd_down` test in tests/test_instance_ready_install_state.py so no unit test reaches the real engine

## Phase 5: User Story 3 - Docs (P2)

- [x] T027 [P] [US3] Why the proxy exists, when it runs, remote-only switch, in docs/clean-url-default.md
- [x] T028 [P] [US3] Short pointer in docs/host-ingress.md
- [x] T029 [P] [US3] Remote-only setup in the README setup section, README.md
- [x] T030 [P] [US3] CLAUDE.md clean-URL rule mentions on-demand lifetime and the switch; agent context plan pointer

## Phase 6: Polish

- [x] T031 Run the touched and neighbouring test modules on xspeed-dev (Haiku helper, fixed report) and compare with T001
- [x] T032 Self-review `git diff origin/latest..HEAD`; commit; push `fix/proxy-on-demand`
- [x] T033 Luna review of the head (read-only); fix or answer every finding; re-push

## Dependencies

- T002-T004 before every story. T005-T006 after T002-T004.
- US1 (T007-T017) and US2 (T018-T026) are independent except both edit sandbox/core/_domains.py and sandbox/commands/lifecycle.py (serialize edits).
- US3 after US1 and US2 so the docs describe the shipped behaviour.
- T031 after all code; T033 after T032.

## Parallel examples

- T005 and T006 (different test files).
- T007, T008, T009, T011 (one file, written together) alongside T018-T020.
- T027-T030 (different docs).

## Implementation strategy

MVP is US1 (the reported problem). US2 ships in the same release cycle: it is one coherent
feature outcome (the owner's question covers both), so the phase is not split.

## Verification record (2026-11-10)

- Baseline `origin/latest` 99dec45, job c5bbeaac39f94bf86e09cc561813ff30: Ran 491, 1 failure, 6 errors, 8 skipped.
- Branch 0a7125c, job 152fd6d0c3e15c2da1cbfd915bef0870: Ran 535, 1 failure, 6 errors, 9 skipped. Same failing ids as the baseline (6 `test_creation_requests` errors from the host's network-range state, 1 `test_web_dashboard` remote-summary failure); no new failures.
- `tests/test_lifecycle.py` was excluded from both runs: on the host's Python 3.12 it fails to import (`SyntaxError: too many statically nested blocks`, line 470) on `origin/latest` already.
- Luna review of a2cde6a: APPROVE WITH FIXES (one medium, fixed in 0a7125c); re-check of 0a7125c: APPROVE.

