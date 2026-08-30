# Data Model: Remote Job Runtime

## Storage boundary

Each execution host owns one repository at
`$SANDBOX_HOME/runtime/jobs/registry.sqlite3` and one job directory per job. The
repository is accessed only through `JobRepository`; transports never read it directly.
Remote CLI/MCP calls execute the repository/service on the remote host.

SQLite uses WAL, foreign keys, a bounded busy timeout, explicit transactions, and a
schema-version table. Acceptance and terminal transitions use full durability. Log and
artifact files are referenced by relative Sandbox-owned paths; user-supplied absolute
paths are never stored as repository-owned paths.

## Enumerations

### Job kind

`exec`, `test`, `wordpress_unit`, `wordpress_integration`, `e2e`, `ci_workflow`,
`ci_job`, `ci_matrix_cell`, `plan`, `legacy_async`, `hermes`.

### Lifecycle

| State | Meaning | Allowed next states |
|---|---|---|
| `accepted` | Durable specification exists; supervisor launch not yet confirmed | `queued`, `running`, `failed`, `interrupted` |
| `queued` | Waiting for host/workspace/dependency lease | `running`, `cancelling`, `cancelled`, `timed_out`, `failed`, `interrupted` |
| `running` | Child process group or parent coordinator is active | `cancelling`, `succeeded`, `failed`, `timed_out`, `cancelled`, `interrupted` |
| `cancelling` | Graceful or force cancellation requested | `cancelled`, `failed`, `interrupted` |
| `succeeded` | Exit/result policy passed and output/artifact finalization is complete | terminal |
| `failed` | Command or required finalization failed | terminal |
| `timed_out` | Effective deadline reached | terminal |
| `cancelled` | Explicit cancellation completed | terminal |
| `interrupted` | Supervisor/host/process relationship was lost and success is unknown | terminal |

Terminal states are immutable except for retention metadata and post-terminal artifact
expiry. A retry creates a new attempt/job.

### Health

`unknown`, `active`, `quiet`, `suspected_stalled`, `stuck`,
`supervisor_unresponsive`, `orphaned`, `process_missing`, `unreachable`, `terminal`.

### Output completeness

`complete`, `active`, `storage_pressure`, `limit_exceeded`, `write_failed`,
`redaction_failed`, `index_failed`, `unknown`.

### Lease mode

`exclusive`, `shared_parallel_safe`, `lifecycle_exclusive`, `host_capacity`.

### Cleanup state

`not_requested`, `pending`, `retained`, `completed`, `failed`, `blocked_active_jobs`.

## Entities and tables

### `schema_meta`

| Field | Type | Constraints |
|---|---|---|
| `key` | TEXT | primary key |
| `value` | TEXT | non-null |

Contains `schema_version`, migration timestamp, and writer runtime version.

### `jobs`

One durable accepted execution or aggregate parent.

