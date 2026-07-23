# Contract: Durable Job Service

## Purpose

This is the transport-neutral application contract used by CLI, MCP, local adapters,
remote adapters, WordPress, E2E, CI, and compatibility commands. Callers provide value
objects; they do not access SQLite, job directories, PIDs, or SSH directly.

## Submit

`submit(submission: JobSubmission) -> JobAccepted`

Preconditions:

- Target and operation capability have been resolved.
- Remote source deployment has completed and produced a source identity.
- Argv/plan is explicit and valid; no shell script discovery occurs.
- Deadline is finite, positive, and no greater than seven days.
- Workspace/artifact/output policies are valid.

Result:

```json
{
  "ok": true,
  "job_id": "32-lowercase-hex",
  "status": "accepted",
  "kind": "test",
  "target": {"kind": "remote", "remote": "scaleway-sandbox"},
  "workspace": "node-unit",
  "source": {"identity": "sha256:...", "commit": "...", "dirty": true},
  "deadline": {
    "seconds": 1800,
    "source": "explicit",
    "expires_at": "2026-07-18T12:00:00Z",
    "reminder": null
  },
  "output_profile": "smart",
  "cleanup_policy": "retain",
  "idempotent_replay": false,
  "links": {"status": "...", "output": "..."}
}
```

If the scoped `request_id` already exists with the same canonical submission digest,
the original result is returned with `idempotent_replay: true`. Reusing it with a
different digest fails `request_id_conflict`.

Stable errors: `invalid_target`, `unknown_remote`, `remote_not_provisioned`,
`unsupported_capability`, `invalid_workspace`, `invalid_argv`, `invalid_deadline`,
`invalid_output_profile`, `invalid_artifact`, `deploy_failed`, `storage_unavailable`,
`request_id_conflict`, `supervisor_launch_failed`.

## Get status

`get(job_id, reconcile=true) -> JobSnapshot`

Required fields:

```json
{
  "ok": true,
  "job_id": "...",
  "parent_job_id": null,
  "root_job_id": "...",
  "attempt": 1,
  "kind": "test",
  "status": "running",
  "lifecycle": "running",
  "health": "quiet",
  "health_evidence": {
    "classified_at": "...",
    "supervisor_heartbeat_age_seconds": 2.1,
    "process_identity_valid": true,
    "last_output_age_seconds": 94.0,
    "last_activity_age_seconds": 8.0,
    "stall_threshold_seconds": 300,
    "reasons": ["process alive", "CPU time advanced"]
  },
  "target": {"kind": "remote", "remote": "scaleway-sandbox", "reachable": true},
  "workspace": {
    "label": "node-unit",
    "mode": "persistent",
    "lease": "exclusive"
  },
  "queue": {"reason": null, "position": null},
  "timing": {
    "accepted_at": "...",
    "started_at": "...",
    "finished_at": null,
    "elapsed_seconds": 103,
    "deadline_at": "...",
    "remaining_seconds": 1697
  },
  "process": {
    "supervisor_alive": true,
    "child_alive": true,
    "identity_valid": true
  },
  "output": {
    "completeness": "active",
    "stdout_bytes": 1234,
    "stderr_bytes": 0,
    "events": 18,
    "next_cursor": "opaque"
  },
  "metrics": {"available": true, "samples": 10, "last_at": "..."},
  "artifacts": {"available": 0, "rejected": 0},
  "cleanup": {"policy": "retain", "status": "not_requested"},
  "result": null
}
```

`status` remains the existing broad compatibility key. New callers use `lifecycle` and
`health`. Remote transport timeout yields `ok:false`, `health:"unreachable"`, and the
last known safe snapshot if available; it does not invent a terminal lifecycle.

## List

`list(query) -> JobPage`

Filters: project identity, local/remote target, remote name, workspace, lifecycle,
health, kind, parent, root, since/until, include terminal, limit. Pagination uses an
opaque cursor; default 50, maximum 200. Results are newest-first with stable ordering by
accepted timestamp and job ID.

## Read output

`read_output(job_id, query) -> OutputPage`

Query fields:

- `stream`: combined/stdout/stderr (default combined)
- one position: opaque `cursor`, byte `offset`, `tail_bytes`, `lines`, or `since`
- bounds: `max_bytes` (default/max 65536), `max_events` (default/max 500)
- `encoding`: utf8/base64
- `profile`: full/smart/errors/sampled/quiet/custom name
- `wait_seconds`: 0..20

Result:

