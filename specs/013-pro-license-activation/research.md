# Research: Cross-Instance Pro License Activation & Sharing

**Feature**: 013-pro-license-activation · **Phase 0** · 2026-06-25

Grounded in reading the actual Pro plugin code under `/Users/alim/Sites/plugins-pro/` and the proven
`templately-multi/wp-content/mu-plugins/elementor-multisite.php`.

## D1 — Uniform WPDeveloper activation: HTTP interception, not per-plugin option seeding

**Decision**: Activate all WPDeveloper plugins by intercepting their license-validation HTTP calls to
`api.wpdeveloper.com` (via `pre_http_request`) and returning a synthetic EDD "valid/activated"
response for the centrally-stored key. Where cheaply discoverable, also seed the status option, but do
NOT rely on per-plugin option schemas.

**Rationale**:
- All 8 WPDeveloper plugins validate against the SAME backend (`api.wpdeveloper.com`, EDD Software
  Licensing: `edd_action=get_version` → `/get-latest-license`, `package_download`).
- License option names are built **dynamically** (`"{$prefix}_license_status"`) and the Pro builds are
  partly obfuscated/minified, so statically extracting every plugin's exact option key is unreliable
  (the prefix probe returned noise/garbage for several plugins). Interception sidesteps this entirely:
  the plugin's own code stores whatever it stores from the (synthetic) valid response.
- One interceptor covers both the modern shared-SDK plugins (essential-blocks-pro, notificationx-pro,
  wp-scheduled-posts-pro, betterdocs-pro — `WPDeveloper/Licensing/`) and the older bespoke-license
  plugins (essential-addons-elementor, embedpress-pro, betterlinks-pro, better-payment-pro).

**Alternatives rejected**:
- *Per-plugin option seeding only*: requires each plugin's exact, dynamically built option keys;
  fragile across versions and unobtainable from obfuscated builds; cron re-validation could flip it
  back to invalid.
- *wp-cli license-activate per plugin per instance*: the manual pain this feature removes; also makes
  real upstream activations that consume seats.

## D2 — Elementor Pro sharing: reuse the templately-multi pin, sourced from the central store

**Decision**: Reuse `elementor-multisite.php`'s technique, adapted from multisite to separate
instances:
- `add_filter('elementor_pro/license/api/use_home_url', fn => false)` and, during the license call,
  filter `site_url` to return the **primary** instance URL (from the central store) instead of
  `get_main_site_id()`/`get_site_url()`.
- Intercept `my.elementor.com/api/v1/license` (`pre_http_request`) to return activated for the central
  EL Pro key.
- Seed the EL Pro activation option (`_elementor_pro_license_v2_data` / `elementor_pro_license_key`)
  on install so secondaries report activated immediately.

**Rationale**: This is the exact, proven mechanism the user pointed to; the only change is the source
of the "primary" identity (central `$SANDBOX_HOME` store, since separate instances don't share a DB).
Pinning `site_url` makes every instance verify as the one primary site → one seat, many instances.

**Alternatives rejected**:
- *Activate each instance against Elementor for real*: consumes seats; defeats the purpose.

## D3 — Primary instance designation: first-to-activate, auto-recorded (clarified)

**Decision**: The first instance to activate Elementor Pro writes itself as the primary
(URL + instance name) into the central store; all later instances pin to it. If the recorded primary
is gone (not in the registry / unreachable), the next instance to activate takes over.

**Rationale**: Matches the clarified answer; zero manual designation; self-healing on primary
deletion. The central store holds only the non-secret primary URL/name.

**Alternatives rejected**: explicit developer designation (extra step), fixed config URL (must always
resolve). Both viable but the user chose auto-first.

## D4 — Key storage + how the in-container mu-plugin reads it

**Decision**: Secret keys (WPDeveloper, Elementor Pro) live GLOBALLY in the gitignored per-machine
secret store under `$SANDBOX_HOME` (chmod 600), set via `sb license set`. Non-secret shared state
(primary URL/name, key-present flags) lives in a central `$SANDBOX_HOME` JSON. At provision
(`up`/`apply`), `_provision` writes a per-instance `sandbox-licensing.json` into the instance's
gitignored `mu-plugins/` dir (same pattern as `sandbox-local-sources.json` and the snapshot
mu-plugin's injected token) containing the key(s) + primary URL; the merged mu-plugin reads it at
runtime.

**Rationale**: Mirrors the established secret-handling pattern (`bridge_token` in `sandbox.local.yml`;
secrets written into gitignored per-instance mu-plugin artifacts). Keeps keys out of the repo, out of
command output, out of snapshots (snapshots cover DB/uploads, not mu-plugins). Global (not
per-instance) because one key serves all instances.

**Alternatives rejected**:
- *Keys in the central non-secret JSON*: would put secrets in a plaintext shared file not marked as
  the secret store — violates the Secrets rule.
- *Pass keys via container env only*: env isn't reliably readable in-container (proven false for
  `SANDBOX_PLUGINS_HOST` in spec 011); a written file in the mounted mu-plugins dir is reliable.

## D5 — Install-from-licensed-API (US3), merged into on-demand

**Decision**: Extend `00-sandbox-ondemand.php`'s existing `plugins_api` + `upgrader_pre_download`
hooks: when a key is present, resolve a WPDeveloper plugin's download to `api.wpdeveloper.com`
(`edd_action=package_download` + license + item) and Elementor Pro to Elementor's update API under the
pinned license; fall back to the local-source path (today's behavior) when no key or the API is
unreachable.

**Rationale**: The on-demand plugin already owns this exact path (that's why we merge). Keeps one code
path for "where does this plugin's zip come from." EL Pro install-from-API is parity (local build is
the reliable default).

**Alternatives rejected**: a separate installer mu-plugin (duplicates the on-demand interception;
rejected by the merge decision).

## D6 — `sb license` command surface, no secret echo

**Decision**: `sb license set elementor|wpdeveloper <key>` (writes the secret store), `sb license
status` (shows which keys are set + the current primary, never the key value — masked), `sb license
clear [elementor|wpdeveloper]`. On `set`, optionally re-provision running instances so the new key
takes effect.

**Rationale**: Single documented surface (FR-007); consistent with the `sb` CLI; status shows
presence/masked, never the secret (SC-005).

**Alternatives rejected**: hand-editing the secret file (leak-prone via shell history; rejected in
clarify).

## D7 — Additive safety + interception scoping

**Decision**: The interceptor is registered only when a key is present, and matches ONLY the exact
license hosts (`api.wpdeveloper.com`, `my.elementor.com`). All other HTTP is untouched. With no key,
the mu-plugin behaves exactly as the current on-demand plugin. If `sandbox-licensing.json` is missing
or unreadable, it no-ops.

**Rationale**: FR-005 (additive, no regression) + safety — a license interceptor must never affect
unrelated requests or instances without Pro plugins.

## Open items carried to tasks (build-time, low-risk)

- Exact synthetic **response shapes** for `api.wpdeveloper.com` (EDD `check_license`/`get_version`
  fields: `success`, `license`, `expires`, `item_id`, `license_limit`, `site_count`) and
  `my.elementor.com/api/v1/license` — finalize by reading the SDK `Api.php` response handler + one
  observed real response during implementation.
- The WPDeveloper download item identifiers (item_id/slug) per plugin for US3 `package_download`.
- Whether any plugin caches a hard "invalid" that needs clearing on first activation.
