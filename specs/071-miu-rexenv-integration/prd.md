# Product Requirements Draft: Miu management and RexEnv integration

**Status**: Refined

**Created**: 2026-11-08

**Last Refined**: 2026-11-08

**Input**: "integrate miu and also integrate RexEnv as first class". Miu scope confirmed: "Manage the complete Miu service, dashboard and GitHub App through Sandbox."

**Upstream Contribution**: "Add the management APIs to Miu itself, then integrate that version." Delivery confirmed: "fork it then create pr to the original repo with our changes".

**Drafting Model**: Codex Office, GPT-6.1-Sol, medium reasoning. Office Haiku 5.5 at xhigh gathers source facts. No runtime qualification has run.

**Final Validation**: `PASS`, independent Codex Office GPT-6-Luna review at high reasoning, standard service tier

**Validated On**: 2026-11-08

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

## Problem and motivation

The operator wants a supported alternative to Docker for WordPress development
and server-side QA, with the project selection and evidence workflow used
elsewhere in Sandbox. RexEnv manages sites and services. Miu uses RexEnv to test
WordPress pull requests. Neither application is integrated with Sandbox.
Adopting them currently means managing another installer, service, dashboard,
GitHub App, site inventory, and cleanup workflow.

The desired outcome is one supported operational entry point for RexEnv projects
and the complete Miu service. Resource use motivates the request, but no measured
memory or CPU improvement is established. The integration must expose measured
use and limits without promising savings or describing shared-host execution as
hostile-code isolation.

## Users and desired outcomes

- **Developer**: Select RexEnv for a trusted WordPress project and use normal
  Sandbox lifecycle, execution, inspection, and recovery workflows.
- **QA operator**: Install and manage Miu through Sandbox, connect an explicitly
  selected GitHub App, and review paired base and PR evidence in its dashboard.
- **Host administrator**: See ownership, compatibility, health, resource
  attribution, and pending recovery before approving operational changes.
- **Agent**: Act on one authorized project or run without gaining authority over
  another site's data, provider credentials, or unrelated host services.

## Goals

- Make RexEnv an explicitly selectable runtime with truthful capabilities and
  durable project-to-site identity.
- Support the core WordPress workflow on qualified RexEnv installations:
  create, reconcile, start, stop, inspect, execute, test, snapshot, restore,
  reset, and remove owned resources.
- Manage Miu installation, service configuration, dashboard access, GitHub App
  setup and repository grants, upgrades, health, runs, evidence, and removal.
- Preserve Sandbox job authority, secret mediation, resource governance, and
  recovery controls across both integrations.
- Support Linux headless operation without a desktop login. Qualify every
  advertised platform and capability separately.
- Preserve neighboring projects, DNS, listeners, certificates, services, and
  existing installations through failures and interruptions.

## Non-goals

- Replacing Docker as the implicit default or silently migrating existing projects.
- Moving Lenzora, production services, existing data, or credentials as a side
  effect of implementing or enabling this integration.
- Treating RexEnv as a generic replacement for arbitrary Compose applications.
- Claiming arbitrary PR code is contained by a shared user, a WordPress HTTP
  denylist, or an agent prompt.
- Reimplementing the Miu dashboard, QA method, or RexEnv site engine in Sandbox.
- Automatically adopting, replacing, or deleting unowned installations or data.

## Product scenarios

### Scenario 1: Prepare managed services

- **Starting state**: A supported host has capacity and either no installation
  or an existing installation with unknown ownership.
- **User action**: Inspect support and review an installation or adoption proposal.
- **Expected outcome**: Inspection changes nothing. The proposal identifies
  versions, ownership, accounts, services, listeners, data, and required consent.
  A confirmed application creates only approved resources. An unknown installation
  is preserved and cannot be treated as Sandbox-owned.

### Scenario 2: Use a RexEnv project

- **Starting state**: Compatible RexEnv is available and explicitly selected.
- **User action**: Run the core WordPress workflow and inspect URL and health.
- **Expected outcome**: The project and instance label resolve to one exact site.
  Operations retain bounded output and truthful outcomes and preserve neighboring
  sites. Unsupported optional capabilities are disclosed before effects. Failure
  does not start another backend.

### Scenario 3: Connect Miu and test a PR

- **Starting state**: An approved Miu service is available and the operator has
  permission to connect a GitHub App and selected repositories.
