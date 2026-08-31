# Feature Specification: Owned Storage Authority

**Feature Branch**: `codex/owned-storage-authority` (spec directory `048-owned-storage-authority`)

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "Provide identity-safe immutable storage publication and final cleanup authority for remote sync generations and CI workspaces through a dedicated service identity and private ownership boundary. Resolver mutation is a separate authority and is out of scope."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish an immutable generation (Priority: P1)

A developer or agent publishes a fully screened source generation to a supported remote. The
accepted generation is owned by a dedicated storage authority, cannot be changed by the
publisher, and becomes current only after all accepted bytes and evidence are durable.

**Why this priority**: Immutable publication is the primary value of the feature. Without a
separate owner and all-or-nothing acceptance, a publisher can change material that consumers
trust as an accepted generation.

**Independent Test**: Publish a screened generation through the ordinary product path, attempt
to edit, rename, replace, and remove it as the publishing identity, and confirm that every
attempt leaves the accepted identity, manifest, file count, byte count, and content digests
unchanged.

**Acceptance Scenarios**:

1. **Given** a registered relationship, durable workspace identity, replay-safe request, and
   fully screened generation on a qualified platform, **When** an authorized caller requests
   publication, **Then** the complete generation is accepted under the storage authority and
   becomes current only after its complete evidence is durable.
2. **Given** an existing complete current generation, **When** a new generation is interrupted
   before durable acceptance, **Then** readers continue to observe the prior complete generation
   and never observe the partial candidate.
3. **Given** an accepted generation, **When** the publisher, a CI workload, or an ordinary
   control-plane caller attempts to edit, rename, replace, or remove it or its authority record,
   **Then** the attempt changes no accepted bytes, identity, or evidence.
4. **Given** a caller that possesses a path or opaque storage identifier but lacks authorization
   for the exact operation and durable scope, **When** publication is requested, **Then** the
   request is refused before storage mutation.

---

### User Story 2 - Release a terminal CI workspace safely (Priority: P1)

A CI job submitter finishes a disposable job and the storage authority removes the exact
eligible materialization and its eligible retained artifacts. Cleanup reports measured
reclamation independently and never rewrites the terminal job result.

**Why this priority**: The current product can prove cleanup eligibility but cannot safely
perform the final removal. Identity-bound final cleanup closes that verified ownership gap.

**Independent Test**: Complete a disposable CI job, prove the declared cleanup policy and zero
active references, execute cleanup, and confirm that the exact authority-owned materialization
is absent, reclaimed bytes are reported, unrelated state remains unchanged, and the terminal
job result is byte-for-byte identical to its pre-cleanup value.

**Acceptance Scenarios**:

1. **Given** a terminal disposable CI workspace with matching authority evidence, an explicit
   release policy, and complete proof that no active reference remains, **When** authorized
   cleanup is requested, **Then** the exact materialization and only its eligible retained
   artifacts are removed and observed reclaimed bytes are reported.
2. **Given** a successful or failed terminal job, **When** its cleanup completes, fails, is
   refused, or remains indeterminate, **Then** the job's terminal outcome and immutable result
   remain unchanged and cleanup is reported separately.
3. **Given** an active job, live lease, retained policy, foreign owner, incomplete index, active
   reference, open use, changed object, missing policy, or ambiguous identity, **When** cleanup
   is requested, **Then** no candidate object or metadata is removed and a stable non-success
   reason is reported.
4. **Given** a location that now contains a replacement object, **When** cleanup reaches its
   final identity check, **Then** the replacement remains untouched and the original cleanup is
   refused or reported indeterminate.

---

### User Story 3 - Recover safely after lost responses or interruption (Priority: P1)

A caller can retry the same publication or cleanup request after transport loss, interruption,
or service restart. The storage authority returns or resumes only the original operation and
does not create a duplicate generation, duplicate ownership record, or second cleanup target.

**Why this priority**: Remote operations routinely lose acknowledgements. Retry safety is part
of the authority boundary because a second operation could publish conflicting state or remove
the wrong object.

**Independent Test**: Interrupt publication and cleanup at each durable lifecycle boundary,
restart the authority, replay the exact request, and verify one operation identity, at most one
accepted generation, one terminal cleanup outcome, and no mutation for mismatched replays.

