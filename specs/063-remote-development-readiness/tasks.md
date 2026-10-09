# Tasks: Remote Development Execution Readiness

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/network-ranges.md, contracts/readiness.md
**Tests**: required (tests first within each story).

## Phase 1: Setup

- [x] T001 Create packages `sandbox/remote_network/__init__.py` and `sandbox/readiness/__init__.py`; register them wherever `tests/test_architecture_boundaries.py` requires

## Phase 2: Foundational

- [x] T002 [P] Tests for CIDR validation, subnet math, each overlap class (docker network, host route, docker default pool, CGNAT), partial inventory → unknown, and proposal inside `10.200.0.0/14` in `tests/test_remote_network_ranges.py`
- [x] T003 Implement `sandbox/remote_network/ranges.py` (validation, overlap classification, proposal, capacity)
- [x] T004 [P] Tests running the remote program for real against a temp home via a local `sh -c` stand-in: inventory, assign (idempotent, conflict, overlap, unsupported), allocate all-or-nothing and idempotent per `(workspace_id, network)`, concurrent last-subnet allocation yields one grant, release-owner, `list` returns `capacity_proof`, read-only list without mkdir, file modes 0700/0600, installed protocol below the ranges protocol (or undeclared) → `range_runtime_unsupported` naming the migrate, no secret-shaped values in any output (SC-009) in `tests/test_remote_network_program.py`
- [x] T005 Implement `sandbox/remote_network/program.py` (fixed program, flock, typed errors, `capacity_proof`) and `sandbox/remote_network/store.py` (RangeStore over `ssh_run`, 15 s bound, bounded listing, redaction, installed-protocol marker check per research R10)

## Phase 3: US1 Development capacity without a daemon restart (P1)

- [x] T007 [P] [US1] CLI parser and handler tests for `remote network-range propose|assign|list` (planned without `--confirm`, typed refusals, no subnets outside `list`) and for `remote provision` printing a proposed range and assign command when none is assigned, never assigning (FR-003), in `tests/test_remote_network_ranges.py`
- [x] T008 [US1] Add `network-range` action, `--cidr`, `--subnet-prefix` in `sandbox/cli.py` and `_cmd_network_range` in `sandbox/commands/remote.py`; record the display echo in the remote block; add the proposal to the provision result
- [ ] T009 [P] [US1] Tests for the Compose override: one ipam subnet per created network, owner kind per the data-model mapping (workspace stack, job, preview, CI cell), externals skipped and reported `outside_range`, refusal before transfer when capacity < N, a network-create collision with an unobserved network → typed refusal with no retry in `tests/test_remote_network_override.py`
- [ ] T010 [US1] Implement `sandbox/remote_network/override.py` and wire it into the remote compose invocation for workspaces, jobs, previews and CI cells (built-in template via `sandbox/core/_docker.py`, generic Compose instances via the effective config)
- [x] T011 [P] [US1] Tests: evaluator counts pool plus range capacity, never default pools; `missing_pool_evidence` wording names the range remedy; exhaustion returns the allocation table (≤32 rows, no subnets) and release commands in `tests/test_resource_network_capacity.py`
- [x] T012 [US1] Extend `sandbox/resources/network_capacity.py` for range evidence and the new wording
- [ ] T013 [US1] Admission allocates in the same remote program call as the pool probe in `sandbox/core/_remote.py` (`remote_network_capacity_admission`), passing owner kind, owner id and workspace; propagate granted ids to the run
- [ ] T013a [US1] Bump `CONTROL_PROTOCOL_SPOKEN` (next free number; coordinate with 060 T009, oldest served stays 1) in `sandbox/remote_runtime/protocol.py` and re-record `sandbox/remote_runtime/control_shapes.json` with `python -m sandbox.remote_runtime.shapes --write`
- [ ] T014 [P] [US1] Tests: workspace release, reap and retention expiry free allocations; a killed job's allocation stays attributed in `tests/test_workspace_runtime.py`
- [ ] T015 [US1] Call `release-owner` from release, reap and retention paths in `sandbox/application/workspace_service.py`
- [ ] T015a [US1] Docs for US1: `docs/remote-hosting.md` (capacity admission, ranges, allocation lifetime), CHANGELOG.md, CLAUDE.md gotcha

