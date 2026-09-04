# Data Model: Owned Storage Authority

> **Planning status: REPAIRED (Option 2 Authorized).** FR-058 is amended to
> decouple lifecycle state from OCI hosting and use a dedicated, crash-safe
> storage authority lifecycle repository. See [analysis.md](./analysis.md).

The model is path-free at every application and public boundary. The protected
remote lifecycle owns capability candidates, review, promotion, revocation,
and the public capability projection. The storage authority owns only its
private object/operation journal and a non-authorizing adoption binding.
Filesystem locators and kernel identity fields exist only inside the private
repository and Linux adapter. Existing sync, job, workspace, and resource
records remain authoritative for their current application domains.

All lifecycle-owned models below are durably persisted in a dedicated,
crash-safe `StorageAuthorityLifecycleRepository` located under the service runtime
boundary (e.g. `runtime/storage_authority/lifecycle.json`). It uses owner-only
permissions (`0600`), advisory locking (`fcntl.flock`), atomic replacement via
temporary file rename, and generation-based CAS for concurrency control.
It is completely decoupled from OCI hosting (`hosts.json` / `RecoveryRepository`).

## AuthorityCapability

One lifecycle-owned observation for an exact remote platform and operating mode.

| Field | Type | Rules |
|---|---|---|
| capability_id | opaque string | Stable identifier for `owned-storage-authority-v1`. |
| remote_identity | opaque string | Exact registered remote; never an SSH target or path. |
| platform_mode | safe enum/string | Exact reviewed mode, initially Ubuntu 24.04/systemd 255 private-root mode. |
| support_tier | enum | `unavailable`, `unsupported`, `implemented_unproven`, `proven`, `drifted`. |
| adoptable | boolean | True only when tier is `proven` and evidence is current. |
| service_revision | digest/version | Exact installed service protocol/source revision. |
| owner_identity_digest | digest/null | Digest of the observed dedicated identity evidence; raw UID is not public. |
| root_identity_digest | digest/null | Digest of private root/service ownership evidence. |
| primitive_states | bounded map | Separate ownership, controller-process/peer-auth, no-replace, dirfd, mount isolation, restart, and final-removal checks. |
| evidence_id | opaque string/null | Human-reviewed live evidence identity. Null is not adoptable. |
| ordinary_evidence_id | opaque string/null | Closed post-promotion ordinary-path evidence; required before support outside the fixture. |
| acceptance_state | enum/null | `pending_ordinary`, `complete`, or `failed` for a promoted disposable fixture. |
| observed_at/expires_at | timestamp | Freshness bound. Expired evidence closes mutation admission. |
| reason_code | safe code | Explains non-adoptability without host paths/details. |

Validation rules:

- Any missing, stale, unknown, contradictory, or drifted primitive makes
  `adoptable=false`.
- A fixture-validation promotion keeps tier `implemented_unproven` and
  `adoptable=false`; only its exact active binding may open validation `future`.
  `proven`/true requires terminal `CapabilityAcceptance` with ordinary evidence.
- A capability report cannot promote itself; review creates only the
  fixture-validation promotion, and only protected lifecycle acceptance
  finalization may add ordinary evidence and change tier to proven/adoptable.
- Storage and resolver capability identities are disjoint.

## CapabilityReviewRequest

The lifecycle-owned replay boundary for one protected review. It is not a
storage-authority operation and consumes no qualification budget.

| Field | Type | Rules |
|---|---|---|
| review_request_id/request_digest | opaque/digest | Canonical protected-lifecycle replay identity. |
| evidence_candidate_id/candidate_close_generation | opaque/integer | Exact closed candidate and immutable close version. |
| evidence_digest/cleanup_evidence_digest | digests | Complete bounded proof and successful cleanup binding. |
| source_revision/service_revision/contract_revision | digests | Must equal candidate and fresh installed observations. |
| controller_identity_digest | digest | Exact reviewed controller identity. |
| remote/project/fixture identities | opaque strings | Exact disposable qualification scope. |
| reviewer_identity_digest | digest | Derived from protected operator authorization. |
| requested_decision | enum | `accepted` or `rejected`. |
| proposed_review_decision_id/proposed_promotion_id/proposed_authority_binding_id | opaque strings | Preallocated at reservation; immutable before any authority prepare. |
| expected_binding_digest | digest | Canonical digest of every proposed binding field. |
| lifecycle_generation | integer | CAS generation at reservation. |
| phase | enum | `reserved`, `binding_prepared`, `committed`, `terminal`. |

