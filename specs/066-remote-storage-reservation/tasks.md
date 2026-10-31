# Tasks: Remote Storage Reservation

Tests first within each story.

## Phase 1: Setup
- [x] T001 Create `sandbox/resources/storage_floor.py` skeleton (decision dataclass-free dict API) and `tests/test_storage_floor.py`

## Phase 2: US1 Storage-floor admission (P1)
- [x] T002 [US1] Tests: pure `evaluate_storage_floor` (admit, below, equal, unknown, concurrent reservations, default estimate) in tests/test_storage_floor.py
- [x] T003 [US1] Tests: `deploy_exact_working_tree` raises `StorageFloorAdmissionError` before `ensure_deploy_repo`; no floor means no probe; `remote_jobs._deploy` translates to `RemoteJobStorageFloorError` payload in tests/test_storage_floor.py
- [x] T004 [US1] Implement evaluator + remote probe/reserve program + `remote_storage_floor_admission` in sandbox/resources/storage_floor.py
- [x] T005 [US1] Wire gate in sandbox/core/_remote.py and translation in sandbox/transports/remote_jobs.py
- [x] T006 [US1] `--storage-floor-gb` on `remote add`, new `remote set-storage-floor` in sandbox/cli.py and sandbox/commands/remote.py; show floor in remote list

## Phase 3: US2/US3 Finished-idle reap and complete teardown (P1)
- [x] T007 [US2] Tests: finished retained job older than threshold yields `finished_job_idle`, recent/missing finished_at or non-terminal keeps `active_job` in tests/test_reclaim_finished_idle.py
- [x] T008 [US2] Tests: `_entry_tier` returns `safe/finished_job_idle`; hosted/registry still PROTECTED in tests/test_reclaim_finished_idle.py
- [x] T009 [US2] Tests: reclaim_run worktree teardown removes project containers, unshared volumes, networks, then source; failure keeps source with `stack_teardown_incomplete`; `sandbox-host-*` project never touched in tests/test_reclaim_project_teardown.py
- [x] T010 [US2] Implement inventory evidence in sandbox/resources/remote.py and tier rule in sandbox/resources/reclaim.py
- [x] T011 [US2] Implement project teardown in sandbox/resources/remote.py reclaim_run

## Phase 4: US4 Host heal (P2)
- [x] T012 [US4] Tests: candidate selection, dry run restarts nothing, confirm gated strictly above floor and re-selects, unknown floor refuses in tests/test_host_heal.py
- [x] T013 [US4] Implement sandbox/hosting/heal.py and `sb host heal` wiring in sandbox/cli.py, sandbox/commands/hosting.py

## Phase 5: US5 Phase B plan (P3)
- [x] T014 [US5] Write specs/066-remote-storage-reservation/phase-b-isolation.md

## Phase 6: Polish
- [x] T015 Docs: README remote section, CLAUDE.md gotcha 31, CHANGELOG
- [ ] T016 Full suite via Haiku; Sol merge-gate review; push; migrate xcloud-london; set floor 30; live quickstart

## Dependencies
US1 independent; US2 independent of US1; US4 reuses the probe from T004.
