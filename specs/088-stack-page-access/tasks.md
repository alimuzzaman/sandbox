# Tasks: Stack page access

**Input**: spec.md, plan.md, research.md, data-model.md and contracts/access.md.
One release phase covers US1–US6; implementation checkpoints do not authorize a partial release.
Types → test definitions → implementation follows the explicit owner task. Only Luna
runs tests/type-checks, on the coordinated remote. No live mutation is authorized here.

## Phase 1: Setup

- [x] T001 Recover existing source/research and complete design artifacts in specs/088-stack-page-access/.
- [x] T002 Complete read-only Spec Kit consistency analysis of specs/088-stack-page-access/{spec,plan,tasks}.md before implementation.

## Phase 2: Foundations

- [ ] T003 Define relationship, revision, sign-in availability and attempt types in sandbox/hosting/stack_registry.py, sandbox/hosting/panel_view.py, sandbox/hosting/share/attempts.py and src/web/src/types.ts; avoid placeholder behavior in callable code.
- [ ] T004 Define focused behavioral regressions in tests/test_host_stack.py, tests/test_web_hosting_api.py, tests/test_web_share.py, tests/test_hosting.py and tests/test_host_share.py before implementation; captured subprocesses use tests/subprocess_support.py.

## Phase 3: US1 — Tailnet stack page

Goal: direct page/reload lists all recorded routes, delivered evidence and inbox.
Independent acceptance: compare a fixture page with stack list including absent revision/inbox states.

- [ ] T005 [US1] Align controller route revisions with stack_registry.delivered_commit in sandbox/hosting/panel_view.py, keeping linked site commits and explicit evidence states (FR-002–003).
- [ ] T006 [US1] Resolve same-remote recorded inbox names and unavailable guidance in sandbox/hosting/panel_access.py and sandbox/commands/ui_dash.py (FR-004–006).
- [ ] T007 [US1] Add clean route, card navigation and direct/missing-stack page in src/web/src/router.ts, src/web/src/render.ts, src/web/src/pages/hosting.ts and src/web/src/pages/stack.ts (FR-001–006).

## Phase 4: US2 — Per-click WordPress sign-in

Goal: each click mints for the selected recorded target without invalidating another click.
Independent acceptance: two fixture links both work in reverse order once, then replay/expiry fail.

- [ ] T008 [US2] Add recorded-manifest resolution and CLI --name target selection in sandbox/hosting/panel_access.py and sandbox/commands/hosting.py; prevent list/GET/selection-conflict minting (FR-007,012).
- [ ] T009 [US2] Implement bounded atomic hash pool and fragment POST opener in sandbox/hosting/autologin.py, with integration in sandbox/core/_hosting.py and sandbox/commands/hosting.py (FR-009–011).
- [ ] T010 [US2] Add same-origin POST sign-in endpoint with bounded exact payload, no-store response, safe errors and audit in sandbox/commands/ui_dash.py; add one-shot action in src/web/src/api.ts and src/web/src/pages/stack.ts (FR-007–012,034).

## Phase 5: US3 — Direct shared stacks

Goal: template uses is validated, persisted and shown with shared routes/inbox/sign-ins.
Independent acceptance: refuse invalid/missing/self/duplicate/over-limit uses before writes; show missing later references.

- [ ] T011 [US3] Validate optional stack.uses shape and distinct refusals in sandbox/hosting/stack_template.py and same-remote existence before plan/confirm writes in sandbox/commands/host_stack.py (FR-013–014).
- [ ] T012 [US3] Persist uses across delivery/reapply, show list JSON/text and down dependents in sandbox/hosting/stack_registry.py and sandbox/commands/host_stack.py (FR-015,018).
- [ ] T013 [US3] Add direct Shared sections, missing-reference state and actions in sandbox/hosting/panel_view.py and src/web/src/pages/stack.ts; never expand nested uses (FR-016–017).

## Phase 6: US4 — Per-stack gate hostnames

Goal: two renders yield distinct gate/login hostnames, with literal regex substitutions.
Independent acceptance: render two names, validate both and refuse placeholders outside approved paths.

