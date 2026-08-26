# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/wp.py`)

### `wp_cli(command, timeout=60, *, project_dir)`
- Unchanged synchronous WP-CLI call.

### `wp_cli_async(command, *, project_dir)`
- Launches the same authorised WP-CLI command detached and returns
  `{ ok, job_id, status:"running" }`; it never waits for the WP command's
  completion. The CLI equivalent is `./sb wp --async <args>`.
- No PID is returned to callers. The per-job `.pid` artifact is an internal
  cancellation handle; on Herd it is written immediately from the spawned
  wrapper PID to avoid an immediate-poll/cancel race.

### `wp_cli_job(job_id, offset=0, limit=1048576, *, project_dir)`
- Validates `job_id`; returns `{ ok, job_id, status, exit_code?, stdout, bytes_read, truncated }`.
- New jobs may also expose value-free acceptance metadata (`launcher` and
  measured `acceptance_ms`); command argv and output remain separate artifacts.
- `status ∈ {running, completed, not_found}`; `limit=-1` ⇒ whole log.

### `wp_cli_job_kill(job_id, *, project_dir)`
- Herd sends `SIGTERM` to the wrapper process **group**. Docker force-removes
  the detached job container (and therefore its children). A `143` status is
  written only after the process group/container is verified gone.
- A cancelled job reports `status:"completed"` with `exit_code:143` (the `.status` file is present = done); "cancelled" is a human-facing interpretation, not a distinct query status (analysis F2).
- Killing a finished/unknown job → `{ ok, status }` no-op (no error).
- Polling reconciles a known job whose process/container died before writing
  `.status` into a durable non-zero completion rather than reporting it as
  running forever.

## CLI (`sandbox/commands/`)

- `./sb wp --timeout SECONDS -- <args>` → runs synchronously with a 60-second
  default and an integer bound from 1 through 3600. `--timeout` and `--async`
  are mutually exclusive and rejected by argparse before instance resolution
  or runtime work.
- `./sb wp --async <args>` → prints the `job_id`; use this detached path for
  work that should outlive the synchronous wait bound.
- Async Docker probe/launch is bounded to 15 seconds. A timeout is reported as
  acceptance unknown; inspect `./sb jobs` before retrying because a detached
  launch may have crossed the transport boundary.
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

- **Docker**: when the running web service has the shipped WP-CLI binary,
  `compose exec -d -u www-data -T wp sh -c 'echo $$ > …pid; wp <args> > …log 2>&1; echo $? > …status'`
  reuses that container for fast acceptance. Older/stopped/LiteSpeed instances
  fall back to `compose run -d --name <job-name> --entrypoint sh wpcli -c …`;
  the fallback container is the cancellation boundary. Both paths use the
  same bind-mounted `.sb-jobs` artifacts and shell-quoted argv.
- **Herd**: Python spawns `sh -c '<same wrapper>'` from `<wp_root>` with
  `start_new_session=True` and records the returned wrapper PID immediately.
- Args shell-quoted per token (`shlex.quote`).