| Field | Type | Constraints / meaning |
|---|---|---|
| `job_id` | TEXT | primary key; 32 lowercase hex for new jobs; readers accept 16-hex legacy IDs |
| `request_id` | TEXT nullable | idempotency key, unique with target/project scope |
| `parent_job_id` | TEXT nullable | FK to jobs; no cycles |
| `root_job_id` | TEXT | FK to jobs; self for root |
| `attempt` | INTEGER | >=1 |
| `retry_of_job_id` | TEXT nullable | FK to prior job |
| `kind` | TEXT | enumerated job kind |
| `project_root` | TEXT | canonical host-local deployed project root |
| `project_identity` | TEXT | stable hash/slug, never an unowned global project |
| `target_kind` | TEXT | `local` or `remote` as submitted/result provenance |
| `remote_name` | TEXT nullable | registered remote selected by caller/config |
| `workspace_id` | TEXT nullable | exact opaque workspace owner fixed before acceptance; null only for legacy/unindexed rows |
| `workspace_label` | TEXT | validated label |
| `workspace_mode` | TEXT | persistent or isolated/ephemeral |
| `lifecycle` | TEXT | lifecycle enum |
| `health` | TEXT | last classified health |
| `priority` | INTEGER | queue ordering with bounded range |
| `queue_reason` | TEXT nullable | host capacity, workspace lease, dependency |
| `queue_position` | INTEGER nullable | advisory snapshot |
| `command_json` | TEXT | explicit argv or normalized parent plan; no secret values |
| `cwd_relative` | TEXT | path constrained beneath deployed project |
| `environment_keys_json` | TEXT | names/provenance only; values passed via protected launch input |
| `execution_profile` | TEXT | resolved profile |
| `output_profile` | TEXT | resolved presentation profile |
| `deadline_seconds` | INTEGER | finite, >0, <=604800 |
| `deadline_source` | TEXT | explicit/profile/workflow/fallback |
| `stall_seconds` | INTEGER | finite and positive |
| `cancel_on_stall` | INTEGER | boolean; false by default |
| `cleanup_policy` | TEXT | retain/always/on-success/ephemeral |
| `source_identity` | TEXT | deployment identity |
| `source_commit` | TEXT nullable | local commit identity |
| `source_dirty_digest` | TEXT nullable | exact dirty/untracked manifest digest |
| `accepted_at` | TEXT | UTC RFC3339 |
| `queued_at` | TEXT nullable | UTC RFC3339 |
| `started_at` | TEXT nullable | UTC RFC3339 |
| `finished_at` | TEXT nullable | UTC RFC3339 |
| `deadline_at` | TEXT | UTC RFC3339 |
| `updated_at` | TEXT | UTC RFC3339 |
| `exit_code` | INTEGER nullable | exact child exit code when known |
| `termination_reason` | TEXT nullable | stable code plus safe detail elsewhere |
| `output_completeness` | TEXT | completeness enum |
| `cleanup_state` | TEXT | cleanup enum |
| `integrity_sha256` | TEXT nullable | terminal canonical result/log index identity |
| `result_json` | TEXT nullable | bounded structured result, compatibility keys included |
| `submission_json` | TEXT nullable | additive bounded canonical policy/reference snapshot for lossless retry; null on legacy rows; no environment values or arbitrary JSON |

Indexes: `(project_identity, accepted_at)`, `(workspace_label, lifecycle)`,
`(parent_job_id, attempt)`, `(lifecycle, priority, accepted_at)`, and unique
`(target_kind, COALESCE(remote_name,''), project_identity, request_id)` when request ID
is non-null.

For terminal workspace cleanup, `workspace_id` is the only deletion identity.
`project_identity`, `workspace_label`, paths, age, and naming patterns are
cross-check evidence and can never substitute for a missing or conflicting ID.
Terminal job/result rows remain retained even after an authorized disposable
checkout is released.

### `process_identities`

One current supervisor and optional child identity per leaf job.

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | PK/FK jobs |
| `host_boot_id` | TEXT | Linux boot ID or platform fallback identity |
| `supervisor_pid` | INTEGER | >0 |
| `supervisor_start_identity` | TEXT | `/proc` start ticks or platform-safe start identity |
| `supervisor_nonce_hash` | TEXT | launch nonce identity without exposing nonce |
| `child_pid` | INTEGER nullable | >0 when running |
| `child_pgid` | INTEGER nullable | owned process group |
| `child_start_identity` | TEXT nullable | PID-reuse guard |
| `child_executable_identity` | TEXT nullable | safe executable basename/hash |
| `recorded_at` | TEXT | UTC RFC3339 |
| `last_verified_at` | TEXT nullable | last liveness verification |

Identity verification requires matching boot ID, PID, and start identity. PID alone is
never enough for success, health, or cancellation.

### `heartbeats`

