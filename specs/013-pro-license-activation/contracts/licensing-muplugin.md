# Contract: in-instance licensing behavior (merged into `00-sandbox-ondemand.php`)

**Feature**: 013-pro-license-activation · **Phase 1** · 2026-06-25

The on-demand mu-plugin gains a licensing layer that reads
`mu-plugins/sandbox-licensing.json` and activates Pro plugins. Strictly scoped, additive, no-op
without keys.

## Inputs (read at runtime)
- `sandbox-licensing.json`: `{ wpdeveloper_key?, elementor_pro_key?, elementor_primary_url?, is_primary }`.
- Absent file / absent keys → the licensing layer registers nothing (today's on-demand behavior).

## WPDeveloper activation (when `wpdeveloper_key` present)
- Register a `pre_http_request` handler matching ONLY `api.wpdeveloper.com`.
- For license check/activation requests carrying the central key, return a synthetic EDD
  "valid/activated" response (`success: true`, `license: valid`, sane `expires`, `item_*`).
- Optionally seed known `*_license_status = valid` options for instant activation.
- Effect: all 8 WPDeveloper plugins (shared-SDK + bespoke) report licensed without manual activation.

### Guarantees
- A WPDeveloper plugin on the instance reports licensed/activated with no manual step. (FR-001, SC-001)
- Requests to hosts other than `api.wpdeveloper.com` are untouched. (FR-005, D7)

## Elementor Pro activation (when `elementor_pro_key` present)
- `add_filter('elementor_pro/license/api/use_home_url', fn => false)` and, during the license API
  call, filter `site_url` to return `elementor_primary_url` (skip if `is_primary`).
- Register a `pre_http_request` handler matching ONLY `my.elementor.com` (`/api/v1/license`) returning
  activated for the central key.
- Seed `_elementor_pro_license_v2_data` / `elementor_pro_license_key` so the instance reports activated
  on install.
- If `elementor_primary_url` is empty AND this instance activates, it records itself as primary in the
  central store (first-to-activate).

### Guarantees
- A secondary instance reports Elementor Pro activated, pinned to the primary's identity — one seat,
  many instances. (FR-002, SC-002)
- The recorded primary going away → the next activator takes over; secondaries follow the current
  primary. (FR-008)
- Requests to hosts other than `my.elementor.com` are untouched. (D7)

## Install-from-API (US3, extends existing on-demand hooks)
- When a key is present, `plugins_api` + `upgrader_pre_download` resolve a WPDeveloper plugin to
  `api.wpdeveloper.com` (`edd_action=package_download` + key + item) and Elementor Pro to Elementor's
  update API under the pinned license.
- No key, or API unreachable → fall back to the local-source path (today's behavior). (FR-006, SC-006)

## Cross-cutting invariants
- No key set → byte-for-byte today's behavior; instances without Pro plugins are unaffected. (FR-005, SC-004)
- The key value is never written to a tracked file, command output, or snapshot. (FR-004, SC-005)
- Reads only; safe to re-run / re-provision (idempotent).
