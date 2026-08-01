# Feature Specification: Native Runtime Adoption

**Feature Branch**: `latest`

**Created**: 2026-08-01

**Status**: Draft

**Input**: Ready product requirements from `specs/039-native-runtime-adoption/prd.md`

## Clarifications

### Session 2026-08-01

- Q: Must managed-native add a bespoke kernel socket-count controller beyond Docker-class resource controls? → A: Standard Docker-class controls. Descriptor ceilings include sockets, while service and firewall connection ceilings independently bound network use; all must be proved effective or startup fails closed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a Docker-Like Isolated Managed Native Instance (Priority: P1)

A developer explicitly selects a Sandbox-managed native stack and receives a WordPress
instance whose web PHP, cron, CLI, arbitrary execution, dependency scripts, and tests all
run inside the same fail-closed boundary. Untrusted code cannot reach the host or sibling
instances outside declared capabilities.

**Why this priority**: Isolation is the user's primary requirement; a native process with
separate ports but developer-account access is not an acceptable substitute for Docker.

**Independent Test**: Provision one managed-native instance and execute the same hostile
probe through every untrusted execution path, testing filesystem, process, IPC, device,
socket, network, secret, privilege, descriptor, and resource boundaries.

**Acceptance Scenarios**:

1. **Given** a supported host and complete isolation prerequisites, **When** a managed-
   native instance starts, **Then** all untrusted execution paths see only declared runtime
   dependencies, instance files, read-only source mounts, explicitly writable paths, and
   granted capabilities.
2. **Given** a hostile plugin or command, **When** it attempts to read or write host/sibling
   files, enumerate or signal host processes, reach host devices/control sockets, or use
   undeclared networks, **Then** every attempt is denied by the operating-system boundary.
3. **Given** a required isolation prerequisite is missing or weakened, **When** start or an
   untrusted command is requested, **Then** the operation fails closed before project code
   runs and recommends Docker.
4. **Given** one instance exhausts a declared resource, **When** a sibling is exercised,
   **Then** the sibling and host remain healthy and outside the first instance's visibility.

---

### User Story 2 - Install and Operate an Isolated Native Stack Beside System Services (Priority: P1)

A developer without the required binaries can preview and confirm a trusted package
transaction, then run Sandbox-owned nginx or Apache, PHP-FPM, and MariaDB/MySQL service
state without taking over default system services.

**Why this priority**: A first-party native mode must be complete while keeping package
installation and shared-service side effects explicit and reversible.

**Independent Test**: On the initial advertised host matrix with unrelated services
already occupying default ports, preview installation, confirm it interactively, provision,
re-ensure, stop/start, destroy, and compare all unrelated services and configuration.

**Acceptance Scenarios**:

1. **Given** missing supported packages, **When** the developer previews installation,
   **Then** Sandbox shows package source, versions, transaction actions, owned path roots,
   privilege changes, and known system-service effects before confirmation.
2. **Given** current interactive confirmation, **When** installation proceeds, **Then**
   only approved trusted packages are installed and no unapproved repository, installer,
   build, or version substitution occurs.
3. **Given** unrelated web/database services are running, **When** managed-native starts,
   **Then** each Sandbox instance uses separate processes, endpoints, configuration, logs,
   secrets, and database data and every unrelated service remains healthy.
4. **Given** no terminal or an unavailable required version, **When** installation is
   requested, **Then** no package mutation starts and a bounded actionable result is
   returned.

---

### User Story 3 - Use an Incumbent Native Runtime Honestly (Priority: P2)

A developer may explicitly choose Herd, official Valet, or a declared POSIX stack for
trusted project code, while Sandbox clearly reports that this shared-host mode does not
provide managed-native hostile-code containment.

**Why this priority**: Existing Herd behavior must remain compatible, and users should be
able to reuse trusted native tools without confusing convenience with security isolation.

**Independent Test**: Preflight and provision Herd and Valet instances, verify backend,
PHP, database, CLI, and tests, and inspect isolation/support labels before and after.

**Acceptance Scenarios**:

1. **Given** an explicitly selected supported incumbent and user-supplied database,
   **When** preflight runs, **Then** Sandbox reports required/optional capabilities and the
   trusted-project/lower-isolation level before mutation.
2. **Given** successful C backend provisioning, **When** URL setup follows, **Then** C
   supplies runtime state only, B owns hostname/resolution, and A alone registers the
   Herd/Valet route and TLS state.