Latest heartbeat is stored transactionally; optional history is kept in metric files.

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | PK/FK jobs |
| `supervisor_at` | TEXT | last supervisor heartbeat |
| `child_observed_at` | TEXT nullable | last valid child identity observation |
| `last_output_at` | TEXT nullable | last persisted output event |
| `last_activity_at` | TEXT nullable | output/progress/resource movement |
| `last_progress_at` | TEXT nullable | declared progress event |
| `last_metric_at` | TEXT nullable | resource sample time |
| `metric_digest` | TEXT nullable | compact movement identity |
| `health_evidence_json` | TEXT | bounded safe evidence and thresholds |

### `workspace_leases`

| Field | Type | Constraints |
|---|---|---|
| `lease_id` | TEXT | primary key |
| `target_namespace` | TEXT | host-local identity that separates local and each remote |
| `project_identity` | TEXT | project owner |
| `workspace_label` | TEXT | selected workspace |
| `job_id` | TEXT | FK jobs |
| `mode` | TEXT | lease enum |
| `parallel_safe` | INTEGER | explicit declaration |
| `acquired_at` | TEXT | UTC RFC3339 |
| `expires_at` | TEXT | renewable safety expiry |
| `heartbeat_at` | TEXT | lease heartbeat |

Unique/transactional constraints prevent an exclusive lease coexisting with any active
lease and prevent shared leases unless all active holders are declared shared-safe.
Reset/deploy/destroy uses `lifecycle_exclusive` and cannot overlap execution.

### `host_capacity_leases`

| Field | Type | Meaning |
|---|---|---|
| `slot` | INTEGER | primary key within host capacity |
| `job_id` | TEXT | unique FK jobs |
| `acquired_at` | TEXT | UTC RFC3339 |
| `heartbeat_at` | TEXT | renewable lease heartbeat |

Expired leases are reclaimed only after process-identity reconciliation.

### `output_streams`

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | FK jobs |
| `stream` | TEXT | stdout/stderr/combined |
| `first_sequence` | INTEGER | retained range start |
| `next_sequence` | INTEGER | exclusive next sequence |
| `bytes_stored` | INTEGER | redacted payload bytes |
| `events_stored` | INTEGER | event count |
| `segments` | INTEGER | segment count |
| `last_segment_bytes` | INTEGER | append offset |
| `complete` | INTEGER | terminal index closed |
| `sha256` | TEXT nullable | terminal stream identity |
| `available` | INTEGER | false after explicit cleanup removes retained bytes |
| `updated_at` | TEXT | UTC RFC3339 |

`bytes_stored` is the size of the redacted retained payload and is independent of
response presentation. Output responses retain the legacy `bytes_read` meaning and
may additionally report `rendered_bytes`, the UTF-8 size of the final returned data
string (including base64 text when that encoding is requested).

The payload itself lives in `stdout/00000000.log`, `stderr/00000000.log`, etc. Combined
order lives in `events.ndjson`; entries reference payload stream/segment/offset/length.
After 24 hours, closed terminal segments may become `.log.gz`; the index records
compression and uncompressed length.

### `job_events`

Only bounded semantic events are indexed in SQLite; high-volume output ordering is in
the append-only file index.

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | FK jobs |
| `sequence` | INTEGER | monotonic per job |
| `kind` | TEXT | lifecycle/health/progress/artifact/annotation/completion |
| `occurred_at` | TEXT | UTC RFC3339 |
| `payload_json` | TEXT | bounded redacted data |

Primary key `(job_id, sequence)`.

### `metrics_index`

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | PK/FK jobs |
| `samples` | INTEGER | count |
| `first_at` / `last_at` | TEXT | retained time range |
| `sha256` | TEXT nullable | terminal identity |
| `complete` | INTEGER | collection finalized |
| `available` | INTEGER | false after explicit cleanup removes retained samples |

Samples live in `metrics.jsonl` and may include process CPU time, RSS, process count,
I/O counters where available, process state, load, free disk, and supervisor heartbeat.
Unsupported fields are null with a capability list; they are never fabricated.

