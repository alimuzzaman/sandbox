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

- [ ] T001 Add a `mode` (`db-only|full`) writer/reader for snapshot `META` + the reserved baseline dir `__install__/` (`@install` is a label only). Put the reserved-name guard next to `_valid_snapshot_name`/`_slug_snapshot_name` in **`sandbox/core/_bridge.py`** (NOT data.py — analysis F3), and enforce baseline protection at all three mutation sites: `cmd_snapshot`/delete in `data.py`, `_bridge_handle` (POST `/snapshot`, DELETE `/snapshot/<name>`) in `_bridge.py`, and `_web_do_action` in `_dash.py`.

## Phase 2: Foundational (blocking prerequisites)

- [ ] T002 Implement a shared `capture_db_only(instance, name)` + `restore_snapshot(instance, name)` helper path in `data.py` reused by snapshot/reset/baseline (so CLI + MCP + baseline share one implementation).

## Phase 3: User Story 1 — DB-only snapshot (P1)

**Goal**: capture only the DB; restore correctly without touching uploads.
**Independent test**: `snapshot --db-only` omits uploads.tgz and restores cleanly.

- [ ] T003 [US1] Add `--db-only` to `cmd_snapshot` — read via `getattr(args, "db_only", False)` ([data.py](../../sandbox/commands/data.py) reads `args.force` directly, so all callers matter); skip the uploads tar; write `mode=db-only` to META. Update the **three** callers that build args without `db_only`: the cli.py snapshot subparser, the bridge POST `/snapshot` (`_bridge.py`), and the dashboard `_web_do_action` (`_dash.py`). Add a **net-new** `snapshot(db_only)` MCP tool (no snapshot MCP tool exists today — analysis F2/F4).
- [ ] T004 [P] [US1] `cmd_snapshots` + the bridge `GET /snapshots` report each snapshot's `mode` **and** list/filter the `__install__` baseline separately (it lives in the same `snapshots_dir` — analysis F8).
- [ ] T005 [US1] Live verification (quickstart §1-§2): db-only snapshot has db.sql+META, no uploads.tgz, faster; restore rolls back DB and leaves uploads untouched.

## Phase 4: User Story 2 — Reset to fresh install (P1)

**Goal**: auto-baseline at install; `reset` restores it in seconds.
**Independent test**: dirty DB → reset → post-install state.

- [ ] T006 [US2] Hook auto-capture of the reserved baseline (db-only) into the **ensure/onboard flow after plugin/theme wiring (`_instances.py`) + seed import (`_onboard_instance` in `_misc.py`)** — NOT `cmd_install` (which runs before wiring/seed). Idempotent. [analysis F1]
- [ ] T007 [US2] Implement `cmd_reset` (`./sb reset [--yes] [--rebaseline]`) in `data.py` (restore baseline; `--rebaseline` re-captures; actionable guidance if no baseline); register in the COMMANDS registry **and add `"reset"` to `INSTANCE_SCOPED` in `cli.py`** + add the `reset` subparser (`--yes`, `--rebaseline`) so instance resolution + error guidance fire (analysis F7).
- [ ] T008 [P] [US2] Implement `wp_reset(confirm, rebaseline=false, *, project_dir)` MCP tool.
- [ ] T009 [US2] Live verification (quickstart §3): baseline exists post-install; dirty DB → reset → post-install state, uploads intact; no-baseline guidance; `--rebaseline` works.

## Phase 5: User Story 4 — Reset is gated (P1)

**Goal**: destructive reset is confirmed; baseline protected.
**Independent test**: reset prompts/requires confirm; baseline can't be clobbered.

- [ ] T010 [US4] Add the destructive gate: CLI confirm unless `--yes`; MCP `wp_reset` requires `confirm=true`. Enforce baseline protection in snapshot/delete paths.
- [ ] T011 [US4] Live verification (quickstart §4): unconfirmed reset prompts/refuses; `snapshot @install`/delete can't overwrite/remove the baseline.

## Phase 6: User Story 3 — From the dashboard (P2)

**Goal**: DB-only capture + reset from wp-admin.
**Independent test**: dashboard toggle + button round-trip via the bridge.

- [ ] T012 [US3] Extend the snapshot mu-plugin **template** (`_write_snapshot_muplugin`/`_SNAPSHOT_MU_TEMPLATE` in `_bridge.py`/`_paths.py`): a "DB only" capture checkbox + a "Reset to fresh install" button; add the `db_only` param to POST `/snapshot` and a new POST `/reset` route in **`_bridge_handle`** (the mu-plugin's trust-boundary bridge, not `_dash.py` — analysis F6), out-of-band + completion polling.
- [ ] T013 [US3] Live verification (quickstart §5): dashboard DB-only capture + reset complete and report status.

## Phase 7: Polish & Cross-Cutting

- [ ] T014 [P] herd guard: `reset`/`--db-only` emit the existing herd-unsupported notice on herd instances (consistent with `cmd_snapshot`/`cmd_restore`).
- [ ] T015 [P] Docs-with-code: CLAUDE.md snapshot section + a `reset` common-loop entry + MCP table (`wp_reset`, `snapshot` db_only); update `skills/snapshot/SKILL.md`; cross-reference spec 002.

## Dependencies & Order

- Setup (T001) → Foundational (T002) → stories.
- Priority order: US1 (T003-T005) → US2 (T006-T009) → US4 (T010-T011) → US3 (T012-T013) → Polish.
- US2/US4 depend on the T002 shared helpers; US3 depends on US1+US2 (db-only + reset) existing. `[P]` tasks touch distinct files.

## MVP scope

US1 + US2 (T001-T009) — db-only snapshots + `./sb reset` to the post-install baseline —
is the core increment; the dashboard surface (US3) is the P2 add-on.
