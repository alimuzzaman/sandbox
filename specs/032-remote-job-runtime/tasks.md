# Tasks: Remote Job Runtime

**Input**: Design documents from `/specs/032-remote-job-runtime/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`,
`quickstart.md`

**Tests**: Required. Write each story's tests first, observe the intended failure, then
implement the corresponding production task. Runtime-touching validation uses `./sb`.

**Organization**: Tasks are grouped by user story so each story can be implemented and
validated as an increment. Shared job/storage/config/transport contracts are foundational.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: May run in parallel because it owns different files and has no incomplete
  dependency in the same phase.
- **[US1]..[US5]**: Maps directly to the user stories in `spec.md`.
- Every task names an exact repository file or directory.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish explicit package and composition boundaries without behavior.

- [X] T001 Create the runtime-neutral job package exports in `sandbox/jobs/__init__.py` and empty explicit registration surface in `sandbox/jobs/manifest.py`
- [X] T002 [P] Create application service module boundaries in `sandbox/application/job_service.py`, `sandbox/application/target_service.py`, and `sandbox/application/workspace_service.py`
- [X] T003 [P] Create local and remote transport protocol module boundaries in `sandbox/transports/__init__.py`, `sandbox/transports/jobs.py`, and `sandbox/transports/remote_jobs.py`
- [X] T004 [P] Create the CI compatibility package boundary in `sandbox/ci/__init__.py`, `sandbox/ci/compatibility.py`, and `sandbox/ci/workflow.py`
- [X] T005 Register feature-owned CLI module placeholders through `sandbox/commands/manifest.py` for `sandbox/commands/jobs_runtime.py` and `sandbox/commands/workspaces.py`
- [X] T006 Register a feature-owned MCP jobs group placeholder and dependency keys in `mcp/wp-server/tools/manifest.py` and `mcp/wp-server/tools/jobs.py`
- [X] T007 Add architecture-boundary assertions for every new package and manifest owner in `tests/test_architecture_boundaries.py`, `tests/test_command_composition.py`, and `tests/test_mcp_composition.py`
- [X] T008 Run the new composition tests with `.cli-venv/bin/python -m unittest tests.test_architecture_boundaries tests.test_command_composition tests.test_mcp_composition -v` and record the command in `specs/032-remote-job-runtime/implementation-evidence.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement contracts shared by all target, execution, health, workspace, and
CI stories.

**Critical**: No user-story implementation starts until this phase passes.

### Foundational tests

- [X] T009 [P] Add exhaustive enum, ID, argv, deadline, profile, relationship, and transition tests in `tests/test_job_models.py` covering FR-002, FR-004, FR-006, FR-007, FR-015, FR-016, FR-020, FR-029, and 16-hex compatibility
- [X] T010 [P] Add repository migration, atomic acceptance/idempotency, transition, concurrency, foreign-key, and recovery tests in `tests/test_job_registry.py` covering FR-006, FR-007, FR-009, FR-014, FR-022, and FR-032
- [X] T011 [P] Add project runtime schema and precedence tests in `tests/test_runtime_config.py` covering defaults, custom execution/output profiles, invalid/unbounded values, multiple workspaces, and FR-001 through FR-004
- [X] T012 [P] Add local/configured/remote target precedence, namespace separation, capability-first failure, invalid remote, and label tests in `tests/test_target_resolution.py` covering FR-001 and FR-023
- [X] T013 [P] Add process identity tests in `tests/test_job_process_identity.py` for boot changes, PID reuse, PGID ownership, missing `/proc`, and FR-021/FR-022
- [X] T014 [P] Add source/deadline/output/workspace/artifact serialization redaction tests in `tests/test_job_contracts.py` for every value object in `specs/032-remote-job-runtime/contracts/job-service.md`

### Foundational implementation

- [X] T015 Implement immutable job, target, execution-policy, source, process, output, metrics, artifact, lease, and result value objects in `sandbox/jobs/models.py`
- [X] T016 Implement lifecycle transition validation, terminal invariants, ID generation/legacy validation, canonical submission digests, and finite deadline resolution in `sandbox/jobs/models.py`
- [X] T017 Implement the versioned SQLite schema, WAL/full-durability setup, migrations, transactions, and repository errors in `sandbox/jobs/registry.py`
- [X] T018 Implement atomic submit/idempotency, job relationship, lifecycle, heartbeat, process identity, output index, event, metric index, artifact index, and compatibility-difference repository methods in `sandbox/jobs/registry.py`
- [X] T019 [P] Implement common runtime configuration normalization and built-in/default profile definitions in `sandbox/config/runtime.py` matching `specs/032-remote-job-runtime/contracts/config.schema.json`
- [X] T020 Register common runtime configuration providers explicitly and compose them after kind-specific descriptors in `sandbox/config/manifest.py` and `sandbox/config/facade.py`
- [X] T021 Preserve WordPress and Compose descriptor compatibility while exposing normalized runtime policy in `sandbox/config/wordpress.py` and `sandbox/config/compose.py`
- [X] T022 Implement pure target/workspace/deadline/output precedence and validation in `sandbox/application/target_service.py`
- [X] T023 [P] Implement portable boot/process start identity collection and verified process-group ownership in `sandbox/jobs/process.py`
- [X] T024 [P] Implement owner-only job-directory creation, atomic JSON mirrors, safe relative paths, disk reserve checks, and fsync helpers in `sandbox/jobs/storage.py`
- [X] T025 Define local job transport protocols and service result translation in `sandbox/transports/jobs.py`
- [X] T026 Register repository, storage, process identity, clock, and profile providers in `sandbox/jobs/manifest.py` without importing compatibility facades
- [X] T027 Compose shared job dependencies at CLI and MCP composition roots in `sandbox/application/context.py` and `mcp/wp-server/tools/manifest.py`
- [X] T028 Run all foundational tests and the existing config/runtime/architecture suites with `.cli-venv/bin/python -m unittest tests.test_job_models tests.test_job_registry tests.test_runtime_config tests.test_target_resolution tests.test_job_process_identity tests.test_job_contracts tests.test_config_descriptors tests.test_project_config tests.test_runtime_contracts tests.test_runtime_adapters tests.test_architecture_boundaries -v`
- [X] T029 Inspect the SQLite schema and atomic idempotency behavior through the public repository test fixture and append evidence to `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T030 Commit and push the passing foundational increment with `sandbox/jobs/`, config manifest/runtime policy, shared application composition, and its tests