3. **Given** web, CLI, exec, and test PHP versions differ from the project pin, **When**
   readiness is evaluated, **Then** the instance is not declared ready and no silent global
   PHP fallback occurs.
4. **Given** a detected but unsupported incumbent, **When** it is selected, **Then**
   Sandbox reports detect-only/outside-platform status and does not edit private state.

---

### User Story 4 - Use One Capability-Aware Tooling Lifecycle (Priority: P2)

A tooling caller can preflight, ensure, inspect, open, execute WordPress commands, run
tests, reconcile, and destroy through one operation model while optional native gaps are
returned explicitly before side effects.

**Why this priority**: Native adoption is maintainable only if callers stop embedding
product-specific branches and can trust capability checks.

**Independent Test**: Run the required operation suite against Compose, managed-native,
Herd, and Valet; request each unsupported optional operation and compare result shape and
side effects.

**Acceptance Scenarios**:

1. **Given** an adoptable native runtime, **When** capability discovery runs, **Then** every
   required and optional operation has an explicit supported/unsupported state.
2. **Given** an optional operation is unsupported, **When** a caller requests it, **Then**
   Sandbox returns a structured limitation and safe alternative without global mutation.
3. **Given** an existing populated instance and a different runtime selection, **When**
   ensure runs, **Then** Sandbox refuses implicit switching and preserves all data.
4. **Given** a project with no native opt-in, **When** ensure runs, **Then** Compose remains
   the unchanged default.

---

### User Story 5 - Destroy Only Owned Native State (Priority: P2)

A host owner can destroy or recover a native instance without deleting pre-existing sites,
databases, services, packages, or externally changed state.

**Why this priority**: Native runtimes share the host and credentials, so ownership errors
have a larger blast radius than an isolated container volume.

**Independent Test**: Exercise foreign collisions, externally drifted owned state,
unavailable runtimes, normal destroy, and repeated destroy while comparing host state.

**Acceptance Scenarios**:

1. **Given** a foreign directory, database, or runtime identity collision, **When** ensure
   runs, **Then** C refuses before partial provisioning and leaves it unchanged.
2. **Given** unchanged C-owned files, databases, process state, and metadata, **When**
   destroy is confirmed, **Then** only those objects are removed and A independently owns
   route cleanup.
3. **Given** externally changed or unavailable native state, **When** destroy runs, **Then**
   Sandbox preserves ambiguous state and returns incomplete cleanup with retry guidance.
4. **Given** shared binaries installed for managed-native, **When** the final instance is
   destroyed, **Then** packages remain installed unless a separate reviewed unused-package
   cleanup is explicitly requested.

### Edge Cases

- An inherited descriptor already references a host file or control socket before the
  sandbox starts; untrusted execution does not inherit usable access.
- A declared source mount contains symlinks escaping its root; resolution cannot expose
  targets outside the declared boundary.
- A writable subpath is nested below a read-only checkout; only that exact subtree is
  writable and renames/links cannot escape it.
- An egress grant is present; external access works only within its declared scope and does
  not expose host loopback, private, sibling, or control networks.
- Disk bytes remain below quota but inode count is exhausted, or descriptor/service-
  connection ceilings are exhausted before memory/CPU; each effective limit contains the
  failure.
- Package installation would start a default service or alter a conflicting system file;
  the initial matrix must prove suppression/preservation or decline before transaction.
- A managed service binary is upgraded by the package manager while instances exist;
  preflight detects effective-version/policy drift before restart.
- A native runtime disappears or database credentials change; status turns unhealthy but
  preserves C-owned data.
- A remote or CI caller selects native mode; remote deployment remains Compose-only and no
  interactive package or host-runtime action starts.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose exactly three explicit local runtime modes: Compose,
  adopted incumbent native, and Sandbox-managed native.
- **FR-002**: Compose MUST remain the default for unpinned projects, CI, and remote
  deployment.
- **FR-003**: Native mode MUST require an explicit machine-local project selection and MUST
  never be selected from runtime detection alone.
- **FR-004**: The system MUST report incumbent native modes as trusted-project/lower-
  isolation and MUST NOT equate them with managed-native or Docker containment.
- **FR-005**: Managed-native MUST fail closed unless the host can enforce and prove the
  complete declared isolation boundary.
