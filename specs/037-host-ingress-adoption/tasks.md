# Tasks: Host Ingress Adoption

**Input**: Design documents from `specs/037-host-ingress-adoption/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`,
`quickstart.md`

**Tests**: Listener-topology, adapter-contract, transactional rollback, ownership, and live
route tests are required by the specification and constitution. Tests precede implementation.

**Organization**: Tasks are grouped by user story, with read-only listener correctness as
the first shippable slice and per-port access preserved throughout.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish explicit feature packages, registrations, and host fixtures.

- [X] T001 Create ingress and adapter package exports in `sandbox/ingress/__init__.py` and `sandbox/ingress/adapters/__init__.py`
- [ ] T002 [P] Add free, exact-loopback, dedicated-loopback, IPv4/IPv6 wildcard, split-owner, and product listener fixtures in `tests/host_fixtures/ingress/`
- [ ] T003 Register ingress surfaces through the shared domains command/MCP manifests in `sandbox/commands/manifest.py`, `sandbox/commands/domains.py`, and `mcp/wp-server/tools/manifest.py`
- [ ] T004 Update static command/MCP/modularity inventories for new ingress dependencies in `tests/test_command_composition.py`, `tests/test_mcp_composition.py`, and `tests/test_modularity.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish typed endpoint semantics, deterministic adapters, owned state, and
the reusable transaction boundary before route mutation.

- [X] T005 Write failing endpoint normalization, bind-overlap, protocol-set, ownership, digest, and transition tests in `tests/test_ingress_models.py`
- [X] T006 Implement immutable listener, observation, selection, route, consent, support, transaction, and cleanup models in `sandbox/ingress/models.py`
- [X] T007 [P] Write failing deterministic adapter order, duplicate registration, capability, tier, and proof-gate tests in `tests/test_ingress_registry.py`
- [X] T008 Implement ingress adapter protocol, registry, and product support/proof manifest in `sandbox/ingress/registry.py` and `sandbox/ingress/manifest.py`
- [X] T009 [P] Write failing repository locking, atomicity, migration, attribution, drift, and residual-retention tests in `tests/test_ingress_repository.py`
- [X] T010 Implement the versioned locked ingress state repository in `sandbox/ingress/repository.py`
- [X] T011 [P] Write failing full-current/full-candidate validation, atomic activation, reload, baseline-health, rollback, and timeout tests in `tests/test_ingress_transactions.py`
- [X] T012 Implement the adapter-neutral validation/activation/health/rollback transaction runner in `sandbox/ingress/transaction.py`
- [ ] T013 Write failing helper verb/path/service/owner/symlink/race and secret-redaction tests in `tests/test_ingress_helper.py`
- [ ] T014 Define the fixed-verb ingress helper schema, canonical owned-fragment paths, symlink refusal, service allowlist, and install-copy flow in `tools/ingress-helper.sh`
- [ ] T015 Compose ingress and clean-URL services with injected listener, process, HTTP, repository, domain, and runtime dependencies in `sandbox/application/context.py`

**Checkpoint**: Route decisions and transactions are expressible without touching a real
incumbent; legacy clean URLs remain unchanged.

---

## Phase 3: User Story 1 - Detect Real Port Ownership Before Acting (Priority: P1)

**Goal**: Observe real kernel bind scopes for every required protocol, distinguish own and
foreign listeners, avoid split ingress, and never misdiagnose a port collision as Docker.

**Independent Test**: Replay every listener fixture and run live read-only detection while
comparing all process/listener state before and after.

### Tests for User Story 1

- [X] T016 [P] [US1] Write failing Linux `/proc` and `ss` exact/wildcard IPv4/IPv6 bind-overlap tests in `tests/test_ingress_listeners_linux.py`
- [X] T017 [P] [US1] Write failing macOS `lsof` listener normalization and partial-process-evidence tests in `tests/test_ingress_listeners_macos.py`
- [X] T018 [P] [US1] Write failing Sandbox-owner, foreign-owner, unknown-owner, stale-process, and permission-limited tests in `tests/test_ingress_detection.py`
- [X] T019 [P] [US1] Write failing required-protocol, unrequested-protocol, split-owner, TLS, and wildcard-capability selection tests in `tests/test_ingress_selection.py`
- [X] T020 [US1] Write failing `proxy_available`, startup error, and per-port fallback regression tests in `tests/test_proxy_availability.py`

### Implementation for User Story 1

- [X] T021 [US1] Implement kernel-authoritative endpoint observation and exact/wildcard IPv4/IPv6 overlap in `sandbox/ingress/listeners.py`
- [X] T022 [US1] Implement best-effort process/product evidence without using it as bind authority in `sandbox/ingress/detection.py`
- [X] T023 [US1] Implement capability-aware one-ingress selection and explicit split-owner refusal in `sandbox/application/ingress_service.py`
- [X] T024 [US1] Replace binary-only proxy availability and misleading Docker errors with structured listener results in `sandbox/core/_domains.py`
- [ ] T025 [US1] Expose read-only `domains ingress detect|support|status` JSON/text contracts in `sandbox/commands/domains.py`
- [ ] T026 [US1] Run live free/exact/wildcard/owned/foreign detection and capture non-mutating listener evidence in `specs/037-host-ingress-adoption/evidence/listeners.md`

**Checkpoint**: Detection is independently useful and safe even before any adopter is
advertised.

---

## Phase 4: User Story 2 - Adopt a Supported Incumbent Safely (Priority: P1)

**Goal**: Add an attributable route through one live-proven incumbent only after B verifies
the hostname, preserving prior routes and rolling back every failed transaction.

**Independent Test**: For each advertised adapter, perform add/request/update/request/remove
with foreign route health baselines and inject validation, reload, and health failures.

### Tests for User Story 2

- [ ] T027 [P] [US2] Write failing Sandbox Caddy endpoint-ownership, exact-bind, fragment, lifecycle, and fallback tests in `tests/test_ingress_sandbox_caddy.py`
- [ ] T028 [P] [US2] Write failing Herd/Valet link/proxy/secure capability, ownership, and rollback tests in `tests/test_ingress_herd_valet.py`
- [ ] T029 [P] [US2] Write failing system nginx owned-fragment, full-config validation, reload, health, and rollback tests in `tests/test_ingress_nginx.py`
- [ ] T030 [P] [US2] Write failing Apache owned-fragment, module/capability, full-config validation, graceful reload, and rollback tests in `tests/test_ingress_apache.py`
- [ ] T031 [P] [US2] Write failing persistent Caddy import-fragment, service-identity validation, reload, health, and rollback tests in `tests/test_ingress_caddy.py`
- [ ] T032 [P] [US2] Write failing Traefik file-provider enablement, owned-file, dynamic reload, health, and rollback tests in `tests/test_ingress_traefik.py`
- [ ] T033 [US2] Write failing A→B→A hostname/address/capability handshake and no-route-on-DNS-failure tests in `tests/test_clean_url_service.py`
- [ ] T034 [US2] Write failing foreign-hostname/wildcard collision, backend update, idempotency, and baseline-route preservation tests in `tests/test_ingress_service.py`

### Implementation for User Story 2

- [ ] T035 [US2] Implement exact-endpoint-safe Sandbox Caddy adapter using existing owned fragments in `sandbox/ingress/adapters/sandbox_caddy.py`
- [ ] T036 [US2] Implement Herd/Valet route-only adapter with runtime ownership handed to C in `sandbox/ingress/adapters/herd_valet.py`
- [ ] T037 [US2] Implement system nginx owned-fragment adapter with complete config validation and graceful reload in `sandbox/ingress/adapters/nginx.py`
- [ ] T038 [US2] Implement Apache owned-fragment adapter with complete config validation and graceful reload in `sandbox/ingress/adapters/apache.py`
- [ ] T039 [US2] Implement persistent system Caddy import-fragment adapter without ephemeral API state in `sandbox/ingress/adapters/caddy.py`
- [ ] T040 [US2] Implement enabled Traefik file-provider adapter with owned dynamic fragment lifecycle in `sandbox/ingress/adapters/traefik.py`
- [ ] T041 [US2] Implement foreign route collision checks, transaction orchestration, baseline probes, and route verification in `sandbox/application/ingress_service.py` and `sandbox/ingress/verification.py`
- [ ] T042 [US2] Implement C-backend → A-capabilities → B-resolution → A-activation sequencing in `sandbox/application/clean_url_service.py`
- [ ] T043 [US2] Delegate legacy clean-URL and proxy entry points through the composed service while preserving rollback paths in `sandbox/core/_domains.py`
- [ ] T044 [US2] Run live system Caddy add/request/update/request/remove, foreign-route, and rollback conformance and capture evidence in `specs/037-host-ingress-adoption/evidence/system-caddy.md`

**Checkpoint**: The current host's proven Caddy can serve a clean URL without a Sandbox
proxy; other adapters remain unadvertised until individually proven.

---

## Phase 5: User Story 3 - Preserve Ownership Through Cleanup and Drift (Priority: P2)

**Goal**: Remove only unchanged owned routes, preserve drift/foreign state, and retain
recovery identity before instance deletion.

**Independent Test**: Exercise normal, changed, unavailable, repeated, and uninstall cleanup
while byte-comparing foreign fragments and incumbent health.

### Tests for User Story 3

- [ ] T045 [P] [US3] Write failing unchanged, target-drifted, property-drifted, foreign-marker, and unavailable cleanup tests in `tests/test_ingress_cleanup.py`
- [ ] T046 [P] [US3] Write failing repeated cleanup, residual retry, incumbent replacement, and uninstall aggregation tests in `tests/test_ingress_recovery.py`
- [ ] T047 [US3] Write failing registry/local deletion-order and route-recovery survival tests in `tests/test_ingress_destroy_ordering.py`

### Implementation for User Story 3

- [ ] T048 [US3] Implement observed-versus-last-applied route status and compare-before-change cleanup in `sandbox/application/ingress_service.py`
- [ ] T049 [US3] Persist non-secret incomplete cleanup records and retry transitions in `sandbox/ingress/repository.py`
- [ ] T050 [US3] Persist ingress cleanup outcome before registry/local identity deletion and expose independent retry in `sandbox/commands/instances_cmd.py`
- [ ] T051 [US3] Implement `domains ingress cleanup|reconcile` JSON/text contracts in `sandbox/commands/domains.py`
- [ ] T052 [US3] Run live normal/repeated/drift/unavailable cleanup and capture foreign-route health evidence in `specs/037-host-ingress-adoption/evidence/cleanup.md`

**Checkpoint**: Destroy and uninstall are conservative and retryable.

---

## Phase 6: User Story 4 - Understand Support, Consent, and Fallback (Priority: P2)

**Goal**: Report truthful support/capability tiers, honor pin precedence and remembered
consent, keep credentials local, and never block MCP/CI with an interactive prompt.

**Independent Test**: List tiers, accept/decline/reconsider interactively, invoke without a
TTY or credentials, and conflict project/machine pins across every declared product.

### Tests for User Story 4

- [ ] T053 [P] [US4] Write failing accept/decline/reconsider, incumbent identity, and machine-scope consent tests in `tests/test_ingress_consent.py`
- [ ] T054 [P] [US4] Write failing non-TTY pending-consent/pending-credential/no-prompt/no-mutation tests in `tests/test_ingress_noninteractive.py`
- [ ] T055 [P] [US4] Write failing project-pin/machine-override precedence, unavailable-pin, and pin-source tests in `tests/test_ingress_pins.py`
- [ ] T056 [P] [US4] Write failing NPM/DDEV/Local/XAMPP/Laragon/WAMP detect-only/outside-platform tests in `tests/test_ingress_detect_only.py`
- [ ] T057 [US4] Write failing CLI/MCP parity, support matrix, secret-redaction, and fallback-result tests in `tests/test_ingress_cli_mcp.py`

### Implementation for User Story 4

- [ ] T058 [US4] Implement TTY-only consent, remembered decline, reconsideration, and pin-source selection in `sandbox/application/ingress_service.py`
- [ ] T059 [US4] Implement detect-only and outside-platform declarations using public evidence only in `sandbox/ingress/adapters/detect_only.py`
- [ ] T060 [US4] Implement machine-local credential references and redacted pending results in `sandbox/ingress/models.py` and `sandbox/application/ingress_service.py`
- [ ] T061 [US4] Complete `domains ingress support|plan|apply|status|cleanup|reconsider` handlers in `sandbox/commands/domains.py`
- [ ] T062 [US4] Implement import-safe ingress MCP tools using explicit service dependencies in `mcp/wp-server/tools/domains.py`
- [ ] T063 [US4] Capture support-tier, consent, pin, credential-pending, and detect-only evidence in `specs/037-host-ingress-adoption/evidence/support-and-consent.md`

**Checkpoint**: “Detected” and “adoptable” are never conflated, and automation never hangs.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, architecture guards, full regression, and live evidence.

- [ ] T064 [P] Update clean URL, ingress selection/pins, support tiers, consent, status, and fallback guidance in `README.md` and `docs/sandbox-config-reference.md`
- [ ] T065 [P] Document listener semantics, adapter proof requirements, transaction recovery, and platform matrix in `docs/host-ingress.md` and `docs/cross-platform-support.md`
- [ ] T066 Add static guards against ingress-state JSON consumers, unregistered adapters, and compatibility-facade imports in `tests/test_architecture_boundaries.py`
- [ ] T067 Run unit/contract/integration suites, verify 2-second detection/status, 3-second planning, and 30-second transaction bounds, and run `git diff --check`, fixing regressions in `tests/`
- [ ] T068 Run `specs/037-host-ingress-adoption/quickstart.md` end to end through `./sb` and complete the evidence index in `specs/037-host-ingress-adoption/evidence/README.md`
- [ ] T069 Verify existing Sandbox Caddy/per-port live parity and the corrected conflict diagnosis in `specs/037-host-ingress-adoption/evidence/compatibility.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup has no dependencies.
- Foundational depends on Setup and blocks every user story.
- US1 depends on Foundational and blocks all route mutation.
- US2 depends on US1 and the 038 domain-service handshake.
- US3 depends on US2 ownership and transaction records.
- US4 depends on the selection and repository contracts but can proceed beside US2 adapters.
- Polish depends on all delivered stories.