**Checkpoint**: Durable contracts, target resolution, profiles, process identity, and
repository transactions are independently tested and available to all stories.

---

## Phase 3: User Story 1 - Run a Remote Test Safely (Priority: P1) - MVP

**Goal**: Deploy exact source, accept a durable detached test job, persist separate and
combined output locally on the execution host, and resume compact/full output after
disconnect without affecting the child process.

**Independent Test**: Submit a long-running explicit command, receive a job ID,
disconnect the submitting process, reconnect by cursor, and verify terminal outcome plus
complete retained stdout/stderr/combined output.

### Tests for User Story 1

- [ ] T031 [P] [US1] Add streaming redaction, partial-line, invalid-UTF8, control-code, chunk-boundary secret, segmentation, combined-order, and integrity tests in `tests/test_job_output.py` for FR-009 through FR-014
- [ ] T032 [P] [US1] Add full/smart/errors/sampled/quiet/custom profile tests including every-10/20-lines, time sampling, context, deduplication, heartbeat, and budgets in `tests/test_output_profiles.py` for FR-012/FR-013
- [ ] T033 [P] [US1] Add opaque cursor, no-duplicate resume, stream/offset/tail/line/time reads, compression, base64, bounded long-poll, and expired-range tests in `tests/test_job_output_cursor.py` for FR-011 and SC-003
- [ ] T034 [P] [US1] Add detached supervisor tests in `tests/test_job_supervisor.py` for descriptor detachment, local pipe drainage, caller exit, deadline, exit code, child descendants, output/storage failure, and FR-006/FR-008/FR-014/FR-020
- [ ] T035 [P] [US1] Add service submission tests in `tests/test_job_service.py` proving durable acceptance precedes launch response and launch failure never reports running/success
- [ ] T036 [P] [US1] Add mocked remote transport tests in `tests/test_remote_job_transport.py` proving capability validation and exact-tree deployment precede remote acceptance, retry reuses request ID, and SSH carries bounded JSON rather than child pipes for FR-005/FR-007/FR-008
- [ ] T037 [P] [US1] Add CLI contract tests in `tests/test_job_cli.py` for explicit argv, target/workspace/timeout/output options, detach/wait, stderr reminders, exit codes, and malformed commands
- [ ] T038 [P] [US1] Add MCP contract tests in `tests/test_job_mcp.py` for remote-aware `run_tests`, `instance_exec`, job start/status/output, preserved result keys, bounded responses, and optional progress for FR-038/FR-039/FR-040

### Implementation for User Story 1