### `artifacts`

| Field | Type | Meaning |
|---|---|---|
| `artifact_id` | TEXT | primary key |
| `job_id` | TEXT | FK jobs |
| `declared_path` | TEXT | caller-requested workspace-relative pattern |
| `stored_relative_path` | TEXT | path under job artifact root |
| `display_name` | TEXT | safe name |
| `kind` | TEXT | file/archive/manifest |
| `size_bytes` | INTEGER | bounded size |
| `sha256` | TEXT | content identity |
| `media_type` | TEXT | detected/declared safe media type |
| `created_at` | TEXT | UTC RFC3339 |
| `expires_at` | TEXT nullable | retention expiry |
| `status` | TEXT | available/rejected/expired/retrieval_failed |
| `reason` | TEXT nullable | safe rejection/failure code |

Collection resolves literal paths without following unsafe symlinks, requires containment
in the deployed project/workspace, rejects devices/sockets/FIFOs, and applies file/count/
total-size limits before copying. Literal directories become deterministic bounded tar
archives with sorted entries and normalized metadata.

### `compatibility_differences`

| Field | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | FK root CI job |
| `difference_id` | TEXT | versioned catalog identifier |
| `workflow_path` | TEXT | workspace-relative workflow file |
| `location` | TEXT | job/step/key location |
| `severity` | TEXT | block/warn/safe-mode-skip |
| `accepted` | INTEGER | explicit caller acceptance |
| `detail` | TEXT | bounded explanation |
| `catalog_version` | TEXT | detector version |

Primary key `(job_id, difference_id, location)`.

## Per-job directory

```text
$SANDBOX_HOME/runtime/jobs/<job-id>/
├── spec.json                 # immutable redacted normalized request
├── launch.json               # protected supervisor launch metadata, owner-only
├── events.ndjson             # combined ordered output + semantic event references
├── stdout/
│   ├── 00000000.log
│   └── 00000001.log[.gz]
├── stderr/
│   └── 00000000.log[.gz]
├── metrics.jsonl
├── result.json               # canonical terminal result mirror
├── artifacts/
│   ├── manifest.json
│   └── <artifact-id>/...
└── supervisor.log            # bounded internal diagnostics, secret-redacted
```

Files are created owner-only. `spec.json` and `result.json` are atomically replaced via
same-directory temporary files. Append-only files are fsynced at bounded intervals and
at terminal finalization. The SQLite row is authoritative for lifecycle; mirrors aid
recovery and bounded export.

## Core value objects

### `TargetRequest`

- `project_dir`
- explicit target selector: exactly one of local/remote/none
- optional remote name
- workspace label and mode
- operation capability

### `ResolvedTarget`

- canonical local project root
- execution location (`local`/`remote`)
- registered remote identity when remote
- host namespace
- deployed project root on execution host
- workspace label
- resolution source for each field
- required capability set

### `ExecutionPolicy`

- profile name
- deadline seconds/source/reminder
- stall seconds
- graceful cancellation seconds
- cancel-on-stall flag
- output profile
- cleanup policy
- artifact policy

### `JobSubmission`

- target and source identity
- kind and explicit argv or declared child plan
- workspace lease intent
- execution policy
- request ID
- environment references (not persisted secret values)
- workspace-relative cwd
- declared artifacts

### `JobSnapshot`

- stable job identity/relationships
- lifecycle + terminal outcome
- health + evidence
- target/workspace/source identity
- queue state
- timing/deadline
- verified process liveness
- output retained range/completeness
- metrics/artifact availability
- cleanup state
- compatibility keys

## Invariants

1. A job ID is returned only after its durable row and immutable spec exist.
2. A repeated scoped request ID returns the original accepted job and cannot launch a
   duplicate supervisor.
3. A terminal success requires exit-policy success, complete required output, required
   artifact finalization, and terminal repository commit.
