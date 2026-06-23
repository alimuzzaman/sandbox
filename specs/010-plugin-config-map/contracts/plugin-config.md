# Contract: Plugin Config Schema, Merge & On-Demand Surface

The external surfaces this feature defines: (1) the `plugins` config schema, (2) the merge
contract, (3) the legacy-sugar mapping, (4) the generated local-source map, (5) the
on-demand admin UI.

## C1 — `plugins` schema (type-polymorphic)

`plugins` is EITHER an array (legacy) or an object/map (canonical).

```jsonc
// canonical map
"plugins": {
  "<slug>": true,                  // state only: active        (source UNSET → resolved)
  "<slug>": false,                 // state only: inactive      (source UNSET → resolved)
  "<slug>": "<local-path>",        // source only: local        (state UNSET)  (".", "~/x", "../x", "/abs")
  "<slug>": "https://….zip",       // source only: zip          (state UNSET)
  "<slug>": {                      // full control (exactly one source)
    "path": "<local-path>",        //   OR  "zip": "<url>"  OR  "source": "org"
    "active": true|false,          //   optional
    "onDemand": true|false         //   optional
  }
}
```
Rules:
- The KEY is the authoritative install slug (never derived from a dir name).
- Boolean shorthand sets **state only**; string shorthand sets **source only**.
- Object: at most ONE of `path`/`zip`/`source` — more than one is an error.
- `themes` is a separate, unchanged key.

## C2 — Merge contract (normalize-then-field-merge)

1. Normalize each layer's `plugins` (and legacy keys, C3) into `{slug: {source?, active?,
   onDemand?}}` with unspecified fields **UNSET**.
2. Field-merge across layers, precedence **project > override > user-global**; a field set in
   a higher layer wins; an UNSET field never overwrites a lower layer's value.
3. Apply resolved defaults to still-UNSET fields LAST: `source → org`; state → `on-demand`.

Guarantees:
- An override changes only the fields/slugs it names (no clobber, no drop).
- `project: true` + `user-global: "<path>"` → `{active:true, source:{path}}` (both kept; org
  fallback NOT applied).
- A user-global bare path is a **catalog** entry → on-demand → never auto-enables a plugin.
- Same slug in a legacy key AND the map → **map wins + one-line warning**.

## C3 — Legacy sugar (exact current behavior, deprecated)

| Legacy | Canonical | Today's behavior preserved |
|--------|-----------|----------------------------|
| `plugins: [".", "slug", "…zip", "/path"]` | array branch | install + activate |
| `mappings: {wp-content/plugins/<slug>: src}` | `{<slug>: {path:src, active:true}}` | symlink + activate |
| `mappings_inactive: {…/<slug>: src}` | `{<slug>: {path:src, active:false}}` | symlink, inactive |

One deprecation hint emitted when any legacy key is present. Removal deferred (parity).

## C4 — Local-source map (generated artifact)

A per-instance JSON written under the WP tree at provision:

```json
{ "<slug>": { "path": "/host/abs/path" }, "<slug>": { "zip": "/host/abs/file.zip" } }
```

- Contains on-demand entries AND locally-sourced entries (so any install of the slug is
  served local).
- Read by the dl-cache mu-plugin and the on-demand admin UI.
- Regenerated every provision (idempotent). Host paths only — no secrets.

## C5 — On-demand interception (behavioral contract)

When ANY WordPress install path (`Plugin_Upgrader->install` — Templately FSI, wp-cli
`plugin install <slug>`, wp-admin "Add Plugin" upload/install) requests a slug present in
the local-source map, the system MUST install the **local** copy and MUST NOT download it
from the registry. If the slug's local source is missing, surface a clear error (no silent
registry fallback).

**Temp-copy rule (mirrors the existing dl-cache gotcha):** `WP_Upgrader` DELETES the package
it is handed, so the interception MUST always return a **throwaway temp copy**, never the
real source. A local **directory** source is zipped to a fresh temp zip per install (under
the WP temp dir); a local **zip** source is copied to a temp file. The original local
checkout/zip is never moved or deleted.

## C6 — On-demand admin UI (v1)

A wp-admin screen MUST:
- list the instance's on-demand plugins (from the local-source map) with name + slug + state
  (available / installed / active);
- provide a **one-click "Install from local"** action that installs the local source (via
  C5) and offers activation;
- enforce `current_user_can('manage_options')` + a nonce on the action;
- be sandbox-only (never affects a real site — guarded like the other mu-plugins).

## C7 — CLI surface (no breaking changes)

`./sb` config consumers (ensure/apply/status) accept both `plugins` shapes transparently.
No new required flags. (A `./sb plugins` listing/​install-on-demand command is OPTIONAL —
the admin UI is the v1 discoverability surface.)
