---
description: "Task list — Per-Project-First Instance Model & Modular sb"
---

# Tasks: Per-Project-First Instance Model & Modular `sb`

**Input**: Design documents from `specs/001-per-project-modular/`

**Prerequisites**: plan.md ✅, spec.md ✅ (constitution at `.specify/memory/constitution.md`)

**Tests**: No unit/contract test tasks — constitution Principle IV mandates **live-stack
verification** (CLI/MCP against a running instance) as the proof of done. Each phase ends
with explicit live-verification tasks instead.

**Organization**: Tasks are grouped by user story. Stage labels (A/B/C) from plan.md map to
the stories: Stage A → US3 foundation (parity), Stage B → US2 (remove `main`), Stage C →
US4 (modular). US1 (cwd routing) is already delivered (commits `dc0b276`, `37509c7`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1..US4 from spec.md
- All paths are repo-root-relative within the worktree.

## Path Conventions

Single CLI project. Key files: `sb`, `sandbox_core.py`, `mcp/wp-server/server.py`,
`src/web/src/*.ts`, `config/sandbox-web.js`, `package.json`, `scripts/make-release.sh`.
Target package (Stage C): `sandbox/core/*`, `sandbox/commands/*`, `sandbox/cli.py`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Baseline + keep spec-kit out of the shipped product before any refactor.

- [ ] T001 Capture a baseline live-smoke transcript (pre-change) from a registered instance: `sb status`, `sb wp plugin list`, `sb doctor`, `sb snapshots` against `templately-fsi-rewrite` (running) — save under `specs/001-per-project-modular/research.md` as the parity baseline.
- [ ] T002 [P] Exclude spec-kit dev tooling from the shipped product: add `!.specify/**` and `!skills/speckit-*/**` (or equivalent negations) to `package.json` `files`, and add `.specify` + `skills/speckit-*` to the prune list in `scripts/make-release.sh` (FR-009).
- [ ] T003 [P] Distill the three exploration reports (command map, core-helpers map, packaging map) into `specs/001-per-project-modular/research.md` as the Phase 0 record referenced by plan.md.

**Checkpoint**: Baseline recorded; spec-kit will not ship.

---

## Phase 2: Foundational — Stage A (Feature parity before removal) ⚠️ BLOCKING

**Purpose**: Make every feature work on the per-instance model so removing `main` loses
nothing (constitution Principle VI). MUST complete before Phase 3.

- [ ] T004 [US3] Audit every call site of `compose`, `wpcli`, `save_local_app_password`, `_active_project_name` in `sb` and confirm each passes an explicit `instance` (record any relying on the `DEFAULT_INSTANCE` default — these must be fixed before Stage B deletes the default).
- [ ] T005 [US3] Unify app-password WRITE on the per-instance key: in `sb` `save_local_app_password` (~L2028) always write `instances.<name>.app_password`; remove the `instance == DEFAULT_INSTANCE` → `mcp.wp.application_password` branch (~L2043).
- [ ] T006 [US3] Unify app-password READ in `sb` `cmd_doctor` (~L2161-2166) to read `instances.<name>.app_password` for every instance (drop the `main` legacy-key branch).
- [ ] T007 [P] [US3] Unify app-password READ in `mcp/wp-server/server.py` `_resolve_instance` (~L191-198) to the per-instance key for every instance (drop the `DEFAULT_INSTANCE` legacy branch).
- [ ] T008 [US3] Audit `_build_instance_block` / `ensure_instance` / `apply_config` and confirm the written `sandbox.local.yml` instance block is complete (ports, server, domain, wp_config, multisite, app_password) so nothing depended on the synthesized `main` runtime defaults.
- [ ] T009 [US3] **Live-verify Stage A**: for each registered instance run `sb doctor` and confirm `application_password set` is OK; run one MCP tool that needs the password (e.g. `wp_rest`) against a running project and confirm it authenticates. App-password now resolves per-instance with `main` still present.

**Checkpoint**: Parity proven on the per-instance model — safe to remove `main`.

---

## Phase 3: User Story 2 — No phantom `main` anywhere (Priority: P1) — Stage B 🎯

**Goal**: Remove the synthesized `main` and `DEFAULT_INSTANCE` from every surface; an
unresolved command errors with guidance instead of targeting a fallback.

**Independent Test**: `sb instances`, dashboards, and MCP list only registered projects;
deleting any instance needs no special-case; an unregistered cwd errors helpfully.

- [ ] T010 [US2] In the resolution gate (`sb` ~L7005-7027) drop the `→ DEFAULT_INSTANCE` fallback: when no instance resolves and the command is not PROJECT_ROUTED, `die()` with guidance ("cd into a registered project or run `sb init`/`sb ensure`"). Keep PROJECT_ROUTED commands working without an instance.
- [ ] T011 [US2] In `resolve_instances` (`sb` ~L385-466) source instances from the registry + their `sandbox.local.yml` blocks and remove the `main` synthesis: delete the `if not instances: return {DEFAULT_INSTANCE: …}` (~L459) and the `out.setdefault(DEFAULT_INSTANCE, …)` (~L465); update the header comment (~L217-223).
- [ ] T012 [P] [US2] Remove the `main` delete-guard in `cmd_instance` delete (`sb` ~L5168-5169).
- [ ] T013 [P] [US2] Remove the `main` delete-guard in the TUI dashboard (`sb` ~L5991-5992).
- [ ] T014 [P] [US2] Remove the `main` delete-guard in the web handler (`sb` ~L6455-6456).
- [ ] T015 [P] [US2] Remove the `main` name reservation in `_derive_instance_name` (`sb` ~L4105).
- [ ] T016 [P] [US2] Remove the web-UI `main` delete-guard in `src/web/src/render.ts` (L49) and `src/web/src/instance.ts` (L53); rebuild the bundle via `scripts/build-web-js.sh` → `config/sandbox-web.js`.
- [ ] T017 [US2] Remove the legacy migration: delete `migrate_legacy_layout` (`sb` ~L1476-1557), `_legacy_stack_running` (~L1462-1473), the call + comment (~L7033-7036), and the `runtime/.legacy-migrated` `.gitignore` line (now moot).
- [ ] T018 [US2] Delete `DEFAULT_INSTANCE` from `sb` (L72) and `mcp/wp-server/server.py` (L46); make `instance` a required parameter on `compose`/`wpcli`/`save_local_app_password`/`_active_project_name` (+ `server.py` `_compose`/`_wpcli`/etc.) per the T004/T007 audit.
- [ ] T019 [US2] Update docs in the same change: `CLAUDE.md` (remove `main` mentions / "implicit main"; state the per-project-only model) and any `docs/*` that describe `--instance` defaulting to `main`.
- [ ] T020 [US2] **Live-verify Stage B**: from a registered dir, `sb status`/`wp`/`doctor` target that instance; from the sandbox (unregistered) dir, a non-project-routed command errors with guidance (no `main` boot); `--instance X` and `$SANDBOX_INSTANCE` still override; `sb instances` shows no `main`; delete works without a guard; `grep -n 'DEFAULT_INSTANCE\|migrate_legacy' sb mcp/wp-server/server.py` and a `"main"` audit return zero load-bearing hits (SC-002).

**Checkpoint**: `main` is gone everywhere; behavior verified live.

---

## Phase 4: User Story 3 — Feature parity preserved (Priority: P1)

**Goal**: Confirm zero regressions for registered instances across the full surface.

**Independent Test**: Run the command matrix before/after; outputs equivalent modulo the
intended `main`-removal behavior.

- [ ] T021 [US3] **Parity matrix** against `templately-fsi-rewrite` (and one apache instance): `sb status`, `sb wp plugin list`, `sb doctor`, `sb snapshot t1` + `sb snapshots` + `sb restore t1`, `sb domains`/`secure` status — compare to the T001 baseline; record results in research.md.
- [ ] T022 [P] [US3] `python3 sandbox_core.py --selftest-registry` passes; MCP `ensure_instance` + `wp_cli` + a password-needing tool succeed against a real project.

**Checkpoint**: No regressions — old model fully retired with parity proven.

---

## Phase 5: User Story 4 — Modular feature package (Priority: P2) — Stage C

**Goal**: Split `sb` into a `sandbox/` package (10 groups) with a command registry; `sb`
becomes the thin entry. Pure refactor — behavior identical.

**Independent Test**: Each feature lives in its own module; the CLI builds from a registry;
the installed `sb` (symlink + tarball) runs identically from any directory.

- [ ] T023 [US4] Create the package skeleton: `sandbox/__init__.py`, `sandbox/cli.py` (COMMAND registry + argparse build + resolution gate + dispatch), and empty `sandbox/core/` + `sandbox/commands/` modules per plan.md's tree.
- [ ] T024 [US4] Move shared infrastructure into `sandbox/core/`: `paths.py` (ROOT + constants), `ui.py` (info/ok/die/run), `config.py` (load_config/deep_merge/expand/_local_yaml), `instances.py` (resolve_instances + path helpers + ports), `docker.py` (compose/wpcli/render_compose/_web_*), `domains.py` (PROXY_*/caddy/mkcert/valet/site_url), `provision.py` (plugins/themes/mu-plugins/multisite/install), `herd.py` (_herd_*). Keep behavior identical.
- [ ] T025 [US4] Move each command group into its own `sandbox/commands/<group>.py` exposing `register(subparsers)` + `run(cfg, args)`: lifecycle, instances_cmd, config_setup, data, wp, net, debug, integ, ui_dash, uninstall (groups per plan.md). Replace the `handlers = {...}` dict with registry self-registration in `cli.py`.
- [ ] T026 [US4] Reduce `sb` to the thin polyglot entry: keep the shell→python bootstrap + `ROOT` + `sys.path.insert(0, ROOT)`, then call `sandbox.cli:main`. No feature logic remains in `sb`.
- [ ] T027 [P] [US4] Update `package.json` `files` to include `sandbox/`; confirm `bin/sandbox.js` and `scripts/make-release.sh` resolve the package (no change expected since they ship sibling files).
- [ ] T028 [US4] **Live-verify Stage C**: `python3 -c "import ast; ast.parse(open('sb').read())"`; import-smoke `python3 -c "import sandbox.cli"`; run the full T021 matrix again from a project dir AND via the global `sb` symlink; build the release tarball (dry-run) and confirm `sandbox/` is in it while `.specify/`+`skills/speckit-*` are pruned; `scripts/build-web-js.sh` then `sb web` lists instances with delete enabled for all.

**Checkpoint**: Modular package shipped; CLI behavior identical via all install paths.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T029 [P] Final docs sweep: `CLAUDE.md` folder-layout section reflects the `sandbox/` package; `README.md` updated if it documents the file layout or `--instance` default.
- [ ] T030 Final acceptance: re-run SC-001..SC-005 checks and record evidence in research.md; confirm no `main`/`DEFAULT_INSTANCE` regressions reintroduced.

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → **Phase 2 (Stage A, blocking)** → **Phase 3 (Stage B)** → **Phase 4 (parity gate)** → **Phase 5 (Stage C)** → **Phase 6 (polish)**.
- Stage A MUST precede Stage B (parity before removal, constitution VI).
- Stage C (refactor) MUST follow Stage B so the package is extracted from already-correct code (avoids moving then mutating).
- Within Phase 3, T012-T016 are parallel ([P], different files); T010-T011 and T017-T018 touch core `sb` regions and should be sequential.

## Parallel Opportunities

- T002 + T003 (Setup) in parallel.
- T007 (server.py) parallel with T005/T006 (sb) in Stage A.
- T012-T016 (independent `main`-guard removals) in parallel in Stage B.
- T027 parallel with T026 finalization in Stage C.

## Implementation Strategy

- **MVP increment = Stage A + Stage B** (US3 foundation + US2): delivers the headline value
  — every command is per-project, no phantom `main` — without the larger refactor.
- **Stage C (US4)** is a separable follow-on (pure refactor) that can land after the
  behavior change is proven, reducing risk on critical shared tooling.
- Each phase ends with a live-verification checkpoint; do not proceed past a failing
  checkpoint.
