# Tasks: Stack page access

**Input**: spec.md, plan.md, research.md, data-model.md and contracts/access.md.
One release phase covers US1–US6; implementation checkpoints do not authorize a partial release.
Current execution uses Codex Office Sol high, Office Luna high for bounded checks,
and a fresh independent Office Sol high review. Source checkmarks mean implemented
in the reconciled cbda71e source, not installed or runtime accepted. The owner approved
the stack access model and Tailnet-panel/current applied agent_mint policy; runtime
work still requires the coordinator's explicit slot. Paused 090 source stays excluded.

## Phase 1: Setup

- [x] T001 Recover existing source/research and complete design artifacts in specs/088-stack-page-access/.
- [x] T002 Complete read-only Spec Kit consistency analysis of specs/088-stack-page-access/{spec,plan,tasks}.md before implementation.

## Phase 2: Foundations

- [x] T003 Define relationship, revision, sign-in availability and attempt/physical ownership types in sandbox/hosting/share/controller.py and sandbox/hosting/stack_registry.py, sandbox/hosting/panel_view.py, sandbox/hosting/share/attempts.py and src/web/src/types.ts; avoid placeholder behavior in callable code.
- [ ] T004 Define focused behavioral regressions in tests/test_host_stack.py, tests/test_web_hosting_api.py, tests/test_web_share.py, tests/test_hosting.py and tests/test_host_share.py after the supported implementation workflows have been exercised; captured subprocesses use tests/subprocess_support.py.

## Phase 3: US1 — Tailnet stack page

Goal: direct page/reload lists all recorded routes, delivered evidence and inbox.
Independent acceptance: compare a fixture page with stack list including absent revision/inbox states.

- [x] T005 [US1] Align controller route revisions with stack_registry.delivered_commit in sandbox/hosting/panel_view.py, keeping linked site commits and explicit evidence states (FR-002–003).
- [x] T006 [US1] Resolve same-remote recorded inbox names and unavailable guidance in sandbox/hosting/panel_access.py and sandbox/commands/ui_dash.py (FR-004–006).
- [x] T007 [US1] Add clean route, card navigation and direct/missing-stack page in src/web/src/router.ts, src/web/src/render.ts, src/web/src/pages/hosting.ts and src/web/src/pages/stack.ts (FR-001–006).

## Phase 4: US2 — Per-click WordPress sign-in

Goal: each click mints for the selected recorded target without invalidating another click.
Independent acceptance: two fixture links both work in reverse order once, then replay/expiry fail.

- [x] T008 [US2] Add recorded-manifest resolution and CLI --name target selection in sandbox/hosting/panel_access.py and sandbox/commands/hosting.py; prevent list/GET/selection-conflict minting (FR-007,012).
- [x] T009 [US2] Implement bounded atomic hash pool and fragment POST opener in sandbox/hosting/autologin.py, with integration in sandbox/core/_hosting.py and sandbox/commands/hosting.py (FR-009–011).
- [x] T010 [US2] Add same-origin POST sign-in endpoint with bounded exact payload, no-store response, safe errors and audit in sandbox/commands/ui_dash.py; add one-shot action in src/web/src/api.ts and src/web/src/pages/stack.ts (FR-007–012,034).

## Phase 5: US3 — Direct shared stacks

Goal: template uses is validated, persisted and shown with shared routes/inbox/sign-ins.
Independent acceptance: refuse invalid/missing/self/duplicate/over-limit uses before writes; show missing later references.

- [x] T011 [US3] Validate optional stack.uses shape and distinct refusals in sandbox/hosting/stack_template.py and same-remote existence before plan/confirm writes in sandbox/commands/host_stack.py (FR-013–014).
- [x] T012 [US3] Persist uses across delivery/reapply, show list JSON/text and down dependents in sandbox/hosting/stack_registry.py and sandbox/commands/host_stack.py (FR-015,018).
- [x] T013 [US3] Add direct Shared sections, missing-reference state and actions in sandbox/hosting/panel_view.py and src/web/src/pages/stack.ts; never expand nested uses (FR-016–017).

## Phase 6: US4 — Per-stack gate hostnames

Goal: two renders yield distinct gate/login hostnames, with literal regex substitutions.
Independent acceptance: render two names, validate both and refuse placeholders outside approved paths.

- [x] T014 [US4] Extend placeholder allow-list and literal regex rendering in sandbox/hosting/stack_template.py; cover exact autologin declaration paths in tests/test_host_stack.py (FR-019–020).

## Phase 7: Dependency checkpoint

- [x] T015 Reconcile specs/088-stack-page-access/contracts/access.md and source against origin/latest cbda71e, which includes short links e04e293 and the owner-sharing closure. Exact callback wire, capabilities and cleanup descriptors are source-defined; installed compatibility remains unverified.


## Phase 8: US5 — One link reaches everything

Goal: one grant opens all own/direct shared services without panel authority.
Independent acceptance: fresh non-tailnet browser reaches own/shared routes, admin, renderer and inbox through one link.

