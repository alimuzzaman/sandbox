# Product Requirements Draft: Owned Storage Authority

**Status**: Ready for Specification

**Created**: 2026-08-31

**Last Refined**: 2026-08-31

**Input**: "Provide identity-safe immutable storage publication and final cleanup authority for remote sync generations and CI workspaces through a dedicated service identity and private ownership boundary. Resolver mutation is a separate authority and is out of scope."

**Drafting Model**: `gpt-5.6-sol` High

**Final Validation**: `PASS` — independent `gpt-5.6-sol` High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox can validate and atomically publish a remote synchronization generation, and it
can prove that a terminal CI workspace is eligible for cleanup. However, the ordinary
submitting identity still owns the relevant stored bytes. A publisher can therefore alter
or remove material described as immutable, while terminal cleanup cannot safely perform
the final removal without risking deletion of a replacement created at the same location.
The current implementation correctly fails closed, but that leaves verified disposable
storage retained and marked failed or indeterminate.

Operators need one trustworthy storage boundary that separates the identities allowed to
request publication or cleanup from the identity that owns the stored objects and performs
their final lifecycle mutations. The same product policy must cover immutable remote sync
generations and disposable CI workspace materializations without granting general host,
project, network, or resolver mutation authority.

## Users and Desired Outcomes

- **Developer or agent publishing source**: Gets one durable, immutable generation whose
  acceptance can be recovered safely after a lost response and cannot be changed by the
  publishing identity.
- **CI job submitter**: Gets a disposable workspace that remains usable for the job and is
  released according to the declared cleanup policy after terminal completion, without
  weakening the recorded job result.
- **Sandbox operator**: Can see what storage is current, retained, eligible, reclaimed,
  refused, or indeterminate, with exact ownership and byte evidence rather than guesses.
- **Maintainer or auditor**: Can prove that publication, retention, and removal decisions
  were identity-bound, replay-safe, bounded, and isolated from unrelated host state.
- **Consumer of an accepted generation**: Reads stable bytes for the selected generation
  and never observes a partially published or silently replaced generation.

## Goals

- Establish a dedicated storage authority identity whose control records, published
  generations, and storage roots are not writable by publishers, job workloads, or ordinary
  Sandbox control-plane callers. An authorized CI workload may write only inside its declared
  isolated or ephemeral materialization while it is active.
- Publish screened remote sync generations atomically and make every accepted generation
  immutable for its entire retained lifetime.
- Give the same authority exclusive final cleanup control over eligible disposable CI
  workspace materializations and authority-owned retained artifacts.
- Bind every publication, current-generation change, retention decision, and removal to
  exact durable identities and evidence, never a path, label, age, or process name alone.
- Preserve replay safety across retries, lost acknowledgements, interruption, and service
  restart without duplicate publication, duplicate ownership, or deletion of replacement
  content.
- Apply explicit retention and reclamation policy while protecting current, pending,
  referenced, retained, foreign, ambiguous, or incompletely observed storage.
- Advertise support only where the ownership and final-removal guarantees are proven;
  retain material and report a stable refusal everywhere else.
- Preserve existing remote sync, durable workspace, job-result, cleanup-policy, and public
  status semantics while allowing explicit use of the new authority for future objects.

## Non-Goals

- Granting resolver, DNS, ingress, network, container, package, credential, or arbitrary
  privileged filesystem mutation authority. Resolver mutation remains a separate product
  authority with its own qualification and cleanup rules.
- Broad cleanup of deployment roots, legacy workspaces, caches, volumes, hosted sites, or
  unrelated artifacts.
- Inferring ownership or deletion eligibility from a pathname, naming convention, age,
  stopped state, missing record, user ID alone, or incomplete inventory.
- Making mutable hosted-source synchronization or general project checkout replacement
  immutable under this feature.
- Changing source capture rules, credential screening, job scheduling, CI compatibility
  evaluation, job success criteria, or source-write policy.
- Automatically adopting, moving, deleting, or rewriting legacy generations, workspaces,
  metadata, or foreign-owned storage.
- Relocating or adopting legacy storage into the private authority boundary. This feature may
  report eligibility, but it governs only newly authority-published or authority-materialized
  objects.