Uniqueness is `(remote_identity, project_identity, operation=review,
review_request_id)`. Exact digest replay resumes or returns the same result;
changed input or decision conflicts before effect. One closed
candidate/digest/revision tuple yields at most one accepted or rejected result.
A rejected candidate requires new evidence.

## CapabilityReviewDecision

One terminal lifecycle-owned result over a `CapabilityReviewRequest`.

| Field | Type | Rules |
|---|---|---|
| review_decision_id/review_request_id | opaque strings | Exact review result and request. |
| evidence_candidate_id/candidate_close_generation | opaque/integer | Exact immutable candidate binding. |
| reviewer_identity_digest | digest | Derived authorization identity; never caller display text. |
| decision/reason_code | enum/safe code | `accepted` or `rejected`; reason required for rejection. |
| request_digest/lifecycle_generation | digest/integer | Exact replay and commit generation. |
| decided_at/expires_at | timestamps | Accepted review must be current for promotion. |

## CapabilityPromotion

The lifecycle-owned promotion receipt and capability projection. Ordinary CLI,
MCP, and the storage authority cannot create it.

| Field | Type | Rules |
|---|---|---|
| promotion_id | opaque string | Lifecycle-generated. |
| review_decision_id/evidence_candidate_id | opaque strings | Exact accepted decision and candidate. |
| capability_id/remote/project/fixture identities | opaque strings | Exact disposable proof scope. |
| source_revision/service_revision/contract_revision/evidence_digest | digests | Must match current observation and review. |
| authority_binding_id/binding_generation | opaque/integer | Exact prepared then active enforcement binding. |
| phase | enum | `validation_pending`, `supported`, or `revoked`. |
| support_tier/adoptable | enum/boolean | `implemented_unproven`/false while validation is pending; `proven`/true only after final acceptance and while binding is current. |
| request_id/request_digest | opaque/digest | Same exact review replay boundary. |
| promoted_at/expires_at | timestamps | Freshness bound. |

The lifecycle preallocates every decision/promotion/binding identity and binding
digest in the reserved review, then obtains that exact non-authorizing prepared
binding. It commits review decision, validation promotion, and capability
projection atomically only through the shared target transaction owner. Exact
replay byte-compares and activates the binding after proving that receipt.
Pending validation remains `implemented_unproven`/non-adoptable but may permit
`future` only for the exact disposable fixture. Mixed state remains closed.

## CapabilityAcceptanceRequest

The protected lifecycle replay boundary for finalizing the post-promotion
ordinary fixture evidence. Public input contains only the exact promotion ID,
request ID, confirmation, and registered remote/project selectors. Reviewer
identity is derived from protected operator authorization; evidence is derived
through typed read-only sync, job, workspace, cleanup, capability, and
unrelated-state ports rather than caller fields.

| Field | Type | Rules |
|---|---|---|
| acceptance_request_id/request_digest | opaque/digest | Canonical protected replay identity. |
| promotion_id/authority_binding_id | opaque strings | Exact validation promotion and active binding. |
| reviewer_identity_digest | digest | Derived protected operator authorization. |
| starting_lifecycle_generation | integer | Shared target CAS generation. |
| observed_evidence_digest | digest/null | Lifecycle-derived closed evidence; never caller asserted. |
| phase | enum | `reserved`, `observing`, `evidence_closed`, `committed`, `revocation_pending`, `terminal`. |
| outcome/reason_code | enum/safe code | `complete` or `failed`; reason required for failure. |

Uniqueness is `(remote_identity, project_identity,
operation=acceptance_finalize, request_id)`. Exact digest replay resumes or
returns the same terminal result; changed promotion/input conflicts. A crash
resumes only the stored phase under the shared target lock/generation. Failure
atomically commits non-adoptable/failed lifecycle state before binding revoke.

## CapabilityAcceptance

The lifecycle-owned terminal result of the post-promotion ordinary fixture
journey.

| Field | Type | Rules |
|---|---|---|
| acceptance_id/promotion_id | opaque strings | Exact promoted disposable scope. |
| sync/ci/cleanup operation and object identities | opaque strings | Newly created normal-policy lineage only. |
| policy/promotion/evidence/binding identities | opaque strings | Must match every object and current capability. |
| ordinary_evidence_digest | digest | Binds `qualification:null`, replay/conflict, cleanup, job-result, unrelated-state, and rollback evidence. |
| outcome/reason_code | enum/safe code | `complete` or `failed`; reason required for failure. |
| request_id/request_digest/lifecycle_generation | opaque/digest/integer | Exact replay and lifecycle CAS boundary. |
| completed_at | timestamp | Terminal evidence time. |

