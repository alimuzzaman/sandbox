# Tasks: Remote Development Execution Readiness

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/network-ranges.md, contracts/readiness.md
**Tests**: required (tests first within each story).

## Phase 1: Setup

- [ ] T001 Create packages `sandbox/remote_network/__init__.py` and `sandbox/readiness/__init__.py`; register them wherever `tests/test_architecture_boundaries.py` requires

## Phase 2: Foundational

- [ ] T002 [P] Tests for CIDR validation, subnet math, each overlap class (docker network, host route, docker default pool, CGNAT), partial inventory → unknown, and proposal inside `10.200.0.0/14` in `tests/test_remote_network_ranges.py`
- [ ] T003 Implement `sandbox/remote_network/ranges.py` (validation, overlap classification, proposal, capacity)
- [ ] T004 [P] Tests running the remote program for real against a temp home via a local `sh -c` stand-in: inventory, assign (idempotent, conflict, overlap, unsupported), allocate all-or-nothing, concurrent last-subnet allocation yields one grant, release-owner, read-only list without mkdir, file modes 0700/0600 in `tests/test_remote_network_program.py`
- [ ] T005 Implement `sandbox/remote_network/program.py` (fixed program, flock, typed errors) and `sandbox/remote_network/store.py` (RangeStore over `ssh_run`, 15 s bound, bounded listing, redaction)
- [ ] T006 Bump `CONTROL_PROTOCOL_SPOKEN` to 2 (oldest served stays 1) in `sandbox/remote_runtime/protocol.py` and re-record `sandbox/remote_runtime/control_shapes.json` with `python -m sandbox.remote_runtime.shapes --write` once T013 lands

## Phase 3: US1 Development capacity without a daemon restart (P1)

- [ ] T007 [P] [US1] CLI parser and handler tests for `remote network-range propose|assign|list` (planned without `--confirm`, typed refusals, no subnets outside `list`) in `tests/test_remote_network_ranges.py`
- [ ] T008 [US1] Add `network-range` action, `--cidr`, `--subnet-prefix` in `sandbox/cli.py` and `_cmd_network_range` in `sandbox/commands/remote.py`; record the display echo in the remote block
- [ ] T009 [P] [US1] Tests for the Compose override: one ipam subnet per created network, externals skipped and reported `outside_range`, refusal before transfer when capacity < N in `tests/test_remote_network_override.py`
- [ ] T010 [US1] Implement `sandbox/remote_network/override.py` and wire it into the remote compose invocation for workspaces, jobs, previews and CI cells (built-in template via `sandbox/core/_docker.py`, generic Compose instances via the effective config)
- [ ] T011 [P] [US1] Tests: evaluator counts pool plus range capacity, never default pools; `missing_pool_evidence` wording names the range remedy; exhaustion returns the allocation table (≤32 rows, no subnets) and release commands in `tests/test_resources_network_capacity.py`
- [ ] T012 [US1] Extend `sandbox/resources/network_capacity.py` for range evidence and the new wording
- [ ] T013 [US1] Admission allocates in the same remote program call as the pool probe in `sandbox/core/_remote.py` (`remote_network_capacity_admission`), passing owner kind, owner id and workspace; propagate granted ids to the run
- [ ] T014 [P] [US1] Tests: workspace release, reap and retention expiry free allocations; a killed job's allocation stays attributed in `tests/test_workspace_runtime.py`
- [ ] T015 [US1] Call `release-owner` from release, reap and retention paths in `sandbox/application/workspace_service.py`

## Phase 4: US2 One readiness answer before submitting (P1)

- [ ] T016 [P] [US2] Tests for each row's states and remedies (remedies parse against the CLI), the 60 s deadline turning unfinished rows `unknown`, proposed range only when unassigned and inventory complete in `tests/test_readiness.py`
- [ ] T017 [US2] Implement `sandbox/readiness/rows.py` and `sandbox/readiness/check.py` (concurrent rows, proof file 0600, reuse window 300 s bound to installed revision, handoff record)
- [ ] T018 [US2] Add `remote readiness` CLI action and the MCP tool `remote_readiness` with the shared envelope (`sandbox/commands/remote.py`, `sandbox/cli.py`, `mcp/wp-server/tools/`)
- [ ] T019 [P] [US2] Tests: every submission path refuses with the first `not_ready` row before transfer (`bytes_transferred: 0`), never on `unknown`/`not_applicable`, reuses a fresh proof, re-checks after a revision change in `tests/test_readiness_gate.py`
- [ ] T020 [US2] Implement `sandbox/readiness/gate.py` and call it from `test`/`run_tests`, `e2e`/`run_e2e`, `ci`/`ci_run`, `exec --remote`, `job-start`, `ensure --remote`; record the handoff after ensure→exec succeeds
- [ ] T021 [US2] `sb doctor` "Remote targets" covers every declared remote including unregistered names using readiness rows in `sandbox/commands/lifecycle.py`

## Phase 5: US3 Retired or ambiguous remote is told, not guessed (P2)

- [ ] T022 [P] [US3] Tests: `unknown_remote` carries name, source, registered list and remedy on every path; `ambiguous_remote` lists candidates; `remote_not_provisioned`; no local fallback; `--local` after `not_ready` states the declared remote and failing row in `tests/test_remote_selection_refusals.py`
- [ ] T023 [US3] Enrich refusals and report `remote_selection` in `sandbox/application/target_service.py`; remove silent local fallback from submission paths; add explicit `--local` selector where missing

## Phase 6: US4 Daemon-pool change stays a visible maintenance step (P3)

- [ ] T024 [P] [US4] Tests: plan lists hosted targets, other running containers and `plan_digest`; apply with a stale digest refuses `docker_pool_plan_changed` with zero restarts in `tests/test_docker_pool_plan_digest.py`
- [ ] T025 [US4] Add hosted inventory, container count and digest to the pool plan; `--plan-digest` required with `--confirm` in `sandbox/core/_remote.py`, `sandbox/commands/remote.py`, `sandbox/cli.py`

## Phase 7: Polish

- [ ] T026 Docs: `docs/remote-hosting.md` (capacity admission, ranges, pool plan), `docs/remote-job-runtime.md` (readiness, selection, fallback), CLAUDE.md gotcha, CHANGELOG.md
- [ ] T027 Run `./sb selftest` and the architecture test
- [ ] T028 Live proof per quickstart on a disposable remote (remote install protocol; `xcloud-london` needs owner approval)

## Dependencies

Phase 2 before all stories. T013 needs T005 and T012; T006 follows T013. US2's capacity row needs T003/T005 (read-only inventory). US3 is independent of US1. US4 is independent.

## Parallel examples

- T002, T004 together; then T007, T009, T011, T014 together.
- T016, T019, T022, T024 can be written in parallel.

## MVP

US1 (T001-T015) restores remote development on a pool-less remote without a restart.
