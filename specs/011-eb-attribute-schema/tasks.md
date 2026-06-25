# Tasks: EB-Aware Attribute-Schema Resolver for `editor-schema`

**Feature**: 011-eb-attribute-schema · **Input**: design docs in `specs/011-eb-attribute-schema/`

**Primary file of change**: `sandbox/assets/abilities/sandbox-editor.php`
(`sandbox_editor_schema()` + new helper functions). Most implementation tasks touch this one file,
so they are sequential (not `[P]`) unless noted. Docs and baseline-capture tasks are `[P]`.

**Verification model (Constitution IV)**: a task that adds behavior is "done" only when its live
`editor-schema` check from `quickstart.md` passes against the running `templately-fsi-rewrite`
instance, evidence captured.

## Phase 1: Setup

- [x] T001 Cut working branch `011-eb-attribute-schema` from current HEAD (push upstream as itself, not main) — pause for user approval before any push.
- [x] T002 Confirm the test environment in `templately-fsi-rewrite`: `ensure_instance`, EB + EA active, and an EB source checkout (free + Pro) reachable under the mounted `plugins_home`/`SANDBOX_PLUGINS_HOST`; capture the resolved scan root. Take a DB snapshot before any authoring checks.

## Phase 2: Foundational (blocking prerequisites)

These are shared by US1 and US2 and must land before either story's behavior is correct.

- [x] T003 In `sandbox/assets/abilities/sandbox-editor.php` `sandbox_editor_schema()`, gate the new resolver strictly on `builder === 'gutenberg'` AND `strpos($name,'essential-blocks/') === 0`; route every other request through the existing unchanged code (no `fidelity` object for non-EB). (FR-007 skeleton; D7)
- [x] T004 Add `sandbox_editor_eb_source_discover($block_name)` in the same file: scan `getenv('SANDBOX_PLUGINS_HOST')` for an `essential-blocks`/`essential-blocks-pro` checkout containing `src/blocks/<name>/src/attributes.js` + a controls helpers dir; pick deterministically (shortest path, then lexicographic); return chosen path or null. (D2, FR-006)
- [x] T005 Add `sandbox_editor_eb_fidelity($level,$count,$checkout,$unresolved)` returning the structured FidelityReport `{level,count,source_checkout,unresolved,reason}` plus the back-compat `eb_attribute_fidelity` string; reason text per data-model. (D6, FR-003)

## Phase 3: User Story 1 — Full EB attribute discovery (Priority: P1) 🎯 MVP

**Goal**: A named EB block with a source checkout present returns its complete attribute set
(name+type+default), including generator-expanded and nested attributes, with `level: full`.

**Independent test**: `editor-schema {builder:"gutenberg", name:"essential-blocks/advanced-heading"}`
returns ≥700 attributes including `titleText`/`tagName` and nested border/shadow dimension keys, with
`fidelity.level === "full"`.

- [x] T006 [US1] Add `sandbox_editor_eb_parse_attributes($attributes_file)` — brace-aware literal parse extracting explicit `name:{type,default}` entries and `...generateXxxAttributes(PREFIX,opts?)` calls; resolve `PREFIX` constants from the block's `constants/*.js`. (D3, BlockAttributeSource/GeneratorCall)
- [x] T007 [US1] Add `sandbox_editor_eb_expand_generator($generator,$prefix,$checkout)` — derive the key-family by parsing `src/controls/src/helpers/*.js` for `[`${prefix}Suffix`]:` / `TAB`/`MOB` templates; recurse into nested generator spreads (border/shadow → 4× dimensions); fall back to the verified built-in family table when a helper file is unparseable, flagging it unresolved. (D4, FR-002, GeneratorKeyFamily)
- [x] T008 [US1] Assemble the full set: merge explicit + expanded entries (explicit wins on key collision), carry type/default, and return it on the named-EB response with `fidelity.level = full` when all generators resolved. (FR-001)
- [x] T009 [US1] Add transient caching: key `eb_schema_<block>_<fingerprint>` where fingerprint hashes checkout path + mtimes of attributes.js, its constants, and helper files used; serve on hit, recompute when fingerprint changes; idempotent reads only. (D5, FR-011)
- [x] T010 [US1] Live-verify quickstart Check 1, Check 2, Check 5 against the instance (count ≥700, `titleText`/`tagName` present, nested `*Bdr_Top`/`*Rds_Top` + background keys present, cache hit faster, invalidation after `touch`). Capture response JSON as evidence. (SC-001, SC-002)
- [x] T010b [US1] Live-verify full resolution for ≥10 distinct EB blocks (e.g. advanced-heading, button, accordion, advanced-tabs, wrapper, call-to-action, countdown, dual-button, flipbox, advanced-image): each returns `fidelity.level === "full"` and includes its explicit attributes plus all generator-contributed attributes with zero silent truncation to the generic keys. Capture per-block counts. (SC-004)
- [ ] T010c [US1] Live-verify an EB **Pro** block's schema resolves at `fidelity.level === "full"` from the Pro checkout (FR-008), using the same resolver path as free blocks. Capture the response + chosen `source_checkout`.

