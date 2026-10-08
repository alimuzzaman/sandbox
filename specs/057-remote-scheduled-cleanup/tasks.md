# Tasks: Scheduled Safe Cleanup on a Remote

**Input**: `specs/057-remote-scheduled-cleanup/` (plan.md, spec.md, research.md, data-model.md, contracts/routine-control.md, quickstart.md)

**Tests**: requested (tests first). Each test task must fail before its implementation task.

**Restricted files (never edit)**: `sandbox/commands/hosting.py`, `sandbox/delivery/hosting.py`, `sandbox/core/_remote.py`, `tests/test_hosting.py`.

## Phase 1: Setup

- [x] T001 Create package `sandbox/resources/cleanup_routine/__init__.py` (empty public surface; docstring naming spec 057)
- [x] T002 Promote the helpers reused by the routine in `sandbox/resources/schedule.py` (`_atomic_write`, `_write_unit`, `_snapshot_installation`, `_restore_installation`, `_bounded_file_bytes`, `_run_bounded`) to public names, keeping the private aliases so existing callers and `tests/test_storage_monitor_schedule.py` stay green

## Phase 2: Foundational (blocks all stories)

- [x] T003 [P] Write failing tests in `tests/test_host_reclaim_guard.py`: guard acquire/busy/release with a temp `SANDBOX_HOME`; apply-lock probe reports busy when another fd holds `LOCK_EX` on a temp lock path, and not held when the file is missing; parity test importing `sandbox.commands.hosting._CADDY_LOCK_PATH`, `sandbox.hosting.front_door.nginx.LOCK_PATH` and the docker-pool lock literal from `sandbox.core._remote` (read only), asserting they equal the guard module's literals
- [x] T004 Implement `sandbox/resources/host_guard.py`: `RECLAIM_GUARD` path under `RUNTIME/resources/reclaim-host.lock` (0600, created safely, no symlink), `APPLY_TRANSACTION_LOCKS` tuple, `try_reclaim_guard()` context manager (non-blocking, yields bool), `apply_transaction_active()` (read-only `LOCK_SH|LOCK_NB` probe)
- [x] T005 Write failing tests in `tests/test_resource_reclaim_service.py` (ProbeCase style): a probe `reclaim` while the guard is held returns `host_reclaim_busy` and writes no manifest line; `ReclaimService.cleanup` maps it to `status == "skipped"`, code `host_reclaim_busy`; `reap(trigger="scheduled_routine")` passes that trigger into the manifest `run_start` and intents
- [x] T006 Add the guard to the probe's `reclaim_action` in `sandbox/resources/remote.py` (after run_id validation, before `DELETION_DIR.mkdir`/`run_start`; held until return; apply-lock probe first); the probe program must import nothing new at module level that the shipped program cannot reach — inline the minimal flock code in `_REMOTE_PROGRAM` if `host_guard` is not importable there
- [x] T007 In `sandbox/resources/reclaim_service.py`: add `trigger: str = "reap"` keyword to `reap()`; add an optional validated `run_id` keyword to `cleanup()` so the routine can name its manifest before the host run; map a provider `host_reclaim_busy` refusal to a skipped result and leave the plan begun so the same plan id can be retried (`PlanStore` has no "unused" terminal state)
- [x] T008 [P] Write failing tests in `tests/test_cleanup_routine_contract.py`: request validation (unknown keys, caps, exactly the three actions), exclusion validator (≤32, ≤128 chars, no control chars or `/`, balanced brackets → else `invalid_exclusion`), timeout/delay validated with storage-monitor time-span rules (`invalid_timeout`), response shape per contracts/routine-control.md with no SSH target or token fields
- [x] T009 Implement `sandbox/resources/cleanup_routine/contract.py` to pass T008
- [x] T010 [P] Write failing tests in `tests/test_cleanup_routine_store.py`: config write/read 0600 under 0700 dir; atomic replace on re-enable; run records keep newest 30 (31st prunes oldest); open `running` record older than timeout+60s is finalized `timed_out` with counts from manifest outcome lines
- [x] T011 Implement `sandbox/resources/cleanup_routine/store.py` to pass T010

## Phase 3: User Story 1 — Recurring safe cleanup runs on the remote (P1, MVP)

**Goal**: enable installs a host timer; each run reclaims safe-tier waste with manifest-first deletion.
**Independent test**: seeded temp host → enable (faked systemd) → `--routine-run` → manifest and host state match SC-002.