- [x] T016 [US5] Validate route_sign_in, reserved $wordpress and explicit inbox_sign_in inbox_gate, aggregate every discoverable own/direct gap without filtering/truncation and complete access coverage in sandbox/hosting/login_service/__init__.py and sandbox/hosting/share/stack_page.py; list all gaps before mint (FR-021–024,035).
- [x] T017 [US5] Add version-3 token-free original-settlement persistence with conservative version-2 reads, original safe runtime/gate/WP-cleanup identity persisted before unknown-before-keyinstall/mint, attempt lock then sorted unique effect locks without shared-state nesting, and same preallocated MintedLink derived ids in sandbox/hosting/share/attempts.py and sandbox/commands/host_share.py (FR-025,028–029).
- [x] T018 [US5] Mint direct companions first and parent last with final page in the same mint, no reservation/attach/idempotent replay; clamp to earliest expiry, cleanup on failure and no nested companions in sandbox/commands/host_share.py and sandbox/hosting/share/stack_page.py (FR-025–026).
- [x] T019 [US5] Extend validated gate stack records and renderer in sandbox/hosting/login_service/runtime.py for exact sandbox.share-stack.v2 with retained v1 reader and unchanged share-link.v2, read-only GET/HEAD, guarded CSRF action POST, eight pending hashes per entry with bounded expiry, separate CLI singleton, non-consuming authenticated companion probe/navigation, audit and host-only sessions (FR-023,026–028,033–034).

## Phase 9: US6 — Revoke and recovery

Goal: parent revoke and repeated cleanup reach every grant despite interruption or state loss.
Independent acceptance: inject companion failure and gate restart; list remaining grants and complete repeat revoke.

- [x] T020 [US6] Implement cleanup/reconcile/list and repeat revoke from durable records in sandbox/hosting/share/attempts.py and sandbox/commands/host_share.py (FR-029–032).
- [x] T021 [US6] Implement shared-page WP fixed-endpoint purpose-separated HMAC online atomic claim and signed nonce-bound receipt in sandbox/hosting/share/stack_page.py and gate runtime; bind unused sign-ins to live source grants and remove per-link keys even after gate absence and report remaining live ids/expiry in sandbox/hosting/login_service/runtime.py, sandbox/commands/ui_dash.py and src/web/src/types.ts (FR-030–034).

## Phase 10: Verification and handoff

- [ ] T022 Update docs/remote-hosting.md, docs/login-service.md, README.md, hosting skill docs and matching owning revision/schema evidence; send final schema/readiness to setup/orchestrator, using placeholder hosts (FR-036–037).
- [ ] T023 Run bounded checks through Codex Office GPT-6-Luna high for focused suites, UI typecheck/build and required gates, with finite-timeout durable jobs where supported and a reserved host slot; retain fixed reports under shared scratch. Rebuild config/sandbox-web.js after UI source changes.
- [ ] T024 Obtain owner authorization and remote ownership release for runtime acceptance and fill bounded unresolved targets in specs/088-stack-page-access/quickstart.md before executing; record all own/shared access, concurrency and recovery proof. Unavailable proof stays explicitly incomplete.
- [ ] T025 Fresh independent Codex Office GPT-6.1-Sol high reviews final diff against latest, fix/respond to findings and repeat after changes; owner authentication sign-off required before release. Record review evidence in shared scratch.
- [ ] T026 Stage/commit/push verified work to the named branch, no force push or PR creation; write requested handback under shared scratch and notify orchestrator with manifest fields, results, review verdict and open limits.

## Dependencies and parallel opportunities

T001 → T002 → T003. Finish implementation and exercise supported workflows before
writing/running T004 regressions and required gates. US1–US4 may be coded in separate files; US3 Shared
sections depend on US1 view and US2 actions. Gate edits T016–T021 depend on T015.
T017 precedes external companion mint, and T020 reuses that durable record.
T022–T026 cover every story and are required before the phase can be marked ready.

Parallel examples: US1 route renderer and backend revision projection after types;
US2 pool renderer and guarded endpoint after operation contract; US3 template shape
and frontend Shared section after view types; US4 fixture cases per approved field;
US5 coverage validator and attempt repository after contracts; US6 gate source-grant
checks and controller revoke aggregation after repository contract. Never share one
remote/browser between helpers. Parent owns integration. Only bounded checks and the requested independent review are delegated; remote and browser ownership remain coordinator-owned.

US1 is an implementation checkpoint, not an agreed independently shippable release.
All six stories stay in the phase until their combined acceptance is proven.

## R1 source review remediation

The broad review returned NOT SAFE. Verification/review tasks remain open.
Implementation corrections include share/controller.py physical leases, exact
running source/config/access/target parity and immutable gate/WP effects; attempts.py
v3 original_settled provenance; runtime.py entry TTL clamp and route action controls.
Legacy unknown gate_revoked records cannot discharge original execution uncertainty.
