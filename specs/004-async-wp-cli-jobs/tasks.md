---
description: "Task list for Async / Background WP-CLI Jobs"
---

# Tasks: Async / Background WP-CLI Jobs

**Input**: Design documents from `specs/004-async-wp-cli-jobs/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task.

## Path Conventions

Host-side only: `mcp/wp-server/tools/wp.py` + `sandbox/commands/` + per-instance
`runtime/wp-<instance>/.sb-jobs/`.

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Add a job-dir helper + `job_<id>` validator (`^[a-f0-9]{16}$`) + a 16-hex id minter in `mcp/wp-server/tools/wp.py` (host-path resolver for both Docker and herd → `runtime/wp-<instance>/.sb-jobs/`).
- [ ] T002 Add `.sb-jobs/` to gitignore (runtime state).

## Phase 2: Foundational (blocking prerequisites)

- [ ] T003 Implement the log-slice reader (fseek `offset`, read `limit`, compute `truncated`; `limit=-1` ⇒ whole file) in `mcp/wp-server/tools/wp.py`.
- [ ] T004 Implement the launch wrappers under **`setsid`** (so `$$` is the process-group leader): Docker `compose exec -d` and herd backgrounded, each writing `echo $$ > job_<id>.pid` before `wp …`, then exit code to `job_<id>.status`; args shell-quoted per token. Confirm `.sb-jobs/` resolves to the same files host-side and in-container via the bind-mount (gotcha #3).

## Phase 3: User Story 1 — Long command doesn't block (P1)

**Goal**: async start returns a job_id immediately; command keeps running.
**Independent test**: start a long command async, get a job_id in <~2s.

- [ ] T005 [US1] Add `async: bool=False` to `wp_cli` in `mcp/wp-server/tools/wp.py`; when true, launch via T004 and return `{ok, job_id, pid?, status:"running"}`.
- [ ] T006 [US1] Add `./sb wp --async <args>` to the `wp` command (prints job_id).
- [ ] T007 [US1] Live verification (quickstart §1): async start returns a 16-hex id in <~2s; command continues.

## Phase 4: User Story 2 — Incremental output polling (P2)

**Goal**: poll status + only-new output by offset.
**Independent test**: poll with advancing offset; only new bytes return.

- [ ] T008 [US2] Implement `wp_cli_job(job_id, offset, limit, *, project_dir)` in `mcp/wp-server/tools/wp.py` (validate id; read `.status`/`.log` slice; return status/exit_code/stdout/bytes_read/truncated).
- [ ] T009 [US2] Add `./sb job <id> [--follow]` in `sandbox/commands/jobs.py` (status + tail; `--follow` streams), self-registered in `sandbox/registry.py`.
- [ ] T010 [US2] Live verification (quickstart §2): running→completed; incremental slices via offset; **a finished job re-queried still returns its terminal status + exit_code (FR-006 durability, analysis F4)**; `offset` past EOF → empty slice, not an error.

## Phase 5: User Story 3 — Cancel a running job (P1)

**Goal**: kill a running job; no-op on finished/unknown.
**Independent test**: start long job, kill it, process gone + status cancelled.

- [ ] T011 [US3] Implement `wp_cli_job_kill(job_id, *, project_dir)` (SIGTERM the pid's process group — container via `compose exec`, herd directly; write `143` to `.status`; no-op on finished/unknown).
- [ ] T012 [US3] Add `./sb job <id> --kill` to `sandbox/commands/jobs.py`.
- [ ] T013 [US3] Live verification (quickstart §3): kill stops the process **and its child `wp`/`php` (no orphans — verify via process list; analysis F6)**; status reports `exit_code:143`; re-kill is a clean no-op.

## Phase 6: User Story 4 — CLI parity (P2)

**Goal**: full lifecycle from the CLI incl. listing + prune.
**Independent test**: list jobs and prune from the CLI.

- [ ] T014 [US4] Implement `./sb jobs [--prune]` (glob the job dir; list with status; `--prune` removes old artifacts) in `sandbox/commands/jobs.py`.
- [ ] T015 [US4] Add age-based auto-prune (default 24h) invoked on `jobs` and **on instance up** (satisfies SC-005 retention; analysis F3). The on-up hook ships regardless of whether the US4 CLI listing is in the MVP slice, so retention is never silently skipped.
- [ ] T016 [US4] Live verification (quickstart §4): list shows jobs; prune removes them.

## Phase 7: User Story 5 — Works on every driver (P1)

**Goal**: identical behavior on Docker and herd.
**Independent test**: run async/poll/kill on a herd instance.

- [ ] T017 [US5] Verify the herd `nohup` path uses the pinned `php<MM>`/`wp` shims + correct `.sb-jobs/` host path; reconcile any divergence from the Docker path.
- [ ] T018 [US5] Live verification (quickstart §5): async + poll + kill on a herd instance match Docker.

## Phase 8: Polish & Cross-Cutting

- [ ] T019 [P] Safety verification (quickstart §6): forged `job_id` rejected before filesystem access; async accepts no command the sync path wouldn't.
- [ ] T020 [P] Docs-with-code: add `wp_cli` `async`, `wp_cli_job`, `wp_cli_job_kill` to the CLAUDE.md MCP-surface table + MCP server `instructions`; document `./sb wp --async`/`job`/`jobs` in `docs/sandbox-config-reference.md`.

## Dependencies & Order

- Setup (T001-T002) → Foundational (T003-T004) → stories.
- Priority order: US1 (T005-T007) → US3 (T011-T013) → US2 (T008-T010) → US4 (T014-T016) → US5 (T017-T018) → Polish.
- US3 cancel depends on T004 pid self-report; US2/US3 depend on Foundational reader/launch. `[P]` tasks touch distinct files.

## MVP scope

US1 (T001-T007) — async start + job_id is the minimal increment that unblocks the agent.
