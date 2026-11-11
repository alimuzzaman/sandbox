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

## R2. Which older jobs block a younger one ("candidate")

- **Decision**: the candidate test in `contracts/admission.md`, evaluated in Python
  inside the admission transaction with the same `workspace_compatible` rule `acquire`
  uses for the job itself. Order key `(accepted_at, job_id)`, host-wide.
- **Rationale**: Reusing the admission rule makes "could start right now" exact for
  exclusive, parallel-safe and sync-generation jobs (FR-002, US2 AS3). Dependency, sync
  and policy waits carry their own reasons; drains and status reads re-record a reason
  once they observe the wait has ended.
- **No stuck head**: every exception inside `get()`'s admission block moves the job to
  `failed`, so a broken candidate stops being one. Only a registry read error before
  that block leaves it queued, and that affects every job equally. An admitted job holds
  a lease, so it is never a candidate.
- **Alternatives**: a pure SQL predicate (cannot express parallel-safe sharing without
  duplicating the rule; rejected after analyze finding I2).

## R3. What drives admission

- **Decision**: `JobService.drain_queue()` walks queued jobs oldest-first
  (`queued_in_order`) and calls `get(job_id, reconcile=False)` for each, which runs the
  existing admission and launch path. It stops when the scheduler records a host-wide
  refusal (slots, floor, policy) or no slot is free. The refusal kind and the
  re-entrancy flag are thread-local. The remote service runs its sync MCP tools on the
  event-loop thread (FastMCP in `mcp==1.25.0`), and the tick and the supervisor build
  their own repository and connection, so one registry connection is never used by
  two threads; `JobRepository` does not support that.
- **Status reads**: outside a drain, a status read of a queued job runs the drain
  first and only then reads its own job, whose `acquire` stays behind the order gate,
  so stale reasons cannot let a poller jump ahead (analyze I1).
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

## R9. One job reached by two callers

- **Decision**: `acquire` refuses with kind `admitted` when the job already holds a
  lease or is no longer `accepted`/`queued`, inside the transaction.
- **Rationale**: Today a parallel-safe job reached by two callers fails the second
  insert on the unique slot row, and the generic error path then releases the winner's
  leases and marks the job failed. More drains make that race likelier (analyze I4).