```json
{
  "ok": true,
  "job_id": "...",
  "stream": "combined",
  "profile": "smart",
  "encoding": "utf8",
  "data": "...",
  "events": [],
  "bytes_read": 4096,
  "events_read": 22,
  "cursor": "opaque-next-cursor",
  "has_more": true,
  "bounded": true,
  "retained": {"first_sequence": 0, "next_sequence": 180},
  "output_completeness": "active",
  "job_terminal": false
}
```

The cursor is exclusive: using the returned cursor never repeats a prior event. A
cursor for another job/stream or an expired retained range fails explicitly.

## Metrics

`read_metrics(job_id, since?, until?, max_samples=500) -> MetricsPage`

Returns sampled values with an opaque cursor and capability list. Unsupported host
metrics are null. Health evidence identifies which samples contributed to classification.

## Cancellation

`cancel(job_id, mode="graceful", reason, wait_seconds=0) -> JobSnapshot`

- `mode=graceful` records intent and sends TERM only to a verified owned process group.
- `mode=force` requires explicit caller intent and sends KILL after identity verification.
- Parent cancellation propagates to non-terminal children and preserves child results.
- Terminal jobs reject cancellation as `already_terminal`.
- Identity mismatch yields `process_identity_mismatch`; no signal is sent.

## Retry

`retry(job_id, request_id?, workspace_policy="reuse") -> JobAccepted`

Creates a new job/attempt linked through `retry_of_job_id`. It revalidates target,
source, deadline, workspace, and capability. It never mutates the prior terminal job.
Every newly accepted job stores a bounded canonical submission snapshot containing policy
and reference data only: artifact declarations, compatibility differences, dependency and
failure policy, cleanup policy, environment key names, source/workspace/deadline/output
settings, and parent context. Secret environment values and arbitrary/unbounded JSON are
excluded. Argv retains valid tabs/newlines and rejects only empty/NUL-bearing arguments
plus total snapshot bounds. Exact scoped request replay is resolved by stored digest before
new snapshot validation, preserving pre-migration replay compatibility. Retry uses this
snapshot; rows accepted before the additive migration use only safe fields available in
legacy columns/tables. A standalone retry keeps
`parent_job_id:null`; a CI child retry keeps its actual parent. Aggregate-parent retry
fails with stable `aggregate_retry_unsupported` until scoped complete-graph retry exists.
Retries linked to a terminal parent appear under additive `retry_attempts`; frozen original
`children` membership and terminal aggregate result do not change.

## Artifacts

- `list_artifacts(job_id) -> ArtifactPage`
- `get_artifact(job_id, artifact_id, offset=0, max_bytes=1MiB, encoding=base64) -> ArtifactChunk`

Artifact retrieval is bounded: offset must be a non-negative non-boolean integer and page
size must be 1..1 MiB. Literal directory declarations are retained as byte-deterministic
bounded tar archives; collection rejects any symlink, device, socket, FIFO, path escape,
entry-count overflow, byte-limit overflow, or file growth/shrink/change during collection.
Both file and directory collection account verified live bytes. CLI may provide an
explicit local destination and streams every bounded chunk to a same-directory temporary
file, validates declared total size and SHA-256, then atomically publishes the file. A
failed transfer leaves no partial destination. MCP returns one bounded chunk plus
size/hash/next-offset metadata per call. Paths are never accepted as retrieval identities
after collection—only artifact IDs.

## Maintenance

- `reconcile(job_id|all)` verifies process/boot identity and terminal mirrors.
- `collect_retention(now, dry_run=true)` plans or applies expiry while protecting active
  jobs and retained workspaces.
- `cleanup(job_id, artifacts?, logs?, workspace?)` applies only explicit scoped policy.

Maintenance returns structured planned/applied/skipped/failed counts and never treats a
partially failed cleanup as success. Cleanup keeps bounded index rows for audit but marks
removed output/metrics unavailable and retained artifact rows `expired` with a stable
cleanup reason. Metrics cleanup removes the authoritative `metrics.jsonl`; service reads
fail `metrics_unavailable` after cleanup rather than returning an empty successful page.

Aggregate-parent status preserves existing `aggregate`, original `children`, and raw
`result_json` keys and adds normalized `result` plus separate `retry_attempts`. On terminal
transition, `result_json` is capped at 256 KiB and persists aggregate counts/conclusion plus
bounded child references with output completeness, artifact/difference counts, and cleanup
policy/state. `child_outcomes_truncated` signals omitted references. Full current artifact,
difference, output, and cleanup detail remains inspectable through `children`; terminal rows
and aggregate membership remain immutable.

