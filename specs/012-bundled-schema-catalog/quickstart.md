# Quickstart / Live Validation: Bundled Schema Catalog

**Feature**: 012-bundled-schema-catalog · **Phase 1** · 2026-06-25

Per Constitution IV, "done" = these live checks pass. Two roles: a **generation** instance (free + Pro
plugins active) and a **consumer** instance (NO source checkout) that serves the committed catalog.

## Prerequisites
- A generation instance with EB free + **EB Pro** active (keyless via spec 013) and Elementor +
  **Elementor Pro**/EA active (shared via spec 013) so the registries are complete.
- A consumer instance running only the distributed (`.org`) builds, no EB source checkout.

## Check 1 — Generate the catalog (SC-007, FR-002/003)
- `sb schema-catalog generate --instance <gen>`.

**Expected**: a coverage report listing each plugin (present, full/partial, version) + per-builder
counts; the committed gzipped catalog + index updated under `sandbox/assets/editor-schema/`.

## Check 2 — Size bound (SC-004)
- `sb schema-catalog status`.

**Expected**: committed compressed size ≤ ~3MB and ≥5× smaller than the ~16MB raw dump; no uncompressed
multi-MB blob in the working tree.

## Check 3 — Full schema with NO source, including Pro (SC-001/SC-002)
On the consumer instance (no source checkout), via `editor-schema`:
- an **EB Pro** block (e.g. `essential-blocks/pro-business-hours`)
- an EB free block (e.g. `essential-blocks/advanced-heading`)
- an **Elementor Pro** widget

**Expected**: each returns its FULL attribute/control set with `source: "catalog"` where live was
reduced (EB Pro from ~3 keys → full); ≥95% of Gutenberg blocks and 100% of Elementor widgets full.

## Check 4 — Precedence: live preferred, catalog fallback (FR-005)
- Request a block whose live result is already `full` (source checkout present, or a core block).

**Expected**: `source: "live"` — the catalog is NOT consulted when live is full; when live is
partial/reduced and the catalog is richer, `source: "catalog"`.

## Check 5 — No regression (FR-008, SC-005)
- `editor-schema` for an installed Elementor widget (`eael-info-box`) and a core block (`core/heading`).

**Expected**: byte-identical to pre-feature — NO `source` marker added to those live results.

## Check 6 — Version awareness (FR-007, SC-006)
- Serve a catalog entry whose plugin version differs from the installed build (bump one).

**Expected**: still served (better than reduced) but flagged `version_mismatch` with both versions;
never presented as current.

## Check 7 — Zero per-user regeneration (SC-003)
- On a fresh consumer instance, run no generation; request Pro schemas.

**Expected**: full schemas served from the committed catalog with zero generation steps.

## Evidence to capture
Per check: the `editor-schema` response JSON (with `source`/`catalog` markers), the `sb schema-catalog
status` size/coverage output, and the no-regression diff for Check 5.
