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

Lists metadata or returns a bounded base64 chunk. Artifact downloads larger than the
MCP limit require repeated offset calls or CLI retrieval.

### `job_cancel`, `job_retry`, `job_cleanup`

Explicit mutation tools. Force cancellation and workspace cleanup require explicit
boolean confirmation/reason fields.

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

