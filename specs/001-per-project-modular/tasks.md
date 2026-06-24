---
description: "Task list — Per-Project-First Instance Model & Modular sb"
---

# Tasks: Per-Project-First Instance Model & Modular `sb`

**Input**: Design documents from `specs/001-per-project-modular/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
contracts/cli-contract.md ✅, quickstart.md ✅ (constitution at `.specify/memory/constitution.md`)

**Tests**: No unit/contract test tasks — constitution Principle IV mandates **live-stack
verification** (CLI/MCP against a running instance). Each phase ends with live checks from
[quickstart.md](./quickstart.md) instead.

**Organization**: by user story. Stage labels (A/B/C from plan.md) map to stories: Stage A →
US3 foundation (parity), Stage B → US2 (remove `main`), Stage C → US4 (modular). US1 (cwd
routing) is already delivered (commits `dc0b276`, `37509c7`).

**Branch**: feature `001-per-project-modular`; developed on the shared worktree branch
`cwd-instance-resolution`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1..US4 from spec.md

## Path Conventions

`sb`, `sandbox_core.py`, `mcp/wp-server/server.py`, `src/web/src/*.ts`,
`config/sandbox-web.js`, `package.json`, `scripts/make-release.sh`. Target package (Stage C):
`sandbox/core/*`, `sandbox/commands/*`, `sandbox/cli.py`. Contracts: [cli-contract.md](./contracts/cli-contract.md).

---

## Phase 1: Setup

- [x] T001 Capture a pre-change baseline live-smoke transcript from a registered instance (`sb status`, `sb wp plugin list`, `sb doctor`, `sb snapshots` against a running instance) and append it to `specs/001-per-project-modular/quickstart.md` notes as the parity baseline.
- [x] T002 [P] Exclude spec-kit dev tooling from the shipped product: add `!.specify/**` + `!skills/speckit-*/**` negations to `package.json` `files`, and add `.specify` + `skills/speckit-*` to the prune list in `scripts/make-release.sh` (FR-009, C5).

**Checkpoint**: Baseline recorded; spec-kit will not ship.

---

## Phase 2: Foundational — Stage A (Feature parity before removal) ⚠️ BLOCKING

**Purpose**: Make every feature work on the per-instance model so removing `main` loses
nothing (constitution VI; research R2/R6). MUST complete before Phase 3.

- [x] T003 [US3] Audit every call site of `compose`, `wpcli`, `save_local_app_password`, `_active_project_name` in `sb` and record any relying on the `DEFAULT_INSTANCE` default (must pass an explicit `instance` before Stage B deletes the default) — research R6.
- [x] T004 [US3] Unify app-password WRITE on the per-instance key: in `sb` `save_local_app_password` (~L2028) always write `instances.<name>.app_password`; remove the `instance == DEFAULT_INSTANCE` → `mcp.wp.application_password` branch (~L2043) — contract C3.
- [x] T005 [US3] Unify app-password READ in `sb` `cmd_doctor` (~L2161-2166) to the per-instance key for every instance (drop the `main` legacy-key branch).
- [x] T006 [P] [US3] Unify app-password READ in `mcp/wp-server/server.py` `_resolve_instance` (~L191-198) to the per-instance key for every instance (drop the `DEFAULT_INSTANCE` legacy branch) — contract C3.
- [x] T007 [US3] Audit `_build_instance_block`/`ensure_instance`/`apply_config` and confirm the written `sandbox.local.yml` instance block is complete (ports, server, domain, wp_config, multisite, app_password) so nothing relied on the synthesized `main` runtime defaults (data-model: Instance config).
- [x] T008 [US3] **Live-verify Stage A** (quickstart Stage A): `sb doctor` shows app-password OK for each registered instance; an MCP password-needing tool authenticates per-instance — with `main` still present.

**Checkpoint**: Parity proven on the per-instance model — safe to remove `main`.

---

## Phase 3: User Story 2 — No phantom `main` anywhere (Priority: P1) — Stage B 🎯

**Goal**: Remove the synthesized `main` + `DEFAULT_INSTANCE` from every surface; an unresolved
command errors with guidance instead of a fallback (contract C1/C2).

**Independent Test**: `sb instances`, dashboards, MCP list only registered projects; deleting
any instance needs no special-case; an unregistered cwd errors helpfully.

- [x] T009 [US2] Resolution gate (`sb` ~L7005-7027): drop the `→ DEFAULT_INSTANCE` fallback; no instance resolved + non-PROJECT_ROUTED command → `die()` with guidance; keep PROJECT_ROUTED commands working without an instance (contract C1).
- [x] T010 [US2] `resolve_instances` (`sb` ~L385-466): source from the registry + `sandbox.local.yml` blocks; remove the `if not instances: return {DEFAULT_INSTANCE: …}` (~L459) and `out.setdefault(DEFAULT_INSTANCE, …)` (~L465); update the header comment (~L217-223) — contract C2.
- [x] T011 [P] [US2] Remove the `main` delete-guard in `cmd_instance` delete (`sb` ~L5168-5169).
- [x] T012 [P] [US2] Remove the `main` delete-guard in the TUI dashboard (`sb` ~L5991-5992).
- [x] T013 [P] [US2] Remove the `main` delete-guard in the web handler (`sb` ~L6455-6456).
- [x] T014 [P] [US2] Remove the `main` name reservation in `_derive_instance_name` (`sb` ~L4105).
- [x] T015 [P] [US2] Remove the web-UI `main` delete-guard in `src/web/src/render.ts` (L49) + `src/web/src/instance.ts` (L53); rebuild via `scripts/build-web-js.sh` → `config/sandbox-web.js` (research R8).
- [x] T016 [US2] Remove the legacy migration: delete `migrate_legacy_layout` (`sb` ~L1476-1557), `_legacy_stack_running` (~L1462-1473), the call + comment (~L7033-7036), and the now-moot `runtime/.legacy-migrated` `.gitignore` line.
- [x] T017 [US2] Delete `DEFAULT_INSTANCE` from `sb` (L72) and `mcp/wp-server/server.py` (L46); make `instance` a required parameter on `compose`/`wpcli`/`save_local_app_password`/`_active_project_name` (+ `server.py` `_compose`/`_wpcli`/etc.) per the T003/T006 audit (research R6).
- [x] T018 [US2] Docs-with-code: update `CLAUDE.md` (remove "implicit main"; state the per-project-only model + resolution precedence) and any `docs/*` describing `--instance` defaulting to `main` (constitution V).
- [x] T019 [US2] **Live-verify Stage B** (quickstart Stage B): per-project routing; unregistered-dir error (no `main` boot); `--instance`/`$SANDBOX_INSTANCE` overrides; `sb instances` shows no `main`; CLI delete works without a guard AND the **web + TUI dashboards show delete enabled for every instance** (no `main` guard) — verify the rebuilt bundle here, not deferred to Stage C (U1); `grep -n 'DEFAULT_INSTANCE\|migrate_legacy' sb mcp/wp-server/server.py` zero load-bearing hits (SC-002); `python3 sandbox_core.py --selftest-registry` passes.

**Checkpoint**: `main` is gone everywhere; behavior verified live.

---

## Phase 4: User Story 3 — Feature parity preserved (Priority: P1)

**Goal**: Confirm zero regressions for registered instances across the full surface.

**Independent Test**: Run the command matrix before/after; outputs equivalent modulo the
intended `main`-removal behavior.

- [x] T020 [US3] **Parity matrix** against a running instance (+ one apache instance): `sb status`, `sb wp plugin list`, `sb doctor`, `sb snapshot t1`/`sb snapshots`/`sb restore t1`, `sb domains`/`secure` status — compare to the T001 baseline; record in quickstart notes (SC-003).
- [x] T021 [P] [US3] MCP `ensure_instance` + `wp_cli` + a password-needing tool succeed against a real project; `python3 sandbox_core.py --selftest-registry` passes.

**Checkpoint**: No regressions — old model fully retired with parity proven.

---

## Phase 5: User Story 4 — Modular feature package (Priority: P2) — Stage C

**Goal**: Split `sb` into a `sandbox/` package (10 groups) with a command registry; `sb`
becomes the thin entry (contract C4/C5). Pure refactor — behavior identical.

**Independent Test**: Each feature in its own module; CLI builds from a registry; installed
`sb` (symlink + tarball) runs identically from any directory.

- [x] T022 [US4] Create the package skeleton: `sandbox/__init__.py`, `sandbox/cli.py` (COMMAND registry + argparse build + C1 resolution gate + dispatch), empty `sandbox/core/` + `sandbox/commands/` per plan.md's tree.
- [x] T023 [US4] Move shared infrastructure into `sandbox/core/`: `paths.py`, `ui.py`, `config.py`, `instances.py` (resolve_instances + path helpers + ports), `docker.py`, `domains.py`, `provision.py`, `herd.py` — behavior identical.
- [x] T024 [US4] Move each command group into `sandbox/commands/<group>.py` exposing `register(subparsers)` + `run(cfg, args)` (lifecycle [**incl. `open`** — FR-012], instances_cmd, config_setup, data, wp, net, debug, integ, ui_dash, uninstall); replace the `handlers = {...}` dict with registry self-registration in `cli.py` (contract C4). Verify EVERY command in the old `handlers` dict (39 + `ui` alias) has a module home — none dropped.
- [x] T025 [US4] Reduce `sb` to the thin polyglot entry (bootstrap + `ROOT` + `sys.path.insert` + call `sandbox.cli:main`); no feature logic remains in `sb` (contract C5).
- [x] T026 [P] [US4] Update `package.json` `files` to include `sandbox/`; confirm `bin/sandbox.js` + `scripts/make-release.sh` resolve the package (no change expected).
- [x] T027 [US4] **Live-verify Stage C** (quickstart Stage C): `ast.parse` `sb`; `import sandbox.cli`; full parity matrix from a project dir AND via the global `sb` symlink AND via the npm bin shim (`node bin/sandbox.js status` — C1) to exercise all three install paths (SC-004); release tarball dry-run contains `sandbox/` while `.specify/`+`skills/speckit-*` are pruned; **line-count check**: `sb` ≤ ~200 lines, no `sandbox/` module > ~1500 lines (SC-005); `scripts/build-web-js.sh` then `sb web` lists instances with delete enabled for all.

**Checkpoint**: Modular package shipped; CLI behavior identical via all install paths.

---

## Phase 6: User Story 4 (cont.) — Modularize the MCP server (Priority: P2) — Stage D

**Goal**: Split `mcp/wp-server/server.py` into a thin entry + grouped tool modules reusing
`sandbox/core/*` (FR-011). Identical MCP tool surface — pure refactor on a second process.

**Independent Test**: Every `mcp__sandbox__*` tool behaves identically against a real instance
after the split; `server.py` is a thin entry.

- [x] T028 [US4] Create `mcp/wp-server/tools/` and move the ~21 tools into grouped modules (e.g. `stack.py`, `wp.py`, `db.py`, `fs.py`, `mail.py`, `instances.py`, `context.py`), each registering its tools; reduce `server.py` to a thin entry that imports the groups.
- [x] T029 [US4] Replace `server.py`'s private helpers (config/registry/docker/herd resolution, the duplicated `DEFAULT_INSTANCE`-era code) with imports from `sandbox/core/*` so the CLI and MCP share one implementation; ensure the per-instance app-password path (Stage A) is the shared one.
- [x] T030 [US4] **Live-verify Stage D**: restart Claude Code (gotcha #4) so the re-registered MCP server loads; exercise every tool group against a real instance (`ensure_instance`, `wp_cli`, `wp_rest`, `db_query`, `fs_read`, `tail_log`, `mail_list`, `focus_get`, …); confirm identical behavior and the per-instance app-password resolution; line-count check `server.py` thin + no `tools/` module > ~1500 lines (SC-005).

**Checkpoint**: Both critical processes (CLI + MCP server) modular and sharing `sandbox/core`.

---

## Phase 7: Polish & Cross-Cutting

- [x] T031 [P] Final docs sweep: `CLAUDE.md` folder-layout + MCP-surface sections reflect the `sandbox/` package and `mcp/wp-server/tools/`; `README.md` updated if it documents the file layout or `--instance` default.
- [x] T032 Final acceptance: re-run SC-001..SC-005 (quickstart Acceptance) and record evidence; confirm no `main`/`DEFAULT_INSTANCE` reintroduced in `sb` OR `server.py`.

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 (Stage A, blocking) → Phase 3 (Stage B) → Phase 4 (parity gate) → Phase 5 (Stage C) → Phase 6 (Stage D) → Phase 7 (polish)**.
- Stage A MUST precede Stage B (parity before removal, constitution VI).
- Stage C (CLI refactor) MUST follow Stage B so the package is extracted from already-correct code.
- Stage D (MCP refactor) MUST follow Stage C so `server.py` can import the finished `sandbox/core/*`.
- Within Phase 3: T011-T015 parallel ([P], different files); T009-T010 and T016-T017 touch core `sb` regions and stay sequential.
- **All implementation happens in the main checkout** (`/Users/alim/Sites/git/sandbox`) after the worktree branch is merged to `main` — so edited code runs against the real `runtime/` and the global `sb`/MCP server.

## Parallel Opportunities

- T002 (Setup) standalone.
- T006 (server.py app-pw) ∥ T004/T005 (sb) in Stage A.
- T011-T015 (independent `main`-guard removals) in parallel in Stage B.
- T026 ∥ T025 finalization in Stage C.
- T028 ∥ T029 partly (tool grouping vs core-helper imports) in Stage D.

## Implementation Strategy

- **MVP increment = Stage A + Stage B** (US3 foundation + US2): every command per-project, no
  phantom `main` — without the larger refactor.
- **Stage C (CLI package)** then **Stage D (MCP server)** are separable refactor follow-ons
  (behavior-preserving) landing after the behavior change is proven; Stage D builds on Stage C's
  `sandbox/core/*`. Each reduces risk on a critical shared process.
- Each phase ends with a live-verification checkpoint; do not proceed past a failing one. Stage
  D's checkpoint requires a Claude Code restart (gotcha #4) for the re-registered MCP tools.

---

## Implementation status — 2026-06-21 (branch `impl/per-project-modular`)

All stages implemented and live-verified against the real registry (6 instances):

- **Stage A** (`d412fdc`) — app-password unified per-instance (sb + MCP). ✅
- **Stage B** (`e87a03a`, `b4d2571`) — `main`/`DEFAULT_INSTANCE` removed; resolve→error;
  legacy migration + all `main` guards gone; web bundle rebuilt; docs. ✅
- **Stage C** (`11939c4`, `10b1fea`, `1edac61`) — `sb` is a 60-line thin entry; logic in the
  `sandbox/` package: `core.py` + `commands/<group>.py` (10 feature modules, all <1500 lines)
  + `registry.py` (self-registering COMMANDS, no central dispatch dict). Packaging +
  release-exclusion done. ✅
- **Stage D** (`5fa7e39`) — MCP server split: thin `server.py` + `app.py` + `tools/<group>.py`
  (26 tools + 8 prompts register; parity verified via venv). ✅

Acceptance: SC-001 (no `main`) ✅ · SC-002 (zero legacy refs) ✅ · SC-003 (parity, REST 200)
✅ · SC-004 (runs via `./sb` AND `node bin/sandbox.js`) ✅.

**SC-005 (module line count) — fully met.** The shared core was sub-split from one
~4.8k-line `sandbox/core.py` into the `sandbox/core/` package — 13 thematic submodules
(`_paths`, `_ui`, `_config`, `_docker`, `_domains`, `_provision`, `_herd`, `_instances`,
`_dash`, `_integ`, `_tests`, `_bridge`, `_misc`), each < 1500 lines (largest ~785). The
`core/__init__` back-fills the full shared namespace into every submodule, so cross-module
calls resolve at call time with zero import-cycle risk and `from sandbox.core import *`
keeps working for the command modules unchanged. Verified: all 66 tests + live CLI/MCP.
(One white-box test patches on the owning submodule rather than the package, since back-fill
copies references per submodule.)

**Post-implementation:** the MCP server changes (Stages A/B/D) require a **Claude Code
restart** (gotcha #4) before the live `mcp__sandbox__*` tools run the new code.