**Acceptance Scenarios**:

1. **Given** publication completed but its acknowledgement was lost, **When** the caller repeats
   the exact canonical request and all relationship, workspace, generation, manifest, file-count,
   and byte-count evidence matches, **Then** the original accepted result is returned without a
   second generation or current-reference change.
2. **Given** a reused request identity with missing, changed, or contradictory canonical fields,
   **When** publication or cleanup is replayed, **Then** the result is refused or unknown and no
   bytes, ownership records, or current references change.
3. **Given** an interrupted cleanup against an unchanged original object, **When** the same
   operation is resumed, **Then** it reaches one terminal cleanup outcome and already-removed
   material is reported idempotently.
4. **Given** an interrupted operation followed by authority restart, **When** the caller
   reconciles the original request, **Then** durable evidence identifies the original operation
   without relying on a caller-selected path or a new request identity.

---

### User Story 4 - Use a bounded writable CI materialization (Priority: P2)

An authorized CI workload can write inside its declared isolated or ephemeral
materialization while the storage authority retains control of the materialization root,
authority record, accepted generations, and managed read-only source.

**Why this priority**: CI work needs a writable area, but that access must not weaken immutable
publication or final cleanup ownership.

**Independent Test**: Run an authorized workload that creates, changes, and removes files inside
its declared materialization, then attempt changes to its root, authority record, an accepted
generation, managed source, and another workspace; confirm only the declared interior writes
succeed.

**Acceptance Scenarios**:

1. **Given** an active authorized workload and its declared materialization, **When** it creates,
   changes, or removes files inside that materialization, **Then** the requested workspace writes
   succeed.
2. **Given** the same workload, **When** it attempts to replace or delete the materialization
   root, change the authority record, mutate an accepted generation, or write managed read-only
   source, **Then** each attempt changes nothing outside the declared writable interior.
3. **Given** a workload authorized for one project and workspace, **When** it presents an
   identifier belonging to another project or workspace, **Then** the request is refused before
   mutation.

---

### User Story 5 - Preview and reclaim retained storage (Priority: P2)

An operator can inspect current, protected, retained, eligible, reclaimed, refused, and
indeterminate authority-owned storage. A bounded read-only preview shows exactly which objects
could be reclaimed and why; execution removes only previewed objects that remain eligible at a
fresh final check.

**Why this priority**: Reclamation without complete identity, policy, and reference evidence
would turn storage pressure into unsafe deletion authority.

**Independent Test**: Populate the retention matrix with current, pending, unknown,
referenced, retained, unexpired, eligible superseded, and released objects; compare preview
with execution and confirm only eligible, previewed, unchanged objects are removed.

**Acceptance Scenarios**:

1. **Given** mixed lifecycle and retention states, **When** an operator requests preview,
   **Then** each inspected object is reported as protected or eligible with its stable identity,
   reason, and known estimated bytes, and unknown bytes are excluded from reclaimable totals.
2. **Given** an eligible superseded generation or terminal materialization, **When** its release
   condition or retention window has passed and complete current-reference and active-reference
   evidence proves it inactive, **Then** it may appear in preview.
3. **Given** an object that was not previewed or that changed, became current, gained a
   reference, or lost complete evidence after preview, **When** reclamation executes, **Then** the
   object is retained with a stable reason.
4. **Given** storage pressure or exhausted capacity, **When** retention evidence is missing or
   invalid, **Then** protection remains unchanged and no unknown object becomes eligible.

---

### User Story 6 - See truthful support and compatibility status (Priority: P2)

An operator can tell whether each platform and operating mode qualifies for owned storage
authority. Unsupported platforms refuse authority-dependent mutation without weakening the
existing compatibility path, and legacy storage remains visible without being adopted.

**Why this priority**: A partial ownership boundary must never be advertised as immutable or
safe for final cleanup.

**Independent Test**: Exercise qualified, unqualified, legacy, and future-object opt-in cases
and confirm that status and outcomes distinguish them without moving, adopting, or deleting
legacy material.

**Acceptance Scenarios**:

1. **Given** a platform or mode that cannot prove separate ownership, caller non-mutation,
   all-or-nothing publication, restart recovery, and identity-bound final removal, **When**
   authority publication, future-object opt-in, or final cleanup is requested, **Then** the
   request is refused as unsupported before authority-dependent mutation.
