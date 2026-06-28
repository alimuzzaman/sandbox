# Tasks: Bundled Schema Catalog for Editor Authoring

**Feature**: 012-bundled-schema-catalog · **Input**: design docs in `specs/012-bundled-schema-catalog/`

**Files of change**: `sandbox/commands/schema_catalog.py` (new `sb schema-catalog`),
`sandbox/core/_schema_catalog.py` (pack/index/precedence/generation orchestration),
`sandbox/assets/abilities/00-sandbox-schema-dump.php` (headless GB registry dump),
`sandbox/assets/abilities/sandbox-editor.php` (editor-schema catalog fallback),
`sandbox/assets/editor-schema/` (the committed gzipped catalog), `sandbox/core/_provision.py`
(provision catalog into instances), `sandbox/cli.py` + `sandbox/registry.py` (wire the command), docs.

**Verification model (Constitution IV)**: a behavior task is "done" only when its live quickstart check
passes (editor-schema serving catalog schemas on a no-source instance; precedence; no regression),
evidence captured.

## Phase 1: Setup

- [x] T001 Cut working branch `012-bundled-schema-catalog` from current HEAD (push upstream as itself; pause for user approval before any push).
- [ ] T002 Confirm two instances: a **generation** instance with EB free + Pro and Elementor + Pro/EA active and a **consumer** instance running the `.org` builds with NO source checkout. Snapshot before changes. Pro activation is reachable via spec 013 (keyless WPDeveloper + Elementor sharing — already merged to `main`); if EL Pro isn't connected yet, fall back to activating it manually on the generation instance so the registries are complete.

## Phase 2: Foundational (blocking prerequisites)

The dump page, catalog format/packer, and provisioning are shared by all stories.

- [x] T003 Add `sandbox/assets/abilities/00-sandbox-schema-dump.php`: a finalizer-style admin page (modeled on `00-sandbox-eb-finalizer.php`) that boots `wp.blocks` (registerCoreBlocks + EB editor assets), serializes `wp.blocks.getBlockTypes()` → name→{attributes,supports,dynamic} JSON, and persists it to a **file** under the instance's gitignored runtime (NOT a wp_option — the ~16MB payload risks DB/option-size limits) for the host to read. Handles the WP 6.9 traps the finalizer solves. (D2, data-model: Headless dump page)
- [x] T004 Add `sandbox/core/_schema_catalog.py`: the catalog format + gzip pack/unpack + version-keyed index (`by_item`, `plugins`, `counts`), and helpers to read/lookup an entry by (builder, name) with the installed version. (D3/D6, data-model: Catalog/Index)
- [x] T005 Wire catalog provisioning: in `sandbox/core/_provision.py`, write the relevant committed catalog files into each instance's abilities dir on up/apply (same mechanism as the editor assets) so the in-instance ability can read them. Idempotent; no-op when no catalog asset is present. (D5)

## Phase 3: User Story 1 — Full schema for every widget/block without source (Priority: P1) 🎯 MVP

**Goal**: On a no-source instance, `editor-schema` returns the full set for any widget/block — incl EB
Pro + Elementor Pro — served from the bundled catalog.

**Independent test**: on the consumer instance, request an EB Pro block, an EB free block, and an
Elementor Pro widget → each returns its full attribute/control set with `source: "catalog"` where live
was reduced.

- [x] T006 [US1] Implement `sb schema-catalog generate --instance <gen>` in `schema_catalog.py` + `_schema_catalog.py`: drive the Elementor PHP `get_controls()` dump (reuse the introspect/editor-schema path) and the headless Gutenberg dump page; collect both; pack → committed gzipped catalog + index under `sandbox/assets/editor-schema/`. Each packed entry MUST record its `coverage` (full/partial) + source plugin `version` (FR-006). Print a coverage report. (FR-001/002/003/006, contracts/sb-schema-catalog-cli)
- [x] T007 [US1] Generate the catalog from the generation instance (EB Pro + Elementor Pro active) and commit the gzipped asset; confirm it includes EB Pro blocks (e.g. pro-business-hours) + Elementor Pro widgets at full coverage. (FR-001)
- [x] T008 [US1] Add the `editor-schema` catalog fallback in `sandbox/assets/abilities/sandbox-editor.php`: after the live result, when live is partial/reduced/absent and a catalog entry exists, return the catalog entry; tag `source` (live|catalog) + catalog/installed version; read the provisioned catalog (gunzip one entry). (FR-004, D4, contracts/editor-schema-catalog)
- [x] T009 [US1] Live-verify quickstart Check 3 on the consumer instance: EB Pro / EB free / Elementor Pro all return full schemas from the catalog (EB Pro ~3 keys → full); ≥95% GB blocks + 100% EL widgets full. Capture evidence. (SC-001, SC-002)

**Checkpoint**: US1 delivers the core value — full schemas everywhere, no source, no per-user regen.

## Phase 4: User Story 2 — editor-schema falls back to the catalog (Priority: P1)

**Goal**: The fallback is wired into the tool everyone calls, with deterministic precedence (live
preferred, catalog fills gaps) and an honest source marker.

