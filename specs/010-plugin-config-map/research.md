# Phase 0 Research: Unified Slug-Keyed Plugin Config Map

All unknowns resolved against the codebase + the clarified spec. No NEEDS CLARIFICATION
remain (the 3 open items were closed by `/speckit-clarify` Session 2026-06-23).

## D1 — Canonical entry shape & value shorthands

**Decision**: Normalize every entry to `CanonicalEntry = {slug, source, active, onDemand}`
where each non-key field is one of a value or the sentinel **UNSET**. Source is one of
`{org, (zip, url), (path, p)}` or UNSET. Shorthands:
- `true` → `{active: True}` (source UNSET)
- `false` → `{active: False}` (source UNSET)
- `"<path>"` / `"."` / `"~/…"` / relative → `{source: path}` (active UNSET)
- `"<…>.zip"` URL → `{source: zip}` (active UNSET)
- object `{path|zip|source?, active?, onDemand?}` → fields as given; unspecified stay UNSET

**Rationale**: keeping fields explicitly UNSET (not defaulted) until after the layer merge
is what makes field-merge non-clobbering (FR-002/003/004). Defaults (`source=org`,
`state=on-demand`) are applied only at the very end to still-UNSET fields.

**Alternatives considered**: defaulting at normalize time (rejected — a project's `true`
would carry `source=org` and overwrite a catalog path); a flat string DSL like
`"templately@local!active"` (rejected — unreadable, hard to merge).

## D2 — Per-field merge & precedence

**Decision**: Resolve `plugins` OUTSIDE the generic `_deep_merge`/`_merge_layers`. Build a
normalized map per layer, then field-merge in precedence order. Precedence:
**state** project > override > user-global (project wins; if all silent → on-demand default);
**source** explicit project > override > user-global catalog > org fallback. UNSET never
overwrites a set value. Implement as: start from user-global, fold override on top (set
fields win), fold project on top — per field — yielding the same "higher layer wins per
field it sets" result.

**Rationale**: the generic merge replaces lists and per-key-replaces dict values (the
original bug). A dedicated field-merge for `plugins` is the minimal correct fix and keeps
the other config keys on the existing merge.

**Alternatives considered**: making `_deep_merge` recurse into list-of-objects (rejected —
lists don't key; can't field-merge); forcing everyone onto the object map immediately
(rejected — breaks parity).

## D3 — Source catalog semantics (user-global)

**Decision**: A user-global entry that sets only a source (bare path) yields state UNSET →
resolves to **on-demand** (available, never auto-installed/active). It enables a plugin only
if (a) the project declares the slug, (b) on-demand interception fires, or (c) the
user-global entry **explicitly** sets `active: true` (force-active everywhere). This falls
out of D1+D2 for free — no special case beyond "default UNSET state = on-demand".

**Rationale**: exactly the user's requirement — keep every local checkout path in one
catalog without enabling them in projects that don't use them.

## D4 — Legacy translation (parity, no breakage)

**Decision**: `_normalize_plugins` folds the 3 legacy keys into the canonical map preserving
EXACT current behavior (from `_wire_project_plugins`):
- legacy `plugins` array: `"."`/local-path → `{source: path, active: True}` (slug = dir name,
  for compat — NOT the worktree-corrected key); zip → `{source: zip, active: True}`; slug →
  `{active: True}` (source UNSET → org).
- legacy `mappings` `{wp-content/plugins/<slug>: src}` → `{slug: {source: path(src),
  active: True}}` (today's behavior: symlink + activate).
- legacy `mappings_inactive` → `{slug: {source: path(src), active: False}}` (symlink,
  inactive — eager, matching today; NOT on-demand, to preserve behavior).
- Non-plugin mappings (themes, mu-plugins, arbitrary wp-paths) are NOT plugin entries —
  keep handling them through the existing mappings path (do not force into the plugin map).
- Emit ONE deprecation hint when any legacy key is present.

**Rationale**: constitution VI — prove the map before removing the keys; existing repos +
the user-global Pro `mappings_inactive` set keep working unchanged.

**Note**: legacy `mappings_inactive` stays EAGER-inactive (current), while the new map's
`onDemand` is LAZY. Both coexist; the new lazy behavior is opt-in via the map.

## D5 — On-demand interception (all install paths)

**Decision**: Provisioning writes a per-instance **local-source map** (`slug → {path|zip}`)
into the WP tree (a JSON the mu-plugin reads). Extend `00-sandbox-dl-cache.php`'s existing
`upgrader_pre_download` hook: before the cache logic, if the package being installed resolves
to a slug present in the local-source map, return a local zip (zip a local dir on the fly, or
use the zip path) so WP installs the LOCAL copy with no download. This single hook covers
**all** `Plugin_Upgrader->install` paths — Templately FSI, wp-cli `plugin install`, and
wp-admin "Add Plugin" (upload + install-by-slug). It already hands WP a throwaway temp copy
(WP deletes the package), so the existing pattern is reused.

**Rationale**: one interception point already exists and covers every programmatic install;
no new hook surface needed. Slug↔package matching reuses the dl-cache plugin's slug logic.

**Alternatives considered**: eager symlink like today's `mappings_inactive` (rejected — the
user wants lazy/not-present-until-needed); a `plugins_api` shim to surface Pro plugins in the
wp.org search results (rejected for v1 — the admin UI in D6 gives discoverability instead).

## D6 — On-demand admin UI (v1)

**Decision**: A mu-plugin admin page (`Plugins → Sandbox: Available` or a Tools page) lists
the instance's on-demand plugins (from the local-source map) with a **one-click "Install from
local"** button → triggers `Plugin_Upgrader->install()` against the local source (served by
D5), then offers activate. Auth: `current_user_can('manage_options')` + nonce
(`check_admin_referer`). Written by a `_write_*_muplugin` provisioning hook like the others.

**Rationale**: clarified in scope for v1; gives self-serve discoverability without a
`plugins_api` shim. Reuses the snapshot mu-plugin's admin-page + nonce pattern.

## D7 — Schema: type-polymorphic `plugins`

**Decision**: `plugins` accepts an **array** (legacy) or an **object** (canonical map); the
loader branches on `isinstance(value, dict)`. `DEFAULTS["plugins"]` stays `["."]` (array) so
a project with no config still installs its own dir. `themes` stays a separate key (FR-015).

**Rationale**: clarified — one key, smoothest migration, no new top-level concept.

## D8 — Validation & errors

**Decision**: reject malformed entries (object with >1 source, unknown shorthand type) with
a message naming the slug. Same slug in a legacy key AND the map → **map wins + warning**
(clarified). A local source path that doesn't exist → skip-with-warning at provision (never
silently install org in its place when a local override was intended, FR-011).

## D9 — Out of scope

- Folding `themes` into the map (FR-015).
- A `plugins_api` search shim surfacing Pro plugins in wp.org search (admin UI covers
  discoverability for v1).
- Removing the legacy keys (staged for a later release after parity is proven).