- [ ] T039 [US1] Implement streaming secret redaction with cross-chunk overlap and explicit redaction failure in `sandbox/jobs/output.py`
- [ ] T040 [US1] Implement separate segmented stdout/stderr byte stores and append-only combined event ordering in `sandbox/jobs/output.py`
- [ ] T041 [US1] Implement opaque cursor encoding/validation and bounded stream/offset/tail/line/time/base64 retrieval in `sandbox/jobs/output.py`
- [ ] T042 [US1] Implement full/smart/errors/sampled/quiet and declarative named custom presentation policies in `sandbox/jobs/output.py`
- [ ] T043 [US1] Implement the detached supervisor entrypoint, lease wait heartbeat, child session/process group launch, non-blocking local pipe drainage, and atomic finalization in `sandbox/jobs/supervisor.py`
- [ ] T044 [US1] Implement deadline enforcement, TERM/grace/KILL cleanup of owned descendants, output/storage failure promotion, and terminal integrity hashes in `sandbox/jobs/supervisor.py`
- [ ] T045 [US1] Implement submit/get/list/read-output use cases and idempotent launch recovery in `sandbox/application/job_service.py`
- [ ] T046 [US1] Implement the host-local service transport and detached supervisor launcher with every standard descriptor disconnected in `sandbox/transports/jobs.py`
- [ ] T047 [US1] Extend exact-working-tree deployment to return commit/dirty-manifest/deploy identities and workspace target paths in `sandbox/commands/deploy.py` and `sandbox/core/_remote.py`
- [ ] T048 [US1] Implement bounded remote `sb` JSON invocation, timeout/error redaction, and reconnectable job reads in `sandbox/transports/remote_jobs.py`
- [ ] T049 [US1] Add remote-aware explicit-argv `exec` and `test` submission/follow behavior in `sandbox/commands/runtime.py` while preserving local Compose execution
- [ ] T050 [US1] Add feature-owned job status/list/output/follow command parsers and renderers in `sandbox/commands/jobs_runtime.py` and register them in `sandbox/commands/manifest.py`
- [ ] T051 [US1] Add MCP `job_start`, `job_status`, `job_list`, `job_output`, and bounded `job_follow` tools in `mcp/wp-server/tools/jobs.py`
- [ ] T052 [US1] Extend MCP `run_tests` and `instance_exec` with optional target/workspace/deadline/output settings and preserved compatibility keys in `mcp/wp-server/tools/debug.py` and `mcp/wp-server/tools/runtime.py`
- [ ] T053 [US1] Implement optional monotonic rate-limited MCP progress summaries in `mcp/wp-server/tools/jobs.py` without making notifications durable state
- [ ] T054 [US1] Run the US1 tests plus existing async/runtime/MCP suites with `.cli-venv/bin/python -m unittest tests.test_job_output tests.test_output_profiles tests.test_job_output_cursor tests.test_job_supervisor tests.test_job_service tests.test_remote_job_transport tests.test_job_cli tests.test_job_mcp tests.test_asyncjobs tests.test_runtime_transport tests.test_mcp_composition -v`
- [ ] T055 [US1] Run a local detached disconnect/resume smoke through `./sb exec --local --timeout 60 --detach -- .cli-venv/bin/python -c 'import time; print("start", flush=True); time.sleep(2); print("done", flush=True)'` and capture job/output evidence in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T056 [US1] Record the passing increment commit and push identity in `specs/032-remote-job-runtime/implementation-evidence.md` after committing supervisor, durable output, local/remote transport, CLI/MCP start/status/output, and tests

**Checkpoint**: US1 independently provides durable remote-safe execution and resumable
retained output. Smart/sampled streaming is a log-view operation, never a process pipe.

---

## Phase 4: User Story 2 - Inspect a Running or Stalled Job (Priority: P1)

**Goal**: Expose process liveness, health evidence, metrics, cancellation, reconciliation,
full output, artifacts, and explicit terminal reasons during and after execution.

**Independent Test**: Run controlled active, quiet, stalled, identity-mismatched,
cancelled, timed-out, unreachable, and restarted-host fixtures and verify lifecycle,
health, evidence, metrics, artifacts, and final results.

### Tests for User Story 2

- [ ] T057 [P] [US2] Add health classifier table tests for active, quiet, suspected-stalled, stuck, supervisor-unresponsive, orphaned, process-missing, unreachable, unknown, and terminal conditions in `tests/test_job_health.py` for FR-015 through FR-018 and SC-005
- [ ] T058 [P] [US2] Add Linux `/proc` and portable-fallback metric sampling tests for CPU, RSS, I/O, process count/state, disk, capability gaps, and movement digest in `tests/test_job_metrics.py`
- [ ] T059 [P] [US2] Add graceful/force/parent cancellation tests with process identity mismatch and descendant cleanup in `tests/test_job_cancellation.py` for FR-019 through FR-021
- [ ] T060 [P] [US2] Add host restart, stale heartbeat, missing final row, orphan, and best-evidence reconciliation tests in `tests/test_job_reconciliation.py` for FR-021/FR-022
- [ ] T061 [P] [US2] Add artifact containment, symlink/device/FIFO escape, count/size, hash, retention, bounded retrieval, and partial failure tests in `tests/test_job_artifacts.py` for FR-031/FR-032
- [ ] T062 [P] [US2] Add CLI/MCP status, metrics, artifact, cancel, retry, and cleanup contract tests in `tests/test_job_observation_contracts.py` for FR-038/FR-039

