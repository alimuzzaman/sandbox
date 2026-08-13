# Tasks: Safe Secret Inspection

**Input**: Design documents from `specs/041-safe-secret-inspection/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Tests are required because the specification defines explicit disclosure, filesystem, concurrency, CLI/MCP parity, and leak-prevention outcomes. Story tests are written first and observed failing before implementation.

**Organization**: Tasks are grouped by user story and ordered by shared dependencies.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the modular package and explicit configuration/registration seams.

- [X] T001 Create the `sandbox/secrets/` package skeleton and public error/result boundary in `sandbox/secrets/__init__.py` and `sandbox/secrets/models.py`
- [X] T002 [P] Add failing common secret-configuration normalization and explicit MCP `use` authorization tests in `tests/test_secret_config.py`
- [X] T003 Implement `secrets.sources` and `secrets.useProfiles` normalization in `sandbox/config/secrets.py`, `sandbox/config/manifest.py`, `sandbox/config/wordpress.py`, and `sandbox/config/compose.py`
- [X] T004 Convert the existing secrets command to owned `CommandSpec` parser composition while retaining `migrate-zshrc` in `sandbox/commands/secrets.py`, `sandbox/cli.py`, and `sandbox/commands/manifest.py`
- [X] T005 Exempt every secrets action from unrelated runtime reconciliation in `sandbox/cli.py` and add the regression seam in `tests/test_secret_commands.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build secure source, parser, audit, and service composition used by every story.

**Critical**: No user-story implementation starts until this phase passes focused tests.

- [X] T006 [P] Add failing inert parser and syntax-preservation tests in `tests/test_secret_parser.py`
- [X] T007 [P] Add failing safe-open, alias-containment, ownership, mode, type, link, size, count, and race tests in `tests/test_secret_service.py`
- [X] T008 [P] Add failing owner-only intent/outcome audit and forbidden-field tests in `tests/test_secret_service.py`
- [X] T009 Implement bounded inert literal-assignment parsing and syntax-preserving records in `sandbox/secrets/parser.py`
- [X] T010 Implement registered personal/project source resolution and descriptor-safe reads in `sandbox/secrets/sources.py`
- [X] T011 Implement owner-only durable audit intent/outcome records in `sandbox/secrets/audit.py`
- [X] T012 Implement transport-neutral request/result validation and safe reason codes in `sandbox/secrets/models.py`
- [X] T013 Compose the shared service with project config, personal resolver, runtime paths, and optional project scope in `sandbox/secrets/context.py`
- [X] T014 Create the core orchestration boundary and audit-first operation lifecycle in `sandbox/secrets/service.py`

**Checkpoint**: Sources, parsing, audit, and service composition pass without reading any real secret file.

---

## Phase 3: User Story 1 - Discover and inspect only the needed secret (Priority: P1) MVP

**Goal**: Default key-only inventory and bounded one-key metadata through CLI and explicitly authorized MCP.

**Independent Test**: Inspect a synthetic multi-key registered source and prove default names-only output, one-key safe states, unsafe-source refusal, audit-first behavior, and no runtime reconciliation.

### Tests for User Story 1

- [X] T015 [P] [US1] Add failing inventory, metadata, length-bucket, exact-length, cardinality, and no-leak service tests in `tests/test_secret_service.py`
- [X] T016 [P] [US1] Add failing CLI inventory/metadata and JSON parity tests in `tests/test_secret_commands.py`
- [X] T017 [P] [US1] Add failing opt-in MCP group, source-mode authorization, project-scope, and default-catalog tests in `tests/test_secret_mcp.py` and `tests/test_mcp_composition.py`

### Implementation for User Story 1

- [X] T018 [US1] Implement key inventory and safe metadata behavior in `sandbox/secrets/service.py`
- [X] T019 [US1] Implement `secrets inspect` human/JSON adapters in `sandbox/commands/secrets.py`
- [X] T020 [US1] Register an opt-in explicit secrets MCP group and injected service factory in `mcp/wp-server/tools/secrets.py`, `mcp/wp-server/tools/manifest.py`, and `mcp/wp-server/server.py`
- [X] T021 [US1] Verify the Story 1 focused tests and record the independent MVP result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: Agents can stop after names or metadata without any value-derived output.

