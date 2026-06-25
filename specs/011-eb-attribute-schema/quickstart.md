# Quickstart / Live Validation: EB Attribute-Schema Resolver

**Feature**: 011-eb-attribute-schema · **Phase 1** · 2026-06-25

Per Constitution IV, the feature is "done" only when these live checks pass against a running
instance. Use the `templately-fsi-rewrite` project (Elementor + Essential Addons + Essential Blocks
active, and the EB dev checkouts present at `~/Sites/git/essential-blocks` + `…-pro`).

## Prerequisites

- `ensure_instance(project_dir="/Users/alim/Sites/git/templately-fsi-rewrite")` → instance ready.
- An EB source checkout (free, and Pro if testing Pro blocks) reachable under the mounted
  `plugins_home`. Confirm the resolver's scan root: it reads `SANDBOX_PLUGINS_HOST` inside the
  container; the EB checkout must live under that mounted path (or be mapped) to reach full fidelity.
- Abilities enabled (`./sb abilities status` → on).

All checks below invoke the ability through `wp_eval_live` (which proxies to `sandbox/execute-php`)
calling `wp_get_ability('sandbox/editor-schema')->execute([...])`.

## Check 1 — Full fidelity for advanced-heading (SC-001, SC-002)

Run `editor-schema` for `essential-blocks/advanced-heading` with the EB checkout present.

**Expected**:
- `fidelity.level === "full"`, `fidelity.source_checkout` non-null.
- `fidelity.count >= 700` (verified ground truth ≈ 787).
- `attributes` contains `titleText` and `tagName`.

## Check 2 — Generator + nested expansion (FR-002)

In the Check 1 result, assert presence of representative generated keys:
- Typography: a `*FontSize` / `TAB*FontSize` key.
- Dimensions: a margin/padding `*Top` + `*Unit` + `*isLinked` set.
- Border/shadow nested dimensions: a `*Bdr_Top` and `*Rds_Top` key (proves nesting expanded).
- Background: a `*backgroundType` + `*overlayColor` key.

**Expected**: all present — confirming generator families and the border/shadow→dimensions nesting.

## Check 3 — Reduced fidelity is honest when source is absent (FR-004)

Temporarily make the EB source unreachable (point the scan root away, or test on an instance whose
`plugins_home` has no EB checkout), then request the same block.

**Expected**:
- `fidelity.level === "reduced"`, `source_checkout === null`, non-empty `reason`.
- `attributes` is the 3 generic keys — but the response is NOT presented as complete.

## Check 4 — Partial fidelity names the gap (FR-005)

Simulate an unknown generator (e.g. point at a block whose `attributes.js` references a generator the
resolver can't expand, or stub one).

**Expected**: request succeeds; `fidelity.level === "partial"`; `unresolved` names the generator.

## Check 5 — Cache + invalidation (FR-011)

1. Call Check 1 twice; second call is materially faster (cache hit).
2. `touch` the block's `attributes.js` in the checkout; call again.

**Expected**: after the touch, the resolver recomputes (fingerprint changed) and still returns the
correct full set — no stale schema served.

## Check 6 — No regression for Elementor + core (FR-007, SC-005)

- `editor-schema {builder:"elementor", name:"eael-info-box"}` → `controls` map, same shape/count as
  before the feature (~385).
- `editor-schema {builder:"gutenberg", name:"core/heading"}` → 16 block.json attributes, no
  `fidelity` object added.

**Expected**: byte-for-byte identical to pre-feature output for both.

## Check 7 — End-to-end author + render (SC-006)

Using the full schema from Check 1, author an `advanced-heading` via `sandbox/gutenberg-insert` with
the CORRECT keys (`titleText`, `tagName`), then load the page frontend with `visit`.

**Expected**: the heading renders non-empty on the first attempt (closing the day-one empty-render
bug that motivated this feature).

## Check 8 — Breadth: ≥10 distinct EB blocks at full fidelity (SC-004)

Request `editor-schema` for at least 10 different EB blocks (advanced-heading, button, accordion,
advanced-tabs, wrapper, call-to-action, countdown, dual-button, flipbox, advanced-image).

**Expected**: each returns `fidelity.level === "full"`, includes its explicit attributes plus all
generator-contributed attributes, and none is silently truncated to the 3 generic keys. Record each
block's `count`.

## Check 9 — Pro block full fidelity (FR-008)

With the EB Pro source checkout reachable, request a Pro block's schema.

**Expected**: `fidelity.level === "full"`, `source_checkout` points at the Pro checkout — same
resolver path as free blocks.

## Evidence to capture

For each check, save the ability response JSON (and the Check 7 screenshot under `tmp/`) as the
done-proof. A check that cannot be reproduced live blocks completion — do not mark done from code
reading.
