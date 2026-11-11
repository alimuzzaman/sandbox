# Tasks: Front-door-only refresh for hosted targets

**Input**: Design documents from `specs/084-front-door-refresh/`

**Tests**: requested (the user's process is types first, then tests, then code).

## Phase 1: Setup

- [ ] T001 Record the test baseline on `85c278f` for the modules in quickstart.md (xspeed-dev durable job, Haiku helper), result in the scratch dir

## Phase 2: Foundational (types)

- [ ] T002 Define refusal codes, `FrontDoorRefreshRefused`, outcome literals, `declaration_digest()`, route feature readers and audit record shape in sandbox/hosting/front_door/refresh.py
- [ ] T003 [P] Add `origin_certificate_present()` (check only) in sandbox/hosting/front_door/nginx.py
- [ ] T004 [P] Extract `recorded_request_scope(previous)` from `require_previous_snapshot` in sandbox/delivery/hosting.py (no behaviour change)

## Phase 3: User Story 1 - refresh a public target (P1)

- [ ] T005 [US1] Tests in tests/test_front_door_refresh.py: record binding (port, records, proxy, names, kind, route name, coverage, digest) each refuses with its part; unchanged route is not reloaded; refreshed route; verification failure restores previous; restore failure is reported; no route / strict read failure refuses; HTTPS lost, names changed, auth weakened refuse; missing certificate and missing secret refuse; shared-coverage verification; plan mode takes no lock and writes no audit; a re-run after an interrupted run converges; no DNS write, Compose, checkout or certificate issuance call in any of these paths
- [ ] T006 [US1] Implement `check_recorded_binding()` and `check_no_weakening()` in sandbox/hosting/front_door/refresh.py
- [ ] T007 [US1] Add a check-only `existing_certificate_for_refresh()` to `_CaddyHostEdge` and `_NginxHostEdge` in sandbox/commands/hosting.py
- [ ] T008 [US1] Implement `_refresh_host_front_door()` (plan and confirm modes) beside `_continue_host_edge_only` in sandbox/commands/hosting.py
- [ ] T009 [US1] Record `front_door_declaration` on apply success and edge continuation success in sandbox/commands/hosting.py, with a test in tests/test_front_door_refresh.py
- [ ] T010 [US1] Wire `refresh-front-door` into the `host` action choices (sandbox/cli.py) and `cmd_host` dispatch with `--target`/`--name` resolution in sandbox/commands/hosting.py

## Phase 4: User Story 2 - private targets stay private (P1)

- [ ] T011 [US2] Tests: marked private route required, would-publish refusal, proxied refusal, privacy verify kwargs and route tag passed, failure restores the marked route, in tests/test_front_door_refresh.py
- [ ] T012 [US2] Apply the private rules in `_refresh_host_front_door()` (reuse `_refuse_proxied_records_for_private`, `_privacy_verify_kwargs`, `_route_tag`, `_restore_edge_fail_closed`) in sandbox/commands/hosting.py

## Phase 5: User Story 3 - ownership, fences, audit (P1)

- [ ] T013 [US3] Tests: active operation / uncertainty / image activation / destroy refuse; lease held refuses; refused-attempt target runs and its hosting operation is untouched; audit lines written without secrets, in tests/test_front_door_refresh.py
- [ ] T014 [US3] Hold `target_mutation_port("apply")` + state check and the remote target lease around the run; write audit start/end and the `front_door_refresh` record, in sandbox/commands/hosting.py and sandbox/hosting/front_door/refresh.py
- [ ] T015 [US3] Add the MCP tool `host_front_door_refresh` in mcp/wp-server/tools/share.py and its name in mcp/wp-server/tools/manifest.py; test that it returns the CLI JSON and never raises

## Phase 6: User Story 4 - stack delivery selectors (P2)

- [ ] T016 [US4] Tests in tests/test_delivery_stack_selector.py: inspect by `--name` reads the recorded scope; an unknown stack refuses `host_target_unknown`; retire falls back to the recorded scope only for the recorded request id
- [ ] T017 [US4] `delivery inspect --name/--target` (sandbox/commands/delivery.py, sandbox/delivery/context.py, sandbox/delivery/service.py)
- [ ] T018 [US4] `host retire-delivery` accepts `--name/--target` and falls back to the recorded scope (sandbox/commands/hosting.py, sandbox/delivery/hosting.py)

## Phase 7: Polish

- [ ] T019 Document refresh, refusal codes, fence guidance and stack selectors in docs/remote-hosting.md
- [ ] T020 [P] Update skills/sandbox-cli/SKILL.md and copy it byte-identically to .agents/skills/sandbox-cli/SKILL.md
- [ ] T021 [P] Add the 084 row to specs/README.md
- [ ] T022 Run the quickstart test set on xspeed-dev (Haiku helper) and compare with T001
- [ ] T023 Sol review (`gpt-6.1-sol`, high, `codex_office`, read-only) until it says safe; fix findings
- [ ] T024 Stage named paths only (no `git add -A`), commit, push `feat/front-door-refresh`, check `@{upstream}`, write the handback; flag the privacy/routing change for human security review before release (user rule) and leave the live refresh to the orchestrator

## Dependencies

T002-T004 before every story. US1 (T005-T010) before US2 and US3 (they extend the same function). US4 is independent of US1-US3. Polish last.

## Parallel examples

T003 and T004 together; T016-T018 alongside US2/US3; T020 and T021 together.

## Implementation strategy

MVP is US1+US2+US3 together (a refresh that could weaken a private target is not shippable alone). US4 is separable.