4. Remote deploy identity is established before executable job acceptance.
5. A process is considered owned only when boot ID, PID, and start identity match.
6. Cancellation targets only the verified owned process group.
7. An exclusive workspace lease cannot overlap another execution/lifecycle lease.
8. A matrix child has one isolated workspace not shared with sibling cells.
9. Secret redaction precedes log persistence and presentation.
10. Retention never deletes active jobs or workspaces retained by explicit policy.
11. Unreachable remote status is observational and cannot mutate a remote job into a
    false terminal state.
12. Storage/output failure cannot be represented as complete success.

## Convergence amendment — 2026-08-13 (durable workspace metadata/index)

### Workspace index storage

Each execution host additionally owns
`$SANDBOX_HOME/runtime/workspaces/index.sqlite3`. The file is owner-only and uses WAL,
foreign keys, a bounded busy timeout, and an explicit schema/generation record. It is
accessed only through a workspace repository and application service; no transport,
resource provider, CLI adapter, or MCP tool may read the SQLite file or legacy metadata
directly.

### Workspace index tables

| Table | Fields and invariants |
|---|---|
| `workspace_schema` | `schema_version`, `index_generation`, migration identity, and timestamps. Generation increments only after a committed index mutation. |
| `workspaces` | Opaque `workspace_id` primary key; `project_identity`; validated `workspace_label`; mode; lifecycle state; metadata/checkout locators and digests; runtime/Compose identity; generation and timestamps. Unique `(project_identity, workspace_label)`. |
| `workspace_aliases` | `alias_kind`, normalized `alias_digest`, `workspace_id`, evidence digest, and observation time. Unique kind/digest; collisions are retained as explicit ambiguity. |
| `workspace_migrations` | Legacy locator/digest, decision (`adopted`, `unresolved`, `conflict`, `invalid`), bounded reason/evidence, optional workspace ID, and timestamps. The source remains untouched. |
| `workspace_resource_bindings` | Typed resource kind/identity, workspace ID/project identity, active references, evidence digest, lifecycle and observation time; consumed through a typed projection. |
| `workspace_migration_plans` | Immutable plan ID, target identity, complete inventory digest, index generation, candidate decisions, created/expiry timestamps, and state. Apply requires both digest and generation to match. |

Workspace lifecycle is `provisioning`, `ready`, `resetting`, `destroying`, `destroyed`,
or `indeterminate`. An opaque workspace ID remains stable when a checkout or generated
runtime locator moves. Job `workspace_leases` reference the ID plus target namespace;
label-only lookup is a display/filter convenience, never a control identity.

### Legacy metadata discovery

The compatibility source is the exact-depth file
`runtime/jobs/workspaces/<legacy-namespace>/<label>/workspace.json` (under the active
base and any still-supported legacy fallback root). Discovery rejects symlinks, path
escapes, oversized files, malformed JSON, and inconsistent path/field values. It joins
job rows by exact project-root hash/namespace and label and requires exactly one distinct
job `project_identity`; registry/runtime aliases can corroborate but cannot resolve a
conflict. Caller identity is accepted only when it matches an exact expected alias.

Each source obtains a durable migration decision. If the index is empty while relevant
sources are unresolved or conflicting, list/status returns `workspace_index_incomplete`
with bounded decision summaries instead of a false empty result. A malformed or unsafe
source is preserved and reported, never “repaired” in place.

### Crash-safe plan/apply and relocation

Plan creation records the complete source inventory digest and current index generation.
Apply holds a global migration lock and per-workspace locks, rescans before one SQLite
transaction, and refuses drift (`workspace_migration_plan_stale` or
`workspace_ownership_drift`). Startup changes unfinished destructive operations to
`indeterminate`; no reset/destroy action is repeated automatically. Migration and base
relocation only add/move index metadata and regenerate path-bearing locators: they do not
delete legacy files, release networks, destroy containers, or alter job counts.