`complete` atomically changes lifecycle promotion phase to `supported`, adds the
immutable ordinary evidence ID, and projects `proven`/adoptable only after the
exact binding remains active. `failed` changes phase to `revoked`, commits
non-adoptable first, and requires binding revocation. It never edits storage
objects or authority operation rows.

## CapabilityRevocation

A separate lifecycle operation over one existing promotion. It is unique on
`(remote_identity, project_identity, operation=revoke, request_id)` and binds
promotion ID, reason, reviewer authorization, request digest, and lifecycle
generation. The lifecycle commits `adoptable=false` first, then asks the
authority to mark the exact binding `revoked`. Lost acknowledgement stays
non-adoptable and exact replay reconciles deactivation.

## AuthorityAdoptionBinding

The sole cross-store record in the authority repository. It enforces a
lifecycle decision but owns no review or support policy.

| Field | Type | Rules |
|---|---|---|
| authority_binding_id/binding_generation | opaque/integer | Binding ID is lifecycle-preallocated in the reserved review; only the CAS generation is authority-generated. |
| remote/project/platform/fixture identities | opaque strings | Exact reviewed disposable scope. |
| review_decision_id/promotion_id | opaque strings | Exact preallocated lifecycle lineage. |
| evidence_candidate_id/evidence_digest | opaque/digest | Closed candidate identity and digest. |
| source_revision/service_revision/controller_revision/contract_revision | digests | Exact current implementation boundary. |
| lifecycle_request_id/request_digest/lifecycle_generation | opaque/digest/integer | Exact semantic-owner receipt binding. |
| binding_digest | digest | Must equal the digest preallocated in the reserved review. |
| expires_at/revocation_generation | timestamp/integer-null | Freshness and later revoke fence. |
| phase | enum | `prepared`, `active`, or `revoked`. |

`prepared` grants no policy or mutation authority. It becomes `active` only on
exact replay that proves the matching committed lifecycle promotion. Any
missing, mixed, stale, conflicting, or unknown evidence is non-adoptable.

## QualificationAdmission

A sealed, short-lived proof-candidate admission for one disposable live fixture.
It is not a normal project storage policy and cannot make a capability adoptable.

| Field | Type | Rules |
|---|---|---|
| admission_id | opaque string | Lifecycle-authority generated; never caller selected. |
| remote_identity/project_identity | opaque strings | Exact registered disposable proof scope. |
| fixture_identity | opaque string | Newly created acceptance fixture only. |
| source_revision/service_revision | digests | Exact clean source and installed service pair. |
| controller_identity_digest | digest | Exact supervised controller UID/GID, PID/start, executable, unit/cgroup, config, and connection expectation. |
| evidence_candidate_id | opaque string | Lifecycle-owned candidate binding for all produced storage receipts. |
| allowed_operations | fixed set | Publication, materialization, reference, cleanup, and reconciliation cases required by the acceptance matrix only. |
| operation_budget | positive integer | Finite maximum fixed before admission. |
| issued_at/expires_at | timestamps | Short bounded lifetime; expiry closes admission. |
| state | enum | `sealed`, `active`, `closing`, `closed`, `failed`, `indeterminate`. |
| cleanup_evidence_digest | digest/null | Required to close successfully; incomplete cleanup rejects promotion. |

Validation rules:

- The supported lifecycle mints the admission only after explicit human
  authorization; ordinary CLI/MCP input cannot construct or widen it.
- The fixed acceptance harness consumes it only through the authenticated
  controller process and exact fixture scope.
- Reserving a canonical qualification operation atomically links its operation
  row and decrements the remaining admission budget; replay does not decrement
  twice.
- It never changes `AuthorityPolicy`, `support_tier`, or `adoptable`.
- Review, promotion, and revocation never reserve an
  authority operation and never decrement this admission's budget.
- Missing, changed, expired, exhausted, replay-conflicting, or incompletely
  closed admission evidence refuses further mutation and cannot become proof.

## AuthorityPolicy

Future-object selection for one exact registered scope.

