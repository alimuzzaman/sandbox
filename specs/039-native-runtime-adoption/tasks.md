# Tasks: Native Runtime Adoption

**Input**: Design documents from `specs/039-native-runtime-adoption/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`,
`quickstart.md`

**Tests**: Contract, integration, hostile-probe, and live-stack tasks are required by the
specification and constitution. Test tasks precede their implementation tasks.

**Organization**: Tasks are grouped by user story and keep Compose compatibility available
throughout the migration.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish explicit files/manifests without changing runtime behavior.

- [X] T001 Create runtime/isolation package skeletons and public exports in `sandbox/runtimes/incumbent/__init__.py`, `sandbox/runtimes/managed/__init__.py`, and `sandbox/isolation/__init__.py`
- [X] T002 [P] Add managed-native hostile probe fixtures for PHP, shell, plugin activation, Composer, and PHPUnit in `tests/hostile/`
- [X] T003 [P] Add Ubuntu 24.04 managed-native matrix fixture with exact expected capability fields in `tests/fixtures/native/ubuntu-24.04.json`
- [X] T004 Register the feature-owned `native` command module and parser contract in `sandbox/commands/manifest.py` and `sandbox/commands/native.py`
- [X] T005 Update static CLI/MCP/modularity inventory expectations for the new registered surfaces in `tests/test_command_composition.py`, `tests/test_mcp_composition.py`, and `tests/test_modularity.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the typed selection, adapter, policy, repository, and helper boundaries
required before any native story can mutate state.

- [X] T006 Write failing provenance, default-Compose, explicit-native, unknown-key, and runtime-switch schema tests in `tests/test_wordpress_runtime_config.py`
- [X] T007 Implement `wordpressRuntime` normalization and machine-override provenance without overloading job `runtime` config in `sandbox/config/wordpress_runtime.py` and register it in `sandbox/config/manifest.py`
- [X] T008 Write failing two-dimensional project-kind/backend selection and duplicate-adapter registration tests in `tests/test_native_runtime_service.py`
- [X] T009 Extend runtime models/registry/service to resolve `(project kind, local mode, adapter id)` and structured capability results in `sandbox/runtimes/base.py`, `sandbox/runtimes/registry.py`, and `sandbox/application/runtime_service.py`
- [X] T010 [P] Write failing validation/state-transition/secret-redaction tests for RuntimeSelection, ManagedIsolationPolicy, PackageTransactionPlan, NativeBackendRecord, EgressGrant, and CleanupRecovery in `tests/test_native_models.py`
- [X] T011 [P] Implement immutable native/isolation models and canonical digest validation in `sandbox/isolation/models.py` and `sandbox/runtimes/managed/models.py`
- [X] T012 [P] Write failing migration, locking, compare-before-change, and residual-survival tests in `tests/test_native_ownership.py`
- [X] T013 Implement the versioned locked native state repository and ownership comparison in `sandbox/runtimes/managed/repository.py`
- [X] T014 Define and register the exact Ubuntu/incumbent support and proof manifest in `sandbox/isolation/manifest.py` and `sandbox/runtimes/manifest.py`
- [X] T015 Write failing helper argument/path/symlink/race/non-owner tests in `tests/test_native_helper.py`
- [X] T016 Write the fixed-verb root-helper schema, path/ID validation, policy-digest verification, and install-copy logic in `tools/native-helper/native-helper.py` and `tools/native-helper/VERSION`
- [X] T017 Compose the runtime service with injected process/http/path/registry/isolation/package/network/database dependencies in `sandbox/application/context.py`

**Checkpoint**: Compose still behaves unchanged; native requests can be validated and
rejected before side effects through typed contracts.

---

## Phase 3: User Story 1 - Docker-Like Isolated Managed Native Instance (Priority: P1)

**Goal**: Start one managed-native instance only when every boundary is effective and route
all hostile execution paths through the same fail-closed policy.

**Independent Test**: Provision one Ubuntu instance and run the hostile fixture through
web, cron, WP-CLI/eval, exec, Composer, activation hooks, durable local job, and PHPUnit;
every undeclared host/sibling access fails and disabling any gate prevents payload start.

### Tests for User Story 1

- [X] T018 [P] [US1] Write failing OS/kernel/systemd/cgroup/nspawn/bubblewrap/nftables/LSM effective-preflight tests in `tests/test_isolation_preflight.py`
- [X] T019 [P] [US1] Write failing mount visibility, read-only source, writable-subpath, and symlink-escape tests in `tests/test_isolation_policy.py`
- [X] T020 [P] [US1] Write failing private UID/PID/IPC/UTS/device/capability/seccomp/nested-userns tests in `tests/test_isolation_namespaces.py`
- [X] T021 [P] [US1] Write failing veth default-deny, host/sibling/private/metadata denial, ingress-reply, grant, and revoke tests in `tests/test_isolation_network.py`
- [X] T022 [P] [US1] Write failing CPU/memory/PID/time/disk/inode/FD/connection/I/O exhaustion tests in `tests/test_isolation_resources.py`
- [X] T023 [P] [US1] Write failing inherited-FD/environment/credential/control-socket leakage tests in `tests/test_isolation_credentials.py`
- [X] T024 [US1] Write failing all-entry-path policy-digest and no-host-fallback integration tests in `tests/test_isolation_execution_paths.py`

### Implementation for User Story 1

- [X] T025 [US1] Implement effective host prerequisite, observed-policy verification, and policy-drift preflight with fail-closed results in `sandbox/isolation/preflight.py` and `sandbox/isolation/verification.py`
- [X] T026 [US1] Implement fixed-size/inode ext4 image creation, ownership verification, mount options, and conservative unmount in `sandbox/runtimes/managed/image.py`
- [X] T027 [US1] Implement private-user nspawn descriptors, namespace/device/capability/seccomp settings, and observed-policy verification in `sandbox/isolation/nspawn.py`
- [X] T028 [US1] Implement read-only/writable mount compilation, canonical path and symlink checks in `sandbox/isolation/policy.py`
- [X] T029 [US1] Implement unique point-to-point veth allocation, no-default-route nftables policy, ingress allowance, scoped egress grants, counters, and revocation in `sandbox/isolation/network.py`
- [X] T030 [US1] Implement cgroup/service/disk/FD/connection/time/I/O limits and effective observation in `sandbox/isolation/resources.py`
- [X] T031 [US1] Implement per-instance credential injection, environment allowlist, close-range descriptor sanitation, and leak gate in `sandbox/isolation/credentials.py`
- [X] T032 [US1] Implement the defense-in-depth one-shot bubblewrap profile with clearenv, source modes, nested-userns disable, capability drop, private temp, and bounded argv in `sandbox/isolation/bubblewrap.py`
- [X] T033 [US1] Implement `IsolationLauncher` for web PHP, cron, WP-CLI/eval, exec, Composer, activation, PHPUnit, and durable jobs with no host fallback in `sandbox/isolation/launcher.py`
- [X] T034 [US1] Implement nginx/PHP-FPM/MariaDB/cron supervision and backend reporting inside the nspawn boundary in `sandbox/runtimes/managed/services.py` and `sandbox/runtimes/managed/database.py`
- [X] T035 [US1] Implement managed-native preflight/ensure/status/exec/test operations and policy-digest enforcement in `sandbox/runtimes/managed/adapter.py`
- [X] T036 [US1] Route current WordPress CLI/eval/exec/dependency/activation/test/job call sites through the runtime isolation gateway in `sandbox/core/_docker.py`, `sandbox/core/_provision.py`, `sandbox/core/_tests.py`, `sandbox/commands/runtime.py`, `sandbox/jobs/supervisor.py`, and `mcp/wp-server/tools/wp.py`
- [X] T037 [US1] Run the simulated and unprivileged isolation integration suite, verify the proof gate remains unadvertised before live rootfs installation, and capture results in `specs/039-native-runtime-adoption/evidence/pre-live-gate.md`

**Checkpoint**: The managed isolation boundary composes and fails closed under tests but
remains unadvertised until package/rootfs installation and the live hostile matrix pass;
no ingress/DNS mutation is owned by C.

---

## Phase 4: User Story 2 - Install and Operate Beside System Services (Priority: P1)

**Goal**: Preview/confirm trusted host and image packages, support nginx or Apache, and
leave unrelated host services/configuration unchanged.

**Independent Test**: With foreign web/database services on default endpoints, run preview,
non-TTY refusal, interactive install, nginx and Apache lifecycle, re-ensure, and destroy;
foreign baselines remain unchanged.

### Tests for User Story 2

- [X] T038 [P] [US2] Write failing APT-source/version/simulation/plan-digest/unavailable-version tests in `tests/test_managed_package_plan.py`
- [X] T039 [P] [US2] Write failing TTY confirmation, digest-drift, remote-script/PPA/source-build refusal, and noninteractive-zero-mutation tests in `tests/test_managed_package_apply.py`
- [X] T040 [P] [US2] Write failing host-service active/enabled/config/data coexistence tests in `tests/test_managed_coexistence.py`
- [X] T041 [P] [US2] Write failing Apache variant service/backend/PHP/database lifecycle tests in `tests/test_managed_apache.py`

### Implementation for User Story 2

- [X] T042 [US2] Implement exact configured-source host/image APT simulation, package closure, effects, owned roots, and digest output in `sandbox/runtimes/managed/packages.py`
- [X] T043 [US2] Implement TTY-only confirmed package apply with re-simulation, image-local service-start suppression, and host baseline verification in `sandbox/runtimes/managed/packages.py` and `tools/native-helper/native-helper.py`
- [X] T044 [US2] Implement Noble rootfs bootstrap and exact PHP 8.3/MariaDB 10.11/nginx-or-Apache image configuration in `sandbox/runtimes/managed/image.py`
- [X] T045 [US2] Implement Apache 2.4 managed service configuration and veth backend parity in `sandbox/runtimes/managed/services.py`
- [X] T046 [US2] Implement `native support|preflight|install-plan|install` CLI JSON/text contracts in `sandbox/commands/native.py`
- [ ] T047 [US2] Run live install, full hostile/exhaustion, and lifecycle proof for nginx and Apache with foreign host services and capture evidence in `specs/039-native-runtime-adoption/evidence/ubuntu-nginx.md`, `specs/039-native-runtime-adoption/evidence/ubuntu-package-coexistence.md`, and `specs/039-native-runtime-adoption/evidence/ubuntu-apache.md`

**Checkpoint**: Both managed web variants install and operate without starting a WordPress
container or altering host service baselines.

---

## Phase 5: User Story 3 - Use an Incumbent Native Runtime Honestly (Priority: P2)

**Goal**: Adopt Herd, official Valet, or declared POSIX profiles for trusted code with
truthful lower-isolation and no C-owned hostname route.

**Independent Test**: Preflight/ensure/web/CLI/test/destroy each advertised incumbent,
verify PHP agreement and lower-isolation labels, and assert zero C route/TLS/DNS mutations.

### Tests for User Story 3

- [X] T048 [P] [US3] Write failing Herd capability/version/database/backend/no-route tests in `tests/test_incumbent_herd.py`
- [X] T049 [P] [US3] Write failing official-Valet macOS platform/capability/version/no-route tests in `tests/test_incumbent_valet.py`
- [X] T050 [P] [US3] Write failing declared-POSIX authority/collision/lower-isolation tests in `tests/test_incumbent_posix.py`
- [X] T051 [P] [US3] Write failing Local/XAMPP/Laragon/WAMP detect-only/outside-platform tests in `tests/test_native_detection.py`

### Implementation for User Story 3

- [X] T052 [US3] Implement Herd runtime-only adapter and move link/secure/unlink ownership behind A compatibility handoff in `sandbox/runtimes/incumbent/herd.py` and `sandbox/core/_herd.py`
- [X] T053 [US3] Implement official macOS Valet runtime-only adapter with user-supplied database and version checks in `sandbox/runtimes/incumbent/valet.py`
- [X] T054 [US3] Implement declared POSIX profile validation, ownership, execution, database, and truthful limitation contract in `sandbox/runtimes/incumbent/posix.py`
- [X] T055 [US3] Implement detect-only/outside-platform declarations without private-state access in `sandbox/runtimes/manifest.py`
- [X] T056 [US3] Capture live Herd/Valet/POSIX evidence where hosts are available and keep unproven adapters non-adoptable in `specs/039-native-runtime-adoption/evidence/incumbents.md`

**Checkpoint**: Incumbents are useful but never confused with managed-container isolation.

---

## Phase 6: User Story 4 - One Capability-Aware Tooling Lifecycle (Priority: P2)

**Goal**: Expose one preflight/ensure/status/open/CLI/exec/test/apply/destroy result model and
reject optional capability gaps before side effects.

**Independent Test**: Run the required suite and unsupported optional requests against
Compose, managed-native, Herd, and Valet; compare envelopes and global side effects.

### Tests for User Story 4

- [X] T057 [P] [US4] Write failing required/optional capability envelope and safe-alternative tests in `tests/test_native_capabilities.py`
- [X] T058 [P] [US4] Write failing Compose-default/no-regression and populated-mode-switch refusal tests in `tests/test_native_mode_lifecycle.py`
- [X] T059 [P] [US4] Write failing CLI/MCP parity, noninteractive pending, and secret-redaction tests in `tests/test_native_cli_mcp.py`

### Implementation for User Story 4

- [X] T060 [US4] Complete adapter-neutral WordPress operation dispatch and optional-capability errors in `sandbox/runtimes/wordpress.py` and `sandbox/application/runtime_service.py`
- [X] T061 [US4] Route lifecycle, WP, test, debug, data, and instance callers through capability preflight in `sandbox/commands/lifecycle.py`, `sandbox/commands/wp.py`, `sandbox/commands/debug.py`, `sandbox/commands/data.py`, and `sandbox/commands/instances_cmd.py`
- [X] T062 [US4] Extend explicit runtime MCP dependencies/tools without `app.py` helper imports in `mcp/wp-server/tools/runtime.py`, `mcp/wp-server/tools/wp.py`, and `mcp/wp-server/tools/manifest.py`
- [X] T063 [US4] Add runtime/isolation/capability health to status and doctor output in `sandbox/commands/lifecycle.py` and `sandbox/core/_dash.py`
- [X] T064 [US4] Run the cross-adapter contract suite and record supported/unsupported operation parity in `specs/039-native-runtime-adoption/evidence/capability-parity.md`

**Checkpoint**: Callers no longer need product-specific native branches.

---

## Phase 7: User Story 5 - Destroy Only Owned Native State (Priority: P2)

**Goal**: Remove only unchanged C-owned state, retain drift/unavailable recovery, and leave
shared packages and A-owned routes alone.

**Independent Test**: Exercise foreign collision, changed owned state, missing runtime,
normal and repeated destroy; compare every path/database/image/unit/network/route/package.

### Tests for User Story 5

- [X] T065 [P] [US5] Write failing foreign path/image/machine/database/unit/network collision tests in `tests/test_native_destroy.py`
- [X] T066 [P] [US5] Write failing drift/unavailable/residual/retry/idempotent destroy tests in `tests/test_native_recovery.py`
- [X] T067 [P] [US5] Write failing A-route separation and shared-package preservation tests in `tests/test_native_cleanup_boundaries.py`

### Implementation for User Story 5

- [X] T068 [US5] Implement compare-before-remove cleanup ordering for services, databases, network, mounts, image, policy, and state in `sandbox/runtimes/managed/adapter.py`
- [X] T069 [US5] Implement incumbent owned-database/backend cleanup and drift preservation in `sandbox/runtimes/incumbent/herd.py`, `sandbox/runtimes/incumbent/valet.py`, and `sandbox/runtimes/incumbent/posix.py`
- [X] T070 [US5] Persist incomplete cleanup before registry/local identity removal and hand A route cleanup separately in `sandbox/commands/instances_cmd.py`
- [X] T071 [US5] Add recovery inspection/retry to `native status|cleanup` without package uninstall in `sandbox/commands/native.py`
- [ ] T072 [US5] Run live normal/repeated/drift/unavailable cleanup proof and capture evidence in `specs/039-native-runtime-adoption/evidence/cleanup.md`

**Checkpoint**: Native destroy is conservative, repeat-safe, and independently recoverable.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, boundary audits, full regressions, and release proof.

- [X] T073 [P] Update runtime selection, isolation guarantees/limits, package preview, capabilities, and examples in `README.md` and `docs/sandbox-config-reference.md`
- [X] T074 [P] Document Ubuntu matrix, threat model, incident/recovery, and live evidence requirements in `docs/native-runtime-isolation.md` and `docs/cross-platform-support.md`
- [X] T075 Audit production code for direct host execution of project/PHP argv and add a static boundary guard in `tests/test_architecture_boundaries.py`
- [X] T076 Run unit/contract/integration suites, verify 3-second preflight/status and 20-second warm-start bounds, and run `git diff --check`, fixing regressions in `tests/`
- [ ] T077 Run `specs/039-native-runtime-adoption/quickstart.md` end to end on a normally booted Ubuntu 24.04 host and complete the evidence index in `specs/039-native-runtime-adoption/evidence/README.md`
- [X] T078 Verify Compose live-stack parity with `./sb ensure`, `./sb status`, `./sb wp`, and `./sb test` from a real registered project and record it in `specs/039-native-runtime-adoption/evidence/compose-regression.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup has no dependencies.
- Foundational depends on Setup and blocks every user story.
- US1 depends on Foundational and supplies the isolation/runtime core.
- US2 depends on US1's policy/image/service boundary.
- US3 depends only on Foundational and may proceed alongside US1/US2.
- US4 depends on US1 and US3 adapters being callable.
- US5 depends on the implemented adapters from US1–US4.
- Polish depends on all delivered stories.