### Implementation for User Story 2

- [ ] T063 [P] [US2] Implement host/process resource metric sampling and movement evidence in `sandbox/jobs/metrics.py`
- [ ] T064 [P] [US2] Implement evidence-based lifecycle-independent health classification and threshold reporting in `sandbox/jobs/health.py`
- [ ] T065 [P] [US2] Implement constrained artifact planning, collection, hashing, indexing, expiry, and bounded retrieval in `sandbox/jobs/artifacts.py`
- [ ] T066 [US2] Record heartbeats/metrics/progress and apply warn-only or opt-in cancel-on-stall policy in `sandbox/jobs/supervisor.py`
- [ ] T067 [US2] Implement verified graceful/force/parent cancellation and retry use cases in `sandbox/application/job_service.py`
- [ ] T068 [US2] Implement on-read and maintenance reconciliation for boot change, PID reuse, supervisor loss, child loss, and incomplete finalization in `sandbox/jobs/retention.py` and `sandbox/application/job_service.py`
- [ ] T069 [US2] Implement terminal-job/log/metric/artifact retention planning and scoped cleanup with active-job protection in `sandbox/jobs/retention.py`
- [ ] T070 [US2] Add CLI metrics/artifact-get/cancel/retry/cleanup commands and full health rendering in `sandbox/commands/jobs_runtime.py`
- [ ] T071 [US2] Add MCP `job_metrics`, `job_artifacts`, `job_artifact_get`, `job_cancel`, `job_retry`, and `job_cleanup` tools in `mcp/wp-server/tools/jobs.py`
- [ ] T072 [US2] Run the US2 tests and failure-path stress fixture with `.cli-venv/bin/python -m unittest tests.test_job_health tests.test_job_metrics tests.test_job_cancellation tests.test_job_reconciliation tests.test_job_artifacts tests.test_job_observation_contracts -v`
- [ ] T073 [US2] Run active/quiet/stalled/timed-out local jobs through `./sb`, verify status latency and evidence, and append job IDs/results to `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T074 [US2] Commit and push the passing observation, health, cancellation, artifact, retry, and retention increment and record its identity in `specs/032-remote-job-runtime/implementation-evidence.md`

**Checkpoint**: US2 independently distinguishes lifecycle from evidence-based health and
supports safe mid-run inspection/cancellation without attaching to child pipes.

---

## Phase 5: User Story 3 - Reuse and Isolate Remote Workspaces (Priority: P1)

**Goal**: Reuse persistent named workspaces, serialize ordinary work in one instance,
allow explicit shared-safe jobs, and isolate bounded matrix cells across multiple
instances with independent results and cleanup.

**Independent Test**: Start two exclusive jobs in one workspace and observe queue order;
run explicitly shared-safe jobs where allowed; run simultaneous matrix cells in distinct
workspaces/instances; rerun, retain failure, reset, and destroy explicitly.

### Tests for User Story 3

- [ ] T075 [P] [US3] Add atomic exclusive/shared/lifecycle/host-capacity lease and stale-lease reconciliation tests in `tests/test_job_scheduler.py` for FR-025 through FR-028
- [ ] T076 [P] [US3] Add deterministic concurrent-safe <=21-character matrix label tests across projects, parents, retries, and canonical matrix values in `tests/test_workspace_labels.py`
- [ ] T077 [P] [US3] Add persistent create/list/status/reset/destroy, busy mutation, retained failure, idempotent create, and local/remote namespace tests in `tests/test_workspace_runtime.py` for FR-023/FR-024/FR-026
- [ ] T078 [P] [US3] Add same-instance serial, explicit shared-safe, immediate-busy suggestion, and process-isolation tests in `tests/test_workspace_concurrency.py` for FR-025/FR-026 and SC-006
- [ ] T079 [P] [US3] Add parent/child dependency, multi-command, fail-fast/continue, capacity queue, cell isolation, retry, and cleanup aggregation tests in `tests/test_job_matrix.py` for FR-027 through FR-030
- [ ] T080 [P] [US3] Add workspace CLI/MCP lifecycle and matrix contract tests in `tests/test_workspace_contracts.py` for FR-038

### Implementation for User Story 3

- [ ] T081 [US3] Implement transactional workspace/host capacity leases, queue state, renewal, release, and immediate-busy suggestions in `sandbox/jobs/scheduler.py`
- [ ] T082 [US3] Implement deterministic safe isolated label generation and target namespace separation in `sandbox/jobs/scheduler.py`
- [ ] T083 [US3] Implement parent coordinators, declared step dependencies, per-child deadlines/results, matrix capacity, fail-fast/continue, retry relationships, and aggregate status in `sandbox/jobs/scheduler.py`
- [ ] T084 [US3] Implement persistent workspace create/list/status and exclusive reset/destroy orchestration in `sandbox/application/workspace_service.py`
- [ ] T085 [US3] Adapt existing local and remote ensure/reset/destroy mechanisms behind workspace service contracts in `sandbox/transports/jobs.py`, `sandbox/transports/remote_jobs.py`, and `sandbox/core/_remote.py`
- [ ] T086 [US3] Enforce deploy/ensure/reset/destroy lifecycle leases and execution leases before side effects in `sandbox/application/job_service.py` and `sandbox/application/workspace_service.py`
- [ ] T087 [US3] Add `sb workspace create|list|status|reset|destroy` feature-owned command group in `sandbox/commands/workspaces.py` and register it in `sandbox/commands/manifest.py`
- [ ] T088 [US3] Add `sb test matrix` and declared multi-step plan submission/status rendering in `sandbox/commands/runtime.py` and `sandbox/commands/jobs_runtime.py`
- [ ] T089 [US3] Add MCP workspace lifecycle and parent/matrix job inputs/results in `mcp/wp-server/tools/jobs.py`
- [ ] T090 [US3] Adapt WordPress unit/integration tests and E2E shards to leaf/parent jobs with per-workspace isolation in `sandbox/commands/debug.py`, `sandbox/commands/e2e.py`, and `mcp/wp-server/tools/e2e.py`
- [ ] T091 [US3] Run the US3 tests plus existing fanout/E2E/WordPress suites with `.cli-venv/bin/python -m unittest tests.test_job_scheduler tests.test_workspace_labels tests.test_workspace_runtime tests.test_workspace_concurrency tests.test_job_matrix tests.test_workspace_contracts tests.test_fanout tests.test_e2e tests.test_runtime_test_modes -v`
- [ ] T092 [US3] Run live same-instance serialization, reusable rerun, two isolated simultaneous instances, failed retention, reset, and destroy checks through `./sb` and append evidence to `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T093 [US3] Commit and push the passing workspace lease, lifecycle, matrix, WordPress, and E2E increment and record its identity in `specs/032-remote-job-runtime/implementation-evidence.md`

