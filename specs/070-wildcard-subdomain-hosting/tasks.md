# Tasks: Wildcard Subdomain Hosting

**Input**: `specs/070-wildcard-subdomain-hosting/` (plan, spec, research, data-model, contracts, quickstart)

**Tests**: required. The owner's rule is types first, then tests, then implementation, so each story lists tests before implementation. Tests use fake provider and SSH doubles, as in `tests/test_front_door_nginx.py`. No test touches a real provider, remote or DNS.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Create the package skeleton and fixed error type `CoverageError(code, message)` in `sandbox/hosting/zone_coverage/__init__.py`, with the refusal codes from `contracts/cli.md`

## Phase 2: Foundational

**Blocks every story.**

- [ ] T002 [P] Write tests for states, allowed transitions, document validation, higher-schema refusal and `eligible()` (first-level, `*`, `_` labels, exact node, subordinate node, other zone, apex) in `tests/test_zone_coverage_models.py`
- [ ] T003 Implement `sandbox/hosting/zone_coverage/models.py`: state enum, transition table, `validate_document`, `eligible(hostname, zone, zone_records)`
- [ ] T004 [P] Write tests that run the store program with a real temporary home through a local subprocess: read absent, list, begin and stale generation, live lease refusal, same-kind resume after expiry with a new id, old id refused after takeover, different kind refused, phase for holder only and lease renewal, finish, dependent bind and release, corrupt document refusal with file unchanged, size bounds, file mode 0600, in `tests/test_zone_coverage_program.py`
- [ ] T005 Implement the fixed program per `contracts/zone-store.md` in `sandbox/hosting/zone_coverage/program.py`, standard library only, imports inside the program text
- [ ] T006 Write tests with a fake `ssh_run` (timeout, bad JSON, non-zero exit, refusal as data) in `tests/test_zone_coverage_program.py`, then implement the controller client in `sandbox/hosting/zone_coverage/client.py`, with a 15 s call bound, one-JSON-line validation and `coverage_store_unavailable` on anything else, following `sandbox/hosting/coordination/client.py`
- [ ] T007 [P] Add tests for `Client.zone_records` pagination, its page bound and invalid responses in `tests/test_cloudflare_zone_records.py`
- [ ] T008 Implement `Client.zone_records(zone_id)` in `sandbox/core/_cloudflare.py`: paginated, bounded, refusing rather than truncating
- [ ] T009 Add `ZONE_COVERAGE` to the capabilities in `sandbox/hosting/front_door/__init__.py`, declared for `nginx` only, and test that `require_capability` refuses on `caddy` in `tests/test_front_door_nginx.py`

## Phase 3: User Story 1 - Turn on zone coverage safely (P1)

**Goal**: plan, enable, status, and a safe retry. **Independent test**: fake provider and SSH: refusals write no DNS, and the record write happens only after the fallback probe passes.

