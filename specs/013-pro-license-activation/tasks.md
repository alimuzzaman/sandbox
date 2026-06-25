# Tasks: Cross-Instance Pro License Activation & Sharing

**Feature**: 013-pro-license-activation · **Input**: design docs in `specs/013-pro-license-activation/`

**Files of change**: `sandbox/core/_provision.py` (extend `00-sandbox-ondemand.php` template +
`_write_licensing_state`), `sandbox/commands/license.py` (new `sb license`), `sandbox/core/_licensing.py`
(central store + secret helpers), `sandbox/registry.py` (register command), docs.

**Verification model (Constitution IV + Secrets gate)**: a behavior task is "done" only when its live
quickstart check passes against running instances, evidence captured, AND no key value leaks (grep).

## Phase 1: Setup

- [x] T001 Cut working branch `013-pro-license-activation` from current HEAD (push upstream as itself; pause for user approval before any push).
- [ ] T002 Confirm a two-instance test bed: `templately-fsi-rewrite` + a second project, each with Elementor Pro + ≥2 WPDeveloper pro plugins installed/active; snapshot both before changes. Have the developer's WPDeveloper + Elementor Pro keys available locally (not in the repo).

## Phase 2: Foundational (blocking prerequisites)

Shared by all stories: the central store, secret helpers, and the per-instance seed-write are required
before any activation behavior works.

- [x] T003 Add `sandbox/core/_licensing.py`: central licensing-state read/write under `$SANDBOX_HOME` (non-secret JSON: `*_present`, `elementor_primary_url/instance`) + secret read/write helpers for the keys in the gitignored secret store (chmod 600), reusing the spec-009 base seam + the `bridge_token` secret pattern. No echo of secret values. (D4, data-model: License secret + Central licensing state)
- [x] T004 Add `_write_licensing_state(instance)` in `sandbox/core/_provision.py`: render a per-instance `mu-plugins/sandbox-licensing.json` from the central store + secrets (`wpdeveloper_key?`, `elementor_pro_key?`, `elementor_primary_url?`, `is_primary`); always written (even empty) so a cleared key reverts behavior; gitignored runtime only. Wire it into the up/apply provisioning sequence next to `_write_ondemand_muplugin`. (D4, data-model: Per-instance licensing seed)
- [x] T005 Extend the `00-sandbox-ondemand.php` template in `sandbox/core/_provision.py` with a licensing layer scaffold: read `sandbox-licensing.json`; register NOTHING when absent/empty (additive no-op); strictly host-scoped `pre_http_request` registration gated on key presence. (FR-005, D7)

## Phase 3: User Story 1 — One WPDeveloper key licenses all WPDeveloper plugins (Priority: P1) 🎯 MVP

**Goal**: With one central WPDeveloper key, every installed WPDeveloper pro plugin reports
licensed/activated on every instance, zero manual activation.

**Independent test**: set the key; boot two instances; every WPDeveloper pro plugin reports activated
on both, no manual steps.

- [x] T006 [US1] In the on-demand template, add the WPDeveloper interceptor: a `pre_http_request` handler matching ONLY `api.wpdeveloper.com` that returns a synthetic EDD valid/activated response for the central key (finalize the response shape — `success`,`license:valid`,`expires`,`item_*` — against `WPDeveloper/Licensing/Api.php`). (FR-001, D1, contracts/licensing-muplugin)
- [x] T007 [US1] Add best-effort option seeding for instant activation where discoverable (status options → `valid`); never depend on per-plugin option names (interceptor is the guarantee). (D1)
- [x] T008 [US1] Add `sb license set wpdeveloper <key>` path end-to-end (writes secret store via `_licensing.py`, sets `wpdeveloper_present`, re-provisions running instances). (FR-007, depends T003)
- [ ] T009 [US1] Live-verify quickstart Check 2 on BOTH instances: each WPDeveloper pro plugin reports licensed/activated, zero manual actions (the headline SC-003 metric: down from one activation per plugin per instance). Capture per-plugin state. (SC-001, SC-003)

**Checkpoint**: US1 delivers the core value — one key licenses the whole WPDeveloper family across instances.

## Phase 4: User Story 2 — One Elementor Pro activation shared (Priority: P1)

**Goal**: One EL Pro key; first instance to activate becomes primary; all others share that activation.

**Independent test**: activate on A (becomes primary); B reports activated, pinned to A's identity, no
extra seat.

- [ ] T010 [US2] Add the Elementor Pro pin to the template: `add_filter('elementor_pro/license/api/use_home_url', fn=>false)` + `site_url` → `elementor_primary_url` during license calls (skip when `is_primary`), adapted from `elementor-multisite.php`. (FR-002, D2)
- [ ] T011 [US2] Add the EL Pro interceptor (`pre_http_request` on `my.elementor.com` `/api/v1/license`) + seed `_elementor_pro_license_v2_data` / `elementor_pro_license_key` so secondaries report activated on install. (FR-002, D2)
- [ ] T012 [US2] First-to-activate primary recording: when `elementor_primary_url` is empty and this instance activates, write itself as primary to the central store; on a stale primary (gone from registry), let the next activator take over. (FR-008, D3)
- [ ] T013 [US2] Add `sb license set elementor <key>` path (secret store + `elementor_present` + re-provision). (FR-007)
- [ ] T014 [US2] Live-verify quickstart Check 3: A becomes primary, B reports EL Pro activated pinned to A; destroy A → next activator takes over. Capture evidence. (SC-002, FR-008)

