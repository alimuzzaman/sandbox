# Data Model: Recoverable Delivery Outcomes

All public models are closed schema objects; schema-v1 is the baseline, with the explicitly additive `source_schema=2` projection described below. Reject unknown fields, booleans used as integers, non-finite numbers, invalid UTF-8/NUL text, oversized collections and unsupported schema versions. Canonical JSON digests use the existing canonical-digest utility. Never serialize an arbitrary upstream dictionary or exception.

## Common types and bounds

| Type | Definition |
|---|---|
| Identifier | Nonempty allowlisted identifier, at most 128 UTF-8 bytes; UUID delivery IDs are canonical lowercase |
| Owner request ID | Opaque owner-compatible ID, at most 256 ASCII bytes; letters/digits plus `._:/-`, starting with a letter/digit. Slashes have no path meaning. Request selectors, summaries, evidence and request-key lookup preserve exact bytes. |
| Digest | Existing sha256 canonical digest encoding; full value, never a display abbreviation |
| Git revision | Full resolved commit ID in the repository's supported format; no branch name accepted as exact revision |
| Source artifact | Closed mapping from the original Git source commit to the deployed artifact: schema_version=1, kind=git_commit or git_subtree, root_relative, revision; normalized POSIX path and full lowercase commit only |
| Timestamp | UTC RFC 3339 timestamp, or null with an explicit missing reason |
| Safe reason | One closed reason code plus redacted human explanation, at most 256 bytes |
| Safe reference | Kind + opaque identifier/digest, at most 512 bytes; no raw filesystem/log URL, credentials, query tokens or command/environment payload |
| Evidence state | known, partial, missing, unsupported, expired, conflicting, or not_applicable |
| Observation result | passed, failed, pending, unknown, or not_applicable |
| Collections | Services/images/requirements at most 32 each; route hosts at most 20; recovery links at most 16; references at most 32 |

Every evidence object contains source_kind, observed_at, target_digest, applicability (required/optional/not_applicable), state, result, and reason. Missing a required dimension is different from not_applicable. A successful observation does not set the delivery's success boolean.

## ExactTarget

Fields: schema_version, project_identity, project_root_digest, remote_name, registered_host_digest, machine_identity, target_kind, environment, label, instance_id, instance_incarnation_id, workspace_id, runtime_identity.

target_kind is hosted, deploy, or preview. Hosted targets require environment and runtime_identity; instance targets require the exact selected label and, once proved, instance/incarnation. Unknown incarnation is null plus evidence state; it must never match a known incarnation. The stable request scope includes project, registered remote and environment/label. The resolved incarnation is an additional exact join condition, not a way to silently retarget an accepted request.

Changing registration identity, project root/source identity, environment, label, machine identity or incarnation makes a conflicting observation ineligible for a join. A reused remote display name is insufficient.

## RequestedOutcome

Fields: kind, target_digest, application, control, configuration_digest, route_contract_digest, requirements, requested_at.

- kind: hosted_apply, immutable_activation, deploy_exposure, or preview_creation.
- application: source_identity, commit, optional source_artifact, dirty_policy (clean_required/overlay_declared/not_applicable), dirty_digest, artifact_digest, config_digest, plan_digest, proof_digest. Ordinary apply requires clean_required and no dirty digest. Deploy/preview preserve their existing explicit overlay policy and record its digest; they do not inherit ordinary apply's clean-only gate. `commit` remains the original submitted/job source commit; `source_artifact.revision` is the deployed Git artifact when a nested source is selected.
- control: Sandbox source commit, source runtime revision, installed controller runtime revision, and capability versions. Application and control fields never substitute for each other.
- requirements: closed typed declarations for workload, initializer, runtime identity/health, exposure, public release identity and edge proof. Each is required, optional, or not_applicable, with its source. A contract may not convert missing proof to not_applicable after admission.

The canonical requested-outcome digest is frozen before effects and binds request reuse. Secret configuration contributes only an existing nonsecret authority/config digest, never values or low-entropy secret hashes introduced by this feature.

### SourceArtifact binding

`SourceArtifact` is a closed object with exactly `schema_version`, `kind`, `root_relative` and `revision`. `schema_version` is the exact integer 1. `kind=git_commit` requires `root_relative="."` and `revision` equal to the original `application.commit`; `kind=git_subtree` requires a nonempty proper prefix relative to the actual owning checkout. `root_relative` is normalized POSIX spelling, at most 1024 UTF-8 bytes and 64 components; reject absolute paths, empty components, `.`, `..`, NUL/control text and noncanonical spelling except the identity-root `.`. `revision` is a full lowercase commit in the repository's supported format. The kind is derived from the actual checkout/root relationship, never from `C != T` or caller input.