- **FR-006**: Web PHP, cron, WordPress CLI, arbitrary exec, dependency scripts, and tests
  MUST all execute inside the same managed-native instance policy.
- **FR-007**: The trusted host control plane MUST NOT load or execute plugin/project PHP
  outside the instance boundary.
- **FR-008**: Managed-native MUST isolate filesystem visibility, users/privileges,
  processes, IPC, network, devices, control sockets, secrets, and sibling instances.
- **FR-009**: Runtime dependencies MAY be shared read-only; host home/state and sibling
  state MUST be absent from the instance view.
- **FR-010**: Project source mounts MUST be read-only by default; writable subpaths MUST
  require explicit declaration and MUST NOT expose parents or symlink targets outside the
  boundary.
- **FR-011**: External network access MUST be denied by default and MUST require an
  explicit, scoped, observable, revocable egress capability.
- **FR-012**: An egress grant MUST NOT expose host loopback, private control surfaces, or
  sibling instance networks.
- **FR-013**: Each managed-native instance MUST have separate process, filesystem, IPC,
  network, secret, endpoint, database, and writable-data boundaries.
- **FR-014**: Each managed-native instance MUST enforce ceilings for CPU, memory, process
  count, execution time, disk bytes, inode count, open descriptors including sockets,
  service/network connection counts, and I/O using standard Docker-class operating-system
  controls.
- **FR-015**: The sandbox MUST close or reject inherited descriptors that could expose a
  host or sibling resource before untrusted execution.
- **FR-016**: Isolation prerequisites and effective policy MUST be checked before every
  instance start and untrusted command; failure MUST NOT downgrade to host execution.
- **FR-017**: Managed-native MUST support nginx or Apache, PHP-FPM/CLI, and MariaDB/MySQL as
  separately owned per-instance service state while sharing only approved binaries.
- **FR-018**: The first advertised managed-native matrix MUST be Ubuntu 24.04 with its
  configured package sources, PHP 8.3, bubblewrap 0.9, MariaDB 10.11, and nginx 1.24 or
  Apache HTTP Server 2.4.
- **FR-019**: Any additional platform/version combination MUST remain unadvertised until it
  has an explicit matrix entry and full installation, coexistence, isolation, hostile-code,
  lifecycle, and WordPress proof.
- **FR-020**: Package installation MUST prefer already installed compatible binaries, then
  configured trusted system sources or explicitly approved official distributions with
  integrity verification.
- **FR-021**: Package installation MUST NOT add a repository, execute a remote installer,
  compile source, or substitute a pinned version without explicit separate approval.
- **FR-022**: Before installation, the system MUST present packages/versions, transaction
  actions, declared owned path roots, privilege changes, and known maintainer-script or
  system-service effects.
- **FR-023**: Package installation MUST require current interactive confirmation and MUST
  never prompt or begin from MCP, CI, or another non-interactive caller.
- **FR-024**: Managed-native MUST use owned configurations, processes, endpoints, sockets,
  PID state, logs, secrets, and database data without enabling, stopping, or rewriting the
  host's default service instances.
- **FR-025**: Destroy MUST NOT automatically uninstall shared packages; unused-package
  cleanup MUST be separate and reviewed.
- **FR-026**: Every adoptable native runtime MUST support preflight, ensure, status/health,
  open, WordPress CLI, bounded exec, filesystem/log access, isolated production/test
  databases, tests, apply/reconcile, and conservative destroy.
- **FR-027**: Optional operations—including per-instance stop, aggregated logs, snapshots,
  mail capture, per-instance debugging, subdomain multisite, server switching, and remote
  deployment—MUST be declared individually.
- **FR-028**: An unsupported optional operation MUST return a structured limitation before
  side effects and SHOULD identify a safe alternative when one exists.
- **FR-029**: Requested PHP major/minor MUST agree across web, CLI, exec, and tests; missing
  or mismatched versions MUST fail readiness without global fallback.
- **FR-030**: C MUST own backend runtime files, endpoint requirements, PHP, database,
  execution, capability, and health state only.
- **FR-031**: B MUST own hostname/TLD selection and resolution; A MUST exclusively own
  hostname-route registration/removal, including Herd/Valet link/proxy/TLS actions.
- **FR-032**: C MUST NOT create, modify, or remove a hostname route.
- **FR-033**: Foreign runtime identity, directory, database, process, or configuration
  collisions MUST be refused before partial provisioning.
