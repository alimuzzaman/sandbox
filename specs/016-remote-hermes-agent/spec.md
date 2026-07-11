# Feature Specification: Remote Hermes Agent Integration

**Feature Branch**: `016-remote-hermes-agent`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "Run Hermes Agent on the existing remote Sandbox server; expose Sandbox CLI and MCP tools to Hermes; work on any Git repository in isolated worktrees; create WordPress instances on demand; add operational hardening in V2 and an authenticated web dashboard after V2."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install and initiate remote Hermes (Priority: P1)

A developer can install a pinned Hermes release on a configured Sandbox remote, generate its Sandbox-aware configuration, verify the installation, and initiate an interactive or one-shot agent session from the local `sb` command.

Hermes runs under the existing remote Sandbox account so it can use the same `$SANDBOX_HOME`, Docker access, instance registry, `sb` CLI, and complete Sandbox MCP tool catalog. No agent token, repository credential, or gateway secret is copied into the repository or printed after storage.

**Why this priority**: This is the minimum useful integration. It proves that Hermes can be installed reproducibly and can operate Sandbox through both its CLI and MCP boundary.

**Independent Test**: On a clean supported remote, install a pinned Hermes version, run the diagnostic command, initiate a one-shot prompt, and have Hermes call a read-only Sandbox MCP tool followed by on-demand creation of a disposable WordPress instance.

**Acceptance Scenarios**:

1. **Given** a configured remote with Sandbox installed and no Hermes installation, **When** the developer runs the Hermes install command, **Then** the pinned release is installed under the remote user's home and repeated installation converges without duplicating state.
2. **Given** Hermes is installed, **When** the developer runs Hermes setup and diagnostics, **Then** the generated profile exposes the complete Sandbox MCP tool catalog, the direct `sb` CLI is executable, and secrets are redacted from output.
3. **Given** a valid WordPress repository and no active instance for its worktree, **When** Hermes invokes the Sandbox instance bootstrap tool, **Then** Sandbox creates or reuses exactly one instance for that worktree and returns its URL.
4. **Given** a non-WordPress repository, **When** Hermes starts a session, **Then** no WordPress instance is created unless the user explicitly requests one and the repository can be initialized for Sandbox.

---

### User Story 2 - Work safely on any Git repository (Priority: P2)

A developer can authenticate the remote host with a Git provider, clone any authorized Git repository into a managed repository root, list managed repositories, and start Hermes against a selected repository. Coding sessions use a new Git worktree by default so simultaneous work does not modify the primary checkout.

**Why this priority**: General repository support turns Hermes from a WordPress-only helper into a remote coding environment while keeping concurrent sessions isolated.

**Independent Test**: Authenticate through an interactive provider flow, clone one public and one private test repository, start two concurrent sessions, and verify they receive different worktrees while the primary checkout remains unchanged.

**Acceptance Scenarios**:

1. **Given** a valid repository URL without embedded credentials, **When** the developer clones it with a unique managed name, **Then** the repository is stored below the configured Hermes repository root and appears in repository listings.
2. **Given** two sessions for the same repository, **When** both use the default isolation mode, **Then** each session operates in a distinct worktree and neither changes the primary checkout.
3. **Given** a traversal path, embedded URL credential, duplicate managed name, or non-Git directory, **When** it is supplied to a repository command, **Then** the command rejects it before remote mutation and returns an actionable sanitized error.
4. **Given** an explicit no-worktree override, **When** the developer starts Hermes, **Then** the command clearly reports that isolation is disabled and uses the primary checkout only for that session.
5. **Given** the developer wants access to only one private repository,
   **When** they authenticate GitHub for Hermes, **Then** the system rejects
   the broad browser OAuth flow and accepts only a fine-grained token scoped to
   the selected repository without organization permissions.

---

### User Story 3 - Operate long-running Hermes access (Priority: P3)

A developer can manage a persistent Hermes gateway on the remote host and submit asynchronous prompts through Sandbox MCP. Gateway access is limited to explicitly configured users or channels, and detached jobs can be inspected without holding an SSH terminal open.

**Why this priority**: Persistent access makes Hermes useful away from a terminal, but it must build on the verified local command and repository lifecycle.

**Independent Test**: Install and start the gateway service with a restrictive allowlist, submit an asynchronous prompt through Sandbox MCP, poll it to completion, inspect sanitized logs, and stop/restart the service across a remote reboot simulation.

**Acceptance Scenarios**:

