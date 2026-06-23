# Feature Specification: Unified Slug-Keyed Plugin Config Map

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "Redefine the sandbox plugin-config model as ONE slug-keyed
`plugins` map that decouples a plugin's SOURCE (org / zip / local path) from its lifecycle
STATE (active / inactive / on-demand), keeping the three legacy keys (`plugins` list,
`mappings`, `mappings_inactive`) as backward-compatible sugar. Fixes the
override-replaces-list footgun and the worktree wrong-slug bug; adds lazy on-demand local
sourcing for FSI/Pro plugins."

## Clarifications

### Session 2026-06-23

- Q: How is the new slug-keyed map expressed in config — reuse `plugins` or a new key? → A: Reuse `plugins`, type-polymorphic — an **array** is the legacy sugar form, an **object/map** is the new canonical form; the loader branches on the value's type.
- Q: Is the wp-admin browse / one-click "install from local" UI for on-demand plugins in v1, or engine-first? → A: **In v1** — ship the admin UI (list on-demand plugins + one-click install-from-local) together with the interception engine.
- Q: When the same slug appears in BOTH a legacy key and the new map, what happens? → A: **The map entry wins**, and a one-line warning naming the slug is emitted (forgiving; eases incremental migration).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One entry per plugin, machine overrides merge per-slug (Priority: P1)

A developer declares their project's plugins once, keyed by slug, in `sandbox.config.json`,
and provides machine-specific source overrides in the gitignored
`sandbox.config.override.json`. The override changes only the plugins it names; every other
declared plugin is preserved. No plugin is ever silently dropped, and no plugin needs to be
declared twice.

**Why this priority**: This is the core defect being fixed. Today a machine override's
plugin list *replaces* the base list, silently dropping entries (the developer's own
addon disappeared in a real case). A slug-keyed map merges per-key across layers, making
the data-loss class of bug structurally impossible. Everything else builds on this.

**Independent Test**: Declare ≥2 plugins in the base config and override exactly one by
slug; resolve the effective config and confirm all base plugins survive with only the named
one changed; boot the instance and confirm every declared plugin is present.

**Acceptance Scenarios**:

1. **Given** a base config declaring plugins A (this repo) and B (org), **When** an override
   sets B's source to a local path, **Then** the resolved config still contains A unchanged
   and B now sourced locally — A is NOT dropped.
2. **Given** the same base, **When** an override adds a new plugin C, **Then** the resolved
   config contains A, B, and C (per-slug merge across project → override → user-global).
3. **Given** a plugin that must be both locally-sourced and active, **When** it is declared
   once in the map, **Then** no second declaration is required anywhere.
4. **Given** the project declares slug X as active (state-only shorthand) AND the
   user-global catalog declares X with a local path (source-only), **When** the config
   resolves, **Then** the result has BOTH: active = from the project, source = the catalog's
   local path — neither the state nor the path is lost, and the org fallback is NOT applied.
5. **Given** the same project-active + catalog-path, **When** the project instead pins an
   explicit source (e.g. org), **Then** the explicit project source wins over the catalog
   path.

---

### User Story 2 - Correct slug on git worktrees (Priority: P1)

A developer works in a git worktree whose directory name is not the plugin slug
(e.g. `templately-ai-builder-fsi-rewrite` for the plugin `templately-ai-builder`). The
plugin installs under its **correct** slug regardless of the directory name.

**Why this priority**: Worktrees are the standard workflow here; an implicitly-derived
slug installs the plugin under the wrong directory, breaking `plugin_dir_url()`, updates,
and activation. Making the slug explicit (the map key) removes a whole class of silent
breakage.

**Independent Test**: From a worktree dir whose name ≠ slug, declare the plugin keyed by
its real slug sourced from the local checkout; boot and confirm the plugin is installed and
active under the correct slug directory.

**Acceptance Scenarios**:

1. **Given** a worktree dir named differently from the plugin slug, **When** the plugin is
   declared keyed by its true slug with the local source, **Then** it is wired under the
   true slug, active, and resolvable — not under the directory name.

---

### User Story 3 - Decouple source from state; on-demand (lazy) Pro plugins (Priority: P2)