---

## Phase 4: User Story 2 - Validate or mask one selected value (Priority: P1)

**Goal**: Reviewed shape validation and fixed non-expandable masking for one eligible credential.

**Independent Test**: Exercise recognized, unrecognized, boundary, repeated, and protected-class values and prove shape labels, `live_checked=false`, fixed disclosure, and CLI/MCP parity.

### Tests for User Story 2

- [X] T022 [P] [US2] Add failing classification, profile, validation, mask-boundary, protected-class, and repeated-disclosure tests in `tests/test_secret_policy.py`
- [X] T023 [P] [US2] Add failing CLI validation/masking and exact fixed-output tests in `tests/test_secret_commands.py`
- [X] T024 [P] [US2] Add failing MCP validation/masking parity and authorization tests in `tests/test_secret_mcp.py`

### Implementation for User Story 2

- [X] T025 [US2] Implement reviewed profiles, length buckets, protected classification, dangerous-name policy, and fixed masks in `sandbox/secrets/policy.py`
- [X] T026 [US2] Integrate validation and masking into the shared service in `sandbox/secrets/service.py`
- [X] T027 [US2] Implement CLI `validate` and masked inspection adapters in `sandbox/commands/secrets.py`
- [X] T028 [US2] Implement MCP `secret_validate` and authorized masked inspection in `mcp/wp-server/tools/secrets.py`
- [X] T029 [US2] Verify Story 2 tests and record the independent result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: Shape questions and eligible identification work without arbitrary character previews.

---

## Phase 5: User Story 3 - Use a secret without seeing it (Priority: P1)

**Goal**: Deliver one selected value to one trusted bounded child without parent export, argv exposure, or ordinary plaintext output.

**Independent Test**: Run synthetic trusted fixtures that confirm child receipt, minimal environment, dangerous-name refusal, timeout/process-group termination, cross-chunk redaction, output truncation, and MCP profile-only behavior.

### Tests for User Story 3

- [X] T030 [P] [US3] Add failing direct-argv, minimal-environment, destination-deny, timeout, process-group, output-bound, and streaming-redaction tests in `tests/test_secret_service.py`
- [X] T031 [P] [US3] Add failing local CLI run and parent-isolation tests in `tests/test_secret_commands.py`
- [X] T032 [P] [US3] Add failing MCP registered-profile-only and source-level `use` authorization tests in `tests/test_secret_mcp.py`

### Implementation for User Story 3

- [X] T033 [US3] Implement bounded child execution with minimal environment, process-group timeout, and streaming redaction in `sandbox/secrets/runner.py`
- [X] T034 [US3] Integrate audit-first use and configured use profiles in `sandbox/secrets/service.py`
- [X] T035 [US3] Implement local `secrets run` direct-argv adapter in `sandbox/commands/secrets.py`
- [X] T036 [US3] Implement MCP `secret_use_profile` without arbitrary command input in `mcp/wp-server/tools/secrets.py`
- [X] T037 [US3] Verify Story 3 tests and record the independent result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: Trusted programs can consume a selected fixture credential without the agent receiving it.

---

## Phase 6: User Story 4 - Update one secret without reading its file (Priority: P2)

**Goal**: Create or replace one literal assignment through protected input while preserving unrelated source content.

**Independent Test**: Update synthetic sources through TTY/stdin/reference/generation and prove byte preservation, revision conflicts, intent checks, profile checks, permissions, cleanup, atomic outcome, and value-free responses.

### Tests for User Story 4

- [X] T038 [P] [US4] Add failing syntax-preserving update, protected-stdin normalization, reference/generation, lock, revision, intent, duplicate, atomic-failure, and permission tests in `tests/test_secret_service.py`
- [X] T039 [P] [US4] Add failing CLI set input-channel and no-plaintext-output tests in `tests/test_secret_commands.py`

### Implementation for User Story 4