- [ ] T012 [P] [US1] Write failing tests in `tests/test_cleanup_routine_units.py`: rendered service/timer text (Type=oneshot, UMask=0077, TimeoutStartSec=bound+60s, OnCalendar, RandomizedDelaySec, Persistent=true, fixed ExecStart argv `<sb-src>/sb resources routine --routine-run --json`); `systemd-analyze calendar` failure → `invalid_cadence` with no file written; missing systemctl → `systemd_unavailable`; linger off → `linger_disabled`; install failure restores prior units (`routine_install_failed`); re-enable leaves exactly one timer
- [ ] T013 [US1] Implement `sandbox/resources/cleanup_routine/units.py` (render, validate cadence and read next elapse, install via `systemctl --user daemon-reload` + `enable --now`, remove via `disable --now` + unlink) using the public schedule helpers from T002
- [ ] T014 [P] [US1] Write failing tests in `tests/test_cleanup_routine_run.py` using `LocalProbeAdapter(home=tmp)` with a seeded host (eligible orphan dirs + package volumes, one STOPPED workspace, one `lenzora-*` excluded workspace, one LIVE instance, one PROTECTED resource): outcome `reclaimed`, manifest intents precede outcomes, zero forbidden items as candidates/intents/removals, excluded item skipped `excluded_by_request`; nothing eligible → `nothing_to_do` with `manifest: null`; incomplete inventory → `refused`/`inventory_incomplete`, nothing removed; guard held → `skipped_busy`; forced small bound → `timed_out` listing pre-bound removals; host `resources.reclaim_exclude` merged, operator config ignored; run record carries the host runtime revision
- [ ] T015 [US1] Implement `sandbox/resources/cleanup_routine/run.py` (load config, finalize stale record, guard pre-check, `ReclaimService(None).reap(tier="safe", confirm=True, exclude_names=effective, trigger="scheduled_routine", budget)`, map results, write run record)
- [ ] T016 [US1] Implement `sandbox/resources/cleanup_routine/host.py`: `handle(payload)` for `cleanup_routine_enable` (revision check → `runtime_revision_mismatch` before any write; validate; install units; record config) and wire it into `_resource_contract` in `mcp/wp-server/server.py` ahead of the probe allowlist; add a route test in `tests/test_server_transport.py`
- [ ] T017 [P] [US1] Write failing tests in `tests/test_cleanup_routine_cli.py`: `resources routine --enable` without `--confirm` → `protected_operation`, no request; unknown/unprovisioned remote refused before request; enable request carries `expected_runtime_revision` and the resolved `schedule_timeout`/`schedule_randomized_delay` from `monitor.resolve_policy(remote)`; `--cadence` and repeated `--exclude` forwarded; JSON output has no SSH target
- [ ] T018 [US1] Add `routine` action to `sandbox/commands/resources.py` (parser flags per contract; hidden `--routine-run` dispatching to `run.py`) and client method `routine(action, **fields)` on `RemoteResourceAdapter` in `sandbox/resources/remote.py` using `remote_resource_request`

## Phase 4: User Story 2 — See what the routine did (P2)

**Goal**: status returns config, effective exclusions, enable and last-run revisions, and run history.
**Independent test**: after reclaimed, nothing-to-do and refused runs, status matches contract.

- [ ] T019 [P] [US2] Extend `tests/test_cleanup_routine_cli.py` and `tests/test_cleanup_routine_run.py`: `cleanup_routine_status` returns enabled state, cadence, effective exclusions, `enabled_revision`, `last_run_revision`, `next_run`, newest-first runs ≤ 30; manifest reference only for runs that attempted removal; a stale open record is finalized on status read
- [ ] T020 [US2] Implement the status action in `sandbox/resources/cleanup_routine/host.py` and the CLI rendering (human and `--json`) in `sandbox/commands/resources.py`

## Phase 5: User Story 3 — Turn the routine off (P3)

**Goal**: disable removes the timer and keeps history.
**Independent test**: enable → disable → no timer files, status disabled with history.

- [ ] T021 [P] [US3] Extend `tests/test_cleanup_routine_units.py` and `tests/test_cleanup_routine_cli.py`: disable without `--confirm` refused; disable runs `disable --now`, removes both unit files, sets `enabled: false`, keeps runs; disable when not enabled is idempotent `ok`; failure → `routine_remove_failed`
- [ ] T022 [US3] Implement the disable action in `sandbox/resources/cleanup_routine/host.py`, `units.py` and the CLI

## Phase 6: Polish & Cross-Cutting

- [ ] T023 [P] Docs: new "Scheduled safe cleanup on a remote" section in `docs/resource-monitoring.md` (enable/status/disable, cadence grammar, policy source, guard and busy semantics, trigger `scheduled_routine`, retention, migrate needed); update "Deletion manifest" trigger list
- [ ] T024 [P] Docs: CLAUDE.md gotcha 23, `README.md` resources section, `skills/sandbox-cli/SKILL.md` and `.agents/skills/sandbox-cli/SKILL.md`, `docs/remote-hosting.md` remote resource note
- [ ] T025 Run `python3 -m unittest` for every test module in quickstart.md plus `tests.test_architecture_boundaries`; fix regressions
- [ ] T026 Live proof per quickstart.md on a disposable remote after `sb remote service migrate` with no active deploy and pinned consumers repinned; record evidence in this file and resolve feedback 8a3e8c35

## Dependencies

- T001–T002 → everything.
- Foundational: T003→T004; T005→T006,T007 (needs T004); T008→T009; T010→T011.
- US1 needs all of Phase 2. Within US1: T012→T013; T014→T015; T016 needs T009, T011, T013; T017→T018 (needs T016).
- US2 needs US1's host handler (T016) and store (T011). US3 needs T013 and T016.
- Polish after US1–US3; T026 last.

## Parallel examples

- Phase 2: T003, T008, T010 together (separate test files).
- US1: T012, T014, T017 together; then T013 and T015 in parallel (different files).
- Polish: T023 and T024 together.

## Implementation strategy

MVP = Phases 1–3 (US1): enable plus runs, provable with the run tests. Then US2
(visibility) and US3 (disable) as increments. Live proof (T026) only after a
coordinated remote install.