- Deploying, releasing, migrating production state, or changing a remote service lifecycle.

## Product Scenarios

### Scenario 1 — Publish an immutable synchronization generation

- **Starting state**: A registered relationship, durable workspace identity, replay-safe
  request, and fully screened generation are ready for publication on a supported remote.
- **User action**: The developer or agent requests publication.
- **Expected outcome**: The authority validates the complete generation binding, publishes
  all bytes as one immutable generation, and advances the relationship's current reference
  only after durable acceptance. Readers see either the prior complete generation or the
  new complete generation, never a partial state.

### Scenario 2 — Recover a lost publication acknowledgement

- **Starting state**: Publication may have completed, but the caller received no valid
  acknowledgement.
- **User action**: The caller repeats the exact request identity.
- **Expected outcome**: The authority returns the original accepted result when exact
  request, relationship, workspace, generation, manifest, file-count, and byte-count
  evidence match. Any missing, mismatched, or ambiguous evidence remains unknown and causes
  no new publication or current-reference change.

### Scenario 3 — Resist publisher-side mutation

- **Starting state**: A generation is accepted and available to consumers.
- **User action**: The publisher, a job workload, or another ordinary caller tries to edit,
  rename, replace, or remove the generation or its authority record.
- **Expected outcome**: The attempt cannot change authority-owned material. Subsequent reads
  return the same generation identity and bytes, and the refused attempt cannot create
  cleanup eligibility.

### Scenario 3A — Allow bounded CI workspace writes

- **Starting state**: An authorized CI workload is running against its declared isolated or
  ephemeral materialization while its authority record and workspace root remain controlled
  by the storage authority.
- **User action**: The workload creates, changes, or removes files inside that materialization.
- **Expected outcome**: Declared workspace writes succeed without granting the workload power
  to replace or delete the materialization root, alter its authority record, write to an
  accepted sync generation, or mutate managed read-only source.

### Scenario 4 — Release a terminal disposable CI workspace

- **Starting state**: A CI job is terminal, its exact durable workspace and materialization
  authority match, its declared cleanup policy authorizes release, and all active references
  are proven absent.
- **User action**: Terminal reconciliation or an explicit cleanup request asks the authority
  to release it.
- **Expected outcome**: The exact workspace materialization and eligible authority-owned
  retained artifacts are removed, reclaimed bytes are measured, durable cleanup evidence is
  preserved, and the job's terminal outcome and immutable result remain unchanged.

### Scenario 5 — Refuse ambiguous or active cleanup

- **Starting state**: The requester is unauthorized or revoked, or the workspace is active,
  retained by policy, foreign, replaced, incompletely indexed, still referenced, changed
  since authorization, or otherwise lacks complete identity evidence.
- **User action**: Cleanup is requested or retried.
- **Expected outcome**: No candidate bytes, metadata, or unrelated resource are removed.
  The response states a stable refusal or indeterminate reason and preserves retryable
  evidence where safe.

### Scenario 6 — Retain and reclaim superseded storage

- **Starting state**: Authority-owned generations or CI materializations have become
  superseded or terminal, and an explicit retention policy or expiry applies.
- **User action**: The operator previews or runs reclamation.
- **Expected outcome**: Current, pending, unknown-acceptance, actively referenced, retained,
  and unexpired material is protected. Only exact eligible objects are listed in a bounded
  preview and reclaimed; each outcome and observed byte change is reported.

### Scenario 7 — Retry after interruption or replacement race

- **Starting state**: Publication or cleanup stopped after recording intent, or a location
  was replaced while the operation was in progress.
- **User action**: The same operation is reconciled or replayed.
- **Expected outcome**: Already completed work is reported idempotently. Remaining work
  continues only against the original verified object identity. A replacement or changed
  object is preserved and produces a refusal rather than being treated as the original.

### Scenario 8 — Run on an unsupported or unqualified platform

- **Starting state**: The platform cannot prove private authority ownership, immutable
  publication, durable identity comparison, or identity-bound final removal.