- [X] T040 [US4] Implement opaque source revisions, cooperative locking, one-record replacement, sync, atomic rename, and safe cleanup in `sandbox/secrets/writer.py`
- [X] T041 [US4] Implement protected stdin, hidden TTY, registered-reference copy, and reviewed generation orchestration in `sandbox/secrets/service.py`
- [X] T042 [US4] Implement `secrets set` create/replace/either CLI behavior in `sandbox/commands/secrets.py`
- [X] T043 [US4] Verify Story 4 tests and record the independent result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: One fixture key can change without caller access to old/new values or unrelated entries.

---

## Phase 7: User Story 5 - Reveal exactly one secret as a last resort (Priority: P3)

**Goal**: Provide a fresh-confirmed human-only TTY display with empty stdout and no programmable reveal surface.

**Independent Test**: Use fake controlling TTYs to prove warning, exact confirmation, one-value display, empty stdout, audit gating, and refusal of non-TTY, JSON, pipes, wildcard, multiple-key, cached, and MCP paths.

### Tests for User Story 5

- [X] T044 [P] [US5] Add failing fake-TTY warning, confirmation, stdout-empty, audit, and refusal tests in `tests/test_secret_commands.py`
- [X] T045 [P] [US5] Add an architecture assertion that no MCP reveal/value tool exists in `tests/test_secret_mcp.py`

### Implementation for User Story 5

- [X] T046 [US5] Implement one-key audit-first reveal orchestration in `sandbox/secrets/service.py`
- [X] T047 [US5] Implement controlling-TTY-only `secrets reveal` with exact-key confirmation in `sandbox/commands/secrets.py`
- [X] T048 [US5] Verify Story 5 tests without printing even synthetic fixture values and record the result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: Full display is available only as a local human exception and never through stdout or MCP.

---

## Phase 8: User Story 6 - Follow the least-disclosure agent workflow (Priority: P2)

**Goal**: Make the safe sequence and incident response the default documented agent behavior.

**Independent Test**: Review placeholder-only discovery, validation, use, update, and reveal scenarios and verify the skill chooses the lowest-disclosure operation and never requests pasted credentials.

### Tests for User Story 6

- [X] T049 [P] [US6] Add skill discovery/content and unsafe-example regression tests in `tests/test_secret_commands.py`

### Implementation for User Story 6

- [X] T050 [P] [US6] Author the agent runbook in `skills/secret-inspection/SKILL.md`
- [X] T051 [P] [US6] Author operator usage, warnings, examples, and threat boundaries in `docs/secret-inspection.md`
- [X] T052 [US6] Link the feature from `README.md` and align CLI/MCP help text in `sandbox/commands/secrets.py` and `mcp/wp-server/tools/secrets.py`
- [X] T053 [US6] Verify Story 6 content tests and record the independent result in `specs/041-safe-secret-inspection/tasks.md`

**Checkpoint**: Agents have a detailed safe workflow that prefers use over reveal and update over raw file reads.

---

## Phase 9: Polish & Cross-Cutting Verification

**Purpose**: Close composition inventories, leak tests, performance, docs-with-code, and live evidence.

- [X] T054 [P] Update exact CLI/MCP composition and modularity inventories in `tests/test_command_composition.py`, `tests/test_mcp_composition.py`, `tests/test_architecture_boundaries.py`, and `tests/test_modularity.py`
- [X] T055 [P] Add source-size performance and adaptive-disclosure regression coverage in `tests/test_secret_service.py`
- [X] T056 Run all focused feature and architecture tests from `specs/041-safe-secret-inspection/quickstart.md` one group at a time and resolve failures
- [X] T057 Execute isolated live CLI inspection, validation, mask, run, set, reveal-refusal, no-reconciliation, and leak checks from `specs/041-safe-secret-inspection/quickstart.md`
- [X] T058 Execute explicit fake-server MCP composition and authorized/unauthorized tool checks from `specs/041-safe-secret-inspection/quickstart.md`
- [X] T059 Run `git diff --check`, review all changed files for secret literals or value-bearing diagnostics, and confirm no real secret source was opened
- [X] T060 Mark completed tasks and final observed evidence in `specs/041-safe-secret-inspection/tasks.md`
- [X] T061 Stage only feature-owned files, commit the verified implementation on `latest`, and push `latest` without force in the repository root