- **User action**: Complete connection and request a QA run.
- **Expected outcome**: The dashboard exposes the authorized repository and run.
  Base and PR revisions are pinned. Their site states are independent, and claims
  link to retained evidence. Sandbox exposes the actual verdict. External posting
  follows separately recorded authorization.

### Scenario 4: Recover after interruption

- **Starting state**: A site operation, service upgrade, or QA run loses its reply,
  times out, or is cancelled.
- **User action**: Inspect or resume using the retained operation identity.
- **Expected outcome**: Partial effects and unknown outcomes remain visible.
  Observation resolves the owned operation before any retry. Cancellation does
  not release sites, leases, or cleanup authority while owned work may continue.

### Scenario 5: Share a host

- **Starting state**: Compose projects, native sites, resolvers, certificate
  authorities, or listeners already exist.
- **User action**: Configure RexEnv routing or start Miu QA.
- **Expected outcome**: Conflicts produce a proposal or refusal before mutation.
  Existing hostnames, trust, and services remain usable. Global service stop
  cannot be presented as per-project suspension or cleanup.

### Scenario 6: Restore, upgrade, or remove owned resources

- **Starting state**: Managed services hold configuration, app connections, site
  data, run history, and evidence.
- **User action**: Review and confirm a restore, upgrade, or removal proposal.
- **Expected outcome**: The proposal identifies preserved and affected resources,
  active users and runs, compatibility, backups, and recovery limits. Repetition
  creates no duplicates and removes no unowned data. Failed upgrades retain the
  previous installation or a truthful recoverable outcome.

### Scenario 7: Refuse unsafe execution

- **Starting state**: A PR is untrusted, grants are stale, a build is incompatible,
  capacity is insufficient, or required containment is unavailable.
- **User action**: Request a run.
- **Expected outcome**: Refusal names the missing evidence before project code
  executes. Existing permitted work is neither duplicated nor replayed.

## Proposed product behavior

- RexEnv selection persists across failures. Existing registrations and Docker
  defaults retain their meaning.
- Eligibility includes observed compatible builds, owned site mapping, requested
  capabilities, and host trust classification. Installed, adopted, qualified,
  and ready are distinct states.
- Miu remains an external application with its existing dashboard. Sandbox
  manages its lifecycle and exposes run evidence through supported interfaces,
  without direct private-state edits.
- New GitHub App creation and existing App adoption are distinct choices.
  Identity, callback origin, repository grants, and external write scope are
  reviewed before connection or change. Setup codes and secrets never enter
  captured output, arguments, reports, or project configuration.
- Miu's existing posting behavior grants Sandbox no permission to send comments,
  publish reports, register Apps, or expand repository access.
- Managed QA defaults to report-only. A run or service can publish only under
  its recorded external-write authorization and exact connected identity.
  Missing App permissions cannot fall back to a host login, saved token, or
  ambient credential. Fix pushes and other external channels have their own
  authorization requirements.
- Automated management authenticates as a narrowly scoped service identity.
  Loopback location and a dashboard header are not authentication. Sandbox does
  not reuse a human session or inspect Miu's private credential and session files.
- Trusted development code and untrusted PR code have different admission rules.
  Shared-host execution is labeled honestly. Untrusted code needs independent
  containment suitable for its authority or an explicit prior trust classification
  that makes the request eligible under existing policy.
- Runs retain one accepted identity, exact revisions, attributable sites, bounded
  evidence, cancellation state, and cleanup state. Duplicate requests and lost
  replies do not automatically create another run.
- DNS, routing, TLS, service ownership, and project data remain separate scopes.
  There is no implicit TLD takeover, certificate-key copying, broad credential
  grant, or global service stop to satisfy a project operation.
- Support distinguishes available, unqualified, denied, and unknown operations.
  Core workflow gaps block first-class readiness instead of becoming permanent
  optional refusals.
- Resource and retention controls account for persistent services and QA work.
  Admission and cleanup preserve active runs and uncertainty fences. Measurements
  state coverage and shared attribution instead of promising a fixed saving.
- The agreed integration outcome releases as a complete feature. Intermediate
  source work does not establish readiness or authorize live enablement.

## Constraints and dependencies