2. **Given** an existing relationship, generation, or workspace outside the private authority
   boundary, **When** it is inspected or its existing supported operation is replayed, **Then**
   it remains visible on its compatibility path and is not relocated, adopted, or reclassified
   as authority-owned.
3. **Given** an eligible caller opting into authority for future objects, **When** a later
   generation or CI materialization is created on a qualified platform, **Then** only that new
   object enters the authority boundary.
4. **Given** a platform qualified for storage authority but not resolver authority, **When**
   status is requested, **Then** storage support conveys no resolver mutation or cleanup
   eligibility.

---

### User Story 7 - Audit bounded, secret-free evidence (Priority: P3)

A maintainer or auditor can prove who requested each bounded operation, which durable object
and policy governed it, what outcome occurred, and how many bytes were accepted or reclaimed,
without exposing source contents, credentials, unrestricted host configuration, or sensitive
paths.

**Why this priority**: Ownership claims and destructive outcomes must remain independently
answerable, but public evidence must not create a new data-exposure path.

**Independent Test**: Run successful, refused, unsupported, interrupted, and indeterminate
publication and cleanup cases, then verify that their bounded evidence reconstructs each
decision while containing none of the prohibited data classes.

**Acceptance Scenarios**:

1. **Given** any publication, replay, preview, retention, or cleanup outcome, **When** public
   evidence is inspected, **Then** it includes the stable identities, lifecycle, policy,
   timestamps, outcome codes, digests, counts, and aggregate bytes needed to explain the
   decision within documented bounds.
2. **Given** source content, credentials, unrestricted environment data, sensitive host paths,
   or unrelated project state, **When** public evidence is produced, **Then** none of those data
   classes is exposed.
3. **Given** empty, malformed, timed-out, partial, or contradictory evidence, **When** an
   outcome is reported, **Then** it is never represented as acceptance or successful
   reclamation.

### Edge Cases

- Publication stops after some candidate bytes exist but before acceptance is durable; the
  candidate never becomes current and consumers continue reading the prior complete generation.
- An exact request is submitted concurrently more than once; all callers reconcile to one
  operation and at most one accepted generation or cleanup outcome.
- A request identity is reused with one changed canonical field; the replay is refused and
  changes nothing.
- The authority restarts after recording intent but before reporting or completing mutation;
  recovery uses durable identity evidence and does not invent success.
- The storage location is replaced between eligibility review and final removal; the replacement
  is retained because its identity does not match the authorized object.
- A candidate is current, pending, unknown-acknowledgement, actively referenced, retained,
  unexpired, foreign-owned, or incompletely observed; it never becomes reclaimable by inference.
- A workspace has an active process, mount, open reference, live lease, or running job; cleanup
  retains it unless complete authoritative evidence proves those protections absent.
- Storage fills during publication or evidence recording; the prior accepted generation and
  its current reference remain intact, partial bytes are not accepted, and no false reclamation
  is reported.
- Estimated size is unknown; it is omitted from reclaimable-byte totals and uncertainty cannot
  make the object eligible.
- An authority-owned object is already absent during exact replay; the original terminal outcome
  is returned when proven, while any replacement at the former location remains untouched.
- Read-only observation is partial or times out; the result states its incompleteness and grants
  no cleanup authority.
- An ordinary caller attempts to select an arbitrary path, root, project, or general operation;
  the request is refused outside the registered authority boundary.
- A legacy object resembles an authority-owned object by name or location; resemblance never
  grants ownership, adoption, or deletion eligibility.
- Resolver and storage records share a project identity; storage qualification still grants no
  resolver mutation authority.

## Requirements *(mandatory)*

### Functional Requirements

#### Authority and authorization boundary

- **FR-001**: The product MUST use a dedicated storage authority identity to own and perform
  lifecycle mutations for accepted generations, CI materialization roots, authority records,
  and the retained artifacts governed by this feature.
- **FR-002**: Publishers, CI workloads, and ordinary control-plane callers MUST NOT be able to
  grant themselves the authority's equivalent mutation access or mutate authority records and
  storage roots directly.
