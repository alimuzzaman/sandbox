# Research: Oldest-first durable-job admission

## R1. Where order is enforced

- **Decision**: An order gate in `JobScheduler.acquire`, inside its existing
  `transaction(immediate=True)`, after stale reaping and the policy check and before
  slot assignment.
- **Rationale**: That transaction is the one serialization point every admission
  already goes through, across processes (SQLite immediate locks the database file).
  Any caller (status read, submit, drain, another process) is ordered by it.
- **Alternatives**: a host lock file around a drain (new state file, and readers that
  skip it still race); ordering only inside the drain (a status read in another process
  could still win).

## R2. Which older jobs block a younger one ("eligible")

- **Decision**: An older queued job blocks if, all of: `lifecycle='queued'`;
  `queue_reason='workspace_or_capacity_busy'`; it is not an aggregate (`kind` not matrix
  or plan, and no child rows); and its own workspace (namespace, project, label) has no
  lease held by an active job. Order key `(accepted_at, job_id)`, host-wide.
- **Rationale**: Dependency and sync waits carry their own reasons, so the reason
  filter excludes them. A job whose workspace is leased is waiting on its own workspace
  (FR-003). With no lease on its workspace, the only things that could stop it are
  host-wide (slots, floor, policy), and those stop the younger job too.
- **Trade-off**: A parallel-safe older job that could share a leased workspace counts
  as not eligible, so a younger job may start before it. That is conservative (never
  blocks wrongly) and rare.
- **No stuck head**: an older eligible job that the drain tries but cannot start does
  not freeze the host. Every exception inside `get()`'s admission block (sync claim,
  `acquire`, commit, `_launch`) moves that job to `failed` (`supervisor_launch_failed`),
  so it stops being eligible. Only a registry read error before that block leaves it
  queued, and such an error affects every job equally. An admitted job holds a lease on
  its own workspace, so it stops counting as eligible before its lifecycle changes.
- **Alternatives**: evaluating each older job's full compatibility in Python inside the
  transaction (duplicates `acquire`'s rules and is slower).

## R3. What drives admission

- **Decision**: `JobService.drain_queue(limit=50)` walks queued jobs oldest-first
  (`queued_in_order`) and calls `get(job_id, reconcile=False)` for each, which runs the
  existing admission and launch path. It stops when the scheduler records a host-wide
  refusal (slots, floor, policy) or no slot is free. A per-instance re-entrancy flag
  stops nested drains.
- **Triggers**: end of `reconcile_startup` (every job command builds dependencies, so
  each `sb job-*` invocation drains); after `get()` releases a terminal job; after
  `cancel()`; after an order refusal in `get()` outside a drain; the supervisor after it
  releases leases; the service tick.
- **Rationale**: Reuses the one launch path (descriptor, sync claims, failure
  handling), so the drain adds no second way to start a job.

## R4. Supervisor-triggered drain

- **Decision**: After the supervisor releases its leases, it calls
  `sandbox.application.context.drain_after_release(repository.path)`. That helper is a
  no-op unless the path is the host's default registry, and it suppresses all errors.
  It builds dependencies, which runs `reconcile_startup` and so drains.
- **Rationale**: `_launch` starts detached supervisors (`start_new_session=True`), so a
  finishing supervisor can start the next job safely. The default-registry guard keeps
  tests with temporary registries off the real host state.

## R5. Periodic tick and proved-gone sweep

- **Decision**: `_run_streamable_http` in `mcp/wp-server/server.py` starts one daemon
  thread that every 20 s builds fresh `durable_job_dependencies()` (its own SQLite
  connection), which reconciles and drains, then closes that repository.
- **Rationale**: The proof of a lost supervisor already lives in `reconcile_startup`
  (identity mismatch or proved absence, never heartbeat age). Fresh dependencies avoid
  sharing the service's cross-thread connection. A failure is logged to stderr and the
  next tick tries again.
- **Alternatives**: a subprocess `sb job-reconcile` per tick (process start cost, and
  the controller auto-update hook); reusing the cached connection (see plan).

## R6. Queue position

- **Decision**: `position` = 1 + the number of older eligible jobs host-wide (same
  predicate as R2). `blocking_jobs` is unchanged.
- **Rationale**: FR-011. The key set stays the same, so no protocol bump.

## R7. Protocol

- **Decision**: No new payload keys in shape-fingerprinted producers
  (`job_service.py`, `jobs_runtime.py`, `listing.py`, `registry.py`). `drain_queue`
  returns a list of job ids. Re-run `python -m sandbox.remote_runtime.shapes --write`;
  if the fingerprint moves, bump to 24.

## R8. Live-but-silent jobs

- **Decision**: Out of scope; open owner question (plan). Evidence: job `06a20412` on
  xspeed-dev reported `process_alive: true`, an owned descendant, `stalled: true`, a
  supervisor heartbeat until the manual cancel, and `cancel_on_stall` false.