A developer declares Pro/optional plugins that should NOT be installed up front but should
be served from a local copy the moment something tries to install them (Full Site Import,
the wp-admin "Add Plugin" flow, or a command-line install). Such plugins are absent from a
fresh site until requested, then materialized from local — never downloaded.

**Why this priority**: Keeps fresh installs lean while making heavy Pro plugins available
exactly when an import or user action needs them, sourced from the developer's local copy
instead of the network. It generalizes today's "available but inactive" Pro-plugin pattern
into a lazy one.

**Independent Test**: Declare a plugin as on-demand with a local source; confirm it is
absent on a fresh boot; trigger an install of that slug via each install path and confirm
the local copy is used (no download) and the plugin becomes present.

**Acceptance Scenarios**:

1. **Given** a plugin declared on-demand, **When** the instance boots, **Then** the plugin
   is not present/installed.
2. **Given** an on-demand plugin with a local source, **When** any install path requests
   that slug, **Then** the local copy is installed (no network download).
3. **Given** an on-demand plugin, **When** a developer views the available list, **Then**
   it appears as "available to install."
4. **Given** a slug declared ONLY in the user-global catalog as a bare local path (no
   explicit state), **When** a project that does NOT mention that slug boots, **Then** the
   plugin is NOT installed or activated for that instance (catalog default = on-demand
   availability, never auto-enable).
5. **Given** a user-global entry with an explicit active state, **When** any instance boots,
   **Then** that plugin is active in EVERY instance (opt-in "force-active everywhere").

---

### User Story 4 - Existing repos keep working unchanged (Priority: P1)

A developer (or another repo) using the three legacy keys (`plugins` list, `mappings`,
`mappings_inactive`) upgrades and changes nothing. Their instances provision exactly as
before.

**Why this priority**: This is shared tooling across many repos and a machine-wide
user-global config. A redefinition that breaks existing configs is unacceptable;
parity-before-removal is mandatory.

**Independent Test**: With a config using only the legacy keys (including the user-global
Pro-plugin set), boot an instance and confirm the same plugins are installed/active/inactive
as before this change.

**Acceptance Scenarios**:

1. **Given** a config using the legacy `plugins` list, **When** the instance is provisioned,
   **Then** those plugins install and activate exactly as before.