- **FR-003**: The authority MUST be limited to registered Sandbox-owned storage roots and the
  typed publication, observation, retention, and cleanup operations defined by this feature.
- **FR-004**: The authority MUST refuse arbitrary caller-selected paths, general commands, and
  operations outside the exact registered project, relationship, workspace, or object scope.
- **FR-005**: Every mutating request MUST authenticate and authorize the caller for the exact
  operation, remote, project identity, durable relationship or workspace identity, and target
  authority-owned object before mutation.
- **FR-006**: A path, label, digest, request identity, generation identity, workspace identity,
  or other opaque identifier MUST NOT confer authority by possession alone.
- **FR-007**: Unauthorized, cross-project, revoked, substituted, or replay-conflicting requests
  MUST be refused before authority-owned storage or lifecycle state changes.
- **FR-008**: Storage authority qualification and evidence MUST NOT grant or imply resolver,
  DNS, ingress, network, container, package, credential, or general host mutation authority.

#### Immutable generation publication

- **FR-009**: Publication MUST require a registered relationship, durable project and workspace
  identities, a replay-safe request identity, and a fully screened generation with complete
  identity and content evidence.
- **FR-010**: Generation acceptance MUST bind the exact remote, project, relationship,
  workspace, request, generation, manifest digest, file count, and byte count.
- **FR-011**: The authority MUST verify and durably own the complete generation before recording
  acceptance or changing the relationship's current reference.
- **FR-012**: Readers MUST observe either the prior complete accepted generation or the new
  complete accepted generation and MUST NOT observe a partially published generation as current.
- **FR-013**: An accepted generation MUST remain immutable for its complete retained lifetime,
  including against its original publisher, authorized CI workloads, ordinary callers, and exact
  request replay.
- **FR-014**: An accepted generation MUST NOT be overwritten in place; a distinct accepted
  generation MUST have its own durable identity and evidence.
- **FR-015**: Publication failure, interruption, insufficient storage, or incomplete evidence
  MUST preserve the prior accepted generation and current reference and MUST NOT report the
  candidate as accepted.
- **FR-016**: Consumer read access MUST be limited so that consuming an accepted generation does
  not provide authority to edit, rename, replace, or remove it.

#### Bounded CI workspace use

- **FR-017**: An authorized active CI workload MUST be able to create, change, and remove files
  only inside its declared isolated or ephemeral materialization.
- **FR-018**: CI workload access MUST NOT permit replacement or removal of the materialization
  root, alteration of its authority record, mutation of an accepted generation, or writes to
  managed read-only source.
- **FR-019**: A CI materialization MUST be bound to its exact durable project, job, workspace,
  materialization, policy, and authority ownership evidence.
- **FR-020**: Workload access MUST end or become non-authoritative when the job is no longer in
  the lifecycle state authorized for that materialization.

#### Replay and recovery

- **FR-021**: Every publication and cleanup operation MUST bind replay to its complete canonical
  request and original durable operation identity.
- **FR-022**: Repeating an exact canonical request MUST return the original outcome or resume
  only the original operation without creating a duplicate generation, ownership record,
  cleanup target, or terminal cleanup outcome.
- **FR-023**: Reusing a request identity with any different canonical input MUST be refused
  before mutation.
- **FR-024**: Lost, empty, malformed, timed-out, partial, or contradictory acknowledgements MUST
  remain unknown or indeterminate unless complete durable evidence proves the original outcome.
- **FR-025**: Restart recovery MUST preserve operation identity, accepted generation truth,
  cleanup intent, and terminal outcome without requiring a new request identity.
- **FR-026**: An already-removed original object MUST be reported idempotently when its exact
  completed cleanup is proven; a replacement at the former location MUST remain untouched.

#### Retention, preview, and final cleanup

- **FR-027**: The product MUST protect current, pending, unknown-acknowledgement, actively
  referenced, active, retained, unexpired, foreign-owned, ambiguous, and incompletely observed
  objects from reclamation.
- **FR-028**: Superseded generations and terminal disposable materializations MUST become
  eligible only through an explicit release condition or applicable retention window and only
  when complete current-reference and active-reference evidence proves them inactive.
