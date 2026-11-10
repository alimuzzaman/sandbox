# Tasks: Stacks and Hosted Sites View

**Input**: Design documents from `specs/076-web-host-stacks/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Requested by the owner. Order is types first, then tests, then implementation. Tests run only in Haiku helpers on xspeed-dev through `job-start`.

## Phase 1: Setup

- [x] T001 Confirm the spec numbering (076; 073-075 were taken on latest) and record the shared-file list from plan.md in the handoff notes at /Users/alim/Sites/git/.scratch/sandbox/web-host-stacks/notes.txt

## Phase 2: Foundational (types and records shared by every story)

- [x] T002 Define TypedDicts `Route`, `LastApply`, `StackFacts`, `TargetSummary`, `SiteLink`, `ServerRecords` and constants (paths, schema, status values) in sandbox/hosting/panel_records.py
- [x] T003 Add `SiteLinkRef` TypedDict and optional `sites: list[SiteLinkRef]` to `StackRecord` in sandbox/hosting/stack_registry.py
- [x] T004 [P] Write tests for `build_target_summary` (field allow-list, planted secrets absent, template relative path, stack facts, TTL only when present), for `SummaryRecorder` (started, succeeded and failed writes, a swallowed write error, no write before `started`, `run()` re-raises) and for `read_server_records` (valid, malformed, schema mismatch, legacy dir without summary, site links) in tests/test_panel_records.py
- [x] T005 Implement `build_target_summary`, `build_site_link`, `encode_record`, `remote_write_command` (temp file, then `mv`, mode 0600), `SummaryRecorder` and `read_server_records` in sandbox/hosting/panel_records.py

## Phase 3: User Story 1 — every server of a stack (P1)

**Goal**: On the server, a stack shows one linked row per route hostname.
**Independent test**: Fixture summary with three routes gives three rows with URLs, primary first; a wildcard has no URL.

- [x] T006 [P] [US1] Write tests for `build_view` from server records: stack grouping, row order, redirect rows, wildcard rows, plain target separation, legacy target `recorded=false`, partial reasons, and the dedupe of server over controller in tests/test_panel_view.py
- [x] T007 [US1] Implement `build_view(server=..., controller=..., instances=..., instance_route_hosts=..., health=...)` in sandbox/hosting/panel_view.py
- [x] T008 [P] [US1] Write tests for `hosting_api()` in ui_dash: 200 payload shape, query string refused with 400, this-server source only where `_hosts_control_service()` is true, redaction applied, unexpected error gives a 500 without detail, and `_web_do_action` has no hosting action, in tests/test_web_hosting_api.py
- [x] T009 [US1] Add `_hosting_view()` and the `GET /api/hosting` route in sandbox/commands/ui_dash.py
- [x] T010 [US1] Hook `SummaryRecorder` into `_apply_host` (`started` after source publish, `finished(True)` before return) and wrap the caller with `recorder.run(...)` in sandbox/commands/hosting.py
- [x] T011 [P] [US1] Add the `HostingView` types, `fetchHosting`, the `/hosting` route, `pages/hosting.ts`, render wiring and the sidebar link in src/web/src/{types,api,router,render,main}.ts, src/web/src/pages/hosting.ts and sandbox/core/_paths.py

## Phase 4: User Story 2 — WordPress site under its stack (P1)

**Goal**: `sb deploy --stack` links a site; a name match is the labelled fallback.
**Independent test**: Linked site row shows "linked"; an unlinked `wp-demo.<zone>` shows "matched by name"; an unknown slug refuses before effects.

- [x] T012 [P] [US2] Write tests for `--stack` validation (requires expose, bad slug, unknown stack, all before any remote call), link recording (remote write command and controller `sites` updated, write failure gives `recorded=false` and still `ok`) and `host stack up` keeping `sites` in tests/test_deploy_stack_link.py
- [x] T013 [US2] Add `--stack` to the deploy subparser in sandbox/cli.py
- [x] T014 [US2] Implement `_validate_stack_link_args` (called before any effect) and `_record_stack_link` (after success) in sandbox/commands/deploy.py
- [x] T015 [US2] Keep `sites` when `_up` rebuilds a record in sandbox/commands/host_stack.py
- [x] T016 [P] [US2] Add tests for linked precedence over name match, longest-slug match, zone derivation, `instance_not_found` and Caddy file name hosts in tests/test_panel_view.py
- [x] T017 [US2] Implement linked-site and name-match rows in sandbox/hosting/panel_view.py

## Phase 5: User Story 3 — code and state details (P2)

**Goal**: Each stack shows slug, project, ref, template, last apply, Mailpit port and created/TTL.
**Independent test**: The fixture's values appear unchanged in the view payload and the page.

- [x] T018 [P] [US3] Add tests for the controller source: stack registry and hosts.json give the same view shape; last apply comes from `_last_apply_view`; template is relative; mailpit port is picked from ports, in tests/test_panel_view.py
- [x] T019 [US3] Implement `controller_records()` and the stack detail fields in sandbox/hosting/panel_view.py
- [x] T020 [US3] Render the details block (code, state, ports, created/TTL) in src/web/src/pages/hosting.ts

## Phase 6: User Story 4 — health (P2)

**Goal**: Cached, bounded, non-blocking health per hostname.
**Independent test**: First snapshot returns "checking" immediately; after the worker runs, statuses are classified; a second snapshot within the TTL makes no new probe.

- [x] T021 [P] [US4] Write tests for classification, TTL caching, the 64-host cap (`not_checked`), refusal of invalid hostnames (wildcard, port, IP, userinfo), a non-blocking `snapshot`, no redirects followed and no credentials sent, in tests/test_panel_health.py
- [x] T022 [US4] Implement `HealthCache` and `probe()` in sandbox/hosting/panel_health.py and wire one process-wide cache into `_hosting_view()` in sandbox/commands/ui_dash.py
- [x] T023 [US4] Render health badges and a follow-up fetch while any row is "checking" in src/web/src/pages/hosting.ts and src/web/src/main.ts

## Phase 7: User Story 5 — plain hosted targets (P3)

- [x] T024 [US5] Render the "Hosted sites" section, with the legacy note, in src/web/src/pages/hosting.ts (the data comes from T007 and T019)

## Phase 8: Polish

- [x] T025 Rebuild config/sandbox-web.js (`scripts/build-web-js.sh`) and config/sandbox-web.css (`scripts/build-web-css.sh`)
- [x] T026 [P] Update README.md (`sb web` section) and docs/remote-hosting.md (the panel view, the `host apply` summary, `deploy --stack`)
- [x] T027 Run all new test modules plus tests.test_host_stack, tests.test_web_this_server, tests.test_web_dashboard and tests.test_web_poller through Haiku helpers on xspeed-dev; compare any failure with the controller-4e54f8d baseline
- [x] T028 Visual check: run the panel locally against a fixture `SANDBOX_HOME`, take screenshots into /Users/alim/Sites/git/.scratch/sandbox/web-host-stacks/ and downscale them
- [x] T029 Non-author review by Luna (secret exposure, read-only-ness); fix or answer every finding

## Phase 9: Follow-ups from the server owner (2026-11-10)

- [x] T030 Count hosted targets apart from instances and attribute their `sandbox-host-<project>-<environment>-*` containers in `_hosted_inventory_snapshot` (sandbox/remote_runtime/host_inventory.py), tested in tests/test_host_inventory_hosted.py
- [x] T031 Show hosted targets on the host pages with links to their `/hosting` cards (src/web/src/pages/remote.ts, hosts.ts, hosting.ts, main.ts)
- [x] T032 `sb web --allow-host NAME` (repeatable), honoured only on a tailnet address, with Origin `https://NAME` (sandbox/cli.py, sandbox/commands/ui_dash.py), tested in tests/test_web_tailscale.py


