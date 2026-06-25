# Feature Specification: Cross-Instance Pro License Activation & Sharing

**Feature Branch**: `013-pro-license-activation`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "A mu-plugin + central license store that activates and shares Pro plugin licenses (Elementor Pro, WPDeveloper pro plugins) across separate sandbox instances from a single centrally-stored key, so Pro plugins come up licensed/activated on every instance without manual re-activation. Reuse the elementor-multisite license-pin method, adapted to separate instances via a central $SANDBOX_HOME store. Dev/staging only; keys are secrets."

## Clarifications

### Session 2026-06-25

- Q: Where do the license keys and shared state live? → A: A central store under `$SANDBOX_HOME`, shared by all instances; the actual secret keys live in the per-machine secret store (`sandbox.local.yml` / `.env.local`, chmod 600), never committed/echoed. The per-instance mu-plugin reads both at runtime, like the existing bridge token.
- Q: Does this assume the developer owns the licenses? → A: Yes. This is dev/staging-only tooling for a plugin developer (WPDeveloper) using licenses they own across their own local instances; the sandbox is explicitly dev-only. Seat-compliance enforcement is out of scope.
- Q: How is the Elementor Pro "primary" instance designated? → A: The first instance to activate Elementor Pro auto-becomes primary and is recorded in the central store; all later instances pin to it. No manual designation. If the primary is destroyed, the next instance to activate takes over.
- Q: How does the developer manage the central license keys? → A: A dedicated `sb license` command (e.g. `sb license set elementor <key>`, plus list/clear/status) that writes to the gitignored secret store and never echoes the key value.
- Q: Should WPDeveloper use a managed license key? → A: NO. Verified live: the WPDeveloper licensed update API gates the actual plugin download (`package_download`) on a real upstream site activation (which needs 2FA + consumes a seat), so a key buys nothing in the sandbox — the local force-activation works identically with or without one. WPDeveloper is therefore **keyless**: its pro plugins are force-activated in-instance (no key, no `sb license` family). Only vendors that genuinely need a managed secret (e.g. Elementor) remain key-managed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - WPDeveloper pro plugins force-activated keylessly on every instance (Priority: P1)

Every sandbox instance comes up with all installed WPDeveloper pro plugins already activated — with
**no license key at all** — so they run without nag screens and with pro features unlocked, no
per-plugin, per-instance manual activation.

**Why this priority**: This is the core pain — re-activating many WPDeveloper pro plugins on every
throwaway instance by hand. A key buys nothing here (the licensed download is 2FA/activation-gated;
see Clarifications), so the value is the keyless force-activation: install a WPDeveloper pro plugin →
it's active.

**Independent Test**: Boot an instance with several WPDeveloper pro plugins and NO key set; confirm
each plugin reports licensed/activated with zero manual steps; confirm it stays valid on a live
re-check (no 2FA prompt).

**Acceptance Scenarios**:

1. **Given** no key is set, **When** an instance boots with a WPDeveloper pro plugin active, **Then**
   that plugin reports licensed/activated without any key entry or 2FA.
2. **Given** several WPDeveloper pro plugins are active across instances, **When** they boot, **Then**
   all of them are force-activated, in any context (front-end, admin, REST).
3. **Given** a WPDeveloper plugin runs a live license check, **When** it calls the WPDeveloper backend,
   **Then** it receives a synthetic `valid` (never `required_otp`) — the 2FA/seat path is bypassed.
4. **Given** `define('SANDBOX_WPD_ACTIVATE_OFF', true)`, **When** an instance boots, **Then** WPDeveloper
   plugins are left in their real (unlicensed) state — an explicit escape hatch.

---

### User Story 2 - One Elementor Pro activation shared across all instances (Priority: P1)

A developer sets a single Elementor Pro key. The first instance to connect becomes the licensed
"primary"; every other instance shares that one activation — Elementor Pro reports activated on all of
them — without consuming additional license seats or each instance calling the activation API as
itself.

**Why this priority**: Elementor Pro is seat-limited and re-activating per throwaway instance is
costly/impractical; sharing one activation is the explicit goal. Equal P1 with US1.

**Independent Test**: Set the Elementor Pro key; connect a first instance (becomes primary); boot a
second instance; confirm Elementor Pro reports activated on the second instance and its license
verification resolves to the primary instance's identity, not its own.

**Acceptance Scenarios**:

1. **Given** an Elementor Pro key and a designated primary instance, **When** a secondary instance
   performs (or would perform) Elementor Pro license verification, **Then** the verification is pinned
   to the primary instance's site identity and the secondary reports activated.
2. **Given** a fresh secondary instance, **When** it is installed/booted, **Then** its Elementor Pro
   activation state is pre-set (mirrored from the central store) so it is activated on install without
   its own activation call.
3. **Given** no Elementor Pro key is set, **When** an instance boots, **Then** Elementor Pro behaves
   exactly as today (unlicensed until manually connected) — no breakage.

---

### User Story 3 - Install Pro plugins from their licensed API when a key is present (Priority: P2)