- **User action**: Authority publication, future-object opt-in, or cleanup is requested.
- **Expected outcome**: The capability is reported unsupported before authority-dependent
  mutation. Existing material is retained, ordinary paths are not silently upgraded, and
  no weaker fallback is represented as immutable or successfully reclaimed.

### Scenario 9 — Encounter legacy or existing storage

- **Starting state**: A relationship or workspace already exists outside the new private
  authority boundary.
- **User action**: A caller inspects it, replays an old request, or opts to use the new
  authority for future objects.
- **Expected outcome**: Existing read/status and replay behavior remain available where
  already supported. Legacy material stays visible and retained and is not relocated or
  adopted by this feature. Only newly published generations or newly materialized CI
  workspaces may enter the private authority boundary.

## Proposed Product Behavior

- The product separates request authority from storage mutation authority. Callers may
  submit bounded publication or cleanup requests, but only the dedicated storage authority
  owns and mutates objects inside its private boundary.
- Every request is authorized for the exact operation, remote, project identity, relationship
  or workspace identity, and authority-owned object before mutation. Possession of a path,
  label, digest, request ID, generation ID, workspace ID, or other opaque identifier is not
  authorization. Unauthorized, cross-project, revoked, or substituted requests are refused.
- Authority-owned objects are closed to caller mutation after publication. Consumers receive
  only the access required to read an accepted generation or execute an authorized CI job.
  An authorized CI workload may write within its declared isolated or ephemeral
  materialization, but it cannot replace or delete the materialization root, alter authority
  records, mutate accepted generations, or gain write access to managed read-only source.
- A generation acceptance is bound to the remote, relationship, project identity, workspace
  identity, request identity, generation identity, manifest digest, file count, and byte
  count. Acceptance is recorded before it is reported.
- Publication is all-or-nothing. The current reference changes only after the full generation
  is verified and durably owned by the authority. A published generation is never overwritten
  in place, including by an exact replay.
- Cleanup authority is narrower than publication authority. Cleanup proceeds only when an
  exact authority-owned object is eligible under its declared policy, all required lifecycle
  and reference evidence is complete, and the object still has the authorized identity at
  the final mutation boundary.
- Terminal cleanup does not redefine job truth. A successful job remains successful if
  cleanup later fails; cleanup is reported independently as completed, retained, refused,
  failed, or indeterminate.
- Retention is policy-driven. Missing, invalid, or unavailable policy evidence means retain.
  The current accepted generation, a pending generation, an unknown-acknowledgement
  generation, an active or retained CI workspace, and any object with an active reference
  are never reclamation candidates.
- Superseded generations and terminal disposable materializations may become eligible only
  after their applicable explicit release or retention window and a fresh identity/reference
  check. Storage pressure never weakens these protections.
- A reclamation preview is read-only and identifies each candidate, protection, estimated
  bytes, and reason without treating unknown bytes as reclaimable. Execution preserves a
  durable bounded intent/outcome record before discarding the last recoverable object.
- Replaying the same canonical request returns the original outcome or safely resumes the
  original operation. Reusing a request identity with different canonical inputs is refused.
- Public status distinguishes unsupported, refused, unknown, retained, indeterminate,
  accepted, and reclaimed outcomes. Empty, malformed, timed-out, or contradictory evidence
  is never interpreted as success.
- Capability support is explicit per platform and operating mode. A host qualifies only when
  its ordinary product path proves the separate owner, caller non-mutation, atomic publication,
  restart recovery, and identity-bound final removal guarantees.
- Existing non-authority storage and callers remain on their current compatibility path until
  they opt into the authority for future objects. Additive status may explain authority
  eligibility, but old records are not relocated, adopted, reclassified as owned, or made safe
  to delete by this feature.
- Authority records and public evidence are bounded and secret-free. They expose stable
  identities, digests, counts, lifecycle, policy, timestamps, outcome codes, and aggregate
  bytes, not source contents, credentials, unrestricted host configuration, or sensitive
  paths at public boundaries.

## Constraints and Dependencies

- The dedicated service identity and its private ownership boundary are mandatory product
  constraints, not an optional hardening mode. The submitting identity must not be able to
  grant itself equivalent mutation access.
