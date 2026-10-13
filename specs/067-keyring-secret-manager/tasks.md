# Tasks: Cross-Platform Open-Source Secret Manager

**Input**: Design documents from `/specs/067-keyring-secret-manager/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Execution rule**: Complete every implementation and documentation task before running
workflows or writing/running tests. This repository rule overrides test-first template text.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can be edited in parallel without overlapping an incomplete task
- **[Story]**: User story from `spec.md`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish explicit dependency and adapter registration boundaries.

- [x] T001 Add one pinned CLI dependency manifest and revision-digest convergence support in `requirements-cli.txt` and `sandbox/core/_config.py`
- [x] T002 [P] Align MCP dependency pins with the shared credential-store dependencies in `mcp/wp-server/requirements.txt`
- [x] T003 Create provider and credential adapter package skeletons and explicit manifests in `sandbox/secrets/adapter_manifest.py`, `sandbox/secrets/providers/__init__.py`, `sandbox/secrets/providers/manifest.py`, `sandbox/secrets/credentials/__init__.py`, and `sandbox/secrets/credentials/manifest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add strict policy models, stable errors, and injected adapter seams used by all stories.

- [x] T004 Extend source-policy parsing with backward-compatible `file` and strict `infisical` descriptors in `sandbox/config/secrets.py` and `sandbox/secrets/policy.py`
- [x] T005 Define provider policies, capability records, credential states, and stable provider outcomes in `sandbox/secrets/models.py` and `sandbox/secrets/providers/base.py`
- [x] T006 Implement the no-redirect, no-proxy, verified-TLS, deadline-aware capped HTTP transport in `sandbox/secrets/providers/http.py`
- [x] T007 Define the controller credential-store interface and versioned non-secret locator in `sandbox/secrets/credentials/base.py`
- [x] T008 Wire source, provider, transport, and credential factories through `sandbox/secrets/context.py` without direct registry/state reads
- [x] T009 Preserve durable audit-before-callback ordering while adding provider resolution seams in `sandbox/secrets/service.py`

**Checkpoint**: Provider behavior is representable, but no user-facing provider operation is enabled yet.

---

## Phase 3: User Story 1 - Use One Registered Secret Across Controllers (Priority: P1) MVP

**Goal**: Resolve one registered item on Linux and macOS and deliver it only to a fixed child.

**Independent Test**: With injected Linux/macOS stores and a synthetic provider, the same
fixed profile receives the same item, each controller uses a distinct credential, and no
retained surface contains any credential or value.

- [x] T010 [P] [US1] Implement explicit macOS Keychain create/replace/get behavior with no backend discovery in `sandbox/secrets/credentials/macos.py`
- [x] T011 [P] [US1] Implement Linux Secret Service create/replace/get behavior that detects locked or missing sessions without unlock or daemon startup in `sandbox/secrets/credentials/linux.py`
- [x] T012 [US1] Implement one-login/one-exact-read Infisical resolution with memory-only sessions and expansion/import disabled in `sandbox/secrets/providers/infisical.py`
- [x] T013 [US1] Add local protected credential placement and fixed-profile use actions in `sandbox/commands/secrets.py` and `sandbox/commands/manifest.py`
- [x] T014 [US1] Route provider values through the existing minimal-environment fixed-child runner in `sandbox/secrets/service.py` and `sandbox/secrets/runner.py`
- [x] T015 [US1] Preserve bounded MCP fixed-profile behavior for provider sources in `mcp/wp-server/tools/secrets.py`

**Checkpoint**: The synthetic cross-controller fixed-use scenario is implementable without provider browsing.

---

## Phase 4: User Story 2 - Preserve Exact Scope and Caller Boundaries (Priority: P1)

**Goal**: Enforce immutable registered selectors and refuse broader CLI/MCP authority.

**Independent Test**: Cross-project, selector override, browse, reveal, mutation, and
arbitrary-child attempts are refused while one exact registered fixed use succeeds.

- [x] T016 [US2] Enforce exact immutable endpoint/project/environment/path/key selection and singleton key inspection in `sandbox/secrets/providers/infisical.py` and `sandbox/secrets/service.py`
- [x] T017 [US2] Enforce provider mode and fixed-profile authorization without request-time selector or child overrides in `sandbox/secrets/policy.py` and `sandbox/commands/secrets.py`
- [x] T018 [US2] Keep MCP arguments and catalog bounded to existing inspection, validation, and fixed-use tools in `mcp/wp-server/tools/secrets.py` and `mcp/wp-server/tools/manifest.py`
- [x] T019 [US2] Add secret-free capability metadata that distinguishes provider project scope from Sandbox selector policy in `sandbox/secrets/service.py`

**Checkpoint**: The provider source cannot become a general vault or process wrapper through Sandbox.

---

## Phase 5: User Story 3 - Fail Closed with Actionable Outcomes (Priority: P2)

**Goal**: Produce bounded, stable, secret-free results for every defined failure class.

**Independent Test**: Inject each credential, transport, authorization, provider, parsing,
size, timeout, and capacity failure and observe its contracted code, remedy, no retry, no
fallback, and audit ordering.