- Upstream source inspected on 2026-11-08 identifies RexEnv 0.9.7, with release
  commit `2e92c00bd927825f1bb818a0bd47a851a0dae27b`. The source inspection used
  later master `38cb0d7d2567b8504142d1aad83923529ace9b5d`. Compatibility at the
  exact release must be checked rather than inferred from the version string.
- The inspected RexEnv interfaces expose site lifecycle and WordPress argv
  operations. The WP runner launches PHP and can execute arbitrary PHP as the
  site user. This does not establish containment or Sandbox's owned-process
  cancellation contract. Generic execution and restart reconciliation of in-flight
  mutations remain required integration qualification work.
- Complete local database-and-files snapshots are absent from the inspected
  RexEnv source. Live-host push backups and a previous local database copy do
  not meet that requirement. Full snapshot and baseline recovery require an
  additional supported mechanism before core workflow qualification.
- Linux headless support is described upstream, but a RexEnv 0.9.7 headless run
  on Ubuntu 24.04 has not been established. Existing older-build results do not
  qualify the new integration.
- Miu source `b4fa46b866850cdcc44b34b736a9a2dea39ccacd` has dashboard run controls
  and human-consented GitHub App creation, but no service authentication,
  idempotent manual submission, or complete management interface. Its GitHub
  client can fall back to ambient credentials. Its installer uses moving
  dependencies. Full management requires compatible Miu changes before adoption.
- The Miu management prerequisite must provide authenticated scoped control,
  installed-build and health evidence, durable request lookup, explicitly bound
  GitHub identity, safe setup and adoption, and accountable removal. Its change
  ownership is Miu itself through a fork and pull request to the original
  repository. Sandbox then pins the compatible reviewed version. No private-state
  workaround or Sandbox-owned compatibility patch is accepted.
- Existing Sandbox runtime, ingress, resolver, job, secret, resource, recovery,
  CLI, and MCP contracts govern their operations.
- Qualified builds and provenance are explicit. Installation and upgrade cannot
  execute an unreviewed moving upstream installer or silently change versions.
- Initial server qualification targets Ubuntu 24.04 headless, matching Miu's
  documented target. Other platform claims require separate evidence.
- Existing native trust classes apply. Miu's WordPress network guard does not
  establish complete outbound isolation or protection from arbitrary code.
- Snapshot outcomes disclose database and file coverage. Database import alone
  cannot be labeled a complete Sandbox baseline restore.
- Existing authorization requirements still apply to installation, privilege,
  accounts, credentials, GitHub writes, DNS and trust changes, destructive cleanup,
  deployment, production changes, and release.
- Live verification on approved disposable targets is required before runtime
  readiness. This artifact grants no live installation or migration authority.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Outcome | First-class RexEnv and complete Miu management | One supported operational entry point | User, 2026-11-08 |
| Miu scope | Manage service, dashboard, and GitHub App through Sandbox | User selected complete management | User, 2026-11-08 |
| Management API ownership | Add APIs in a Miu fork and open a PR to akashmdiu/miu, then integrate the compatible version | Keep management support upstream rather than maintaining a Sandbox patch | User, 2026-11-08 |
| Managed QA writes | Report-only default; external writes require recorded identity and authorization | Avoid implicit credential fallback and unauthorized publication | Existing user external-action and account policy |
| Default | Explicit RexEnv selection; retain Docker default | Preserve existing projects and policy | Existing runtime and clean-URL policy |
| App ownership | Retain upstream applications and contribute missing Miu interfaces before integration | Preserve product responsibilities and avoid private-state coupling | User, 2026-11-08 |
| Server qualification | Ubuntu 24.04 headless first | Miu's documented target and requested server use | Evidence-backed assumption, subject to refinement |
| Operational effects | Build integration before approved live adoption | Product work does not grant host mutation authority | Existing user authorization policy |

## Open questions

- None. The location of Miu changes is confirmed. Exact interface design and
  runtime qualification remain specification and implementation work, not an
  unresolved product-scope choice.

## Acceptance outcomes

- Support inspection creates no accounts, sites, services, routes, registrations,
  trust, credentials, or GitHub changes on an unconfigured or incompatible host.
- Approved disposable Linux installation reaches healthy Miu dashboard and RexEnv
  states at qualified builds. Repetition creates no duplicates.
- Two RexEnv projects independently complete the core WordPress workflow. Every
  operation targets the correct project and label; failures preserve a neighbor.
