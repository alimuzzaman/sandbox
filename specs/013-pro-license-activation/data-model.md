# Data Model: Cross-Instance Pro License Activation & Sharing

**Feature**: 013-pro-license-activation · **Phase 1** · 2026-06-25

Two stores (mirroring the existing secret/non-secret split) plus per-instance provisioned state.

## Entity: License secret (global, secret store)

The actual key values — stored only in the gitignored per-machine secret store under `$SANDBOX_HOME`
(chmod 600). Global: one set for all instances.

| Field | Type | Notes |
|-------|------|-------|
| `wpdeveloper_key` | string (secret) | One key valid across the WPDeveloper plugin family. Optional. |
| `elementor_pro_key` | string (secret) | The Elementor Pro license key. Optional. |

**Rules**: never committed, echoed, or snapshotted (FR-004/SC-005). Absent key → that family behaves
as today. Written only via `sb license set`.

## Entity: Central licensing state (global, non-secret)

Shared, non-secret coordination data in a central `$SANDBOX_HOME` JSON, readable by all instances.

| Field | Type | Notes |
|-------|------|-------|
| `wpdeveloper_present` | bool | Whether a WPDeveloper key is set (presence, not value). |
| `elementor_present` | bool | Whether an Elementor Pro key is set. |
| `elementor_primary_url` | string \| null | The auto-recorded primary instance's site URL (the pin target). |
| `elementor_primary_instance` | string \| null | The primary instance's canonical name (for staleness detection). |
| `updated_at` | timestamp | Last change. |

**State transitions** (`elementor_primary_*`): `null` → set to the first instance that activates EL
Pro → replaced by the next activator if the recorded primary is gone from the registry. Monotonic per
activation event; never points at a destroyed instance for long.

## Entity: Per-instance licensing seed (provisioned)

Written into each instance's gitignored `mu-plugins/sandbox-licensing.json` at `up`/`apply` from the
two stores above; read by the merged on-demand mu-plugin at runtime.

| Field | Type | Notes |
|-------|------|-------|
| `wpdeveloper_key` | string \| null | The key (secret, in gitignored runtime) for the interceptor. |
| `elementor_pro_key` | string \| null | The key for the EL Pro interceptor/seed. |
| `elementor_primary_url` | string \| null | The URL to pin `site_url` to during EL Pro license calls. |
| `is_primary` | bool | Whether THIS instance is the recorded primary (so it doesn't pin to itself). |

**Rules**: absent file or absent keys → mu-plugin no-ops (today's behavior). Lives only under
gitignored `runtime/wp-<instance>/`.

## Entity: Activation interception

The runtime behavior, not persisted — the `pre_http_request` handler.

| Field | Type | Notes |
|-------|------|-------|
| `target_hosts` | enum set | `api.wpdeveloper.com`, `my.elementor.com` only — all other HTTP untouched. |
| `synthetic_response` | object | EDD/Elementor "valid/activated" payload returned for the central key. |
| `active_when` | predicate | Only when the corresponding key is present in the seed. |

## Entity: WPDeveloper plugin family (reference)

The set this feature licenses; two client tiers behind one backend.

| Plugin | License client | 
|--------|----------------|
| essential-blocks-pro, notificationx-pro, wp-scheduled-posts-pro, betterdocs-pro | shared `WPDeveloper/Licensing/` SDK |
| essential-addons-elementor, embedpress-pro, betterlinks-pro, better-payment-pro | bespoke per-plugin license class |
| (all of the above) | validate against `api.wpdeveloper.com` (EDD-SL) — the single interception point |

## Entity: `sb license` command surface

| Subcommand | Effect | Secret-safe |
|------------|--------|-------------|
| `set elementor\|wpdeveloper <key>` | write the key to the secret store; optionally re-provision running instances | input not stored in history-visible output; value never echoed back |
| `status` | show which keys are present (masked) + current primary | shows presence + masked value only |
| `clear [family]` | remove key(s) from the secret store | confirms without echoing |

## Entity: Elementor Pro license options (reference, seeded/pinned)

| Option | Role |
|--------|------|
| `elementor_pro_license_key` | the key |
| `_elementor_pro_license_v2_data` / `_elementor_pro_license_data` | activation/validation state mirrored so secondaries read activated |
| filter `elementor_pro/license/api/use_home_url` → false + `site_url` pin | makes verification resolve to the primary URL |
