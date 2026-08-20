# Data Model: Scheduled storage-pressure monitor and safe-tier reaper

## StorageMonitorPolicy (resolved, in-memory)

Produced by `normalize_storage_monitor(raw)` in `sandbox/config/storage_monitor.py`.
Pure: no I/O, no host contact. Every field has a built-in default, so an absent block
resolves to a valid policy.

| Field | Type | Default | Validation |
|---|---|---|---|
| `warn_ratio` | float | `0.15` | `0 < warn_ratio < 1` |
| `critical_ratio` | float | `0.05` | `0 < critical_ratio <= warn_ratio` |
| `auto_enabled` | bool | `false` | strictly boolean; a string or number is rejected |
| `auto_tier` | str | `"safe"` | MUST equal `"safe"`; any other value is rejected with `invalid_auto_tier` |
| `auto_ratio` | float | `critical_ratio` | `0 < auto_ratio <= warn_ratio` |
| `reap_enabled` | bool | `false` | strictly boolean |
| `reap_ttl` | str or null | `null` (inherits the 7-day reaper default) | duration form accepted by `reclaim.parse_duration` |
| `schedule_calendar` | str | `"hourly"` | non-empty, no control characters |
| `schedule_randomized_delay` | str | `"5min"` | systemd time span |
| `schedule_timeout` | str | `"30min"` | systemd time span |
| `record_max_age_seconds` | int | `21600` (6h) | `> 0` |

Rejections raise `StorageMonitorConfigError(message, code)` with codes
`invalid_auto_tier`, `invalid_threshold`, `invalid_threshold_order`, `invalid_flag`,
`invalid_duration`, `invalid_schedule_field`, `unknown_key`.

**Resolution order** (first wins is *lowest* precedence; later layers override per key):

1. built-in defaults above
2. `resources.monitor` in `sandbox.yml` (machine defaults)
3. `resources.monitor` in `$SANDBOX_HOME/sandbox.local.yml` (per-machine override)
4. `remotes.<name>.storage_monitor` in `$SANDBOX_HOME/sandbox.local.yml` (per-target)

Layer 4 applies only when a target is named. Naming a target that is not present in
`remotes:` raises `unknown_target` and never falls back to the local machine.

## MonitorRunRecord (durable)

One JSON object per target at
`$SANDBOX_HOME/runtime/resources/monitor/<target_digest>.json`, mode `0600`, written by
atomic replace. `<target_digest>` is `sha256("remote:<name>")` or `sha256("local")`,
truncated to 24 hex characters, so a target name never appears in a path.

| Field | Type | Meaning |
|---|---|---|
| `schema` | int | `1` |
| `target` | object | `{kind, name}` — `kind` is `local` or `remote` |
| `at` | str | ISO-8601 UTC timestamp of the run |
| `trigger` | str | `manual` or `scheduled` |
| `level` | str | `normal`, `warning`, `critical`, `unknown` |
| `free_bytes` / `total_bytes` | int or null | as measured |
| `free_ratio` | float or null | `free_bytes / total_bytes` |
| `warn_ratio` / `critical_ratio` / `auto_ratio` | float | thresholds actually used |
| `threshold_crossed` | str or null | `warn_ratio`, `critical_ratio`, or null |
| `guidance` | str | the next command, from the classifier |
| `auto` | object | `{enabled, eligible, tier, ran, reclaimed_bytes, run_id, reason}` |
| `reap` | object | `{enabled, dry_run, candidates, reclaimed_bytes, reason}` |
| `inventory_status` | str | the reclaim inventory status, e.g. `complete` or `partial` |
| `errors` | list | zero or more `{code, message}` for a run that could not complete |

The record is the only thing `sb doctor` reads. It is a *last-run* record, not a history:
each run replaces it. Deletion history remains 042's per-run manifest.

## SchedulePlan (in-memory, rendered)

| Field | Type | Meaning |
|---|---|---|
| `target` | object | `{kind, name}` |
| `platform` | str | `systemd` or `launchd` |
| `enabled` | bool | always `false` in a plan |
| `calendar` / `randomized_delay` / `timeout` | str | from the policy |
| `command` | list[str] | the exact argv the timer runs |
| `units` | dict | filename → file contents |
| `paths` | dict | filename → absolute install path |
| `activate_command` / `deactivate_command` | list[str] | shown, not run, by a plan |

`command` is always exactly
`["sb", "resources", "monitor", "--scheduled", "--json"]` plus `["--remote", name]` when a
remote is targeted. The renderer refuses any other program, mirroring
`sandbox/recovery/scheduler.py`'s `invalid_schedule_command` guard.

## State transitions

```text
                 +-- policy invalid ---------------------> refused (no host contact)
                 |
resolve policy --+-- lock held ------------------------> skipped (lock_held)
                 |
                 +-- measure --+-- unmeasurable --------> level=unknown, record, no auto
                               |
                               +-- normal --------------> record, no auto, reap per policy
                               |
                               +-- warning -------------> record + warn, auto only if
                               |                          free_ratio <= auto_ratio
                               +-- critical ------------> record + warn, auto if enabled
```

The automatic branch can only ever call `ReclaimService.cleanup(tier="safe",
trigger="scheduled_auto")`. The reap branch is independent of the pressure level: it runs
every scheduled invocation, as a dry run unless `reap_enabled` is true.
