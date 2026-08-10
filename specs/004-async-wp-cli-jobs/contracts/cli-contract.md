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

- `./sb wp --async <args>` → prints the `job_id`.
- `./sb job <id> [--follow] [--kill]` → show status + tail; `--follow` streams; `--kill` cancels.
- `./sb jobs [--prune]` → list active/recent jobs; both normal listing and
  `--prune` sweep only old *terminal* artifact groups, never individual files
  from a running job.

All take the standard instance resolution (cwd project → registry; `--instance`/`$SANDBOX_INSTANCE`); MCP tools require the mandatory `project_dir` and `ensure_instance` first. New MCP tools ⇒ Claude Code restart (gotcha #4).

## Launch wrappers (sh)

The Herd wrapper is launched with Python's portable `start_new_session=True`, so
its `$$` is the process-group leader without depending on an external `setsid`
binary. Cancel sends `SIGTERM` to that **group** so the child `wp`/`php` can't be
orphaned. `.sb-jobs/` is the same directory on host
and container via the bind-mount (gotcha #3 — same absolute path inside the
container), so the host reader and the wrapper resolve identical files (F7).

- **Docker**: `compose run -d --name <job-name> --entrypoint sh wpcli -c 'echo $$ > …pid; wp <args> > …log 2>&1; echo $? > …status'`. The container itself is the cancellation boundary.
- **Herd**: Python spawns `sh -c '<same wrapper>'` from `<wp_root>` with
  `start_new_session=True` and records the returned wrapper PID immediately.
- Args shell-quoted per token (`shlex.quote`).
