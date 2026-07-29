# Feature Specification: Deep Disk Attribution

**Feature Branch**: `latest`

**Created**: 2026-07-29

**Status**: Draft

**Input**: User description: "Use open-source capabilities to find the 74.13 GB
of genuinely unlocated storage, then implement the capability in Sandbox."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reconcile an Unexplained Capacity Gap (Priority: P1)

As a Sandbox operator, I want a bounded, read-only attribution report for a
local or named remote host so that I can identify the largest observed storage
consumers and understand how much capacity remains unexplained.

**Why this priority**: Closing or clearly bounding the unexplained capacity gap
is the core product outcome and provides immediate value without requiring
cleanup.

**Independent Test**: Request deep attribution for a host containing known
allocated files and verify that the report ranks those consumers, states
filesystem coverage, reconciles observed allocation against used capacity, and
makes no host changes.

**Acceptance Scenarios**:

1. **Given** a filesystem with known allocated files and a previously
   unexplained capacity gap, **When** an operator requests deep attribution with
   a finite time budget, **Then** the report ranks the observed allocation
   consumers, identifies the measured filesystem boundaries, and presents a
   non-negative residual unexplained gap.
2. **Given** multiple writable local filesystems, **When** deep attribution
   runs, **Then** every filesystem is inventoried and each one is marked as
   scanned, partially scanned, or not scanned with a reason.
3. **Given** a filesystem whose allocation semantics prevent byte-exact
   ownership, **When** its results are reconciled, **Then** the report describes
   the result as observed allocation, states the relevant limitations, and does
   not claim exact physical ownership.
4. **Given** a completed deep attribution report, **When** the host is inspected
   afterward, **Then** no files, processes, packages, mounts, privileges, or
   storage settings have been changed.

---

### User Story 2 - Identify Deleted-Open Storage (Priority: P2)

As a host administrator, I want to identify deleted files whose blocks remain
in use because a process still holds them open so that directory-visible totals
no longer conceal that capacity.

**Why this priority**: Deleted-open files are a major class of storage that
ordinary directory measurement cannot locate and can directly explain an
otherwise persistent capacity gap.

**Independent Test**: Create a known-size file, keep it open, delete its
directory entry, and verify that deep attribution reports its allocated bytes
separately with safe process identity evidence.

**Acceptance Scenarios**:

1. **Given** a deleted file that remains open by a running process, **When** deep
   attribution runs with sufficient visibility, **Then** the report attributes
   its observed bytes to the applicable filesystem and process without exposing
   file contents, environment values, credentials, or secret-bearing process
   arguments.
2. **Given** several deleted-open files held by the same process, **When** the
   evidence is reported, **Then** their bytes are aggregated without counting
   the same file more than once.
3. **Given** insufficient permission to inspect one or more processes, **When**
   deep attribution runs, **Then** the report marks deleted-open coverage as
   partial, names the permission boundary, and retains the unmeasured amount in
   the residual gap.

---

### User Story 3 - Understand Container Storage Overlap (Priority: P3)

As a Sandbox operator, I want container storage reported with shared, unique,
active, and potentially reclaimable values kept distinct so that logical totals
are not mistaken for additive physical disk use.

**Why this priority**: Shared layers and overlapping cache records can
substantially inflate apparent usage and previously caused logical cleanup
estimates to differ from physical capacity changes.

**Independent Test**: Run deep attribution against a fixture containing shared
container layers and verify that unique and overlapping values are separately
labeled and that overlapping totals do not inflate capacity-accounted
attribution.

**Acceptance Scenarios**:

1. **Given** container images that share storage, **When** deep attribution
   runs, **Then** shared and unique values are reported separately and only
   non-overlapping observed allocation contributes to the capacity
   reconciliation.
2. **Given** active and inactive container resources, **When** the report is
   produced, **Then** activity and potential reclaimability are stated
   independently from physical attribution.
3. **Given** logical build or cache totals that overlap other container storage,
   **When** the final reconciliation is calculated, **Then** those totals remain
   diagnostic and are not added to physical capacity-accounted storage.

