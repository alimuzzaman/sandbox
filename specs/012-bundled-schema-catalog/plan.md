# Implementation Plan: Bundled Schema Catalog for Editor Authoring

**Branch**: `012-bundled-schema-catalog` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-bundled-schema-catalog/spec.md`

## Summary

Ship a committed, gzipped, version-keyed **schema catalog** covering every builder so `editor-schema`
serves full fidelity — including EB Pro and Elementor Pro — with no source checkout and no per-user
regeneration. Generation reads the authoritative runtime registries (NOT plugin source):

- **Elementor / EA / Elementor Pro** — `$widget->get_controls()` from the live PHP registry (eager,
  complete; already what `sb introspect widgets` + the editor-schema Elementor path do).
- **Gutenberg core / EB free / EB Pro** — `wp.blocks.getBlockTypes()` from the **editor JS registry**,
  captured **headlessly** (a finalizer-style admin page serializes the registry, reusing the spec-005
  headless `wp.blocks` mechanism). Server-side PHP (`sb introspect blocks`) only sees block.json, so
  it is **reduced** for EB and is NOT used for the catalog's Gutenberg attributes — verified in spec
  011 (advanced-heading: 3 keys server-side vs 1693 in the editor registry).

`editor-schema` gains a catalog **fallback**: after computing its live result it serves the catalog
entry only when live is partial/reduced/unavailable (richer wins), tagging the response `source`.
A maintainer regenerates with `sb schema-catalog generate` from an instance that has the free **+ Pro**
plugins active; end users consume the committed asset.

## Technical Context

**Language/Version**: Python 3 (the `sb schema-catalog` command + the host-side generation
orchestration + the gzip packer) and PHP 7.4+ (the in-instance `editor-schema` catalog-fallback read,
and the finalizer-style headless dump page). JS only as the payload evaluated in the editor
(`wp.blocks.getBlockTypes()`), not authored by us.

**Primary Dependencies**: the existing `editor-schema` ability + spec-005 headless-editor
infrastructure (`00-sandbox-eb-finalizer.php` pattern that boots `wp.blocks` + `registerCoreBlocks()`
+ EB editor assets); `sb introspect`'s PHP registry-dump pattern (`wp eval-file`) for Elementor; the
spec-009 base seam for asset paths; Python `gzip`/`json` for packing.

**Storage**: A committed, gzipped, version-keyed catalog under `sandbox/assets/editor-schema/`
(e.g. `<builder>/<slug>@<version>.json.gz`, plus an index). At provision, the relevant catalog files
are written into the instance (read by the in-instance ability) — same copy mechanism as the editor
assets. Compressed only; no uncompressed multi-MB blob committed.

**Testing**: Live-stack per constitution — `editor-schema` on an instance with NO source checkout
returns full schemas (incl EB Pro + Elementor Pro) served from the catalog; precedence (live preferred,
catalog fallback) demonstrated; no-regression for Elementor/core live paths; catalog size bound
checked. Plus Python tests for the packer/version-key/precedence resolver where exercisable offline.

**Target Platform**: Generation on a maintainer/CI instance (free + Pro plugins active); consumption
in every project instance's in-container `editor-schema` ability.

**Project Type**: Sandbox tooling — a new `sb schema-catalog` command + generation orchestration + an
editor-schema fallback path + a committed asset. No new MCP tool; editor-schema is the existing surface.

**Performance Goals**: Catalog lookup adds negligible latency to `editor-schema` (indexed read +
gunzip of one entry). Generation is a maintainer batch (minutes), not a hot path.

**Constraints**: The committed catalog MUST stay ≤~3MB compressed (SC-004) — at least 5× smaller than
the ~16MB raw dump. The in-instance ability can only read what's in the container, so the catalog must
be provisioned into the instance (like the editor assets). Generation requires the Pro plugins ACTIVE
(spec 013's keyless WPDeveloper activation + Elementor sharing make this reachable) — coverage is
recorded honestly when a plugin/Pro is absent at generation.

**Scale/Scope**: ~380 Gutenberg blocks (incl EB Pro) + ~240+ Elementor widgets (incl Pro/EA). Per-entry
attributes range into the thousands; the compressed catalog is the shipped artifact.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. Generation runs on whatever instance the
  maintainer points at; consumption is per-instance. No global/fallback instance introduced.
- **II. The Registry Is the Single Source of Truth** — PASS. No change to project→instance resolution.
  The catalog is a separate asset; version-keying references plugin versions, not instances.
- **III. Single Entry File, Modular Package** — PASS. `sb schema-catalog` is a new self-contained
  command module in `sandbox/commands/`, registered via the registry. No new entry file.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS (enforced). SC-001..SC-007 are live
  `editor-schema` checks (catalog-served full schemas incl Pro, precedence, no regression); quickstart
  encodes them. Generation output is validated by serving it live.
- **V. Idempotency and Docs-With-Code** — PASS. `sb schema-catalog generate` is re-runnable
  (regenerates deterministically); ships with CLAUDE.md + the `gutenberg-eb`/`elementor-ea` SKILL
  updates (catalog fallback + how to regenerate) and a `memory/plugin-behavior/` note.
- **VI. Feature Parity Before Removal** — PASS. Additive: the spec-011 live resolver stays as the
  primary (preferred) path; the catalog only adds a fallback. Nothing removed.

**Gate result: PASS — no violations; Complexity Tracking not required.**

## Project Structure

### Documentation (this feature)

```text
specs/012-bundled-schema-catalog/
├── plan.md           # This file
├── research.md       # Phase 0 — generation method, headless GB dump, storage/precedence/versioning
├── data-model.md     # Phase 1 — catalog, entry, index, coverage/version metadata
├── quickstart.md     # Phase 1 — live validation (no-source instance serves full Pro schemas)
├── contracts/
│   ├── sb-schema-catalog-cli.md     # the generate/inspect command surface
│   └── editor-schema-catalog.md     # editor-schema response + precedence + source/version markers
└── tasks.md          # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
sandbox/assets/editor-schema/         # NEW committed asset: gzipped version-keyed catalog + index
sandbox/commands/schema_catalog.py    # NEW: `sb schema-catalog generate|status` (+ registry/cli wiring)
sandbox/core/_schema_catalog.py       # NEW: pack/unpack, version-key, index, host-side generation
                                      #   orchestration (Elementor PHP dump + headless GB dump)
sandbox/assets/abilities/
│   ├── sandbox-editor.php            # CHANGED: editor-schema catalog fallback (richer-wins) + source/
│   │                                 #   version markers; reads the provisioned catalog
│   └── 00-sandbox-schema-dump.php    # NEW: finalizer-style admin page that serializes
│                                     #   wp.blocks.getBlockTypes() headlessly (GB full-fidelity dump)
sandbox/core/_provision.py            # CHANGED: provision the relevant catalog files into the instance
CLAUDE.md                             # CHANGED: gotcha — bundled catalog + editor-schema fallback
memory/plugin-behavior/
└── schema-catalog.md                 # NEW: runtime-registry-as-truth, headless GB dump, regen, Pro
```

**Structure Decision**: A new `sb schema-catalog` command + `_schema_catalog.py` core own generation +
packing; the in-instance `editor-schema` ability gains the fallback read; a new finalizer-style dump
page provides the headless Gutenberg registry (the one thing PHP can't get). This reuses the proven
spec-005 headless-`wp.blocks` mechanism and the spec-011 fidelity model, and keeps the consumer surface
(`editor-schema`) unchanged except for the additive fallback.

## Complexity Tracking

> No constitution violations — section intentionally empty.
