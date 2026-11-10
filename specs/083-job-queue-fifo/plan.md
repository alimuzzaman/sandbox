# Implementation Plan: Oldest-first durable-job admission

**Branch**: `fix/job-queue-fifo` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/083-job-queue-fifo/spec.md`

## Summary

Make host-slot admission oldest-first and driven by activity on the host rather than by
which client polls. Three pieces:

1. **An order gate inside `JobScheduler.acquire`.** It runs in the same immediate SQLite
   transaction that already serializes slot assignment. A job may take a slot only if no
   older *eligible* queued job exists on the host. Eligible means it could take a free
   slot right now: queued with the capacity reason, not an aggregate parent, and no
   active lease on its own workspace.
2. **A drain, `JobService.drain_queue()`.** It offers free slots to queued jobs
   oldest-first through the existing per-job admission path in `get()`, and stops at the
   first host-wide refusal (slots, floor, policy). It runs at the end of every
   `reconcile_startup` (so on every `sb` job command and service start), after every
   release inside the service, after an order refusal on a status read, from a finished
   job's supervisor (only for the host's real registry), and on a 20-second tick in the
   long-lived remote service.
3. **Proved-gone sweep on the tick.** Each tick builds fresh job dependencies, which runs
   the existing `reconcile_startup` proof. Running jobs whose supervisor is proved gone
   become `interrupted` / `supervisor_lost` and release their slot; then the tick drains.

Queue `position` becomes the count of older eligible jobs host-wide plus one, so it
matches start order. No payload key changes are planned, so no protocol bump.

## Technical Context

**Language/Version**: Python 3.10+ on the controller, the remote host's python3 for the
runtime (stdlib plus PyYAML, as today)

**Primary Dependencies**: none new; SQLite (stdlib `sqlite3`), Starlette/uvicorn already
used by the remote service

**Storage**: the host job registry `$SANDBOX_HOME/runtime/jobs/registry.sqlite3`
(`jobs`, `workspace_leases`, `host_capacity_leases`); no schema change

**Testing**: `python3 -m unittest` targeted modules, through Haiku 5.5 helpers; remote
runs via `controller-44bcdba/sb job-start --remote xspeed-dev --cancel-on-stall`

**Target Platform**: Linux remote hosts (xspeed-dev) and macOS/Linux local controllers

**Project Type**: CLI plus a long-lived control service

**Performance Goals**: a freed slot is taken within 30 s with the service running
(SC-002); one drain pass handles at most 50 queued jobs

**Constraints**: admission stays serialized by the immediate transaction; no new lock
file; no change to `sandbox/resources/*`, hosting job paths or the other files the brief
puts off limits

**Scale/Scope**: up to a few hundred queued jobs per host; `max_parallel` 1-32

## Constitution Check

| Principle | Status |
|---|---|
| I Per-project instance model | Not affected (no instance resolution change) |
| II Registry single source of truth | Not affected; job registry stays authoritative, no new state file |
| III Single entry, modular package | Code stays in `sandbox/jobs`, `sandbox/application`, `mcp/wp-server`; no new top-level entry |
| IV Live-stack verification | Unit and concurrency tests plus a run on xspeed-dev; the post-migrate evidence comes from the orchestrator's live check (quickstart) |
| V Idempotency and docs-with-code | The drain is re-runnable by design (FR-009); `docs/remote-job-runtime.md` and CHANGELOG change in the same commits |
| VI Parity before removal | Nothing removed; admission on status read stays, only its order changes |

Gate: PASS. Re-checked after Phase 1 design: PASS (no new storage, no removal).

## Project Structure

### Documentation (this feature)

```text
specs/083-job-queue-fifo/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/admission.md
├── checklists/requirements.md
└── tasks.md            # speckit-tasks
```

### Source Code (repository root)

```text
sandbox/jobs/scheduler.py          # order gate, eligibility SQL, refusal kinds, free_slots, queued_in_order, host-wide position
sandbox/application/job_service.py # drain_queue, drain triggers (release, reconcile_startup end, order refusal)
sandbox/jobs/supervisor.py         # post-release drain for the host's real registry
sandbox/application/context.py     # drain_after_release helper (default registry only)
mcp/wp-server/server.py            # 20 s admission tick in the streamable-http service
docs/remote-job-runtime.md         # behaviour section
CHANGELOG.md
tests/test_job_queue_fifo.py       # new
```

**Structure Decision**: Keep admission policy in `JobScheduler` (it already owns
leases and the serializing transaction), and orchestration in `JobService` (it already
owns the per-job launch path). The service tick only composes them.

## Complexity Tracking

| Choice | Why needed | Simpler alternative rejected because |
|---|---|---|
| Order gate inside `acquire` and a separate drain | The gate guarantees order under every race; the drain is what actually admits the head | A drain alone cannot stop a racing status read in another process from taking the slot first; a gate alone leaves the head unadmitted until someone reads it |
| Fresh dependencies per tick | The service's shared connection is used from many threads; a tick on it could interleave transactions | Reusing the cached service connection risks nested `BEGIN` errors under load |

## Open question for the owner (not in scope)

Every built-in execution profile has `cancelOnStall: false`. The 2026-11-10 slot-holders
were live-but-silent, not process-gone, so this feature would not have freed them. Turning
stall-cancel on by default changes behaviour for every job on every host; until the owner
decides, callers that need it (guided setup) pass `--cancel-on-stall`.
