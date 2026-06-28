# Feature Specification: Bundled Schema Catalog for Editor Authoring

**Feature Branch**: `012-bundled-schema-catalog`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "A sandbox-shipped, versioned, compressed schema catalog covering all builders — Elementor/EA/Elementor-Pro widgets (from the live PHP control registry) and Gutenberg core + EB free + EB Pro blocks (from the editor JS block registry) — so every user gets full-fidelity widget/block schemas without mounting source checkouts or regenerating per-machine. editor-schema serves from the bundled catalog as a fallback."

## Clarifications

### Session 2026-06-25

- Q: What is the authoritative source for each builder's schema? → A: Live runtime registries, not plugin source: Elementor/EA/Elementor-Pro from the PHP control registry (`get_controls()`); Gutenberg core + EB free/Pro from the editor JS block registry (`wp.blocks.getBlockTypes()`), dumped headlessly. Source parsing (spec 011) is only an offline fallback.
- Q: Does this remove the need for plugin source code? → A: Yes for schema. Only Elementor Pro lacks source, but its controls come from the PHP registry, so it is fully covered. No source checkout is required for any builder.
- Q: Where does the bundled catalog physically live? → A: A committed, gzipped, version-keyed asset in the repo (under `sandbox/assets/`), so every clone/install has it with no generation step; regeneratable via the `sb` command. Compressed footprint only — no uncompressed multi-MB blob in the tree.
- Q: When editor-schema has both a live result and a catalog entry, which wins? → A: The richer result — prefer the live registry/source result; fall back to the catalog only when live is partial/reduced/unavailable. Deterministic: pick whichever yields the more complete (higher-fidelity / larger) attribute set.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Full schema for every widget/block without source (Priority: P1)

A developer (or agent) on a fresh sandbox install asks for the schema of any widget or block —
including EB **Pro** blocks and Elementor **Pro** widgets — and gets the complete attribute/control
set (names, types, defaults), with no source checkout mounted and nothing regenerated on their
machine. The catalog ships with the sandbox.

**Why this priority**: This is the whole point — full fidelity for everyone, out of the box. It
directly removes spec 011's gaps (EB Pro → reduced, ~21 free blocks → partial) and the per-user
regeneration burden. Without it there is no feature.

**Independent Test**: On an install with no EB/Elementor source checkout mounted, request the schema
for an EB Pro block, an EB free block, and an Elementor Pro widget; confirm each returns its full set
of named attributes/controls with types and defaults, sourced from the bundled catalog.

**Acceptance Scenarios**:

1. **Given** the sandbox is installed with no plugin source checkouts, **When** a caller requests the
   schema for an EB **Pro** block, **Then** the full attribute set (names + types + defaults) is
   returned from the bundled catalog (not 3 generic keys).
2. **Given** the same install, **When** a caller requests an Elementor (or Elementor **Pro**) widget
   schema, **Then** the full control set is returned, identical to what the live PHP registry would
   produce.
3. **Given** the same install, **When** a caller requests any registered Gutenberg core or EB free
   block, **Then** its full attribute set is returned from the catalog.
4. **Given** any catalog entry, **When** it is returned, **Then** it carries a fidelity/coverage
   marker so the consumer knows it is complete (vs a degraded fallback).

---

### User Story 2 - editor-schema falls back to the catalog (Priority: P1)

When the live registry/source path can't fully resolve a widget/block (e.g. the editor JS dump
isn't available in-session, or the source checkout is absent), `editor-schema` transparently serves
the bundled catalog entry instead of returning a reduced/partial result — so callers get full
fidelity through the same ability they already use.

**Why this priority**: The catalog only delivers value if the tool everyone calls actually uses it.
This wires the catalog into `editor-schema` so the fix is automatic, not a separate lookup. Shares
P1 with US1.

**Independent Test**: Force the live resolver to a partial/reduced result (no source checkout), call
`editor-schema` for that block; confirm it returns the full catalog entry and labels the result as
catalog-sourced.

**Acceptance Scenarios**:

1. **Given** the live resolver would return `partial`/`reduced` for an EB block and a bundled catalog
   entry exists, **When** `editor-schema` is called, **Then** it returns the catalog's full entry and
   indicates the result came from the bundled catalog.
2. **Given** both a live full result and a catalog entry exist, **When** `editor-schema` is called,
   **Then** behavior is deterministic and documented (live-preferred or catalog-preferred), and the
   richer/complete result is returned.
