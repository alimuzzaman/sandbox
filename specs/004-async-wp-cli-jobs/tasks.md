---
description: "Task list for Async / Background WP-CLI Jobs"
---

# Tasks: Async / Background WP-CLI Jobs

**Input**: Design documents from `specs/004-async-wp-cli-jobs/`

**Status**: Implemented + live-verified on Docker (templately-rebuild2). Implementation
landed in `sandbox/commands/jobs.py` (core + CLI) and `mcp/wp-server/tools/wp.py` (MCP
tools), not all in `tools/wp.py` as the original plan guessed.

## Phase 1: Setup

- [x] T001 Job-dir helper + 16-hex `job_id` validator + minter (`sandbox/commands/jobs.py`).
- [x] T002 Job artifacts live under `runtime/wp-<instance>/.sb-jobs/` — already gitignored via `runtime/wp-*/`; no gitignore change needed.

## Phase 2: Foundational

- [x] T003 Log-slice reader (`offset`/`limit`, `truncated`; `limit=-1` ⇒ whole file) in `job_status`.
- [x] T004 Launch wrappers — **Docker**: `compose run -d --entrypoint sh wpcli` (the wpcli service is run-style, not `exec`-able; container kill reaps the whole process tree, so no `setsid` needed there); **herd**: `setsid` + backgrounded host `wp`. Each writes `$$`→`.pid`, runs `wp`, `$?`→`.status`; args `shlex`-quoted. `.sb-jobs/` is the bind-mounted WP root, same files host + container (gotcha #3).

## Phase 3: US1 — Long command doesn't block (P1)

- [x] T005 `./sb wp --async` flag (dest=`run_async`, since `async` is a reserved word) → `launch_job`, prints job_id. **LIVE-VERIFIED**: returns on container-create (~7s, fixed), not on command duration.
- [x] T006 `./sb wp --async <args>` prints job_id + poll/follow/kill hints.
- [x] T007 **LIVE-VERIFIED**: `sleep(8)` job → job_id returned; command continued detached.

## Phase 4: US2 — Incremental output polling (P2)

- [x] T008 `wp_cli_job(job_id, offset, limit, *, project_dir)` MCP tool (validates id; status + log slice).
- [x] T009 `./sb job <id> [--follow]` (status + tail; `--follow` streams).
- [x] T010 **LIVE-VERIFIED**: running→completed (exit 0, captured stdout `done-sleeping`); finished job re-queried still returns terminal status+exit_code; offset past EOF → empty slice (not error).

## Phase 5: US3 — Cancel a running job (P1)

- [x] T011 `wp_cli_job_kill(job_id, *, project_dir)` MCP tool — Docker `docker rm -f` (container+children, no orphan), herd `kill -TERM -PGID`; writes `143`; no-op on finished/unknown.
- [x] T012 `./sb job <id> --kill`.
- [x] T013 **LIVE-VERIFIED**: killed a `sleep(120)` job → container removed, status `completed exit 143`, no orphan; re-kill is a clean no-op.

## Phase 6: US4 — CLI parity (P2)

- [x] T014 `./sb jobs [--prune]` (glob + list with status; `--prune` removes old artifacts).
- [x] T015 Age-based auto-prune (24h) on `jobs` **and on instance up** (cmd_up hook).
- [x] T016 **LIVE-VERIFIED**: `./sb jobs` listed running + completed.

## Phase 7: US5 — Works on every driver (P1)

- [x] T017 Herd path implemented (`setsid` + pinned `php<MM>`/`wp` via `_herd_wp_cmd`, `.sb-jobs/` host path, `kill -TERM -PGID`).
- [~] T018 Herd live-verification DEFERRED — no herd instance running in this environment; Docker path fully verified. Re-verify when a herd instance exists.

## Phase 8: Polish

- [x] T019 Safety: `job_id` validated against `^[a-f0-9]{16}$` before any filesystem/container access; `--async` is only an execution mode for `wp` (no widened surface).
- [x] T020 Docs-with-code: add `wp_cli_async`/`wp_cli_job`/`wp_cli_job_kill` + `./sb wp --async`/`job`/`jobs` to the CLAUDE.md MCP table + config reference. (Pending a docs pass.)  **DONE: CLAUDE.md MCP table now documents `wp_cli_async`/`wp_cli_job`/`wp_cli_job_kill` + the `./sb wp --async`/`job`/`jobs` CLI equivalents (spec 004).**

## Notes

- MCP tools (`wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill`) become callable after a
  Claude Code restart (gotcha #4); the CLI path + the shared `job_status`/`kill_job`
  logic they call are live-verified.
- The async-start latency is the docker `compose run` container-create cost (~7s, fixed)
  — it does NOT scale with command duration, which is the property that matters.