---

### User Story 4 - Receive Honest Partial Results (Priority: P4)

As an automation client, I want stable structured evidence when capabilities,
permissions, mounts, connections, or time budgets limit a scan so that
incomplete results cannot be mistaken for complete attribution.

**Why this priority**: A partial but explicit result is safer and more
actionable than a scan that fails entirely or silently understates usage.

**Independent Test**: Restrict access to a measured path or exhaust the scan
budget and verify that completed evidence is preserved, unfinished categories
are marked partial, and all unresolved bytes remain visible.

**Acceptance Scenarios**:

1. **Given** that an optional measurement capability is unavailable, **When**
   deep attribution runs, **Then** an available read-only fallback is used where
   possible and its coverage limitations are stated.
2. **Given** that neither the preferred capability nor its fallback can inspect
   a filesystem, **When** the report is produced, **Then** that filesystem is
   marked partial or not scanned and its unresolved capacity is not presented
   as attributed.
3. **Given** that the overall time budget expires, **When** the scan stops,
   **Then** completed findings are returned, every unfinished category is
   marked partial, and the result is delivered within five seconds after the
   budget expires.
4. **Given** a remote interruption after a valid partial payload is delivered,
   **When** the result is finalized, **Then** delivered evidence is retained and
   the interruption is recorded as the reason for incomplete coverage.
5. **Given** a total remote transport loss before any valid payload is
   delivered, **When** the result is finalized, **Then** the request returns an
   explicit unavailable result and does not fabricate partial evidence.

---

### User Story 5 - Review Safe Cleanup Guidance (Priority: P5)

As an operator, I want findings classified as existing managed cleanup
opportunities, manual host remediation, or non-cleanable overhead so that
attribution evidence does not authorize unsafe deletion.

**Why this priority**: Once a consumer is identified, operators need a safe next
decision while preserving existing ownership and confirmation protections.

**Independent Test**: Produce findings spanning an exact managed resource, an
unmanaged host file, a deleted-open file, and filesystem overhead; verify that
only the already eligible managed resource is associated with an existing
cleanup action.

**Acceptance Scenarios**:

1. **Given** an exact resource already eligible under an existing managed
   cleanup scope, **When** guidance is generated, **Then** the report may
   reference that existing scope and its normal confirmation requirements.
2. **Given** an unmanaged host path or deleted-open file, **When** guidance is
   generated, **Then** the report identifies it as requiring human remediation
   and does not offer automatic deletion or process termination.
3. **Given** reserved, metadata, shared, or otherwise non-cleanable storage,
   **When** guidance is generated, **Then** it is labeled as diagnostic or
   non-cleanable rather than reclaimable.
4. **Given** an active instance, permanent deployment, running job, backup, or
   unrelated workload, **When** findings are classified, **Then** the report
   does not recommend its removal based solely on size.

---

### Edge Cases

- A nested mount appears beneath a scanned directory; the parent scan must not
  cross into it silently, and the nested filesystem must receive its own
  coverage record.
- A filesystem is mounted, unmounted, or changes from writable to read-only
  during measurement.
- Capacity changes because unrelated workloads write or delete data while the
  scan is running.
- A path becomes inaccessible or disappears after discovery but before
  measurement.
- A filesystem contains hard links spanning several directory branches; one
  inode's allocated blocks must be counted once per scanned filesystem.
- Sparse, compressed, reflinked, snapshot-backed, or copy-on-write data cannot
  be assigned to one path with byte-exact physical ownership.
- Deleted-open evidence refers to a file on an unmounted, renamed, or otherwise
  unresolvable filesystem.
- Process identity or path text contains credentials, tenant identifiers,
  tokens, or sensitive arguments.
- The same storage appears in directory, container, cache, and deleted-open
  views.
- Reported observations exceed the initial used-capacity snapshot because of
  host drift or overlapping evidence.
- Existing non-interactive elevated visibility is unavailable or covers only
  some categories.
- The remote target disconnects, restarts, or exceeds its response deadline.
- A discovered writable filesystem is unrelated to Sandbox and not selected
  for deep measurement.
