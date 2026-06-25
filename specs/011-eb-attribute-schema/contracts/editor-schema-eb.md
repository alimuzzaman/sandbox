# Contract: `editor-schema` — Essential Blocks responses

**Feature**: 011-eb-attribute-schema · **Phase 1** · 2026-06-25

Ability: `sandbox/editor-schema` (in-instance; reached via `wp_eval_live` /
`/wp-json/sandbox/mcp`). This contract covers ONLY the Gutenberg + Essential Blocks path. Elementor
and core/third-party Gutenberg responses are unchanged by this feature.

## Request

```jsonc
{
  "builder": "gutenberg",
  "name": "essential-blocks/advanced-heading",  // omit for a whole-builder listing
  "eb_only": true,                               // listing filter (optional)
  "full": false                                  // optional: force deep per-block resolution in a listing
}
```

## Response — named EB block, full fidelity (source checkout present)

```jsonc
{
  "builder": "gutenberg",
  "name": "essential-blocks/advanced-heading",
  "dynamic": true,
  "attributes": {
    "titleText":  { "type": "string", "default": "Essential Blocks Advanced Heading" },
    "tagName":    { "type": "string", "default": "h2" },
    "TITLEFontSize": { "type": "number", "default": null },
    "WRP_BGbackgroundColor": { "type": "string", "default": null }
    // … hundreds more (≈787 for advanced-heading)
  },
  "fidelity": {
    "level": "full",
    "count": 787,
    "source_checkout": "/Users/alim/Sites/git/essential-blocks",
    "unresolved": [],
    "reason": null
  },
  // back-compat: the legacy string key is retained, derived from fidelity.level
  "eb_attribute_fidelity": "full"
}
```

### Guarantees (named block, full)
- `attributes` includes every explicitly-declared attribute AND every generator-expanded attribute,
  including nested generators (border/shadow → dimensions). (FR-001, FR-002)
- Each entry carries `type`; `default` is present when the source declares one. (FR-001)
- `fidelity.level === "full"`, `fidelity.count === count(attributes)`, `source_checkout` non-null.
  (FR-003)
- `titleText` and `tagName` are present. (SC-002)
- `count >= 700` for `advanced-heading`. (SC-001)

## Response — named EB block, partial fidelity (source found, a generator unresolved)

```jsonc
{
  "builder": "gutenberg",
  "name": "essential-blocks/some-block",
  "dynamic": true,
  "attributes": { /* explicit + all resolvable generators */ },
  "fidelity": {
    "level": "partial",
    "count": 142,
    "source_checkout": "/…/essential-blocks",
    "unresolved": ["generateNewFancyAttributes"],
    "reason": "1 generator could not be expanded from source; counts may be incomplete"
  },
  "eb_attribute_fidelity": "partial"
}
```

### Guarantees (partial)
- The request still succeeds with all resolvable attributes. (FR-005)
- `unresolved` names what could not be expanded; `level === "partial"`. (FR-005)

## Response — named EB block, reduced fidelity (no source checkout reachable)

```jsonc
{
  "builder": "gutenberg",
  "name": "essential-blocks/advanced-heading",
  "dynamic": true,
  "attributes": {
    "lock": { "type": "object", "default": null },
    "metadata": { "type": "object", "default": null },
    "className": { "type": "string", "default": null }
  },
  "fidelity": {
    "level": "reduced",
    "count": 3,
    "source_checkout": null,
    "unresolved": [],
    "reason": "no EB source checkout with src/blocks + controls found under the mounted plugin-home"
  },
  "eb_attribute_fidelity": "reduced"
}
```

### Guarantees (reduced)
- Matches today's behavior (block.json attributes only) BUT is explicitly flagged reduced with a
  reason; it MUST NOT present the 3 generic keys as if complete. (FR-004)

## Response — whole-builder listing (`eb_only`, no `name`)

```jsonc
{
  "builder": "gutenberg",
  "count": 82,
  "fidelity": { "level": "listing", "depth": "shallow", "count": 82, "source_checkout": "/…/essential-blocks" },
  "blocks": {
    "essential-blocks/advanced-heading": { "dynamic": true, "attributes": ["lock","metadata","className"] }
    // … shallow by default (block.json keys only) to stay fast
  }
}
```

### Guarantees (listing)
- Listing stays at today's cost — shallow per-block keys — unless `full: true` is passed. (FR-010)
- The listing `fidelity` uses a distinct `level: "listing"` + `depth` field so it never collides
  with the per-block `reduced` meaning ("no source checkout"); it describes listing depth, not
  per-block completeness.

## Invariants across all EB responses
- Every EB response carries a `fidelity` object with `level` and `count`. (FR-003)
- No EB response returns only the generic wrapper keys without `level === "reduced"` + a `reason`.
  (FR-004)
- Reads are idempotent and never mutate plugin source or instance state. (FR-009)

## Non-EB responses (unchanged — regression guard)
- `builder: "elementor"`, `name: "eael-info-box"` → `{ controls: {…} }` identical to pre-feature.
- `builder: "gutenberg"`, `name: "core/heading"` → block.json attributes (16) identical to
  pre-feature; no `fidelity` object added for non-EB blocks. (FR-007, SC-005)
