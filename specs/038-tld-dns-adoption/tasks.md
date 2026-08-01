# Tasks: TLD and DNS Adoption

**Input**: Design documents from `specs/038-tld-dns-adoption/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`,
`quickstart.md`

**Tests**: Contract, integration, collision, drift, and live resolver tests are required by
the specification and constitution. Test tasks precede their implementation tasks.

**Organization**: Tasks are grouped by user story so exact-name resolution can ship before
wildcard support while existing per-port URLs remain available throughout.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish registered feature surfaces and deterministic host fixtures.

- [X] T001 Create the network domain package and adapter exports in `sandbox/network/__init__.py` and `sandbox/network/adapters/__init__.py`
- [X] T002 [P] Add systemd-resolved, NetworkManager, macOS, dnsmasq, Herd/Valet, unsupported, and collision host observations in `tests/host_fixtures/resolvers/`
- [X] T003 Register the feature-owned domains command module and MCP group in `sandbox/commands/manifest.py`, `sandbox/commands/domains.py`, and `mcp/wp-server/tools/manifest.py`
- [X] T004 Update static command/MCP/modularity inventory expectations in `tests/test_command_composition.py`, `tests/test_mcp_composition.py`, and `tests/test_modularity.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build provenance, typed contracts, adapter registration, owned state, and
collision-safe shared mechanisms before any resolver mutation.

- [X] T005 Write failing omitted-versus-explicit hostname/TLD provenance tests for WordPress and generic Compose descriptors in `tests/test_domain_config.py`
- [X] T006 Implement hostname/TLD normalization with explicit provenance and machine-override precedence in `sandbox/config/domains.py` and register it in `sandbox/config/manifest.py`
- [X] T007 Write failing validation, canonical digest, transition, and redaction tests for all domain entities in `tests/test_domain_models.py`
- [X] T008 [P] Implement immutable domain intent, observation, plan, binding, authority, consent, and cleanup models in `sandbox/network/models.py`
- [X] T009 [P] Write failing deterministic adapter order, duplicate registration, support-tier, and proof-gate tests in `tests/test_domain_registry.py`
- [X] T010 Implement the resolver adapter contract, registry, and live-evidence manifest in `sandbox/network/registry.py` and `sandbox/network/manifest.py`
- [X] T011 [P] Write failing locking, atomicity, migration, attribution, compare-before-change, and residual-retention tests in `tests/test_domain_repository.py`
- [X] T012 Implement the versioned locked resolver-state repository in `sandbox/network/repository.py`
- [X] T013 [P] Write failing UDP-and-TCP endpoint collision, race, foreign-owner, and release tests in `tests/test_domain_endpoint_allocator.py`
- [X] T014 Extend the shared port mechanism with paired UDP/TCP reservation and ownership checks in `sandbox/services/ports.py`
- [X] T015 Write failing helper verb, path, resolver identity, symlink, race, and non-owner tests in `tests/test_resolver_helper.py`
- [X] T016 Define the fixed-verb resolver helper schema, canonical path validation, symlink refusal, and install-copy flow in `tools/resolver-helper.sh`
- [X] T017 Compose the domain service with injected resolver, process, HTTP, endpoint, registry, repository, and ingress dependencies in `sandbox/application/context.py`

**Checkpoint**: Configuration intent and resolver plans can be calculated without host
mutation; existing lifecycle behavior remains unchanged.

---

## Phase 3: User Story 1 - Resolve Through the Active Host Manager (Priority: P1)

**Goal**: Detect the real resolver owner, apply only a supported scoped integration, and
verify a fresh answer plus ingress request without global takeover.

**Independent Test**: On every advertised environment, compare resolver ownership and
unrelated answers before/after, resolve the instance name freshly, and request it through A.

### Tests for User Story 1

- [X] T018 [P] [US1] Write failing read-only owner/mode/extension/actual-answer detection tests in `tests/test_domain_detection.py`
- [X] T019 [P] [US1] Write failing non-forwarding authority config, endpoint collision, idempotency, and last-owner shutdown tests in `tests/test_domain_authority.py`
- [X] T020 [P] [US1] Write failing systemd-resolved route-only, resolv.conf-symlink-preservation, reload, and rollback tests in `tests/test_domain_resolved.py`
- [X] T021 [P] [US1] Write failing NetworkManager, direct dnsmasq, and exact-name hosts scoped-extension, validation, ownership, and rollback tests in `tests/test_domain_linux_adapters.py`
- [X] T022 [P] [US1] Write failing macOS resolver-file scoped ownership, validation, and rollback tests in `tests/test_domain_macos_adapter.py`
- [X] T023 [P] [US1] Write failing Herd/Valet integration, WSL2 detect-only, and unsupported-manager zero-mutation tests in `tests/test_domain_incumbent_adapters.py`
- [X] T024 [US1] Write failing service sequence, consent, fresh-answer, ingress-handshake, and fallback integration tests in `tests/test_domain_service.py`

### Implementation for User Story 1

- [X] T025 [US1] Implement bounded read-only resolver ownership and current-answer detection in `sandbox/network/detection.py`
- [X] T026 [US1] Implement Sandbox-owned non-forwarding dnsmasq configuration, supervision, health, and reference counting in `sandbox/network/authority.py`
- [X] T027 [US1] Implement systemd-resolved scoped routing without global resolver replacement in `sandbox/network/adapters/resolved.py`
- [X] T028 [US1] Implement NetworkManager, existing dnsmasq, and attributable exact-name hosts adapters in `sandbox/network/adapters/networkmanager.py`, `sandbox/network/adapters/dnsmasq.py`, and `sandbox/network/adapters/hosts.py`
- [X] T029 [US1] Implement macOS `/etc/resolver` owned-fragment adapter in `sandbox/network/adapters/macos.py`
- [X] T030 [US1] Implement Herd/Valet scoped integration plus detect-only external-manager results in `sandbox/network/adapters/incumbent.py` and `sandbox/network/adapters/external.py`
- [X] T031 [US1] Implement fresh uncached DNS verification followed by ingress HTTP verification in `sandbox/network/verification.py`
- [X] T032 [US1] Implement plan/apply/status sequencing, TTY-only consent, rollback, and per-port fallback in `sandbox/application/domain_service.py`
- [ ] T033 [US1] Delegate legacy domain entry points to the application service while retaining rollback compatibility in `sandbox/core/_domains.py`
- [ ] T034 [US1] Run live systemd-resolved exact-name adoption and capture ownership, unrelated-answer, fresh-lookup, HTTP, rollback, and cleanup evidence in `specs/038-tld-dns-adoption/evidence/systemd-resolved.md`

**Checkpoint**: Exact local names work on live-proven managers; unsupported paths remain
usable through their per-port URLs.

---

## Phase 4: User Story 2 - Preserve Safe Project Identity (Priority: P1)

**Goal**: Give only new omitted identities `.test`, preserve persisted `.tst` and explicit
names exactly, reject `.local`, and never silently rename an incompatible project.

**Independent Test**: Exercise new omitted, persisted `.tst`, explicit `.test`, `.local`,
public FQDN, conflicting pin, and incompatible-ingress fixtures through ensure and apply.

### Tests for User Story 2

- [X] T035 [P] [US2] Write failing new-default, persisted-legacy, explicit-name, `.local`, and public-FQDN identity tests in `tests/test_hostname_intent.py`
- [X] T036 [P] [US2] Write failing machine-override/project-pin precedence and source-reporting tests in `tests/test_domain_pins.py`
- [X] T037 [US2] Write failing incompatible identity preservation and per-port fallback lifecycle tests in `tests/test_domain_identity_lifecycle.py`

### Implementation for User Story 2

- [X] T038 [US2] Implement standards-safe hostname classification, `.test` defaulting, legacy preservation, and `.local` rejection in `sandbox/config/domains.py`
- [X] T039 [US2] Implement public-answer consumption without local override and explicit-name preservation in `sandbox/application/domain_service.py`
- [X] T040 [US2] Persist selected identity/provenance without retroactively changing existing registry entries in `sandbox/application/instance_service.py`
- [X] T041 [US2] Add regression fixtures for WordPress absolute URLs and generic Compose hostnames across re-ensure in `tests/test_domain_identity_lifecycle.py`

**Checkpoint**: Identity migrations cannot occur implicitly.

---

## Phase 5: User Story 3 - Diagnose, Reconcile, and Clean Up Safely (Priority: P2)

**Goal**: Measure current DNS behavior, preserve foreign/drifted state, and retain retryable
cleanup identity before an instance record disappears.

**Independent Test**: Replace the resolver owner, modify an owned rule, stop its authority,
then run read-only status and repeated destroy/cleanup while comparing all foreign state.

### Tests for User Story 3

- [X] T042 [P] [US3] Write failing resolver-change, address-mismatch, authority-down, cache-stale, and pin-source status tests in `tests/test_domain_status.py`
- [X] T043 [P] [US3] Write failing drift, unreachable-manager, changed-rule, repeated-cleanup, and retained-residual tests in `tests/test_domain_cleanup.py`
- [X] T044 [US3] Write failing registry/local deletion-order and post-instance recovery tests in `tests/test_domain_destroy_ordering.py`

### Implementation for User Story 3

- [X] T045 [US3] Implement observed-versus-desired status and actionable reconciliation results in `sandbox/application/domain_service.py`
- [X] T046 [US3] Implement compare-before-remove cleanup and durable non-secret residual records in `sandbox/network/repository.py`
- [X] T047 [US3] Persist DNS cleanup outcome before registry/local identity deletion and expose independent retry in `sandbox/commands/instances_cmd.py`
- [ ] T048 [US3] Implement `domains detect|plan|apply|status|cleanup|reconsider` JSON/text contracts in `sandbox/commands/domains.py`
- [ ] T049 [US3] Implement import-safe MCP domain status/plan/apply/cleanup tools and setup-result delegation in `mcp/wp-server/tools/domains.py` and `mcp/wp-server/tools/instances.py`
- [ ] T050 [US3] Run live owner-change, drift, unreachable, normal, and repeated cleanup scenarios and capture evidence in `specs/038-tld-dns-adoption/evidence/cleanup.md`

**Checkpoint**: Destroy never claims success after unreachable or drifted DNS cleanup.

---

## Phase 6: User Story 4 - Support Wildcard-Dependent Sites Without Overreach (Priority: P2)

**Goal**: Provide reference-counted local wildcard zones only when explicitly required and
safe, and reject exact-only or publicly delegated wildcard requests.

**Independent Test**: Resolve an unseen multisite subdomain, remove one then the last zone
consumer, and compare public/foreign namespace behavior throughout.

### Tests for User Story 4

- [ ] T051 [P] [US4] Write failing exact-preference, wildcard-capability, local-suffix, public-delegation, and foreign-zone tests in `tests/test_domain_wildcards.py`
- [ ] T052 [US4] Write failing shared-zone reference-count and last-owner cleanup tests in `tests/test_domain_wildcard_lifecycle.py`

### Implementation for User Story 4

- [ ] T053 [US4] Implement safe local-zone classification, exact-first planning, and wildcard capability refusal in `sandbox/application/domain_service.py`
- [ ] T054 [US4] Implement wildcard-zone authority records with attributable shared owners in `sandbox/network/authority.py`
- [ ] T055 [US4] Run live unseen-subdomain, shared-owner, final-owner, and public-refusal proof and capture evidence in `specs/038-tld-dns-adoption/evidence/wildcards.md`

**Checkpoint**: Multisite receives truthful wildcard readiness without broad shadowing.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, parity, architecture guards, regressions, and release evidence.

- [ ] T056 [P] Update hostname defaults, resolver pins, consent, status, fallback, and cleanup guidance in `README.md` and `docs/sandbox-config-reference.md`
- [ ] T057 [P] Document resolver threat boundaries, support/proof tiers, platform recovery, and `.tst` compatibility in `docs/domain-resolution.md` and `docs/cross-platform-support.md`
- [ ] T058 Add static guards against resolver-state JSON consumers and unregistered host mutations in `tests/test_architecture_boundaries.py`
- [ ] T059 Run unit/contract/integration suites, verify 2-second read-only and 30-second mutation bounds, and run `git diff --check`, fixing regressions in `tests/`
- [ ] T060 Run `specs/038-tld-dns-adoption/quickstart.md` end to end through `./sb` and complete the evidence index in `specs/038-tld-dns-adoption/evidence/README.md`
- [ ] T061 Verify existing persisted `.tst` and Compose per-port live parity and capture it in `specs/038-tld-dns-adoption/evidence/compatibility.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup has no dependencies.
- Foundational depends on Setup and blocks every user story.
- US1 and US2 depend on Foundational; US1 consumes only explicit or persisted intent already
  validated by T006, while US2 completes omitted/default and incompatibility lifecycle cases.
