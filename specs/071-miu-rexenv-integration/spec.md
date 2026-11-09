# Feature Specification: Miu Management and RexEnv Integration

**Feature Branch**: `071-miu-rexenv-integration`

**Created**: 2026-11-08

**Status**: Draft

**Input**: Ready PRD: `specs/071-miu-rexenv-integration/prd.md` (authoritative)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate a WordPress project with RexEnv (Priority: P1)

A developer explicitly selects RexEnv for a trusted WordPress project and uses the familiar project lifecycle, execution, inspection, testing, and recovery workflow. Docker remains the default for existing and new projects unless the user selects RexEnv.

**Why this priority**: First-class RexEnv support is the core runtime outcome and must provide practical value across a complete project lifecycle.

**Independent Test**: On a qualified disposable host, select RexEnv for two projects and complete the supported core workflow for each while verifying project identity, URLs, isolation of effects, and recovery.

**Acceptance Scenarios**:

1. **Given** a compatible RexEnv installation and a registered project, **When** the user explicitly selects RexEnv and creates or reconciles its site, **Then** the project maps to exactly one identified site and reports its capabilities and health.
2. **Given** two RexEnv projects, **When** the user starts, stops, inspects, executes, tests, or removes one project, **Then** only that project's resources are targeted and the other remains usable.
3. **Given** a project with a saved baseline, **When** the user snapshots, changes both database and files, and restores the baseline, **Then** the declared database and file state is restored and the selected restore point remains available.
4. **Given** RexEnv is unavailable, incompatible, or unqualified for a requested operation, **When** the user requests that operation, **Then** Sandbox reports the limitation before effects and does not silently switch runtimes.

### User Story 2 - Manage the Miu service and dashboard (Priority: P1)

A host administrator uses Sandbox to inspect, install, configure, access, upgrade, monitor, and remove an explicitly managed Miu service while retaining its dashboard and run evidence.

**Why this priority**: Complete Miu service management is expressly requested and is a prerequisite for a usable managed QA workflow.

**Independent Test**: On a qualified disposable Linux host, inspect support, review and approve an installation, operate the dashboard and service, upgrade, and remove only the managed resources while preserving selected data.

**Acceptance Scenarios**:

1. **Given** an unconfigured or unknown host, **When** an administrator inspects support or requests an installation proposal, **Then** inspection has no side effects and the proposal names ownership, compatibility, affected resources, and required consent.
2. **Given** an approved installation proposal, **When** an administrator confirms it, **Then** the service and dashboard become available at qualified versions and repeated requests do not create duplicate installations.
3. **Given** a managed Miu installation, **When** an administrator inspects health, configuration, version, active runs, or retained evidence, **Then** Sandbox reports the observed state and unknown values remain explicitly unknown.
4. **Given** an upgrade or removal proposal, **When** the administrator reviews it, **Then** affected and preserved resources, active work, compatibility, recovery limits, and required consent are visible before action.
5. **Given** a failed or interrupted upgrade, **When** the administrator inspects the service, **Then** the previous usable state or a truthful recoverable/unknown outcome is reported without claiming success.

### User Story 3 - Connect a GitHub App and run authorized QA (Priority: P1)

A QA operator connects an explicitly selected GitHub App and repository grants, starts a QA run for a pull request, and reviews its result alongside retained evidence in the Miu dashboard.

**Why this priority**: Miu's QA workflow is the reason to manage the service; connection and run handling must preserve explicit authority and evidence.

**Independent Test**: With an approved disposable App and selected repository, submit one run, verify pinned base and PR revisions and evidence, then verify duplicate delivery and unauthorized writes do not cause extra or external actions.

**Acceptance Scenarios**:

1. **Given** an App creation or adoption proposal, **When** the operator reviews it, **Then** identity, callback origin, repository grants, and external write scope are shown and no App or grant changes occur before consent.
2. **Given** a human-approved App connection, **When** the operator connects it, **Then** the exact connected identity and selected grants are reported without exposing setup codes, tokens, or private key material.
3. **Given** an authorized repository and eligible pull request, **When** the operator requests a QA run, **Then** one accepted run is associated with pinned base and PR revisions, attributable sites, bounded retained evidence, and a visible verdict.
4. **Given** report-only defaults or absent external-write authorization, **When** a run completes, **Then** no comment, report publication, App registration, or grant expansion occurs.
5. **Given** an explicitly authorized external write, **When** Miu performs it, **Then** the action is attributable to the exact connected identity and actual replay guarantees are disclosed.

### User Story 4 - Recover interrupted and unsafe operations (Priority: P1)

A developer or operator can determine what happened after a lost reply, timeout, cancellation, refusal, or restart, and safely resolve owned work before retrying or cleaning up.

**Why this priority**: Both applications manage persistent host resources and potentially untrusted pull request code; ambiguity must not cause duplicate work or premature release of authority.

**Independent Test**: Interrupt representative site, service, and QA operations at controlled points; observe retained status; resolve the existing operation; and confirm no duplicate execution or premature cleanup.

**Acceptance Scenarios**:

1. **Given** a request whose reply was lost, **When** the user looks up its retained operation identity, **Then** the accepted operation's status and partial effects are returned before any retry is offered.
2. **Given** work may still be running after cancellation or timeout, **When** the user requests cleanup or a retry, **Then** sites, leases, and cleanup authority remain held until completion or safe resolution is established.
3. **Given** untrusted pull request code without suitable independent containment or an eligible prior trust classification, **When** a QA run is requested, **Then** the request is refused before project code executes and the missing evidence is identified.
4. **Given** stale grants, insufficient capacity, incompatible builds, or missing identity evidence, **When** a run is requested, **Then** it is refused without using ambient credentials or launching fallback work.

### User Story 5 - Coexist safely with existing host services (Priority: P2)

A host administrator can adopt or operate RexEnv and Miu alongside existing projects and services without taking over unrelated DNS, listeners, certificates, data, credentials, or installations.

**Why this priority**: Safe coexistence is necessary on shared development hosts, though it follows the main project and service journeys.

**Independent Test**: Seed a disposable host with neighboring projects, services, routes, and trust settings; exercise supported proposals and operations; compare their usability and ownership afterward.

**Acceptance Scenarios**:

1. **Given** an unknown or unowned installation, **When** the user inspects or proposes adoption, **Then** it remains unchanged and cannot be treated as managed until explicit ownership is established.
2. **Given** a hostname, listener, resolver, or certificate conflict, **When** the user requests routing or service setup, **Then** Sandbox presents a conflict or refusal before mutation and preserves existing behavior.
3. **Given** multiple projects on the host, **When** one project is suspended or removed, **Then** the action does not stop a global service or remove another project's resources.

### Edge Cases

- RexEnv is installed but the installed build is unsupported, its exact provenance is unknown, or the host platform has no matching qualification evidence.
- A prior request may have completed despite the caller seeing a timeout; operation lookup is empty, delayed, or unavailable.
- A snapshot captures only database or only files, a later snapshot is taken, or storage capacity is insufficient; coverage and retention must remain clear.
- Site creation returns credentials once, output is bounded, or the caller disconnects before receiving the result.
- A site or service is partially created, upgraded, stopped, or removed when the host restarts.
- A local file and remote file both changed, or a restore point is missing, corrupt, or belongs to another site.
- Miu is reachable but reports no authenticated service identity, version, health, or durable request status.
- GitHub App grants are revoked or insufficient, the connected identity differs from the expected identity, or a write result is ambiguous.
- A PR contains conflicting or untrusted code, and the available host execution model cannot independently contain its authority.
- DNS, routing, TLS, account, or service ownership is shared with another application or cannot be attributed confidently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST make RexEnv an explicit runtime choice while preserving the existing Docker default and existing project registrations.
- **FR-002**: Sandbox MUST report RexEnv support, build provenance, qualification, host trust classification, and each requested capability as available, unqualified, denied, or unknown before an operation that depends on them.
- **FR-003**: Sandbox MUST preserve a durable, unambiguous mapping between each registered project and its exact RexEnv site; ambiguous mappings MUST block mutation.
- **FR-004**: For qualified installations, Sandbox MUST support the core WordPress project lifecycle: create, reconcile, start, stop, inspect, execute, test, snapshot, restore, reset, and remove owned resources.
- **FR-005**: Sandbox MUST make database and file snapshot coverage explicit and MUST NOT describe a partial snapshot or database-only restore as a complete project baseline.
- **FR-006**: Restore MUST identify the selected restore point and its retained database and file coverage, and MUST preserve unrelated sites and unowned resources.
- **FR-007**: Sandbox MUST report bounded operation outcomes, partial effects, cancellation, and unresolved states truthfully and retain operation identity sufficient to inspect accepted work after a lost reply or restart.
- **FR-008**: Retrying an accepted operation MUST resolve that operation first and MUST NOT silently create a second site, service, or QA run.
- **FR-009**: Sandbox MUST distinguish managed, adopted, unowned, and unknown installations and MUST require explicit review and consent before adoption or changes to pre-existing resources.
- **FR-010**: Sandbox MUST manage the Miu service lifecycle, including support inspection, installation proposal and confirmation, configuration, dashboard access, health and version inspection, upgrade, recovery, and scoped removal.
- **FR-011**: Miu management MUST use supported, authenticated, scoped Miu management interfaces and MUST report observed state; Sandbox MUST NOT depend on private credential/session files or human dashboard sessions.
- **FR-012**: Sandbox MUST retain access to Miu QA run status, verdict, revisions, and evidence through supported interfaces without reimplementing Miu's dashboard or QA method.
- **FR-013**: GitHub App creation and adoption MUST be separate reviewed choices. Before either changes state, Sandbox MUST disclose the connected identity, callback origin, repository grants, and requested external-write authority.
- **FR-014**: Sandbox MUST support human-consented connection of an explicitly selected GitHub App and repository grants without capturing or exposing setup codes, tokens, or private key material.
- **FR-015**: Managed QA MUST default to report-only behavior. App registration, repository grant changes, comments, and publication MUST require separately recorded authorization for the exact action and identity.
- **FR-016**: A missing, revoked, or insufficient App grant MUST NOT fall back to a host login, saved token, or ambient GitHub identity.
- **FR-017**: Each accepted QA run MUST identify its repository, pull request, pinned base and PR revisions, attributable sites, outcome, evidence, and cleanup state.
- **FR-018**: Duplicate delivery, lost replies, restart, output pressure, timeout, and cancellation MUST NOT silently duplicate accepted QA work or release its sites, leases, or cleanup authority while work may continue.
- **FR-019**: Sandbox MUST refuse untrusted code execution before launch unless suitable independent containment or a prior trust classification makes the request eligible under existing policy; shared-host execution MUST be labeled truthfully.
- **FR-020**: Sandbox MUST disclose resource capacity, ownership, and attribution sufficiently to govern persistent services and QA work without promising unmeasured resource savings.
- **FR-021**: DNS, routing, TLS, certificates, listeners, host services, project data, and credentials MUST remain separately scoped; conflicts MUST be resolved before mutation and MUST NOT trigger implicit takeover or global service stop.
- **FR-022**: Upgrade, recovery, reset, and removal proposals MUST identify affected and preserved resources, active work, required consent, and known recovery limits before confirmation.
- **FR-023**: Failed or interrupted operations MUST preserve unowned resources and report partial or unknown outcomes; successful completion MUST NOT be claimed without confirming the resulting state.
- **FR-024**: Linux headless operation MUST be qualified on Ubuntu 24.04 for the selected supported builds before Sandbox advertises that platform and capability as ready; other platforms require separate qualification.
- **FR-025**: First-class readiness MUST include verified CLI and applicable MCP evidence for the assembled core lifecycle, Miu management, and recovery workflows, with matching user documentation.

