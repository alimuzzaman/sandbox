# Tasks: Sandbox Modular Boundaries

**Input**: Design documents from `specs/022-sandbox-modular-boundaries/`

**Prerequisites**: [prd.md](prd.md), [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Required. Write each contract/behavior test first and confirm it fails for the intended boundary before implementation.

**Organization**: Tasks are grouped by independently testable user story. Execute sequentially by phase with one writer by default. Stop on unexplained WordPress, registry, remote, Hermes, authorization, or persisted-state drift.

## Phase 1: Setup and Baseline Evidence

**Purpose**: Freeze observable behavior and create reviewable inventories before changing shared paths.

- [X] T001 Create the implementation trace and phase evidence template in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T002 Record exact CLI command names, aliases, parser ownership, scope, representative help/error output, and exit behavior in `specs/022-sandbox-modular-boundaries/cli-inventory.md`
- [X] T003 [P] Record exact MCP tool/group names, required parameters, response-shape fixtures, and current bootstrap ownership in `specs/022-sandbox-modular-boundaries/mcp-inventory.md`
- [X] T004 [P] Record direct registry/config readers, wildcard/back-filled imports, runtime-kind branches, concrete dependency construction, and Hermes concern ownership in `specs/022-sandbox-modular-boundaries/dependency-inventory.md`
- [X] T005 [P] Add representative registry v1/v2/current/future/unknown-field fixtures in `tests/fixtures/modularity/registry/`
- [X] T006 [P] Add representative global/project/override/label/legacy WordPress config fixtures in `tests/fixtures/modularity/config/`
- [X] T007 Capture current WordPress ensure/status/WP-CLI/REST/tests/domain/HTTPS/lifecycle evidence using Sandbox tools in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T008 Capture current remote and Hermes status/job/gateway/public-access/backup contract evidence using Sandbox commands in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: Public surfaces, state formats, dependency debt, and live baselines are reviewable; production behavior is unchanged.

---

## Phase 2: Foundational Contracts and Boundary Guards

**Purpose**: Add contracts and test seams without switching production behavior.

**CRITICAL**: No production migration begins until these tests and contracts are reviewed.

- [X] T009 [P] Add failing duplicate/ordering and test-registration tests for schemas/adapters in `tests/test_runtime_contracts.py`
- [X] T010 [P] Add repository protocol parity, supported/future/corrupt fixture, and unknown-field contract tests in `tests/test_registry_repository.py`
- [X] T011 [P] Add failing process/HTTP/port/path/proxy fake contracts and zero-side-effect recorder tests in `tests/test_service_contracts.py`
- [X] T012 [P] Add failing command/tool manifest duplicate, ownership, and deterministic-order tests in `tests/test_command_composition.py` and `tests/test_mcp_composition.py`
- [X] T013 [P] Add failing no-new-wildcard, no-new-back-fill, approved-kind-branch, direct-registry-access, and dependency-direction guards in `tests/test_architecture_boundaries.py`
- [X] T014 Define descriptor, schema, capability, adapter, request/result/error, and dependency contracts in `sandbox/runtimes/base.py` and `sandbox/application/context.py`
- [X] T015 Define registry repository and transaction contracts in `sandbox/project_registry/base.py`
- [X] T016 Define process, HTTP, port, path, and proxy service contracts plus fakes in `sandbox/services/` and `tests/fakes/sandbox_services.py`
- [X] T017 Extend the existing command registry with backward-compatible `CommandSpec`, duplicate rejection, scope, capability, confirmation, and legacy-bridge metadata in `sandbox/registry.py`
- [X] T018 Define `ToolGroupSpec`, dependency keys, deterministic composer skeleton, and duplicate detection in `mcp/wp-server/composition.py` and `mcp/wp-server/dependencies.py`

**Checkpoint**: Contracts and guard failures describe the target boundary; no existing production caller has switched.

---

## Phase 3: User Story 1 — Add a Project Schema Independently (Priority: P1)

**Goal**: Select project kind before defaults and resolve WordPress through a registered schema without changing legacy results.

**Independent Test**: Register a test-only schema without editing central config/registry/CLI/MCP code, prove duplicate rejection, and compare every legacy WordPress fixture byte-for-observable-result.

### Tests for User Story 1

- [X] T019 [P] [US1] Add failing kind-before-default, omitted-kind, invalid-common-name, duplicate-schema, and side-effect-free parsing tests in `tests/test_config_descriptors.py`
- [X] T020 [P] [US1] Add failing legacy config precedence and normalized WordPress parity tests from `tests/fixtures/modularity/config/` in `tests/test_config_facade.py` and `tests/test_sandbox.py`
- [X] T021 [P] [US1] Add failing compatibility-facade and no-new-consumer tests for shipped `sandbox_core.py` config functions in `tests/test_config_facade.py`

### Implementation for User Story 1

- [X] T022 [US1] Implement common descriptor models, discovery, allowed-root validation, and side-effect-free kind selection in `sandbox/config/descriptors.py`
- [X] T023 [US1] Implement explicit schema registry, deterministic composition, and duplicate rejection in `sandbox/config/registry.py`
- [X] T024 [US1] Move WordPress defaults/plugin identity/plugin-map normalization behind the WordPress schema in `sandbox/config/wordpress.py`
- [X] T025 [US1] Implement legacy config compatibility delegation without eager behavior change in `sandbox/config/facade.py` and `sandbox_core.py`
- [X] T026 [US1] Confirm existing consumers continue through the public config facade without adding direct schema dependencies in `sandbox/core/_instances.py` and relevant `sandbox/commands/` modules
- [X] T027 [US1] Run descriptor/config/facade tests plus the recorded WordPress config baseline and append evidence to `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: A second schema can be added without touching WordPress normalization, but no generic runtime exists.

---

## Phase 4: User Story 2 — Store Registry Identity Without Runtime Policy (Priority: P1)

**Goal**: Isolate identity persistence, locking, and migrations behind one repository contract.

**Independent Test**: Run the same contract suite against memory and JSON repositories and prove legacy/future/interrupted-write safety without booting WordPress.

### Tests for User Story 2

- [X] T028 [P] [US2] Add failing in-memory repository CRUD/identity/label/unknown-field tests in `tests/test_registry_repository.py`
- [X] T029 [P] [US2] Add failing JSON lock contention, sibling-temp atomic replacement, interrupted write, corruption, and unsupported-future-version tests in `tests/test_registry_repository.py`
- [X] T030 [P] [US2] Add failing legacy facade parity and direct-file-access guard tests in `tests/test_sandbox.py` and `tests/test_architecture_boundaries.py`

### Implementation for User Story 2

- [X] T031 [US2] Implement record/version models, identity keys, compatible field handling, and repository errors in `sandbox/project_registry/base.py`
- [X] T032 [US2] Implement the in-memory repository in `sandbox/project_registry/memory.py`
- [X] T033 [US2] Implement the file-backed repository with existing lock semantics and atomic same-filesystem replacement in `sandbox/project_registry/json.py`
- [X] T034 [US2] Route shipped registry functions through one compatibility facade in `sandbox_core.py` without changing registry location or broad eager-writing behavior
- [X] T035 [US2] Route existing instance/bridge consumers through the facade and replace MCP direct registry JSON reads with repository access in `mcp/wp-server/app.py`
- [X] T036 [US2] Run repository/config/bridge/MCP tests against copied live fixtures and append round-trip/interruption evidence to `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: Registry persistence is independently testable and contains no runtime-default policy.

---

## Phase 5: User Story 3 — Dispatch Runtime Operations by Capability (Priority: P1)

**Goal**: Route shared lifecycle requests through one adapter/capability service and preserve WordPress behavior.

**Independent Test**: Register a fake adapter, compare direct/CLI/MCP supported results, and prove unsupported requests record zero side effects.

### Tests for User Story 3

- [X] T037 [P] [US3] Add failing adapter registration, conflicting-kind, capability, request/result/error, and fake-dispatch tests in `tests/test_runtime_service.py`
- [X] T038 [P] [US3] Add failing zero-side-effect capability rejection tests for representative CLI and MCP WordPress-only operations in `tests/test_cli.py` and `tests/test_mcp.py`
- [X] T039 [P] [US3] Add failing WordPress adapter delegation and result-parity tests in `tests/test_runtime_service.py`

### Implementation for User Story 3

- [X] T040 [US3] Implement explicit adapter registry, deterministic built-in manifest, and duplicate/conflict rejection in `sandbox/runtimes/registry.py` and `sandbox/runtimes/__init__.py`
- [X] T041 [US3] Implement capability-aware runtime service and structured unsupported-kind/capability errors in `sandbox/application/runtime_service.py`
- [X] T042 [US3] Implement the WordPress compatibility adapter by delegating current lifecycle behavior in `sandbox/runtimes/wordpress.py`
- [X] T043 [US3] Add production dependency composition for descriptor, registry, and WordPress adapter services in `sandbox/application/context.py`
- [X] T044 [US3] Route migrated instance resolution/ensure/status/apply operations through the runtime service in `sandbox/commands/instances_cmd.py` and `sandbox/commands/config_setup.py`
- [X] T045 [US3] Route representative MCP instance operations through the same service in `mcp/wp-server/tools/instances.py`
- [X] T046 [US3] Add capability preflight to representative WordPress-only CLI and MCP paths before helper/subprocess invocation in `sandbox/commands/wp.py`, `sandbox/commands/data.py`, `mcp/wp-server/tools/wp.py`, and `mcp/wp-server/tools/data.py`
- [X] T047 [US3] Add reviewed kind/capability filtering to remote/deploy/hosting/preview entry points without changing supported WordPress behavior in `sandbox/commands/{remote,deploy,hosting,preview}.py`
- [X] T048 [US3] Run focused runtime/CLI/MCP/remote tests and the recorded live WordPress baseline; append zero-side-effect and parity evidence to `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: Transports share one capability boundary; WordPress remains the only production adapter.

---

## Phase 6: User Story 4 — Own CLI Commands Within Features (Priority: P2)

**Goal**: Build CLI parsers from feature-owned command specifications with a bounded legacy bridge.

**Independent Test**: Register a test command without central edits, compose twice deterministically, reject collisions, and replay all current commands.

### Tests for User Story 4

- [X] T049 [P] [US4] Add failing exact command/alias inventory, deterministic help grouping, and collision tests in `tests/test_command_composition.py`
- [X] T050 [P] [US4] Add failing test-only command, project/instance resolution, capability preflight, and destructive-confirmation tests in `tests/test_command_composition.py`
- [X] T051 [P] [US4] Add failing compatibility tests for current parser options, representative errors, JSON output, and exit codes in `tests/test_cli.py`

### Implementation for User Story 4

- [X] T052 [US4] Implement deterministic parser composition, shared scope resolution, capability preflight, and legacy bridge in `sandbox/registry.py` and `sandbox/cli.py`
- [X] T053 [US4] Add the explicit built-in command manifest in `sandbox/commands/manifest.py`
- [X] T054 [US4] Move instance/config parser ownership beside handlers in `sandbox/commands/instances_cmd.py` and `sandbox/commands/config_setup.py`
- [X] T055 [US4] Move shared lifecycle parser ownership beside handlers in `sandbox/commands/lifecycle.py`
- [X] T056 [US4] Represent every remaining command through a feature-owned spec or explicit bridge entry without changing its handler in `sandbox/commands/manifest.py`
- [X] T057 [US4] Run exact inventory, full CLI parser, representative live command, and no-central-growth checks; update `specs/022-sandbox-modular-boundaries/cli-inventory.md` and implementation evidence

**Checkpoint**: Spec 021 and recovery can add commands without editing central parser/routing lists.

---

## Phase 7: User Story 5 — Compose MCP Tool Groups Deterministically (Priority: P2)

**Goal**: Register tool groups through one explicit manifest with isolated dependencies and duplicate rejection.

**Independent Test**: Register a test group without server edits, compose twice with exact tool/schema parity, and fail closed on duplicate ownership.

### Tests for User Story 5

- [X] T058 [P] [US5] Add failing exact group/tool inventory, deterministic order, duplicate group/tool, and test-group tests in `tests/test_mcp_composition.py`
- [X] T059 [P] [US5] Add failing dependency declaration, isolated fake context, and no-import-side-effect tests in `tests/test_mcp_composition.py`
- [X] T060 [P] [US5] Add failing public name/required-parameter/response compatibility snapshots for all groups in `tests/test_mcp.py`

### Implementation for User Story 5

- [X] T061 [US5] Complete the MCP dependency container and deterministic group composer in `mcp/wp-server/dependencies.py` and `mcp/wp-server/composition.py`
- [X] T062 [US5] Add one explicit built-in tool-group manifest in `mcp/wp-server/tools/manifest.py`
- [X] T063 [US5] Replace the manual server import list with composer invocation while preserving transport behavior in `mcp/wp-server/server.py`
- [X] T064 [US5] Migrate instance/runtime and Hermes groups to explicit dependencies without wildcard `app` imports in `mcp/wp-server/tools/instances.py`, `mcp/wp-server/tools/hermes.py`, and focused shared helpers
- [X] T065 [US5] Represent every remaining tool group exactly once through the manifest/compatibility wrapper, run MCP schema/registration tests, and update `specs/022-sandbox-modular-boundaries/mcp-inventory.md` and implementation evidence

**Checkpoint**: Future runtime/recovery tool groups register without central bootstrap edits or broad globals.

---

## Phase 8: User Story 6 — Test Side Effects Through Explicit Services (Priority: P2)

**Goal**: Provide bounded runtime-neutral process, HTTP, port, path, and proxy services with deterministic failure tests.

**Independent Test**: Inject every documented failure and verify redaction, timeout, rollback, path safety, and no expanded side effects.

### Tests for User Story 6

- [X] T066 [P] [US6] Add failing argument-list, cwd/environment, timeout, output-limit, result, and secret-redaction tests in `tests/test_service_process.py`
- [X] T067 [P] [US6] Add failing HTTP timeout/status and port collision/reservation tests in `tests/test_service_http_ports.py`
- [X] T068 [P] [US6] Add failing allowed-root/artifact-path and proxy plan/apply/remove/rollback tests in `tests/test_service_paths_proxy.py`

### Implementation for User Story 6

- [X] T069 [US6] Implement the production bounded process runner in `sandbox/services/process.py`
- [X] T070 [P] [US6] Implement the HTTP probe and port allocator services in `sandbox/services/http.py` and `sandbox/services/ports.py`
- [X] T071 [P] [US6] Implement the allowed-root/artifact path policy in `sandbox/services/paths.py`
- [X] T072 [US6] Implement the proxy route plan/apply/remove/rollback adapter over existing Caddy/domain behavior in `sandbox/services/proxy.py`
- [X] T073 [US6] Inject services into the runtime composition root and migrated WordPress paths without moving WordPress-specific policy in `sandbox/application/context.py` and focused `sandbox/core/` callers
- [ ] T074 [US6] Run service failure tests plus live domain/HTTPS/lifecycle parity and append redaction/rollback evidence to `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: Generic and recovery features can orchestrate safe mechanisms without importing WordPress policy.

---

## Phase 9: User Story 7 — Change One Hermes Concern Independently (Priority: P2)

**Goal**: Split Hermes state, routing, jobs, gateway/public access, and backup planning behind one compatibility service.

**Independent Test**: Each bounded module runs isolated tests without initializing unrelated providers, while remote public behavior remains compatible.

### Tests for User Story 7

- [X] T075 [P] [US7] Add failing state schema/lock/atomic-write/corruption/compatibility tests in `tests/test_hermes_state.py`
- [X] T076 [P] [US7] Add failing side-effect-free target/policy routing tests in `tests/test_hermes_routing.py`
- [X] T077 [P] [US7] Add failing job/worktree/status/cancel/race/cleanup tests with fakes in `tests/test_hermes_jobs.py`
- [X] T078 [P] [US7] Add failing gateway plan/apply/remove/auth-order/rollback tests with fakes in `tests/test_hermes_gateway.py`
- [X] T079 [P] [US7] Add failing backup artifact/integrity/list/retention-hook/non-mutating-restore-plan tests in `tests/test_hermes_backup.py`
- [X] T080 [P] [US7] Add failing facade/public-function and no-cross-internal-import tests in `tests/test_hermes_service.py` and `tests/test_architecture_boundaries.py`

### Implementation for User Story 7

- [X] T081 [US7] Extract Hermes state models, validation, atomic persistence, and corruption reporting into `sandbox/hermes/state.py`
- [X] T082 [US7] Extract side-effect-free target resolution and routing policy into `sandbox/hermes/routing.py`
- [X] T083 [US7] Extract run/worktree process coordination, status, cancellation, and cleanup into `sandbox/hermes/jobs.py`
- [ ] T084 [US7] Run focused state/routing/jobs tests and remote status/job lifecycle parity; record pre-gateway evidence in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T085 [US7] Extract gateway/public endpoint/tunnel/route/auth-related plan and reversible operations into `sandbox/hermes/gateway.py`
- [ ] T086 [US7] Run gateway tests and verify `hermes.asb.bd` authentication, route, WebSocket reconnect, and no-exposure-drift behavior through Sandbox commands; record evidence
- [X] T087 [US7] Extract existing artifact create/list/integrity behavior and non-mutating restore planning into `sandbox/hermes/backup.py`
- [X] T088 [US7] Implement Hermes dependency composition and service orchestration in `sandbox/hermes/service.py`
- [X] T089 [US7] Preserve existing public functions and error/authorization ordering through `sandbox/hermes/facade.py` and `sandbox/core/_hermes.py`
- [X] T090 [US7] Route CLI Hermes handlers through the facade in `sandbox/commands/hermes.py`
- [X] T091 [US7] Route MCP Hermes tools through explicit service dependencies in `mcp/wp-server/tools/hermes.py`
- [X] T092 [US7] Add a facade ledger with owner, consumers, rollback, tests, and removal gates in `specs/022-sandbox-modular-boundaries/compatibility-facades.md`
- [ ] T093 [US7] Run the complete focused Hermes suite and existing local/remote acceptance checks without restore application or deletion; append exact evidence to `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [ ] T094 [US7] Verify scoped recovery can be specified solely against `sandbox/hermes/backup.py` and shared service contracts, documenting any remaining blocker in implementation evidence

**Checkpoint**: Recovery policy can be added without modifying unrelated Hermes state/routing/jobs/gateway internals.

---

## Phase 10: User Story 8 — Preserve Existing Users During Migration (Priority: P1)

**Goal**: Prove configuration, registry, CLI, MCP, WordPress, remote, and Hermes compatibility across all phases.

**Independent Test**: Replay every captured automated and live baseline and explain every permitted nondeterministic difference.

### Tests and Integration for User Story 8

- [X] T095 [P] [US8] Add final exact CLI/MCP inventory and compatibility assertions in `tests/test_cli.py`, `tests/test_mcp.py`, and composition suites
- [X] T096 [P] [US8] Add final config/registry/facade compatibility matrix tests in `tests/test_project_config.py`, `tests/test_sandbox.py`, and new facade suites
- [X] T097 [P] [US8] Add final WordPress runtime, remote, and Hermes facade regression tests in `tests/test_runtime_service.py`, `tests/test_remote.py`, and `tests/test_hermes_service.py`
- [ ] T098 [US8] Run every WordPress scenario in `quickstart.md` on the live stack through Sandbox tools and record exact results
- [ ] T099 [US8] Run every remote/Hermes scenario in `quickstart.md` through Sandbox commands/MCP and record exact results without destructive restore or deletion
- [X] T100 [US8] Run all registry/state failure-injection scenarios against copied/disposable state and record recovery evidence in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T101 [US8] Review and resolve every unexplained baseline drift or document return to the prior facade path in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T102 [US8] Confirm all compatibility facades have no new consumers and all deferred removals remain blocked in `specs/022-sandbox-modular-boundaries/compatibility-facades.md`
- [X] T103 [US8] Obtain fresh correctness/regression and security/data-loss review, recording findings and resolutions in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`

**Checkpoint**: Existing users require no config edits and observe no unexplained behavior, protocol, authorization, state, or public-access drift.

---

## Phase 11: Documentation, Enforcement, and Downstream Handoff

- [X] T104 [P] Update architecture, config, command, MCP, and Hermes guidance in `README.md`, `docs/sandbox-config-reference.md`, `AGENTS.md`, and `CLAUDE.md`
- [X] T105 [P] Add durable module-boundary and compatibility-facade guidance to the relevant Sandbox skills/workflows under `.agents/skills/` and `workflows/`
- [X] T106 [P] Update Spec 021 status/dependency notes to remain implementation-blocked and identify moved responsibilities in `specs/021-generic-project-instances/plan.md` and `tasks.md`
- [X] T107 Enable the reviewed architecture guards and exact inventory checks in `tests/test_architecture_boundaries.py`
- [ ] T108 Run focused suites, `python3 -m unittest discover -s tests -v`, `./sb selftest`, `git diff --check`, and every scenario in `quickstart.md`; append commands/results/retries to implementation evidence
- [X] T109 Confirm scope stayed bounded, user changes were preserved, no secret was recorded, no unapproved git/release/production action occurred, and every deferred item matches the spec in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`
- [X] T110 Re-run Spec-Kit analysis/convergence against the implemented feature and append any remaining work to `tasks.md` before claiming completion
- [X] T111 Prepare the downstream handoff in `specs/022-sandbox-modular-boundaries/implementation-evidence.md`: replan `specs/021-generic-project-instances/` and start the scoped-recovery Spec-Kit feature only after explicit human approval to unblock them

---

## Dependencies & Execution Order

### Phase dependencies

- Phase 1 has no dependency and establishes immutable evidence.
- Phase 2 depends on Phase 1 and blocks every migration.
- US1 and US2 depend on Phase 2; execute US1 then US2 because the current facade combines config and registry.
- US3 depends on US1 and US2.
- US4 and US5 depend on US3; they may be researched in parallel but have separate writers and file sets.
- US6 depends on the runtime contracts from US3 and must complete before Hermes extraction uses shared services.
- US7 depends on US5 and US6.
- US8 depends on all migrated stories.
- Phase 11 depends on all delivered stories and final review.

### User story graph

```text
Baseline -> Contracts -> US1 descriptors -> US2 registry -> US3 runtime dispatch
                                                     ├-> US4 CLI
                                                     ├-> US5 MCP
                                                     └-> US6 services
                                                  US5 + US6 -> US7 Hermes
                                            all migrated stories -> US8 parity -> handoff
```

### Parallel opportunities

- Baseline documentation/fixture tasks marked `[P]` own separate files.
- Contract test modules marked `[P]` may be written independently before shared contract implementation.
- CLI and MCP composition phases may use separate worktrees only after runtime contracts stabilize and never share files.
- Shared HTTP/port/path tests and implementations own separate files; process and proxy integration remain sequential.
- Hermes concern tests own separate files, but production extractions are sequential in state→routing→jobs→gateway→backup order.
- One owner controls each compatibility facade and all stateful remote verification.

## Implementation Strategy

### First safe increment

1. Complete baseline and foundational contracts.
2. Complete descriptor and registry stories.
3. Stop and replay WordPress config/registry behavior.
4. Do not start runtime or Hermes migration until this checkpoint passes review.

### Incremental continuation

1. Add runtime dispatch and prove zero-side-effect rejection.
2. Move CLI and MCP composition separately with exact inventory parity.
3. Add shared services and replay live WordPress behavior.
4. Extract one Hermes concern at a time, with remote parity after each group.
5. Enable boundary enforcement only after migrations and exceptions are understood.

## Task Format Validation

All implementation lines use `- [ ] TNNN`; story tasks include `[USN]`; parallel markers are limited to non-overlapping files; every task names a concrete file or evidence artifact. No task authorizes commit, push, release, deployment, backup deletion, or applied restore.
