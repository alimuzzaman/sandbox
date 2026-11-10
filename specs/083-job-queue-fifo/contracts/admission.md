# Contract: host admission order

Internal contract between the scheduler, the job service and the remote service. No CLI
flag, MCP tool or payload key changes. This file is the single definition of a
*candidate*; the other artifacts link here.

## Candidate (an older job that blocks a younger one)

A queued job C blocks the admission of job J when all of these hold, evaluated inside
J's admission transaction:

1. `C.lifecycle = 'queued'` and `(C.accepted_at, C.job_id) < (J.accepted_at, J.job_id)`.
2. C is not an aggregate: `C.kind` is not `matrix` or `plan`, and no row has
   `parent_job_id = C.job_id`.
3. `C.queue_reason = 'workspace_or_capacity_busy'` (dependency, sync-generation and
   policy reasons are not candidates; a drain or status read re-records the reason once
   it observes the wait is over).
4. C holds no lease itself (an admitted job is never a candidate).
5. The workspace-sharing rule `acquire` applies (`workspace_compatible`, shared code)
   would let C take its workspace now, with C's own `parallel_safe` (or isolated mode),
   `sync_generation_id` and `source_access`.

Host-wide conditions (slots, floor, policy) are not part of the test: they stop J too.

## `JobScheduler.acquire(row, parallel_safe=False)`

Inside one immediate transaction, in this order. Each refusal raises `WorkspaceBusy`
(or its subclass) with `.kind`, and stores the kind in the calling thread's
`last_refusal`:

1. Reap terminal leases (unchanged).
2. **New**: J already holds a lease, or J's lifecycle is not `accepted`/`queued`:
   kind `admitted`. The caller leaves J alone (FR-015).
3. Policy invalid: `SchedulerPolicyInvalid`, kind `policy`.
4. Floor unavailable: kind `floor`.
5. J's own workspace incompatible (`workspace_compatible`): kind `workspace`.
6. **New**: a candidate older than J exists: kind `order`.
7. Held slots >= `max_parallel`: kind `capacity`.
8. Insert the workspace and slot leases (unchanged); `last_refusal = None`.

`queue_reason` values are unchanged: refusals 2-7 keep `workspace_or_capacity_busy`, or
the policy code for 3.

## `JobScheduler.queued_in_order(after=None, limit=500)`

One page of ids of queued, non-aggregate jobs ordered by `(accepted_at, job_id)`,
starting after the `(accepted_at, job_id)` key `after`.

## `JobScheduler.free_slots()`

`max_parallel` minus slots held by active jobs, floored at 0. It refreshes the policy
first, so a fresh scheduler is never read with a stale limit.

## `JobScheduler.record_queue_reason(job_id, reason)`

Updates `queue_reason` of a job that is still `queued`, nothing else; a no-op otherwise.

## `JobService.drain_queue() -> list[str]`

- Returns the ids of jobs that left `queued` during this pass.
- No-op (returns `[]`) without a scheduler, or when already draining in this thread.
- Walks every queued job, page by page through `queued_in_order`, so blocked older jobs
  never hide a later startable one (FR-003). Before each job: stop if
  `free_slots() == 0`, then clear this thread's `last_refusal`. Calls
  the admission path of `get(id, reconcile=False)`, suppressing per-job errors. After
  it: stop if this thread's `last_refusal` is `capacity`, `floor` or `policy`;
  otherwise continue (`workspace`, `order`, `admitted`, dependency waits, launch
  failures).
- Safe to run concurrently from several threads and processes; the gate in `acquire`
  keeps order and the slot limit, and kind `admitted` stops double admission.

## Status read of a queued job (`JobService.get`)

Outside a drain, `get` of a queued job runs `drain_queue()` and then returns that job's
fresh snapshot. It never calls `acquire` for that job directly.

## Drain triggers

| Trigger | Where |
|---|---|
| End of `reconcile_startup` | `JobService.reconcile_startup` |
| Status read of a queued job (outside a drain) | `JobService.get` |
| Terminal job released on a status read | `JobService.get` |
| Cancel released a lease | `JobService.cancel` |
| Supervisor released its leases | `sandbox/jobs/supervisor.py` via `context.drain_after_release`, default registry only |
| Every 20 s | remote service tick in `mcp/wp-server/server.py` |

## Queue details

`queue_details(row)["position"]` = 1 + the number of candidates older than the row
(host-wide). Keys unchanged: `reason`, `position`, `blocking_jobs`, `namespace`.