- Filesystem reserved or metadata usage cannot be observed separately.
- No directory-accounting capability is usable on a selected filesystem.
- A selected filesystem is empty or reports zero used capacity.
- Ranked results contain more consumers than the report's bounded result limit.
- Two scans of a live host produce materially different totals because the host
  changed between runs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow an operator to request explicit deep storage
  attribution for either the current local target or a named remote target.
- **FR-002**: Every deep attribution request MUST be read-only and MUST NOT
  install software, download executables, delete data, terminate or restart
  processes, alter mounts, change privileges, change filesystem settings, or
  invoke cleanup.
- **FR-003**: Every request MUST accept a finite overall time budget and MUST
  stop outstanding measurement when that budget is exhausted or the request is
  cancelled.
- **FR-004**: The system MUST return all completed evidence present in a valid
  partial payload when a request times out, is cancelled, is interrupted, or
  encounters an error after partial progress; a total transport loss before a
  valid payload MUST return an explicit unavailable result.
- **FR-005**: The system MUST begin each report with the target's filesystem
  capacity and mount topology as observed for that request.
- **FR-006**: The system MUST inventory every discovered writable local
  filesystem and record its capacity, mount identity, selection status, and
  coverage status.
- **FR-007**: By default, the system MUST deeply measure the root filesystem and
  any distinct filesystem containing Sandbox-managed storage,
  container-engine storage, or another known managed root.
- **FR-008**: A discovered filesystem that is not deeply measured MUST remain
  visible in the report with an explicit reason.
- **FR-009**: Directory attribution MUST measure observed allocated storage
  within one filesystem boundary at a time and MUST NOT silently cross into
  nested or virtual filesystems.
- **FR-010**: The system MUST rank the largest observed allocation consumers for
  every successfully measured filesystem.
- **FR-011**: Directory attribution MUST count the allocated blocks of a
  hard-linked file no more than once per scanned filesystem.
- **FR-012**: The system MUST state whether hard-link deduplication was
  confirmed, partial, or unavailable for each directory measurement.
- **FR-013**: The system MUST report known limitations associated with sparse
  data, compression, reflinks, snapshots, copy-on-write storage, or other
  allocation-sharing behavior and MUST avoid claiming byte-exact physical
  ownership when those limitations apply.
- **FR-014**: The system MUST use an already available read-only measurement
  capability and MUST use an available standard host fallback when the
  preferred capability is absent or incompatible.
- **FR-015**: The report MUST identify the selected measurement capability, its
  safely observable version or identity, its coverage characteristics, and its
  known limitations without requiring package installation.
- **FR-016**: If no usable directory-accounting capability exists for a
  selected filesystem, the system MUST mark that filesystem partial and
  preserve its unmeasured capacity in the residual gap.
- **FR-017**: The system MUST detect deleted files that continue to consume
  allocated storage because they remain open by a process when host visibility
  permits.
- **FR-018**: Deleted-open evidence MUST be aggregated by filesystem and process
  and MUST prevent duplicate counting of the same open file.
- **FR-019**: Deleted-open findings MUST include the observed byte count and a
  minimized process identity sufficient for remediation while excluding file
  contents, environment values, credentials, sensitive mount options, and
  secret-bearing process arguments.
- **FR-020**: The system MUST provide read-only container-storage diagnostics
  that distinguish unique, shared, active, inactive, and potentially
  reclaimable logical values.
- **FR-021**: Shared or overlapping container, image, layer, volume, and cache
  totals MUST be labeled as overlapping diagnostics and MUST NOT be added as
  independent physical allocation.
- **FR-022**: Every measurement category and filesystem record MUST include
  completion status, duration, confidence, errors, privilege sufficiency, and
  coverage limitations.
- **FR-023**: Completion status MUST distinguish complete, partial, unavailable,
  not selected, timed out, cancelled, and disconnected outcomes where
  applicable.
- **FR-024**: The system MUST use existing non-interactive elevated read access
  when already authorized and available, but MUST NOT prompt for credentials or
  modify privilege policy.