- Snapshots restore declared database and file baselines after controlled changes.
  Database-only and full coverage are visibly different.
- An explicitly connected App with selected repository grants permits one accepted
  QA run against pinned revisions. Miu and Sandbox agree on verdict, evidence,
  completion, and unresolved limits.
- Supported Miu management demonstrates installed build and health inspection,
  configuration, upgrade, dashboard access, QA runs, evidence, and scoped removal.
  App creation or adoption and repository grants follow human consent and report
  the exact connected identity and grant scope.
- Duplicate delivery, restart, lost replies, timeout, cancellation, and output
  pressure neither duplicate accepted work nor release its authority early.
- Refused external writes create no comment, publication, App, or grant change.
  Authorized actions remain attributable and respect actual replay guarantees.
  Missing or insufficient App grants never use an available host GitHub identity
  as fallback.
- Untrusted PRs without adequate containment are refused. Missing identity,
  access, capacity, or support evidence launches no project code or fallback.
- Listener, resolver, or hostname conflicts preserve existing resources.
  Coexisting Compose and RexEnv projects retain canonical URLs and cleanup scope.
- Upgrade, rollback, and removal preserve unowned resources and approved persistent
  data and evidence, and report partial or unknown effects truthfully.
- Snapshot qualification independently changes and restores both a local database
  and local files, preserves a second site, and proves the selected restore point
  is retained across a later snapshot. Live-host backups cannot satisfy this check.
- Runtime readiness requires CLI and applicable MCP evidence on the assembled
  feature, independent review of consequential authority changes, and matching
  documentation. Synthetic tests alone do not satisfy it.

## Risks and assumptions

- **Evidence**: [RexEnv platform support](https://github.com/rexenv/rexenv/blob/38cb0d7d2567b8504142d1aad83923529ace9b5d/docs/PLATFORMS.md),
  [site operations](https://github.com/rexenv/rexenv/blob/38cb0d7d2567b8504142d1aad83923529ace9b5d/src-tauri/src/mcp_server/user_sites.rs),
  and [published qualification notes](https://github.com/rexenv/rexenv/blob/38cb0d7d2567b8504142d1aad83923529ace9b5d/docs/PUBLISH-TESTING.md)
  were inspected as source evidence. No installed-runtime proof is claimed.
- **Evidence**: [RexEnv WordPress execution](https://github.com/rexenv/rexenv/blob/38cb0d7d2567b8504142d1aad83923529ace9b5d/src-tauri/src/core/wordpress.rs)
  and [local live-pull behavior](https://github.com/rexenv/rexenv/blob/38cb0d7d2567b8504142d1aad83923529ace9b5d/src-tauri/src/core/live_sync/pull.rs)
  distinguish scoped WP execution from containment and database copies from full
  local snapshots. Live synchronization is not a substitute recovery operation.
- **Evidence**: [Miu dashboard controls](https://github.com/akashmdiu/miu/blob/b4fa46b866850cdcc44b34b736a9a2dea39ccacd/bin/qa-dashboard),
  [GitHub credential selection](https://github.com/akashmdiu/miu/blob/b4fa46b866850cdcc44b34b736a9a2dea39ccacd/lib/github.mjs),
  and [App setup](https://github.com/akashmdiu/miu/blob/b4fa46b866850cdcc44b34b736a9a2dea39ccacd/lib/ghapp.mjs)
  establish the management prerequisites by source inspection, without executing
  the installer or calling its live APIs.
- **Risk**: Miu may expose interactive setup and private-file conventions without
  supported management interfaces. Upstream work may be a prerequisite.
- **Risk**: Caller timeout may leave app-side mutation running. Cancellation and
  reconciliation need independent evidence.
- **Risk**: Shared QA slots and service or agent accounts may conflict with
  independent ownership and concurrent Sandbox work.
- **Risk**: Shared native services, DNS, TLS, and alternate ports can constrain
  coexistence and WordPress URL rewriting.
- **Risk**: Installation and upgrade cross dependency-trust and privilege
  boundaries. An unpinned installer does not qualify.
- **Risk**: Host execution near secrets can expose them unless the execution and
  credential boundaries are independently enforced.
- **Assumption**: First-class support means a working core lifecycle and truthful
  capabilities. It implies neither generic Compose replacement nor resource savings.

## Readiness for specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`
