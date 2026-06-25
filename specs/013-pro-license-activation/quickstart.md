# Quickstart / Live Validation: Cross-Instance Pro License Activation

**Feature**: 013-pro-license-activation · **Phase 1** · 2026-06-25

Per Constitution IV, "done" = these live checks pass against running instances, evidence captured, and
no secret leaks. Use two instances that have Pro plugins (e.g. `templately-fsi-rewrite` + a second
project) with Elementor Pro and several WPDeveloper pro plugins active.

## Prerequisites
- Two booted instances, each with Elementor Pro + ≥2 WPDeveloper pro plugins installed/active.
- A valid WPDeveloper key and Elementor Pro key (the developer's own).

## Check 1 — Set keys with no leakage (US4, SC-005)
- `sb license set wpdeveloper <key>` and `sb license set elementor <key>`.
- `sb license status` → both show `set` (masked), primary `none` initially.

**Expected**: command output shows no full key value. Then grep the repo, the command transcript, and
a fresh snapshot for the key value → **zero** matches.

## Check 2 — One WPDeveloper key licenses all WPDeveloper plugins on both instances (US1, SC-001)
- Re-provision/apply both instances; on each, query each WPDeveloper pro plugin's license/activation
  state (option or admin status).

**Expected**: every WPDeveloper pro plugin reports licensed/activated on BOTH instances, with zero
manual activation actions.

## Check 3 — One Elementor Pro activation shared, first-to-activate primary (US2, SC-002, FR-008)
- On instance A, trigger Elementor Pro activation → A becomes the recorded primary (`sb license status`
  shows A's URL).
- Boot/apply instance B; check Elementor Pro status on B.

**Expected**: B reports Elementor Pro activated; B's license verification resolves to A's site
identity (pinned), consuming no extra seat. Destroy A → next instance to activate becomes primary;
others follow.

## Check 4 — Additive: no key → unchanged (SC-004)
- `sb license clear` (both); re-provision an instance.

**Expected**: Pro plugins revert to today's behavior (unlicensed/manual); instances without Pro
plugins boot unaffected; schema/behavior byte-identical to pre-feature.

## Check 5 — Install from licensed API when key present (US3, SC-006)
- With the WPDeveloper key set, install a WPDeveloper pro plugin not present locally.

**Expected**: it is fetched from `api.wpdeveloper.com` using the key. Remove the key → the same install
falls back to local source. API unreachable with key set → graceful local fallback (no broken
install).

## Check 6 — Interception is strictly scoped (D7)
- With keys set, confirm requests to hosts other than `api.wpdeveloper.com` / `my.elementor.com` are
  unaffected (e.g. a normal `wp_remote_get` to example.com behaves normally).

**Expected**: only the two license hosts are intercepted; all other HTTP is untouched.

## Check 7 — Unblocks spec 012 Pro coverage (SC-007)
- With Pro plugins now activated across instances, run the spec-012 schema dump.

**Expected**: Elementor Pro + WPDeveloper Pro widgets/blocks now resolve at full fidelity in the
catalog (no longer reduced for lack of activation).

## Evidence to capture
Per check: the `sb license status` output (masked), the per-plugin activation state, the grep proving
no key leak, and the install-source proof. A check that can't be reproduced live blocks done.