**Checkpoint**: Multiple tests in one instance are serialized by default; multiple tests
across isolated instances run concurrently within host capacity and remain independently
inspectable.

---

## Phase 6: User Story 4 - Remote CI with Clear Compatibility Evidence (Priority: P2)

**Goal**: Preflight and run compatible Linux GitHub Actions graphs on one remote host,
with strict named divergence acceptance, safe mode, parent/child/matrix jobs, artifacts,
deadlines, retries, and cleanup.

**Independent Test**: Preflight a compatible workflow and a workflow containing every
catalogued unsupported behavior; run the compatible graph with dependencies/matrix/
artifacts; verify the incompatible graph has no side effect until named differences are
accepted.

### Tests for User Story 4

- [ ] T094 [P] [US4] Add versioned `act` compatibility catalog and exact workflow-location detector tests in `tests/test_ci_compatibility.py` for all entries in `specs/032-remote-job-runtime/contracts/remote-ci.md`
- [ ] T095 [P] [US4] Add workflow path, Linux runner, dependency graph, matrix, selection, expression-boundary, malformed YAML, and no-side-effect preflight tests in `tests/test_ci_workflow.py` for FR-033/FR-034/FR-037
- [ ] T096 [P] [US4] Add safe-mode classification tests for deploy/release/publish/external mutation, credential allowlists, unknown mutation, and recorded semantic differences in `tests/test_ci_safe_mode.py` for FR-035/FR-036
- [ ] T097 [P] [US4] Add remote CI parent/child/cell, capacity, outer timeout, output, artifact, retry, cleanup, and aggregate-result tests in `tests/test_remote_ci_jobs.py` for FR-033 through FR-037 and SC-009
- [ ] T098 [P] [US4] Add CI CLI/MCP preflight and run contract tests in `tests/test_ci_contracts.py`

### Implementation for User Story 4