| Field | Type | Rules |
|---|---|---|
| policy_id | opaque string | Authority-generated. |
| remote_identity | opaque string | Required registered remote. |
| project_identity | opaque string | Required durable project identity. |
| mode | enum | `legacy` or `future`; default `legacy`. |
| effective_generation | positive integer | Monotonic policy generation. |
| changed_by | caller identity digest | Derived from authenticated peer/application authorization. |
| request_id/request_digest | opaque/digest | Replay-safe transition identity. |
| admission_basis | typed binding | Exact current promotion/evidence and active authority-binding identities for `future`; null for `legacy`. |
| changed_at | timestamp | Durable transition time. |

Validation rules:

- `future` requires explicit confirmation, a current lifecycle promotion, and
  the exact active authority adoption binding for the same scope. A
  `validation_pending` promotion permits only its exact disposable fixture;
  general scope requires `supported` plus completed ordinary evidence.
- Returning to `legacy` affects later creation only.
- A transition never adopts, relocates, rewrites, or deletes an existing object.
- Reusing a request ID with a different canonical transition is a conflict.

## CanonicalOperationRequest

The exact replay and authorization boundary for one operation.

| Field | Type | Rules |
|---|---|---|
| operation_id | opaque string | Authority-generated durable identity. |
| operation_type | enum | `policy`, `qualification`, `publish`, `materialize`, `reference_open`, `reference_close`, `preview`, `cleanup`, `reconcile`. Review/promotion/revocation are forbidden lifecycle operations. |
| request_id | replay-safe string | Caller-supplied within bounded syntax. |
| request_digest | SHA-256 digest | Canonical exact request; excludes transport-only cursor/deadline presentation. |
| authorization_id | opaque string | One operation authorization minted by the authenticated controller. |
| controller_epoch/sequence | opaque/integer | Exact current controller process connection and monotonic message position. |
| caller_identity_digest | digest | Derived from authenticated peer plus application scope. |
| remote_identity | opaque string | Required exact remote. |
| project_identity | opaque string | Required exact project. |
| relationship_id | opaque/null | Required for generation publication/reference operations. |
| workspace_id | opaque/null | Required for materialization/CI cleanup operations. |
| job_id | opaque/null | Required for job-bound materialization/reference/cleanup. |
| target_object_id | opaque/null | Required for existing-object operations. |
| canonical_evidence_digest | digest | Digest of manifest/count or policy/reference projection. |
| qualification_admission_id/evidence_candidate_id | opaque/null | Both required for qualification operations and forbidden for normal policy operations. |
| promotion_id/authority_binding_id | opaque/null | Both required for normal `future` operations and forbidden for qualification operations. Normal operations retain `qualification:null`. |
| phase | enum | `reserved`, `receiving`, `verified`, `effect_intent`, `effect_applied`, `terminal`. |
| outcome | enum/null | `accepted`, `completed`, `already_completed`, `retained`, `refused`, `unsupported`, `unknown`, `failed`, `indeterminate`. |
| reason_code | safe code/null | Required for every non-success outcome. |
| created_at/updated_at | timestamp | Durable lifecycle evidence. |

Uniqueness and replay:

- Unique `(remote_identity, project_identity, operation_type, request_id)`.
- Exact digest replay returns or resumes this row.
- Different digest under the same key is `request_id_conflict` before effect.
- An operation never changes target object after `effect_intent`.

## AuthorityOwnedObject

One object whose lifecycle is controlled by the dedicated authority.

| Field | Type | Rules |
|---|---|---|
| object_id | opaque string | Authority-generated; public identity and only cleanup selector. |
| object_kind | enum | `sync_generation`, `ci_materialization`, `retained_artifact`. |
| remote_identity/project_identity | opaque strings | Exact durable owner scope. |
| relationship_id | opaque/null | Required for generation and its retained artifacts. |
| workspace_id/job_id | opaque/null | Required for CI materialization and its artifacts. |
| parent_object_id | opaque/null | Artifact or materialization relationship; same project only. |
| created_by_operation_id | opaque string | Exact creation operation. |
| lifecycle | enum | `staging`, `verified`, `accepted`, `active`, `superseded`, `retained`, `eligible`, `quarantining`, `quarantined`, `removed`, `indeterminate`. |
| policy_id/policy_generation | opaque/integer/null | Future-object policy in force at normal creation; null for qualification objects. |
| qualification_admission_id/evidence_candidate_id | opaque/null | Both required instead of policy fields for a proof object; bind fixture ancestry and budget. |
| promotion_id/evidence_id/authority_binding_id | opaque/null | Required with policy fields for normal authority-owned objects; qualification fields remain null. |
| retention_policy_digest | digest | Exact immutable policy projection. |
| content_evidence | typed record | Generation binding or materialization binding below. |
| filesystem_identity | private typed record | Device/inode/mount/marker/parent identity; never public. |
| known_bytes | integer/null | Non-negative bounded apparent bytes. Null stays unknown. |
| created_at/accepted_at/removed_at | timestamp/null | Lifecycle evidence. |