- **FR-025**: When elevated visibility is unavailable, the system MUST return
  the best unprivileged result, mark affected evidence as partial, and identify
  the inaccessible boundary.
- **FR-026**: The final reconciliation MUST separately report used capacity,
  capacity-accounted observed allocation, deleted-open allocation, observable
  reserved or metadata allocation, overlapping logical diagnostics,
  measurement drift, and the residual unexplained gap.
- **FR-027**: The final residual unexplained gap MUST be non-negative; if
  observations exceed the capacity snapshot, the report MUST expose the
  discrepancy as drift or overlap rather than hiding it.
- **FR-028**: The reconciliation MUST prevent the same known physical allocation
  from contributing more than once to capacity-accounted observed allocation.
- **FR-029**: Missing, unreadable, timed-out, or otherwise incomplete evidence
  MUST remain represented by the residual unexplained gap and MUST NOT be
  inferred as reclaimable.
- **FR-030**: The system MUST distinguish exact managed cleanup opportunities
  governed by existing cleanup scopes from manual host remediation and
  non-cleanable or diagnostic findings.
- **FR-031**: Deep attribution MUST NOT create a new deletion path for arbitrary
  host files, deleted-open files, processes, unmanaged resources, or filesystem
  overhead.
- **FR-032**: Cleanup guidance MUST preserve existing confirmation, ownership,
  activity, permanence, job, backup, and workload protections.
- **FR-033**: Human-readable and structured reports for the same target and
  request MUST expose equivalent target, capacity, attribution, coverage,
  reconciliation, and diagnostic semantics.
- **FR-034**: Structured evidence MUST use stable field meanings and explicit
  units so automation clients do not need to infer whether values are physical,
  observed, logical, overlapping, reclaimable, or unexplained.
- **FR-035**: Reports MUST minimize and redact sensitive paths, tenant
  identifiers, credentials, tokens, command arguments, environment values,
  file contents, and mount details while retaining enough evidence to identify
  the measured category and remediation owner.
- **FR-036**: Ranked output MUST be bounded while preserving aggregate totals
  for findings omitted from the displayed ranking.
- **FR-037**: Repeated measurements of an unchanged deterministic fixture MUST
  produce the same ranking, byte totals, statuses, and reconciliation.
- **FR-038**: When live-host drift exceeds the greater of 1% of used capacity or
  64 MiB, the system MUST state that the result changed materially during or
  between measurements.
- **FR-039**: A failure in one filesystem or diagnostic category MUST NOT
  prevent independent categories from completing and appearing in the report.
- **FR-040**: The system MUST identify every excluded, unreadable, skipped,
  interrupted, or incomplete boundary by category and reason.

### Key Entities

- **Deep Attribution Request**: A read-only request identifying the local or
  named remote target, overall time budget, cancellation state, and reporting
  preferences.
- **Target Capacity Snapshot**: The observed total, used, available, reserved,
  and drift-related capacity values for the target at a specific measurement
  time.
- **Filesystem Record**: A discovered writable local filesystem, including its
  identity, capacity, mount relationship, managed-root relationship, selection
  reason, and coverage status.
- **Coverage Record**: The completion status, duration, confidence, privilege
  sufficiency, errors, exclusions, and limitations for a filesystem or
  diagnostic category.
- **Allocation Finding**: A ranked observed storage consumer with allocated
  bytes, filesystem relationship, deduplication status, sensitivity treatment,
  and confidence.
- **Deleted-Open Finding**: Allocated storage associated with deleted files
  still held by a process, aggregated by filesystem and minimized process
  identity.
- **Container Storage Finding**: Unique, shared, active, inactive, and
  potentially reclaimable container-related values with explicit overlap
  classification.
- **Capability Record**: The available measurement capability used for a
  category, its observable identity or version, fallback status, and
  limitations.
- **Reconciliation**: The relationship among used capacity,
  capacity-accounted observed allocation, deleted-open allocation, observable
  overhead, overlapping diagnostics, drift, and residual unexplained bytes.