**Checkpoint**: US1 + US2 = both Pro families licensed/shared across instances.

## Phase 5: User Story 3 — Install Pro plugins from licensed API when key present (Priority: P2)

**Goal**: With a key, install pulls the licensed build from the vendor API; no key → local source.

**Independent test**: with key, install a WPDeveloper plugin from API; without key, same install uses
local source.

- [ ] T015 [US3] Extend the on-demand `plugins_api` + `upgrader_pre_download` hooks: when the WPDeveloper key is present, resolve a WPDeveloper plugin download to `api.wpdeveloper.com` (`edd_action=package_download` + key + item id); for Elementor Pro, to Elementor's update API under the pinned license. (FR-006, D5)
- [ ] T016 [US3] Fallback: no key OR API unreachable → existing local-source path (today's behavior); never leave a broken install. (FR-006)
- [ ] T017 [US3] Live-verify quickstart Check 5: key set → install sourced from API; key cleared → local source; API unreachable → graceful local fallback. (SC-006)

## Phase 6: User Story 4 — Manage keys safely, zero leakage (Priority: P2)

**Goal**: `sb license` sets/updates/clears/inspects keys + primary, never echoing a value.

**Independent test**: set a key; grep repo/output/snapshots → zero matches; instances still pick it up.

- [x] T018 [US4] Implement `sandbox/commands/license.py`: `set <family> <key>`, `status` (masked + current primary), `clear [family]`; register in `sandbox/registry.py`. Per `contracts/sb-license-cli.md`; never print a key value. (FR-007)
- [ ] T019 [US4] Live-verify quickstart Check 1 + Check 6: `sb license status` masks values; grep repo, command transcript, and a fresh snapshot for the key → zero matches; interception is strictly scoped to the two license hosts (other HTTP untouched). (SC-005, D7)

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T020 [P] Docs-with-code: update the on-demand SKILL/docs + `CLAUDE.md` with a new gotcha — cross-instance Pro licensing, the central store/secret split, `sb license`, and the strict interception scope. (Constitution V)
- [ ] T021 [P] Add `memory/plugin-behavior/pro-license-activation.md` — the WPDeveloper EDD backend (`api.wpdeveloper.com`) shared by all 8 plugins, the Elementor Pro pin (`use_home_url`+`site_url`, options, `my.elementor.com`), and the interception model.
- [ ] T022 Live-verify: (a) **single-family independence** — an instance with only WPDeveloper plugins (no EL Pro) and one with only EL Pro each activate their family correctly (FR-010); (b) Check 4 (additive: no key → byte-for-byte today's behavior; non-Pro instances unaffected); (c) Check 7 (unblocks spec 012 Pro coverage — Pro widgets/blocks now resolve full). Restore the T002 snapshots; assemble the evidence bundle (no-leak proof included). (FR-010, SC-004, SC-007)

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T005)** → stories.
- **US1 (T006–T009)** and **US2 (T010–T014)** both depend on Foundational; US1 is the MVP. They are
  largely independent (different hosts/plugins) but both edit the on-demand template — sequence the
  template edits.
- **US3 (T015–T017)** depends on the keys existing (T008/T013) and the on-demand hooks.
- **US4 (T018–T019)** depends on T003 (secret store) and is the management surface for all stories;
  T018 can start right after T003.
- **Polish (T020–T022)** after the stories; T020/T021 are `[P]` (separate docs files).

## Parallel Opportunities

- T020 + T021 (separate docs files) run in parallel once behavior is final.
- US1 and US2 verification (T009, T014) are independent once their code lands.
- Within `00-sandbox-ondemand.php`, the interceptor edits (T006, T010, T011, T015) are sequential
  (same file) — do not parallelize.

## Implementation Strategy

- **MVP = Phase 1 + 2 + US1 (T001–T009)**: one WPDeveloper key → whole family licensed across
  instances. Highest value, fewest moving parts (single host interception).
- **Increment 2 = US2 (T010–T014)**: Elementor Pro sharing (pin + primary).
- **Increment 3 = US3 + US4 + Polish**: install-from-API, the `sb license` surface, docs, and the
  no-leak + additive verification.
- Secret-safety is non-negotiable at every step: never echo, commit, or snapshot a key; verify with
  grep (T019) before declaring done.

## Format Validation

- All tasks use `- [ ] T### …` with file paths; story tasks carry `[US1]/[US2]/[US3]/[US4]`; setup,
  foundational, polish carry no story label; `[P]` only on independent-file tasks.