2. **Given** a config using legacy `mappings`, **When** provisioned, **Then** those plugins
   are symlinked from local AND activated (today's behavior, preserved).
3. **Given** a config using legacy `mappings_inactive` (incl. the user-global Pro set),
   **When** provisioned, **Then** those plugins are symlinked but inactive, available for
   import-time activation — exactly as before.
4. **Given** any legacy key is present, **When** the config is loaded, **Then** a one-line
   deprecation hint is surfaced pointing to the new map form.

---

### Edge Cases

- **Same slug in a legacy key AND the new map**: the map entry is authoritative; the
  conflict is surfaced, not silently merged ambiguously.
- **A local source path that does not exist** (e.g. an override pointing at a missing
  checkout): provisioning skips it with an actionable warning rather than failing the whole
  boot, and never silently installs the org version in its place when a local override was
  intended.
- **A map value in an unrecognized shorthand** (neither boolean, string path/zip/slug, nor
  the object form): rejected with a clear message naming the offending slug.
- **On-demand plugin requested but its local source is missing**: the install surfaces a
  clear error (local source unavailable) rather than silently falling back to a download.
- **A plugin keyed by slug but sourced from a zip/org while also given a local path**:
  exactly one source per entry; declaring more than one is rejected with a clear message.
- **Theme entries**: `themes` remains a separate key (unchanged); the plugin map does not
  absorb themes in this feature.
- **Same slug across three layers** (project, override, user-global): normalize each to the
  canonical shape, then **field-merge** — per-field precedence project > override > user-global
  for fields each layer actually sets; UNSET fields never clobber. Canonical example: project
  `templately: true` (active, source unset) + user-global `templately: "~/…"` (source path,
  state unset) → resolved `{ active: true, source: ~/… }`; the org fallback is NOT applied
  and the path is NOT lost.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a canonical `plugins` configuration that is a map
  keyed by plugin **slug**, where the key is the authoritative install slug (never derived
  from a directory name). The `plugins` key MUST be **type-polymorphic**: an **array**
  value is the legacy sugar form (FR-008), an **object** value is the new canonical map; the
  config loader branches on the value's type. (No new top-level key is introduced.)
- **FR-002**: Each map value MUST express two orthogonal axes — **source** (org/registry
  slug, a zip URL, or a local path) and **state** (active, inactive, or on-demand) — via
  ergonomic shorthands or an object form. Critically, the boolean shorthands set **state
  only and leave source UNSET**: `true` = active (source unset), `false` = inactive (source
  unset). A bare string sets **source only and leaves state UNSET**: a local path or zip URL.
  The object form may set any subset of fields. "From org/registry" is NEVER an explicit
  source a shorthand stamps in — it is the **final fallback** applied only when no layer
  supplied a source (FR-004c).
- **FR-003**: The system MUST normalize every entry into one internal canonical shape
  — { slug, source: (unset | org | zip | path), active: (unset | bool), onDemand:
  (unset | bool) } — **before merging layers**, where any field a shorthand did not
  specify remains explicitly UNSET (so it cannot overwrite another layer's value).
- **FR-004**: The `plugins` map MUST be merged by **normalize-then-field-merge**, NEVER
  whole-value replace: each layer's entry is normalized to the canonical shape, then entries
  for the same slug are deep-merged **per field** across layers (project → project override →
  user-global). A field set in a higher-precedence layer wins; a field left UNSET there does
  NOT clobber a value supplied by another layer. An override therefore changes only the
  fields (and slugs) it names; no unrelated slug or field is dropped.
- **FR-004a**: Per-field precedence MUST be: **state** (active/onDemand) — project wins; if
  the project is silent on a slug, the slug's state comes from the catalog default (FR-004b,
  on-demand) unless a layer set it explicitly. **source** — an explicit project source wins,
  else the user-global catalog's source (local path), else the org fallback (FR-004c).
  Result for the canonical case (project sets `active`, user-global sets `path`): both are
  kept — active from the project, source from the catalog; the org fallback is not applied.
- **FR-004b**: The **user-global** config is a machine-wide **source catalog**: a plugin
  entry there whose value is a bare source (e.g. a local path) provides only *where* the
  plugin's code lives and MUST default to **on-demand** — it MUST NOT, by itself, install or
  activate that plugin into any instance. A plugin is installed/activated for an instance
  ONLY when (a) the **project** declares the slug, or (b) something requests it on-demand
  (FR-007), or (c) the user-global entry **explicitly** sets an active state — the opt-in
  "force-active in every instance" case. (This is what lets a developer keep all their local
  checkout paths in one user-global catalog without enabling those plugins in projects that
  don't use them.)
- **FR-004c**: "Install from org/registry" MUST be the **final source fallback**, applied
  only when no configuration layer supplied a source for a slug after merging. It MUST NOT
  be stamped onto an entry by a state-only shorthand — so a project's `true` (active) never
  overwrites a user-global catalog's local path; the local path is preserved and used.
- **FR-005**: For a plugin sourced from a **local path**, the system MUST install it under
  the slug given by the map key (worktree-safe), regardless of the source directory's name.
- **FR-006**: A plugin whose state is **active** MUST be installed and activated; **inactive**
  MUST be installed/present but not activated; **on-demand** MUST NOT be installed at
  provision time.
- **FR-007**: When any install path (Full Site Import, wp-admin "Add Plugin", or a
  command-line install) requests a slug declared **on-demand** with a local source, the
  system MUST install the local copy and MUST NOT download it from the registry.
- **FR-008**: The system MUST continue to accept the three legacy keys (`plugins` list,
  `mappings`, `mappings_inactive`) and translate them into the canonical map at load time,
  **preserving their exact current behavior**: legacy `plugins` list = install + activate
  (local-path slug still derived from directory name, for compat); legacy `mappings` =
  local symlink + activate; legacy `mappings_inactive` = local symlink, inactive.
- **FR-009**: When any legacy key is present, the system MUST surface a one-line deprecation
  hint pointing to the canonical map form (without failing).
- **FR-010**: The legacy keys MUST NOT be removed until the canonical map is proven on the
  live stack (staged parity); both forms MUST coexist for at least one release.
- **FR-011**: A **local source override** that points at a missing path MUST be reported
  with an actionable warning and MUST NOT be silently replaced by a registry download of the
  same slug.
- **FR-012**: Malformed entries (multiple sources for one slug, unknown shorthand) MUST be
  rejected with a message naming the offending slug. When the **same slug** appears in both a
  legacy key and the new map, the **map entry MUST win** and a one-line warning naming the
  slug MUST be emitted — never resolved ambiguously and silently.
- **FR-013**: A developer MUST be able to see and install **on-demand** plugins from within
  wp-admin: a UI (mu-plugin/admin screen) MUST list the plugins declared on-demand for the
  instance and offer a **one-click "install from local"** action that materializes the local
  source (no download). This admin UI is in scope for v1 (not deferred).
- **FR-014**: Documentation MUST be updated in the same change — the config reference and
  the agent guide's config-keys section — describing the canonical map, value shorthands,
  the merge contract, the legacy-sugar mapping, and the deprecation timeline. Per-project
  example config files live in the plugin repos (outside this repo); this feature updates the
  in-repo docs plus a reference note showing the recommended map form for those examples.
- **FR-015**: `themes` remains a separate, unchanged configuration key in this feature.

### Key Entities *(include if feature involves data)*

- **Plugin entry**: the canonical normalized unit — { slug (key), source (UNSET | org |
  zip-url | local-path), active (UNSET | bool), on-demand (UNSET | bool) }. Fields are
  explicitly UNSET until a layer sets them, so layers field-merge without clobbering; the
  org source and the on-demand state are the resolved defaults for still-UNSET fields.
- **Plugins map**: slug → plugin entry; the authoritative per-project plugin declaration,
  merged per-slug across config layers.
- **Local-source registry**: the generated map of slug → local source consulted at
  install time so on-demand (and locally-overridden) slugs are served from local instead of
  downloaded.
- **Config layers**: project config, project override (per-machine, gitignored), and
  user-global config — folded together with per-slug merge semantics.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single slug-keyed map can express **100%** of the cases the three legacy
  keys express today (active local, active org, inactive local, on-demand local, org/zip
  install), verified by an equivalence check.
- **SC-002**: A machine override that changes one plugin preserves **100%** of the other
  declared plugins (zero silent drops) across project → override → user-global.
- **SC-003**: A plugin sourced from a local checkout in a differently-named worktree installs
  under the **correct slug 100%** of the time.
- **SC-004**: An on-demand plugin is absent on a fresh boot and, when requested via **each**
  of the three install paths, is served from local with **zero** network downloads.
- **SC-005**: **100%** of existing repos using only the legacy keys provision identically
  (same installed/active/inactive plugin set) after this change.
- **SC-006**: Using any legacy key produces exactly one deprecation hint (no breakage, no
  noise loop).
- **SC-007**: When a slug is declared as state-only in the project and source-only (local
  path) in the user-global catalog, the resolved entry retains **both** fields in 100% of
  cases — the path is never lost and the org fallback is never wrongly applied.
- **SC-008**: A local-path entry present ONLY in the user-global catalog enables the plugin
  in **0%** of projects that do not declare its slug (catalog never auto-enables).

## Assumptions

- The default registry source is wp.org (today's behavior); "org" means a registry slug
  install.
- The three configuration layers and their precedence (project > override > user-global,
  with per-slug/per-field merge) are those already established by the sandbox config loader.
- The on-demand interception reuses the existing download-cache mechanism's hook into the
  WordPress upgrader rather than introducing a new interception layer.
- Legacy `mappings` keeps activating (its current behavior) under the sugar; the new
  "source-without-activation" semantics live only in the canonical map's explicit fields, so
  no existing repo silently deactivates a plugin.
- The admin/mu-plugin "install on demand" UI (list on-demand plugins + one-click
  install-from-local) is **in scope for v1** (FR-013), alongside the headless interception so
  on-demand also works via FSI / wp-cli / wp-admin install-by-slug.
- Themes are intentionally out of scope for folding into the map (separate key retained).
