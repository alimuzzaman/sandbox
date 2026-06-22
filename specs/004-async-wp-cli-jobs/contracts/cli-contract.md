# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/wp.py`)

### `wp_cli(command, timeout=60, background=false, *, project_dir)`
- `background=false` (default): unchanged synchronous behavior.
- `background=true`: launch detached, return `{ ok, job_id, status:"running" }` in <~2s; never blocks beyond spawn.
- **Note (analysis F1)**: the param is `background`, **not** `async` — `async` is a Python reserved keyword. The CLI flag `--async` is accepted but maps to `dest="background"` (so `args.background`). No `pid` is returned at start (analysis F5): detached `compose exec -d` doesn't surface the inner PID; the cancel handle is `job_<id>.pid` read at kill time.

### `wp_cli_job(job_id, offset=0, limit=1048576, *, project_dir)`
- Validates `job_id`; returns `{ ok, job_id, status, exit_code?, stdout, bytes_read, truncated }`.
- `status ∈ {running, completed, not_found}`; `limit=-1` ⇒ whole log.

### `wp_cli_job_kill(job_id, *, project_dir)`
- `kill -TERM -$(cat job_<id>.pid)` — SIGTERM the wrapper's process **group** (container via `compose exec`; herd directly); write `143` to `.status`.
- A cancelled job reports `status:"completed"` with `exit_code:143` (the `.status` file is present = done); "cancelled" is a human-facing interpretation, not a distinct query status (analysis F2).
- Killing a finished/unknown job → `{ ok, status }` no-op (no error).

## CLI (`sandbox/commands/`)

- `./sb wp --async <args>` → prints the `job_id`.
- `./sb job <id> [--follow] [--kill]` → show status + tail; `--follow` streams; `--kill` cancels.
- `./sb jobs [--prune]` → list active/recent jobs; `--prune` removes old artifacts.

All take the standard instance resolution (cwd project → registry; `--instance`/`$SANDBOX_INSTANCE`); MCP tools require the mandatory `project_dir` and `ensure_instance` first. New MCP tools ⇒ Claude Code restart (gotcha #4).

## Launch wrappers (sh)

The wrapper is launched with **`setsid`** so its `$$` is the process-group leader;
cancel sends `SIGTERM` to the **group** (`kill -TERM -$(cat …pid)`) so the child
`wp`/`php` can't be orphaned (analysis F6). `.sb-jobs/` is the same directory on host
and container via the bind-mount (gotcha #3 — same absolute path inside the
container), so the host reader and the wrapper resolve identical files (F7).

- **Docker**: `compose exec -d -w <ABSPATH> wpcli setsid sh -c 'echo $$ > .sb-jobs/job_<id>.pid; wp <args> > .sb-jobs/job_<id>.log 2>&1; echo $? > .sb-jobs/job_<id>.status'`
- **Herd**: `cd <wp_root> && setsid sh -c '<same wrapper>' >/dev/null 2>&1 &`
- Args shell-quoted per token (`shlex.quote`).
