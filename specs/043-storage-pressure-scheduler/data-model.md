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

## MonitorLock (persistent, owner-only lease)

`monitor_lock(target, *, stale_after_seconds=1800)` arbitrates one local monitor
run per target. The explicit 1800-second grace is the conservative default that
corresponds to the current `schedule_timeout: 30min`; a runner with a resolved
timeout passes its value explicitly. The canonical artifact is the opaque
`<digest>.guard` in the same validated 0700 parent directory. It is mode 0600 and
persists after release: the file is both the nonblocking POSIX `flock(LOCK_EX |
LOCK_NB)` liveness lock and the state evidence. Its compact ASCII JSON object is
schema 2 and has exactly these fields:

```json
{"created_at":"2026-08-20T00:00:00Z","owner_token":"0123456789abcdef0123456789abcdef","pid":123,"released_at":null,"schema":2,"state":"active"}
```

`pid` is positive, `created_at` is UTC, and `owner_token` is random 32-character
lowercase hex. Active state requires `released_at: null`; released state requires
a UTC `released_at` not earlier than `created_at`. All state reads and writes use
the retained guard fd (`lseek`/`read`/`ftruncate`/`write`/`fsync`); no lifecycle
operation unlinks or replaces either lock pathname. Replacing the pathname after
the initial fd identity checks detaches the old lease and fails closed without
touching the successor.

`monitor_lock` returns a context manager with `acquired` and one of `acquired`,
`stale_lock_recovered`, or `lock_held` as `reason`; a held lock never waits or
raises. An empty or released guard can be written as a fresh active lease. Active
evidence is recoverable only when its UTC age is strictly older than the grace and
`kill(pid, 0)` returns definite `ESRCH`; live/EPERM, young, future, malformed,
unreadable, symlink, nonregular, multi-link, wrong-mode, or ambiguous-PID evidence
is held and left untouched. Release writes and fsyncs a released marker through
the held fd, then unlocks/closes idempotently. If that write fails, the lease still
unlocks while active evidence is retained, so a later caller fails closed.

During one transition from an empty guard, the unreleased draft `<digest>.lock`
may be inspected for compatibility. Missing legacy state bootstraps v2; valid
old/dead state migrates into a fresh v2 active lease with
`stale_lock_recovered`. Young/live, malformed, or unsafe legacy state returns
`lock_held`; the legacy file is never deleted. Once v2 state exists, the guard is
authoritative. The flock is advisory and protects cooperating callers only; this
spec contains no live runner, timer, or process-isolation proof.

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
| `activate_command` / `deactivate_command` | list[str] | fixed local scheduler transitions |
| `activation_supported` / `timeout_enforced` | bool | true only for the bounded systemd renderer |

`command` is always exactly
`["sb", "resources", "monitor", "--scheduled", "--json"]` plus `["--remote", name]` when a
remote is targeted. The renderer refuses any other program, mirroring
`sandbox/recovery/scheduler.py`'s `invalid_schedule_command` guard.

Activation writes an owner-only canonical installed-plan receipt beside the units before
the scheduler transition. Matching files are never active-state proof: activation always
re-runs the idempotent transition. Confirmed deactivation reads the receipt instead of
current policy, so policy drift or remote removal cannot strand a known installation;
missing or invalid evidence fails with `schedule_evidence_unknown`. Before snapshot, read,
write, rollback, or removal, the complete scheduler-directory chain must have the expected
owner, directory type, and safe mode, with no symlink component. Pre-transition update
failure restores the exact prior units, modes, and receipt. Every unit/receipt read is capped
at 256 KiB and compares bounded bytes to canonical UTF-8; malformed content is a bounded
refusal before scheduler state changes.

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