Validation rules:

- Exactly one of normal policy fields or qualification admission/evidence
  ancestry is present. Normal objects require `future`, `qualification:null`,
  exact active binding and promotion, plus either supported/proven/complete
  scope or validation-pending/implemented-unproven/pending-ordinary exact
  disposable-fixture scope. Qualification objects require one exact active
  `QualificationAdmission` and remain fixture-bound.
- A legacy/foreign object can be projected for status but cannot be inserted as
  owned by resemblance, path, UID, name, age, or migration.
- `accepted` generation content evidence is immutable.
- `removed` is terminal; a replacement cannot reuse the object ID or receipt.

## GenerationBinding

Immutable content evidence for one sync generation object.

| Field | Type | Rules |
|---|---|---|
| remote_identity | opaque string | Exact target remote. |
| project_identity | opaque string | Exact project. |
| relationship_id/workspace_id | opaque strings | Exact Spec 033 owner tuple. |
| request_id | replay-safe string | Original publication request. |
| generation_id | opaque digest ID | Exact Spec 033 generation. |
| manifest_digest | digest | Screened canonical manifest. |
| archive_manifest_digest | digest | Canonical transferred-entry projection. |
| file_count | bounded integer | Must equal verified inventory. |
| byte_count | bounded integer | Must equal verified payload bytes. |
| accepted_at | timestamp | Recorded after durable publish. |

Uniqueness:

- Unique generation ID and content tuple within one relationship.
- Exact request replay maps to one object and one acceptance receipt.
- A different tuple requires a new generation/object/request identity.

## RelationshipCurrentSelection

The authority's physical current-generation truth.

| Field | Type | Rules |
|---|---|---|
| relationship_id | opaque string | Unique row. |
| object_id/generation_id | opaque strings | Must reference an accepted generation in the same scope. |
| selection_generation | positive integer | Monotonic CAS generation. |
| operation_id | opaque string | Publication that advanced selection. |
| changed_at | timestamp | Durable time. |

The Spec 033 journal remains the application projection. A lost projection
update is reconciled from this selection and the original operation receipt.
Readers never select a `staging` or `verified` candidate.

## MaterializationBinding

Immutable control evidence for one CI workspace object plus mutable interior.

| Field | Type | Rules |
|---|---|---|
| project_identity/job_id/workspace_id | opaque strings | Exact Spec 032 scope. |
| source_generation_object_id | opaque/null | Accepted authority source when used; never a path. |
| source_identity_digest | digest | Exact submitted source identity. |
| materialization_id | opaque string | Exact interior generation. |
| workspace_mode | enum | `isolated` or `ephemeral` only. |
| cleanup_policy | enum | Existing `retain`, `always`, `on-success`, or `ephemeral`. |
| root_identity_digest | digest | Public-safe digest of private root evidence. |
| writable_interior_identity | private record | Exact child/mount identity; not public. |
| created_at | timestamp | Before workload lease. |

Validation rules:

- The object root and authority record stay service-owned.
- Only the writable interior may be exposed read-write, and only through a
  qualified private mount namespace.
- Managed source/accepted generations are exposed read-only.
- No host path is accepted from the job or projected publicly.

## MaterializationLease

Service-owned active-reference evidence for one workload exposure.

| Field | Type | Rules |
|---|---|---|
| lease_id | opaque string | Authority-generated. |
| object_id/job_id/workspace_id | opaque strings | Exact materialization scope. |
| lifecycle_generation | positive integer | Rotates on open/revoke/restart recovery. |
| mount_identity_digest | digest | Exact qualified private mount. |
| state | enum | `reserved`, `active`, `closing`, `closed`, `revoked`, `indeterminate`. |
| opened_at/heartbeat_at/expires_at | timestamp | Finite lease. |
| closed_at | timestamp/null | Required before cleanup eligibility. |