- [ ] T014 [US4] Extend placeholder allow-list and literal regex rendering in sandbox/hosting/stack_template.py; cover exact autologin declaration paths in tests/test_host_stack.py (FR-019–020).

## Phase 7: Dependency checkpoint

- [ ] T015 Verify short-share-links has merged into latest, then rebase feat/stack-page-access without force-push; reconcile contracts/access.md with actual short-link ids and gate API before gate edits. If branch rewrite would require force-push, coordinate a merge update instead of violating push policy.

## Phase 8: US5 — One link reaches everything

Goal: one grant opens all own/direct shared services without panel authority.
Independent acceptance: fresh non-tailnet browser reaches own/shared routes, admin, renderer and inbox through one link.

- [ ] T016 [US5] Validate proposed route-sign-in mapping and complete access coverage in sandbox/hosting/login_service/__init__.py and sandbox/hosting/share/stack_page.py; list all gaps before mint (FR-021–024,035).
- [ ] T017 [US5] Add token-free attempt persistence, stable target lock ordering and preallocated ids in sandbox/hosting/share/attempts.py and sandbox/commands/host_share.py (FR-025,028–029).
- [ ] T018 [US5] Mint/attach direct companions with earliest expiry, cleanup on failure and no nested companions in sandbox/commands/host_share.py and sandbox/hosting/share/stack_page.py (FR-025–026).
- [ ] T019 [US5] Extend validated gate stack records and renderer in sandbox/hosting/login_service/runtime.py for per-click sign-ins, bounded unused links, unavailable companion state, audit and host-only sessions (FR-023,026–028,033–034).

## Phase 9: US6 — Revoke and recovery

Goal: parent revoke and repeated cleanup reach every grant despite interruption or state loss.
Independent acceptance: inject companion failure and gate restart; list remaining grants and complete repeat revoke.

- [ ] T020 [US6] Implement cleanup/reconcile/list and repeat revoke from durable records in sandbox/hosting/share/attempts.py and sandbox/commands/host_share.py (FR-029–032).
- [ ] T021 [US6] Bind unused sign-ins to source grants, revoke keys after parent/companion removal and report remaining live ids/expiry in sandbox/hosting/login_service/runtime.py, sandbox/commands/ui_dash.py and src/web/src/types.ts (FR-030–034).

## Phase 10: Verification and handoff

- [ ] T022 Update docs/remote-hosting.md, docs/login-service.md, README.md, hosting skill docs and matching owning revision/schema evidence; send final schema/readiness to setup/orchestrator, using placeholder hosts (FR-036–037).
- [ ] T023 Ask Luna to run focused suites, UI typecheck/build and required gates via finite-timeout durable jobs on the coordinated remote; retain fixed reports under shared scratch. Rebuild config/sandbox-web.js after UI source changes.
- [ ] T024 Obtain owner authorization and remote ownership release for runtime acceptance in specs/088-stack-page-access/quickstart.md; record all own/shared access, concurrency and recovery proof. Unavailable proof stays explicitly incomplete.
- [ ] T025 Fresh independent Sol reviews final diff against latest, fix/respond to findings and repeat after changes; owner authentication sign-off required before release. Record review evidence in shared scratch.
- [ ] T026 Stage/commit/push verified work to the named branch, no force push or PR creation; write requested handback under shared scratch and notify orchestrator with manifest fields, results, review verdict and open limits.

## Dependencies and parallel opportunities

T001 → T002 → T003 → T004. US1–US4 may then be coded in separate files; US3 Shared
sections depend on US1 view and US2 actions. Gate edits T016–T021 depend on T015.
T017 precedes external companion mint, and T020 reuses that durable record.
T022–T026 cover every story and are required before the phase can be marked ready.

Parallel examples: US1 route renderer and backend revision projection after types;
US2 pool renderer and guarded endpoint after operation contract; US3 template shape
and frontend Shared section after view types; US4 fixture cases per approved field;
US5 coverage validator and attempt repository after contracts; US6 gate source-grant
checks and controller revoke aggregation after repository contract. Never share one
remote/browser between helpers. Parent owns integration. No implementation delegated
by this task list without an explicit bounded brief.

US1 is an implementation checkpoint, not an agreed independently shippable release.
All six stories stay in the phase until their combined acceptance is proven.