---

## Phase 10: Approved follow-up - structured credential files

**Purpose**: Extend the registered-source boundary to documented credential
containers without adding arbitrary paths, format inference, or broader MCP
value access.

- [X] T062 Record official provider-format provenance and the open-source wrapper decision in `specs/041-safe-secret-inspection/research.md`
- [X] T063 Add an all-synthetic provider fixture corpus and exact selector expectations under `tests/fixtures/secret-formats/`
- [X] T064 Add bounded inert JSON, INI, properties, TOML, YAML, XML, PEM, opaque, and binary adapters in `sandbox/secrets/formats.py`
- [X] T065 Extend explicit source configuration and service dispatch while retaining dotenv-only mutation and structured MCP keys/metadata limits
- [X] T066 Add parser, service, CLI, malformed-input, exception-chain, and traceback non-disclosure regressions
- [X] T067 Update the operator guide, agent skill, README, plan, and research with structured selector and refusal behavior
- [X] T068 Run focused plus composition regressions, static checks, fixture provenance/credential scans, then commit and push the verified follow-up on `latest`

---

## Phase 11: Approved follow-up - metadata-only source probing

**Purpose**: Let agents determine whether a registered secret file exists, has
contents, and is broker-safe without reading or parsing the file.

- [X] T069 Define the metadata-only source contract and exact-size disclosure boundary in the operator guide and agent skill
- [X] T070 Implement no-follow, nonblocking source probing without read syscalls in `sandbox/secrets/sources.py`
- [X] T071 Add audited service and CLI `source-info` operations with local-only opt-in exact byte size
- [X] T072 Add explicitly authorized MCP `secret_source_info` without an exact-size argument
- [X] T073 Add missing, empty, unsafe-type, symlink, inaccessible-error, no-read, CLI, MCP, and configuration regressions
- [X] T074 Run the complete relevant suite, static checks, diff security review, then commit and push the verified metadata follow-up on `latest`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup**: starts immediately.
- **Foundational**: depends on Setup and blocks all stories.
- **US1 and US2**: depend on Foundational; US2 may proceed in parallel with US1 after shared models stabilize.
- **US3**: depends on Foundational and the destination/classification portion of US2.
- **US4**: depends on Foundational; profile-validated writes also use US2 policy.
- **US5**: depends on Foundational and one-key service selection from US1.
- **US6**: may draft after contracts but final verification depends on US1–US5 interfaces.
- **Polish**: depends on every selected story.

### User Story Dependencies

- **US1**: standalone MVP after Foundational.
- **US2**: independently testable after Foundational.
- **US3**: uses US2 dangerous-destination policy but has its own runner and tests.
- **US4**: uses foundational parser/source/audit and optional US2 validation profiles.
- **US5**: uses US1 one-key retrieval and foundational audit; no MCP dependency.
- **US6**: documentation-only behavior depends on final interfaces from all prior stories.

### Parallel Opportunities

- Configuration, parser, source, and audit failing tests can be authored in parallel.
- Within each story, service, CLI, and MCP tests marked `[P]` touch separate initial files.
- US1 and US2 can be implemented in parallel after the foundation; US4 and US5 can proceed in parallel once one-key service selection is stable.
- Skill and operator guide authoring can proceed in parallel.

## Parallel Examples

### User Story 1

```text
T015 service inventory tests
T016 CLI inventory tests
T017 MCP catalog/authorization tests
```

### User Story 3

```text
T030 runner/service tests
T031 CLI run tests
T032 MCP profile tests
```

### User Story 6

```text
T050 agent skill
T051 operator guide
```

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational phases.
2. Complete US1 names/metadata through CLI and authorized MCP.
3. Run the Story 1 checkpoint before higher-disclosure capabilities.

### Incremental Delivery