When a key is set, installing a Pro plugin pulls the licensed build from its vendor's update API
(authenticated with the key) instead of the local source — for **both** WPDeveloper plugins (the
WPDeveloper API) and Elementor Pro (Elementor's update API, using the pinned/primary license). With no
key, it falls back to the existing local-source behavior.

**Why this priority**: Lets instances install the real licensed/distributed builds (incl. versions not
checked out locally), but the local-source path (spec 010) already works, so this is an enhancement
rather than the core fix. For Elementor Pro specifically, install-from-API is parity/refresh (the
local build is the reliable default).

**Independent Test**: With a key set, install a WPDeveloper pro plugin and confirm it came from the
licensed API; remove the key and confirm the same install falls back to the local source.

**Acceptance Scenarios**:

1. **Given** a WPDeveloper key is set, **When** a WPDeveloper plugin is installed, **Then** it is
   fetched from the licensed WPDeveloper API using the key.
2. **Given** an Elementor Pro key is set, **When** Elementor Pro is installed/updated, **Then** it can
   be fetched from Elementor's update API under the pinned primary license (local build otherwise).
3. **Given** no key is set, **When** the same plugin is installed, **Then** it is served from the
   local source exactly as the existing on-demand behavior.
4. **Given** the licensed API is unreachable, **When** a key is set, **Then** installation
   deterministically falls back to the local source; only if no local source exists does it fail with
   a clear error — never a broken install.

---

### User Story 4 - Manage central keys safely, with no secret leakage (Priority: P2)

A developer can set, update, and clear the central license keys and designate the primary instance
through a single documented mechanism, and at no point is a key written to a tracked file, printed to
output, or stored in a snapshot/commit.

**Why this priority**: The feature handles secrets; safe management is a hard requirement, but it is a
supporting capability around US1/US2 rather than the headline value.

**Independent Test**: Set a key via the management mechanism; grep the repo, logs, and any committed
artifact to confirm the key value never appears; confirm the instances still pick it up.

**Acceptance Scenarios**:

1. **Given** a developer sets a key, **When** they inspect the repo / command output / snapshots,
   **Then** the key value appears in none of them (only in the gitignored per-machine secret store).
2. **Given** keys are set, **When** a developer lists/clears them, **Then** the management surface
   confirms the action without echoing the secret value.

---

### Edge Cases

- **No keys set**: every Pro behavior is exactly as today (additive feature; nothing licensed
  automatically).
- **Instance lacks the Pro plugin**: the mu-plugin no-ops for that plugin (never errors on a missing
  plugin).
- **Primary instance destroyed/changed**: the auto-recorded primary for Elementor Pro sharing is
  detected as stale and the next instance to activate takes over; secondary instances follow the
  current primary rather than being left unverifiable.
- **Key invalid/expired upstream**: license verification fails honestly; the feature must not fake
  success in a way that corrupts plugin state — it surfaces the failure (especially for US3 API
  installs), while seat-sharing for an otherwise-valid key still works for already-mirrored options.
- **Secret store missing/unreadable in-container**: the mu-plugin degrades to today's behavior (no
  activation) rather than erroring.
- **WPDeveloper plugins with differing license mechanisms**: a single key must map onto each plugin's
  own activation/license storage; plugins that don't follow the common mechanism are reported as
  unhandled rather than silently assumed activated.
- **Mixed presence**: an instance may have only Elementor Pro, only WPDeveloper plugins, or both; each
  family activates independently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every installed WPDeveloper pro plugin MUST be force-activated on every instance with NO
  license key — no per-plugin or per-instance manual activation, and no 2FA. The activation MUST work
  in every request context (front-end, admin, REST) by deriving each plugin's license-status option
  from the active-plugin slug (not from context-dependent plugin constants).
- **FR-002**: A single centrally-stored Elementor Pro license key MUST be shared across all instances
  so that secondary instances report activated by pinning license verification to one designated
  primary instance's site identity (one seat, many instances).
- **FR-003**: On install/boot, each instance MUST come up with the applicable Pro plugins already in
  an activated/licensed state (seeded/mirrored from the central store) without a manual activation
  step.
- **FR-004**: License keys MUST be stored only in the per-machine secret store (gitignored,
  restricted permissions) and read at runtime; they MUST NEVER be committed, echoed to output, or
  captured in snapshots. Shared non-secret state (e.g. the primary instance URL, mirrored activation
  options) MUST live in a central location under the per-user base shared by all instances.
- **FR-005**: The feature MUST be additive: with no key set, every Pro plugin behaves exactly as
  today (local-source install, manual/unlicensed state); the change MUST never break instances that
  lack the Pro plugins.
- **FR-006**: Install path for Pro plugins is **local source** (the existing on-demand mechanism).
  Installing from the WPDeveloper licensed API is NOT used: verified live, `package_download` is gated
  on a real upstream site activation (2FA/seat), which the keyless bypass deliberately avoids — so the
  download 401s. WPDeveloper update *checks* (`get_version`) still pass through unmodified. Elementor
  Pro install-from-API is likewise best-effort/parity with the local build as the default.