1. **Given** a configured gateway identity and allowlist, **When** the developer installs and starts the service, **Then** it runs under the remote Sandbox account and reports a healthy status.
2. **Given** an empty or allow-all gateway policy, **When** setup or start is requested, **Then** the operation fails closed with guidance to configure explicit access.
3. **Given** a managed repository, **When** a caller submits an asynchronous Hermes prompt through Sandbox MCP, **Then** the response returns a job identifier and later status calls expose bounded, sanitized output.

---

### User Story 4 - Update, recover, and operate Hermes reliably (Priority: P4 / V2)

After the V1 workflows are stable, an operator can preview and confirm a pinned Hermes update, back up and restore Hermes state, constrain concurrent jobs and worktrees, clean stale resources, and diagnose recovery after failure or reboot.

**Why this priority**: These controls are required before exposing an always-on browser surface, but they are not necessary to prove the V1 agent workflow.

**Independent Test**: On a V1 installation, preview an update, reject an unconfirmed change, perform a confirmed pinned update, simulate a failed health check and rollback, exercise resource limits, and restore state from a backup.

**Acceptance Scenarios**:

1. **Given** an available pinned release, **When** an operator requests an update plan, **Then** the current version, target version, backup action, health checks, and rollback action are shown without mutation.
2. **Given** no explicit confirmation, **When** an operator requests update, restore, or destructive cleanup, **Then** no state changes occur.
3. **Given** a confirmed update whose health check fails, **When** verification completes, **Then** the previous installation and configuration are restored and the failure is reported.
4. **Given** configured job, worktree, disk, and memory limits, **When** a request exceeds a limit, **Then** it is rejected or queued without destabilizing existing Sandbox instances.
5. **Given** a server reboot, **When** the remote becomes available, **Then** enabled Hermes services recover, stale job state is reconciled, and diagnostics report any manual action required.

---

### User Story 5 - Use an authenticated web dashboard (Priority: P5 / V3 after V2)

Only after the V2 operational acceptance gate passes, an operator can install and manage the upstream Hermes web dashboard against the same Hermes home, profiles, skills, MCP servers, gateway, and session state. The dashboard binds to loopback by default and is accessed through SSH forwarding; optional public exposure requires authenticated TLS routing and an explicit reviewed confirmation.

**Why this priority**: A browser interface is valuable, but exposing agent controls before update, recovery, resource, and health safeguards are proven would create unnecessary operational risk.

**Independent Test**: Verify dashboard commands are gated before V2 completion, then install it after the gate, access it over an SSH tunnel with authentication, and separately test a planned public exposure whose failed health check restores the prior routing state.

**Acceptance Scenarios**:

1. **Given** V2 has not passed its recorded acceptance gate, **When** dashboard installation or exposure is requested, **Then** the command refuses and identifies the unmet prerequisites.
2. **Given** V2 has passed, **When** the dashboard is installed and started with default settings, **Then** it binds only to loopback, requires authentication, and uses the same Hermes profile and Sandbox MCP configuration as CLI sessions.
3. **Given** a supported managed-hosting deployment and explicit public FQDN, **When** the operator reviews and confirms an exposure plan, **Then** authenticated TLS routing is added without an insecure mode.
4. **Given** a failed dashboard or routing health check, **When** exposure verification ends, **Then** the previous routing state is restored and no unauthenticated endpoint remains.

### Edge Cases

- The remote is reachable but lacks Docker group access, Python, Git, systemd, free disk, or memory required by the selected operation.
- A Hermes release tag moves, its downloaded revision differs from the requested commit, or the requested version is unavailable.
- Hermes is partially installed, its configuration is malformed, or its executable is not on the non-interactive SSH path.
- Sandbox is installed at a non-default remote home or relocates after Hermes configuration was generated.
- The Sandbox MCP process starts but reports an incomplete tool catalog or times out during an instance operation.
- Two clone, update, cleanup, or worktree requests race for the same repository or Hermes home.
- A repository has uncommitted changes, submodules, Git LFS objects, an unusually long name, or a default branch that differs from `main`.
- A session exits unexpectedly and leaves a worktree, branch, process, or job record behind.
- Git provider authentication expires or a private repository becomes inaccessible after it was registered.
- The gateway or dashboard is configured with an empty allowlist, wildcard audience, insecure authentication, public bind address, or conflicting port.
- A backup is incomplete, too old for the installed Hermes schema, or cannot be restored within available disk space.
- Managed-hosting support required for public dashboard exposure is absent or still under development.

## Requirements *(mandatory)*

### Functional Requirements

#### V1 — Core remote agent