- **FR-034**: Names or expected paths alone MUST NOT prove native-state ownership; observed
  state MUST match an attributable last-applied record.
- **FR-035**: C MUST modify or remove only unchanged state it owns and MUST preserve drifted
  or ambiguous state for reconciliation.
- **FR-036**: Unavailable or failed cleanup MUST return incomplete status with minimal non-
  secret retry state.
- **FR-037**: Changing runtime mode for an existing populated instance MUST be refused
  until a separate explicit export/recreate/import workflow is performed.
- **FR-038**: Ensure, apply, start, stop where supported, status, and destroy MUST be
  idempotent.
- **FR-039**: Secrets and machine paths MUST remain in gitignored machine-local state and
  MUST be absent from output, logs, recovery records, and tracked files.
- **FR-040**: Each runtime advertised as adoptable MUST have captured live evidence for the
  entire required operation set and its declared isolation level.

### Key Entities

- **Runtime Selection**: Mode, adapter identity, machine-local source, support tier,
  isolation level, requested versions, and effective capabilities.
- **Runtime Capability Declaration**: Required/optional operation support, prerequisites,
  limitation reason, and live-proof status.
- **Managed Isolation Policy**: Visible read-only/writable roots, privileges, process/IPC/
  network rules, egress grants, resource ceilings, descriptor policy, and effective proof.
- **Package Transaction Plan**: Trusted source, packages/versions, actions, privilege and
  maintainer effects, declared owned roots, confirmation, and result.
- **Native Backend Record**: Instance runtime endpoint requirements, PHP/database state,
  processes, files, secrets references, health, last applied state, and drift.
- **Database Boundary**: Instance production/test databases or dedicated server state,
  socket/credentials scope, ownership, health, and cleanup state.
- **Cleanup Recovery**: Ambiguous or unavailable C-owned state, expected prior value,
  failure reason, and retry status without secrets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On every advertised managed-native matrix, 100% of hostile probes run through
  web, cron, CLI, exec, dependency scripts, and tests fail to access undeclared host or
  sibling filesystem, process, IPC, device, socket, network, secret, or control resources.
- **SC-002**: Disabling any mandatory isolation prerequisite or exposing an unexpected
  resource causes 100% of starts and untrusted commands to fail before project code runs.
- **SC-003**: CPU, memory, process, time, disk-byte, inode, descriptor, service/network-
  connection, and I/O exhaustion probes remain within their instance and leave the host
  and sibling health checks successful.
- **SC-004**: On the initial matrix, both nginx and Apache variants complete preview →
  install where needed → provision → live request → CLI → test → re-ensure → destroy
  without starting a WordPress container.
- **SC-005**: With unrelated services on default endpoints, 100% of sampled foreign PIDs,
  configurations, data, ports, and health results remain unchanged across the managed-
  native lifecycle.
- **SC-006**: Non-interactive and unavailable-version installation cases perform zero
  package mutations and return within normal command bounds.
- **SC-007**: Web, CLI, exec, and tests report the requested PHP major/minor in 100% of
  ready native instances; mismatches have a 0% ready-state rate.
- **SC-008**: All foreign-collision and drift tests preserve foreign/changed state byte-for-
  byte and produce an actionable refusal or incomplete-cleanup result.
- **SC-009**: Required/optional capability discovery is available before mutation for 100%
  of advertised runtimes; unsupported optional requests cause zero global changes.
- **SC-010**: Repeating ensure/apply/destroy produces no duplicate runtime, database,
  process, or metadata state and converges to the same final result.
- **SC-011**: Herd, Valet, and declared shared-host profiles display the trusted-project/
  lower-isolation label in every preflight and status result; managed-native never silently
  downgrades to them.
- **SC-012**: C creates zero hostname-route mutations in integration tests; all such
  mutations and cleanup are attributable to A after B supplies verified naming state.

## Assumptions

- Managed-native treats plugin and CLI input as hostile; incumbent native modes are only
  for trusted project code and are labeled accordingly.
- The initial matrix remains intentionally narrow until the complete security and live-
  stack proof is repeated for another host/version combination.
- Package binaries may be shared, but instance state and untrusted execution may not.
- Docker remains available as the recommended fallback when native isolation or versions
  cannot be proved.
- Runtime switching and foreign-site import remain separate explicit workflows.
