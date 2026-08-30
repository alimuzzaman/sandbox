# Data model: One-Click Host Storage Reclamation

## DeploymentEntry (evidence, produced by the probe)

| Field | Type | Notes |
|---|---|---|
| `name` | str | directory basename |
| `path` | str | absolute path on the target host |
| `size_bytes` | int \| null | null when unmeasured |
| `size_state` | `measured`\|`not_measured`\|`timed_out`\|`unavailable` | |
| `mtime` | float \| null | POSIX seconds |
| `is_workspace` | bool | name contains `-workspace-` or `.workspace-` |
| `is_symlink` | bool | |
| `containers` | list[{id, name, running}] | containers binding this path |
| `registry` | bool | referenced by the instance registry |
| `indexed` | bool | present in the workspace index |
| `hosted` | bool | belongs to a registered hosted site |
| `active_job` | bool | bound by a non-terminal job through exact `workspace_id`; terminal evidence remains retained without an active projection |

An inactive projection is reporting evidence only. Automatic CI checkout release
also requires the accepted controller materialization-authority digest, fresh zero
process/container/mount/binding/lease/job observations, and quarantine identity
revalidation. Names and path correlation never supply that authority.
| `protections` | list[str] | protection reasons already known host-side |

## ClassifiedEntry (decision, produced by `reclaim.py`)

`DeploymentEntry` plus:

| Field | Type | Notes |
|---|---|---|
| `lifecycle_class` | `PROTECTED`\|`LIVE`\|`STOPPED`\|`REGONLY`\|`BASE`\|`ORPHAN`\|`UNKNOWN` | exactly one |
| `reason` | str | the rule that produced the class |
| `evidence` | tuple[str, …] | ordered, stable strings |
| `lease` | LeaseState | see below |
| `in_use` | bool | §4 of the policy contract |
| `age_seconds` | int \| null | |

## VolumeEntry

| Field | Type | Notes |
|---|---|---|
| `name` | str | docker volume name |
| `project` | str \| null | compose project label |
| `size_bytes` | int \| null | |
| `mounted_running` | bool | a running container mounts it |
| `workspace` | str \| null | captured workspace segment when the name matches §3 |
| `eligible` | bool | |
| `reason` | str | `workspace_scoped_volume` or the protection rule name |

## LeaseState

| Field | Type | Notes |
|---|---|---|
| `state` | `released`\|`expired`\|`active`\|`none` | |
| `expires_at` | str \| null | ISO-8601 UTC |
| `released` | bool | |
| `source` | `lease`\|`default_window` | where the expiry came from |

## ReclaimCandidate

| Field | Type |
|---|---|
| `seq` | int (1-based, stable within a plan) |
| `kind` | `worktree`\|`volume`\|`runtime`\|`download_cache`\|`job_artifact`\|`container` |
| `locator` | str |
| `display_name` | str |
| `bytes` | int |
| `mtime` | float \| null |
| `lifecycle_class` | str |
| `tier` | `safe`\|`tmp`\|`all` |
| `reason` | str |
| `stop_containers` | tuple[str, …] |

Serialized into the existing `CleanupCandidate` as: `resource_id = "<kind>-<sha256[:20]>"`,
`locator`, `expected_size_bytes = bytes`, `expected_reclaimable_bytes = bytes`, and an
`evidence_digest` over `(kind, locator, class, tier, reason, mtime)` — so a plan cannot be
executed against a host whose evidence changed.

## ManifestRecord

| Field | Type | Notes |
|---|---|---|
| `schema` | int | `1` |
| `run_id` | str | 32 hex |
| `seq` | int | matches the candidate |
| `phase` | `intent`\|`outcome` | |
| `path` / `locator` | str | |
| `kind`, `class`, `tier`, `reason` | str | intent only |
| `bytes` | int | intent only |
| `status`, `elevated`, `verified_absent` | | outcome only |
| `at` | str | ISO-8601 UTC |
| `trigger` | `manual`\|`threshold`\|`reap` | intent only |

Stored at `$SANDBOX_HOME/runtime/resources/deletions/<run_id>.jsonl`, mode `0600`,
append-only, one file per run.

## Persisted lease record

`$SANDBOX_HOME/runtime/resources/leases/<name>.json`, mode `0600`:

```json
{"schema":1,"name":"lenzora-workspace-a655","expires_at":"2026-08-23T09:00:00Z",
 "released":false,"released_at":null,"updated_at":"2026-08-16T09:00:00Z","note":null}
```

## DiskPressure

`{level, free_ratio, free_bytes, total_bytes, warn_ratio, critical_ratio,
threshold_crossed, auto_tier, guidance}` — emitted inside `data.reclaim.capacity_pressure`
and, for parity with the existing surface, alongside the network pressure block.