Any `reserved`, `active`, `closing`, unknown, stale, or indeterminate lease is
an active reference and forces retention. A missing row is not proof of absence
unless the complete reference index and lifecycle generation are proven.

## RetentionPolicyProjection

The exact application policy supplied at object creation or release.

| Field | Type | Rules |
|---|---|---|
| policy_digest | digest | Canonical immutable projection. |
| policy_kind | enum | `current`, `pending`, `retain`, `release`, `window`. |
| release_condition | safe enum/null | Exact product condition, never inferred. |
| retain_until | timestamp/null | Required for a time window. |
| source_policy_generation | positive integer | Existing app policy generation. |
| observed_at | timestamp | Freshness. |

Missing/invalid/stale policy means retain. Storage pressure cannot alter it.

## ReferenceSnapshot

Bounded application and service-owned evidence used by preview/cleanup.

| Field | Type | Rules |
|---|---|---|
| snapshot_id | opaque string | Authority-generated. |
| object_id | opaque string | Exact candidate. |
| current_selection_generation | integer/null | Required for generation cleanup. |
| workspace_index_generation | integer/null | Required for CI cleanup. |
| active_reference_counts | bounded typed map | Jobs, leases, mounts, readers, pins, processes, containers; each exact zero or unknown. |
| complete | boolean | False on missing/stale/contradictory source. |
| digest | digest | Canonical snapshot. |
| observed_at/expires_at | timestamp | Short freshness bound. |

Only a complete, fresh snapshot with every required count exactly zero can
support eligibility. An unavailable observer produces unknown, never zero.

## ReclamationPreview

Read-only reviewed candidate set.

| Field | Type | Rules |
|---|---|---|
| preview_id | opaque string | Authority-generated. |
| remote_identity/project_identity | opaque strings | Exact scope. |
| inventory_generation | positive integer | Authority repository generation. |
| policy_generation | positive integer | Future/retention policy generation. |
| candidate_digest | digest | Canonical ordered candidate decisions. |
| candidates | up to 10,000 PreviewCandidate rows | No paths or contents. |
| estimated_reclaimable_bytes | integer | Sum of known eligible bytes only. |
| complete | boolean | False means execution is forbidden. |
| created_at/expires_at | timestamp | Maximum 15-minute lifetime. |

### PreviewCandidate

| Field | Type | Rules |
|---|---|---|
| object_id/object_kind | opaque/enum | Exact inspected object. |
| lifecycle | enum | Observed lifecycle. |
| decision | enum | `eligible` or `protected`. |
| reason_code | safe code | Stable explanation. |
| estimated_bytes | integer/null | Unknown omitted from totals. |
| object_evidence_digest | digest | Identity/content evidence at preview. |
| reference_snapshot_digest | digest | Exact reference evidence. |

Execution considers no object outside this immutable candidate list and removes
only `eligible` objects still eligible after a fresh final check.

## CleanupIntent and CleanupOutcome

Durable reconciliation evidence for one exact object.

| Field | Type | Rules |
|---|---|---|
| cleanup_id | opaque string | One per operation/object. |
| operation_id/preview_id/object_id | opaque strings | Exact authorized target and reviewed preview. |
| expected_object_evidence_digest | digest | Final identity expectation. |
| expected_reference_digest | digest | Preview reference evidence; freshly replaced before effect. |
| final_entry_evidence_digest | digest/null | Exact empty quarantine entry, opened identity, and private parent generation committed before final removal. |
| phase | enum | `intent`, `quarantined`, `removing`, `final_remove_intent`, `removed`, `terminal`. |
| outcome | enum/null | `completed`, `already_completed`, `retained`, `refused`, `failed`, `indeterminate`. |
| reason_code | safe code/null | Required for non-completed outcome. |
| estimated_bytes | integer/null | Preview estimate. |
| observed_reclaimed_bytes | integer/null | Known only after completed removal. |
| job_result_digest_before/after | digest/null | CI cleanup proves immutable terminal result. |
| created_at/updated_at/completed_at | timestamp | Durable phase evidence. |

`observed_reclaimed_bytes` excludes unknown, retained, refused, failed,
indeterminate, and partial removals. A second exact replay returns the same
terminal row. A changed replacement is protected and cannot become the target.

## LegacyProjection

Read-only compatibility evidence for a non-authority object.

