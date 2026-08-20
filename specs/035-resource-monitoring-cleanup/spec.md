# Feature Specification: Resource Monitoring and Safe Cleanup

**Feature Branch**: `latest`

**Created**: 2026-07-28

**Status**: Implemented

**Input**: Ready PRD in `specs/035-resource-monitoring-cleanup/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand Host Storage (Priority: P1)

As a Sandbox operator, I can request a read-only storage status for the current
machine or a named remote so that I know how full the host is, which managed
owners and resource categories consume the most space, and how much space may
be reclaimable.

**Why this priority**: Operators must understand the problem before any cleanup
can be planned safely. This story delivers useful monitoring without mutation.

**Independent Test**: Run storage status against a host containing active
instances, managed caches, and an unmanaged directory. Verify that host capacity
and ranked categories are reported, the managed resources have evidence-based
owners and lifecycle states, the unmanaged directory is visible but not marked
reclaimable, and no host state changes.

**Acceptance Scenarios**:

1. **Given** a reachable host with sufficient measurement privileges, **When**
   an operator requests fast storage status, **Then** the result reports total,
   used, available, attributed, unknown, and estimated reclaimable bytes with a
   scan timestamp and target identity.
2. **Given** managed resources belonging to several projects, workspaces, jobs,
   and backups, **When** status is requested, **Then** the largest resources are
   ranked and each discovered resource has an owner or an explicit ownership
   gap, lifecycle classification, measurement state, and reclaimable estimate.
3. **Given** an unmanaged filesystem path or engine resource, **When** status is
   requested, **Then** it is reported as unmanaged or unknown and is not
   presented as automatically reclaimable.
4. **Given** an operator requests a named remote, **When** the target is
   resolved, **Then** all capacity and resource results identify that remote and
   do not include resources from another host.

---

### User Story 2 - Perform Thorough Attribution (Priority: P1)

As a host administrator, I can request a thorough scan when fast status leaves
large or slow categories unresolved so that expensive worktrees, volumes,
backups, caches, and shared storage are reconciled without an indefinite wait.

**Why this priority**: Safe cleanup depends on complete enough evidence, and the
live incident showed that ordinary engine summaries and broad directory walks
can hang or omit privately mounted data.

**Independent Test**: Scan a host containing a deliberately slow dependency
tree, an unmeasurable category, a mounted persistent volume, and an unmounted
managed worktree. Verify visible progress, bounded category work, correct
classifications, and an explicit partial result for the unmeasured category.

**Acceptance Scenarios**:

1. **Given** a host with expensive storage categories, **When** an operator
   requests a thorough scan with a time budget, **Then** progress identifies the
   current category and the scan completes or returns a useful partial result
   within that budget.
2. **Given** a category exceeds its measurement budget, **When** the scan
   continues, **Then** the category is marked timed out or unavailable, its
   bytes remain unknown, and overall confidence is reduced rather than treating
   the category as empty.
3. **Given** resources change while a scan is running, **When** the final report
   is assembled, **Then** the result identifies material drift or uncertainty
   and does not claim exact reconciliation.
4. **Given** a managed volume or worktree is mounted or referenced by a live
   resource, **When** it is reconciled, **Then** it is classified as active or
   retained and never as a stale cleanup candidate.

---

### User Story 3 - Review a Safe Cache Cleanup Plan (Priority: P2)

As an operator, I can request a no-write cleanup plan for disposable caches so
that I can review exactly what would be removed and what would be preserved
before authorizing any mutation.

**Why this priority**: Planning converts monitoring into an actionable result
while retaining the default read-only safety boundary.

**Independent Test**: Generate a cache cleanup plan on a host with unused
disposable cache, a running container, a named persistent volume, host logs,
and an unmanaged cache. Verify that only eligible managed cache appears in the
plan, all protected resources appear in exclusions, and measured storage is
unchanged.

**Acceptance Scenarios**:

1. **Given** unused managed images, build data, stopped temporary containers,
   unused managed networks, download cache, or expired job artifacts, **When**
   an operator requests a safe cache cleanup plan, **Then** each eligible item
   is listed with its cleanup class, owner, evidence, and estimated bytes.
2. **Given** running containers, named persistent volumes, current backups,
   retained job artifacts, host logs, package caches, or unmanaged data,
   **When** a cache plan is generated, **Then** those resources are excluded
   with a reason.
3. **Given** a cleanup plan is generated, **When** the operator compares host
   state before and after planning, **Then** no resource has been deleted,
   stopped, restarted, or modified.
4. **Given** one or more relevant categories are unverified, **When** a plan is
   generated, **Then** those categories are excluded rather than assumed safe.

---

### User Story 4 - Execute Confirmed Safe Cache Cleanup (Priority: P2)

As an operator, I can explicitly confirm a current safe-cache plan so that
disposable managed cache is reclaimed while active and persistent resources
remain protected.

**Why this priority**: This delivers routine reclamation for the lowest-risk
resource classes after the operator has reviewed the scope.

**Independent Test**: Confirm a plan containing disposable cache while
simultaneously making one planned item active. Verify that the newly active
item is skipped, other eligible items are processed, persistent volumes and
running containers remain intact, and actual reclaimed bytes are reported.

**Acceptance Scenarios**:

1. **Given** a current reviewed plan, **When** an authorized operator explicitly
   confirms it, **Then** only listed items that still pass eligibility checks
   are removed.
2. **Given** an operator attempts cleanup without confirmation or with an
   expired or mismatched plan, **When** execution is requested, **Then** cleanup
   is refused and zero bytes are intentionally reclaimed.
3. **Given** a planned resource becomes active before its turn, **When** cleanup
   revalidates it, **Then** that resource is skipped and the reason appears in
   the outcome.
4. **Given** some eligible items fail to be removed, **When** cleanup finishes,
   **Then** successful, skipped, and failed items are distinguished and final
   host capacity is reported without claiming full success.

---

### User Story 5 - Remove Proven Stale Managed Resources (Priority: P3)

As a host administrator, I can separately plan and confirm removal of stale
Sandbox-owned worktrees or persistent-looking volumes so that abandoned
resources are reclaimable without weakening ordinary cache protections.

**Why this priority**: These resources represented most of the live storage
incident, but require stronger ownership and non-use evidence than cache.

**Independent Test**: Prepare one positively owned and unreferenced managed
worktree, one positively owned and unmounted managed volume, one ambiguous
volume, and one permanent host source. Verify that only the first two can enter
a stale-resource plan and that execution rechecks both before removal.

**Acceptance Scenarios**:

1. **Given** an unmounted worktree or volume with positive Sandbox ownership and
   no registry, live runtime, retained job, backup, or permanent-host reference,
   **When** stale-resource planning is requested, **Then** it may be listed as a
   candidate with all eligibility evidence.
2. **Given** an old or dangling resource whose ownership is ambiguous, **When**
   stale-resource planning is requested, **Then** it remains unverified and is
   excluded regardless of its name or age.
3. **Given** a named volume appears in an ordinary cache cleanup request,
   **When** eligibility is evaluated, **Then** it is excluded and can only be
   considered through the separate stale-resource flow.
4. **Given** a stale-resource plan has been confirmed, **When** a candidate
   becomes referenced or mounted before deletion, **Then** it is preserved and
   reported as skipped.

---

### User Story 6 - Use Equivalent Automated Monitoring (Priority: P3)

As an automation client, I can request the same monitoring, planning, and
cleanup capabilities available to interactive operators so that maintenance
can be inspected and orchestrated without losing safety or outcome detail.

**Why this priority**: Sandbox exposes both interactive and automation
surfaces, and operational policy requires them to agree.

**Independent Test**: Submit materially equivalent requests through both
supported surfaces against the same stable fixture. Verify equivalent targets,
classifications, plan contents, confirmation protections, partial-result
semantics, and outcomes.

**Acceptance Scenarios**:

1. **Given** equivalent monitoring requests against a stable target, **When**
   interactive and automation clients receive results, **Then** capacity,
   classifications, exclusions, confidence, and error semantics materially
   agree.
2. **Given** an automation client requests mutation without the required
   confirmation, **When** the request is evaluated, **Then** it is refused with
   the same zero-mutation protection as an interactive request.
3. **Given** a completed cleanup, **When** the automation client reads the
   result, **Then** it can distinguish planned, removed, skipped, failed, and
   remaining resources without access to secrets or file contents.

### Edge Cases

- The target runs no container engine or the engine is installed but
  unavailable.
- The named remote is unknown, unreachable, disconnects mid-scan, or
  reconnects to a different host identity.
- The caller can read host capacity but lacks privileges for one or more
  detailed categories.
- The host has zero free space, capacity changes rapidly, or a measurement
  itself needs temporary working space.
- A directory contains millions of entries, cyclic links, inaccessible paths,
  sparse files, hard links, shared layers, or private mounts.
- Category totals overlap or do not reconcile with host-used bytes because
  resources are shared or changed during measurement.
- A resource is created, mounted, unmounted, renamed, or deleted during a scan
  or between plan and execution.
- A cleanup candidate has Sandbox-like naming but no authoritative ownership
  evidence.
- A current backup uses a temporary-looking directory, or a stale-looking
  directory is still protected by retention policy.
- A plan is confirmed twice, is older than its validity window, belongs to
  another target, or references resources that no longer exist.
- Cleanup succeeds for some candidates and fails or times out for others.
- Human-readable unit rounding would otherwise make category sums appear
  inconsistent.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The product MUST provide read-only resource monitoring for the
  current machine and for an explicitly named configured remote.
- **FR-002**: A remote operation MUST resolve and report the exact target before
  measurement or planning and MUST fail without mutation when the target is
  unknown, ambiguous, or unreachable.
- **FR-003**: Every storage result MUST identify the target, scan mode, start
  and completion times, host total bytes, used bytes, available bytes, and the
  measurement's completeness state.
- **FR-004**: Monitoring MUST report attributed, unknown, and estimated
  reclaimable bytes separately and MUST NOT silently present partial category
  sums as complete host attribution.
- **FR-005**: Monitoring MUST discover and report material Sandbox deployment
  copies, runtime state, instance and workspace storage, engine volumes and
  cache classes, job artifacts, download caches, snapshots, backups and
  staging, and Sandbox-owned logs or package caches where present.
- **FR-006**: Monitoring MUST make material unmanaged or unattributed host
  categories visible without inspecting or exposing their file contents.
- **FR-007**: Every discovered resource MUST be classified as active,
  retained, disposable cache, stale candidate, unverified, or unmanaged.
- **FR-008**: Every managed classification MUST identify its owner or explicitly
  state that ownership could not be established; names or age alone MUST NOT
  establish ownership.
- **FR-009**: Resource entries MUST report measured bytes or an explicit
  unavailable, timed-out, or not-measured state; an unsuccessful measurement
  MUST NOT be represented as zero bytes.
- **FR-010**: Resource entries MUST include the evidence quality used for
  ownership, liveness, retention, and reclaimability decisions.
- **FR-011**: Fast monitoring MUST return a useful bounded result without
  requiring an expensive traversal of every resource.
- **FR-012**: Thorough monitoring MUST accept an overall time budget, show
  category-level progress, bound work for each category, and return completed
  observations when the budget is exhausted.
- **FR-013**: Monitoring MUST identify material drift or reduce confidence when
  concurrent changes prevent exact reconciliation.
- **FR-014**: Monitoring MUST order or summarize results so an operator can
  identify the largest measured owners, categories, and reclaimable candidates.
- **FR-015**: The product MUST provide a no-write safe-cache cleanup plan that
  lists exact candidates, owners, eligibility evidence, exclusions, and
  estimated reclaimable bytes.
- **FR-016**: Safe-cache eligibility MUST be limited to unused, Sandbox-managed
  disposable cache classes, including eligible engine cache, stopped temporary
  containers, unused managed networks, download cache, and expired job
  artifacts.
- **FR-017**: Ordinary safe-cache cleanup MUST exclude running containers,
  named persistent volumes, current or retained backups, retained job
  artifacts, permanent host deployments, user source repositories, host logs,
  host package caches, and unmanaged resources.
- **FR-018**: Planning MUST cause no deletion, stop, restart, content change, or
  other mutation of measured resources.
- **FR-019**: A cleanup plan MUST identify its target and validity window and
  MUST be unusable for another target or after it expires.
- **FR-020**: Any cleanup execution MUST require explicit confirmation of a
  current matching plan; missing, expired, replayed, or mismatched confirmation
  MUST cause zero intentional mutations.
- **FR-021**: Cleanup MUST revalidate each candidate's ownership, liveness,
  retention, and target immediately before acting on it.
- **FR-022**: Cleanup MUST skip any candidate whose evidence changed, became
  incomplete, or no longer proves eligibility.
- **FR-023**: Cleanup MUST be safe to repeat; rerunning a completed or partially
  completed cleanup MUST preserve protected resources and report already
  absent candidates without treating them as failures requiring unsafe action.
- **FR-024**: The product MUST provide a stale managed-resource plan that is
  distinct from ordinary cache cleanup.
- **FR-025**: A worktree or persistent-looking volume MUST enter a stale
  managed-resource plan only when positive Sandbox ownership is established
  and no registry, live runtime, retained job, backup, or permanent-host
  reference protects it.
- **FR-026**: Named persistent volumes MUST never enter ordinary cache cleanup
  and MUST remain excluded from stale-resource cleanup when ownership or
  non-use evidence is ambiguous.
- **FR-027**: Confirmed stale-resource cleanup MUST apply the same plan matching,
  expiration, per-candidate revalidation, idempotency, and partial-failure
  protections as safe-cache cleanup.
- **FR-028**: Cleanup results MUST report planned bytes, observed reclaimed
  bytes, final host capacity, and itemized removed, skipped, failed, timed-out,
  and already-absent outcomes.
- **FR-029**: When final capacity does not reconcile with itemized outcomes,
  the result MUST disclose drift, shared-storage effects, rounding, or unknown
  categories rather than asserting an exact reclaimed total.
- **FR-030**: Human-readable and automation results MUST provide materially
  equivalent target scope, classifications, safeguards, completeness,
  confidence, exclusions, and outcome details.
- **FR-031**: Monitoring and cleanup MUST expose errors as bounded per-category
  or per-item outcomes whenever useful work can continue safely.
- **FR-032**: Cleanup MUST refuse to act on any category for which required
  ownership, liveness, retention, or target evidence is unavailable.
- **FR-033**: Reports MUST exclude credentials, secret values, sensitive mount
  options, file contents, and direct personal identifiers.
- **FR-034**: Each confirmed cleanup MUST leave an operator-readable record
  containing the target, plan identity, timestamps, non-secret candidate
  identifiers, decisions, and outcomes.
- **FR-035**: Host logs and package caches MUST remain monitoring-only unless a
  specific resource has positive Sandbox ownership and satisfies every
  applicable cleanup safeguard.

### Key Entities

- **Storage Target**: The local machine or explicitly named remote being
  measured; includes stable target identity and capacity observations.
- **Storage Scan**: One fast or thorough read-only measurement; includes time
  budget, timestamps, completeness, confidence, drift, category progress, and
  errors.
- **Resource Observation**: A discovered worktree, volume, cache, artifact,
  backup, log group, or other category; includes size state, owner,
  classification, age when known, references, and evidence quality.
- **Resource Owner**: The project, host, instance, workspace, job, backup, or
  other managed lifecycle responsible for a resource.
- **Cleanup Candidate**: A resource observation that satisfies the eligibility
  policy for either safe-cache or stale managed-resource cleanup.
- **Cleanup Plan**: A target-bound, time-limited, no-write proposal containing
  candidates, exclusions, evidence, and estimated reclaimable bytes.
- **Cleanup Run**: A confirmed attempt to execute one plan; includes
  revalidation decisions, per-item outcomes, capacity before and after, and
  partial-failure state.

## Dependencies and Constraints

- Managed ownership depends on the existing Sandbox registry and lifecycle
  records; current use depends on live runtime state.
- Remote operations depend on an existing configured remote connection, a
  provisioned Sandbox runtime, and sufficient non-interactive measurement
  privileges.
- Existing instance, workspace, job, deployment, preview, backup, and retention
  behavior remains authoritative and is not redesigned by this feature.
- Existing local and remote instances, permanent hosts, custom deployments, and
  non-Sandbox workloads must remain protected and compatible.
- Storage measurement and cleanup must remain bounded and cancellable.
- Runtime-changing behavior must be available through the same supported
  Sandbox product surfaces as monitoring and planning.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On healthy targets, at least 95% of fast status requests return
  host capacity, the largest measured categories, and completeness information
  within 15 seconds.
- **SC-002**: Every thorough scan completes or returns a useful partial result
  within the operator-selected overall time budget, with 100% of timed-out or
  unavailable categories explicitly identified.
- **SC-003**: For every discovered managed worktree and named volume in the
  acceptance fixture, the result provides a lifecycle classification and
  either an evidence-backed owner or an explicit ownership gap.
- **SC-004**: Reported host total bytes equal used plus available and reserved
  or otherwise unavailable capacity within the host's reported accounting
  precision; any category reconciliation gap is explicitly quantified.
- **SC-005**: Across the protected-resource acceptance matrix, planning and
  unconfirmed cleanup produce zero deletions, stops, restarts, or content
  changes.
- **SC-006**: Across the protected-resource acceptance matrix, confirmed cache
  cleanup preserves 100% of running containers, named persistent volumes,
  retained backups, permanent host deployments, and unmanaged resources.
- **SC-007**: In concurrency tests, 100% of planned candidates that become
  active or lose eligibility before deletion are skipped and reported.
- **SC-008**: A completed cleanup reports capacity before and after and assigns
  every planned candidate exactly one observable outcome: removed, skipped,
  failed, timed out, or already absent.
- **SC-009**: Equivalent interactive and automation requests against a stable
  fixture agree on 100% of target identity, candidate membership,
  classifications, exclusions, confirmation decisions, and terminal outcomes.
- **SC-010**: No monitoring, planning, cleanup, or audit result exposes a
  credential, secret value, sensitive mount option, or file content in the
  security acceptance corpus.
- **SC-011**: On the storage-incident fixture represented by the July 2026
  audit, a thorough scan identifies the abandoned dependency volumes and
  unmounted deployment worktrees as the two largest reclaimable managed
  categories without classifying active permanent data as reclaimable.

## Assumptions

- Operators already have authority to inspect the selected host; this feature
  does not create a new authentication or authorization system.
- A named remote must already be configured through Sandbox before it can be
  monitored or cleaned.
- Live runtime state is authoritative for current use, while Sandbox lifecycle
  records are authoritative for managed ownership and retention.
- Historical resources without sufficient ownership metadata remain unverified
  even if their names resemble managed resources.
- Named volumes are persistent by default and require the stronger
  stale-resource flow.
- Existing backup and job-retention policies determine whether related
  resources are current, retained, expired, or abandoned.
- Operators prefer a missed cleanup opportunity over deletion of ambiguous or
  permanent data.
- Reports retain raw byte values for reconciliation; human-readable units are
  presentation aids and may be rounded.
- A plan validity window is short enough to limit stale evidence and is always
  followed by per-candidate revalidation.

## Convergence amendment — 2026-08-13 (27-feedback network lifecycle)

This dated section tightens the read-only network accounting and lifecycle
boundary without authorizing broad Docker pruning. It maps
`a813480b`, `bf05eeb9`, `0fac3b07`, `822b9323`, `78aaf583`, and the shared
`6bc4c6d5` decoder issue (whose canonical contract lives in Spec 032).

### Normative requirements

- **FR-012**: Network observations MUST use one lifecycle model with stable
  identity, owner evidence, active references, workspace/job references,
  allocation state, and last observation. Allocation, reconciliation, planning,
  and cleanup MUST consume that model rather than independently guessing from a
  name (`a813480b`).
- **FR-013**: Create/stop/destroy/recreate cycles MUST be idempotent and bounded:
  a stopped or destroyed Sandbox-owned workspace releases only its own network
  after no active lease/container/job reference remains; repeated cycles MUST not
  create orphan or duplicate allocations (`bf05eeb9`).
- **FR-014**: Active, foreign, unattributed, or indeterminate networks MUST be
  excluded from cleanup. A candidate can be planned only with positive Sandbox
  ownership plus current inactive evidence; the plan/apply path MUST never delete
  an active or foreign network (`0fac3b07`).
- **FR-015**: Address-pool exhaustion or allocation collision MUST produce a
  structured capacity/unavailable result containing bounded observations and a
  recovery hint. It MUST not auto-delete networks, retry indefinitely, or claim
  that disk capacity remediation solved address exhaustion (`822b9323`).
- **FR-016**: A remote inventory timeout, stale control record, or missing
  observation MUST become `partial`/`unavailable` evidence with a bounded error;
  it MUST not leak a traceback or turn missing evidence into a deletion
  candidate. A subsequent rescan is required before any plan (`78aaf583`).
- **FR-017**: Resource-monitoring consumers MUST use the Spec 032 feature-owned
  top-level job-list decoder and MUST reject malformed or nested `.data` shapes;
  they MUST not add a second parser (`6bc4c6d5`).

### Acceptance evidence required before closing this amendment

The fixture matrix MUST cover constrained-pool allocation, repeated
create/stop/destroy, active and foreign networks, collision, exhaustion and
recovery, remote observation timeout, and top-level job-list decoding. All
checks are read-only unless an already-authorized exact cleanup plan is under
test; no broad prune or automatic retention deletion is implied.

## Convergence amendment — 2026-08-13 (workspace index ownership projection)

Resource monitoring must remain a typed consumer of durable workspace ownership. This
amendment closes the workspace metadata/index boundary without making resource monitoring
an owner of workspace migration or authorizing network cleanup.

### Normative requirements

- **WM-FR-001**: Workspace resources MUST be attributed through a typed projection keyed
  by opaque `workspace_id` and `project_identity`; resource providers MUST NOT open
  `$SANDBOX_HOME/runtime/workspaces/index.sqlite3` or legacy `workspace.json` files.
- **WM-FR-002**: A projection MUST include workspace label, owner kind, lifecycle state,
  alias evidence, active lease/container/job references, locator/evidence digests, and
  observation time. Paths and names alone MUST NOT establish ownership.
- **WM-FR-003**: Missing, unresolved, conflicting, duplicate, stale, or generation-drifted
  workspace bindings MUST produce explicit unknown/indeterminate evidence and zero
  reclaimable bytes; an empty/incomplete workspace index MUST surface
  `workspace_index_incomplete` rather than an empty-success resource status.
- **WM-FR-004**: Resource status, plan, and apply MUST consume one projection and one
  lifecycle model. Alias collisions, duplicate owner bindings, active references, and
  foreign/unknown networks MUST remain exclusions across rescans; monitoring MUST not
  repair ownership by guessing or by mutating workspace metadata.
- **WM-FR-005**: Workspace metadata migration or base relocation MUST be observable as
  metadata-only. It MUST not change network, container, job, volume, upload, snapshot, or
  project-file counts and MUST not create a cleanup candidate merely because a locator
  moved.
- **WM-FR-006**: Remote resource status MUST obtain the projection through the supported
  workspace/job service and strict top-level job-list decoder. A remote timeout, stale
  generation, or unavailable index yields bounded partial evidence and requires a fresh
  rescan before planning or apply.

### Acceptance evidence required before closing this amendment

Fixtures MUST cover complete, missing, unresolved, conflicting, duplicate, stale, and
relocated workspace bindings; active/foreign/indeterminate network references; and remote
partial results. Checks must prove no direct SQLite/legacy JSON consumer and unchanged
resource counts across a metadata-only migration. No broad prune, network release, reset,
or destroy is implied.