- **FR-001**: The system MUST install a version-pinned Hermes release on an explicitly selected configured Sandbox remote without modifying other remotes.
- **FR-002**: Installation and setup MUST be idempotent and MUST distinguish installed, configured, running, degraded, and unavailable states.
- **FR-003**: Hermes MUST run under the same remote operating-system account that owns the Sandbox installation and Docker access.
- **FR-004**: The generated Hermes profile MUST expose the complete Sandbox MCP tool, resource, and prompt catalog without a feature-level include or exclude filter.
- **FR-005**: Hermes MUST be able to execute the remote `sb` CLI directly with the same absolute `$SANDBOX_HOME` used by its MCP server.
- **FR-006**: The system MUST verify MCP connectivity and catalog completeness during diagnostics and MUST fail diagnostics when required Sandbox capabilities are unavailable.
- **FR-007**: WordPress instance creation MUST use Sandbox's existing per-project/worktree instance lifecycle and registry rather than a Hermes-specific instance registry.
- **FR-008**: Starting a session for a non-WordPress repository MUST NOT implicitly create a WordPress instance.
- **FR-009**: The local CLI MUST support install, setup, status, diagnostics, interactive chat, and one-shot execution against an explicit remote.
- **FR-010**: Machine-readable commands MUST use a stable sanitized result envelope containing operation status, remote, version, repository, path, job identifier, and structured error fields when applicable.
- **FR-011**: Setup MUST keep model-provider credentials, Git credentials, gateway tokens, dashboard credentials, and private keys outside version control and MUST NOT print stored secret values.
- **FR-012**: Destructive or privilege-sensitive terminal actions MUST require Hermes manual approval; background scheduled terminal actions MUST be denied by default.
- **FR-013**: Documentation and generated policy MUST state that full unfiltered MCP access includes destructive Sandbox tools and requires explicit user confirmation for destructive operations.

#### V1 — Repository, worktree, and gateway lifecycle

- **FR-014**: The system MUST maintain managed repositories below one configured remote repository root and MUST prevent paths from escaping that root.
- **FR-015**: Repository commands MUST support provider authentication, clone, list, and selection without accepting credentials embedded in a repository URL.
- **FR-015b**: GitHub authentication MUST reject the account-wide browser OAuth flow and accept only a fine-grained, repository-scoped token over standard input; the token MUST never appear in command arguments, state, logs, or output, and HTTPS Git transport MUST be used for that credential.
- **FR-016**: Each coding session MUST create an isolated Git worktree by default and MUST provide an explicit per-session override to use the primary checkout.
- **FR-017**: Worktree creation MUST preserve existing uncommitted user changes and MUST never delete a dirty or active worktree automatically.
- **FR-018**: Concurrent repository and worktree mutations MUST be serialized per affected repository.
- **FR-019**: The gateway MUST be managed as an operating-system service with setup, install, start, stop, restart, status, and bounded log retrieval commands.
- **FR-020**: Gateway setup and start MUST reject empty, wildcard, or allow-all access policies.
- **FR-021**: Sandbox MCP MUST expose Hermes status and prompt submission operations, with asynchronous submission using the existing Sandbox job lifecycle.
- **FR-022**: Remote command, MCP, gateway, and job output MUST be bounded and sanitized before it is returned locally.

#### V2 — Operational hardening

- **FR-023**: V2 MUST add preview-and-confirm updates pinned to an immutable release revision; it MUST NOT automatically track a moving default branch.
- **FR-024**: Update MUST create a restorable backup, run health verification, and automatically restore the previous installation and configuration when verification fails.
- **FR-025**: V2 MUST support backup listing, validated restore, and explicit destructive cleanup with dry-run output.
- **FR-026**: V2 MUST enforce configurable limits for concurrent Hermes jobs, active worktrees, minimum free disk, and minimum free memory.
- **FR-027**: V2 MUST reconcile stale jobs and worktrees without deleting dirty worktrees or state that cannot be proven inactive.
- **FR-028**: V2 MUST provide log rotation, structured health output, bounded retention, and service recovery across remote reboot.
- **FR-029**: V2 completion MUST be recorded only after automated tests and a live remote acceptance run verify update rollback, restore, resource rejection, stale-state handling, and reboot recovery.

#### V3 — Dashboard after V2

