# Contract: MCP Tools

## Composition

Add an explicit runtime-neutral `jobs` tool group in
`mcp/wp-server/tools/manifest.py`. Its dependency bundle contains only application job,
target, workspace, and transport services. Tools do not import `sandbox_core.py`, read
registry/state JSON, open job SQLite/files, or build raw SSH commands.

Remote live operations should use the co-located remote MCP server when available.
Local MCP may use the bounded remote transport as a compatibility path; it still calls
the remote host's job service rather than attaching process pipes.

## Shared target input

Remote-aware start/workspace tools accept:

```json
{
  "project_dir": "/absolute/project",
  "target": "configured|local|remote",
  "remote": "scaleway-sandbox",
  "workspace": "node-unit",
  "timeout_seconds": 1800,
  "execution_profile": "unit",
  "output_profile": "smart",
  "request_id": "caller-stable-id"
}
```

`target=remote` requires `remote`. `target=configured` applies project defaults. An
explicit timeout and execution profile may both be supplied only when the timeout is
the effective override and the profile supplies other policy. Every result identifies
resolution sources and emits `deadline_reminder` when fallback supplied the deadline.

## Job tools

### `job_start`

Inputs: shared target input, `kind`, explicit `argv` or declared `plan`, optional
workspace-relative cwd, parallel-safe flag, cleanup policy, artifact paths, detach/wait
policy. Returns the `JobAccepted` shape. Optional bounded wait does not change the
durable job contract.

### `job_status`

Inputs: `project_dir`, `job_id`, optional remote. Returns `JobSnapshot` with lifecycle,
health/evidence, timing, liveness, queue, output, metrics, artifacts, and cleanup.

### `job_list`

Inputs: project, optional remote/workspace/lifecycle/kind filters, cursor, limit.
Returns bounded page and opaque next cursor.

### `job_output`

Inputs: project, job, remote, stream, cursor/offset/tail/lines/since, profile,
max-bytes/events, encoding, wait seconds. Returns `OutputPage`. Default max is 64 KiB;
MCP hard maximum is 256 KiB per call.

### `job_follow`

This is a bounded convenience call, not an endless stream. Inputs include cursor,
profile, max updates, max duration (<=20 seconds per server request), and optional MCP
progress. It returns emitted summaries/events plus next cursor.

### `job_metrics`

Returns a bounded metrics page and health evidence.

### `job_artifacts` / `job_artifact_get`

Lists metadata or returns exactly one bounded base64 chunk with declared size, SHA-256,
next offset, and `has_more`. Invalid negative/boolean offsets or page sizes outside
1..1 MiB return `invalid_artifact_query` before any transport read. Artifact downloads larger than the MCP limit require repeated
offset calls or CLI retrieval; MCP never loops through an unbounded artifact in one tool
response.

### `job_cancel`, `job_retry`, `job_cleanup`

Explicit mutation tools. Force cancellation and workspace cleanup require explicit
boolean confirmation/reason fields. `job_retry` rejects aggregate parents with
`aggregate_retry_unsupported`; retrying a terminal child retains its parent but appears in
parent `retry_attempts`, not frozen original `children` membership.

### `workspace_create/list/status/reset/destroy`

Remote-aware lifecycle controls with busy-lease protection. Reset/destroy require
confirmation. Listing separates local and remote namespaces.

## Existing tool extensions

### `run_tests`

Add optional target/workspace/deadline/output/detach inputs. Preserve existing keys:

```json
{
  "ok": true,
  "passed": true,
  "summary": "...",
  "job_id": "...",
  "status": "succeeded",
  "target": {...},
  "workspace": "...",
  "deadline": {...},
  "output": {...}
}
```

When detached, `passed` is null and `summary` states that the job was accepted; callers
use `job_status`. When waited, existing success/failure semantics remain.

### `instance_exec`

Add the same optional target/workspace/deadline/output inputs and return job identity
alongside existing runtime result keys. Explicit argv remains required.

### `ci_run`

The adapter accepts both preserved local reports containing `cells` and durable remote
reports containing `parent_job_id` plus `children`, including detached remote acceptance.
Callers inspect remote parent/child results through ordinary job tools.

### Existing async tools

`wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill`, E2E async, CI async, and generic
async-job tools retain their names and fields. During migration they map legacy IDs and
offset reads onto job service responses. New 32-hex IDs are accepted; legacy 16-hex IDs
remain readable.

## Progress behavior

- Progress notifications are opt-in and require a client-provided progress token.
- Progress is monotonic for the active MCP request. It represents bounded observation
  progress, not the child process's universal percentage unless a test explicitly
  declares a total.
- Default emission interval is at least 2 seconds and at most one notification per 50
  retained events; stricter server limits may apply.
- Messages use the selected output presentation profile and a bounded character budget.
- Notifications stop when the MCP request returns. The remote job may continue.
- Clients must use durable status/output calls after disconnect and must not infer
  completion from missing notifications.

## Stable errors

Errors preserve `ok:false`, `code`, `message`, target/workspace/job identity where safe,
and actionable suggestion. Secret values, SSH connection strings, raw environment, and
unsafe artifact paths are redacted.

## Workspace metadata/index controls (convergence)

Workspace tools use typed service inputs:

```json
{
  "remote": "scaleway-sandbox",
  "project_identity": "project-id",
  "workspace_id": "opaque-id",
  "plan_id": "opaque-plan-id",
  "confirm": false
}
```

`workspace_list`, `workspace_status`, and `workspace_migrate` MUST work without
`project_dir`; reset/destroy require an opaque workspace ID, explicit confirmation, and
the busy-lock controller. Migration plans expose inventory digest, index generation,
expiry, and adopted/unresolved/conflict/invalid decisions. Apply refuses drift with
`workspace_migration_plan_stale`/`workspace_ownership_drift` and reports
`workspace_index_incomplete` rather than an empty success. `workspace_list` accepts
`measure_sizes` and always returns `ok: true` with an `index` block
(`complete`, `code`, `generation`, `counts`) and an `on_disk` block, so a degraded index
is reported rather than hiding on-disk deployment storage; mutating workspace tools keep
refusing degraded records. MCP adapters consume the
workspace service and typed resource projection; they never open the SQLite index or
legacy workspace JSON. No metadata migration call releases networks or performs cleanup.