- Durable workspace identity and complete ownership/index evidence are prerequisites for CI
  cleanup. A degraded or incomplete index cannot authorize mutation.
- Complete current-reference and active-reference evidence is a prerequisite for reclaiming
  generations or CI materializations. Missing, stale, contradictory, or incomplete reference
  evidence forces retention; this feature does not assume unfinished generation pinning or
  projection work is already available.
- Screened capture and exact request/generation reconciliation remain prerequisites for sync
  acceptance; this feature does not weaken credential or unstable-capture refusal.
- Existing cleanup policies, immutable terminal job results, accepted-generation semantics,
  and active-reference protections remain authoritative.
- A supported platform must provide stable object identity and final-removal guarantees across
  pathname replacement, restart, concurrent submission, open-reference, and interruption
  races. Lack of any required capability is an unsupported result.
- The authority must remain bounded to registered Sandbox-owned storage roots and typed
  operations. It may not accept arbitrary caller-selected paths or general commands.
- The authority must authenticate and authorize the caller independently of caller-supplied
  storage identifiers, and must refuse cross-project, revoked, substituted, or replay-conflict
  requests before mutation.
- Read-only observation and preview must remain available when safe even if mutation support is
  unavailable. Observation gaps must be reported and must never grant cleanup authority.
- Production rollout, service installation, privileges, and platform qualification require
  separate implementation and human review; this PRD authorizes no remote or production change.
- Resolver ownership and resolver cleanup evidence are independent of storage ownership. A
  storage qualification cannot qualify or authorize resolver mutation, and vice versa.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Storage owner | Use a dedicated service identity with a private ownership boundary inaccessible for mutation by submitting identities | Caller-owned permissions cannot prove immutability or safe final removal | User direction |
| Authority scope | Cover immutable remote sync generations and disposable CI workspace materializations plus their authority-owned retained artifacts | These are the two verified ownership gaps | User direction and current fail-closed behavior |
| CI workload access | Permit authorized writes only inside a declared isolated or ephemeral materialization; keep its root, authority record, accepted generations, and managed source outside workload mutation authority | CI work needs a writable isolated copy without weakening immutable publication or cleanup ownership | Existing source-access policy |
| Request authorization | Authorize the caller for the exact operation and durable scope; identifiers alone confer no authority | Identity-bound storage still needs a non-forgeable caller boundary | Existing ownership and security policy |
| Resolver boundary | Exclude resolver and DNS mutation completely | Resolver mutation has different ownership, atomicity, and qualification requirements | User direction and existing resolver policy |
| Publication | Validate and durably publish a new immutable generation before changing the current reference | Prevents partial or mutable acceptance | Existing sync policy |
| Replay | Bind replay to the complete canonical request and return or reconcile only the original operation | Lost responses must not create a second generation or cleanup target | Existing sync and job policy |
| Cleanup | Require exact authority ownership, terminal/policy eligibility, complete inactive-reference proof, and final identity match | Names, paths, age, and preliminary checks are not deletion authority | Existing workspace and reclamation policy |
| Retention | Protect current, pending, unknown, active, retained, referenced, unexpired, foreign, and incomplete objects; reclaim only explicitly eligible objects | Unknown evidence must fail closed | Existing workspace and reclamation policy |
| Legacy compatibility | Retain and report pre-authority storage; do not relocate or adopt it in this feature; opt-in affects future objects only | Legacy adoption would expand scope and could grant false ownership or deletion authority | Stated feature scope and existing migration policy |
| Unsupported platforms | Retain and report a stable unsupported/refused result with no weaker fallback claim | A partial ownership boundary is not immutable storage authority | User direction and existing fail-closed policy |
| Job result integrity | Report cleanup independently and never rewrite the terminal job outcome | Cleanup failure must remain truthful without corrupting execution evidence | Existing durable job contract |

## Open Questions

- None.

## Acceptance Outcomes

- On every advertised platform, an ordinary publisher or job workload is unable to modify,
  rename, replace, or delete an accepted authority-owned generation; a post-attempt read
  reproduces the accepted manifest, file count, byte count, and content digests exactly.