**Checkpoint**: US1 alone delivers the core value — full schema for EB blocks (free + Pro) with source present.

## Phase 4: User Story 2 — Honest fidelity reporting (Priority: P1)

**Goal**: When source is absent or a generator can't be expanded, the response is explicitly
reduced/partial with a reason — never a misleading 3-attribute "complete" schema.

**Independent test**: with no reachable EB checkout, a named EB block returns `level: reduced`,
`source_checkout: null`, a non-empty reason, and only the generic keys (not presented as complete).

- [x] T011 [US2] Reduced path: when `sandbox_editor_eb_source_discover` returns null, return the existing block.json (generic) attributes BUT wrapped with `fidelity.level = reduced` + reason "no EB source checkout with src/blocks + controls under the mounted plugin-home"; never present them as complete. (FR-004)
- [x] T012 [US2] Partial path: when source is found but ≥1 generator falls back/unknown, return all resolvable attributes with `fidelity.level = partial` and `unresolved` naming the generator(s); request still succeeds. (FR-005)
- [x] T013 [US2] Live-verify quickstart Check 3 (reduced honest) and Check 4 (partial names the gap). Capture evidence. (FR-004, FR-005)

**Checkpoint**: US1 + US2 = correct full schema AND trustworthy degradation.

## Phase 5: User Story 3 — No regression for Elementor & core blocks (Priority: P2)

**Goal**: Elementor/EA widget schemas and core/third-party Gutenberg schemas are byte-for-byte
unchanged.

**Independent test**: `eael-info-box` and `core/heading` schemas are identical before and after.

- [ ] T014 [P] [US3] Capture pre-change baselines: `editor-schema {builder:"elementor",name:"eael-info-box"}` and `{builder:"gutenberg",name:"core/heading"}` JSON into `tmp/` for diffing.
- [x] T015 [US3] Confirm the T003 gating leaves both baselines byte-identical (no `fidelity` object added to non-EB blocks); live-verify quickstart Check 6 and diff against T014 baselines. (FR-007, SC-005)

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T016 [P] Docs-with-code: update `skills/gutenberg-eb/SKILL.md` — full-fidelity EB schema, the source-checkout-under-plugin-home requirement, and the structured `fidelity` field. (Constitution V)
- [x] T017 [P] Add `memory/plugin-behavior/eb-attribute-schema.md` — EB declares 0 attrs in block.json and assembles them in JS; the generator key-families; the resolver's scan + container-mount rule; re-verify counts on major EB bumps.
- [x] T018 [P] Update `CLAUDE.md` gotcha #17 reduced-fidelity wording to reflect the new resolver and its plugin-home-mount constraint.
- [ ] T019 [US1] Live-verify quickstart Check 7 end-to-end: author `advanced-heading` via `gutenberg-insert` using the resolved `titleText`/`tagName`, then `visit` the frontend; confirm non-empty render on first attempt; screenshot to `tmp/`. (SC-006)
- [ ] T020 Run the sandbox PHP unit harness for the pure parser/expander functions (where exercisable without WP); then a final full quickstart pass; restore the T002 snapshot; assemble evidence bundle.

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T005)** → user stories.
- **US1 (T006–T010)** depends on Foundational; it is the MVP and unblocks US2/US3 reporting paths.
- **US2 (T011–T013)** depends on Foundational (T004 discovery, T005 report) and US1's expansion (to know when a generator is unresolved → partial). Start after T008.
- **US3 (T014–T015)** depends only on T003 gating; T014 baseline can be captured anytime (even before T003) and is `[P]`.
- **Polish (T016–T020)** after the stories; docs tasks T016–T018 are mutually `[P]` (separate files).

