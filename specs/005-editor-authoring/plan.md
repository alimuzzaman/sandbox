# Implementation Plan: AI Editor Authoring — Elementor/EA Widgets + Gutenberg/EB Blocks

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-editor-authoring/spec.md`

**Prior art / research**: [research.md](./research.md) (kept from the deep-dive — data
models, comparable projects, WP-Abilities determination, in-house wp-pilot recipes).

## Summary

Two editor-authoring engines, exposed as WP abilities on spec 003's in-instance
Abilities layer, **Gutenberg/EB first** then **Elementor/EA**:

- **Gutenberg/EB**: parse→mutate→serialize for safe cases; a **real-editor finalizer**
  (mu-plugin queue + headless `visit` driving `wp.blocks.serialize()`) for
  static/third-party blocks so they're valid + styled from first save; honor
  `blockId`/`blockMeta`/parent-context.
- **Elementor/EA**: build the element tree server-side (7-hex IDs) and persist via
  `Document::save(['elements'=>$tree])` (admin context) — CSS regen + widget-enable +
  survive-verify + page-template + media `{id,url}`.

Plus live `editor_schema` introspection + `elementor-ea`/`gutenberg-eb` skills. The
existing on-branch `skills/wp-pilot/recipes/` are re-architected into the engines (and
their 8-hex element-ID bug fixed to 7-hex).

## Technical Context

**Language/Version**: PHP (abilities on the 003 mu-plugin layer + the EB finalizer mu-plugin) + Python 3 (`mcp/wp-server/` proxies + `sandbox/`) + browser JS (the finalizer page, driven by `visit`).

**Primary Dependencies**: **spec 003** (in-instance Abilities layer — hard dependency); the `visit` headless-browser tool; `wp_exec`/`wp eval`; Elementor's `Plugin::$instance->documents->get($id)->save()`; WP `parse_blocks`/`serialize_blocks` + `WP_Block_Type_Registry`; EA/EB plugins (WPDeveloper's own); the on-branch `skills/wp-pilot/recipes/`.

**Storage**: Elementor → `_elementor_data` postmeta (+ `_elementor_edit_mode`, `_elementor_version`, `_wp_page_template`); Gutenberg → `post_content` + EB `blockMeta`/`eb-style` CSS; the EB finalizer queue (a CPT/option queue in its mu-plugin); cached schemas under `runtime/schemas/<instance>/`.

**Testing**: live-stack verification (constitution IV) — insert+restyle an EA widget and an EB accordion (parent+child); confirm rendered + editor-valid + styled; finalizer round-trip on a static third-party block.

**Target Platform**: macOS/Linux; Docker + herd (the mu-plugins are host-file based).

**Project Type**: WordPress abilities/mu-plugins + host MCP/CLI + skills.

**Performance Goals**: direct Elementor/EB writes are sub-second; the finalizer path is bounded by a real editor load (seconds) but headless + pollable.

**Constraints**: depends on 003; never hand-serialize static blocks (validity from the block's own JS save); address by id/blockId not position; read-before-write; destructive ops gated; EB `src/controls` submodule needed for full schema introspection.

**Scale/Scope**: 2 engines (EB first), `editor_schema`, the EB finalizer mu-plugin, 2 skills, re-architected wp-pilot recipes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Only** — PASS. Abilities/mu-plugins per-instance; schemas cached per-instance.
- **II. Registry SoT** — PASS. Tools resolve the instance the standard way.
- **III. Single Entry, Modular** — PASS. Engines as abilities (003 layer) + a `tools/editor.py` proxy + the finalizer mu-plugin; `sb` single-entry.
- **IV. Live-Stack Verification** — PASS. quickstart inserts/restyles real widgets/blocks and checks render+editor-validity+styling.
- **V. Idempotency & Docs-With-Code** — PASS. Writers idempotent; CLAUDE.md + 2 skills land with code.
- **VI. Parity Before Removal** — PASS. Additive; wp-pilot recipes retained as the verify/escape-hatch layer.
- **Boundaries / Secrets** — PASS. mu-plugins in writable bind-mounts; EA/EB plugin sources read-only; no secrets.
- **Dependency note**: 005 cannot ship before 003 (the abilities layer it registers on). Sequenced after 003 in implementation.

No violations — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/005-editor-authoring/
├── plan.md
├── research.md          # KEPT — prior-art deep-dive
├── data-model.md        # element node, block, block spec, schema, finalization job
├── quickstart.md        # live insert/modify/finalizer verification
├── contracts/
│   └── abilities.md     # elementor_* / gutenberg_* / editor_schema ability contracts
└── tasks.md
```

### Source Code (repository root)

```text
# Gutenberg/EB first, then Elementor/EA — both registered as abilities on the 003 layer
wp-content/mu-plugins/sandbox-abilities/   # 003 layer — editor abilities register here
wp-content/mu-plugins/00-sandbox-eb-finalizer.php   # EB finalizer queue + admin page (visit-driven)
mcp/wp-server/tools/editor.py              # proxies + editor_schema (introspect registries)
runtime/schemas/<instance>/{elementor,gutenberg}.json   # cached schemas
skills/elementor-ea/SKILL.md · skills/gutenberg-eb/SKILL.md
skills/wp-pilot/recipes/*.js                # re-architected into the engines (8-hex→7-hex fix)
```

**Structure Decision**: Engines ride 003's abilities layer; the EB finalizer is a
dedicated mu-plugin driven by `visit`; schema introspection + proxies are host-side.

## Complexity Tracking

| Item | Why Needed | Simpler Alternative Rejected Because |
|------|------------|-------------------------------------|
| EB real-editor finalizer (browser) | Static/third-party blocks are only valid + styled when serialized by the block's own JS `save()` | Server-side hand-serialization produces invalid/unstyled blocks (the exact problem); dynamic blocks alone don't cover EB's static blocks |

## Phase 0 — Research

[research.md](./research.md) (kept): Elementor/EA + EB data models; comparable
projects (msrbuilds/elementor-mcp, block-mcp); WP-Abilities determination; Angie
contract-layer lessons; in-house wp-pilot recipes + gotchas.

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): element node, block, block spec, schema, finalization job.
- [contracts/abilities.md](./contracts/abilities.md): the editor ability contracts.
- [quickstart.md](./quickstart.md): live insert/modify/finalizer verification.
- Agent context: SPECKIT block points at this plan.

## Phase 2 — Tasks

Generated by `/speckit-tasks`.