- Across at least 100 publication interruption/restart trials, readers observe only the prior
  complete generation or the new complete generation. No partial generation becomes current,
  and exact request replay creates zero duplicate accepted generations.
- In lost-acknowledgement tests, only evidence matching all canonical request and generation
  fields can recover acceptance. Every missing or mismatched field returns unknown/refused and
  changes neither stored bytes nor the current reference.
- A disposable CI workspace with authorized terminal policy and zero proven active references
  is fully removed through the authority on every supported platform, including the final
  owned directory or artifact object; measured reclaimed bytes are reported and the immutable
  terminal job result is byte-for-byte unchanged.
- An authorization and negative-cleanup matrix covering unauthorized, cross-project, revoked,
  and substituted callers plus active job, live lease, retained policy, foreign owner,
  ambiguous identity, incomplete index, changed object, replacement race, active process or
  mount, open reference, missing policy, and stale replay removes zero protected objects and
  reports a stable non-success reason for every case.
- An authorized isolated or ephemeral CI workload can write inside its declared
  materialization, while the same workload cannot replace/delete the materialization root,
  alter authority records, mutate an accepted generation, or write managed read-only source.
- Replaying an interrupted cleanup against the same operation and object identity produces one
  terminal cleanup outcome. Already-removed material is reported idempotently, while a
  replacement at the former location remains untouched.
- A retention test with current, pending, unknown-acknowledgement, actively pinned superseded,
  active, retained, unexpired, expired-superseded, and explicitly released objects lists only
  the exact eligible objects in preview. Execution considers no object outside that preview
  and removes only previewed objects still eligible after the final recheck; changed or unknown
  candidates are retained with a stable reason. Estimated and observed reclaimed bytes exclude
  unknown bytes.
- On each unsupported or unqualified platform, authority-dependent publication, future-object
  opt-in, and final cleanup are refused before the requested authority mutation; existing material is
  retained, and status never reports immutable acceptance or successful reclamation.
- Existing remote sync, durable workspace, job status/result, and cleanup-policy compatibility
  tests remain unchanged in outcome; new authority information is additive, no legacy record is
  relocated or adopted, and opt-in governs future objects only.
- Public CLI and MCP evidence for publication, status, replay, retention, and cleanup contains
  no source contents, credential values, unrestricted environment data, or sensitive host paths,
  and remains within documented response bounds.
- Read-only preview and status complete within their bounded product deadlines, while mutation
  timeout, transport loss, storage exhaustion, or service restart yields explicit partial or
  unknown evidence and never a false success.
- At least one newly created disposable remote fixture proves the complete ordinary product
  path: private ownership, immutable sync publication, caller mutation refusal, terminal CI
  cleanup, measured reclamation, replay, restart recovery, and preservation of unrelated state.

## Risks and Assumptions

- **Risk**: A platform may isolate writes but still lack a reliable identity-bound final removal
  primitive; advertising it would reproduce the current unsafe final-cleanup gap.
- **Risk**: Privilege or ownership misconfiguration could expand the authority from its bounded
  storage roots into general host mutation.
- **Risk**: Retention or active-reference evidence can drift between preview and mutation; every
  mutation therefore depends on a fresh final check and must fail closed on change.
- **Risk**: A future migration of legacy material could create false ownership if identity
  evidence is incomplete; any such adoption remains outside this feature and requires its own
  explicit product decision.
- **Risk**: A full disk can prevent publication or durable evidence recording; failure must
  preserve existing accepted material and must not report partial cleanup as reclaimed.
- **Risk**: Consumers may depend on mutable same-user generation paths; explicit opt-in for
  future objects and compatibility evidence are required before changing their access model.
- **Assumption**: Supported remotes can run a dedicated identity whose storage mutation rights
  are not available to the submitting user or workload.
- **Assumption**: Existing durable relationship, request, generation, job, workspace, and
  cleanup-policy records remain available as authoritative inputs. Complete active-reference
  evidence is a dependency; when it is missing or stale, the product retains the object.
- **Assumption**: Product-specific retention policy supplies an explicit release or effective
  retention window; absent or invalid policy means retain rather than infer expiry.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
