# Tasks: Oldest-first durable-job admission

**Input**: Design documents from `specs/083-job-queue-fifo/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/admission.md, quickstart.md

**Tests**: Requested (the user rules require types, then tests, then code). Tests come
before implementation in every story. Each story's commit carries its docs change
(`docs/remote-job-runtime.md` "Job slots" section, CHANGELOG) in the same commit.

## Phase 1: Setup

- [ ] T001 Create tests/test_job_queue_fifo.py with a temporary-registry fixture, a recording launcher, a scheduler with floors off, and helpers to accept jobs with explicit `accepted_at`; confirm `python3 -m unittest tests.test_job_queue_fifo` discovers it

## Phase 2: Foundational (blocks all stories)

- [ ] T002 Add refusal kinds (`admitted`, `workspace`, `order`, `capacity`, `floor`, `policy`) as `.kind` on `WorkspaceBusy`, and a thread-local `JobScheduler.last_refusal`, in sandbox/jobs/scheduler.py
- [ ] T003 Extract the workspace-sharing rule from `acquire` into `workspace_compatible(connection, row, parallel_safe)` with no behaviour change, in sandbox/jobs/scheduler.py
- [ ] T004 Add `queued_in_order(limit)`, `free_slots()` (refreshes policy) and `record_queue_reason(job_id, reason)` per contracts/admission.md, in sandbox/jobs/scheduler.py
- [ ] T005 Tests then code: `acquire` refuses kind `admitted` for a job that already holds a lease or is no longer accepted/queued, and a racing second caller neither fails nor releases the winner (FR-015), in tests/test_job_queue_fifo.py and sandbox/jobs/scheduler.py

**Checkpoint**: existing scheduler and service tests pass unchanged.

## Phase 3: User Story 1 - Older queued jobs start first (P1) MVP

**Goal**: a freed slot goes to the oldest candidate whoever polls.

**Independent test**: one slot, queued A then B, only B polled; A starts first.

- [ ] T006 [US1] Tests: older candidate wins over a younger poller; a status read of B leaves B queued and starts A; two free slots start the two oldest; ties order by job id; a replay of the 2026-11-10 pattern gives 0 out-of-order starts (SC-001); in tests/test_job_queue_fifo.py
- [ ] T007 [US1] Tests: a terminal release on a status read, a cancel, and `reconcile_startup` each drain; a re-entrant drain in the same thread is a no-op while another thread's drain still runs; no free slot leaves every queued job and its reason untouched (FR-012); a capacity refusal in one drain does not stop a later drain in the same thread whose first job waits on a dependency (refusal kind cleared per visit); a drain interrupted part way leaves valid state and the next drain finishes (FR-009); in tests/test_job_queue_fifo.py
- [ ] T008 [US1] Implement the candidate test and the order gate (contract steps 2 and 6) in `JobScheduler.acquire`, in sandbox/jobs/scheduler.py
- [ ] T009 [US1] Implement `JobService.drain_queue()` and switch a status read of a queued job (outside a drain) to run the drain, recording each visited job's reason, in sandbox/application/job_service.py
- [ ] T010 [US1] Wire drain triggers (end of `reconcile_startup`, terminal release in `get()`, `cancel()`), in sandbox/application/job_service.py
- [ ] T011 [US1] Tests then code: the supervisor calls `context.drain_after_release(repository.path)` after releasing leases; a no-op for a non-default registry; errors suppressed; in tests/test_job_queue_fifo.py, sandbox/application/context.py and sandbox/jobs/supervisor.py
- [ ] T012 [US1] Docs: admission order, drain triggers and read-command side effects in docs/remote-job-runtime.md; CHANGELOG.md entry; same commit as T008-T011

**Checkpoint**: US1 tests pass; existing job tests pass.

## Phase 4: User Story 2 - A blocked older job does not hold up others (P1)

- [ ] T013 [US2] Tests: an older job on a busy exclusive workspace does not block; a same-workspace pair starts the older; an older parallel-safe job that may share a leased workspace does block a younger job (US2 AS3); dependency and sync-pending older jobs do not block until a drain records them ready; aggregates never block; 60 workspace-blocked older jobs do not hide a startable job behind them (analyze U1); in tests/test_job_queue_fifo.py
- [ ] T014 [US2] Adjust the candidate test until T013 passes without changing workspace or sync rules, in sandbox/jobs/scheduler.py; docs note in docs/remote-job-runtime.md in the same commit

## Phase 5: User Story 3 - A job whose owner is proved gone gives back its slot (P2)

- [ ] T015 [US3] Tests: a tick pass interrupts a running job whose supervisor is proved absent (`supervisor_lost`) and starts the next queued job; a stale heartbeat alone does not; a live-but-silent child without `cancel_on_stall` keeps its slot; in tests/test_job_queue_fifo.py
- [ ] T016 [US3] Implement the tick pass (`run_admission_tick()` in sandbox/application/context.py: fresh dependencies, which reconcile and drain, then close the repository) and the 20-second daemon thread started only by `_run_streamable_http` in mcp/wp-server/server.py; docs in docs/remote-job-runtime.md in the same commit

## Phase 6: User Story 4 - Queue position tells the truth (P3)

- [ ] T017 [US4] Tests: host-wide positions across projects; a workspace-blocked older job is not counted; in tests/test_job_queue_fifo.py
- [ ] T018 [US4] Compute `position` from the candidate test in `JobScheduler.queue_details`, in sandbox/jobs/scheduler.py

## Phase 7: Polish and cross-cutting

- [ ] T019 Edge-case tests in tests/test_job_queue_fifo.py: a candidate already started or terminal is skipped; a launch failure in a drain fails that job and the drain continues; floor below minimum admits nothing; lowering `max_parallel` below slots in use admits nothing; an invalid policy keeps its code as the reason (FR-014); existing reason values unchanged (FR-014)
- [ ] T020 Concurrency test: 200 seeded interleavings of drains, status reads, submissions and cancellations, run as two OS processes on one registry file; assert no double start and held slots never above the limit (SC-003, FR-008), in tests/test_job_queue_fifo.py
- [ ] T021 Run `python -m sandbox.remote_runtime.shapes --write`; bump `CONTROL_PROTOCOL_SPOKEN` to 24 only if the fingerprint changed, in sandbox/remote_runtime/
- [ ] T022 Pre-merge live evidence: run the quickstart module list locally and once on xspeed-dev through controller-44bcdba with `--cancel-on-stall` (Haiku helpers). Post-migrate live check (orchestrator, after merge and `sb remote service migrate`): quickstart "Live check after migrate", recording release and start timestamps
- [ ] T023 Luna review, then a fresh Sol high concurrency review that must say "safe"; fix or answer every finding

## Dependencies

- Phase 2 blocks every story. US1 (T006-T012) is the MVP. US2 refines the gate from T008.
  US3 depends on T009-T010. US4 depends on T008.
- T019-T023 come after all stories.

## Parallel opportunities

- All code lives in two shared files (`sandbox/jobs/scheduler.py`,
  `sandbox/application/job_service.py`) and one test module, so story phases run in
  sequence. Only the docs work could run beside implementation, and it stays in each
  story's commit by rule.

## Implementation strategy

MVP = Phases 1-3. Then US2, US4, US3, then polish. All phases ship in one release cycle
on this branch.
