# Tasks: Host Stack Templates

**Input**: Design documents from `specs/071-host-stack-templates/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: requested by the owner (types first, then tests, then implementation; tests proportional). Tests run only in the Haiku test helper.

## Phase 1: Setup

- [ ] T001 Point the agent context marker at this plan in CLAUDE.md and set `.specify/feature.json` to `specs/071-host-stack-templates`

## Phase 2: Foundational (types and shared guards)

- [ ] T002 Add `HostingRefusal(HostingError)` with a stable `code` attribute and `branch_allowed(branch, patterns)` (fnmatchcase; empty branch passes only `"*"`) in sandbox/core/_hosting.py
- [ ] T003 [P] Define `StackTemplate`, `StackRef`, `StackTemplateError` and the placeholder grammar/allowed-field paths as types in sandbox/hosting/stack_template.py
- [ ] T004 [P] Define `StackRecord` shape, `STACK_PORT_RANGE = range(18100, 18200)`, `StackRegistryError` in sandbox/hosting/stack_registry.py
- [ ] T005 [P] Define `HostnameOwnedError` (`hostname_owned_by_other_project`) and `holdings()` signature in sandbox/hosting/hostname_ownership.py

## Phase 3: User Story 4 - Templates cannot be applied by accident (P2, blocking for US1)

- [ ] T006 [US4] Test: validate_manifest refuses a `stack:` block or any placeholder with `stack_template_requires_stack_up`; ordinary manifest unchanged, in tests/test_host_stack.py
- [ ] T007 [US4] Implement template detection in `validate_manifest` and a JSON-coded refusal in the `cmd_host` validation error path in sandbox/core/_hosting.py and sandbox/commands/hosting.py

## Phase 4: User Story 1 - Bring up a stack (P1) MVP

- [ ] T008 [US1] Tests: template parse/validate (allowed fields, stray braces, undeclared port, basic-auth exemption), render ({slug}, {SLUG}, {port:x}, {ref_branch}), empty allowed_branches refusal, rendered-manifest acceptance only under runtime/stacks/<slug>/, env-file value for `${MAILPIT_PORT:-8025}` (docker compose config when available), in tests/test_host_stack.py
- [ ] T009 [US1] Tests: registry port allocation (range, reuse, pin, other-stack refusal, bound-port skip, own-project binding accepted) and web allocation skipping 18100-18199, in tests/test_host_stack.py
- [ ] T010 [US1] Tests: ref resolution (local-only branch, remote-tracking, detached commit, unknown ref) and `stack up --plan` changes nothing / `--confirm` submits one job with expected argv (job-start subprocess faked), in tests/test_host_stack.py
- [ ] T011 [US1] Implement parse/validate/render in sandbox/hosting/stack_template.py
- [ ] T012 [US1] Implement rendered-manifest acceptance (`stack_instance`, absolute source_root under a Git checkout, location check) and `validated["stack"]`; split `validate_manifest` load from validation; reserve 18100-18199 in `allocate_loopback_port` in sandbox/core/_hosting.py
- [ ] T013 [US1] Implement registry store (flock, 0600, atomic write) and port allocation in sandbox/hosting/stack_registry.py
- [ ] T014 [US1] Implement `stack up` (ref resolution, template read from commit, remote port probe, render, worktree, rendered write, job-start submission, record) in sandbox/commands/host_stack.py
- [ ] T015 [US1] Teach `_validate_apply_source` the stack detached-HEAD branch and glob policy; push stack sources to the immutable ref (`allow_detached`); glob in `_recovery_observer`, in sandbox/commands/hosting.py
- [ ] T016 [US1] Add `stack` action, `up|list|down`, `--template`, `--name`, `--ref`, `--port`, `--plan` in sandbox/cli.py and dispatch in sandbox/commands/hosting.py

## Phase 5: User Story 2 - Hostname ownership guard (P1)

- [ ] T017 [US2] Tests: holdings from `hostnames`, `dns_coverage.hostname`, `records[].applied.name` and stack records; other remote ignored; same project passes, in tests/test_host_stack.py
- [ ] T018 [US2] Implement holdings + check in sandbox/hosting/hostname_ownership.py; call it in `cmd_host` apply right after state load and in `stack up`; record `hostnames` when an apply is accepted in sandbox/commands/hosting.py

## Phase 6: User Story 3 - List and tear down (P2)

- [ ] T019 [US3] Tests: list fields and job-derived status; down refuses without --confirm, keeps record when destroy fails, releases ports and removes worktrees on success, in tests/test_host_stack.py
- [ ] T020 [US3] Implement `stack list` and `stack down` in sandbox/commands/host_stack.py

## Phase 7: User Story 5 - Branch globs (P3)

- [ ] T021 [US5] Test `branch_allowed` and `_validate_apply_source` with `feat/*`, exact, `"*"` in tests/test_host_stack.py

## Phase 8: Polish

- [ ] T022 [P] Document stack templates in docs/remote-hosting.md and docs/sandbox-config-reference.md
- [ ] T023 [P] Document `sb host stack` in skills/sandbox-cli/SKILL.md and its .agents mirror
- [ ] T024 Run the hosting and new suites in the Haiku helper; exercise `sb host stack up --plan` against a fixture template locally
- [ ] T025 Independent Luna review of the diff; fix or answer every finding

## Dependencies

- Phase 2 before all stories. US4 before US1 (US1's rendered manifests rely on the template refusal and acceptance split). US2 and US3 depend on US1's registry. US5 is independent after T002.

## Parallel Opportunities

- T003, T004, T005 (separate new files). T022 and T023 (separate docs).

## Implementation Strategy

MVP is US4 + US1 (render and submit). US2 lands in the same branch because the owner requires it before cutover. US3 and US5 complete the set; all ship together in one release phase.