- **Cleanup Guidance**: A classification linking a finding to an existing
  managed cleanup scope, manual host remediation, monitoring-only evidence, or
  non-cleanable overhead without authorizing a new deletion action.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A deterministic fixture containing 6 GiB of readable allocated
  files and 1 GiB of deleted-open files reduces the unexplained gap by at least
  7 GiB and reports the two classes separately, within one filesystem
  allocation block of the fixture sizes.
- **SC-002**: In 100% of completed deep reports, every discovered writable local
  filesystem has a visible status of scanned, partially scanned, or not scanned
  with an explicit reason.
- **SC-003**: In 100% of reports, the reconciliation keeps
  capacity-accounted observed allocation separate from overlapping logical
  diagnostics and produces a non-negative residual unexplained gap.
- **SC-004**: A hard-link fixture with multiple names for one inode contributes
  that inode's allocated blocks exactly once per scanned filesystem.
- **SC-005**: Sparse, compressed, reflinked, snapshot-backed, and copy-on-write
  fixtures or equivalent capability simulations produce explicit
  allocation-limit warnings in 100% of cases where physical ownership cannot be
  proven.
- **SC-006**: A request that reaches its time budget returns completed evidence
  within five seconds after the budget expires and marks every unfinished
  filesystem or category partial or timed out.
- **SC-007**: Missing capabilities, insufficient privilege, unreadable paths,
  delivered partial remote payloads, and category failures produce usable
  partial reports in 100% of tested failure scenarios without presenting
  unresolved bytes as reclaimable; total transport loss produces an explicit
  unavailable result.
- **SC-008**: Re-running an unchanged deterministic fixture produces identical
  ranked findings, totals, statuses, and reconciliation values.
- **SC-009**: On a live host without intentional workload changes, repeated
  reports remain within the greater of 1% of used capacity or 64 MiB for both
  capacity and attributed-byte drift; larger differences are explicitly
  reported.
- **SC-010**: Deep attribution performs zero observed mutations across files,
  packages, processes, mounts, privileges, storage settings, managed resources,
  and cleanup state in all verification scenarios.
- **SC-011**: Human-readable and structured representations of the same report
  agree on target, capacity, coverage, attributed totals, deleted-open totals,
  overlapping diagnostics, and residual bytes in 100% of parity tests.
- **SC-012**: In a representative operator review, at least 9 of 10 participants
  can identify the largest observed consumer, determine whether the report is
  complete, and distinguish an authorized managed cleanup action from manual or
  non-cleanable guidance without using external host commands.
- **SC-013**: Sensitive-data review finds no exposed file contents, credentials,
  tokens, environment values, secret-bearing process arguments, or sensitive
  mount options in any deep attribution output.
- **SC-014**: A failure isolated to one filesystem or category does not suppress
  completed evidence from any independent category in 100% of tested isolation
  scenarios.

## Assumptions

- Supported hosts expose basic filesystem capacity, mount topology, directory
  allocation, and process-file visibility through existing read-only host
  facilities.
- Optional measurement capabilities may improve performance or evidence quality
  but are not prerequisites for a supported scan.
- Target hosts are already configured for Sandbox access; this feature does not
  establish new remote access or privilege relationships.
- Existing non-interactive elevated read access may be used when already
  authorized, but partial unprivileged results are acceptable when it is
  unavailable.
- Filesystems may change while a scan is running, so capacity and attribution
  observations represent bounded snapshots rather than transactional state.
- The default deep-scan set includes the root filesystem and distinct
  filesystems containing Sandbox-managed storage, container storage, or other
  known managed roots; unrelated writable filesystems remain inventoried unless
  explicitly selected.
- Existing managed cleanup eligibility, confirmation, ownership, activity,
  permanence, job, backup, and workload protections remain authoritative.
- Arbitrary host remediation, package installation, process management,
  filesystem tuning, and interactive disk browsing remain outside this feature.
- Byte-exact physical ownership is not assumed on filesystems with sharing,
  snapshots, compression, sparse allocation, reflinks, or copy-on-write
  behavior.