- **FR-007**: For key-managed vendors (Elementor), a developer MUST be able to set, update, clear, and
  inspect the central key + primary designation through a dedicated `sb license` command (`sb license
  set elementor <key>`, `sb license status|clear`) that writes to the gitignored secret store and
  never echoes secret values. WPDeveloper has NO key family (keyless force-activation); `sb license
  status` reports it as keyless.
- **FR-008**: The first instance to activate Elementor Pro MUST auto-become the recorded primary for
  sharing; all later instances MUST pin to it. The system MUST detect when the primary is gone and let
  the next instance to activate take over, so sharing keeps working without manual designation.
- **FR-009**: The per-instance activation logic MUST be delivered by **merging into the existing
  on-demand mu-plugin (`00-sandbox-ondemand.php`)** — extending it with licensing/activation rather
  than shipping a separate file — provisioned into each instance on boot/apply like the other sandbox
  mu-plugins, reading the central store + secrets at runtime.
- **FR-010**: Each Pro family (Elementor Pro, WPDeveloper) MUST activate independently; an instance
  with only one family present MUST still activate that family correctly.

### Key Entities *(include if data involved)*

- **Central license store**: The shared, per-user record of license configuration — WPDeveloper key
  reference, Elementor Pro key reference, the designated primary instance identity, and mirrored
  activation options — readable by every instance's mu-plugin.
- **Secret entry**: A license key value, held only in the gitignored per-machine secret store with
  restricted permissions; referenced (never duplicated) by the central store.
- **Activation seed**: The per-plugin license/activation state written into an instance on
  boot/install so the plugin reports licensed without a manual step.
- **Primary instance designation**: The one instance whose site identity Elementor Pro verification is
  pinned to; reassignable when the current primary is gone.
- **License-management surface**: The documented mechanism to set/update/clear/inspect keys and the
  primary designation without leaking secrets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With NO key, 100% of installed WPDeveloper pro plugins report licensed/activated on a
  freshly booted instance with zero manual activation actions, in front-end, admin, and REST contexts.
- **SC-002**: With one Elementor Pro key and a designated primary, a freshly booted secondary instance
  reports Elementor Pro activated, consuming no additional license seat (verification resolves to the
  primary's identity).
- **SC-003**: Bringing a new instance to a fully-licensed Pro state takes zero manual activation steps
  (down from one activation per plugin per instance today).
- **SC-004**: With no keys set, Pro plugin behavior and schema output are byte-for-byte unchanged from
  before the feature (no regression); instances without Pro plugins boot unaffected.
- **SC-005**: A license key value appears in zero tracked files, zero command outputs, and zero
  snapshots across the whole workflow (verified by search).
- **SC-006**: WPDeveloper pro plugins install from local source in 100% of cases (the licensed API
  download is activation-gated and intentionally not used under the keyless bypass). A WPDeveloper
  live license check returns synthetic `valid` (never `required_otp`) in 100% of cases — 2FA bypassed.
- **SC-007**: This feature unblocks spec 012's Pro coverage — with Pro plugins activated across
  instances, the schema catalog can include Elementor Pro and WPDeveloper Pro widgets/blocks.

## Assumptions

- The developer owns the licenses and is using dev/staging/local installs as permitted; seat-compliance
  enforcement and production use are out of scope. The sandbox is explicitly dev-only.
- The Elementor Pro sharing reuses the proven `elementor-multisite.php` technique (pin license
  verification to a fixed primary site identity + mirror the activation option), adapted from a single
  multisite DB to separate instances via the central per-user store.
- WPDeveloper pro plugins are available locally (a plugins-pro directory) and also installable from the
  WPDeveloper licensed API with the key; a single key is valid across the WPDeveloper plugin family.
- All WPDeveloper pro plugins validate against the same EDD-style licensing backend
  (`api.wpdeveloper.com`), so one shared activation mechanism (intercept that validation + seed the
  per-plugin `*_license_status = valid` state) covers the whole family — both the plugins using the
  shared `WPDeveloper/Licensing` SDK (essential-blocks-pro, notificationx-pro, wp-scheduled-posts-pro,
  betterdocs-pro) and the older bespoke-license plugins (essential-addons-elementor, embedpress-pro,
  betterlinks-pro, better-payment-pro). The licensing logic is merged into `00-sandbox-ondemand.php`.
- The per-instance mu-plugin is provisioned like the existing sandbox mu-plugins and reads the central
  store + secrets in-container at runtime (the secret store must be reachable in the instance the same
  way the existing bridge token is).
- Only Elementor Pro and the WPDeveloper plugin family are in scope for v1; other third-party Pro
  licenses are future work.
- Per the constitution, "done" means the licensed/activated state is demonstrated by live checks on
  running instances (Pro plugins reporting activated; an install sourced from the licensed API),
  captured as evidence — and no secret leaks.
