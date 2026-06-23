# Phase 1 Data Model: Unified Slug-Keyed Plugin Config Map

No database. The "data model" is the config schema, the canonical normalized entry, and the
generated local-source map.

## Entity: Plugin entry (canonical, normalized)

| Field | Type | Notes |
|-------|------|-------|
| `slug` | string (the map KEY) | authoritative install slug; worktree-proof |
| `source` | UNSET \| `org` \| `{zip: url}` \| `{path: hostpath}` | where code comes from |
| `active` | UNSET \| bool | true=activate, false=installed-inactive |
| `onDemand` | UNSET \| bool | true=not installed at provision; lazy via interception |

**Resolved defaults (applied LAST, only to still-UNSET fields):** `source` → `org`; state →
**on-demand** when both `active` and `onDemand` are UNSET after merge (the catalog default —
never auto-enable). An explicit `active` (true/false) from any layer wins over this default.
See the State-resolution block below.

**State resolution (post-merge):**
- `active=true` → install (from source) + activate
- `active=false` (and not onDemand) → install + do NOT activate
- `onDemand=true` → do NOT install; register source into the local-source map
- all UNSET → **on-demand** (catalog default; never auto-enable)

**Validation:** exactly one source kind; unknown shorthand → error naming the slug; missing
local path → skip + warning (never silently substitute org).

## Entity: `plugins` config value (per layer, raw → normalized)

| Raw form | Meaning |
|----------|---------|
| **array** (legacy) | sugar; each element normalized per legacy rules (D4) |
| **object/map** (canonical) | `{ slug: <value> }`, value = shorthand or object (D1) |

Value shorthands → normalized entry:
| Shorthand | Normalized |
|-----------|-----------|
| `true` | `{active: true}` (source UNSET) |
| `false` | `{active: false}` (source UNSET) |
| `"."` / `"~/p"` / `"../p"` / `/abs` | `{source: {path}}` (active UNSET) |
| `"https://….zip"` | `{source: {zip}}` (active UNSET) |
| `{ path\|zip\|source?, active?, onDemand? }` | fields as given; rest UNSET |

## Entity: Config layers (merge inputs)

| Layer | File | Role |
|-------|------|------|
| project | `sandbox.config.json` (or `.wp-env.json`) | declares the project's plugins + states |
| override | `sandbox.config.override.json` (gitignored) | per-machine source overrides |
| user-global | `$SANDBOX_HOME/config.json` | machine-wide **source catalog** (default on-demand) |

**Merge (normalize-then-field-merge):** normalize each layer's `plugins` (incl. legacy-key
fold-in) → field-merge with precedence project > override > user-global, UNSET never clobbers
→ apply resolved defaults last. (Other config keys keep the existing `_deep_merge` /
`_merge_layers`.)

## Entity: Legacy-key translation (parity)

| Legacy | → canonical entry |
|--------|-------------------|
| `plugins: ["."]` / path | `{source:{path}, active:true}` (slug = dir name, compat) |
| `plugins: ["slug"]` | `{active:true}` (source UNSET→org) |
| `plugins: ["…zip"]` | `{source:{zip}, active:true}` |
| `mappings: {wp-content/plugins/<slug>: src}` | `{source:{path:src}, active:true}` |
| `mappings_inactive: {…/<slug>: src}` | `{source:{path:src}, active:false}` (eager-inactive) |
| `mappings*` to non-plugin wp-paths | unchanged (handled by existing mappings path) |

One deprecation hint emitted when any legacy key present. Same slug in legacy + map → map
wins + warning.

## Entity: Local-source map (generated, per instance)

`slug → { path | zip }` — written into the WP tree at provision for on-demand (and
locally-sourced) slugs. Consumed by:
- the dl-cache mu-plugin's `upgrader_pre_download` → serve local on any install of the slug
- the on-demand admin UI → list + one-click "install from local"

Regenerated on every provision (idempotent); contains only host paths (no secrets).

**Temp-copy rule:** when interception serves a local source, it MUST hand `WP_Upgrader` a
**throwaway temp copy** (a fresh temp zip for a local dir; a temp copy for a local zip) —
because the upgrader deletes the package it receives (same gotcha as the existing dl-cache
mu-plugin). The real local source is never moved or deleted.

## State transitions: a slug from config → instance

```
declared (some layer)
  → normalized entry (UNSET-aware)
  → field-merged across layers
  → defaults applied (source=org, state=on-demand)
  → provisioning:
       active            → install(source) + activate
       inactive          → install(source), no activate
       on-demand + local → register in local-source map (NOT installed)
                            → later install attempt (FSI/wp-cli/wp-admin) → served local
                            → admin UI "install from local" → installed on click
```