### Key Entities

- **Project Runtime Selection**: A registered project, its explicitly selected runtime, and the selection's persisted state.
- **Managed Site**: A RexEnv site, its durable project identity, ownership status, capabilities, health, and site data coverage.
- **Operation**: An accepted lifecycle or management request with identity, target, progress, outcome, partial effects, cancellation, and recovery state.
- **Snapshot**: A restore point with site identity, capture time, database coverage, file coverage, retention state, and restore outcome.
- **Managed Service**: A Miu installation with ownership, build provenance, configuration, health, dashboard access, active work, and retained data.
- **GitHub App Connection**: An explicitly selected App identity, callback origin, approved repository grants, and separately authorized external-write scope.
- **QA Run**: A Miu run linked to a repository and pull request, pinned revisions, sites, verdict, retained evidence, authorization, and cleanup state.
- **Host Resource**: A service, account, listener, route, resolver, certificate, or data set with ownership, compatibility, and dependent operations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In qualification, two independently registered RexEnv projects each complete every supported core lifecycle operation with 100% of actions attributed to the intended project and no neighbor mutation.
- **SC-002**: In 100% of tested restore cases, the user can identify whether both database and files are covered, and a declared full baseline restores both after controlled changes while retaining the selected restore point.
- **SC-003**: In interruption qualification, 100% of accepted operations can be resolved by their original identity without a duplicate site, service, or QA run.
- **SC-004**: In 100% of unauthorized-write cases, no comment, publication, App registration, or repository-grant change occurs.
- **SC-005**: In 100% of unsafe-run cases, refusal occurs before PR code starts and no ambient credential fallback is used.
- **SC-006**: A qualified Ubuntu 24.04 headless installation completes managed service inspection, dashboard access, a QA run, evidence review, upgrade recovery, and scoped removal without requiring a desktop login.
- **SC-007**: In 100% of coexistence qualification cases, neighboring projects, existing hostnames, trust settings, and unowned resources remain usable and unchanged after the tested operation.
- **SC-008**: Administrators can complete each authorized Miu management task with a visible proposal or outcome, and can identify the exact managed build and connected GitHub identity for every relevant action.

## Assumptions

- First-class RexEnv means the complete qualified core WordPress lifecycle, including database-and-file snapshots and recovery; unavailable core capabilities prevent readiness rather than becoming indefinite optional limitations.
- Docker remains the default. RexEnv adoption is explicit and does not migrate existing projects implicitly.
- Miu remains an external application and retains its dashboard and QA behavior. Required management interfaces are contributed to an authorized fork of `alimuzzaman/miu` and proposed to `akashmdiu/miu` before Sandbox relies on them.
- The initial headless qualification target is Ubuntu 24.04. No other platform is implied by this target.
- Untrusted pull request execution requires suitable independent containment or an eligible prior trust classification. A shared service account or application-level network guard alone does not establish containment.
- GitHub App setup and adoption involve human consent; Sandbox does not automate consent or infer permission from available credentials.
- Measurements of resource use describe observed coverage and attribution. Resource savings are not assumed.
- Installation, privilege changes, credentials, GitHub writes, DNS and trust changes, destructive cleanup, production changes, and release retain their existing authorization requirements.
- Verified live qualification on approved disposable targets is required before runtime readiness; this specification itself authorizes no installation, migration, or production change.