3. **Given** no catalog entry and no live source, **When** `editor-schema` is called, **Then** it
   degrades to today's reduced result with an honest marker (no regression of the spec 011 behavior).

---

### User Story 3 - Version-aware, never silently stale (Priority: P2)

The catalog is keyed by plugin version. When a plugin is upgraded, the catalog entry for the old
version no longer matches, and the system either refreshes it or clearly flags the served schema as
generated from a different version — so a stale catalog never masquerades as current.

**Why this priority**: A shipped catalog's main risk is silent drift from the installed build.
Version-keying makes drift visible and refreshable, but it is a safeguard on US1/US2 rather than new
user value, so P2.

**Independent Test**: Serve a catalog entry, then simulate a plugin version bump; confirm the
response either refreshes from the live registry or is flagged as version-mismatched, never returned
as if current.

**Acceptance Scenarios**:

1. **Given** a catalog generated for plugin vX, **When** the installed plugin is vY (Y≠X), **Then**
   the served entry is flagged as version-mismatched (or refreshed), never presented as current.
2. **Given** a maintainer wants to refresh, **When** they run the catalog regeneration step, **Then**
   a new version-keyed catalog is produced covering all builders, replacing the stale one.

---

### User Story 4 - Sampled control-to-value validation (Priority: P3, optional)

A maintainer runs an optional, **sampled** validation pass that drives the real editor: it changes
representative controls on representative widgets/blocks, saves, and diffs the resulting saved block
JSON / Elementor data to confirm which control writes which attribute and to capture example saved
values. This enriches/validates the catalog; it is not how the catalog is primarily built.

**Why this priority**: Useful confidence + control→attribute mapping + example values, but expensive
and inherently sampled (exhaustive coverage of every control of every widget is impractical). It is
explicitly out of scope for v1 delivery and gated behind US1–US3.

**Independent Test**: For a small sample (e.g. one EB block + one Elementor widget), drive the editor,
change a known control, save, and confirm the catalog's predicted attribute key matches the actually
saved key/value.

**Acceptance Scenarios**:

1. **Given** the validation pass runs over a defined sample, **When** a sampled control is changed and
   saved, **Then** the saved attribute key matches the catalog's schema for that control, and any
   mismatch is reported.
2. **Given** the pass is sampled, **When** it completes, **Then** it explicitly reports coverage (what
   was and wasn't validated) — no silent claim of full validation.

---

### Edge Cases

- **No catalog entry + no live source**: degrade to today's reduced result with an honest marker (US2
  scenario 3); never fabricate attributes.
- **Catalog covers a block the install doesn't have registered**: returned only when the block exists
  in the install, or clearly marked as catalog-only.
- **Plugin present but the catalog was generated without it (e.g. a newly installed addon)**: that
  widget/block falls through to the live resolver / reduced path until the catalog is refreshed.
- **Catalog size**: the raw multi-builder dump is large (~16MB uncompressed); shipping it MUST not
  bloat the repo/release materially.
- **Elementor Pro with no source**: still fully covered — controls come from the live PHP registry,
  so the catalog includes them.
- **Partial-coverage builders**: if a builder/source can only be partially captured at generation
  time, the catalog entry records partial coverage rather than claiming full.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a shipped schema catalog covering all supported builders:
  Elementor + Essential Addons + Elementor Pro widgets, and Gutenberg core + Essential Blocks free +
  EB Pro blocks — each entry carrying the full attribute/control set (name/id, type, default).
- **FR-002**: The catalog MUST be generated from the live runtime registries (the PHP control
  registry for Elementor-family widgets; the editor block registry for Gutenberg-family blocks), so
  no plugin source checkout is required to achieve full fidelity — including Elementor Pro and EB Pro.
- **FR-003**: A maintainer MUST be able to regenerate the catalog on demand via a single documented
  step, producing a complete, version-keyed catalog for all builders.
- **FR-004**: `editor-schema` MUST serve from the bundled catalog as a fallback so that, on a fresh
  install with no source checkout, a caller still receives the full schema for any covered
  widget/block; the result MUST indicate it was catalog-sourced.
- **FR-005**: Precedence MUST be deterministic: when the LIVE result is `full`, return it (the catalog
  is not consulted). Otherwise — live is partial/reduced/absent — return whichever of {live, catalog}
  yields the more complete (higher-fidelity / larger) attribute set, preferring the catalog on ties.
  The response MUST indicate which source served it.
