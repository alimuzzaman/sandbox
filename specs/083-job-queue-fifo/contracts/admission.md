# Contract: host admission order

Internal contract between the scheduler, the job service and the remote service. No CLI
flag, MCP tool or payload key changes.

## `JobScheduler.acquire(row, parallel_safe=False)`

Inside one immediate transaction, in this order:

1. Reap terminal leases (unchanged).
2. Policy invalid: raise `SchedulerPolicyInvalid` (`last_refusal = "policy"`).
3. Floor unavailable: raise `WorkspaceBusy` (`last_refusal = "floor"`).
4. Own workspace incompatible: raise `WorkspaceBusy` (`last_refusal = "workspace"`).
5. **New**: an older eligible queued job exists: raise `WorkspaceBusy`
   (`last_refusal = "order"`, `queue_reason` stays `workspace_or_capacity_busy`).
6. Held slots >= `max_parallel`: raise `WorkspaceBusy` (`last_refusal = "capacity"`).
7. Insert the workspace and slot leases (unchanged). `last_refusal = None`.

Eligibility is defined in research R2. The row being admitted is never its own blocker.

## `JobScheduler.queued_in_order(limit)`

Returns the ids of queued, non-aggregate jobs ordered by `(accepted_at, job_id)`, at most
`limit`.

## `JobScheduler.free_slots()`

`max_parallel` minus held slots of active jobs, floored at 0, using the last loaded
policy (no config read).

## `JobService.drain_queue(limit=50) -> list[str]`

- Returns the ids of jobs that left `queued` during this pass.
- No-op (returns `[]`) without a scheduler, or when called re-entrantly.
- For each id from `queued_in_order`: stop if `free_slots() == 0`; call
  `get(id, reconcile=False)`, suppressing per-job errors; then stop if `last_refusal`
  is `capacity`, `floor` or `policy`.
- Safe to run concurrently from several processes; the gate in `acquire` keeps order
  and the slot limit.

## Drain triggers

| Trigger | Where |
|---|---|
| End of `reconcile_startup` | `JobService.reconcile_startup` |
| Terminal job released on a status read | `JobService.get` |
| Cancel released a lease | `JobService.cancel` |
| Order refusal on a status read (not inside a drain) | `JobService.get` |
| Supervisor released its leases | `sandbox/jobs/supervisor.py`, through `context.drain_after_release`, default registry only |
| Every 20 s | remote service tick in `mcp/wp-server/server.py` |

## Queue details

`queue_details(row)["position"]` = 1 + count of older eligible queued jobs, host-wide.
Keys unchanged: `reason`, `position`, `blocking_jobs`, `namespace`.