## Phase 4: US2 One readiness answer before submitting (P1)

- [ ] T016 [P] [US2] Tests for each row's states and remedies (remedies parse against the CLI), the 60 s deadline turning unfinished rows `unknown`, a reachable remote with no probe timeouts completing within 30 s (SC-003, fake clock), proposed range only when unassigned and inventory complete, `remote_selection` on every success result in `tests/test_readiness.py`
- [ ] T017 [US2] Implement `sandbox/readiness/rows.py` and `sandbox/readiness/check.py` (concurrent rows, per-project proof file 0600, reuse rules and invalidation from data-model, handoff record)
- [ ] T018 [US2] Add `remote readiness` CLI action and the MCP tool `remote_readiness` with the shared envelope (`sandbox/commands/remote.py`, `sandbox/cli.py`, `mcp/wp-server/tools/remote.py`, registered in `mcp/wp-server/tools/manifest.py`)
- [ ] T019 [P] [US2] Tests: every submission path refuses with the first `not_ready` row before transfer (`bytes_transferred: 0`), never on `unknown`/`not_applicable`, reuses a fresh all-ready proof for the same project only, never reuses a `not_ready` proof, re-checks after a revision change, after a same-revision migrate (`installed_at`), and after range assign or release-owner, and reports `remote_selection` on success in `tests/test_readiness_gate.py`
- [ ] T020 [US2] Implement `sandbox/readiness/gate.py` and call it from `test`/`run_tests`, `e2e`/`run_e2e`, `ci`/`ci_run`, `exec --remote`, `job-start`, `ensure --remote`; record the handoff after ensure→exec succeeds
- [ ] T020a [P] [US2] Tests: doctor "Remote targets" lists every declared remote, including unregistered names, with readiness rows in `tests/test_doctor_remote_targets.py`
- [ ] T021 [US2] `sb doctor` "Remote targets" covers every declared remote including unregistered names using readiness rows in `sandbox/commands/lifecycle.py`

## Phase 5: US3 Retired or ambiguous remote is told, not guessed (P2)

- [ ] T022 [P] [US3] Tests: `unknown_remote` carries name, source, registered list and remedy on every path; `ambiguous_remote` lists candidates; `remote_not_provisioned`; no local fallback; `--local` after `not_ready` states the declared remote and failing row in `tests/test_remote_selection_refusals.py`
- [ ] T023 [US3] Enrich refusals and report `remote_selection` in `sandbox/application/target_service.py`; confirm by test that no submission path falls back to local (research R9: none does today; any path found falling back stops for parity evidence and approval, principle VI); add explicit `--local` selector where missing
- [ ] T023a [US2/US3] Docs: `docs/remote-job-runtime.md` (readiness, proof reuse, selection, no fallback), MCP tool docs

## Phase 6: US4 Daemon-pool change stays a visible maintenance step (P3)

- [ ] T024 [P] [US4] Tests: plan lists hosted targets, other running containers and `plan_digest`; apply with a stale digest refuses `docker_pool_plan_changed` with zero restarts in `tests/test_docker_pool_plan_digest.py`; update `tests/test_remote_docker_pool_capacity.py` for the required digest, and `--recover-interrupted` stays digest-free (it restores a recorded plan, not a new one)
- [ ] T025 [US4] Add hosted inventory, container count and digest to the pool plan; `--plan-digest` required with `--confirm` in `sandbox/core/_remote.py`, `sandbox/commands/remote.py`, `sandbox/cli.py`

## Phase 7: Polish

- [ ] T026 Docs: `docs/remote-hosting.md` pool plan and digest; final pass over US1-US3 docs and CHANGELOG.md
- [ ] T027 Run `./sb selftest` and the architecture test
- [ ] T028 Live proof per quickstart on a disposable remote (remote install protocol; `xcloud-london` needs owner approval)

## Dependencies

Phase 2 before all stories. T013 needs T005 and T012; T013a follows T013. Each story's docs land in the same commit as its code. US2's capacity row needs T003/T005 (read-only inventory). US3 is independent of US1. US4 is independent.

## Parallel examples

- T002, T004 together; then T007, T009, T011, T014 together.
- T016, T019, T022, T024 can be written in parallel.

## MVP

US1 (T001-T015) restores remote development on a pool-less remote without a restart.