- **FR-029**: Missing, invalid, stale, contradictory, or unavailable retention, ownership,
  lifecycle, index, current-reference, or active-reference evidence MUST force retention rather
  than inferred eligibility.
- **FR-030**: Read-only preview MUST identify each inspected object's stable identity, lifecycle,
  protection or eligibility reason, and known estimated bytes without changing storage,
  authority records, policies, or references.
- **FR-031**: Preview MUST exclude unknown bytes from estimated reclaimable totals and MUST state
  when inventory or observation is incomplete.
- **FR-032**: Reclamation execution based on a preview MUST consider no object outside that
  reviewed preview, and every cleanup path MUST perform a fresh authorization, policy,
  lifecycle, ownership, reference, and object-identity check immediately before final mutation.
- **FR-033**: An object that changed or became protected after preview MUST be retained with a
  stable reason and MUST NOT be counted as reclaimed.
- **FR-034**: Final cleanup MUST remove only the exact authority-owned object whose identity
  matches the authorized candidate at the final mutation boundary.
- **FR-035**: Cleanup MUST preserve unrelated projects, foreign objects, replacement objects,
  active materializations, managed source, legacy storage, and all resources outside the
  registered owned-storage boundary.
- **FR-036**: Cleanup MUST preserve durable bounded intent and outcome evidence sufficient to
  reconcile interruption before discarding the last recoverable authority-owned object.
- **FR-037**: Cleanup MUST report each object as completed, already removed, retained, refused,
  failed, or indeterminate with a stable reason and observed reclaimed bytes when known.
- **FR-038**: Estimated and observed reclaimed-byte totals MUST exclude unknown, retained,
  refused, failed, indeterminate, and only partially removed bytes.
- **FR-039**: Cleanup state and evidence MUST be reported independently from the immutable
  terminal CI job outcome and MUST never rewrite that outcome or result.
- **FR-040**: Concurrent or repeated cleanup requests MUST NOT cause the same owned object to be
  removed as two different authorized targets.
- **FR-041**: Storage pressure or exhausted capacity MUST NOT weaken identity, authorization,
  reference, retention, or final-match protections.

#### Qualification, compatibility, and evidence

- **FR-042**: Support MUST be reported separately for each platform and operating mode and only
  after its ordinary product path proves separate ownership, caller non-mutation, all-or-nothing
  publication, restart recovery, and identity-bound final removal.
- **FR-043**: If any required ownership or final-removal guarantee is unavailable, authority
  publication, future-object opt-in, and final cleanup MUST return a stable unsupported or
  refused outcome before authority-dependent mutation, with no weaker fallback represented as
  immutable or reclaimed.
- **FR-044**: Safe read-only status and preview MUST remain available on unqualified platforms
  when their evidence can be reported truthfully; observation gaps MUST be explicit and MUST
  grant no mutation authority.
- **FR-045**: Existing non-authority relationships, generations, workspaces, metadata, and
  callers MUST remain on their current compatibility path until an explicit future-object
  opt-in applies on a qualified platform.
- **FR-046**: This feature MUST NOT relocate, adopt, rewrite, delete, or reclassify legacy or
  foreign storage as authority-owned, and resemblance by path, name, age, or owner label MUST
  not create ownership or deletion eligibility.
- **FR-047**: Future-object opt-in MUST govern only new generations and CI materializations
  created after the opt-in; it MUST NOT retroactively change existing objects.
- **FR-048**: Public authority evidence MUST distinguish unsupported, refused, unknown,
  retained, indeterminate, accepted, completed, and reclaimed outcomes.
- **FR-049**: Public evidence MUST be bounded and limited to the stable identities, digests,
  counts, lifecycle, policy, timestamps, outcome codes, and aggregate bytes needed to explain
  the operation.
- **FR-050**: Public evidence MUST NOT expose source contents, credential values, unrestricted
  environment or host configuration, sensitive host paths, or unrelated project state.
- **FR-051**: Empty, malformed, timed-out, incomplete, or contradictory evidence MUST NOT be
  interpreted or reported as successful acceptance, cleanup, or reclamation.
- **FR-052**: Production rollout, service installation, privilege grants, platform
  qualification, and remote state migration MUST remain separate human-reviewed activities and
  MUST NOT occur as a consequence of defining or inspecting this feature.

### Scope Boundaries