- **FR-006**: Every catalog entry MUST carry a coverage/fidelity marker (full vs partial) and its
  source plugin version, so consumers can judge completeness and currency.
- **FR-007**: The catalog MUST be version-aware: a served entry whose plugin version does not match
  the installed plugin MUST be flagged as version-mismatched (or refreshed), never presented as
  current.
- **FR-008**: The change MUST be additive: Elementor and core-block schema results from the live path
  MUST remain unchanged; the catalog only adds a fallback/coverage layer.
- **FR-009**: The shipped catalog MUST be a committed, gzipped, version-keyed asset under
  `sandbox/assets/` — present for every clone/install with no generation step — stored compactly
  (compressed only; no uncompressed multi-MB blob in the working tree) so it does not materially bloat
  the repository or release artifact.
- **FR-010**: The catalog and its fallback MUST require NO per-user regeneration: a user who never
  runs the generator still gets full fidelity for covered widgets/blocks out of the box.
- **FR-011**: The optional sampled control-to-value validation pass (US4) MUST, when run, report its
  coverage explicitly and MUST NOT be required for the catalog to ship or function.

### Key Entities *(include if feature involves data)*

- **Catalog**: The shipped collection of schema entries across all builders, keyed by builder + item
  name + plugin version, with coverage metadata.
- **Catalog entry**: One widget's or block's full schema — its attributes/controls (name/id, type,
  default), a coverage marker (full/partial), and the source plugin version it was generated from.
- **Generation run**: A maintainer-triggered process that reads the live registries for every
  builder and (re)produces the version-keyed catalog.
- **Coverage/fidelity marker**: Per-entry metadata stating full vs partial capture and the plugin
  version of origin; surfaced to consumers and used by `editor-schema` precedence.
- **Validation sample (US4)**: A defined subset of widgets/blocks + controls exercised by the
  optional save-diff validation, with an explicit coverage report.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a fresh install with NO plugin source checkout mounted, `editor-schema` returns the
  full schema (named attributes/controls + types + defaults) for 100% of the Elementor-family widgets
  and ≥95% of the Gutenberg-family blocks **registered in that consumer install**, including EB Pro and
  Elementor Pro.
- **SC-002**: EB Pro blocks that return `reduced` today return their full attribute set via the
  catalog (e.g. the 22 Pro blocks observed in spec 011 go from ~3 keys to their real attribute sets).
- **SC-003**: A user performs zero regeneration steps and still gets full-fidelity schemas — the
  catalog ships ready to use.
- **SC-004**: The shipped catalog artifact is at least 5× smaller than the raw uncompressed dump
  (~16MB → ≤~3MB), and adds no uncompressed multi-MB blob to the repo working tree.
- **SC-005**: Elementor and core-block schema results from the live path are byte-identical before and
  after this feature (no regression).
- **SC-006**: When the installed plugin version differs from the catalog's, the served schema is
  flagged as version-mismatched (or refreshed) in 100% of cases — never silently returned as current.
- **SC-007**: Regenerating the catalog is a single documented command/step and produces a complete
  version-keyed catalog for all builders.

## Assumptions

- The authoritative schema lives in the runtime registries, not plugin source; the spec 011 source
  parser remains only as an additional offline fallback. The sandbox can run the editor block
  registry headlessly (reusing the spec 005 finalizer mechanism) to dump Gutenberg-family schemas
  deterministically, and read the PHP control registry for Elementor-family widgets.
- The catalog ships in the repository/release as a compressed, version-keyed asset and is also
  regeneratable on demand; provisioning uses the bundled asset without requiring the user to generate
  anything.
- Version-keying is per plugin (slug + version). When multiple covered plugins are present, each
  contributes entries keyed by its own version.
- Catalog generation is a maintainer/CI activity; end users consume the shipped result. Generation
  requires the relevant plugins active in a generation instance (free + Pro where licensed) so the
  registries are complete; coverage is recorded honestly when a plugin/Pro is absent at generation.
- US4 (control-to-value save-diff validation) is optional and sampled; a Playwright MCP may be used if
  connected, otherwise the headless editor page / `visit` tool drives it. It is not required for v1.
- Schema content is attribute/control metadata (names, types, defaults), not plugin code; bundling
  this metadata is acceptable and does not vendor third-party source.
- Per the constitution, "done" means full-fidelity catalog-served schemas are demonstrated by live
  `editor-schema` calls on a fresh-style install (no source mounted), captured as evidence.
