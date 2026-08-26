---
description: "Task list for Async / Background WP-CLI Jobs"
---

# Tasks: Async / Background WP-CLI Jobs

**Input**: Design documents from `specs/004-async-wp-cli-jobs/`

**Status**: Implemented with partial Docker live verification. The current
Nginx shared and `wp db` fallback paths have disposable `<2s` evidence; LiteSpeed,
older/stopped-service, and cold-daemon parity remain open under T021. The
implementation landed in `sandbox/commands/jobs.py` (core + CLI) and
`mcp/wp-server/tools/wp.py` (MCP tools), not all in `tools/wp.py` as the original
plan guessed.

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
- [x] T020 Docs-with-code: add `wp_cli_async`/`wp_cli_job`/`wp_cli_job_kill` +
  `./sb wp --async`/`job`/`jobs` to the CLAUDE.md MCP table + config reference.
  **DONE:** CLAUDE.md documents the MCP tools and their CLI equivalents (spec
  004).

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
  candidate job ID for inspection. Disposable Docker evidence was collected on
  2026-08-26 in a temporary per-worktree `SANDBOX_HOME` (the shared registry
  and existing instances were not used): the first post-ensure Nginx launch
  returned in 1,270.243 ms client-side (330.449 ms in its receipt), three
  subsequent warm launches returned in 1,194.099–1,323.606 ms (292.788–370.227
  ms in their receipts), and two `wp db` compatibility launches returned in
  1,918.533–1,974.677 ms (1,009.751–1,051.358 ms in their receipts). All six
  jobs produced a non-empty 16-hex ID and completed with exit 0. Polling
  retained output and exit status; a long job was cancelled and completed with
  exit 143 without its post-cancel marker. These measurements prove the current
  Nginx paths in this disposable matrix, not all server tiers or a cold Docker
  daemon.

  Bounded implementation tasks:

  - [x] T021a Measure the current Docker start path with a monotonic client-side
    timer and retain the job acceptance receipt separately from command runtime.
    The first post-ensure launch is recorded as the cold launch sample and
    three later launches are recorded as warm-instance samples above; no single
    sample is used as the gate.
  - [x] T021b Compare the warm running-web-service `compose exec -d` launcher
    with the lightweight detached `compose run -d` compatibility launcher.
    Both paths preserve the job directory, PID/status files, shell quoting,
    cancellation boundary, and project mount in the focused fixture suite; the
    disposable matrix exercised both launchers.
  - [x] T021c Define the acceptance envelope: non-empty job ID, durable receipt
    before acknowledgement, bounded start latency, and a truthful
    `acceptance_unknown` result on disconnect or malformed output.
  - [x] T021d Add focused unit/fixture tests for cold and warm paths, duplicate
    request IDs, cancellation, retained output, and cleanup. The CLI and MCP
    async WP surfaces now accept a stable request ID. Same-instance/argv
    replays return the reserved job ID without a second launch; a different
    argv fails closed; an acceptance/transport exception reserves an
    `unknown` inspection handle. Focused fixtures cover both launcher paths,
    cancellation, retained output, request-record redaction, and cleanup.
    Keep the current `compose run -d` implementation as a compatibility
    fallback until parity is proven for every server tier.
  - [x] T021e Run the disposable Docker acceptance matrix used by T005, T007,
    T010, and T013, then record measured `<2s` evidence and the residual
    blocker above. Nginx shared and `wp db` fallback paths passed; LiteSpeed,
    older-image/stopped-service, and cold-daemon evidence remain open.
    Duplicate-request behavior is fixture verified, not live-tier parity
    evidence. A follow-up disposable LiteSpeed attempt on 2026-08-26
    reached container creation but failed Sandbox's bounded 30-second
    document-root bootstrap check, so it produced no async-launch sample. This
    is a readiness blocker, not evidence that the `compose run -d` fallback
    meets the acceptance target. No remote or production mutation is part of
    T021.
