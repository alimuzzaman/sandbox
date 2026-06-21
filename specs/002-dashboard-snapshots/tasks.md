---
description: "Task list — Snapshot & Restore from the WordPress Dashboard"
---

# Tasks: Snapshot & Restore from the WordPress Dashboard

**Input**: Design documents from `specs/002-dashboard-snapshots/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
contracts/bridge-api.md ✅, quickstart.md ✅ (constitution at `.specify/memory/constitution.md`)

**Tests**: No unit/contract test tasks — constitution Principle IV mandates **live-stack
verification**. Each phase ends with explicit live checks (the quickstart scenarios).

**Dependency note (ordering vs spec 001 — resolves analyze I2)**: This feature **targets the
current monolithic `sb`** — all line refs below (`cmd_web`, `_write_*`, `_is_herd_instance`)
are as-of pre-001-Stage-C. **Recommended ordering: land after spec 001 Stage B** (per-project
model stable, `main` removed) so the bridge resolves instances against the final model; then
**after 001 Stage C** the host code (`cmd_web`, provisioning, `_write_snapshot_muplugin`)
moves to its modular home (`sandbox/commands/ui_dash.py`, `sandbox/core/provision.py`) — a
mechanical relocation, not a rewrite. If built before 001 Stage C, a follow-up task moves it.
Do not start before 001 Stage B.

**Branch (resolves analyze I1)**: feature directory `002-dashboard-snapshots`; developed on
the shared worktree branch `cwd-instance-resolution` (spec-kit's `create-new-feature.sh` did
not create per-feature branches here — all specs share one branch). Headers in spec.md/plan.md
reflect this.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1 (take), US2 (restore), US3 (list/delete)

## Path Conventions

Host: `sb` (`cmd_web` routes, provisioning, `_write_snapshot_muplugin`, token mint),
`.gitignore`, `CLAUDE.md`. Generated guest file:
`runtime/wp-<instance>/wp-content/mu-plugins/00-sandbox-snapshots.php`. Job state:
`runtime/bridge-jobs/<instance>/<job_id>.json`.

---

## Phase 1: Setup

- [ ] T001 Read `cmd_web` + its `/api/*` handlers in `sb` and the mu-plugin writers (`_write_mail_muplugin` ~L1031, `_autologin_mu_plugin` ~L2065) to confirm the extension points; record the exact functions/lines to touch in `specs/002-dashboard-snapshots/research.md`.
- [ ] T002 [P] Gitignore the async job store: add `runtime/bridge-jobs/` to `.gitignore`.

**Checkpoint**: Extension points confirmed.

---

## Phase 2: Foundational (Blocking Prerequisites) ⚠️

**Purpose**: The bridge infrastructure every user story depends on. MUST complete first.

- [ ] T003 Mint + persist a per-instance `bridge_token`: add a helper in `sb` (mirroring the autologin-token flow) that writes `instances.<name>.bridge_token` to `sandbox.local.yml`; call it from `ensure_instance`/provisioning (idempotent; regenerate on recreate). (data-model: Bridge token)
- [ ] T004 Add `_write_snapshot_muplugin(instance, token, url)` in `sb` that generates `runtime/wp-<instance>/wp-content/mu-plugins/00-sandbox-snapshots.php` with `SANDBOX_BRIDGE_URL`/`SANDBOX_BRIDGE_TOKEN`/`SANDBOX_INSTANCE` constants and a sandbox-only guard; call it from provisioning alongside the mail/ssl/autologin mu-plugins (FR-006, FR-013).
- [ ] T005 Compute the container-reachable bridge URL (host gateway + `sb web` port) and pass it to T004; document the macOS vs Linux `host.docker.internal` handling per research D5.
- [ ] T006 Auto-start `sb web` from `sb up`/`ensure_instance` idempotently so the bridge is reachable whenever the instance runs (FR-014); bind it to a host-gateway-reachable address (research D5). **(U2)** Record the EXACT bind address chosen (the Docker host-gateway interface, NOT a broad `0.0.0.0`) in research.md so FR-012's "localhost/host-gateway only" is not violated by an over-broad bind; the `bridge_token` remains the auth boundary regardless.
- [ ] T007 Add the bridge auth + instance-resolution middleware in `cmd_web`: resolve `<inst>` from the route, require `Authorization: Bearer <token>`, constant-time compare to that instance's `bridge_token`, 403 otherwise; reject unknown instance (404) and herd instance (409, via `_is_herd_instance`) (FR-010, FR-012, contracts error shapes).
- [ ] T008 Implement the async job runner: spawn `sb` ops detached, write `runtime/bridge-jobs/<instance>/<job_id>.json` with `status` transitions (queued→running→succeeded/failed), and add `GET /api/instance/<inst>/job/<job_id>` returning the full job shape `{status, op, name, detail}` (data-model: Bridge job; contracts: job poll). **(A1)** Enforce a max-duration / stuck-job guard: if the detached process exceeds a configured timeout (or its PID is gone without a terminal write), mark the job `failed` with a detail message — never leave a wedged op in `running` indefinitely.
- [ ] T009 Scaffold the mu-plugin admin screen in `00-sandbox-snapshots.php` (T004 generator): Tools → "Sandbox Snapshots" page, `sandbox_*` page slug/handles, `manage_options` capability + `sandbox_snapshots` nonce on every action, a bridge HTTP client (`wp_remote_post`/`wp_remote_get` with the Bearer token), and a job-poll helper (FR-001, FR-004, FR-008).
- [ ] T010 **Live-verify foundation**: provision/`sb up` an instance; from inside the WP container `curl` the bridge with/without/with-wrong token (expect 200 vs 401/403 vs 403) and with the right token but a DIFFERENT `<inst>` (expect 403); confirm `GET job/<id>` returns the full `{status,op,name,detail}` shape **(G1)**; recreate the instance and confirm the OLD token is now rejected (rotation) **(U3)**; confirm the mu-plugin loads in wp-admin and no-ops when constants are absent (quickstart S4, S6).

**Checkpoint**: Authenticated, instance-scoped bridge + admin shell working.

---

## Phase 3: User Story 1 — Take a snapshot from wp-admin (P1) 🎯 MVP

**Goal**: Capture DB + uploads from wp-admin, identical to a CLI snapshot.

**Independent Test**: Take `t1` in wp-admin → appears in `sb snapshots` with db.sql+uploads.tgz.

- [ ] T011 [US1] Add `POST /api/instance/<inst>/snapshot` to `cmd_web`: validate name (`^[\w.-]+$`; blank → `snap-YYYYMMDD-HHMMSS`), 409 if exists and not `force`, else spawn `sb snapshot <name> --instance <inst> [--force]` via the T008 job runner; return `202 {job_id,name}` (contracts).
- [ ] T012 [US1] Add the "Take snapshot" UI to `00-sandbox-snapshots.php`: name field + force checkbox, POST to the bridge, poll the job, surface success/failure (FR-007).
- [ ] T013 [US1] **Live-verify (quickstart S1)**: take `t1` from wp-admin; confirm `sb snapshots --instance <inst>` lists it and `runtime/snapshots/<inst>/t1/` has db.sql+uploads.tgz+META (SC-001). **(C1)** Assert **round-trip format parity** with the CLI: `sb restore t1` (CLI) succeeds on the dashboard-made snapshot, AND a CLI-made snapshot is restorable from the dashboard — proving identical format, not just file presence (FR-002).

**Checkpoint**: Capture works end-to-end from the browser (MVP).

---

## Phase 4: User Story 2 — Restore a snapshot from wp-admin (P1)

**Goal**: Roll back to a captured state from wp-admin, out-of-band so the request's DB
isn't severed mid-restore.

**Independent Test**: Mutate state, restore `t1`, site returns to captured state; request
doesn't error mid-restore.

- [ ] T014 [US2] Add `POST /api/instance/<inst>/restore` to `cmd_web`: 404 if snapshot absent, else spawn `sb restore <name> --instance <inst>` via the job runner (out-of-band); return `202 {job_id,name}` (research D6, contracts).
- [ ] T015 [US2] Add the "Restore" UI to `00-sandbox-snapshots.php`: per-snapshot restore with an explicit destructive-confirm (FR-005), POST to the bridge, poll the job (with a **max-poll cap** so a stuck job surfaces as failed — pairs with T008's guard, **A1**), surface the result; handle the serving DB resetting underneath (poll tolerates transient errors during restore).
- [ ] T016 [US2] **Live-verify (quickstart S2)**: mutate state, restore `t1`, confirm point-in-time replacement matches a CLI restore and the admin flow reports success without a false failure (SC-002). **(U1)** Also verify the FAILURE path (AS US2 #3): restore a deliberately corrupt/incomplete snapshot (or kill the restore mid-flight) and confirm the job reports `failed`, the admin sees a clear error, and the instance is left recoverable (not wedged half-restored).

**Checkpoint**: Rollback works end-to-end from the browser.

---

## Phase 5: User Story 3 — List & delete (P2)

**Goal**: See and manage snapshots for the current instance in the dashboard.

**Independent Test**: List matches `sb snapshots`; delete removes from both views.

- [ ] T017 [P] [US3] Add `GET /api/instance/<inst>/snapshots` to `cmd_web` returning `{snapshots:[{name,size_kb,meta}]}` (shell `sb snapshots` or read `runtime/snapshots/<inst>/`) (contracts).
- [ ] T018 [P] [US3] Add `DELETE /api/instance/<inst>/snapshot/<name>` to `cmd_web`: 404 if absent else remove the snapshot dir; return `{ok:true}` (contracts).
- [ ] T019 [US3] Add the list + delete UI to `00-sandbox-snapshots.php`: render the snapshot table (name / size **with explicit unit label, e.g. "KB"** — **A2**, matching the contract's `size_kb` / meta), delete with confirm, refresh after actions (FR-001).
- [ ] T020 [US3] **Live-verify (quickstart S3)**: take `t2`; confirm both listed matching `sb snapshots`; delete `t1` and confirm it's gone from the dashboard, `sb snapshots`, and disk.

**Checkpoint**: Full take/restore/list/delete from wp-admin.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T021 [P] Herd handling: on a herd instance the dashboard shows a clear "not supported on herd" notice with actions disabled (quickstart S5, SC-005).
- [ ] T022 Security pass on the mu-plugin handlers (auth nonce + capability, sanitize-in/escape-out, prefix `sandbox_*`, `wp_remote_*` only) and on the bridge (no arbitrary `sb` passthrough; token + instance scoping) per CLAUDE.md plugin non-negotiables and FR-010/FR-012.
- [ ] T023 [P] Docs-with-code: add a `00-sandbox-snapshots.php` entry to the CLAUDE.md mu-plugin list/gotchas (alongside mail/ssl) and note the `sb web` auto-start + `bridge_token`; ensure `.specify`/spec-kit exclusions don't affect this guest file.
- [ ] T024 Final acceptance: re-run quickstart S1–S6 end-to-end and record evidence; confirm SC-001..SC-005 met, including the **C1 round-trip parity** (CLI↔dashboard snapshots interchangeable) and the **U1 restore-failure** recovery path.

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 (blocking) → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6**.
- US1/US2/US3 each depend only on Phase 2 (the bridge + mu-plugin shell); US2 and US3 are
  independent of US1 once the foundation exists, but P1 ordering puts US1 first (MVP).
- Within Phase 2, T003→T004→T005 are sequential (token feeds generator feeds URL); T006/T007/
  T008 can proceed once the route layer is understood (T001); T009 depends on T004.

## Parallel Opportunities

- T002 ∥ T001 (Setup).
- T017 ∥ T018 (independent routes) in US3.
- T021 ∥ T023 (Polish, different files).

## Implementation Strategy

- **MVP = Phase 2 + Phase 3 (US1)**: capture from wp-admin, cross-visible with the CLI.
- **US2 (restore)** is the highest-value follow-on; it carries the out-of-band/job
  complexity, so land it right after the MVP proves the bridge.
- **US3 (list/delete)** is thin once the bridge + job model exist.
- Each phase ends at a live-verification checkpoint; do not proceed past a failing one.