1. Add validation/masking and prove the fixed disclosure budget.
2. Add child-scoped use and prove parent/output isolation.
3. Add targeted updates and prove non-target preservation.
4. Add human-only reveal and prove every programmable path refuses it.
5. Land the skill/docs and full composition/live verification.

## Notes

- `[P]` marks tasks that operate on different files or can be authored before their shared implementation.
- Tests must be observed failing before the corresponding implementation.
- Use synthetic fixtures only; never inspect an existing `.env`, `sandbox.local.yml`, personal secret file, or other real credential source during development.
- The final commit and push happen only after focused tests and isolated live evidence pass.

## Observed implementation evidence

- 2026-08-11: Focused suites passed independently: config 13, parser 13,
  policy 7, service/writer/runner 21, CLI/live 7, and MCP 3 tests.
- 2026-08-11: Composition suites passed independently: command 6, MCP 16,
  architecture boundaries 16, and modularity inventory 2 tests.
- 2026-08-11: The isolated live CLI test used a temporary project and temporary
  `SANDBOX_HOME` to prove key inventory, offline validation, fixed masking,
  redacted direct-child use, protected stdin update, non-TTY reveal refusal,
  and absence of Compose reconciliation.
- 2026-08-11: Fake-TTY and fake-MCP tests proved TTY-only reveal with empty
  stdout, exact four-tool MCP registration, default-catalog exclusion, bounded
  denials, and profile-only use.
- 2026-08-11: `sb skill list` and `sb skill show secret-inspection` passed after
  the supported skill edit/load loop. `py_compile`, `git diff --check`, and the
  credential-pattern scan passed. No existing personal, project, `.env`, or
  machine secret source was opened; all exercised values were temporary,
  synthetic fixtures.
- 2026-08-11 follow-up: Added 18 synthetic provider/container fixtures covering
  10 explicit broker formats. Focused secret suites passed 76 tests before the
  final malformed-parser matrix was added; the structured parser/service/CLI
  subset then passed 38 tests. Composition/config regressions passed 50 tests.
- 2026-08-11 final follow-up: The combined secret, configuration, CLI, MCP,
  composition, architecture, and modularity suite passed 152 tests. Metadata
  regressions proved no read syscall, missing/empty/type states, symlink and
  special-file refusal, source-swap and oversize classification, stable OS-error
  output, default size buckets, CLI-only exact size, and explicit MCP
  `source_info` authorization. Compilation, fixture integrity, skill rendering,
  and `git diff --check` passed.
- 2026-08-11 security receipt: Codex Security diff scan
  `c14205a9-eb80-4189-821c-e40d8f3cde8d` sealed 21/21 worklist rows across
  seven surfaces with zero findings. Implementation commit `6b4c060` was pushed
  to `origin/latest` before this evidence-only closeout.

## Phase 12: Convergence — 2026-08-13 (27-feedback redaction)

These tasks remain open and do not revise the prior evidence or completion
markers.

- [x] T075 [US1/US2/US6] Add the `81f43e6f` synthetic redaction corpus for
  bearer/API assignments, common provider prefixes, Basic Auth URL userinfo,
  token query parameters, mixed case/spacing, and nested exception chains.
- [ ] T076 [US2/US6] Prove the shared redaction service is applied consistently
  to CLI, MCP, feedback, remote verification, job output, and child stderr;
  assert no raw traceback or command/URL bypass remains.
- [ ] T077 [US1/US6] Record only pattern names and aggregate pass/fail counts in
  implementation evidence, run `git diff --check` and focused secret suites,
  and retain the no-real-secret fixture boundary.

## Phase 13: Feedback query JSON convergence — 2026-08-13

- [X] T078 [US6] Normalize internal UTC `datetime` filters to RFC 3339 strings
  before returning feedback list receipts, closing feedback
  `eb496b17c1c33e47aaa7e74fde5ea4bf` without changing append-only records.
- [X] T079 [US6] Add service and live CLI regressions proving `--since` and
  `--until` response filters round-trip through JSON while bounded untrusted
  records retain the established redaction projection.
