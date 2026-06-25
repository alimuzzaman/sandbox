# Data Model: EB-Aware Attribute-Schema Resolver

**Feature**: 011-eb-attribute-schema · **Phase 1** · 2026-06-25

These are in-memory/response structures produced by the resolver — there is no persistent database
schema. The only stored artifact is the transient cache (D5).

## Entity: AttributeSchemaEntry

One controllable attribute of a block, as returned to the caller.

| Field | Type | Notes |
|-------|------|-------|
| `name` | string (key) | The attribute key the caller sets (e.g. `titleText`, `tagName`, `WRP_BGbackgroundColor`). |
| `type` | string \| null | JS attribute type (`string`, `number`, `boolean`, `object`, `array`). Null when source omits it. |
| `default` | scalar \| null | Default value when the source declares one; null otherwise. |
| `source` | enum: `explicit` \| `generator` | Whether it came from a top-level declaration or a generator expansion. Optional/diagnostic. |
| `generator` | string \| null | The generator that produced it (e.g. `generateTypographyAttributes`) when `source = generator`. Optional/diagnostic. |

**Validation rules**:
- `name` MUST be unique within a block's attribute set; on collision the explicit declaration wins
  over a generator-produced key.
- Type/default are passed through from source as-is; the resolver does not invent values.

## Entity: BlockAttributeSource

The parsed description of one EB block's real attribute surface (intermediate, not always returned).

| Field | Type | Notes |
|-------|------|-------|
| `block_name` | string | e.g. `essential-blocks/advanced-heading`. |
| `attributes_file` | path | Resolved `src/blocks/<name>/src/attributes.js`. |
| `explicit` | AttributeSchemaEntry[] | Top-level declared attributes. |
| `generator_calls` | GeneratorCall[] | The `...generateXxxAttributes(PREFIX, opts?)` spreads found. |

## Entity: GeneratorCall

A single generator spread within a block's `attributes.js`.

| Field | Type | Notes |
|-------|------|-------|
| `generator` | string | e.g. `generateDimensionsAttributes`. |
| `prefix` | string | The resolved prefix constant value (e.g. `WRP_MARGIN`). |
| `resolved` | boolean | True when the generator's key-family was expanded from source; false → fell back / unknown. |
| `nested` | GeneratorCall[] | Generators this one spreads internally (border/shadow → 4× dimensions). |

## Entity: GeneratorKeyFamily

The set of attribute-name suffixes a generator emits for a given prefix.

| Field | Type | Notes |
|-------|------|-------|
| `generator` | string | Helper name. |
| `suffixes` | string[] | Suffix templates (e.g. `Top`, `Right`, `TAB…Top`, `MOB…Top`). |
| `derived_from` | enum: `source` \| `fallback` | Parsed from helper file vs built-in table. |

**Known families (verified ground truth, used as the fallback table)**:
- `generateTypographyAttributes`: 24 keys/prefix (12 desktop + 6 tablet + 6 mobile).
- `generateDimensionsAttributes`: 16 keys/prefix (T/R/B/L/Unit/isLinked × desktop/tab/mob).
- `generateBorderShadowAttributes`: 21 own + 4 nested `generateDimensionsAttributes` (`Bdr_`, `Rds_`,
  `HRds_`, `HBdr_`) = 85 keys/prefix.
- `generateBackgroundAttributes`: 155 keys/prefix.
- `generateResponsiveRangeAttributes`: 7 keys/prefix.
- `generateResponsiveAlignAttributes`: 3 keys/prefix.

## Entity: FidelityReport

Metadata on every EB schema response (D6).

| Field | Type | Notes |
|-------|------|-------|
| `level` | enum: `full` \| `partial` \| `reduced` \| `listing` | full = all generators expanded from source; partial = source found but ≥1 generator fell back/unknown; reduced = no source checkout found (named-block path); listing = a whole-builder listing response (depth, not per-block completeness). |
| `depth` | enum: `shallow` \| `full` | Present only when `level = listing`: whether the listing deep-resolved each block. |
| `count` | integer | Total attributes (named block) or block count (listing). |
| `source_checkout` | path \| null | The checkout the schema was resolved from; null when `reduced`. |
| `unresolved` | string[] | Generators (or blocks) that could not be expanded; empty when `full`. |
| `reason` | string \| null | Human-readable explanation when not `full` (e.g. "no EB source checkout under mounted plugin-home"). |

**Level semantics**: for a named-block resolution the `level` is one of three terminal values —
`full`, `partial`, or `reduced` — computed once per request (there is no in-request transition; the
ordering full ⊐ partial ⊐ reduced only reflects decreasing completeness). `listing` is used solely for
whole-builder responses and never appears on a named-block response.

## Entity: ResolvedSchemaCacheItem

The transient cache record (D5).

| Field | Type | Notes |
|-------|------|-------|
| key | string | `eb_schema_<block>_<fingerprint>`. |
| `fingerprint` | string | Hash of checkout path + mtimes of attributes.js, its constants, and the helper files used. |
| `attributes` | AttributeSchemaEntry[] | The resolved set. |
| `fidelity` | FidelityReport | The report computed at resolution time. |

**Invalidation**: implicit — a changed source mtime changes the fingerprint, changing the key, so a
new resolution runs and the old item simply expires by disuse (plus a normal transient TTL backstop).
