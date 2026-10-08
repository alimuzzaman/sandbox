# Data Model: Scheduled Safe Cleanup on a Remote

All records live on the host under `$SANDBOX_HOME/runtime/resources/cleanup-routine/`,
owner-only (0600 files, 0700 directory), JSON, schema version 1.

## RoutineConfig (`routine.json`)

| Field | Type | Rule |
|---|---|---|
| `schema` | int | `1` |
| `enabled` | bool | false after disable; the file is kept for status |
| `cadence` | str | systemd calendar expression, validated by `systemd-analyze calendar`; default `daily` |
| `timeout` | str | systemd time span from the operator's resolved `schedule_timeout`; default `30min` |
| `randomized_delay` | str | systemd time span from the resolved `schedule_randomized_delay`; default `5min` |
| `exclusions` | list[str] | ≤ 32 globs, each validated (research R8) |
| `enabled_revision` | str | host runtime revision at enable |
| `enabled_at` / `disabled_at` | str | UTC ISO-8601 |
| `unit` | str | fixed `sandbox-cleanup-routine` |

Transitions: absent → enabled (enable) → enabled (re-enable replaces atomically) → disabled (disable) → enabled.

## RunRecord (`runs/<run_id>.json`)

| Field | Type | Rule |
|---|---|---|
| `schema` | int | `1` |
| `run_id` | str | the reclaim run id (also names the manifest) |
| `started_at` / `ended_at` | str | `ended_at` is null while open |
| `outcome` | enum | `running`, `reclaimed`, `nothing_to_do`, `refused`, `timed_out`, `skipped_busy` |
| `reason` | str or null | typed code when `refused`/`skipped_busy` (e.g. `inventory_incomplete`, `host_reclaim_busy`, `apply_transaction_active`) |
| `bytes_reclaimed` | int | ≥ 0 |
| `removed` / `skipped` | int | counts; the skipped count includes `excluded_by_request` |
| `skipped_reasons` | object | skipped count per reason (for example `excluded_by_request`, `candidate_modified_since_plan`); the spec's "skipped items with reasons" |
| `runtime_revision` | str | host revision at run start |
| `manifest` | str or null | `deletions/<run_id>.jsonl` when any removal was attempted |

Transitions: `running` → one terminal outcome. An open `running` record older than
`timeout + 60s` is finalized to `timed_out` (reason `run_bound_exceeded`) from the
manifest's completed outcomes at the next run or status read. The stored record also
keeps `timeout_seconds` (the bound it ran under) for that check; it is not part of the
public status projection. Retention: newest 30 records by `started_at`.

## Deletion manifest (existing)

Unchanged format; intents and `run_start` carry `trigger: "scheduled_routine"`.

## Host reclaim guard (`runtime/resources/reclaim-host.lock`)

0600 regular file; held with `flock(LOCK_EX|LOCK_NB)` for the duration of the
probe's `reclaim_action`. Probed apply locks (read only, `LOCK_SH|LOCK_NB`):
`/run/lock/sandbox-hosting-caddy.lock`, `/run/lock/sandbox-edge-nginx.lock`,
`/run/lock/sandbox-docker-pool.lock`.