- [ ] T010 [P] [US1] Write tests for fallback rendering: `server_name *.zone`, 80 and 443, server-level `return 404`, both headers `always`, no `default_server`, identical body in both servers, IPv6 and http2 feature flags, in `tests/test_zone_coverage_fallback.py`
- [ ] T011 [P] [US1] Write overlap tests in `tests/test_front_door_nginx.py`: a declared exact name against the registered fallback file is no conflict, in both the module function and the program text at `nginx.py:479` and `nginx.py:516`; a declared wildcard against it conflicts; the same `server_name` in any other file conflicts; a look-alike path conflicts; the fallback's own row lists no exact routes as conflicts in `probe` and `summarize`
- [ ] T012 [US1] Add `is_coverage_fallback_file` and the exemption to `find_conflicts` and to the embedded program copy in `sandbox/hosting/front_door/nginx.py`, and a `classify_for_fallback(pairs, zone)` returning permitted exact exceptions and blocking wildcard or regex rows
- [ ] T013 [US1] Implement rendering, install through `apply_route`, origin probe with a random label requiring 404 and marker, and `ensure` and forced-renew certificate helpers in `sandbox/hosting/zone_coverage/fallback.py`
- [ ] T014 [P] [US1] Write service tests in `tests/test_zone_coverage_service.py`: plan writes nothing; each prerequisite failure (capability, nginx preflight, zone missing, ssl not strict, existing `*.zone` record, panel wildcard, panel regex, ordinary Sandbox wildcard) refuses with zero provider writes and no fallback; fallback probe failure means no DNS write; call order is store, certificate, fallback, probe, record, edge check, finish; `--stop-before-publish` ends in `fallback_only`; plan digest staleness refuses; rerun after completion makes no writes
- [ ] T015 [P] [US1] Write failure tests in the same module: provider timeout after a write leaves `incomplete` with the unknown effect reported, and the retry reads before writing; edge check failure with successful rollback ends in `fallback_only`; failed rollback ends in `incomplete`; a simulated crash after each phase is finished by a resume with no duplicate write; retrying `incomplete(disable)` never publishes DNS; the fallback is never removed by any of these; exceptions appear in the plan and status output
- [ ] T016 [US1] Implement `plan_enable`, `enable` and `status` in `sandbox/hosting/zone_coverage/service.py`, per the state machine in `data-model.md`. The plan output states the consequence for older checkouts from research R1
- [ ] T017 [US1] Add `sb remote coverage` to `sandbox/cli.py` and `sandbox/commands/remote.py`, per `contracts/cli.md`, with `--zone`, `--enable`, `--disable`, `--repair`, `--renew-certificate`, `--remove-fallback`, `--stop-before-publish`, reusing `--plan`, `--confirm`, `--plan-digest`, `--json`, with CLI tests in `tests/test_zone_coverage_service.py`
- [ ] T018 [US1] Make `sb remote edge` mark a fallback file with `coverage_fallback: true`, make `--remove-route` refuse it, and change the overdue-certificate text for a fallback to name `--renew-certificate`, with tests, in `sandbox/hosting/front_door/nginx.py`, `sandbox/commands/remote.py` and `tests/test_front_door_nginx.py`
- [ ] T018a [US1] Document enabling, status, the older-checkout consequence, the anonymity meaning (FR-044) and the FR-021 limit in `docs/remote-hosting.md`, and add a `CLAUDE.md` gotcha and a `skills/sandbox-cli/SKILL.md` section, in the same stage

## Phase 4: User Story 3 - A name with no site returns an error (P1)

**Goal**: the error is correct everywhere. **Independent test**: render-level and probe-level checks.

- [ ] T019 [P] [US3] Add tests to `tests/test_zone_coverage_fallback.py`: body names no zone, host or site; the rendered config has no `location`, `proxy_pass`, `root` or `try_files`; the probe rejects a 200, a missing marker and a different body
- [ ] T020 [US3] Use a fixed body with no name in it and a fixed marker header in `sandbox/hosting/zone_coverage/fallback.py`, and state FR-022 in `docs/remote-hosting.md`

## Phase 5: User Story 2 - A new site needs no DNS change (P1)

**Goal**: covered apply with no DNS write. **Independent test**: fake provider sees zero writes on apply, with a resolver that always fails for the target name.