- [ ] T099 [P] [US4] Implement the versioned known-divergence catalog and detector contract in `sandbox/ci/compatibility.py`
- [ ] T100 [P] [US4] Implement workflow loading, workspace containment, selected graph/dependency/matrix normalization, and Linux runner validation in `sandbox/ci/workflow.py`
- [ ] T101 [US4] Implement strict preflight result assembly, named acceptance validation, engine/version observation, and no-side-effect guarantee in `sandbox/ci/workflow.py`
- [ ] T102 [US4] Implement safe-mode step/action classification and a fail-closed neutralized workflow plan in `sandbox/ci/workflow.py`
- [ ] T103 [US4] Adapt existing `act` execution to remote parent/child/matrix jobs with Sandbox outer deadlines, logs, artifacts, retries, and cleanup in `sandbox/commands/ci.py`
- [ ] T104 [US4] Preserve existing CI parser/result compatibility and add `ci preflight` plus remote target/output/deadline/difference options in `sandbox/commands/ci.py`
- [ ] T105 [US4] Add MCP CI preflight/start/status integration over durable jobs in `mcp/wp-server/tools/ci.py` and register dependencies in `mcp/wp-server/tools/manifest.py`
- [ ] T106 [US4] Run CI compatibility, workflow, safe-mode, remote-job, contract, and existing CI suites with `.cli-venv/bin/python -m unittest tests.test_ci_compatibility tests.test_ci_workflow tests.test_ci_safe_mode tests.test_remote_ci_jobs tests.test_ci_contracts tests.test_ci -v`
- [ ] T107 [US4] Run disposable remote acceptance for a compatible Linux workflow with dependencies/matrix/artifacts and an incompatible workflow blocked before side effects, recording evidence in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T108 [US4] Commit and push the passing strict remote-CI increment and record its identity in `specs/032-remote-job-runtime/implementation-evidence.md`

**Checkpoint**: US4 provides bounded remote CI rather than hosted-runner parity claims;
all known semantic differences are visible and preflighted.

---

## Phase 7: User Story 5 - Choose Local or Remote Execution Deliberately (Priority: P3)

**Goal**: Make configured remote execution the recommended/default path while retaining
an explicit local override and identical job concepts across CLI and MCP.

**Independent Test**: Configure a project remote default, omit target, observe selected
remote; repeat with `--local`; verify unknown remote/workspace fails before side effects
and both results identify target resolution.

### Tests for User Story 5

- [ ] T109 [P] [US5] Add end-to-end CLI precedence and help-text tests for configured remote, explicit named remote, explicit local, no configured remote, unknown remote/workspace, and profile deadline reminders in `tests/test_remote_first_cli.py` for FR-001 through FR-004 and FR-041
- [ ] T110 [P] [US5] Add MCP instruction/catalog and shared target-input parity tests in `tests/test_remote_first_mcp.py` for FR-038/FR-039/FR-041
- [ ] T111 [P] [US5] Add CLI guide and skill content assertions for remote recommendation, deploy-first, deadlines, reusable workspaces, matrix isolation, and remote MCP preference in `tests/test_remote_first_guidance.py` for FR-041
- [ ] T112 [P] [US5] Add existing local CLI/MCP/runtime compatibility assertions with remote config enabled in `tests/test_local_override_compatibility.py` for FR-040 and SC-010

### Implementation for User Story 5

- [ ] T113 [US5] Apply the shared target resolver to `ensure`, `status`, `logs`, `exec`, and `test` command paths with mutually exclusive `--local|--remote` and workspace options in `sandbox/commands/lifecycle.py`, `sandbox/commands/runtime.py`, and `sandbox/cli.py`
- [ ] T114 [US5] Ensure all CLI human/JSON outputs report target/workspace/deadline source and actionable unknown-target guidance in `sandbox/commands/runtime.py`, `sandbox/commands/jobs_runtime.py`, and `sandbox/commands/lifecycle.py`
- [ ] T115 [US5] Apply shared target/deadline/output inputs and result translation across MCP runtime/test/job/workspace tools in `mcp/wp-server/tools/runtime.py`, `mcp/wp-server/tools/debug.py`, `mcp/wp-server/tools/jobs.py`, and `mcp/wp-server/tools/instances.py`
- [ ] T116 [US5] Update CLI-first command catalog and generated guidance to recommend configured remote execution and explicit local override in `sandbox/commands/runtime.py`
- [ ] T117 [US5] Update MCP server instructions to prefer co-located remote MCP and durable status/output reads in `mcp/wp-server/app.py` and `mcp/wp-server/tools/context.py`
- [ ] T118 [US5] Update the Sandbox CLI skill with remote-first development, deadlines, output modes, status inspection, workspace reuse/isolation, and cleanup guidance in `skills/sandbox-cli/SKILL.md`
- [ ] T119 [US5] Update repository agent reflexes and MCP catalog for remote-first job operation in `AGENTS.md` and `CLAUDE.md`
- [ ] T120 [US5] Run remote-first CLI/MCP/guidance/local-override tests with `.cli-venv/bin/python -m unittest tests.test_remote_first_cli tests.test_remote_first_mcp tests.test_remote_first_guidance tests.test_local_override_compatibility -v`
- [ ] T121 [US5] Run configured-remote and explicit-local live smoke tests through `./sb`, capture resolved targets/job results in `specs/032-remote-job-runtime/implementation-evidence.md`, and commit/push the passing remote-first interface increment

