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
- [x] T004 Launch wrappers — **Docker**: reuse the running web service with `compose exec -d -u www-data -T wp sh -c …` when the shipped WP-CLI binary is present; retain `compose run -d --entrypoint sh wpcli` as the compatibility fallback (the fallback container is the cancellation boundary). **Herd**: Python `start_new_session=True` + host `wp`. Each writes `$$`→`.pid`, runs `wp`, `$?`→`.status`; args `shlex`-quoted. `.sb-jobs/` is the bind-mounted WP root, same files host + container (gotcha #3).

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
- [x] T018 Herd live verification completed 2026-08-14 against a disposable Herd
  1.29.0 / external-DBngin WordPress instance. Detached job `244c28884de16b0a`
  retained `phase-one` and `phase-two` and completed with exit 0. Job
  `80812f40d818f0e6` retained its pre-sleep output, was killed through the
  supported job surface, completed with exit 143 without the post-sleep marker,
  and a repeated kill reported `already finished` without error.

## Phase 8: Polish

- [x] T019 Safety: `job_id` validated against `^[a-f0-9]{16}$` before any filesystem/container access; `--async` is only an execution mode for `wp` (no widened surface).
- [x] T020 Docs-with-code: add `wp_cli_async`/`wp_cli_job`/`wp_cli_job_kill` + `./sb wp --async`/`job`/`jobs` to the CLAUDE.md MCP table + config reference. (Pending a docs pass.)  **DONE: CLAUDE.md MCP table now documents `wp_cli_async`/`wp_cli_job`/`wp_cli_job_kill` + the `./sb wp --async`/`job`/`jobs` CLI equivalents (spec 004).**

## Notes

- MCP tools (`wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill`) become callable after a
  Claude Code restart (gotcha #4); the CLI path + the shared `job_status`/`kill_job`
  logic they call are live-verified.
- The historical fallback async-start latency was the Docker `compose run`
  container-create cost (~7s, fixed). The built-in Docker path now uses
  `compose exec -d` against the already-running web service; fresh `<2s`
  timing evidence is still required before SC-001 is marked complete.

## Phase 9: Convergence

- [ ] T021 Reduce or otherwise redesign Docker async-job acceptance so it meets SC-001's under-2-second target; the recorded ~7-second `compose run -d` acceptance is a partial implementation of SC-001 (partial).

  Progress 2026-08-26: Apache/Nginx jobs now use the already-running web
  service via `compose exec -d` when the built-in WP-CLI binary is present.
  Shared-container polling/cancellation is covered by an internal launcher
  marker and wrapper TERM trap; `wp db …`, LiteSpeed, older images, and
  unavailable web services retain the `compose run -d` fallback. Acceptance
  now writes a private timing receipt (`acceptance_ms`) separate from command
  output, and the probe/launcher share a finite 15-second acceptance deadline;
  CLI timeout reports `acceptance_unknown` guidance. The MCP wrapper now adds
  a 30-second outer bound and returns a bounded `acceptance_unknown` envelope
  for timeout, malformed, and non-zero launcher results, retaining only a
  candidate job ID for inspection. This is a source/test improvement only; no
  live timing or all-tier parity claim is recorded yet.

  Bounded implementation tasks:

  - [ ] T021a Measure the current Docker start path with a monotonic client-side
    timer and retain the job acceptance receipt separately from command runtime.
    Record cold-start and warm-instance samples; do not claim the target from a
    single run.
  - [ ] T021b Compare a warm worker/session launcher with a lightweight detached
    launcher. Both must preserve the existing job directory, PID/status files,
    argv quoting, cancellation behavior, and project mount boundary.
  - [ ] T021c Define the acceptance envelope: non-empty job ID, durable ledger
    row before acknowledgement, bounded start latency, and a truthful
    `acceptance_unknown` result on disconnect or malformed output.
  - [ ] T021d Add focused unit/fixture tests for cold and warm paths, duplicate
    request IDs, cancellation, retained output, and cleanup. Keep the current
    `compose run -d` implementation as a compatibility fallback until parity
    is proven for every server tier.
  - [ ] T021e Run the same disposable Docker acceptance matrix used by T005,
    T007, T010, and T013, then record measured `<2s` evidence or a precise
    residual blocker. No remote or production mutation is part of T021.
