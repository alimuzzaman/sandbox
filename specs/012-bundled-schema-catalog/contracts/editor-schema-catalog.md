# Contract: `editor-schema` — catalog fallback

**Feature**: 012-bundled-schema-catalog · **Phase 1** · 2026-06-25

Additive to the existing `sandbox/editor-schema` ability. Live result is preferred; the catalog fills
gaps. Every response gains a `source` marker.

## Request (unchanged)
```jsonc
{ "builder": "gutenberg", "name": "essential-blocks/pro-business-hours" }
```

## Response — live full (catalog not consulted)
```jsonc
{
  "builder": "gutenberg", "name": "essential-blocks/advanced-heading",
  "attributes": { /* full, from the live resolver */ },
  "fidelity": { "level": "full", "count": 787 },
  "source": "live"
}
```

## Response — live partial/reduced, catalog richer → catalog served
```jsonc
{
  "builder": "gutenberg", "name": "essential-blocks/pro-business-hours",
  "attributes": { /* full, from the bundled catalog */ },
  "fidelity": { "level": "full", "count": 2563 },
  "source": "catalog",
  "catalog": { "version": "2.9.3", "installed_version": "2.9.3" }
}
```

### Guarantees
- On a fresh install with NO source checkout, an EB Pro / Elementor Pro item returns its FULL set from
  the catalog (not 3 keys). (FR-004, SC-001/SC-002)
- Live is preferred when `full`; the catalog is used only when live is partial/reduced/absent and the
  catalog entry is richer (deterministic by count/fidelity). (FR-005)
- `source` is always present (`live`|`catalog`); a catalog hit echoes the catalog + installed versions.

## Response — version mismatch (still served, flagged)
```jsonc
{
  "source": "catalog",
  "catalog": { "version": "2.9.3", "installed_version": "3.0.0" },
  "version_mismatch": true
}
```
- A catalog/installed version difference MUST be flagged, never silently presented as current. (FR-007)

## Response — no live, no catalog (honest reduced)
```jsonc
{ "fidelity": { "level": "reduced" }, "source": "live", "reason": "..." }
```

## Invariants (regression guard)
- A LIVE response for an installed Elementor widget or core Gutenberg block is **byte-identical** to
  pre-feature — NO `source` marker is added to those (preserves SC-005). The `source`/`catalog`
  markers appear ONLY on (a) the EB named-block path (which already carries a `fidelity` object) and
  (b) any response actually served from the catalog (i.e. where there was no full live result to
  preserve). So the catalog fallback never alters an existing full live result. (FR-008, SC-005)
- The catalog read is in-instance (provisioned per D5), gunzip of one entry; reads only, idempotent.