**In scope**:

- Newly authority-published immutable remote synchronization generations and their retained
  authority-owned artifacts.
- Newly authority-materialized disposable CI workspaces, their controlled roots and records,
  and their eligible authority-owned retained artifacts.
- Exact-scope authorization, replay and restart recovery, read-only status and preview,
  retention decisions, final identity-bound cleanup, and bounded public evidence for those
  objects.
- Additive future-object opt-in and compatibility status for existing relationships and
  callers.

**Out of scope**:

- Resolver, DNS, ingress, network, container, package, credential, or general privileged host
  mutation.
- Broad cleanup of deployment roots, legacy workspaces, caches, volumes, hosted sites, or
  unrelated artifacts.
- Adoption, relocation, deletion, ownership inference, or migration of legacy or foreign
  storage.
- Mutable hosted-source synchronization, general project checkout replacement, changes to
  capture or credential screening, job scheduling, CI compatibility evaluation, job success
  criteria, or managed-source write policy.
- Production deployment, release, service installation, remote migration, or privilege
  changes.

### Key Entities

- **Storage authority**: The dedicated identity and private ownership boundary that exclusively
  owns authority records and performs permitted lifecycle mutations for registered objects.
- **Authority-owned object**: A generation, materialization root, or retained artifact with a
  durable identity, registered scope, lifecycle, ownership evidence, and applicable policy.
- **Canonical operation request**: The complete identity-bound publication or cleanup request;
  its operation, caller authorization, durable scope, target identity, and content or policy
  evidence determine replay equivalence.
- **Remote sync generation**: One screened, immutable set of source content bound to a remote,
  project, relationship, workspace, request, generation, manifest digest, file count, and byte
  count.
- **Current reference**: The durable selection of the complete accepted generation consumers
  should read; it never selects a partial or unaccepted candidate.
- **CI workspace materialization**: The declared isolated or ephemeral writable interior used
  by one authorized job, enclosed by an authority-controlled root and authority record.
- **Authority record**: Bounded durable evidence that connects an authority-owned object to its
  exact project, relationship or workspace, operation, lifecycle, ownership, policy, and
  outcome.
- **Retention policy**: The explicit release condition or time window that may make an inactive,
  superseded, or terminal object eligible; missing or invalid policy always means retain.
- **Active reference**: Authoritative evidence that an object is still current, pending, open,
  leased, pinned, or otherwise in use and therefore protected.
- **Reclamation preview**: A read-only bounded decision set containing exact candidate
  identities, eligibility or protection reasons, and known estimated bytes.
- **Cleanup outcome**: The independent terminal or non-terminal truth for one exact cleanup
  operation and object: completed, already removed, retained, refused, failed, or indeterminate.
- **Platform qualification**: Evidence for one platform and operating mode that all required
  ownership, immutability, recovery, and final-removal guarantees hold through the ordinary
  product path.
- **Legacy object**: Existing storage outside the authority boundary; it remains visible on its
  compatibility path but gains no authority ownership or deletion eligibility from this feature.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On every advertised platform and mode, 100% of attempted publisher, workload, and
  ordinary-caller edits, renames, replacements, and removals against accepted generations leave
  the accepted manifest, file count, byte count, and content digests unchanged.
- **SC-002**: Across at least 100 publication interruption and restart trials spanning every
  durable lifecycle boundary, readers observe only the prior complete generation or the new
  complete generation, zero partial generations become current, and exact replay creates zero
  duplicate accepted generations.
- **SC-003**: In 100% of lost-acknowledgement tests, only a replay matching every canonical
  request and generation field recovers acceptance; every missing, changed, or contradictory
  field changes zero bytes and zero current references.
- **SC-004**: On every advertised platform and mode, 100% of eligible terminal disposable CI
  workspace tests remove the final authority-owned materialization and eligible artifacts,
  report measured reclaimed bytes, and leave the terminal job outcome and result unchanged.
- **SC-005**: A negative authorization and cleanup matrix covering unauthorized, cross-project,
  revoked, substituted, active, leased, retained, foreign, ambiguous, incomplete, changed,
  replaced, open, missing-policy, and stale-replay cases removes zero protected objects and
  reports one stable non-success reason for every case.