T002 → T004/T005 → (T006–T011) → (T012–T017) → (T018–T020) → (T021–T023) → T024 → T025–T029. US1 and US2 share `panel_view.py`, so their view tasks are sequential. The TS tasks (T011, T020, T023, T024) can run in parallel with Python tests once the payload shape (data-model.md) is fixed.

## Parallel examples

- T004, T006, T008 and T012 are separate new test files and can be written together.
- T011 (TS) runs alongside T010 (hosting.py hook).

## Implementation strategy

MVP is US1 plus the summary write, which already answers the owner's "find the servers" on the server. US2 adds the WordPress row. US3 to US5 complete the details. All of it ships in one release cycle (one phase).

## Phase 10: Convergence

Follow-up 2026-11-10 (US6-US8). Types first, then tests, then code. Stay off `sandbox/commands/host_stack.py`, `sandbox/commands/hosting.py`, `sandbox/resources/*`, `tests/test_lifecycle.py`, `tests/test_remote_hosting_images.py` and `tests/test_server_transport.py`.

- [ ] T033 Types: `LifetimeView`, `LifetimeRow` and the `ApplyStatus` value `unverified` plus `LastApply.reason` in sandbox/hosting/panel_lifetime.py and sandbox/hosting/panel_records.py, plus the mirror TS types in src/web/src/types.ts, per FR-017/FR-026 and plan data model (missing)
- [ ] T034 Tests: tests/test_panel_lifetime.py covers projection, unclassified-protected for missing, malformed, truncated, oversized and symlinked records, ordering, copies as separate rows, `Store.peek` writing nothing and taking no lock, and controller list parsing and cache bounds. Also tests/test_panel_view.py (lifetimes joined, a record with no card listed, stack fallback), per FR-017 to FR-021 and SC-006 (missing)
- [ ] T035 Tests: tests/test_panel_records.py covers apply outcome from delivery (unverified with reason; failed only when execution failed; legacy bool), and tests/test_host_inventory_hosted.py covers lifetime in rows, stack_slug fallback and last_apply_reason, per FR-022/FR-026 (missing)
- [ ] T036 Tests: src/web/tests/hostlabels.test.ts covers apply labels, time left, lifetime badges, bulk-action visibility and sidebar counts, run from tests/test_web_poller.py, per FR-023/FR-024/FR-027 (missing)
- [ ] T037 `Store.peek(now)` in sandbox/hosting/lifetime/hoststore.py: lock-free, no writes, no symlinks, bounded, Python 3.8 stdlib, per FR-019 and plan R8 (missing)
- [ ] T038 sandbox/hosting/panel_lifetime.py: projection through `hoststore.parse_record` and `view`, ordering, the server reader, and `LifetimeCache` for `sb host stack list --remote R --json`, per FR-018 to FR-021 and plan R9 (missing)
- [ ] T039 sandbox/hosting/panel_view.py and sandbox/commands/ui_dash.py: per-card `lifetime`, `compose_project`, `stack_slug` and the top-level `lifetimes`, wired for both sources, per FR-017/FR-021 (missing)
- [ ] T040 sandbox/hosting/panel_records.py `SummaryRecorder` outcome from `result["delivery"]` (plan R10), and sandbox/remote_runtime/host_inventory.py rows with lifetime, stack fallback and reason, per FR-022/FR-026 (missing)
- [ ] T041 Web: hosting.ts (Lifetimes list, card lifetime line, hosted-targets columns, apply labels, CPU label), hosts.ts (landing page), remote.ts (Sandbox dev instances), render.ts and main.ts (sidebar counts, bulk buttons, local-host signature), ui/hostlabels.ts, plus the sidebar slot in sandbox/core/_paths.py and src/web/index.html, per FR-022 to FR-025 and FR-027 (missing)
- [ ] T042 Rebuild config/sandbox-web.js and config/sandbox-web.css with scripts/build-web-js.sh and scripts/build-web-css.sh, per plan (missing)
- [ ] T043 Docs: the panel section in docs/remote-hosting.md and README, per FR-016 (missing)
- [ ] T044 Run the new and touched test modules on xspeed-dev in Haiku helpers and compare failures with an origin/latest baseline; visual check against a fixture SANDBOX_HOME with downscaled screenshots of /hosting, /host/local and a host resource page under /Users/alim/Sites/git/.scratch/sandbox/panel-lifetime/, per SC-006 to SC-008 (missing)
- [ ] T045 Luna review of the final head; fix or answer every finding, per FR-013/FR-028 (missing)
