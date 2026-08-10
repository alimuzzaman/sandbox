---
description: "Task list for spec 010 — unified slug-keyed plugin config map"
---

# Tasks: Unified Slug-Keyed Plugin Config Map

**Input**: Design documents from `specs/010-plugin-config-map/` (plan.md, research.md,
data-model.md, contracts/, quickstart.md)

**Tests**: Unit tests ARE requested for the two pure functions (`_normalize_plugins`,
`_merge_plugin_maps`) — they are pure dict→dict and high-leverage (plan §Testing,
quickstart §6). Every user story also ends with a **live-stack verification** task
(constitution IV).

**Organization**: by user story. MVP = US1 (per-slug merge, no clobber) + the
provisioning rewire it needs. Priorities: US1/US2/US4 = P1, US3 = P2.

## Path Conventions

Config loader: `sandbox_core.py`. Provisioning: `sandbox/core/_provision.py`,
`sandbox/core/_docker.py`. Unit tests: `tests/test_sandbox.py`. Docs: repo root + `docs/`.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Inventory the seam: in `sandbox_core.py` note `DEFAULTS`, `_deep_merge`,
  `_merge_layers`, `load_project_config`; in `sandbox/core/_provision.py` note the current
  `_wire_project_plugins` legacy behavior (the parity reference) and the `_write_*_muplugin`
  pattern + where they're invoked (cmd_up/install/apply). Record the exact current behavior
  of `plugins`/`mappings`/`mappings_inactive` as the parity contract (data-model.md C3/D4).
- [x] T002 Add a test module section in `tests/test_sandbox.py` for the new pure functions
  (no code yet — just the TestCase scaffolding + fixtures: a project doc, an override doc, a
  user-global doc).

## Phase 2: Foundational (BLOCKING — the normalize + merge core every story needs)

**⚠️ No user story works until the canonical entry, normalization (incl. legacy fold-in),
and per-field merge exist and feed `load_project_config`.**

- [x] T003 Define the canonical entry + UNSET sentinel in `sandbox_core.py`: a small
  representation `{slug, source, active, onDemand}` where unspecified fields are an explicit
  UNSET marker (per data-model.md). Add value-shorthand parsing helpers (bool / string-path /
  string-zip / object) → entry, with malformed-entry errors naming the slug (FR-012).
- [x] T004 Implement `_normalize_plugins(doc)` in `sandbox_core.py`: raw doc → `{slug: entry}`.
  Handle the **object** (canonical) and **array** (legacy) forms of `plugins`, AND fold in
  legacy `mappings` (→ `{path, active:true}`) and `mappings_inactive` (→ `{path,
  active:false}`) per data-model.md C3, leaving non-plugin mappings untouched. Emit exactly
  ONE deprecation hint when any legacy key is present (FR-008/009). Same slug in a legacy key
  AND the map → map wins + one-line warning (FR-012).
- [x] T005 Implement `_merge_plugin_maps(layers)` in `sandbox_core.py`: per-FIELD merge across
  layers with precedence project > override > user-global; UNSET never clobbers a set value
  (FR-004/004a). NEVER whole-value replace.
- [x] T006 Wire into `load_project_config()` (`sandbox_core.py`): resolve `plugins` via
  `_normalize_plugins` per layer + `_merge_plugin_maps`, SEPARATELY from the generic
  `_deep_merge`/`_merge_layers` used for other keys; then apply resolved defaults LAST to
  still-UNSET fields (`source→org`; state→on-demand, FR-004b/004c). Keep `DEFAULTS["plugins"]`
  as `["."]` so a config-less project still installs its own dir.
- [x] T007 [P] Unit tests in `tests/test_sandbox.py` for T003–T006: shorthands; UNSET
  non-clobber; precedence; project-state + catalog-path → both kept (SC-007); catalog-only
  path defaults on-demand (SC-008); legacy fold-in equals current behavior (SC-001/005);
  malformed/conflict cases.

**Checkpoint**: `python -m unittest tests.test_sandbox` green for the new functions; the
loader returns a canonical `plugins` map for both config shapes.

## Phase 3: User Story 1 — Per-slug merge, nothing dropped (P1) 🎯 MVP

**Goal**: declared plugins survive machine overrides; one declaration per plugin.
**Independent test**: quickstart §1.

- [x] T008 [US1] Rewrite `_wire_project_plugins` in `sandbox/core/_provision.py` to consume
  the canonical `{slug: entry}` map: for each entry resolve source (org→`plugin install`;
  zip→install; path→symlink) and state (active→activate; inactive→install-no-activate;
  on-demand→skip here, handled in US3). Preserve idempotency (don't reinstall present
  plugins; reactivate as today).
- [x] T009 [US1] Live verification (quickstart §1): an override that re-sources ONE slug
  keeps all other declared plugins (SC-002); project `true` + user-global catalog `path`
  resolves to `{active:true, source:path}` with the org fallback NOT applied (SC-007).

**Checkpoint**: US1 shippable — config merges per-slug and provisions without clobbering.

## Phase 4: User Story 2 — Correct slug on worktrees (P1)

**Goal**: local-sourced plugins install under the map-key slug regardless of dir name.
**Independent test**: quickstart §2.

- [x] T010 [US2] In the `_wire_project_plugins` path-source branch
  (`sandbox/core/_provision.py`), symlink the local source under the **slug given by the map
  key**, NOT the source directory's name (FR-005). (Legacy array `"."`/path entries keep the
  dir-name slug for compat — T004.)
- [x] T011 [US2] Live verification (quickstart §2): from a worktree dir whose name ≠ slug,
  a `{ "<real-slug>": "." }` entry installs + activates under `<real-slug>` (SC-003).