- [ ] T021 [P] [US2] Write decision-table tests for every row of research R6 in `tests/test_zone_coverage_targets.py`
- [ ] T022 [US2] Validate `cloudflare.dns` (`auto`, `exact`; anything else refuses) in `sandbox/core/_hosting.py`, and make `desired_plan` emit no records when `validated["dns_coverage"]["mode"] == "shared"`, with tests in `tests/test_zone_coverage_targets.py`, confirming that output is byte-identical for manifests without coverage
- [ ] T023 [P] [US2] Write edge probe tests in `tests/test_zone_coverage_targets.py`, with a local TLS server and an injected resolver: certificate verified against the target name, SNI and Host set; random label failure gives `unverified`; a real HTTP failure after retries gives `failed`; resolver failure for the target name gives `negative` and does not fail
- [ ] T024 [US2] Implement `decide`, `bind_pending`, `bind_bound` and the public edge probe in `sandbox/hosting/zone_coverage/targets.py`, per research R3 and R6
- [ ] T024a [US2] Write an apply-level test in `tests/test_zone_coverage_targets.py` that drives the real apply path with fake provider and SSH: zero DNS write calls while the resolver fails for the target name, delivery succeeds, evidence shows `resolver: negative`, the origin certificate call stays allowed
- [ ] T025 [US2] Wire the decision into plan and apply in `sandbox/commands/hosting.py`: bind before `_desired_edge_intent` and `_guarded_host_apply_plan`, register `pending` before the first target effect, skip the DNS loop by empty records, use the edge probe instead of `_verify_edge` for shared targets, mark `bound` after verification, keep `_guarded_host_apply_plan`'s provider check valid with zero records
- [ ] T026 [US2] Add `evidence` to the state `edge` block, the apply result and `host status`, and show coverage mode in `sb host plan`, in `sandbox/commands/hosting.py`
- [ ] T027 [US2] Add optional `dns_coverage` to the edge intent only when shared, and make `_continue_host_edge_only` and recovery treat a shared target's empty records as expected, in `sandbox/commands/hosting.py`, with tests proving that existing intent digests do not change

## Phase 6: User Story 4 - Assign a subdomain elsewhere (P2)

- [ ] T028 [P] [US4] Add tests in `tests/test_zone_coverage_targets.py`: a TXT, A, CNAME, NS or subordinate record at the name refuses `coverage_name_ineligible` with the reason, with zero writes; no operation writes to a record that is not owned
- [ ] T029 [US4] Ensure the refusal text names the record type and how to choose `cloudflare.dns: exact`, in `sandbox/hosting/zone_coverage/targets.py`

## Phase 7: User Story 5 - A site can still have its own record (P2)

- [ ] T030 [P] [US5] Add tests in `tests/test_zone_coverage_targets.py`: `dns: exact` writes as today; a changed record refuses `managed_record_drift`, with zero writes; `--allow-record-overwrite` writes; an identical record is adopted with no refusal; in a zone with no coverage document, today's overwrite is unchanged
- [ ] T031 [US5] Implement the drift rule and `--allow-record-overwrite` in the record loops at `sandbox/commands/hosting.py:3707` and `sandbox/commands/hosting.py:4704`, and in `sandbox/cli.py`

## Phase 8: User Story 6 - Existing sites stay as they are (P2)

- [ ] T032 [P] [US6] Add tests in `tests/test_zone_coverage_targets.py`: enable changes no file or record of an existing target; a target with an owned record stays `exact` under `auto`; a multi-route target refuses or stays `exact` per research R6; applying at a hostname owned by another exact route refuses and names it; a blocked enable changes nothing
- [ ] T033 [US6] Make those tests pass in `sandbox/hosting/zone_coverage/targets.py` and `sandbox/hosting/zone_coverage/service.py`, and document the target rules in `docs/remote-hosting.md`

## Phase 9: User Story 7 - Remove a site (P2)

- [ ] T034 [P] [US7] Add destroy tests to `tests/test_hosting_destroy.py`: a covered target makes zero DNS calls, keeps its own certificate cleanup and releases its dependent; release failure is reported, and does not undo the destroy; a managed target behaves as today; shared records and the fallback are never in the destroy's removal set, including a name collision test
- [ ] T035 [US7] Implement release and the `shared_coverage_retained` report in `sandbox/hosting/destroy.py`

## Phase 10: User Story 8 - Turn coverage off (P2)

- [ ] T036 [P] [US8] Add tests in `tests/test_zone_coverage_service.py`: disable plan names dependents; confirm needs that digest; a dependent added after the plan makes it stale; store failure refuses; only recorded record ids are deleted, and a changed record is kept and reported; routes and the fallback are untouched; re-enable needs no target change; `--remove-fallback` refuses unless state is `disabled` or `fallback_only` and the listing shows the record absent
- [ ] T037 [US8] Implement `plan_disable`, `disable`, `remove_fallback` and `release_dependent` in `sandbox/hosting/zone_coverage/service.py`, with a test that `--release-dependent` needs `--confirm` and removes only the named key, and document them in `docs/remote-hosting.md`

