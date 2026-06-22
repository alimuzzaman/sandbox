# Feature Specification: Async / Background WP-CLI Jobs

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: Novamira parity #2 — "Async/background WP-CLI jobs (`job_id` + offset/limit
log polling). Our `wp_cli` is sync-only — long migrations/imports block."

## Summary

Today both `./sb wp …` and the `wp_cli` MCP tool run synchronously: the caller
blocks until the command exits, and the MCP tool hard-caps at `timeout=60`s
(`wp_cli` in [tools/wp.py](../../mcp/wp-server/tools/wp.py)). Long operations —
`wp media regenerate`, `wp search-replace`, large `wp import`, bulk
`wp post generate`, plugin/DB migrations — either time out or wedge the agent.

Add fire-and-forget background jobs: a command starts, returns a `job_id`
immediately, runs detached, and the agent polls for incremental output + exit
status. This mirrors Novamira's `run-wp-cli (async)` + `get-wp-cli-job` pattern
([includes/abilities/run-wp-cli.php:872](file:///tmp/novamira-review/includes/abilities/run-wp-cli.php#L872))
but implemented on the Sandbox side (Docker `exec -d` / Herd host process)
rather than inside WP.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Long command doesn't block the agent (Priority: P1)

An agent runs `wp media regenerate --yes` (minutes) without blocking the MCP
call or hitting the 60s timeout.

**Acceptance**:
1. **Given** a running instance, **When** the agent calls `wp_cli(command="media
   regenerate --yes", async=true)`, **Then** it returns `{ok, job_id, pid}` in
   under ~1s and the command keeps running after the call returns.
2. **When** the agent calls `wp_cli_job(job_id)` repeatedly, **Then** it returns
   `status: "running"` with the output captured so far, then `status:
   "completed"` with the final `exit_code` once finished.
3. **Given** a finished job, **When** polled again, **Then** the status/exit_code
   persist (re-readable) until the job is reaped.

### User Story 2 — Incremental log streaming (Priority: P2)

The agent fetches only new output each poll via a byte offset, so a multi-MB log
isn't re-sent every call.

**Acceptance**:
1. `wp_cli_job(job_id, offset=N, limit=M)` returns `{stdout, bytes_read,
   truncated}` for the slice `[N, N+M)`; the agent advances `offset` by
   `bytes_read`. (Matches Novamira's `novamira_read_log_slice`.)

### User Story 3 — CLI parity (Priority: P2)

A developer runs `./sb wp --async media regenerate --yes` → prints a `job_id`;
`./sb job <id>` tails it; `./sb jobs` lists active/recent jobs.

### User Story 4 — Works on every server driver (Priority: P1)

Async jobs work on apache / nginx / litespeed (Docker) **and** herd (host).

## Requirements

- **FR-1** `wp_cli` MCP tool gains `async: bool = False`. When true: returns
  `{ok, job_id, pid, status:"running"}`; never blocks beyond process spawn.
- **FR-2** New MCP tool `wp_cli_job(job_id, offset=0, limit=1048576, *,
  project_dir)` → `{ok, job_id, status, exit_code?, stdout, bytes_read,
  truncated}`. `status ∈ {running, completed, not_found}`.
- **FR-3** `job_id` is a 16-hex token (`bin2hex(random_bytes(8))` equivalent;
  validated against `^[a-f0-9]{16}$` before any path use — Novamira does this to
  prevent traversal).
- **FR-4** CLI: `./sb wp --async <args>`, `./sb job <id> [--follow]`, `./sb jobs`.
- **FR-5** Job artifacts (log + status) live in a host-visible, instance-scoped
  dir so both the container/host process and the reader can reach them:
  `runtime/wp-<instance>/.sb-jobs/job_<id>.{log,status}` (already bind-mounted).
- **FR-6** A completed job writes its exit code to `job_<id>.status`; absence of
  that file ⇒ still running. (Lock-free, file-based — same as Novamira.)
- **FR-7** Reaping: `./sb jobs --prune` and an age-based auto-prune (default 24h)
  remove old `.log`/`.status`. Document that logs are gitignored runtime state.
- **FR-8** Safety: async is **only** an execution mode for `wp` — it does not
  widen what commands are allowed. Same instance resolution + `project_dir`
  handshake as `wp_cli`.

## Design

### Launch — Docker drivers

`docker compose exec` returns when the inner process exits, so use **detached**
exec and redirect into the bind-mounted job dir:

```
docker compose -p <proj> exec -d -w <ABSPATH> wpcli \
  sh -c 'wp <args...> > /var/www/html/.sb-jobs/job_<id>.log 2>&1; \
         echo $? > /var/www/html/.sb-jobs/job_<id>.status'
```

`exec -d` (detached) backgrounds inside the container and returns immediately.
PID capture is best-effort (detached exec doesn't surface the inner PID
cleanly); the `.status` file is the source of truth for completion, not the PID.
Args are shell-quoted host-side (`shlex.quote` per token), mirroring Novamira's
`escapeshellarg` of each arg.

### Launch — Herd (host)

No container: spawn on the host with `nohup … &`, exactly Novamira's
`novamira_run_wp_cli_async` shape, using the instance's pinned `php<MM>` + `wp`
shims (`runtime/herd-shims/<instance>/`, see CLAUDE.md gotcha #14):

```
cd <wp_root> && nohup sh -c 'wp <args> > .sb-jobs/job_<id>.log 2>&1; \
  echo $? > .sb-jobs/job_<id>.status' >/dev/null 2>&1 & echo $!
```

### Status read

`wp_cli_job` reads `.sb-jobs/job_<id>.{log,status}` directly from the host path
(both Docker and Herd put them under `runtime/wp-<instance>/.sb-jobs/`). Slice
logic = Novamira's `novamira_read_log_slice` (fseek to `offset`, read `limit`,
`truncated = offset+bytes_read < filesize`). `limit=-1` ⇒ whole file.

### Data model

| File | Meaning |
|------|---------|
| `.sb-jobs/job_<id>.log` | combined stdout+stderr, append-as-it-runs |
| `.sb-jobs/job_<id>.status` | exists ⇒ done; contents = integer exit code |

No DB, no registry entry — file presence is the state machine (running →
log-only; completed → log + status). `./sb jobs` globs the dir.

## Integration points

- **MCP**: edit `wp_cli` and add `wp_cli_job` in
  [mcp/wp-server/tools/wp.py](../../mcp/wp-server/tools/wp.py) (reuse
  `_project_instance`, `_compose`, `_is_herd`, `_wp_root`, `_host_run`,
  `_herd_host_env`). New tool requires a Claude Code restart (CLAUDE.md gotcha #4).
- **CLI**: extend the `wp` command module and add a `jobs` command module under
  [sandbox/commands/](../../sandbox/commands/), self-registering via
  `sandbox/registry.py`.
- **Docs**: update the MCP-surface table in `CLAUDE.md` (add `wp_cli_job`), the
  MCP server `instructions`, and `docs/sandbox-config-reference.md`.

## Out of scope (v1)

- Cancelling a running job (would need PID tracking across detached exec). Add
  `./sb job <id> --kill` later if needed.
- Concurrency limits / a job queue. Jobs run immediately; the OS schedules them.
- Streaming push (SSE) — polling only.

## Tasks

1. `wp_cli_job` slice reader + job-dir helper (host-path resolver for both drivers).
2. `wp_cli(async=…)` launch: Docker `exec -d`, Herd `nohup`.
3. `./sb wp --async`, `./sb job`, `./sb jobs [--prune]` CLI.
4. Auto-prune (24h) on `jobs` / on instance up.
5. Live verification: start `wp media regenerate` async on a Docker instance and
   a Herd instance, poll to completion, assert exit_code 0 and non-empty log.
6. Docs: CLAUDE.md MCP table + instructions + config reference.
