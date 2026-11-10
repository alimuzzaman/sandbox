# Tasks: Oldest-first durable-job admission

**Input**: Design documents from `specs/083-job-queue-fifo/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/admission.md, quickstart.md

**Tests**: Requested (the user rules require types, then tests, then code). Tests come
before implementation in every story.

## Phase 1: Setup

- [ ] T001 Create the test module skeleton with a temporary-registry fixture, a fake launcher and a policy-free scheduler helper in tests/test_job_queue_fifo.py

## Phase 2: Foundational (blocks all stories)

- [ ] T002 Add the refusal-kind type (`workspace`, `order`, `capacity`, `floor`, `policy`) and `JobScheduler.last_refusal`, set on every refusal path of `acquire`, in sandbox/jobs/scheduler.py
- [ ] T003 Add the shared eligibility SQL (older queued job, capacity reason, not aggregate, own workspace unleased by an active job) as one helper in sandbox/jobs/scheduler.py
- [ ] T004 Add `JobScheduler.queued_in_order(limit)` and `JobScheduler.free_slots()` per contracts/admission.md in sandbox/jobs/scheduler.py

**Checkpoint**: scheduler helpers exist; behaviour is unchanged until US1 wires the gate.

## Phase 3: User Story 1 - Older queued jobs start first (P1) MVP

**Goal**: a freed slot goes to the oldest startable job whoever polls.

**Independent test**: one slot, queued A then B, only B polled; A starts first.

- [ ] T005 [US1] Tests: an older startable job wins over a younger poller; a status read of the younger job leaves it queued and admits the older one; two free slots admit the two oldest; ties on `accepted_at` order by job id; in tests/test_job_queue_fifo.py
- [ ] T006 [US1] Tests: a terminal release on a status read, a cancel and `reconcile_startup` each drain the queue; a re-entrant drain is a no-op; in tests/test_job_queue_fifo.py
- [ ] T007 [US1] Implement the order gate in `JobScheduler.acquire` (step 5 of contracts/admission.md) and switch slot refusal to `last_refusal = "capacity"` in sandbox/jobs/scheduler.py
- [ ] T008 [US1] Implement `JobService.drain_queue(limit=50)` with the re-entrancy flag and stop rules in sandbox/application/job_service.py
- [ ] T009 [US1] Wire drain triggers in sandbox/application/job_service.py: end of `reconcile_startup`, after a terminal release in `get()`, after `cancel()` releases, and after an order refusal in `get()` outside a drain
- [ ] T010 [US1] Tests then code: the supervisor calls `context.drain_after_release(repository.path)` after releasing leases; the helper is a no-op for a non-default registry and swallows errors; in tests/test_job_queue_fifo.py, sandbox/application/context.py and sandbox/jobs/supervisor.py

**Checkpoint**: US1 tests pass; existing scheduler and service tests still pass.

## Phase 4: User Story 2 - A blocked older job does not hold up others (P1)

**Goal**: workspace, dependency and sync waits never block younger startable jobs.

**Independent test**: older job on a busy workspace, younger on a free one; the younger starts.

- [ ] T011 [US2] Tests: older job on a busy workspace does not block; same-workspace pair admits the older; an older job waiting on a dependency does not block; a sync-pending older job does not block; parallel-safe sharing in acceptance order; aggregates never block; in tests/test_job_queue_fifo.py
- [ ] T012 [US2] Adjust the eligibility helper or gate until the T011 tests pass without changing workspace or sync rules, in sandbox/jobs/scheduler.py

## Phase 5: User Story 3 - A job whose owner is proved gone gives back its slot (P2)

**Goal**: proved-gone running jobs free their slot on the host's own schedule.

**Independent test**: a running job with a proved-absent supervisor is interrupted on a tick and a queued job starts.

- [ ] T013 [US3] Tests: a tick with a proved-absent supervisor interrupts the job (`supervisor_lost`) and admits the next queued job; a stale heartbeat alone does not; a live-but-silent child without `cancel_on_stall` keeps its slot; in tests/test_job_queue_fifo.py
- [ ] T014 [US3] Implement the 20-second admission tick (a daemon thread with fresh dependencies per pass, closed after use, errors to stderr) started only by `_run_streamable_http` in mcp/wp-server/server.py, with the pass body in a testable function in sandbox/application/context.py

## Phase 6: User Story 4 - Queue position tells the truth (P3)

**Goal**: positions match start order.

**Independent test**: three queued jobs from two projects report 1, 2, 3 and start in that order.

- [ ] T015 [US4] Tests: host-wide positions across projects; a blocked older job is not counted; in tests/test_job_queue_fifo.py
- [ ] T016 [US4] Compute `position` from the eligibility helper in `JobScheduler.queue_details` in sandbox/jobs/scheduler.py

## Phase 7: Polish and cross-cutting

- [ ] T017 Concurrency test: 200 seeded interleavings of drains, status reads, submissions and cancellations across two service instances on one registry file; assert no job starts twice and held slots never exceed the limit (SC-003), in tests/test_job_queue_fifo.py
- [ ] T018 Run `python -m sandbox.remote_runtime.shapes --write`; bump `CONTROL_PROTOCOL_SPOKEN` only if the fingerprint changed, in sandbox/remote_runtime/
- [ ] T019 [P] Document admission order, the drain triggers, the tick and the stall-cancel open question in docs/remote-job-runtime.md, and add a CHANGELOG.md entry
- [ ] T020 Run the quickstart targeted modules locally and once on xspeed-dev through controller-44bcdba with `--cancel-on-stall` (Haiku helpers)
- [ ] T021 Luna review, then Sol high concurrency review; fix or answer every finding

## Dependencies

- Phase 2 blocks every story. US1 (T005-T010) is the MVP and blocks US3's drain reuse.
- US2 depends on the gate from T007. US4 depends on T003. US3 depends on T008.
- T017-T021 come after all stories.

## Parallel opportunities

- T019 (docs) can run beside T017-T018.
- Tests in one module are written in sequence (one file); the story phases share
  `sandbox/jobs/scheduler.py` and `sandbox/application/job_service.py`, so they run in sequence.

## Implementation strategy

MVP = Phases 1-3 (oldest-first with release and reconcile triggers). Then US2 (no
head-of-line blocking), US4 (positions), US3 (tick), then polish. All phases ship in one
release cycle on this branch.