For an extended ordinary operation, the identical object is stored as `RecoveryAdmissionProjection.source.artifact` and `RequestedOutcome.application.source_artifact`, with `source_schema=2`; `application.commit` and the original job/admission source commit remain `C`, while the deployed artifact revision is `T`. Existing identity records use `source_schema=1` and omit the optional artifact field. Missing artifact never implies a historical subtree mapping and cannot authorize a `C != T` join. Existing `artifact_digest` remains the OCI slot and is not reused for a Git commit.

## RecoveryAdmissionProjection

This is a projection of the existing ordinary hosting_operation, not a new authority object.

Fields: source_schema, job_id, request_id, project/root/source bindings, target, starting_generation, accepted_at, accepted_before_effects, existing operation digest, nonsecret config/runtime/edge bindings, safe authority reference, and evidence state. Extended `source_schema=2` projections include the frozen `source.artifact`; legacy `source_schema=1` projections retain their existing shape and identity meaning.

The underlying existing recovery receipt retains its current authority fields and size limit. Additive delivery identifiers are diagnostic links only. The original admission must be committed and read back through its owning repository before the first protected effect. A diagnostic row alone cannot satisfy admission.

## DeliveryOperation

Fields:

- schema_version, operation_id, request_id, job_id, kind, intent_digest, target, requested_outcome.
- accepted_at, started_at, updated_at, finished_at; phase from admitted/source/instance/initializer/runtime/route/edge/cleanup/terminal/unknown.
- execution_state from accepted/running/succeeded/failed/cancelled/timed_out/interrupted/unknown.
- delivery_state from incomplete/succeeded/failed/unknown; delivery_succeeded is true only for succeeded.
- evidence_completeness from complete/partial/missing/expired/conflicting/unsupported.
- admission, creation, runtime, routes, edge and authority: typed evidence blocks.
- Route evidence may retain a closed route_contract containing schemaVersion, delivery, primary_hostname, hostnames, release_expected, edge_binding and contract_digest. It contains public declarations only. Validation reconstructs the normalized route contract and compares its digest and fields; current inspection uses this frozen declaration, never a changed manifest.
- effects: at most 32 named effects, each with scope digest, configured/observed/removed/failed/unknown state and evidence time; successful cleanup does not erase an earlier configured effect.
- recovery_relations, retry_of, failure_stage, reason, safe references and next_action.
- history metadata, terminal_snapshot_digest, pinned_reason, bounded events.

The execution state mirrors its owner. The service computes delivery_state from the frozen requirements and exactly matching evidence. Initializer refusal, unavailable required revision, pending required edge proof, wrong application marker, or any required unverified hostname prevents succeeded. For extended nested-source operations, job/admission evidence joins on `C`, while runtime/edge/application-authority evidence joins on independently observed `T`; a missing or mismatched artifact mapping is partial/conflicting. A fully diagnosed failure can have evidence_completeness=complete and delivery_succeeded=false.

At an authoritative terminal boundary, freeze the terminal snapshot and digest. Repeated identical writes are idempotent; a differing terminal snapshot for the same operation is a conflict, never replacement. Later recovery is a linked operation/result, leaving the original terminal result intact. A terminal failed result with an active uncertainty fence is pinned. Pinned/open records count against the 128 protected-record cap and cannot be evicted.

## CreationReceipt

Fields: schema_version, operation_id, request_id, job_id, intent_digest, project_identity, project_root_digest, label, instance_id, instance_incarnation_id, relation, owner_commit_at, completion, completed_at, result_code.

CreationContext also contains frozen intent_digest and the closed nonsecret intent_fields: schema_version=1, delivery_intent_digest, target_scope_digest, project_identity, project_root_digest, label, instance_config_digest and create_allowed. intent_digest is the canonical digest of exactly intent_fields. target_scope_digest identifies the requested registered host/machine without an unproved incarnation. instance_config_digest covers the owner's resolved nonsecret instance configuration, including runtime/PHP/WordPress overrides and config-label policy; it contains no secret values or new secret-value hashes. The owner recomputes the project/root/label/config/create fields from the current resolved ensure input and verifies the supplied digest before effects. The receipt and transport retain that same frozen digest; changing any bound field conflicts under the same scoped request.

relation is created, reused, or unknown. Only the instance registry owner writes created/reused, in the transaction that selects or commits the incarnation. completion is pending, succeeded, failed, or unknown; a pending receipt proves only the relation already committed. Job terminal proof is separately read from the original job owner and is never inferred from completion alone.

Keep at most 32 terminal receipts and eight pending/uncertain receipts per instance, each at most 4 KiB. Never evict pending/uncertain receipts; capacity refusal occurs before a new ensure ownership commit. Eviction produces explicit expired receipt evidence and preserves the compact creation request guard below. Delivery history may retain a safe snapshot, but that snapshot cannot establish present ownership or permit cleanup.

## Durable request guards