- [x] T020 [P] [US3] Map OS-store absence, lock, unavailable session, and backend failure to stable outcomes in `sandbox/secrets/credentials/linux.py` and `sandbox/secrets/credentials/macos.py`
- [x] T021 [P] [US3] Map DNS/connect, TLS, redirect, timeout, oversized, malformed, 401/403, 404, 429, and 5xx results without response disclosure in `sandbox/secrets/providers/http.py` and `sandbox/secrets/providers/infisical.py`
- [x] T022 [US3] Implement value-free local capability inspection and bounded unauthenticated `/api/status` reporting in `sandbox/secrets/providers/infisical.py` and `sandbox/secrets/service.py`
- [x] T023 [US3] Propagate stable provider outcomes and remedies through text/JSON CLI envelopes in `sandbox/commands/secrets.py`
- [x] T024 [US3] Propagate identical stable provider outcomes through MCP envelopes in `mcp/wp-server/tools/secrets.py`
- [x] T025 [US3] Require non-secret project identity metadata and a present controller-local credential before retrieval while keeping provider role/capacity claims out of local registration in `sandbox/config/secrets.py` and `sandbox/secrets/service.py`

**Checkpoint**: Failures are diagnosable without fetching, printing, retrying, or falling back.

---

## Phase 6: User Story 4 - Adopt Without Disrupting Existing Sources (Priority: P3)

**Goal**: Keep all file-source behavior unchanged while Infisical remains opt-in.

**Independent Test**: Existing file-source conformance results are identical with provider
support installed, and provider registration performs no deployment, mutation, migration,
credential deletion, daemon installation, or existing secret-file read.

- [x] T026 [US4] Preserve the existing file `SourceRegistry` adapter as the default for missing/`file` source types in `sandbox/secrets/sources.py` and `sandbox/secrets/adapter_manifest.py`
- [x] T027 [US4] Keep provider dependencies lazily imported so file-only CLI and MCP paths run without an OS keyring backend in `sandbox/secrets/context.py`
- [x] T028 [US4] Verify registration code remains validation-only with no provider/store/source-file side effects in `sandbox/config/secrets.py` and `sandbox/secrets/context.py`

**Checkpoint**: Provider adoption is explicit and reversible without removing a proven path.

---

## Phase 7: Documentation and Runtime Workflow Exercise

**Purpose**: Finish user-facing guidance, then exercise supported synthetic workflows before tests are authored.

- [x] T029 Update configuration, CLI/MCP authority, Tailscale/TLS boundary, OS-store limitations, and operator responsibilities in `README.md`, `docs/sandbox-config-reference.md`, and `docs/secret-inspection.md`
- [x] T030 Update agent/operator workflow guidance and dependency distribution notes in `CLAUDE.md`, `skills/sandbox-cli/SKILL.md`, and `mcp/wp-server/README.md`
- [x] T031 Exercise supported local `sb secrets` workflows with synthetic configuration and injected fakes, recording secret-free observations against `specs/067-keyring-secret-manager/quickstart.md`

---

## Phase 8: Focused Regression and Acceptance Gates

**Purpose**: Write regressions only after the full implementation and workflow exercise, then run bounded gates.

- [x] T032 [P] Add configuration and file-source compatibility regressions in `tests/test_secret_config.py` and `tests/test_secret_service.py`
- [x] T033 [P] Add fake macOS/Linux credential-store state and no-unlock regressions in `tests/test_secret_credentials.py`
- [x] T034 [P] Add exact-request, transport, error-map, no-retry, and zero-value-status regressions in `tests/test_secret_infisical.py`
- [x] T035 [P] Add CLI protected-input, JSON parity, fixed-child, and refusal regressions using synthetic subprocess environments in `tests/test_secret_commands.py`
- [x] T036 [P] Add MCP mode, selector, mutation, arbitrary-child, and secret-free envelope regressions in `tests/test_secret_mcp.py`
- [x] T037 Run each focused `PYTHONPATH=. python3 -m unittest <module>` gate, record durations, and fix every test taking 5 seconds or longer
- [ ] T038 Run diff/static checks and the repository's final required suite once, recording the 20 slowest tests and treating empty/malformed evidence as unknown
- [ ] T039 With separate authorization, exercise both available and unavailable identity/controller capacity plus role, private-route, cross-project, and two-controller Infisical acceptance, prove capacity refusal precedes authentication/item retrieval, and write versioned secret-free evidence per `specs/067-keyring-secret-manager/quickstart.md`; otherwise record it as unverified rather than passed

---

## Dependencies & Execution Order

- Setup T001-T003 precedes foundational T004-T009.
- All user stories depend on the foundation. US2 builds on the US1 exact-read path; US3
  hardens its failures; US4 proves compatibility after the provider path exists.
- T029-T030 follow all code edits. T031 is the first workflow exercise.
- T032-T036 are written only after T031. T037 precedes T038. T039 requires explicit live
  authorization and is never inferred from local tests.

## Parallel Opportunities

- T002 can proceed independently of T001; package skeleton work T003 follows their chosen pins.
- T010 and T011 are independent OS adapters.
- T020 and T021 address independent failure boundaries.
- T032-T036 touch separate focused test modules and can be authored in parallel after T031.

## Implementation Strategy

The MVP is Phases 1-3: one exact provider item through a registered fixed profile on both
controller types. Phases 4-6 close authority, failure, and backward-compatibility risks
before any verification claim. Documentation and all implementation land before the first
workflow exercise; regression authoring and test execution are deliberately last.