**Checkpoint**: All five stories are functional. Configured remote is recommended and
selected predictably; local operation is preserved by explicit override.

---

## Phase 8: Compatibility, Documentation, and Full Validation

**Purpose**: Complete migration adapters, documentation, acceptance, performance,
security, and release-regression evidence across all stories.

- [ ] T122 [P] Add legacy async-job ID/status/output/kill parity tests and 16/32-hex routing tests in `tests/test_asyncjob_compatibility.py` for FR-040
- [ ] T123 [P] Add Hermes job-view adapter parity tests without changing Hermes scheduling semantics in `tests/test_hermes_job_compatibility.py` for FR-040
- [ ] T124 Adapt `sandbox/core/_asyncjobs.py` and `sandbox/commands/jobs.py` behind the durable job service while preserving existing result keys and rollback path
- [ ] T125 Adapt Hermes job observation through an explicit service adapter in `sandbox/hermes/jobs.py` and `sandbox/commands/hermes.py` without adding compatibility-facade consumers
- [ ] T126 [P] Update user-facing remote-first overview, examples, output recovery, and CI scope in `README.md`
- [ ] T127 [P] Update CLI operation procedures and target/workspace/job command tables in `docs/cli-first-operation.md`
- [ ] T128 [P] Update project runtime schema, profiles, test plans, output policies, and examples in `docs/sandbox-config-reference.md`
- [ ] T129 [P] Update remote hosting/deployment guidance to distinguish source deploy, remote development jobs, remote MCP, and production hosting in `docs/remote-hosting.md` and `docs/remote-hosting-implementation.md`
- [ ] T130 [P] Update E2E/CI behavior, compatibility gate, matrices, artifacts, deadlines, and safe mode in `docs/ci-e2e-runner-spec.md`
- [ ] T131 [P] Add a durable job runtime operations/troubleshooting guide with storage pressure, unreachable host, stalled health, cancellation, reconciliation, retention, and recovery in `docs/remote-job-runtime.md`
- [ ] T132 Add acceptance fixtures for Node unit, PHP unit, WordPress integration, disconnect/resume, simultaneous labels, workspace reuse/failure/reset/destroy, matrix, artifact, timeout, and output retrieval in `tests/acceptance/test_remote_job_runtime.py`
- [ ] T133 Add acceptance fixtures for remote compatible/incompatible CI and safe-mode behavior in `tests/acceptance/test_remote_ci.py`
- [ ] T134 Run the complete pure test suite with `.cli-venv/bin/python -m unittest discover -s tests -v` and record totals/failures in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T135 Run existing local CLI, MCP, WordPress, generic Compose, async-job, E2E, CI, remote-hosting, architecture, and release-boundary suites named in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T136 Run 100 controlled detach/disconnect/reconnect cases and cursor duplicate checks, status latency checks, health-classification fixtures, deadline/cancel cases, and output/artifact retrieval measurements against SC-001 through SC-008 and record measurements in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T137 Run disposable remote Node/PHP/WordPress/multiple-workspace/matrix/CI acceptance through `./sb` and record remote identity, job IDs, source identities, outcomes, cleanup, and any skipped environment-dependent case in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T138 Verify secret redaction, unsafe artifact rejection, bounded response limits, process identity mismatch protection, disk reserve behavior, and no raw remote Docker exposure in `specs/032-remote-job-runtime/security-review.md`
- [ ] T139 Validate every command in `specs/032-remote-job-runtime/quickstart.md`, update it only where observed behavior differs, and record the validation environment in `specs/032-remote-job-runtime/implementation-evidence.md`
- [ ] T140 Run `git diff --check`, inspect `git status`, confirm no changes under `runtime/wp/` or `vendor/`, commit all passing code/docs/evidence, and push the active branch

---

## Dependencies and Execution Order

### Phase dependencies

- **Phase 1 Setup** has no dependency.
- **Phase 2 Foundational** depends on Phase 1 and blocks every story.
- **US1** depends on Phase 2 and is the minimum usable product.
- **US2** depends on US1 retained output/supervisor, but remains independently verifiable
  through controlled job fixtures.
