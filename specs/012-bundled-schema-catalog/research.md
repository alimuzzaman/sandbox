# Research: Bundled Schema Catalog

**Feature**: 012-bundled-schema-catalog · **Phase 0** · 2026-06-25

Decisions grounded in the spec-011 fidelity findings, the spec-005 headless-editor mechanism, the
existing `sb introspect`, and the live Playwright probe (380 blocks incl EB Pro full).

## D1 — Authoritative source per builder (NOT plugin source)

**Decision**: Generate from the live runtime registries.
- **Elementor / EA / Elementor Pro** → PHP `$widget->get_controls()` (eager, complete). Reuse the
  `editor-schema` Elementor path / `sb introspect widgets` (`wp eval-file`).
- **Gutenberg core / EB free / EB Pro** → the editor JS registry `wp.blocks.getBlockTypes()`.

**Rationale**: Verified — server-side PHP only sees block.json for EB (3 keys), while the editor JS
registry has the full set (advanced-heading: 1693), incl EB Pro (24 Pro blocks, avg ~1500 attrs). The
PHP registry is complete for Elementor. So neither source is universal; each builder has its own.

**Alternatives rejected**: source-parsing (spec 011) — fragile, reduced/partial, needs checkouts;
block.json-only (`sb introspect blocks`) — reduced for EB.

## D2 — Headless Gutenberg dump (the one PHP can't do)

**Decision**: A finalizer-style admin page (`00-sandbox-schema-dump.php`, modeled on the spec-005
`00-sandbox-eb-finalizer.php`) that, on load, runs `wp.blocks.getBlockTypes()` (after
`registerCoreBlocks()` + EB editor assets), serializes name→{attributes,supports,dynamic} to JSON, and
persists it (option/file) for the host to read. The generator drives it headlessly (the spec-005
autologin + visit/Playwright pattern) and collects the JSON.

**Rationale**: This is the proven mechanism (the Playwright probe got all 380 blocks this way). A
dedicated dump page is CI-friendly and deterministic — no per-call browser scripting; the page does the
serialization, the host just triggers + reads. Handles the WP 6.9 traps the finalizer already solves
(core-block registration, asset/script ordering, EB quick-setup redirect).

**Alternatives rejected**: live `visit` eval (can't return arbitrary JS values); requiring a Playwright
MCP at generation time (heavier dependency than a dump page).

## D3 — Storage: committed, gzipped, version-keyed asset (clarified)

**Decision**: Commit the catalog under `sandbox/assets/editor-schema/`, gzipped, keyed by
builder + item + plugin version (e.g. `gutenberg/essential-blocks-pro@2.9.3.json.gz`, or a single
`catalog.json.gz` whose entries carry `{builder,name,version}` + an `index.json`). Compressed only.

**Rationale**: Clarified — every clone/install has it with no generation step; regeneratable via the
command. Gzip gets the ~16MB raw dump well under the ~3MB bound (SC-004). Version-keying makes a plugin
upgrade naturally miss → flagged/refreshed (FR-007).

**Alternatives rejected**: release-tarball-only (no catalog on a from-git dev run); per-user runtime
generation (defeats "no per-user regeneration").

## D4 — Serving: editor-schema fallback, richer wins (clarified)

**Decision**: `editor-schema` computes its live result first (spec-011 resolver for EB / PHP registry
for Elementor / block.json for core). If the live result is `full`, return it. If it is
`partial`/`reduced`/absent AND a catalog entry exists for this item, return the catalog entry. Tag every
response with `source` (`live` | `catalog`) and, for catalog, the catalog's plugin version. Determinism:
when both exist, pick the larger/higher-fidelity set.

**Rationale**: Clarified (live preferred, catalog fallback). Live is always current to the installed
build; the catalog fills the Pro/no-source gaps. The `source` marker keeps it honest.

**Alternatives rejected**: catalog-always-wins (can mask a newer installed build); live-only
(Pro/partial stay degraded even when the catalog has them).

## D5 — In-instance access to the catalog

**Decision**: Provision the catalog into each instance (write the gz + index into the instance's
abilities/mu-plugin dir on `up`/`apply`, same mechanism as the editor assets), so the in-container
`editor-schema` ability can read + gunzip the relevant entry. Only the needed builder files need land.

**Rationale**: `editor-schema` is in-instance PHP; it can only read what's in the container (proven in
spec 011 re: mounts). Copying the committed asset in at provision is the reliable path.

**Alternatives rejected**: a host-side resolver (editor-schema isn't a host tool); mounting the repo
(fragile, broad).

## D6 — Version-awareness

**Decision**: Each entry stores the plugin version it was generated from. On serve, compare against the
installed plugin version; if they differ, still serve (better than reduced) but tag the response
`version_mismatch` with both versions (FR-007). `sb schema-catalog status` reports per-plugin
catalog-vs-installed versions.

**Rationale**: A shipped catalog's main risk is silent drift; version tagging makes it visible and
refreshable without blocking the fix it provides.

## D7 — Generation coverage + Pro activation

**Decision**: `sb schema-catalog generate` runs against a designated instance with the free + Pro
plugins active and records per-entry coverage (full/partial). Pro activation is reachable via spec 013
(keyless WPDeveloper; Elementor sharing). When a plugin/Pro isn't active at generation, its entries are
omitted/marked, not faked.

**Rationale**: Honest coverage (FR-006/FR-011); the catalog reflects what was actually registered.

## Open items carried to tasks

- Exact catalog file layout (per-plugin gz vs one catalog.json.gz + index) — pick during T-design;
  both satisfy the contract.
- Compression ratio check against the real dump to confirm ≤3MB (SC-004).
- The dump page's persistence channel (option vs file) + how the generator triggers/reads it.
