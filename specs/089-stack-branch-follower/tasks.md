# Tasks: Stack branch follower

**Input**: spec.md, plan.md. One release phase; install and runtime acceptance on
xspeed-dev are owner steps after review. Checkmarks mean implemented in source.

## Phase 1: Setup

- [x] T001 PRD with independent readiness reviews, spec, plan and tasks in specs/089-stack-branch-follower/.

## Phase 2: Foundations

- [x] T002 Declaration parsing and validation (names, GitHub repos, branches, prepare argv, placeholders, limits) in sandbox/hosting/follow/spec.py (FR-002).
- [x] T003 Follow store: owner-only layout, per-follow JSON, host and apply locks, audit log in sandbox/hosting/follow/store.py (FR-012).
- [x] T004 Deploy keys, pinned GitHub host key, fetch and detached checkout in sandbox/hosting/follow/git.py (FR-003, FR-005).
- [x] T005 Timer render/install/remove reusing schedule and cleanup routine helpers in sandbox/hosting/follow/units.py (FR-004).

## Phase 3: US1 — Declare a follow

- [x] T006 [US1] `follow` action, flags and routing in sandbox/cli.py and sandbox/commands/hosting.py; joined dispatch selection in sandbox/hosting/server_cli_dispatch.py (FR-001).
- [x] T007 [US1] `add` and `key` in sandbox/commands/host_follow.py: validate, store, keys, timer; refusals store nothing (FR-003).

## Phase 4: US2/US3 — Poll and apply

- [x] T008 [US2] Pure `decide()` and the tick in sandbox/hosting/follow/tick.py (FR-005–007, FR-009–010).
- [x] T009 [US2] The attempt job body in sandbox/hosting/follow/apply.py (FR-008, FR-010).

## Phase 5: US4 — Owner controls

- [x] T010 [US4] `status`, `pause`, `resume`, `run-now`, `remove` in sandbox/commands/host_follow.py (FR-011).

## Phase 6: Polish

- [x] T011 docs/remote-hosting.md section and CHANGELOG Unreleased line.
- [x] T012 Regressions in tests/test_host_follow.py after the implementation, run as remote durable jobs; base comparison against 370db06.
- [ ] T013 Owner: install on xspeed-dev (migrate, add, deploy keys in GitHub) and runtime acceptance SC-004.