- **SC-006**: In 100% of bounded-workload tests, authorized writes within the declared CI
  materialization succeed, while attempted changes to the root, authority record, accepted
  generations, managed source, and other workspaces change zero protected bytes.
- **SC-007**: Across at least 100 interrupted or repeated cleanup trials, each canonical cleanup
  reaches at most one terminal outcome, already-removed originals are reported idempotently,
  and every replacement at a former location remains untouched.
- **SC-008**: A retention matrix containing current, pending, unknown-acknowledgement,
  referenced, active, retained, unexpired, eligible superseded, released, foreign, and changed
  objects lists exactly the eligible unchanged objects in preview and removes no object outside
  that set.
- **SC-009**: On every unsupported or unqualified platform and mode, 100% of authority-dependent
  publication, future-object opt-in, and final-cleanup attempts are refused before the requested
  authority mutation, and zero existing objects are adopted or relocated.
- **SC-010**: Existing remote sync, durable workspace, terminal job, cleanup-policy, and legacy
  replay acceptance suites retain 100% of their prior outcomes; authority status is additive and
  future-object opt-in changes zero pre-existing records.
- **SC-011**: Inspection of public evidence from every outcome class finds zero source-content
  excerpts, credential values, unrestricted environment data, sensitive host paths, or unrelated
  project state.
- **SC-012**: At least 95% of read-only status and preview requests covering up to 10,000
  authority and legacy records complete within 30 seconds; every request outside that bound or
  unable to complete reports partial or timed-out evidence and never reports false success.
- **SC-013**: One newly created disposable remote fixture proves the complete ordinary product
  journey end to end: private ownership, immutable publication, caller mutation refusal,
  bounded CI writes, terminal cleanup, measured reclamation, exact replay, restart recovery,
  and preservation of unrelated state.
- **SC-014**: In an acceptance review, 100% of operators and maintainers can correctly identify
  from bounded status evidence whether a sampled object is current, protected, eligible,
  accepted, reclaimed, refused, unsupported, or indeterminate and can state the recorded reason
  without inspecting host internals.

## Assumptions

- Supported remotes can provide a dedicated identity whose storage mutation rights are not
  available to the submitting user, CI workload, or ordinary control-plane caller.
- Existing durable project, relationship, request, generation, job, workspace, cleanup-policy,
  and authority records remain available as authoritative inputs to owned-storage decisions.
- Complete current-reference and active-reference evidence is supplied by the owning product
  lifecycle. When it is absent, stale, contradictory, or incomplete, the authority retains the
  object.
- Existing screened capture and exact request/generation reconciliation remain prerequisites
  for publication; this feature does not weaken credential or unstable-capture refusal.
- Product-specific retention policy supplies an explicit release condition or effective
  retention window. Missing or invalid policy means retain rather than infer expiry.
- Public status and preview are expected to cover inventories of up to 10,000 authority and
  legacy records within the measurable outcome defined here; larger or slower observations may
  return bounded partial evidence but may not grant cleanup authority.
- Existing non-authority storage stays on its current compatibility path. Adoption or migration,
  if ever desired, requires a separate product decision and specification.
- Platform qualification, service installation, privileges, remote migration, release, and
  production rollout require separate implementation evidence and explicit human review.

## Dependencies and Compatibility Constraints

- Durable workspace identity and complete ownership/index evidence are prerequisites for CI
  cleanup. A degraded or incomplete index cannot authorize mutation.
- Complete current-reference and active-reference evidence is required before reclaiming a
  generation or CI materialization.
- Screened capture and exact request/generation reconciliation remain prerequisites for remote
  sync acceptance.
- Existing cleanup policies, immutable terminal job results, accepted-generation semantics,
  active-reference protections, compatibility evaluations, and managed-source write policy
  remain authoritative and unchanged by this feature.
- A qualified platform must preserve stable object identity and final-removal guarantees across
  pathname replacement, restart, concurrent submission, open-reference, interruption, and
  storage-exhaustion races.
- Existing read and status behavior for legacy storage remains compatible and additive; old
  records are not relocated, adopted, reclassified, or made safe to delete.
- Storage ownership and resolver ownership remain independent qualification and mutation
  boundaries. Evidence for either cannot qualify or authorize the other.