## Phase 11: User Story 9 - See and handle drift (P2)

- [ ] T038 [P] [US9] Add tests in `tests/test_zone_coverage_service.py` for status with: record deleted, repointed, proxy off; a new node at a dependent's name; provider failure gives `unavailable`, and never healthy; fallback missing while DNS may exist gives `safe: false`; zone TLS mode not strict; edge TLS probe outcomes; no document with a `*.zone` record or fallback file reported as unmanaged, `decide` refusing, and enable adopting an equal record; plus repair: plan, confirm, and no repair without confirm
- [ ] T039 [US9] Implement observation and `plan_repair`, `repair` in `sandbox/hosting/zone_coverage/service.py`, and document drift in `docs/remote-hosting.md`

## Phase 12: User Story 10 - Mixed versions and concurrent work (P3)

- [ ] T040 [P] [US10] Add tests: the pre-feature `find_conflicts` (a copy in the test of today's semantics) refuses an exact apply against the fallback, as a pinned regression of research R1; a second controller's `begin` refuses during a live lease; a pending dependent blocks stale disable; a higher schema refuses; status reports `safe: false` when the fallback file disappears, as an older `--remove-route` would cause, in `tests/test_zone_coverage_program.py`, `tests/test_zone_coverage_service.py` and `tests/test_front_door_nginx.py`
- [ ] T041 [US10] Add a test that no module under `sandbox/hosting/zone_coverage/` imports the remote runtime or reads its revision (FR-042), in `tests/test_zone_coverage_service.py`, and make T040 pass

## Phase 13: User Story 11 - Keep the fallback certificate valid (P3)

- [ ] T042 [P] [US11] Add tests in `tests/test_zone_coverage_fallback.py`: forced renew installs only after issue succeeds; issue failure keeps the old file; works with zero dependents; status shows `not_after` and `renewal_overdue`
- [ ] T043 [US11] Implement `renew_certificate` in `sandbox/hosting/zone_coverage/service.py`, with nginx reload through the existing transaction, and document it in `docs/remote-hosting.md`

## Phase 14: Polish

- [ ] T044 [P] Review `docs/remote-hosting.md` as a whole against the final behaviour, including FR-022 and FR-044
- [ ] T045 [P] Bring the `CLAUDE.md` gotcha and `skills/sandbox-cli/SKILL.md` in line with the final behaviour, including the nginx conflict text
- [ ] T046 Confirm `sandbox/remote_runtime/shapes.py` stays green with no protocol bump
- [ ] T047 Run `quickstart.md` Stage A read-only, Stage B only after the owner approves the fallback install knowing older checkouts then refuse applies under the zone, and Stage C onward only after the owner's explicit go

## Dependencies

- Phase 2 blocks everything. US1 then US3 come first, since the fallback must exist before anything else.
- US2 needs US1. US4, US5, US6 and US7 need US2. US8, US9 and US11 need US1. US10 needs US1 and US2.
- Polish needs the stories it documents.

## Parallel opportunities

Test tasks marked [P] touch separate files or sections, but one implementer works through them in order here, because the feature has one owner for integration.

## Implementation strategy

Deliver in four reviewed stages, each with its own self-test and independent review before the next:

1. **Stage 1**, T001-T020: foundation, enable, error response, with docs. Nothing changes for targets on any real zone.
2. **Stage 2**, T021-T035: target wiring, drift rule, destroy, with docs.
3. **Stage 3**, T036-T043: disable, drift, compatibility, certificate, with docs.
4. **Stage 4**, T044-T047: final docs review, then live proof.

Each code stage ends with the read-only smoke in quickstart step 0, because Stage 1 changes the overlap check for every nginx apply. MVP is Stages 1 and 2. No stage enables coverage on a real zone.
