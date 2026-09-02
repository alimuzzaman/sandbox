# Data Model: Owned Storage Authority

The authority model is path-free at every application and public boundary.
Filesystem locators and kernel identity fields exist only inside the private
repository and Linux adapter. Existing sync, job, workspace, and resource
records remain authoritative for their current application domains.

## AuthorityCapability

One observation for an exact remote platform and operating mode.

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
| observed_at/expires_at | timestamp | Freshness bound. Expired evidence closes mutation admission. |
| reason_code | safe code | Explains non-adoptability without host paths/details. |

Validation rules:

- Any missing, stale, unknown, contradictory, or drifted primitive makes
  `adoptable=false`.
- A capability report cannot promote itself; the evidence ID and support tier
  change only through the separately reviewed qualification workflow.
- Storage and resolver capability identities are disjoint.

## CapabilityReviewDecision

One durable protected-lifecycle decision over one closed evidence candidate.

| Field | Type | Rules |
|---|---|---|
| review_decision_id | opaque string | Lifecycle-generated durable identity. |
| evidence_candidate_id | opaque string | Exact closed qualification candidate. |
| evidence_digest | digest | Complete bounded acceptance bundle digest. |
| source_revision/service_revision | digests | Must equal the candidate and current installed pair. |
| remote/project/fixture identities | opaque strings | Must equal the qualification scope. |
| reviewer_identity_digest | digest | Derived from protected operator authorization; never caller display text. |
| decision | enum | `accepted`, `rejected`, or `revoked`. |
| reason_code | safe code/null | Required for rejected/revoked; null for accepted. |
| request_id/request_digest | opaque/digest | Exact replay-safe lifecycle decision. |
| decided_at/expires_at | timestamps | Accepted review must be current to promote. |

## CapabilityPromotion

The atomic promotion/revocation receipt. It is requested only by the protected
remote lifecycle and persisted by the authority through the authenticated
controller; it is never self-generated or requested by ordinary project CLI or MCP.

| Field | Type | Rules |
|---|---|---|
| promotion_id | opaque string | Lifecycle-generated. |
| review_decision_id/evidence_candidate_id | opaque strings | Exact accepted decision and candidate. |
| capability_id/remote_identity | opaque strings | Exact capability scope. |
| source_revision/service_revision/evidence_digest | digests | Must all match current observations and review. |
| support_tier | enum | `proven` or `drifted`; no other promotion value. |
| adoptable | boolean | True only for current `proven`. |
| request_id/request_digest | opaque/digest | Replay-safe lifecycle operation. |
| promoted_at/expires_at/revoked_at | timestamps/null | Expiry or revoke closes adoption immediately. |

The protected lifecycle authenticates the authorized operator, rechecks the
closed admission, cleanup digest, evidence/revisions/controller identity, and
accepted review, then writes the review/promotion relationship and capability
projection in one transaction. Exact replay returns the receipt; digest
conflict refuses. Rejection, revocation, expiry, drift, or revision skew sets
`adoptable=false` without deleting owned objects.

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
| evidence_candidate_id | opaque string | Binds all produced receipts to one review candidate. |
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
| changed_at | timestamp | Durable transition time. |

Validation rules:

- `future` requires a current adoptable capability and explicit confirmation.
- Returning to `legacy` affects later creation only.
- A transition never adopts, relocates, rewrites, or deletes an existing object.
- Reusing a request ID with a different canonical transition is a conflict.

## CanonicalOperationRequest

The exact replay and authorization boundary for one operation.

| Field | Type | Rules |
|---|---|---|
| operation_id | opaque string | Authority-generated durable identity. |
| operation_type | enum | `policy`, `qualification`, `review`, `publish`, `materialize`, `reference_open`, `reference_close`, `preview`, `cleanup`, `reconcile`. |
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
| retention_policy_digest | digest | Exact immutable policy projection. |
| content_evidence | typed record | Generation binding or materialization binding below. |
| filesystem_identity | private typed record | Device/inode/mount/marker/parent identity; never public. |
| known_bytes | integer/null | Non-negative bounded apparent bytes. Null stays unknown. |
| created_at/accepted_at/removed_at | timestamp/null | Lifecycle evidence. |

Validation rules:

- Exactly one of normal policy fields or qualification admission/evidence
  ancestry is present. Only objects created while policy is `future` on a proven capability, or
  under one exact active `QualificationAdmission`, may enter this table as
  authority-owned. Qualification objects remain fixture-bound and cannot be
  exposed to another project or normal policy.
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
3. The authority repository is the only owner of physical object and operation
   truth; existing repositories remain owners of their application truth.
4. Policy/references are checked before effects; the service mechanism still
   independently checks private ownership and exact filesystem identity.
5. One request identity plus one canonical digest produces at most one physical
   object or one cleanup outcome.
6. Current, pending, unknown-acknowledgement, active, referenced, retained,
   unexpired, foreign, ambiguous, and incomplete objects are never eligible.
7. Cleanup outcome never mutates the terminal job outcome/result.
8. Resolver/DNS evidence never participates in storage qualification,
   authorization, object identity, or cleanup.
9. Unproven mutation exists only under one sealed `QualificationAdmission`;
   it cannot change support/policy and incomplete closure cannot promote proof.