### User Story Completion Order

```text
Foundation → US1 managed isolation → US2 package/web variants ┐
           └→ US3 incumbents ────────────────────────────────┼→ US4 lifecycle → US5 cleanup → Polish
```

### Parallel Opportunities

- T002–T003, T010–T012, and story test files marked `[P]` are independent.
- US3 may proceed after Foundation while managed-native US1/US2 is developed.
- Documentation T073–T074 may proceed after contracts stabilize.

## Parallel Example: User Story 1

```text
T018 preflight tests
T019 mount tests
T020 namespace tests
T021 network tests
T022 resource tests
T023 credential/FD tests
```

## Implementation Strategy

### Isolation-First MVP

1. Complete Setup and Foundational.
2. Complete US1 as an unadvertised fail-closed isolation boundary.
3. Complete US2 package/rootfs work and the full live nginx/Apache hostile matrix.
4. Advertise managed-native only after every mandatory gate is live-proven.

### Incremental Delivery

1. Land typed selection/capabilities with unchanged Compose behavior.
2. Land managed-native behind an unadvertised proof gate.
3. Promote only after US1/US2 live evidence.
4. Migrate incumbents, common lifecycle, and cleanup without removing compatibility paths.

## Notes

- `[P]` tasks touch independent files or fixtures and may run concurrently.
- Every test task must fail for the intended missing behavior before implementation.
- All runtime-touching validation uses `./sb`; do not substitute raw Docker/systemd/nspawn/
  database commands in user workflows.
- Verified logical groups are committed and pushed on the active non-`main` branch.
