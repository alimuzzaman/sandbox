# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/wp.py`)

### `wp_cli(command, timeout=60, *, project_dir)`
- Unchanged synchronous WP-CLI call.

### `wp_cli_async(command, *, project_dir)`
- Launches the same authorised WP-CLI command detached and returns
  `{ ok, job_id, status:"running" }`; it never waits for the WP command's
  completion. The CLI equivalent is `./sb wp --async <args>`.
- No PID is returned to callers. The per-job `.pid` artifact is an internal
  cancellation handle. Herd records the wrapper group. Docker records
  `launch:<supervisor-pid>` before return, then atomically changes it to
  `container` after named-container acceptance. Both states support immediate
  poll/cancel without minting another job ID.

### `wp_cli_job(job_id, offset=0, limit=1048576, *, project_dir)`
- Validates `job_id`; returns `{ ok, job_id, status, exit_code?, stdout, bytes_read, truncated }`.
- `status ∈ {running, completed, not_found}`; `limit=-1` ⇒ whole log.

### `wp_cli_job_kill(job_id, *, project_dir)`
- Herd sends `SIGTERM` to the wrapper process **group**. Docker force-removes
  the detached job container (and therefore its children). During Docker launch,
  it also stops the identity-checked supervisor. A `143` status is written only
  after the applicable owner and exact named container are both observed absent.
- A cancelled job reports `status:"completed"` with `exit_code:143` (the `.status` file is present = done); "cancelled" is a human-facing interpretation, not a distinct query status (analysis F2).
- Killing a finished/unknown job → `{ ok, status }` no-op (no error).
- Polling reconciles only a definitely dead, published execution boundary. A
  timeout, OS error, malformed observation, or container absence before the
  `launch`→`container` transition is unknown and remains non-terminal.

## CLI (`sandbox/commands/`)

- `./sb wp --timeout SECONDS -- <args>` → runs synchronously with a 60-second
  default and an integer bound from 1 through 3600. `--timeout` and `--async`
  are mutually exclusive and rejected by argparse before instance resolution
  or runtime work.
- `./sb wp --async <args>` → prints the `job_id`; use this detached path for
  work that should outlive the synchronous wait bound.
- `./sb job <id> [--follow] [--kill]` → show status + tail; `--follow` streams; `--kill` cancels.
- `./sb jobs [--prune]` → list active/recent jobs; both normal listing and
  `--prune` sweep only old *terminal* artifact groups, never individual files
  from a running job.

The synchronous CLI passes the selected timeout through WP-CLI and the
managed execution gate. A `subprocess.TimeoutExpired` preserves any partial
stdout/stderr on their matching streams, exits 124, and reports exactly:
`error: wp command timed out after 60 seconds; completion is unknown—inspect
state before retrying, or use --async for long work` (with the requested
integer substituted). This is distinct from a child that completes with exit
124. The Compose client wait is not a guarantee that the container process has
terminated; Sandbox does not retry automatically after a timeout. Synchronous
WP output remains raw and has no JSON wrapper.

All take the standard instance resolution (cwd project → registry; `--instance`/`$SANDBOX_INSTANCE`); MCP tools require the mandatory `project_dir` and `ensure_instance` first. New MCP tools ⇒ Claude Code restart (gotcha #4).

## Launch wrappers (sh)

The Herd wrapper is launched with Python's portable `start_new_session=True`, so
its `$$` is the process-group leader without depending on an external `setsid`
binary. Cancel sends `SIGTERM` to that **group** so the child `wp`/`php` can't be
orphaned. `.sb-jobs/` is the same directory on host
and container via the bind-mount (gotcha #3 — same absolute path inside the
container), so the host reader and the wrapper resolve identical files (F7).

- **Docker**: an isolated host supervisor is the acceptance/cancellation
  boundary while it runs `compose run -d --name <job-name> --entrypoint sh
  wpcli -c 'wp <args> > …log 2>&1; echo $? > …status'`. It traps cancellation,
  cleans the exact named container, verifies exact absence, and only then records
  cancellation or launch failure. Cleanup uncertainty remains observable and
  retryable. After successful creation, the named container becomes the
  cancellation boundary.
- **Herd**: Python spawns `sh -c '<same wrapper>'` from `<wp_root>` with
  `start_new_session=True` and records the returned wrapper PID immediately.
- Args shell-quoted per token (`shlex.quote`).