Both guard stores are deny-only replay protection, not workload/cleanup authority. A guard is at most 1 KiB and contains schema_version, scope_digest, request_key_digest, operation_id, intent_digest, reserved_at and detail_state (present/expired/unknown). Request keys are domain-separated as explicit request ID or, when none was supplied, the controller-generated operation ID. Store only their canonical digests. Neither detail retention nor target/instance deletion removes a guard.

**Delivery owner:** sandbox/delivery/repository.py retains request_bindings in the existing outcomes.sqlite3 transaction with the first operation record. Scope is canonical project_identity + target kind + remote name + environment/label; changed root/registered host/incarnation remains part of the frozen intent and cannot make a used request look unused. At most 4,096 guards per controller, included in the existing 96 MiB cap. Guards never expire or evict. reserve_request(scope, request_key, operation_id, intent_digest) returns new/reserved only after persistence, existing with the original operation, delivery_request_expired when matching detail was evicted, or request_conflict for a different intent. An expired matching request cannot create another operation or effects. At capacity, a new key fails delivery_request_capacity before accepting the delivery record or starting protected effects; existing-key lookups remain available.

**Creation owner:** sandbox/server_config/creation_requests.py owns runtime/instance-creation/requests.sqlite3 on the instance controller, separate from per-instance rows. It stores at most 4,096 guards and uses an 8 MiB database cap plus at most 8 MiB rollback space. Scope is canonical project_identity + requested label on that controller; root, target and resolved configuration are intent fields. reserve_creation_request(scope, request_key, operation_id, intent_digest) persists the guard under the existing per-project lock before any pending/selected incarnation commit. A same-key different intent or operation refuses creation_request_conflict before effects. A same-key match returns the existing guard without execution; only a joined current receipt may describe its result. If the receipt is absent/evicted, return creation_request_unknown/creation_request_expired and never replay. New keys at capacity refuse creation_request_capacity. read_creation_request is mode=ro and never initializes, migrates, reserves or reconciles.

Creation guard reservation and instance receipt commit are two ordered stores, not one atomic transaction. A crash after reservation but before receipt commit leaves unknown work and forbids automatic replay. The receipt is still atomically committed with the incarnation in its existing owner. Guards survive receipt eviction and instance deletion. Both owners preserve guards without TTL/reset/namespace rotation; store corruption/unavailability fails closed. Capacity exhaustion is an explicit product limit; no compaction, expiry or cleanup in this feature reopens a used request.

## URLMutationResult

The creation guard store also retains a distinct apply-phase guard for the one covered reconcile continuation. It uses the same creation scope/request and frozen intent, domain-separated by phase, and counts toward the same permanent capacity. A used apply guard yields unknown without effects; creation completion is not apply completion.

Fields: operation_id, request_id, target_digest, expected_incarnation, observed_incarnation, before_observed_at, writes (home and siteurl only, each attempted/succeeded/failed/unknown/not_applicable), readback match results, finished_at, result_code.

Do not retain old/new secret-bearing URLs. Store validated public origins and bounded public paths only. Verify exact incarnation before each write and again before readback. Partial writes retain their individual results. Incarnation mismatch refuses further writes; no cross-instance rollback.

## Runtime and route evidence

RuntimeEvidence retains exact generation, observed service/image identifiers, installed application state/revision, declared health, initializer result and the owning proof references. For an extended source binding, its application revision is the independently observed `T`; it is null when the observation is missing or mismatched and is never filled from the requested artifact. The count limits above apply. Omitted required services or truncated proof produce partial evidence, never successful completeness.

RouteEvidence contains frozen requested hostnames, public-contract digest, deadline/budget, start/end timestamps, verification result, and per-host checks. Each host has DNS, HTTP redirect, HTTPS TLS, HTTPS application, query preservation, optional release identity and applicable edge references. Retain status, safe destination origin/path, query equality boolean, observed marker digests and timestamps. Never retain response bodies, Set-Cookie, Authorization, login URLs, raw query values or full request/response headers.

## Journal layout and retention

Use a new owner repository at runtime/delivery/outcomes.sqlite3 under the executing controller's Sandbox home. It owns its SQLite schema and all reads/writes. Schema v1 has operations, request_bindings and target_history metadata tables. A single operation document contains its bounded events and typed proof snapshots; do not split arbitrary log payload into an unbounded detail table.

