# Data Model: Bundled Schema Catalog

**Feature**: 012-bundled-schema-catalog · **Phase 1** · 2026-06-25

The shipped artifact (committed, gzipped) + the in-memory shapes the generator and `editor-schema`
fallback use.

## Entity: Catalog (the shipped asset)

The committed collection under `sandbox/assets/editor-schema/`, gzipped, with an index.

| Field | Type | Notes |
|-------|------|-------|
| `index` | Index | maps (builder, item, version) → entry file/offset; the lookup table. |
| `entries` | CatalogEntry[] (gz) | one per widget/block, version-keyed. |
| `generated_at` | timestamp | when the catalog was produced (stamped after generation). |
| `tooling_version` | string | the `sb schema-catalog` generator version, for format evolution. |

## Entity: CatalogEntry

One widget's or block's full schema.

| Field | Type | Notes |
|-------|------|-------|
| `builder` | enum: `gutenberg` \| `elementor` | which surface. |
| `name` | string | block name (`essential-blocks/...`, `core/...`) or widget id (`eael-...`). |
| `version` | string | the source plugin's version at generation. |
| `plugin` | string | owning plugin slug (for version comparison on serve). |
| `attributes` | map | block attributes (name→{type,default}) — Gutenberg. |
| `controls` | map | widget controls (id→{type,default}) — Elementor. |
| `dynamic` | bool? | Gutenberg: has a render_callback. |
| `coverage` | enum: `full` \| `partial` | honesty marker from generation. |

**Rules**: an entry is the authoritative full set captured from the runtime registry; never fabricated.
`attributes`/`controls` are mutually exclusive by `builder`.

## Entity: Index

| Field | Type | Notes |
|-------|------|-------|
| `by_item` | map | `{builder}:{name}` → list of `{version, location}` (newest first). |
| `plugins` | map | plugin slug → catalog version(s) present (for `status`). |
| `counts` | map | per-builder entry counts (for `status` + the size check). |

## Entity: FidelityResolution (editor-schema serve-time, not persisted)

How `editor-schema` chooses live vs catalog (D4).

| Field | Type | Notes |
|-------|------|-------|
| `live` | result \| null | the spec-011 / PHP-registry / block.json live result. |
| `catalog` | CatalogEntry \| null | the matching catalog entry, if any. |
| `chosen` | enum: `live` \| `catalog` | live unless live is partial/reduced/absent and catalog is richer. |
| `source` | enum: `live` \| `catalog` | echoed in the response. |
| `version_mismatch` | object \| null | `{catalog_version, installed_version}` when they differ. |

**State**: prefer `live` when `full`; else `catalog` when present and richer; else the live (reduced)
result. Deterministic by completeness (attribute count / fidelity level).

## Entity: GenerationRun (host-side, transient)

| Field | Type | Notes |
|-------|------|-------|
| `instance` | string | the instance generated from (free + Pro active). |
| `gutenberg_dump` | map | from the headless `wp.blocks.getBlockTypes()` page. |
| `elementor_dump` | map | from PHP `get_controls()` over all widgets. |
| `coverage_report` | map | per plugin: present/absent, full/partial, version. |

## Entity: `sb schema-catalog` command surface

| Subcommand | Effect |
|------------|--------|
| `generate [--instance <name>]` | drive the Elementor PHP dump + the headless Gutenberg dump on the instance, pack → committed gzipped catalog + index; print a coverage report. |
| `status` | per-plugin: catalog version vs installed version (drift), entry counts, compressed size. |

## Entity: Headless dump page (in-instance)

`00-sandbox-schema-dump.php` — a finalizer-style admin page that serializes
`wp.blocks.getBlockTypes()` to JSON for the generator to read. Inputs: none. Output: the full Gutenberg
registry (name → attributes/supports/dynamic). Persistence: an option or file the host reads.