**Independent test**: force live to partial/reduced (no source) → catalog served; live full → catalog
not consulted.

- [x] T010 [US2] Implement deterministic precedence in the fallback (T008): prefer live when `full`; use catalog only when live is partial/reduced/absent AND the catalog entry is richer (by count/fidelity); always set `source`. (FR-005, data-model: FidelityResolution)
- [x] T011 [US2] Live-verify quickstart Check 4: a `full` live result returns `source: "live"` (catalog not consulted); a partial/reduced live with a richer catalog returns `source: "catalog"`. Capture evidence. (FR-005)

**Checkpoint**: US1 + US2 = full schemas served automatically through `editor-schema`, live-preferred.

## Phase 5: User Story 3 — Version-aware, never silently stale (Priority: P2)

**Goal**: Catalog entries are version-keyed; a version mismatch with the installed build is flagged,
never presented as current.

**Independent test**: serve a catalog entry whose version differs from the installed plugin → flagged.

- [x] T012 [US3] In the fallback, compare the served entry's plugin version with the installed plugin version; when they differ, serve (better than reduced) but set `version_mismatch` with both versions. (FR-007, D6)
- [x] T013 [US3] Add `sb schema-catalog status`: per-plugin catalog-vs-installed version (drift), per-builder counts, and committed compressed size vs the ~3MB bound. (FR-007, contracts/sb-schema-catalog-cli)
- [x] T014 [US3] Live-verify quickstart Check 6 (bump a plugin version → `version_mismatch` flagged) and Check 2 (`status` size ≤~3MB, ≥5× smaller than raw). (SC-004, SC-006)

## Phase 6: User Story 4 — Sampled control-to-value validation (Priority: P3, optional)

**Goal**: An optional, sampled pass that drives the editor, changes representative controls, saves,
and diffs the saved JSON / `_elementor_data` to confirm control→attribute mapping. Out of v1 scope;
reports coverage explicitly.

**Independent test**: for a small sample, change a known control, save, confirm the catalog's predicted
key matches the saved key.

- [ ] T015 [P] [US4] Add an optional `sb schema-catalog validate --sample <n>` that drives the editor (headless page / visit / Playwright if connected) over a defined sample, saves, diffs the result vs the catalog's predicted keys, and reports coverage (what was/ wasn't validated). NOT required for the catalog to ship. (FR-011, US4)

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T016 [US2] Live-verify quickstart Check 5 (no regression): installed Elementor widget (`eael-info-box`) + core block (`core/heading`) are byte-identical to pre-feature — NO `source` marker on those live results. Diff against pre-feature baselines. (FR-008, SC-005)
- [x] T017 [US1] Live-verify quickstart Check 7 — narrowly the **zero-regeneration** claim: a brand-new consumer instance that has run NO `sb schema-catalog generate` still serves Pro schemas from the committed catalog out of the box (distinct from T009's full-coverage check). (SC-003)
- [x] T018 [P] Docs-with-code: update `skills/gutenberg-eb/SKILL.md` + `skills/elementor-ea/SKILL.md` (editor-schema catalog fallback + the `source`/version markers) and `CLAUDE.md` (a gotcha on the bundled catalog + how to regenerate). (Constitution V)
- [x] T019 [P] Add `memory/plugin-behavior/schema-catalog.md` — runtime-registry-as-truth, the headless GB `wp.blocks` dump vs server-side block.json, version-keyed committed catalog, regen with Pro active. (Constitution V)
- [x] T020 Wire `sb schema-catalog` into `sandbox/registry.py` + `sandbox/cli.py` (registry-wide, like `cache`/`license`); restore the T002 snapshots; assemble the evidence bundle.

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T005)** → stories.
- **US1 (T006–T009)** depends on Foundational (dump page, packer, provisioning); it is the MVP.
- **US2 (T010–T011)** extends US1's fallback (T008) with precedence; sequence after T008.
- **US3 (T012–T014)** depends on the fallback (version-aware) + the catalog (status).
- **US4 (T015)** optional, after the catalog exists; `[P]` (separate command path).
- **Polish (T016–T020)** after the stories; T018/T019 are `[P]` (separate docs files).

## Parallel Opportunities

- T018 + T019 (separate docs files) run in parallel once behavior is final.
- T015 (optional validate) is independent of the polish docs.
- The ability-file edits (T008/T010/T012 in `sandbox-editor.php`) are sequential (same file).

## Implementation Strategy

- **MVP = Phase 1 + 2 + US1 (T001–T009)**: generate the catalog + serve full schemas (incl Pro) on a
  no-source instance via `editor-schema`. This is the whole point.
- **Increment 2 = US2 (T010–T011)**: deterministic live-preferred precedence + source marker.
- **Increment 3 = US3 + Polish**: version-awareness, `status`, no-regression + zero-regen proofs, docs.
- **US4** optional/last (sampled validation).
- Live-verify each behavior; never mark done from code reading.

## Format Validation

- All tasks use `- [ ] T### …` with file paths; story tasks carry `[US1]/[US2]/[US3]/[US4]`; setup,
  foundational, polish carry no story label; `[P]` only on independent-file tasks.