### User Story Completion Order

```text
Foundation → US1 listener truth → US2 safe adoption → US3 cleanup → Polish
                         └───────→ US4 consent/support ────────┘
```

### Parallel Opportunities

- T002, T007, T009, T011, and independent listener/adapter tests marked `[P]` can proceed independently.
- Product adapters use separate modules after the transaction contract stabilizes.
- Documentation T064–T065 may proceed after contracts stabilize.

## Parallel Example: User Story 2

```text
T027 Sandbox Caddy tests
T028 Herd/Valet tests
T029 nginx tests
T030 Apache tests
T031 system Caddy tests
T032 Traefik tests
```

## Implementation Strategy

### Listener-Truth MVP

1. Complete Setup and Foundational.
2. Ship US1 read-only detection and corrected fallback diagnostics first.
3. Integrate 038, then prove the current host's system Caddy adapter through US2.
4. Keep every other mutation adapter unadvertised until its own live evidence passes.

### Incremental Delivery

1. Land listener observation with zero behavior change beyond accurate diagnostics.
2. Land one live-proven transactional incumbent path.
3. Add conservative cleanup, consent, and additional proof-gated adapters.

## Notes

- `[P]` tasks touch independent files or fixtures and may run concurrently.
- Every test task must fail for the intended missing behavior before implementation.
- All runtime-touching validation uses `./sb`; no raw service or proxy commands in user workflows.
- Verified logical groups are committed and pushed on the active non-`main` branch.
