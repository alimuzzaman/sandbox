# Feature Specification: One-Click Host Storage Reclamation

**Feature Branch**: `latest` (spec directory `042-host-storage-reclamation`)

**Created**: 2026-08-16

**Status**: Draft

**Input**: User description: "One-click, tiered, safety-gated reclamation of remote sandbox host storage: a full categorised resources status inventory (LIVE/STOPPED/REGONLY/BASE/ORPHAN deploy-src classes), a side-effect-free tiered cleanup plan with per-candidate reasons, a resumable idempotent cleanup that writes a durable deletion manifest before deleting and works on a 100% full disk, plus workspace release / TTL / reaper commands with a 7-day default TTL, deny-by-default docker volume protection, hosted-site protection, root-owned file handling, activity-based liveness, registry reconciliation, growth exclusion, and free-space threshold alerting."

## Context

On 2026-08-16 the remote host `scaleway-sandbox` reached 97% full (768 MB free on a 193 GB
disk). An operator classified all 178 deployment directories by hand, deleted ~110 GiB of
abandoned agent workspaces, one-shot base repositories, orphaned per-workspace
`node_modules` volumes, and unused caches, and took the host from 8.4 GB free to 118 GB
free. Every step of that classification and deletion was manual, undocumented, and one
mistaken command away from destroying live site data: a blanket volume prune would have
removed four volumes that read as dangling while holding production databases and uploads.

This feature turns that manual pass into a repeatable, evidence-backed, safety-gated
capability, and adds the retention policy that stops the accumulation from recurring.

## Clarifications

### Session 2026-08-16

Resolved from the originating task brief and the 2026-08-16 audit note rather than an
interactive exchange; no interactive channel was available during this pass. Each answer is
traceable to the source stated with it.

- Q: What exactly makes a workspace "in use"? → A: an active (non-terminal) job binding, an
  explicit retention lease that has not expired, or filesystem activity newer than the
  retention window. A running container alone does not qualify. (Brief, rule (d): nine
  speckit workspaces held 28.8 GiB behind idle keepalive processes.)
- Q: Which volumes may ever be deleted? → A: only volumes whose name matches the
  workspace-scoped disposable pattern `sandbox-<workspace-dir>_*node-modules*` where
  `<workspace-dir>` is itself a reclaim candidate. Everything else is protected at every
  tier. (Brief, rule (a): `lenzora-postgres-data`, `sandbox-amarsonar-bangla-public_wordpress-db`,
  `wordpress-uploads`, `lenzora-storage` all read as dangling while holding live data.)
- Q: What do the three tiers contain? → A: `safe` = ORPHAN workspaces, released workspaces,
  expired workspaces, and their workspace-scoped volumes; `tmp` = `safe` plus disposable
  caches and scratch directories; `all` = `tmp` plus STOPPED workspaces and expired one-shot
  BASE deployments. Tiers are strictly nested. (Brief: the manual pass deleted in exactly
  this order of increasing risk.)
- Q: What is the default retention window? → A: 7 days for workspaces and 7 days for
  one-shot base deployments. (Brief, requirement 6; supersedes the 3-day workspace
  suggestion in the 2026-08-16 audit note.)
