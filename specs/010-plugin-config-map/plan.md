# Implementation Plan: Unified Slug-Keyed Plugin Config Map

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-plugin-config-map/spec.md`

## Summary

Replace the three overlapping plugin-config keys (`plugins` list, `mappings`,
`mappings_inactive`) with ONE canonical slug-keyed `plugins` **map** that decouples a
plugin's **source** (org / zip / local path) from its lifecycle **state** (active /
inactive / on-demand). The `plugins` key becomes type-polymorphic (array = legacy sugar,
object = canonical map). Config layers field-merge per slug via **normalize-then-field-merge**
so a machine override or the user-global *source catalog* changes only the fields it names
— the original override-replaces-list footgun and the worktree wrong-slug bug both vanish.
`mappings`/`mappings_inactive` (and the legacy list) are translated into the canonical map
at load time, preserving exact current behavior, with a deprecation warning. On-demand
plugins are not installed at provision; their local source is registered into a per-instance
local-source map that the existing `00-sandbox-dl-cache.php` mu-plugin consults to serve the
local copy on any install attempt (FSI / wp-cli / wp-admin), plus a v1 wp-admin UI that
lists on-demand plugins and installs them from local with one click.

## Technical Context

**Language/Version**: Python 3 stdlib (no new deps) for the config loader + provisioning;
PHP (mu-plugins) for the on-demand interception + admin UI.

**Primary Dependencies**: existing only — `sandbox_core.py` (config loader + layer merge),
the `sandbox/` CLI package (`core/_provision.py`, `core/_docker.py`), the
`00-sandbox-dl-cache.php` mu-plugin (already hooks `upgrader_pre_download`), wp-cli, Docker.

**Storage**: per-project config files (`sandbox.config.json`,
`sandbox.config.override.json`) + user-global `$SANDBOX_HOME/config.json`; a generated
per-instance **local-source map** (slug → local path/zip) consumed by the mu-plugin.

**Testing**: live-stack verification (constitution IV) per quickstart.md; plus the
sandbox's own `tests/test_sandbox.py` unit layer for the pure normalization/merge functions
(they are pure dict→dict — ideal for fast unit coverage).

**Target Platform**: developer workstations (the sandbox WP stack).

**Project Type**: CLI tool + WP mu-plugins.

**Performance Goals**: config resolution stays O(plugins) with no measurable overhead;
on-demand interception adds no cost until an install of a registered slug occurs.

**Constraints**: zero breakage for existing repos using the 3 legacy keys + the user-global
Pro set (constitution VI parity); idempotent provisioning (V); secrets never logged; the
canonical map must field-merge across all three layers without clobbering.

**Scale/Scope**: ~tens of plugins per project; 3 config layers; one new mu-plugin (or an
extension of the dl-cache one) + one admin screen; ~3 Python functions (normalize, merge,
rewire) + the loader wiring.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. No instance-model change; this is
  per-project config semantics.
- **II. The Registry Is the Single Source of Truth** — PASS. Registry untouched; only the
  config resolution that feeds provisioning changes.
- **III. Single Entry File, Modular Package** — PASS. Logic lands in `sandbox_core.py`
  (normalize + merge) and `sandbox/core/_provision.py` (rewire + mu-plugins); `sb` stays a
  thin entry. The mu-plugin/admin-UI is a vendored PHP asset written by a `_write_*`
  provisioning hook.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS. Each user story ends in
  a live boot/install check (quickstart.md).
- **V. Idempotency and Docs-With-Code** — PASS. Provisioning re-runnable; docs
  (sandbox-config-reference.md, CLAUDE.md config table + examples) land with the code.
- **VI. Feature Parity Before Removal** — PASS. The 3 legacy keys stay as sugar with a
  deprecation warning; nothing is removed until the map is proven on the live stack.

**Additional constraints**: secrets never echoed (local paths are not secrets, but the admin
UI enforces nonce + `manage_options`); WP-touching verification via MCP/`sb`; spec-kit
tooling stays out of the shipped product. No violations → no Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/010-plugin-config-map/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (config schema + merge + mu-plugin/UI contracts)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
sandbox_core.py
├── DEFAULTS                     # `plugins` keeps array default ["."] (legacy-safe);
│                                #   normalization handles both shapes
├── _normalize_plugins(doc)      # NEW: raw doc → {slug: CanonicalEntry} with UNSET fields;
│                                #   handles array(legacy) + object(canonical) + folds in
│                                #   legacy `mappings`/`mappings_inactive`; flags legacy use
├── _merge_plugin_maps(layers)   # NEW: per-field field-merge across layers w/ precedence
│                                #   (project > override > user-global); UNSET never clobbers;
│                                #   map-wins-over-legacy conflict warning
└── load_project_config()        # WIRE: resolve `plugins` via the two fns above (separate
                                 #   from the generic _deep_merge/_merge_layers for other
                                 #   keys); apply source/state defaults (org, on-demand) LAST
sandbox/core/_provision.py
├── _wire_project_plugins()      # REWRITE: consume CanonicalEntry list — path→symlink under
│                                #   the SLUG KEY (worktree-safe); zip/org→install; activate
│                                #   per state; inactive→install-no-activate; on-demand→DON'T
│                                #   install, register into the local-source map
├── _write_local_sources()       # NEW: write per-instance local-source map (slug→path/zip)
│                                #   the mu-plugin reads
├── _write_dl_cache_muplugin()   # EXTEND: `upgrader_pre_download` also looks up the
│                                #   local-source map → serve local copy (no download) for
│                                #   any install of a registered slug (FSI/wp-cli/wp-admin)
└── _write_ondemand_muplugin()   # NEW (v1): wp-admin page listing on-demand plugins +
                                 #   one-click "install from local" (nonce + manage_options)
sandbox/core/_docker.py          # call _write_local_sources alongside the other mu-plugin
                                 #   writers in the provisioning path (idempotent)
docs/sandbox-config-reference.md # canonical map schema, value shorthands, merge contract,
                                 #   source-catalog semantics, legacy-sugar mapping + timeline
CLAUDE.md                        # config-keys table + the per-plugin example configs
tests/test_sandbox.py            # unit tests for _normalize_plugins + _merge_plugin_maps
```

**Structure Decision**: existing single-entry `sb` + `sandbox/` package + `sandbox_core.py`
shared loader. The change is concentrated in two pure functions in `sandbox_core.py`
(normalize + field-merge — unit-testable in isolation), a rewrite of the single
`_wire_project_plugins` consumer, and two mu-plugin assets (extend dl-cache for
interception; add the on-demand admin UI). No new dependencies, no new top-level structure.

## Complexity Tracking

> No constitution violations — table intentionally omitted.