- US3 depends on US1's ownership repository and live operations.
- US4 depends on US1 authority lifecycle and US2 namespace classification.
- Polish depends on all delivered stories.

### User Story Completion Order

```text
Foundation → US1 explicit/persisted exact resolution → US3 recovery
          └→ US2 safe default/incompatible identity ─→ US4 wildcard → Polish
```

### Parallel Opportunities

- T002, T008–T011, and independent adapter tests marked `[P]` can proceed independently.
- Linux, macOS, and incumbent adapters touch separate modules after the registry contract.
- Documentation T056–T057 may proceed after contracts stabilize.

## Parallel Example: User Story 1

```text
T018 detection tests
T019 authority tests
T020 resolved tests
T021 Linux adapter tests
T022 macOS adapter tests
T023 incumbent adapter tests
```

## Implementation Strategy

### Safe Exact-Name MVP

1. Complete Setup and Foundational.
2. Complete US1 for explicit/persisted config-validated names on the current
   systemd-resolved host and advertise only that proven path.
3. Complete US2 before enabling automatic clean naming for an omitted identity.
4. Keep all other adapters unadvertised until their own live evidence exists.

### Incremental Delivery

1. Land provenance and read-only detection with unchanged per-port behavior.
2. Land exact-name resolution and conservative recovery.
3. Add wildcard zones only after exact-name lifecycle proof.

## Notes

- `[P]` tasks touch independent files or fixtures and may run concurrently.
- Every test task must fail for the intended missing behavior before implementation.
- All runtime-touching validation uses `./sb`; no raw resolver or process commands in user workflows.
- Verified logical groups are committed and pushed on the active non-`main` branch.
