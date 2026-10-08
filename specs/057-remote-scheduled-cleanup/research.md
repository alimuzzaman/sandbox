# Research: Scheduled Safe Cleanup on a Remote

## R1 — Transport for enable, disable and status

- **Decision**: new `cleanup_routine_enable|disable|status` actions on the existing `/resources` control route, dispatched in `mcp/wp-server/server.py` `_resource_contract` ahead of the probe allowlist; the client sends them with the public `sandbox.core._remote.remote_resource_request`.
- **Rationale**: this is how the host-memory contract was added (`sandbox/resources/host_memory/remote.py`). It is authenticated, bounded (64 KB body), and needs no change to `_remote.py`.
- **Alternatives**: remote durable job (heavier, no typed result); SSH (forbidden by FR-025).

## R2 — Host unit rendering

- **Decision**: `cleanup_routine/units.py` renders one `sandbox-cleanup-routine.service` + `.timer` pair per host into `~/.config/systemd/user`: `Type=oneshot`, `UMask=0077`, `TimeoutStartSec=<bound+60s>`, `OnCalendar=<cadence>`, `RandomizedDelaySec=<jitter>`, `Persistent=true`. `ExecStart` is the fixed argv `<sb-src>/sb resources routine --routine-run --json`. Write, snapshot/restore and bounded reads reuse helpers from `sandbox/resources/schedule.py`, promoted to public names.
- **Rationale**: `schedule.py` already solves atomic install, rollback and receipt hardening for the local monitor timer.
- **Alternatives**: extending `schedule.py` itself (would mix local-monitor and remote-routine policy; FR-005 forbids reading `schedule_calendar`).

## R3 — Cadence validation

- **Decision**: `systemd-analyze calendar <expr>` on the host, bounded; failure → `invalid_cadence`; its "Next elapse" output gives the next run time shown at enable.
- **Rationale**: FR-005; systemd is the only valid oracle on the host.

## R4 — Shared host reclaim guard

- **Decision**: `sandbox/resources/host_guard.py` defines `RUNTIME/resources/reclaim-host.lock`. The probe's `reclaim_action` (`sandbox/resources/remote.py`) takes `flock(LOCK_EX|LOCK_NB)` on it after `run_id` validation and before the first manifest write, and before that probes, read-only with `LOCK_SH|LOCK_NB`, the host-side apply transaction locks `/run/lock/sandbox-hosting-caddy.lock`, `/run/lock/sandbox-edge-nginx.lock` and `/run/lock/sandbox-docker-pool.lock` (a missing file means not held). Busy → `{"ok": false, "reason": "host_reclaim_busy"}`. `ReclaimService.cleanup` maps that to `status="skipped"`, code `host_reclaim_busy`. The routine also pre-checks the guard before inventory (acquire and release) so a busy host costs no probe.
- **Rationale**: every reclaim path (local cleanup, remote cleanup, monitor `scheduled_auto`, the routine) ends in the probe's `reclaim_action` on the host, so one guard there covers them all (FR-018). The lock paths are duplicated as literals; a parity test imports the originals read-only.
- **Alternatives**: a host apply run marker (needs restricted-file edits; rejected by the D1 decision).

## R5 — Run execution

- **Decision**: `run.py` loads the recorded routine, finalizes any open stale record (FR-016 backstop), pre-checks the guard and apply locks, then, through the local `reclaim_service(None)`, plans the safe tier with exactly the `reap` selection (`plan("safe", exclude_kinds=("runtime",), exclude_names=<effective>)`), refuses with `inventory_incomplete` unless the plan's inventory status is `complete` and untruncated, records `nothing_to_do` (no manifest) when the plan is empty, and otherwise executes that plan with `cleanup(plan_id, confirm=True, trigger="scheduled_routine", budget_seconds=<bound − elapsed>, run_id=<record id>)`. `budget_exhausted` → `timed_out`; a probe `host_reclaim_busy` → `skipped_busy`. Revision comes from `sandbox.services.runtime_revision.runtime_revision(<sb-src root>)`.
- **Rationale**: FR-006 says the selection must equal `workspace reap --tier safe`; using the same plan call as `reap` guarantees it. `reap(confirm=True)` alone cannot satisfy FR-019 (it plans and executes in one step and does not refuse on a partial inventory) and writes a `run_start` manifest line even for an empty plan, so the routine splits plan and execute. Every run plans fresh (FR-017). The record id is passed as the reclaim `run_id` so an open record already names its manifest for the backstop.

## R6 — Runtime match at enable

- **Decision**: the enable request carries `expected_runtime_revision` (the local `runtime_revision(repo root)`); the host compares it with its live revision and refuses `runtime_revision_mismatch` before any write.
- **Rationale**: same pattern as the `/wp-cli` route; avoids the SSH-based service status probe.

## R7 — Policy source

- **Decision**: the operator resolves `schedule_timeout` and `schedule_randomized_delay` via `monitor.resolve_policy(remote)`, sends them at enable; the host re-validates with `config/storage_monitor.py` rules and records them (decision D2).

## R8 — Exclusions

- **Decision**: effective set = routine exclusions ∪ host `resources.reclaim_exclude` (read on the host via its own `load_config`). Validator: non-empty string ≤ 128 characters, no control characters or `/`, at most 32 entries, brackets balanced; otherwise `invalid_exclusion`.
- **Rationale**: `fnmatch` never raises, so FR-014 needs an explicit validator.
- **Finding (implementation)**: the existing reap exclusion matched only display names, so an excluded workspace's package volume (`sandbox-<workspace>_node-modules`) stayed a candidate. `ReclaimService._selection` now drops a workspace-scoped volume whose owner is excluded, reported `excluded_by_request` (FR-013, SC-002). This also applies to `workspace reap --exclude`.

## R9 — Manifest retention

- **Finding**: no code prunes deletion manifests today; "existing manifest retention rules" means manifests are kept. The routine prunes only its own run records (keep 30).
