---
description: "Task list for DB-Only Snapshots & Reset-to-Fresh-Install"
---

# Tasks: DB-Only Snapshots & Reset-to-Fresh-Install

**Input**: Design documents from `specs/008-db-snapshots-reset/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task.

## Path Conventions

Extends the existing snapshot feature: `sandbox/commands/data.py` +
`sandbox/commands/lifecycle.py` (`cmd_install`) + `mcp/wp-server/tools/` + the
spec-002 dashboard mu-plugin/bridge.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Add a `mode` (`db-only|full`) writer/reader for snapshot `META` + the reserved baseline dir `__install__/` (`@install` is a label only). Put the reserved-name guard next to `_valid_snapshot_name`/`_slug_snapshot_name` in **`sandbox/core/_bridge.py`** (NOT data.py — analysis F3), and enforce baseline protection at all three mutation sites: `cmd_snapshot`/delete in `data.py`, `_bridge_handle` (POST `/snapshot`, DELETE `/snapshot/<name>`) in `_bridge.py`, and `_web_do_action` in `_dash.py`.  **DONE: META mode + reserved __install__ dir + reserved-name guard in cmd_snapshot. (bridge/dashboard delete-protection: follow-up with the dashboard slice.)**

## Phase 2: Foundational (blocking prerequisites)

- [x] T002 Implement a shared `capture_db_only(instance, name)` + `restore_snapshot(instance, name)` helper path in `data.py` reused by snapshot/reset/baseline (so CLI + MCP + baseline share one implementation).  **DONE: shared _capture_snapshot / _restore_snapshot in data.py, reused by snapshot/reset/baseline.**

## Phase 3: User Story 1 — DB-only snapshot (P1)

**Goal**: capture only the DB; restore correctly without touching uploads.
**Independent test**: `snapshot --db-only` omits uploads.tgz and restores cleanly.

- [x] T003 [US1] Add `--db-only` to `cmd_snapshot` — read via `getattr(args, "db_only", False)` ([data.py](../../sandbox/commands/data.py) reads `args.force` directly, so all callers matter); skip the uploads tar; write `mode=db-only` to META. Update the **three** callers that build args without `db_only`: the cli.py snapshot subparser, the bridge POST `/snapshot` (`_bridge.py`), and the dashboard `_web_do_action` (`_dash.py`). Add a **net-new** `snapshot(db_only)` MCP tool (no snapshot MCP tool exists today — analysis F2/F4).  **DONE + live-verified: cmd_snapshot --db-only (getattr) skips uploads + META mode=db-only; cli subparser. (bridge/dashboard --db-only caller + snapshot MCP db_only: follow-up with dashboard slice.)**
- [x] T004 [P] [US1] `cmd_snapshots` + the bridge `GET /snapshots` report each snapshot's `mode` **and** list/filter the `__install__` baseline separately (it lives in the same `snapshots_dir` — analysis F8).  **DONE + live-verified: ./sb snapshots shows mode + labels @install baseline separately.**
- [x] T005 [US1] Live verification (quickstart §1-§2): db-only snapshot has db.sql+META, no uploads.tgz, faster; restore rolls back DB and leaves uploads untouched.  **DONE + live-verified: db-only snapshot has db.sql+META, no uploads.tgz; restore leaves uploads intact.**

## Phase 4: User Story 2 — Reset to fresh install (P1)

**Goal**: auto-baseline at install; `reset` restores it in seconds.
**Independent test**: dirty DB → reset → post-install state.

- [x] T006 [US2] Hook auto-capture of the reserved baseline (db-only) into the **ensure/onboard flow after plugin/theme wiring (`_instances.py`) + seed import (`_onboard_instance` in `_misc.py`)** — NOT `cmd_install` (which runs before wiring/seed). Idempotent. [analysis F1]  **DONE: capture_install_baseline hooked into ensure after _wire_project_plugins/themes; captured ONCE (no-op if exists).**
- [x] T007 [US2] Implement `cmd_reset` (`./sb reset [--yes] [--rebaseline]`) in `data.py` (restore baseline; `--rebaseline` re-captures; actionable guidance if no baseline); register in the COMMANDS registry **and add `"reset"` to `INSTANCE_SCOPED` in `cli.py`** + add the `reset` subparser (`--yes`, `--rebaseline`) so instance resolution + error guidance fire (analysis F7).  **DONE + live-verified: ./sb reset [--yes] [--rebaseline]; registered + added to INSTANCE_SCOPED + subparser; actionable msg when no baseline.**
- [x] T008 [P] [US2] Implement `wp_reset(confirm, rebaseline=false, *, project_dir)` MCP tool.  **DONE: wp_reset(confirm, rebaseline) MCP tool (tools/data.py) → ./sb reset.**
- [x] T009 [US2] Live verification (quickstart §3): baseline exists post-install; dirty DB → reset → post-install state, uploads intact; no-baseline guidance; `--rebaseline` works.  **DONE + live-verified: rebaseline created baseline; dirtied blogname → reset --yes → rolled back to 'Sandbox templately-rebuild2'; uploads intact.**

## Phase 5: User Story 4 — Reset is gated (P1)

**Goal**: destructive reset is confirmed; baseline protected.
**Independent test**: reset prompts/requires confirm; baseline can't be clobbered.

- [x] T010 [US4] Add the destructive gate: CLI confirm unless `--yes`; MCP `wp_reset` requires `confirm=true`. Enforce baseline protection in snapshot/delete paths.  **DONE: reset CLI confirm unless --yes; wp_reset requires confirm=true; cmd_snapshot rejects the reserved baseline name.**
- [x] T011 [US4] Live verification (quickstart §4): unconfirmed reset prompts/refuses; `snapshot @install`/delete can't overwrite/remove the baseline.  **DONE: `./sb snapshot __install__` rejected (reserved); baseline only replaced via --rebaseline.**

## Phase 6: User Story 3 — From the dashboard (P2)

**Goal**: DB-only capture + reset from wp-admin.
**Independent test**: dashboard toggle + button round-trip via the bridge.

- [x] T012 [US3] Completed 2026-07-16: the snapshot template now has DB-only capture and a confirmed "Reset to fresh install" action; the token-authenticated bridge accepts asynchronous `POST /reset` and the UI polls the existing job endpoint. Focused bridge tests cover the reset route.
- [~] T013 [US3] Live verification is pending a supported bridge/MCP restart: the disposable dashboard loaded successfully, but its already-running bridge returned 404 for the newly added `/reset` route. Restart the bridge/server, then repeat the DB-only capture and reset round trip.

## Phase 7: Polish & Cross-Cutting

- [x] T014 [P] herd guard: `reset`/`--db-only` emit the existing herd-unsupported notice on herd instances (consistent with `cmd_snapshot`/`cmd_restore`).  **DONE: reset/--db-only die with the herd-unsupported notice on herd (consistent with snapshot/restore).**
- [x] T015 [P] Docs-with-code: CLAUDE.md snapshot section + a `reset` common-loop entry + MCP table (`wp_reset`, `snapshot` db_only); update `skills/snapshot/SKILL.md`; cross-reference spec 002.  **DONE: CLAUDE.md MCP table adds `wp_reset`; "Saving / restoring state" common-loop covers reset vs named snapshots; `skills/snapshot/SKILL.md` gained `@install` reset + wp-admin (spec 002) sections, cross-referenced.**

## Dependencies & Order

- Setup (T001) → Foundational (T002) → stories.
- Priority order: US1 (T003-T005) → US2 (T006-T009) → US4 (T010-T011) → US3 (T012-T013) → Polish.
- US2/US4 depend on the T002 shared helpers; US3 depends on US1+US2 (db-only + reset) existing. `[P]` tasks touch distinct files.

## MVP scope

US1 + US2 (T001-T009) — db-only snapshots + `./sb reset` to the post-install baseline —
is the core increment; the dashboard surface (US3) is the P2 add-on.

## Enhancement — 2026-06-24: auto-capture on create/recreate + robustness

Per a follow-up request ("on first instance create/recreate we should create a
full/db snapshot"):

- The post-provision capture (in `_instances.py` ensure path, which create AND
  recreate run) now takes **two** snapshots: the existing db-only `__install__`
  baseline (powers `reset`) **plus** a full named `install-baseline` (DB +
  uploads) via `capture_install_full_snapshot` — `./sb restore install-baseline`
  for a complete post-install rollback. `install-baseline` is a normal listed
  snapshot; `__install__` stays hidden.
- A `destroy` wipes `snapshots_dir`, so a recreate refreshes both to the fresh
  install (no stale baseline).
- Robustness: `_capture_snapshot` now removes a partial target + re-raises on
  failure, and `capture_install_baseline`/`capture_install_full_snapshot` LOG
  (not silently swallow) — fixes the empty 0 KB `__install__` left by a swallowed
  failure. Live-verified: both captured, listed correctly, `reset` + `restore
  install-baseline` both roll back.

## Phase 8: Convergence

- [ ] T016 Capture the DB-only `@install` and full `install-baseline` snapshots for newly provisioned instances only after their final plugin/theme/seed onboarding state is complete; retain idempotency for existing instances per FR-004/FR-005 (partial).
  **PARTIAL 2026-08-14:** focused ensure-path coverage now proves one capture after
  install plus final plugin/theme wiring and proves the ready-instance fast path does
  not recapture. Seed-completion ordering and the required live new-instance proof
  remain open.
- [x] T017 Remove a pre-existing `uploads.tgz` when `snapshot --db-only --force` overwrites a full snapshot, so its recorded mode and restore behavior remain DB-only per FR-001/FR-002. **DONE 2026-08-20 (source/tests): staged `_capture_snapshot` replacement removes stale archives; focused CLI-path and helper tests assert no `uploads.tgz`, `mode=db-only`, and no uploads archive restore.**
- [x] T018 Make `./sb snapshots` and the dashboard bridge represent the protected `@install` baseline explicitly and separately from normal snapshots, without exposing it as an ordinary restore/delete target, per FR-003/FR-006. **DONE 2026-08-20 (source/tests): CLI and bridge list the baseline as a protected reset target; CLI, bridge, and dashboard capture/restore/delete paths reject reserved baseline labels before dispatch.**
- [ ] T019 Dispatch the dashboard’s reset operation through `cmd_reset` with its explicit confirmed arguments and add focused coverage, per FR-008. **PARTIAL (2026-08-14): the wp-admin AJAX proxy now recognizes reset and forwards the UI's explicit boolean confirmation; the bridge refuses missing/false confirmation before job acceptance and passes `yes=true`, `confirm=true`, and `rebaseline=false` to `cmd_reset`. Focused template/bridge tests pass. Live wp-admin reset and polling remain unverified.**
- [x] T020 Add the MCP `snapshot` tool with `db_only` support, register it in the data manifest, and cover its capability and CLI forwarding behavior per FR-009. **DONE 2026-08-20 (source/tests): `tools.data.snapshot` is manifest-owned, capability-gated before instance resolution, forwards `db_only`/`force`, and returns bounded metadata.**
- [x] T021 Update the Spec 008 contract, quickstart, snapshot skill, and focused tests to describe and verify the corrected baseline, DB-only overwrite, dashboard, and MCP semantics per Constitution V. **DONE 2026-08-20 (source/tests/docs): docs and fixtures now match the current confirmation and safe-output boundaries; live dashboard and seed-order evidence remain explicitly open.**

## Phase 9: Convergence — 2026-08-13 (27-feedback restore safety)

These tasks remain open; no prior task is marked complete by this amendment.

- [x] T022 [US4] Add a noninteractive named-restore regression for `adde58a6`
  proving missing confirmation fails before any database reset/import or archive
  extraction and returns the stable refusal code.
- [x] T023 [US4] Add interactive-cancel and explicit-confirmation tests covering
  CLI, MCP, and bridge/dashboard callers; cancellation must preserve state and
  confirmation must dispatch exactly the requested snapshot.
- [x] T024 [US4] Reconcile `contracts/cli-contract.md`, quickstart, and the
  command/MCP interface fixtures with the one confirmation contract, including
  safe JSON/error output and no secret or snapshot-content disclosure. **DONE
  2026-08-20 (source/tests/docs): named restore requires `--yes`/`confirm=true`
  before provider dispatch; adapter responses are bounded metadata/errors and
  omit command lines, paths, credentials, and snapshot contents.**
