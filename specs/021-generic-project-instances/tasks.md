# Tasks: Generic Project Instances

> **Status (2026-07-14): implementation-blocked pending replan and human
> approval.** Feature 022 moved config/schema, registry persistence, runtime
> dispatch, bounded services, CLI composition, and MCP composition responsibilities.
> Run Spec-Kit clarify/plan/tasks again against those boundaries before executing
> T001 or marking any task complete. Compose/Astro behavior has not been delivered.

**Input**: Design documents from `specs/021-generic-project-instances/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [modularity-audit.md](modularity-audit.md)

**Tests**: Required by FR-019. Write each behavior/contract test before its implementation and confirm it fails for the intended reason.

**Organization**: Tasks are grouped by independently testable user story. This is a side project: execute sequentially by phase with one worker by default and stop at any checkpoint without pulling later phases forward.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Safe to perform concurrently because it owns different files and does not depend on unfinished work
- **[Story]**: Maps the task to a user story in `spec.md`
- Every task names its intended file set and evidence

## Phase 1: Setup and Baseline Evidence

**Purpose**: Establish fixtures, trace, and compatibility evidence before shared lifecycle changes.

- [x] T001 Record the pre-change WordPress ensure/status/WP-CLI/REST/test outputs and current command/tool counts in `specs/021-generic-project-instances/implementation-evidence.md`
- [x] T002 [P] Create a minimal project-owned Compose web fixture with a named-volume marker in `tests/fixtures/generic-compose/compose.yaml`, `tests/fixtures/generic-compose/site/`, and `tests/fixtures/generic-compose/sandbox.config.json`
- [x] T003 [P] Create a representative Astro fixture with conventional and overridden package scripts in `tests/fixtures/astro/package.json`, `tests/fixtures/astro/astro.config.mjs`, and `tests/fixtures/astro/src/`
- [x] T004 [P] Add audit guard scripts or test helpers that count CLI commands, MCP tools, wildcard imports, and runtime-kind branches in `tests/test_modularity.py`

**Checkpoint**: Baseline and fixtures are reviewable; no product behavior has changed.

---

## Phase 2: Foundational Runtime Contract

**Purpose**: Introduce the additive descriptor, adapter, and registry contracts that block every user story.

**CRITICAL**: Stop if any legacy WordPress config or registry assertion changes unexpectedly.

- [x] T005 [P] Add failing project-kind/default-isolation/path-validation tests for legacy WordPress, explicit Compose, dot names, and label overrides in `tests/test_project_config.py`
- [x] T006 [P] Add failing adapter registration, capability, unsupported-kind, and result-shape tests in `tests/test_runtime_adapters.py`
- [x] T007 [P] Add failing additive registry compatibility tests for `kind`, `adapter`, `display_name`, `http_port`, and legacy `wordpress_port` fallback in `tests/test_sandbox.py`
- [x] T008 Define explicit runtime protocol, capability constants, structured results/errors, and injected dependencies in `sandbox/runtimes/base.py`
- [x] T009 Implement explicit built-in adapter registration and kind selection without wildcard imports in `sandbox/runtimes/__init__.py`
- [ ] T010 Implement the WordPress compatibility adapter by delegating to current lifecycle behavior in `sandbox/runtimes/wordpress.py`
- [ ] T011 Split common versus WordPress defaults, select kind before normalization, and scope plugin-slug validation to WordPress in `sandbox_core.py`
- [ ] T012 Extend registry reads/writes additively for common instance metadata while preserving the v2 key and all legacy fields in `sandbox_core.py`
- [ ] T013 Add common URL/port helpers that prefer `http_port` and fall back to `wordpress_port` without changing current WP output in `sandbox/core/_instances.py`
- [ ] T014 Run `tests/test_project_config.py`, `tests/test_runtime_adapters.py`, `tests/test_sandbox.py`, and the recorded WordPress baseline; append exact commands/results to `specs/021-generic-project-instances/implementation-evidence.md`

**Checkpoint**: Adapter foundation exists, WordPress behavior is unchanged, and no generic project starts yet.

---

## Phase 3: User Story 1 — Boot an Explicit Generic Project (Priority: P1) MVP

**Goal**: `sb ensure` and `ensure_instance` boot an explicitly configured Compose web project, including a directory name with a dot, with stable identity and URL.

**Independent Test**: Run ensure twice against `tests/fixtures/generic-compose/`; confirm one healthy Compose instance, stable registry identity/URL, and no WordPress, DB, or Mailpit service.

### Tests for User Story 1

- [ ] T015 [P] [US1] Add failing runtime-safe ID, normalization-collision, and canonical-root identity tests in `tests/test_runtime_adapters.py`
- [ ] T016 [P] [US1] Add failing Compose validation/overlay/argument-list portability/health-timeout/idempotency tests with subprocess fakes in `tests/test_generic_compose.py`
- [ ] T017 [P] [US1] Add failing CLI and MCP ensure contract tests for generic records and unconfigured-repository refusal in `tests/test_cli.py` and `tests/test_mcp.py`

### Implementation for User Story 1

- [ ] T018 [US1] Implement deterministic collision-safe runtime IDs and Sandbox-owned artifact paths in `sandbox/runtimes/base.py`
- [ ] T019 [US1] Implement Compose descriptor validation via resolved project paths and `docker compose config` in `sandbox/runtimes/compose.py`
- [ ] T020 [US1] Render a minimal generated port/label overlay under `$SANDBOX_HOME/runtime/projects/<instance>/` from `sandbox/runtimes/compose.py`
- [ ] T021 [US1] Implement pending/starting/ready/error ensure flow, bounded health probing, and idempotent reuse in `sandbox/runtimes/compose.py`
- [ ] T022 [US1] Introduce backward-compatible feature-owned command/parser registration for instance commands and dispatch project-scoped init/ensure through the selected adapter in `sandbox/registry.py`, `sandbox/cli.py`, and `sandbox/commands/instances_cmd.py`
- [ ] T023 [US1] Make MCP `ensure_instance` kind-neutral and return additive kind/adapter/capability fields in `mcp/wp-server/tools/instances.py`
- [ ] T024 [US1] Live-validate repeated ensure for the dot-name Compose fixture, verify warm ensure completes within 5 seconds, inspect containers/registry/artifacts/URL, and record evidence in `specs/021-generic-project-instances/implementation-evidence.md`

**Checkpoint**: P1 MVP is usable for explicit local Compose projects. Stop here if side-project time is exhausted.

---

## Phase 4: User Story 2 — Operate Generic Instances Safely (Priority: P2)

**Goal**: Shared lifecycle, diagnostics, proxy, MCP, capability errors, and non-destructive cleanup work for generic projects.

**Independent Test**: Exercise status/logs/exec/stop/start/apply/open/secure/destroy through CLI and MCP, then prove WordPress-only tools fail before side effects and project volumes survive destroy.

### Tests for User Story 2

- [ ] T025 [P] [US2] Add failing Compose start/stop/status/logs/exec/apply/destroy and volume-preservation tests in `tests/test_generic_compose.py`
- [ ] T026 [P] [US2] Add failing shared CLI routing and WordPress-command capability rejection tests in `tests/test_cli.py`
- [ ] T027 [P] [US2] Add failing `instance_status`, `instance_logs`, `instance_exec`, lifecycle, and representative WP-tool preflight tests in `tests/test_mcp.py`
- [ ] T028 [P] [US2] Add failing generic clean-URL/HTTPS route tests without WordPress URL rewriting in `tests/test_compose.py` and `tests/test_sandbox.py`

### Implementation for User Story 2

- [ ] T029 [US2] Implement bounded status/start/stop/logs/exec/apply/destroy operations without Compose volume removal in `sandbox/runtimes/compose.py`
- [ ] T030 [US2] Extend command specs with capability metadata, add capability-aware lifecycle dispatch for up/down/status/logs/shell/open/apply, and preflight every WordPress-only CLI command before handler execution in `sandbox/registry.py`, `sandbox/cli.py`, `sandbox/commands/lifecycle.py`, and `sandbox/commands/config_setup.py`
- [ ] T031 [US2] Make instance delete/recreate/secure and shared URL resolution dispatch by adapter with safe generic semantics in `sandbox/commands/instances_cmd.py`, `sandbox/commands/net.py`, and `sandbox/core/_domains.py`
- [ ] T032 [US2] Add kind-neutral bounded `instance_status`, `instance_logs`, and `instance_exec` tools in `mcp/wp-server/tools/runtime.py`
- [ ] T033 [US2] Make MCP lifecycle wrappers adapter-aware and preserve their existing WordPress contracts in `mcp/wp-server/tools/instances.py`
- [ ] T034 [US2] Add one reusable capability preflight and apply it to all WordPress-only MCP groups in `mcp/wp-server/app.py`, `mcp/wp-server/tools/wp.py`, `data.py`, `fs.py`, `mail.py`, `context.py`, `abilities.py`, `debug.py`, `plugin_check.py`, `e2e.py`, `ci.py`, and `remote.py`
- [ ] T035 [US2] Route generic instances through existing Caddy domain/certificate plumbing without WP-CLI or WP REST mutation in `sandbox/core/_domains.py`
- [ ] T036 [US2] Live-validate three lifecycle cycles, MCP operations, capability rejection, HTTPS, and named-volume survival; record evidence in `specs/021-generic-project-instances/implementation-evidence.md`
- [ ] T037 [US2] Re-run the WordPress baseline and focused MCP/compose/domain tests; stop on any externally visible drift and record results in `specs/021-generic-project-instances/implementation-evidence.md`

**Checkpoint**: Generic instances are operationally useful and safely separated from WordPress-only features.

---

## Phase 5: User Story 3 — Initialize an Astro Project (Priority: P2)

**Goal**: Guided initialization writes explicit, reviewable Compose configuration for Astro without a framework-specific runtime.

**Independent Test**: Initialize a disposable copy of `tests/fixtures/astro/`, review generated files, ensure it, and confirm reachable/live-reload behavior.

### Tests for User Story 3

- [ ] T038 [P] [US3] Add failing package-manager/script/port/bind/health inference and ambiguity tests in `tests/test_astro_preset.py`
- [ ] T039 [P] [US3] Add failing non-interactive and guided `sb init --type astro|compose` output tests in `tests/test_cli.py`

### Implementation for User Story 3

- [ ] T040 [US3] Implement read-only Astro metadata inspection and explicit value proposals in `sandbox/runtimes/presets/astro.py`
- [ ] T041 [US3] Implement preset registration and reusable project-owned Compose/config rendering in `sandbox/runtimes/presets/__init__.py` and `sandbox/runtimes/presets/astro.py`
- [ ] T042 [US3] Add `--type compose` and `--type astro` to the feature-owned instance command spec with review/confirmation and non-interactive validation in `sandbox/commands/instances_cmd.py`
- [ ] T043 [US3] Live-validate the disposable Astro fixture from initialization through source update and record exact evidence in `specs/021-generic-project-instances/implementation-evidence.md`

**Checkpoint**: The reported Astro case is handled in two explicit commands; the generic adapter remains framework-neutral.

---

## Phase 6: User Story 4 — Extend Runtimes Without Growing Central Monoliths (Priority: P3)

**Goal**: The touched extension path owns parser/tool registration and uses explicit dependencies, while unrelated modules remain untouched.

**Independent Test**: Register a test-only adapter and command spec without editing central parser/bootstrap lists; verify all current command/tool counts and behavior remain stable.

### Tests for User Story 4

- [ ] T044 [P] [US4] Add failing feature-owned parser registration, duplicate-name, legacy-handler compatibility, and test-adapter discovery tests in `tests/test_cli.py` and `tests/test_runtime_adapters.py`
- [ ] T045 [P] [US4] Add failing deterministic built-in MCP tool-group loading and duplicate-registration tests in `tests/test_mcp.py` and `tests/test_server_transport.py`

### Implementation for User Story 4

- [ ] T046 [US4] Complete duplicate-name rejection, test-adapter discovery, and legacy mapping compatibility for the existing `CommandSpec` and runtime registries in `sandbox/registry.py` and `sandbox/runtimes/__init__.py`
- [ ] T047 [US4] Move the remaining touched lifecycle parser definitions beside handlers in `sandbox/commands/lifecycle.py` and remove their duplicate legacy blocks from `sandbox/cli.py`
- [ ] T048 [US4] Replace the manual MCP group import list with a deterministic package-owned built-in loader in `mcp/wp-server/tools/__init__.py` and `mcp/wp-server/server.py`
- [ ] T049 [US4] Replace wildcard imports only in files modified by this feature where focused tests cover the explicit dependency list in `sandbox/commands/{instances_cmd,lifecycle,config_setup}.py` and `mcp/wp-server/tools/{instances,runtime}.py`
- [ ] T050 [US4] Run modularity guards, update counts/classifications and justified exceptions in `specs/021-generic-project-instances/modularity-audit.md`, and record results in `specs/021-generic-project-instances/implementation-evidence.md`

**Checkpoint**: The adapter path is extensible without claiming that the entire repository has been refactored.

---

## Phase 7: Polish, Documentation, and Final Gates

**Purpose**: Align user guidance, run full verification, and obtain review without shipping.

- [ ] T051 [P] Document generic/Compose/Astro configuration, lifecycle, capability boundaries, and safety model in `README.md` and `docs/sandbox-config-reference.md`
- [ ] T052 [P] Update CLI/MCP agent guidance and tool tables for kind-neutral versus WordPress-only operations in `AGENTS.md`, `CLAUDE.md`, and `mcp/wp-server/app.py`
- [ ] T053 [P] Add non-obvious generic runtime/proxy/Compose findings discovered during live work to `memory/plugin-behavior/generic-project-instances.md`
- [ ] T054 Run all focused tests, `python3 -m unittest discover -s tests -v`, `./sb selftest`, and `git diff --check`; append exact results and any retry count to `specs/021-generic-project-instances/implementation-evidence.md`
- [ ] T055 Execute every scenario in `specs/021-generic-project-instances/quickstart.md` against fresh generic/Astro fixtures and the existing WordPress stack; attach or reference inspectable runtime artifacts in `specs/021-generic-project-instances/implementation-evidence.md`
- [ ] T056 Perform fresh correctness/regression and security/data-loss review of the registry, process execution, path validation, proxy, and destroy diff; record findings and resolutions in `specs/021-generic-project-instances/implementation-evidence.md`
- [ ] T057 Confirm scope stayed bounded, user changes are preserved, no secret was recorded, no unapproved commit/push/release occurred, and residual deferred features match `spec.md` in `specs/021-generic-project-instances/implementation-evidence.md`

---

## Dependencies & Execution Order

### Phase dependencies

- Phase 1 has no dependency.
- Phase 2 depends on Phase 1 and blocks every user story.
- US1 (Phase 3) depends on Phase 2 and is the MVP.
- US2 (Phase 4) depends on US1 because it extends the Compose adapter lifecycle.
- US3 (Phase 5) depends on US1 but not US2; it may proceed after the MVP if capacity favors the immediate Astro case.
- US4 (Phase 6) depends on the touched command/MCP surface from US1/US2 and is intentionally last.
- Phase 7 depends only on the story phases selected for delivery; documentation must accurately describe the delivered subset.

### User story dependency graph

```text
Foundation -> US1 MVP -> US2 operations -> US4 extension seam
                    \-> US3 Astro preset
```

### Parallel opportunities

- Fixture tasks T002-T004 own separate paths.
- Foundational test tasks T005-T007 own separate test modules.
- Each story's test tasks marked `[P]` may be written concurrently only if separate workers have non-overlapping file ownership.
- Documentation tasks T051-T053 own separate primary files but must be reconciled against the same delivered behavior.
- Implementation tasks that touch registry, adapter dispatch, CLI, or MCP remain sequential under one owner.

## Implementation Strategy

### MVP first

1. Complete Phases 1 and 2.
2. Complete US1 only.
3. Stop and live-validate explicit Compose plus WordPress parity.
4. Keep the remaining phases as backlog if there is no side-project capacity.

### Incremental continuation

1. Add US2 when generic instances need agent-safe operations.
2. Add US3 when the actual Astro repository is ready for validation.
3. Add US4 only after the extension seam has at least two real adapters to justify it.
4. Never use US4 as authority for unrelated cleanup.

## Task Format Validation

All implementation lines use `- [ ] TNNN`, story-phase tasks include `[USN]`, parallel markers are limited to non-overlapping paths, and each task names a concrete file or evidence artifact.