- Maximum operation document: 128 KiB. Maximum event ring: 64 events, 1 KiB each, already included in that document. Set omitted_events when older events fall out.
- Terminal detail retention: 30 days, 64 per target and 512 globally. Apply the earliest applicable eviction rule; keep latest retained success separately indexed within what survives. Preserve all request_bindings guards when deleting detail or target metadata.
- Protected records: at most 128 open or uncertainty-pinned records; reject new covered admission when full.
- Target metadata: at most 1,024 entries. Evict only inactive entries without retained operations; lost metadata means unknown history.
- Request bindings: at most 4,096 permanent compact guards, each at most 1 KiB, with no expiry or eviction; reject new request keys at capacity while retaining reads and same-key diagnosis.
- Database: max_page_count enforces 96 MiB; use bounded SQLite transactions and DELETE rollback journaling, with at most 96 MiB additional rollback space. A writer checks limits before accepting another operation.
- Query readers use mode=ro, query_only, a two-second busy bound and bounded SELECTs. They never initialize, migrate, prune, checkpoint, reconcile or archive.

Retention removes diagnostic history only. Existing recovery/activation ownership, job retry guards and instance cleanup rules remain in their owning repositories. Do not delete or weaken them when pruning this journal.

## QueryProjection

Fields: schema_version, ok, error, query_scope, recorded_at, observation_mode, latest_attempt, latest_retained_complete_success, selected_operation, recorded_source_evidence (optional), current_observation, history, next_action, next_action_reason (optional query-only reason; never part of a retained operation).

ok means the query was serviced, not that delivery succeeded. Each delivery summary exposes delivery_succeeded separately. observation_mode is recorded_only or current_read_only. Missing stores/capabilities still return a useful typed partial projection when possible.

recorded_source_evidence is an optional closed evidence-block map using the same block allowlist, validators and bounds as current_observation. It holds stored owner projections read without mutation, including legacy authority when no journal record exists, and may appear in recorded_only mode. Preserve source timestamps and exact join requirements; absent evidence may be omitted or null. current_observation remains reserved for current read-only observations requested by --observe and is null or absent in recorded_only mode. Stored owner-only evidence never creates a journal/history row or manufactures latest_retained_complete_success; missing journal history remains explicitly partial/unsupported.

Default target response contains compact latest/last-success summaries plus a history page of at most ten summaries. Caller limit is 1–50; full response is at most 256 KiB. An operation query returns one document, with safe references to separately authorized evidence. Cursor is an opaque bounded versioned target/filter/snapshot/last-sequence token, at most 512 bytes; it has no mutation authority. Invalid, cross-target, expired or retention-invalidated cursors return an explicit code without silently restarting.

history includes completeness, retention policy, oldest_retained_at, omitted/expired indicators, returned_count, next_cursor, and limits. A bounded answer is never called complete for all time. Current observations never change a retained terminal snapshot or generate a terminal history row.

Extended recovery receipts may additionally retain `invocation_root_digest` when
the resolved original child command selector differs from its source directory.
This binds the original argv selector only; the job/source root and commit stay
unchanged. Legacy absence retains the existing source-root argument check.

## Deployment trace model family (US6)

[Deployment trace v1](contracts/deployment-trace.md) is the canonical complete schema and limit definition for the additional model family below. It is separate from DeliveryOperation and the legacy remote-required QueryProjection; neither legacy codec becomes nullable or gains producer workload authority.

| Model | Owner and purpose |
|---|---|
| TraceStartV1 / TraceRecordV1 | Closed early invocation intent and diagnostic mutations; unresolved bootstrap identity is explicitly partial and known identity is immutable. |
| TraceDocumentV1 | One invocation's retained command/deployment results, stages, bounded events, original context and stable parent relation. Terminal command snapshot is immutable. |
| ProducerOwnerProjectionV1 | Fixed decoded Lenzora owner snapshot with exact deterministic parent/stage/phase links and provenance; producer reports alone are not native deployment proof. |
| ProducerRunRecord | Stable scoped parent shared by resumed invocations and unchanged workers; monotonic role stages/projection, immutable link identities and bounded publication receipts. |
| TraceQueryDetailV1 / TraceQueryProjectionV1 | Separate closed presentation codecs with explicit optional-detail omissions, same-controller scope, exact native owner evidence, parent stages, related recoveries and joined result. Never written back as retained documents. |
| TraceMutationReceipt / ParentPublicationReceipt | Original content digest, accepted sequence and original accepted snapshot digest; lookup/replay evidence, never workload authority. |
| TraceQueryBudget | One cooperative monotonic deadline passed through all capped owner reads and serialization; explicit budget-exhausted coverage. |

Trace and parent detail use a separate 128 MiB store with bounded rollback, a shared permanent 4,096-key trace/parent guard budget, independent finite protected/terminal counts, 30-day detail and 256 receipts per record. Exact caps and terminal/stage/CAS rules are normative in the contract. Guard expiry never permits a new attempt, and missing recovery history never proves recovery absence. These diagnostic limits do not replace existing delivery/creation owner caps.

W11's strict preflight schema adds optional trace metadata so old reports stay readable; normal JSON adds the closed DeploymentCommandResultV1 envelope. Command success, recorded deployment result and current joined owner result remain distinct. Source roles distinguish wrapper control, application release and inherited original deployment metadata.