- **US3** depends on US1 submission/supervision and US2 reconciliation for safe lease
  recovery.
- **US4** depends on US1 durable jobs and US3 parent/child/matrix scheduling.
- **US5** depends on the shared resolver from Phase 2 and integrates all implemented
  command/tool surfaces after US1-US4.
- **Phase 8** depends on all selected stories and is required before feature completion.

### User-story dependency graph

```text
Setup -> Foundation -> US1 -> US2 -> US3 -> US4
                         \______________ -> US5
US1 + US2 + US3 + US4 + US5 -> Compatibility/Full Validation
```

### Within each story

1. Add tests and confirm the relevant failure.
2. Implement models/mechanisms before application services.
3. Implement application services before CLI/MCP adapters.
4. Run targeted pure tests.
5. Run the story's live or mocked independent acceptance.
6. Commit and push the passing increment.

## Parallel Opportunities

- T002-T004 may run in parallel after T001.
- T009-T014 are independent foundational test files.
- T019 and T023-T025 may run in parallel after models/repository contracts are stable.
- US1 output tests (T031-T033), supervisor/service tests (T034-T035), and transport/
  interface tests (T036-T038) own separate files and can run concurrently.
- US2 health, metrics, cancellation, reconciliation, artifact, and contract tests
  T057-T062 can run concurrently.
- US3 lease, label, lifecycle, concurrency, matrix, and interface tests T075-T080 can run
  concurrently.
- US4 catalog/workflow/safe-mode/job/interface tests T094-T098 can run concurrently.
- Documentation tasks T126-T131 own different files and can run concurrently after
  behavior is stable.

## Parallel Examples

### US1 test-first split

```text
Worker A: T031-T033 output/redaction/cursor/profile tests
Worker B: T034-T035 supervisor and job-service tests
Worker C: T036-T038 remote transport, CLI, and MCP contract tests
Integration owner: T039-T053, then T054-T056
```

### US3 multiple-test split

```text
Worker A: T075 + T081 lease and host-capacity scheduler
Worker B: T076-T077 + T082/T084 labels and workspace lifecycle
Worker C: T079 + T083 parent/child/matrix coordinator
Integration owner: T078/T080 + T085-T093 concurrency and interfaces
```

## Implementation Strategy

### MVP first

1. Complete Setup and Foundation.
2. Complete US1 through T056.
3. Validate durable local and mocked-remote disconnect/resume behavior.
4. Stop safely if only remote command/test execution and retained streaming are needed.

### Incremental delivery

1. US1: durable detached execution and resumable output.
2. US2: health, metrics, cancellation, artifacts, reconciliation.
3. US3: reusable workspaces and isolated parallel matrices.
4. US4: strict bounded remote CI.
5. US5: remote-first defaults and unified guidance.
6. Compatibility/full validation before declaring parity.

## Requirement Coverage

| Requirements | Primary tasks |
|---|---|
| FR-001-FR-004 target/profile/deadline | T011-T012, T019-T022, T109-T121 |
| FR-005 exact source deploy | T036, T047-T049, T137 |
| FR-006-FR-008 durability/idempotency/detachment | T009-T010, T015-T018, T034-T046 |
| FR-009-FR-014 output/redaction/profiles/storage | T031-T044, T050-T054, T138 |
| FR-015-FR-022 status/health/cancel/identity/reconcile | T057-T074, T136, T138 |
| FR-023-FR-030 workspaces/concurrency/matrix/plans | T075-T093, T132, T137 |
| FR-031-FR-032 artifacts/retention | T061, T065, T069-T073, T132, T138 |
| FR-033-FR-037 remote CI | T094-T108, T130, T133, T137 |
| FR-038-FR-039 CLI/MCP/progress | T037-T038, T050-T053, T062, T070-T071, T080, T089, T098, T105, T109-T117 |
| FR-040 compatibility | T054, T091, T106, T112, T120, T122-T125, T134-T135 |
| FR-041 guidance | T109-T111, T116-T121, T126-T131 |
| SC-001-SC-010 measurable outcomes | T055, T073, T092, T107, T121, T132-T139 |

## Notes

- `[P]` never means two writers should edit the same file concurrently.
- Compatibility adapters are rollback controls; do not remove old paths in this feature.
- Do not silently skip a remote acceptance failure. Record an environmental skip with
  exact missing capability, or report the feature blocked if required acceptance cannot
  run.
- Never include secrets, SSH targets containing credentials, or unredacted process
  output in `implementation-evidence.md`.
- Every runtime mutation uses `./sb`; raw Docker/SSH is limited to owned transport
  implementation tests and never substitutes for the product's live verification.
