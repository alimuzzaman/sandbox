---
description: "Task list for AI Editor Authoring — Elementor/EA + Gutenberg/EB"
---

# Tasks: AI Editor Authoring — Elementor/EA Widgets + Gutenberg/EB Blocks

**Input**: Design documents from `specs/005-editor-authoring/` (incl. research.md)

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md.
**Hard dependency**: spec 003 (the in-instance Abilities layer) must ship first.

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task. Order: **Gutenberg/EB first, then Elementor/EA.**

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Ensure EB attribute sources are complete: if the instance's EB plugin is a source checkout, init the `src/controls` submodule; **don't hardcode a personal path** — resolve from the instance's plugin source. When only the built plugin (no `src/controls`) is present, `editor_schema` falls back to `block.json` attributes and flags reduced fidelity (analysis U3).
- [ ] T002 Add a `tools/editor.py` MCP module + `runtime/schemas/<instance>/` cache dir; confirm the spec-003 abilities layer is the registration target.

## Phase 2: Foundational (blocking prerequisites)

- [ ] T003 Implement `editor_schema(builder, name?)` (Elementor `widgets_manager` controls; WP `WP_Block_Type_Registry` attributes) with per-instance caching.
- [ ] T004 Implement the shared "read-before-write + address-by-id/blockId" helper with **base-state conflict rejection** (compare a content hash; reject silent overwrite on concurrent edits — both direct-write and finalizer paths, analysis U2) + the all-raw-HTML / deprecated-item guards used by both engines.
- [ ] T004b Add `sandbox/<kebab>` ability naming + inline per-ability capability checks (Application Password + `permission_callback`; destructive/readonly annotations) as the shared registration helper used by T005/T012/T015 — caps are inline, not a trailing task (analysis I1, C1).

## Phase 3: User Story 3 — Insert an EB block (P1) [Gutenberg first]

**Goal**: insert an EB block that renders, is valid, uniquely identified.
**Independent test**: insert an EB block → renders, editor shows no recovery prompt.

- [ ] T005 [US3] Implement `gutenberg_get` (`parse_blocks`) + `gutenberg_insert`/`update`/`delete` (parse→mutate→serialize; dynamic-vs-static classification; unique `blockId`; parent/child `parentBlockId`+`inherited*`) as abilities on the 003 layer.
- [ ] T006 [US3] Live verification (quickstart §2 insert half): EB block renders; editor valid; unique blockId; nested child linked.

## Phase 4: User Story 4 — EB blocks are correctly styled (P1)

**Goal**: inserted EB blocks carry their per-block CSS (`blockMeta`).
**Independent test**: inserted styled block shows styling on the frontend.

- [ ] T007 [US4] Ensure `blockMeta` is populated for inserted blocks; document that the finalizer path produces it naturally and direct static writes must precompute it (EB assembles it lazily into `uploads/eb-style/`).
- [ ] T008 [US4] Live verification (quickstart §2 restyle): styled block renders styled; `gutenberg_update` reflects setting changes.

## Phase 5: User Story 5 — Real-editor finalizer (P1)

**Goal**: static/third-party blocks valid + styled from first save, headless.
**Independent test**: queue a spec → finalizer writes valid content, no human step.

- [ ] T009 [US5] Implement the EB finalizer mu-plugin: a queue (CPT/option) of attribute-level block specs + a finalizer admin page that real `wp.blocks` JS serializes/validates; base-content-hash concurrency. Written by the same **idempotent `_write_*_muplugin` provisioning hook** (cmd_up/install/apply) as the other mu-plugins (constitution V, analysis I3).
- [ ] T010 [US5] Drive the finalizer headlessly via `visit`; expose a completion marker the agent polls; route static/third-party `gutenberg_insert` specs to it.
- [ ] T011 [US5] Live verification (quickstart §3): static third-party block round-trips to valid + styled content headlessly.

## Phase 6: User Story 1 — Insert an EA widget (P1) [Elementor]

**Goal**: insert an EA widget that renders, is styled, editor-valid.
**Independent test**: insert an EA widget → renders styled, editor opens clean.

- [ ] T012 [US1] Implement `elementor_get` (`get_elements_data`) + `elementor_insert`/`delete` as abilities: build node(s) with **7-hex** IDs; persist via `Document::save(['elements'=>$tree])` as `--user=admin`; enable required EA widget first + verify node survived; regenerate CSS; set `_wp_page_template`; fill media `{id,url}`; raw-meta fallback (`wp_slash` + `_elementor_css` delete).
- [ ] T013 [US1] Re-architect the on-branch `skills/wp-pilot/recipes/{elementor-page,figma-to-page}.js` logic into this engine; **fix the 8-hex→7-hex element-ID bug**; keep the recipes as the `visit`-driven verify/escape-hatch layer.
- [ ] T014 [US1] Live verification (quickstart §4 insert): EA widget renders styled + editor-valid; disabled widget enabled+verified; full-width via `elementor_canvas`.

## Phase 7: User Story 2 — Modify a widget's settings (P1)

**Goal**: change a widget setting without corrupting the page.
**Independent test**: update a setting by id → change renders, page intact.

- [ ] T015 [US2] Implement `elementor_update(post_id, element_id, settings)` (locate by id; merge `settings[control_id]`; re-save; CSS regen) handling complex controls (responsive/typography/media/repeater).
- [ ] T016 [US2] Live verification (quickstart §4 restyle): setting change persists + re-renders; other elements untouched.

## Phase 8: User Story 6 — Discover widgets/blocks (P2)

**Goal**: list/inspect available EA widgets + EB blocks.
**Independent test**: `editor_schema` returns accurate names/attributes.

- [ ] T017 [US6] Live verification (quickstart §1): per-item + full-list schema for both builders is accurate.

## Phase 9: Polish & Cross-Cutting

Per constitution V (docs-with-code), the skill/CLAUDE.md updates below land **with the
phase whose code they document** (EB skill with the EB engine, EA skill with the EA
engine), not deferred — listed here only for completeness. Per-op capability checks are
handled inline via T004b, not here (analysis C1).

- [ ] T018 [P] Contract-layer extras (from Angie research): tool annotations (readOnly/destructive + `confirmationMessage`) + read-before-write MCP resources (`elementor://`, `eb://`). (Caps themselves are inline — T004b.)
- [ ] T019 `skills/gutenberg-eb/SKILL.md` (with the EB engine phases) + `skills/elementor-ea/SKILL.md` (with the EA engine phases) — gotchas: canvas template, `{id,url}`, blockMeta, parent context.
- [ ] T020 [P] Docs-with-code: CLAUDE.md editor-authoring loop (lifts the "hand-authored PHP only works for core blocks" limitation) + MCP table; reference docs/plugin-catalog.md for EA/EB slugs.

## Dependencies & Order

- **Spec 003 first** (abilities layer). Then Setup (T001-T002) → Foundational (T003-T004).
- Gutenberg/EB block: US3 (T005-T006) → US4 (T007-T008) → US5 finalizer (T009-T011).
- Then Elementor/EA: US1 (T012-T014) → US2 (T015-T016).
- US6 schema (T017) verifies T003. Polish last. `[P]` tasks touch distinct files.

## MVP scope

EB insert + styling + finalizer (T001-T011) is the first shippable increment (biggest
gap, our own blocks); Elementor/EA (T012-T016) is the second.