- Q: Where does the deletion manifest live and who may read it? → A: append-only JSON Lines
  under the target host's sandbox runtime state directory, owner-only permissions, one file
  per run plus a stable index, written and flushed before each removal. (Brief,
  requirement 3; matches the existing owner-only cleanup-receipt convention.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See the whole host in one command (Priority: P1)

An operator or agent notices a remote host is filling up. They run one status command and
receive the complete picture: capacity, container-engine inventory, engine content stores,
host filesystem roots, and every deployment directory broken down by lifecycle class with
size, last-modification time, and per-class totals.

**Why this priority**: Nothing else is safe without it. The 2026-08-16 audit needed two
hours of ad-hoc shell because the existing report could not attribute 166 GiB of 185 GiB
used. Classification is the prerequisite for every reclaim decision.

**Independent Test**: Run the status command against the real remote read-only and confirm
each deployment directory appears exactly once with a class, a size, an age, and a reason,
and that per-class totals sum to the measured deployment root.

**Acceptance Scenarios**:

1. **Given** a remote host with deployment directories in mixed states, **When** the
   operator asks for status, **Then** each directory is reported in exactly one of the
   classes LIVE, STOPPED, REGISTRY-ONLY, BASE, ORPHAN, or PROTECTED, with size, age, and
   the evidence that produced the class.
2. **Given** a host whose directory index is cached, **When** the operator asks for the
   fast variant, **Then** capacity and the full classification are returned from the cache
   without walking the disk, and the cache age and completeness are stated.
3. **Given** a walk that could not finish inside its budget, **When** results are reported,
   **Then** the report states how many entries were measured, how many were not, and that
   the result is partial — it never presents a truncated total as complete.
4. **Given** an index that lists workspaces that no longer exist on disk (or omits ones that
   do), **When** status runs, **Then** the drift is reported in both directions with counts.

---

### User Story 2 - Preview exactly what would be deleted (Priority: P1)

Before deleting anything, the operator asks for a plan at a chosen tier. The plan lists
every candidate with its path, size, last-modification time, class, and the specific reason
it qualifies; totals per tier; and an explicit list of what is being deliberately skipped
and why. Running a plan changes nothing on the host.

**Why this priority**: The plan is the safety review. It is also the only artifact that can
be checked against the real host before any destructive capability is trusted.

**Independent Test**: Run the plan against the real remote and confirm free space, file
count, and directory listing are byte-identical before and after.

**Acceptance Scenarios**:

1. **Given** any tier, **When** a plan is requested, **Then** no file, container, volume, or
   index entry on the host is created, modified, or removed.
2. **Given** a candidate directory, **When** it appears in a plan, **Then** the plan states
   its path, byte size, modification time, class, tier, and reason string.
3. **Given** resources that are protected, **When** a plan is requested, **Then** they are
   listed under skipped-with-reason rather than silently omitted.
4. **Given** a plan at the safest tier, **When** compared with a plan at the broadest tier,
   **Then** the safest tier's candidate set is a strict subset of the broadest tier's.

---

### User Story 3 - Reclaim the space, with an answerable record (Priority: P1)

The operator executes a previewed plan. Deletion is tiered, resumable after an interruption,
and idempotent on re-run. Before anything is removed, a durable manifest records every
intended deletion with path, bytes, class, reason, and timestamp, so "what happened to X"
is answerable afterwards. Execution must succeed on a disk that is already 100% full.

**Why this priority**: This is the actual one-click outcome the feature exists to deliver.

**Independent Test**: On a fixture host, fill the disk, execute the plan, kill it mid-run,
re-run it, and confirm no double deletion, a complete manifest covering both runs, and a
final state matching the plan.

**Acceptance Scenarios**:

1. **Given** an execution about to begin, **When** the first deletion is attempted, **Then**
   a manifest entry for it already exists durably outside the storage being reclaimed.
2. **Given** an interrupted execution, **When** it is re-run with the same plan, **Then**
   already-removed candidates are reported as already-absent rather than failed, and the
   remaining candidates are processed.
3. **Given** a candidate that cannot be fully removed (for example a subtree owned by
   another user), **When** the removal is attempted, **Then** the outcome is reported as a
   failure or partial removal with the reason — never as success.
4. **Given** a disk with zero free bytes, **When** execution runs, **Then** it completes
   without requiring a writable scratch file it cannot create.
5. **Given** an execution has finished, **When** the operator asks what happened to a path,
   **Then** the manifest answers with the bytes, class, reason, and time of its removal.

---

### User Story 4 - An agent declares it is finished with a workspace (Priority: P2)

An agent that created a workspace for a task marks it reclaimable the moment the task ends,
instead of leaving it to age out. An agent that needs a workspace for longer sets or extends
its own expiry.

**Why this priority**: Explicit release converts most of the accumulation into immediately
reclaimable space, and removes the need to guess.

**Independent Test**: Release a workspace, then run the plan and confirm it appears as an
immediately reclaimable candidate with reason "released".

**Acceptance Scenarios**:

1. **Given** a workspace an agent owns, **When** the agent releases it, **Then** it becomes
   immediately reclaimable regardless of its age.
2. **Given** a workspace with a default expiry, **When** the agent extends the expiry,
   **Then** the new expiry is recorded and the workspace is not reclaimed before it.
3. **Given** a released workspace that is still bound by an active job, **When** reclamation
   runs, **Then** it is skipped with an in-use reason.
4. **Given** a name that does not identify a workspace, **When** release or expiry is
   requested, **Then** the request is refused with a clear reason and nothing is changed.

---

### User Story 5 - Expired workspaces are reclaimed automatically (Priority: P2)

A reaper reclaims workspaces and one-shot base deployments whose retention window has
expired and which are not in use, with a dry-run mode that reports what it would do.

**Why this priority**: Without retention the accumulation returns; the manual audit found
108 orphaned workspaces and 37 one-shot base repositories.

**Independent Test**: Age a fixture workspace past its window and confirm dry-run lists it
and a real run reclaims it, while an in-use workspace of the same age is skipped.

**Acceptance Scenarios**:

1. **Given** a workspace older than its retention window with no active use, **When** the
   reaper runs, **Then** it is reclaimed and recorded in the manifest.
2. **Given** a workspace older than its window that is in use, **When** the reaper runs,
   **Then** it is skipped with an in-use reason.
3. **Given** dry-run mode, **When** the reaper runs, **Then** nothing is changed and the
   would-be actions are listed.

---

### User Story 6 - Warn before the host fills again (Priority: P3)

When free space falls below a warning threshold, the status report says so prominently. The
host can be configured to run the safest reclaim tier automatically at a lower threshold.

**Why this priority**: The host reached 768 MB free before anyone noticed.

**Independent Test**: Report a synthetic capacity below the threshold and confirm the
warning and its numbers appear.

**Acceptance Scenarios**:

1. **Given** free space below the warning threshold, **When** status is reported, **Then**
   the report states the level, the free share, and the threshold that was crossed.
2. **Given** automatic reclamation is enabled and the trigger threshold is crossed, **When**
   status runs, **Then** only the safest tier is eligible to run, and it is recorded.
3. **Given** automatic reclamation is not enabled, **When** any threshold is crossed,
   **Then** nothing is deleted.

---

### Edge Cases

- A volume that is unused by any container but holds live site data (databases, uploads,
  object storage) must never be eligible, at any tier.
- A deployment directory belonging to a registered hosted site must never be eligible, at
  any tier, including when its container is stopped.
- A container that exists and is "running" but is only an idle keepalive process must not
  make its workspace immortal.
- A candidate whose content is still being written must be excluded, and the exclusion must
  be visible in the plan.
- The index and the disk disagree in both directions; neither alone may be treated as truth.
- A subtree owned by a different user (created by a container running as root) must be
  either removed with elevation or reported as not removed.
- The manifest must survive the machine being out of disk space and the run being killed.
- Two reclamation runs must not be able to delete the same path twice or interleave
  destructively.

## Requirements *(mandatory)*

### Functional Requirements

#### Inventory and classification

- **FR-001**: The status report MUST cover, in one invocation: capacity, container-engine
  images/containers/volumes/build cache, engine content stores, host filesystem roots, and
  every entry of the managed deployment root.
- **FR-002**: Every deployment-root entry MUST be assigned exactly one lifecycle class:
  PROTECTED, LIVE, STOPPED, REGISTRY-ONLY, BASE, or ORPHAN.
- **FR-003**: Each entry MUST carry its size, last-modification time, age, class, and the
  evidence strings that produced its class; each class MUST carry an entry count and a byte
  total.
- **FR-004**: The report MUST state index-versus-disk drift in both directions, with counts
  of indexed-but-absent and present-but-unindexed entries.
- **FR-005**: When any measurement is bounded, truncated, or unavailable, the report MUST
  say so with the measured byte total, the measured count, and the unmeasured count. It MUST
  NOT present a bounded result as complete.
- **FR-006**: The report MUST reuse the existing cached host directory index and its
  fast/refresh modes rather than introducing a second measurement path.

#### Planning

- **FR-007**: A plan MUST be produced for a named tier and MUST have no side effects on the
  target host.
- **FR-008**: A plan MUST list, per candidate: path or identifier, byte size, modification
  time, class, tier, and a reason string explaining why it qualifies.
- **FR-009**: A plan MUST report per-tier candidate counts and byte totals, and MUST list
  what it deliberately skipped, each with a reason.
- **FR-010**: Tiers MUST be strictly nested: every candidate of a safer tier MUST also be a
  candidate of any broader tier.
- **FR-011**: A plan MUST be referable by an identifier so that execution acts on the
  reviewed candidate set rather than re-deciding at execution time.

#### Execution

- **FR-012**: Execution MUST require explicit confirmation and MUST refuse without it.
- **FR-013**: Before the first removal, and before each subsequent removal, execution MUST
  durably record a manifest entry containing path or identifier, bytes, class, reason, and
  timestamp.
- **FR-014**: The manifest MUST be written outside the storage being reclaimed, MUST be
  append-only, MUST be readable after the run, and MUST be flushed to durable storage before
  the corresponding removal is attempted.
- **FR-015**: Execution MUST be resumable: re-running the same plan after an interruption
  MUST process remaining candidates and MUST report already-removed candidates as
  already-absent.
- **FR-016**: Execution MUST be idempotent: a second complete run of the same plan MUST make
  no further changes and MUST report success with zero additional bytes.
- **FR-017**: Execution MUST function when the target filesystem has zero free bytes; it
  MUST NOT depend on creating scratch files on the full filesystem.
- **FR-018**: After removals, execution MUST reconcile the workspace index and the instance
  registry so that neither retains records for removed storage.
- **FR-019**: A removal that only partially succeeded MUST be reported as failed or partial,
  with its reason, and MUST NOT be counted as reclaimed bytes.
- **FR-020**: Concurrent executions against the same host MUST be serialized or refused; the
  same candidate MUST NOT be removed by two runs.

#### Safety rules (each MUST be enforced in code, not by convention)

- **FR-021**: Volume removal MUST be deny-by-default. A volume is eligible only when its
  name matches the workspace-scoped disposable pattern AND its owning workspace is itself a
  reclaim candidate. Every other volume is protected at every tier, including volumes the
  engine reports as unused.
- **FR-022**: The hosted-sites subtree of the deployment root, and any deployment directory
  or project belonging to a registered hosted site, MUST be protected at every tier.
- **FR-023**: When removal fails because of insufficient permission, execution MUST attempt
  the existing bounded elevated-removal mechanism, and MUST verify absence afterwards. If
  the path still exists, the outcome MUST be reported as not removed.
- **FR-024**: In-use MUST be defined as recent activity or an active job binding, not merely
  the existence of a process or a running container. The definition MUST be documented and a
  running container alone MUST NOT protect a candidate from an explicit release or an
  expired retention window.
- **FR-025**: A candidate whose size or modification time changed between observation and
  execution MUST be excluded, and the exclusion MUST be recorded with its reason. Growth
  detection MUST compare modification time, not two size samples alone.
- **FR-026**: The set of always-protected resources MUST be expressible and inspectable, and
  a candidate MUST be checked against it immediately before removal, not only at plan time.

#### Retention

- **FR-027**: An agent MUST be able to declare a workspace released, making it immediately
  reclaimable.
- **FR-028**: An agent MUST be able to set or extend a workspace's expiry using a duration.
- **FR-029**: The default retention window MUST be 7 days for workspaces and 7 days for
  one-shot base deployments.
- **FR-030**: The reaper MUST reclaim only candidates that are both expired and not in use,
  and MUST support a dry-run that changes nothing.
- **FR-031**: Release and expiry records MUST survive process restarts and MUST be visible in
  status and plan output as a reason.

#### Alerting

- **FR-032**: Status MUST classify disk capacity pressure and MUST warn when free space is
  below approximately 15% of total.
- **FR-033**: Automatic execution of the safest tier at a configured threshold MUST be
  supported, MUST be off by default, and MUST record every automatic run in the manifest.
- **FR-034**: Capacity pressure MUST be reported through the existing pressure-classification
  surface rather than a parallel one.

### Key Entities

- **Deployment entry**: one directory of the managed deployment root; has a name, path,
  size, modification time, lifecycle class, evidence, and optional workspace identity.
- **Lifecycle class**: PROTECTED, LIVE, STOPPED, REGISTRY-ONLY, BASE, or ORPHAN.
- **Reclaim tier**: `safe`, `tmp`, or `all`; a strictly nested selection policy over
  candidates.
- **Reclaim candidate**: a resource selected by a tier, carrying path/identifier, bytes,
  modification time, class, tier, and reason.
- **Protection rule**: a named, inspectable rule that makes a resource ineligible; carries
  the reason reported in skipped output.
- **Deletion manifest**: an append-only durable record of intended and actual removals with
  path, bytes, class, reason, timestamp, run identifier, and outcome.
- **Workspace lease**: a per-workspace retention record with an expiry and an optional
  released marker.
- **Capacity pressure**: a level plus the free share and threshold that produced it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single status invocation classifies 100% of deployment-root entries, with no
  entry left unclassified and no entry in more than one class.
- **SC-002**: Running the plan against a real host leaves free space, entry count, and index
  contents unchanged.
- **SC-003**: The manual 2026-08-16 classification is reproduced: given the same host state,
  the tool's classes match the operator's LIVE/STOPPED/REGISTRY-ONLY/BASE/ORPHAN assignment
  for every entry.
- **SC-004**: No volume outside the workspace-scoped disposable pattern appears as a
  candidate at any tier, verified against the four live-data volumes that read as unused.
- **SC-005**: No hosted-site path appears as a candidate at any tier.
- **SC-006**: An interrupted execution followed by a re-run removes each candidate exactly
  once and produces one manifest entry per candidate per attempt.
- **SC-007**: A partially-removed candidate is never reported as reclaimed.
- **SC-008**: An idle-but-running keepalive container does not prevent reclamation of an
  expired or released workspace.
- **SC-009**: Free space below the warning threshold produces a warning that states the free
  share and the threshold.
- **SC-010**: Every reclaimed path can be traced afterwards to a manifest entry naming its
  bytes, class, reason, and time.

## Assumptions

- The remote probe program is shipped from the operator's machine over the existing remote
  transport, so status, plan, and execution do not require the host's own copy of the
  runtime to be updated. Only the host-executed workspace control commands
  (`workspace list/status/...` over SSH) depend on the host runtime version.
- Elevated removal uses the host's existing passwordless, bounded elevation, already relied
  on for measurement. Where elevation is unavailable, removal reports not-removed rather
  than partially succeeding.
- "One-shot base deployment" means a deployment directory that is not a workspace, has no
  registered instance, no hosted site, and no active job — the class the audit called BASE
  with no consumer.
- Retention windows are expressed as durations such as `2h` or `14d`.
- The manifest lives under the sandbox runtime state directory on the target host and is
  owner-readable only; it is a durable local log, not a shipped artifact.
- The warning threshold is 15% free by default and the automatic-run threshold, when
  enabled, is lower than the warning threshold.
- Existing scope-based plans (`cache`, `stale`) remain supported; tiers are an additional,
  explicitly requested mode.

## Out of Scope

- Fixing the two structural causes of the accumulation (per-workspace package-store
  duplication and full `.git` copies per workspace). Those are separate features; this one
  reclaims and retains.
- Reclaiming anything on the operator's local machine beyond the same commands' local
  target.
- Any transfer, archival, or backup of reclaimed data. Reclamation deletes.