- **FR-030**: Dashboard implementation and public exposure MUST remain blocked until the V2 completion gate in FR-029 is recorded as passed.
- **FR-031**: V3 MUST use the supported upstream Hermes dashboard rather than creating a custom dashboard application.
- **FR-032**: The dashboard MUST use the same Hermes home, selected profile, model configuration, skills, MCP servers, gateway state, cron state, and session data as non-dashboard Hermes entry points.
- **FR-033**: Dashboard lifecycle MUST support install, setup, start, stop, restart, status, logs, diagnostics, expose, and unexpose operations.
- **FR-034**: The dashboard MUST bind to loopback by default and its documented default access path MUST be SSH port forwarding.
- **FR-035**: Dashboard authentication MUST always be enabled; insecure or unauthenticated modes MUST be rejected.
- **FR-036**: Optional public exposure MUST require an explicit FQDN, a read-only plan, an explicit confirmation, authenticated TLS routing, and the managed-hosting capability delivered by feature 015.
- **FR-037**: Failed dashboard installation, startup, authentication, exposure, or health verification MUST leave no unauthenticated public endpoint and MUST restore prior managed routing state.
- **FR-038**: V3 dashboard scope MUST be limited to supported Hermes chat, sessions, profiles, models, skills, MCP, gateway, cron, and system status; custom UI, multi-tenancy, and Sandbox deployment controls are out of scope.

### Key Entities

- **Hermes installation**: A pinned executable/source revision and launcher owned by the remote Sandbox account, with its install path, current version, and health state.
- **Hermes profile**: The selected non-secret configuration that connects Hermes to Sandbox MCP and establishes terminal, approval, checkpoint, model, gateway, and dashboard behavior.
- **Managed repository**: A validated Git checkout below the configured remote repository root, identified by a unique logical name and canonical path.
- **Hermes session**: One interactive or one-shot agent execution tied to a managed repository, optional isolated worktree, profile, and lifecycle state.
- **Hermes job**: A detached prompt execution with bounded logs, timestamps, status, result metadata, and cleanup eligibility.
- **Gateway service**: The persistent remote Hermes communication process, its restrictive access policy, service state, and health information.
- **Operational backup**: A versioned, integrity-checked snapshot of the Hermes installation and non-secret state sufficient for update rollback or operator restore.
- **V2 completion gate**: Auditable evidence that the operational hardening acceptance suite passed on the supported remote environment.
- **Dashboard service**: The V3 authenticated browser process, loopback listener, lifecycle state, optional managed route, and rollback metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A clean supported remote reaches a healthy, Sandbox-connected Hermes installation through one documented setup sequence, and running that sequence again makes no unintended changes.
- **SC-002**: Diagnostics verify direct `sb` access and enumerate 100% of the Sandbox MCP tools exposed by the installed Sandbox version, with no secret values present in command output or captured logs.
- **SC-003**: A disposable WordPress worktree can create or reuse its instance on demand and return a working instance URL within the existing Sandbox operation timeout.
- **SC-004**: Two simultaneous sessions for the same repository operate in distinct worktrees while a pre-existing dirty primary checkout remains byte-for-byte unchanged.
- **SC-005**: All tested traversal paths, embedded credentials, duplicate names, invalid repositories, empty gateway allowlists, and unauthenticated dashboard configurations are rejected before external exposure or destructive mutation.
- **SC-006**: A detached prompt can be submitted, polled, and completed through Sandbox MCP, with output bounded to the documented maximum and no orphaned running process after cancellation or failure.
- **SC-007**: V2 fault-injection tests demonstrate successful automatic rollback after a failed update and successful validated restoration from the most recent backup.
- **SC-008**: V2 load and recovery tests demonstrate enforcement of configured job/worktree/disk/memory limits and healthy service recovery after reboot without losing active Sandbox registry entries.
- **SC-009**: Before the V2 gate passes, 100% of dashboard lifecycle and exposure attempts are refused; after it passes, the dashboard is reachable over an authenticated SSH-forwarded session and is not listening on a public interface by default.
- **SC-010**: A failed public dashboard exposure test restores the previous managed routing configuration and leaves zero unauthenticated dashboard endpoints.

## Assumptions

- The first supported target is the existing Ubuntu 24.04 x86_64 remote named `scaleway-sandbox`, using systemd, Docker, Git, and Python under the current Sandbox account.
- Sandbox remains the authority for WordPress instance identity, lifecycle, Docker access, and registry state; Hermes orchestrates it but does not replace it.
- The remote installation uses a dedicated Hermes home under the remote user's home and a repository root below `$SANDBOX_HOME`.
- Git provider authentication is performed interactively on the remote host, such as a device flow; local private SSH keys are not copied to the server.
- V1 grants Hermes the complete Sandbox MCP surface as requested. Approval prompts provide policy guidance but do not constitute a technical authorization boundary for arbitrary MCP calls.
- Upstream Hermes CLI, gateway, worktree, MCP, and dashboard capabilities remain available in the pinned supported release; compatibility is revalidated before each supported-version change.
- Public dashboard exposure depends on the separately developed managed-hosting feature 015 and is not implemented until that dependency and V2 are complete.
- This feature does not deploy to production, change DNS, expose a dashboard, or store live credentials during specification and implementation planning.
