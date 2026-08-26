# Data Model: Async / Background WP-CLI Jobs

No database. A Job is three public files plus internal launcher/acceptance
metadata under `runtime/wp-<instance>/.sb-jobs/`.

## Job

| Field | Source | Description |
|-------|--------|-------------|
| job_id | minted at start | 16 hex chars; validated `^[a-f0-9]{16}$` before any path use |
| args | caller | the `wp` args (shell-quoted per token at launch) |
| instance | registry | the resolved target instance |

## Artifacts + state machine

| File | Meaning |
|------|---------|
| `job_<id>.log` | combined stdout+stderr, appended as it runs |
| `job_<id>.pid` | wrapper PID, written (`echo $$`) before exec — the cancel handle |
| `job_<id>.status` | absent ⇒ running; present ⇒ done, contents = exit code (`143` if killed) |
| `job_<id>.launcher` | internal Docker boundary marker: `web-exec` for the shared web container, `run` for the compatibility job container |
| `job_<id>.receipt` | private, value-free acceptance record: launcher kind, monotonic `acceptance_ms`, and wall-clock `accepted_at` |

Query states (what `wp_cli_job` returns): **running** (`.log`/`.pid` present, no
`.status`), **completed** (`.status` present; exit code = its contents), **not_found**
(no files / pruned). A **cancelled** job is just `completed` with `exit_code:143` —
not a distinct query status (analysis F2); "cancelled" is the human interpretation.

Transitions: start → running; process exit → completed; `kill` → targeted
wrapper signal for shared-container Docker, container removal for the fallback,
or SIGTERM to the Herd process **group** → completed(`143`). Age-prune removes
terminal jobs' files, including the internal launcher marker and acceptance
receipt.

## Query result (`wp_cli_job`)

`{ ok, job_id, status: running|completed|not_found, exit_code?, stdout (slice), bytes_read, truncated }`
— `stdout` is the `[offset, offset+limit)` slice of `.log`. Default `limit` = 1 MiB;
`limit=-1` ⇒ whole file; `offset` ≥ file size ⇒ empty slice (`bytes_read:0`,
`truncated:false`), not an error (analysis F10).