| Field | Type | Rules |
|---|---|---|
| legacy_identity | opaque digest | Projection identity, not cleanup authority. |
| project/relationship/workspace identity | opaque/null | Existing service projection only. |
| kind/lifecycle | safe values | Current compatibility state. |
| authority_status | literal | `legacy_not_owned`. |
| eligibility | literal | `not_authority_candidate`. |

No legacy projection is related to `AuthorityOwnedObject` by foreign key and no
operation may convert it in this feature.

## State transitions

### Review, promotion, and revocation

```text
lifecycle review accepted: reserved -> binding_prepared -> committed -> terminal(accepted)
lifecycle review rejected: reserved -> terminal(rejected)
authority binding: absent -> prepared(non-authorizing) -> active -> revoked
acceptance finalize: reserved -> observing -> evidence_closed -> committed -> terminal(complete)
                                            \-> revocation_pending -> terminal(failed)
revocation: reserved -> lifecycle non-adoptable committed -> binding revoked -> terminal
```

- Lifecycle commit is the semantic decision point; there is no transaction
  across repositories.
- Exact replay may activate only the prepared binding named by the committed
  promotion receipt.
- Lifecycle non-adoptable state is committed before binding revocation, so a
  crash or lost acknowledgement cannot leave new mutation authorized.
- Unknown or contradictory lifecycle/binding generations close admission.
- Lock order is shared hosting target lock, then authority binding lock; release
  is reverse. No code may acquire them in the opposite order.

### Publication

```text
reserved -> receiving -> verified -> effect_intent -> accepted -> terminal
    |           |           |             |
    +-----------+-----------+-------------+-> refused | failed | unknown | indeterminate
```

- Before `effect_intent`, a known failure is refusal/failure with no accepted
  object.
- After a filesystem effect may have occurred, missing evidence is unknown or
  indeterminate until the same operation is reconciled.
- Acceptance/current selection is committed only after payload and destination
  parent durability flushes succeed.
- Current selection advances only to `accepted`.

### Materialization and lease

```text
staging -> active -> superseded/retained -> eligible
              |             ^
              +-- lease active/unknown --+

lease: reserved -> active -> closing -> closed
                    |          |
                    +----------+-> revoked | indeterminate
```

Any non-closed lease protects the object.

### Cleanup

```text
eligible -> intent -> quarantined -> removing -> final_remove_intent -> removed -> terminal(completed)
    |          |          |            |
    +----------+----------+------------+-> retained | refused | failed | indeterminate
```

- Eligibility is re-evaluated at `intent` and immediately before quarantine.
- Once quarantined, restart recovery operates only on the original private
  object identity.
- Absence without a matching completed receipt or flushed exact
  `final_remove_intent` is indeterminate, not success.

## Cross-model invariants

1. Project/remote/relationship/workspace/job identities must agree across every
   related row; cross-project foreign keys are forbidden.
2. No path, source content, credential, argv/environment, or unrestricted host
   data appears in an application/public value object.
3. The authority repository is the only owner of physical object and storage
   operation truth. The lifecycle nested state is the only semantic owner of
   review, promotion, acceptance, revocation, and capability truth and is
   persisted only through the shared hosting target transaction port; neither
   side reads or writes the other's storage directly.
4. Policy/references are checked before effects; the service mechanism still
   independently checks private ownership and exact filesystem identity.
5. One request identity plus one canonical digest produces at most one physical
   object or one cleanup outcome.
6. Current, pending, unknown-acknowledgement, active, referenced, retained,
   unexpired, foreign, ambiguous, and incomplete objects are never eligible.
7. Cleanup outcome never mutates the terminal job outcome/result.
8. Resolver/DNS evidence never participates in storage qualification,
   authorization, object identity, or cleanup.
9. Unproven mutation exists only under one sealed `QualificationAdmission` or
   the exact validation-pending normal-admission predicate. The latter requires
   `qualification:null`, future policy, active binding, implemented-unproven/
   non-adoptable/pending-ordinary state, and exact disposable fixture; neither
   can authorize another scope or support claim.
10. Normal `future` mutation requires both a current lifecycle promotion and
    exact active `AuthorityAdoptionBinding`; qualification context is null.
11. Review/revocation never consume qualification budget. A prepared binding is
    non-authorizing, and every mixed cross-store state fails closed.
12. Feature 052 lifecycle state is nested under the Feature 051 shared target
    transaction owner; a second hosting state file/database or direct outer
    state write is forbidden.