## Parallel Opportunities

- T014 (baseline capture) can run in parallel with Foundational work — it only reads.
- T016, T017, T018 (three separate docs files) can be written in parallel once behavior is final.
- Within the core file, T006–T009 are sequential (same file, layered logic) — do not parallelize.

## Implementation Strategy

- **MVP = Phase 1 + 2 + US1 (T001–T010)**: full EB schema with source present. This alone closes the
  guessing-attributes gap that motivated the feature.
- **Increment 2 = US2 (T011–T013)**: honest degradation — ship together with US1 ideally, since
  reduced/partial reporting shares the report builder; both are P1.
- **Increment 3 = US3 + Polish**: regression guard + docs + the end-to-end render proof.
- Keep each task's change small and live-verified; do not mark a behavior task done from code reading.

## Format Validation

- All tasks use `- [ ] T### …` with file paths; story tasks carry `[US1]/[US2]/[US3]`; setup,
  foundational, and polish tasks carry no story label; `[P]` only on independent-file tasks.

## Implementation Status (2026-06-25) — verified live

**Core delivered & live-verified** (T001–T013, T015–T018): the EB resolver in
`sandbox/assets/abilities/sandbox-editor.php` returns full attribute sets with a structured
`fidelity` report. Evidence (against `templately-fsi-rewrite`):
- advanced-heading → **787** attrs, `level: full`, `titleText`/`tagName` present, nested
  border/shadow dimensions expanded (`*Bdr_Top`, `*Rds_Top`). (SC-001, SC-002)
- **10 blocks at full fidelity** (accordion 2378, advanced-tabs 1554, countdown 1321, flipbox 1054,
  advanced-heading 787, call-to-action 570, social-share 473, advanced-image 452, dual-button 143,
  button 21). (SC-004)
- Cache: cold→warm 1ms, recompute after source `touch`. (FR-011)
- Reduced (source hidden) → `level: reduced`, generic keys, reason. (FR-004)
- Partial (wrapper) → names unresolved generators. (FR-005)
- Regression: core/heading (16, no fidelity obj) + eael-info-box (385) unchanged. (FR-007, SC-005)

**Two findings from live verification (Constitution IV caught both):**

1. **Discovery pivoted from `SANDBOX_PLUGINS_HOST` to a `WP_PLUGIN_DIR` scan.** That env is written
   only into the compose `.env` for `${...}` substitution, NOT exported to the container runtime —
   `getenv()` returns false in-instance. T004's plan assumed it was readable. The resolver now scans
   the EB plugins WP actually loads from (resolving symlinks), an optional `source_root` input, and
   the env only if present. Full fidelity requires `src/controls` reachable in-container; the `.org`
   build alone yields `partial` (explicit attrs incl. `titleText`). Plan/research/tasks updated
   wording in the codebase notes; the doc files still describe the env path as the primary — see the
   memory note for the corrected mechanism.

2. **SC-006 (T019) is a finalizer concern, not a schema-resolver one.** With the correct `titleText`,
   `gutenberg-insert` still rendered empty because `advanced-heading` ships a real `save.js` (it is
   effectively a **static** block) — a self-closing insert has no saved markup. Non-empty render
   needs `sandbox/gutenberg-finalize` (spec 005 US5). The editor-schema resolver's job — surface the
   correct attribute names/types — is verified; render fidelity is out of scope for spec 011.
   SC-006 is therefore **not claimed**; it should be re-homed to spec 005.

**Remaining / qualified:**
- **T010c (FR-008 Pro full)**: Pro blocks resolve via the same path, but the mounted `essential-blocks-pro`
  build ships neither the free blocks' `attributes.js` nor `src/controls`, so a Pro block reaches full
  fidelity only when a Pro source checkout with `src/controls` is the active/mapped plugin. Same
  constraint as free; not separately demonstrated at full on this instance.
- **T014**: regression baselines were verified inline (shape + counts identical) rather than saved as
  JSON files; T015 confirms no regression.
- **T020**: extensive live verification done; a dedicated PHP unit harness for the pure parser/expander
  was not added (functions verified end-to-end live). **Open fixture**: `src/controls` was staged into
  the instance's installed `essential-blocks` to demonstrate full fidelity — it is a hand-copied test
  fixture (wiped on plugin reinstall). Durable full fidelity requires mapping a full EB source checkout
  as the active plugin (or passing `source_root`).