## Phase 5: User Story 4 — Legacy keys unchanged (P1)

**Goal**: existing repos + the user-global Pro set provision identically.
**Independent test**: quickstart §4–§5.

- [x] T012 [US4] Verify/finish parity in `_normalize_plugins` (T004) against the captured
  current behavior (T001): legacy `plugins` list, `mappings` (symlink+activate),
  `mappings_inactive` (eager symlink, inactive) all reproduce today's wiring exactly.
- [x] T013 [US4] Handle the missing-local-source and malformed-entry cases in
  `sandbox/core/_provision.py` / `sandbox_core.py`: a missing local path → skip + warning, no
  silent org install (FR-011); surface the map-wins + deprecation warnings once.
- [x] T014 [US4] Live verification (quickstart §4–§5): a legacy-only config (incl. the
  user-global `mappings_inactive` Pro set) boots with the same active/inactive set as before
  (SC-005), emits one deprecation hint (SC-006); conflict → map wins + warning; malformed →
  error naming the slug.

## Phase 6: User Story 3 — On-demand local sourcing + admin UI (P2)

**Goal**: on-demand plugins absent on fresh boot; served from local on any install path;
listed + installable in wp-admin. **Independent test**: quickstart §3.

- [x] T015 [US3] Implement `_write_local_sources` in `sandbox/core/_provision.py`: write a
  per-instance local-source map (`slug → {path|zip}`) into the WP tree from the canonical
  entries (on-demand AND locally-sourced slugs). Call it from the mu-plugin provisioning hook
  (alongside the other `_write_*` writers; idempotent) — wire in `sandbox/core/_docker.py` /
  the up/apply path.
- [x] T016 [US3] In `_wire_project_plugins`, route `onDemand` entries: do NOT install;
  ensure their source is in the local-source map (T015).
- [x] T017 [US3] Extend `_write_dl_cache_muplugin` (`sandbox/core/_provision.py`): in the
  `upgrader_pre_download` hook, if the package resolves to a slug in the local-source map,
  serve the LOCAL copy (zip a local dir / use the zip) so WP installs local with no download
  — covering FSI, wp-cli, wp-admin (FR-007 / contract C5). Missing local source → clear
  error, no registry fallback.
- [x] T018 [US3] Add `_write_ondemand_muplugin` (`sandbox/core/_provision.py`): a wp-admin
  page listing on-demand plugins (from the local-source map) with state + a one-click
  "Install from local" action (`manage_options` + nonce; sandbox-only guard) → installs via
  the C5 path, offers activate (FR-013 / contract C6). Written by a provisioning hook.
- [x] T019 [US3] Live verification (quickstart §3): on-demand plugin absent on fresh boot
  (SC-004); `wp plugin install <slug>` serves local (no download) and version matches the
  local checkout; the admin page lists it and one-click install-from-local works; a
  catalog-only path in a non-declaring project stays absent (SC-008).

## Phase 7: Polish & Docs-With-Code (land WITH the code, constitution V)

- [x] T020 [P] Update `docs/sandbox-config-reference.md`: the canonical `plugins` map
  (slug-keyed, shorthands, object form), the source-catalog semantics, the merge contract
  (normalize-then-field-merge + precedence), the legacy-sugar mapping + deprecation timeline,
  and the on-demand/admin-UI behavior.
- [x] T021 [P] Update `CLAUDE.md`: the `sandbox.config.json` key table (plugins now a
  slug-keyed map; mappings/mappings_inactive marked deprecated-sugar) + the per-plugin
  example configs; cross-reference the doc.
- [x] T022 [P] Update the example override file(s)' guidance to use the map form
  (`sandbox.config.override.example.json` pattern referenced in repos) — in docs only;
  actual plugin-repo example files are out of this repo's tree (note in the reference).
- [x] T023 Equivalence check (SC-001): demonstrate the canonical map expresses every legacy
  case (active local, active org, inactive local, on-demand local, org/zip) — captured as a
  unit-test table in `tests/test_sandbox.py` and referenced in quickstart §6.

## Dependencies & Order

- **Setup (T001-T002)** → **Foundational (T003-T007, BLOCKING)** → user stories.
- **US1 (T008-T009)** is the MVP; the rewire it adds is reused by US2/US4/US3.
- **US2 (T010-T011)** extends the US1 rewire (path→slug-key); do after T008.
- **US4 (T012-T014)** depends on the normalize/legacy fold-in (T004) + the rewire (T008).
- **US3 (T015-T019)** depends on the canonical entries + rewire; T015→T016→T017→T018→T019.
- **Polish (T020-T023)** [P] — distinct files; land with the code they document.

## Parallel opportunities

- T007 (unit tests) parallels T003-T006 authoring once signatures are fixed.
- T020/T021/T022 are `[P]` (distinct doc files).
- US3's mu-plugin tasks (T017 dl-cache, T018 on-demand UI) touch the same file
  (`_provision.py`) → sequential, not `[P]`.

## MVP scope

**Foundational + US1 (T001-T009)** is the first shippable increment: the slug-keyed map
merges per-slug with zero clobbering and provisions correctly. US2 (worktree), US4 (legacy
parity), then US3 (on-demand + admin UI) layer on top.

## Phase 8: Convergence

- [x] T024 Validate canonical plugin map keys and strict object schemas before a slug can
  become a filesystem destination, per FR-001/FR-012 (partial).
- [x] T025 Reconcile declared active-to-inactive/on-demand transitions and keep missing
  on-demand local sources registered for fail-closed interception, per FR-006/FR-007/FR-011
  (partial).
