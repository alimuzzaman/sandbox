# Implementation Plan: Cross-Instance Pro License Activation & Sharing

**Branch**: `013-pro-license-activation` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/013-pro-license-activation/spec.md`

## Summary

Activate and share Pro plugin licenses across separate sandbox instances from a single centrally
stored key, with zero manual per-instance activation. The mechanism — validated against the actual
plugin code — is **license-API HTTP interception** plus **site-identity pinning**, merged into the
existing `00-sandbox-ondemand.php` mu-plugin:

- **WPDeveloper family (8 plugins)** all validate against one EDD-style backend (`api.wpdeveloper.com`).
  A single `pre_http_request` interceptor returns a synthetic "valid/activated" response for the
  central WPDeveloper key, so every plugin reports licensed regardless of its (dynamically built,
  partly obfuscated) option names — uniform, no per-plugin option schema required.
- **Elementor Pro** uses the proven `elementor-multisite.php` method: filter
  `elementor_pro/license/api/use_home_url` → false and pin `site_url` to the auto-recorded **primary**
  instance during license calls, intercept `my.elementor.com/api/v1/license`, and seed the activation
  option (`_elementor_pro_license_v2_data`) so secondaries report activated on install — one seat,
  many instances.

Keys live only in the gitignored per-machine secret store; non-secret shared state (the primary
instance URL, which key is set) lives in a central `$SANDBOX_HOME` file read by every instance's
mu-plugin. A new `sb license` command manages keys without echoing them. With no key set, behavior is
byte-for-byte today's (local-source install, manual activation).

## Technical Context

**Language/Version**: PHP 7.4+ (the in-instance interceptor/seeder, merged into
`00-sandbox-ondemand.php`) + Python 3 (the `sb license` command + `_provision` wiring + central store
resolution, consistent with the existing `sandbox/` package).

**Primary Dependencies**: WordPress HTTP API (`pre_http_request`, `http_response`) for interception;
Elementor Pro's `elementor_pro/license/api/use_home_url` filter + `site_url` filter (proven in
`templately-multi/wp-content/mu-plugins/elementor-multisite.php`); the existing on-demand mu-plugin
(`plugins_api` + `upgrader_pre_download`) for install-from-API (US3); the existing secret-store
helpers (`sandbox.local.yml` / `.env.local`, the `save_local_*` / `_local_yaml` pattern used for
`bridge_token`).

**Storage**: Two tiers, mirroring the existing pattern. (a) **Secrets** — the WPDeveloper key and
Elementor Pro key in the gitignored per-machine secret store (`$SANDBOX_HOME`, chmod 600), GLOBAL
(one set for all instances). (b) **Non-secret shared state** — a central `$SANDBOX_HOME` JSON
(primary instance URL, primary instance name, key-present flags) read by every instance. At
provision, a per-instance `sandbox-licensing.json` is written into the instance's gitignored
`mu-plugins/` dir (same mechanism as `sandbox-local-sources.json`) carrying the key(s) + primary URL
for the in-container mu-plugin to read.

**Testing**: Live-stack verification per constitution — boot two instances with Pro plugins, set a
key via `sb license`, confirm each plugin reports licensed/activated and Elementor Pro verification
resolves to the primary; confirm no key → unchanged; confirm no secret leaks (grep). Plus the
sandbox's existing Python harness for the `sb license` command surface.

**Target Platform**: The in-instance mu-plugin runtime (nginx/fpm, apache, litespeed, herd) + the
host `sb` CLI / `_provision` writers.

**Project Type**: Sandbox tooling enhancement — one mu-plugin (merged), one new `sb` command module,
provisioning wiring, central store helpers.

**Performance Goals**: Interception adds no user-visible latency (synthetic responses are local; real
upstream calls are avoided for the shared key). Activation state present on first boot (no wait for a
remote check).

**Constraints**: Keys MUST never be committed, echoed, or snapshotted (constitution Secrets rule).
The interceptor MUST be strictly scoped to the two license hosts and MUST no-op when no key is set
(additive). It MUST degrade to today's behavior if the secret store is unreadable in-container.

**Scale/Scope**: 8 WPDeveloper plugins + Elementor Pro; N instances sharing one key each; one primary
per Elementor license.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. No instance-model change. The "primary" is an
  auto-recorded designation in the central store, not a new global instance; commands still resolve
  per project.
- **II. The Registry Is the Single Source of Truth** — PASS. Licensing state lives in a separate
  central store; it does not alter project→instance resolution. The primary-instance pointer
  references a registered instance by its canonical identity.
- **III. Single Entry File, Modular Package** — PASS. `sb` stays the single entry; `sb license` is a
  new self-contained command module in `sandbox/commands/` registered via the registry. No new entry
  file.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS (enforced). SC-001..SC-006 are
  live checks (plugins reporting activated; install sourced from API; no secret leak); quickstart.md
  encodes them.
- **V. Idempotency and Docs-With-Code** — PASS. `sb license set` and provisioning are re-runnable;
  ships with CLAUDE.md (a new gotcha on licensing + secret handling), the on-demand SKILL/docs, and a
  `memory/plugin-behavior/` note on the WPDeveloper EDD backend + Elementor pin.
- **VI. Feature Parity Before Removal** — PASS. Additive; the on-demand local-source path is preserved
  as the no-key default; nothing removed.

**Secrets gate (Additional Constraints):** license keys are secrets — stored only in the gitignored
secret store, never echoed by `sb license`, never written to a tracked file or snapshot. This is the
highest-risk area and is called out as a first-class requirement (FR-004, SC-005), verified by grep
in quickstart.

**Gate result: PASS — no violations; Complexity Tracking not required.**

## Project Structure

### Documentation (this feature)

```text
specs/013-pro-license-activation/
├── plan.md           # This file
├── research.md       # Phase 0 — the interception/pin/seed decisions, endpoints, response shapes
├── data-model.md     # Phase 1 — central store + secret + primary designation + seed entities
├── quickstart.md     # Phase 1 — live validation (two instances, activation, no-leak)
├── contracts/
│   ├── sb-license-cli.md       # the sb license command surface
│   └── licensing-muplugin.md   # the in-instance interceptor/seeder behavior contract
└── tasks.md          # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
sandbox/core/_provision.py        # CHANGED: extend the on-demand mu-plugin template with the
                                  #   licensing interceptor/seeder; add _write_licensing_state()
                                  #   (per-instance sandbox-licensing.json from central store+secrets)
sandbox/commands/license.py       # NEW: `sb license set|status|clear` (writes secret store; no echo)
sandbox/core/_licensing.py        # NEW (or fold into _bridge/_config): central store + secret
                                  #   read/write helpers (global key store + primary designation)
sandbox/registry.py               # CHANGED: register the license command module
CLAUDE.md                         # CHANGED: new gotcha — cross-instance Pro licensing + secrets
memory/plugin-behavior/
└── pro-license-activation.md     # NEW: WPDeveloper EDD backend, Elementor pin, interception model
```

**Structure Decision**: Merge the in-instance logic into `00-sandbox-ondemand.php` (per the clarified
decision) since it already owns the install/`upgrader_pre_download` path needed for install-from-API,
and add a thin host-side `sb license` command + central-store helpers. The interceptor is a small,
strictly-scoped `pre_http_request` handler; the EL Pro pin reuses the templately-multi filters
adapted to read the central primary URL instead of `get_main_site_id()`.

## Complexity Tracking

> No constitution violations — section intentionally empty.
