# Product Requirements Draft: Google Drive Backups for Permanent Instances

**Status**: Refined

**Created**: 2026-07-22

**Last Refined**: 2026-07-23

**Input**: "Add built-in scheduled Google Drive backups for permanent sandbox instances. Scope: database, uploads, and sanitized recovery metadata; plugin source remains in Git. Default retention: 7 daily and 4 weekly backups. Include manual backup, scheduled execution, integrity verification, restore, failure reporting, and safe Google Drive authorization without creating accounts or exposing secrets."

**Drafting Model**: `gpt-5.6-sol` (active-session fallback; preferred `gpt-5.6-terra` Medium was unavailable)

**Final Validation**: `PENDING` — independent `gpt-5.6-sol` High override unavailable

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Permanent WordPress instances can hold production database and media state that
cannot be reconstructed from Git. Existing local snapshots live beside the
instance they protect and may disappear with host or instance loss. Existing
scoped recovery can safely encrypt and publish selected material, but permanent
instances do not yet have a complete, opt-in product flow that captures their
recoverable state, runs automatically, verifies remote durability, applies a
bounded retention policy, and restores into a usable instance.

Operators need an off-host Google Drive recovery path that is automatic enough
to be dependable, explicit enough to avoid backing up disposable development
instances, and safe enough to avoid leaking credentials or silently replacing
live production data.

## Users and Desired Outcomes

- **Permanent-instance operator**: Keep recent, verified, off-host recovery
  points without manually exporting database and uploads every day.
- **Incident responder**: Identify newest recoverable backup, understand its
  source and integrity, and restore it into a fresh target after host loss.
- **Site administrator**: Perform an explicitly confirmed in-place restore when
  faster rollback is needed, while retaining a recovery point for overwritten
  state.
- **Security-conscious maintainer**: Use an existing Google Drive identity and
  external secret channels without placing OAuth tokens, encryption secrets, or
  provider credentials in project configuration, logs, manifests, or chat.

## Goals

- Allow operators to explicitly opt eligible production WordPress instances into
  Google Drive backup.
- Capture database, uploads, and sanitized recovery metadata sufficient to
  identify and restore one instance.
- Support both on-demand backup and dependable daily scheduled backup on the
  instance host.
- Encrypt backup content before it leaves the instance host and verify the
  uploaded recovery point before reporting success.
- Retain 7 daily and 4 weekly verified recovery points by default without
  deleting the only recoverable copy.
- Expose bounded, redacted status for success, failure, skipped runs, pending
  publication, verification, retention, and restore.
- Restore a verified backup into either a fresh target or an existing target
  through separate safety gates.
- Preserve compatibility with existing scoped-recovery safety rules rather than
  reviving superseded broad machine-backup behavior.

## Non-Goals

- Back up ordinary disposable development instances by default.
- Back up plugin source, repositories, worktrees, WordPress core, containers,
  images, caches, logs, sockets, or unrelated host files.
- Replace Git as source-of-truth for plugin and application code.
- Create Google accounts, Drive identities, OAuth applications, or encryption
  secrets for the operator.
- Store or display OAuth tokens, Drive credentials, SSH credentials, database
  passwords, or encryption passphrases.
- Synchronize or mirror an entire host to Google Drive.
- Treat a backup as successful before remote integrity verification completes.
- Support generic Compose workloads or Herd instances in first release.
- Automatically promote a fresh restored target into production traffic.

## Product Scenarios

### Scenario 1 — Opt in a permanent instance

- **Starting state**: Operator has an eligible managed-production or persistent
  remote WordPress instance and an existing host-local Google Drive identity and
  encryption secret channel.
- **User action**: Operator explicitly enables backups for that instance.
- **Expected outcome**: Product validates eligibility and backup prerequisites,
  identifies exact protected instance and destination without revealing secrets,
  and keeps backups disabled when any required prerequisite is unsafe or missing.

### Scenario 2 — Create and verify an on-demand backup

- **Starting state**: Eligible instance is opted in and healthy enough to capture.
- **User action**: Operator requests an on-demand backup.
- **Expected outcome**: Product briefly blocks site writes, captures database,
  uploads, and sanitized recovery metadata on instance host, then restores normal
  write access. It encrypts captured state before upload, verifies complete remote
  recovery point, and reports stable recovery-point identity and verification
  result. Partial or unverifiable publication is never reported as successful.

### Scenario 3 — Run scheduled backup

- **Starting state**: Backup schedule is enabled after a verified on-demand backup
  and recovery drill satisfy activation gates.
- **User action**: Daily schedule becomes due, including after host downtime.
- **Expected outcome**: One non-overlapping backup attempt runs on instance host.
  Resource pressure or an existing run produces visible skipped state rather than
  false success. Missed schedules receive bounded catch-up behavior.

### Scenario 4 — Handle failed or interrupted publication

- **Starting state**: Capture succeeds but encryption, upload, connectivity, or
  verification fails.
- **User action**: Operator checks backup status and retries when safe.
- **Expected outcome**: Product reports actionable redacted failure, preserves any
  verified encrypted pending material that can be resumed safely, and never
  publishes an incomplete recovery point as complete.

### Scenario 5 — Apply retention

- **Starting state**: Drive contains multiple complete, incomplete, legacy, or
  unverifiable objects for protected instance.
- **User action**: Verified scheduled backup completes and retention evaluation
  runs.
- **Expected outcome**: Product keeps 7 daily and 4 weekly verified recovery points
  by default. Newest verified point, only verified point, incomplete/unverifiable
  objects, and objects that cannot be confidently classified are protected from
  automatic deletion. Retention reports what it kept and removed without exposing
  secrets.

### Scenario 6 — Restore into fresh target

- **Starting state**: Original host or instance is unavailable, and operator has a
  fresh eligible WordPress target with no protected production state.
- **User action**: Operator selects verified recovery point and requests fresh-target
  restore.
- **Expected outcome**: Product verifies source, destination, decryptability,
  integrity, and target emptiness before mutation; restores database and uploads;
  validates restored site state; and leaves production traffic changes to separate
  explicit operation.

### Scenario 7 — Restore in place

- **Starting state**: Existing eligible instance contains live state that will be
  replaced.
- **User action**: Operator selects verified recovery point and explicitly confirms
  in-place restore.
- **Expected outcome**: Product creates and verifies pre-restore recovery point for
  current state, shows exact target and consequences, replaces database and uploads
  only after confirmation, validates result, and offers bounded rollback when
  restore fails. If target state cannot be checkpointed, normal restore is blocked;
  separate break-glass confirmation may authorize emergency overwrite while clearly
  recording absence of rollback point. Failure cannot be reported as success.

### Scenario 8 — Reject unsafe or ambiguous operations

- **Starting state**: Request targets disposable development instance, unsupported
  runtime, mismatched project/instance identity, unavailable credentials, wrong
  encryption secret, incomplete backup, or ambiguous destination.
- **User action**: Operator requests backup, retention, or restore.
- **Expected outcome**: Product fails before destructive or remote side effects,
  reports stable redacted reason, and does not guess target, credentials, or backup
  identity.

## Proposed Product Behavior

- Backup eligibility is limited to managed-production or persistent remote
  WordPress instances explicitly opted in by operator. Development instances
  remain excluded unless their classification changes through an explicit product
  action.
- Backup work runs on protected instance host. That host owns access to WordPress
  state, configured Google Drive identity, and encryption secret channel.
- Each recovery point contains logical database export, uploads, and sanitized
  metadata needed to identify source, scope, creation outcome, integrity, and
  restore compatibility. Plugin source is referenced through source identity but
  not copied into backup. Capture uses a brief write freeze so database and uploads
  represent one recoverable site state, and normal writes resume after every
  success or failure.
- All protected content is encrypted before leaving host. Remote completion
  becomes visible only after complete uploaded content is verified. Incomplete and
  unverifiable states remain distinguishable.
- Scheduled backup defaults to daily execution with overlap prevention, bounded
  runtime, visible skip/failure state, and missed-run catch-up. Schedule activation
  requires one verified real backup and one successful disposable fresh-target
  restore drill.
- Default retention keeps 7 daily and 4 weekly verified recovery points. Pruning
  occurs only after new recovery point is verified and must never remove only
  verified or newest verified recovery point.
- Fresh-target restore and in-place restore are distinct operations. Fresh restore
  requires empty/new target. In-place restore normally requires exact target
  confirmation and verified pre-restore recovery point. When checkpoint creation is
  impossible because target is already unrecoverable, separate break-glass action
  may authorize overwrite without rollback point; normal confirmation is not enough.
- Backup, retention, and restore status remains bounded and redacted. Provider and
  encryption secrets never appear in command arguments, stored manifests, output,
  logs, or instance configuration.
- Authorization consumes operator-configured Google Drive identity; product does
  not create accounts or provider applications. Missing or invalid authorization
  blocks operation with setup guidance.
- Existing local snapshots remain useful for fast local rollback but do not count
  as off-host backup evidence.

## Constraints and Dependencies

- Per-project instance identity remains authoritative; no implicit global or
  fallback instance may be backed up or restored.
- Protected instance and Drive destination must be explicit and stable across
  scheduled runs.
- Production credentials and development credentials must remain separate and
  must never be selected by inference.
- Encryption passphrase and OAuth/provider tokens must remain external secrets and
  must not cross CLI/MCP result boundaries.
- First release supports Docker-backed managed-production or persistent remote
  WordPress instances. Herd and generic Compose need separate product definitions.
- Restore mutates database and uploads and therefore requires capability checks,
  exact target resolution, confirmation, integrity checks, and recoverable failure
  handling before side effects.
- Backup schedule cannot be activated solely because configuration exists; live
  backup and restore evidence is required first.
- Retention must fail closed when object age, identity, decryptability, or
  completeness cannot be established.
- Remote outage, Drive quota, credential expiry, host disk pressure, large uploads,
  and interrupted processes are expected operating conditions, not exceptional
  cases to hide.
- Existing scoped recovery safety and compatibility behavior remains rollback
  control until replacement parity is proven.
- This feature begins formal specification and implementation only after current
  remote-job-runtime T143 phase is verified, committed, and pushed.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Product form | Built-in scheduled backup with manual execution | Dependable protection requires both automation and operator-triggered recovery points | User, 2026-07-22 |
| Backup scope | Database, uploads, sanitized recovery metadata | Protects non-Git production state while avoiding broad host backup | User, 2026-07-22 |
| Source code | Remains in Git; excluded from Drive backup | Avoids duplicate authority and oversized archives | User, 2026-07-22 |
| Default retention | 7 daily and 4 weekly verified points | Bounded storage with useful short- and medium-term rollback | User, 2026-07-22 |
| Eligibility | Explicitly opted-in managed-production or persistent remote WordPress instances | Prevents disposable development data from entering production backup flow | User, 2026-07-23 |
| Execution location | Protected instance host | Avoids routing database/uploads through developer machine and keeps host-local capture coherent | User, 2026-07-23 |
| Restore modes | Fresh-target and explicitly confirmed in-place restore | Supports disaster recovery and fast rollback while preserving separate safety gates | User, 2026-07-23 |
| Encryption | Client-side before Drive upload using external secret channel | Existing security boundary prevents provider or project configuration from seeing plaintext secrets | Existing policy and requested security scope |
| Authorization | Consume existing operator-configured Google Drive identity; create no account or provider app | Complies with no-live-account-creation policy and keeps credential ownership explicit | Existing policy and requested authorization scope |
| Schedule activation | Require verified backup and disposable fresh-target restore drill | Automation must not run before recoverability is demonstrated | Existing recovery policy |
| Capture consistency | Briefly freeze site writes during database and uploads capture | One recovery point should represent one coherent production state | User, 2026-07-23 |
| In-place safety | Require verified pre-restore recovery point and exact confirmation under normal conditions | Live replacement should remain recoverable and unambiguous | User restore choice plus existing destructive-operation policy |
| Emergency in-place restore | Permit separate break-glass override when current target cannot be checkpointed | Already-unrecoverable target may still need direct disaster restoration | User, 2026-07-23 |

## Open Questions

- None blocking product scope. Independent Sol High validation remains unavailable
  in current agent tool configuration and therefore readiness cannot yet be marked
  complete.

## Acceptance Outcomes

- Operator can identify eligible permanent instance, opt it in, and see backup
  prerequisites without any secret value appearing in output or persisted product
  configuration.
- On-demand run creates remotely verified encrypted recovery point containing
  database, uploads, and sanitized metadata, with no plugin source content.
- Interrupted or failed upload never appears as successful recovery point, and
  status distinguishes capture, pending, incomplete, unverifiable, skipped, failed,
  and verified outcomes.
- Scheduled backup can run daily on instance host, prevents overlap, performs
  bounded catch-up after downtime, and exposes last attempt and last verified point.
- With more than retention window, verified set converges to 7 daily and 4 weekly
  points while preserving newest and only verified points and failing closed on
  ambiguous objects.
- Fresh-target drill restores selected verified point into empty disposable target,
  validates database and uploads, and demonstrates usable site state without
  changing production traffic.
- Backup capture briefly blocks writes, produces database and uploads from one
  coherent state, and restores write access after both successful and failed runs.
- In-place restore refuses without explicit exact-target confirmation and verified
  pre-restore point under normal conditions; successful restore validates result,
  and induced failure leaves actionable rollback path.
- When current target cannot be checkpointed, in-place restore remains blocked until
  operator invokes distinct break-glass confirmation; resulting status records that
  no pre-restore rollback point exists.
- Wrong passphrase, expired Drive authorization, quota exhaustion, insufficient host
  space, identity mismatch, unsupported runtime, and remote outage fail with stable
  redacted status and no false success.
- Ordinary development, Herd, and generic Compose instances remain excluded in
  first release.
- Live evidence demonstrates backup, remote verification, retention, fresh restore,
  in-place restore, rollback, schedule behavior, and failure reporting before
  automatic schedule activation is accepted.

## Risks and Assumptions

- **Risk**: Brief write freeze may affect production availability when database or
  uploads capture takes longer than expected; backup must bound this impact and
  always restore write access.
- **Risk**: Large uploads or slow Drive links may exceed host disk, quota, or bounded
  execution time.
- **Risk**: Host-local OAuth or encryption credentials can expire or become
  unavailable while schedule continues firing.
- **Risk**: In-place restore can extend downtime or leave mixed state if checkpoint,
  replacement, validation, and rollback are not treated as one protected operation.
- **Risk**: Retention bugs can destroy recovery depth; ambiguous objects must remain
  protected even when that delays pruning.
- **Risk**: A backup can be internally valid yet operationally unusable because
  source code/version or runtime compatibility changed.
- **Assumption**: Operator already controls Google Drive identity, encryption secret,
  and enough Drive quota; product provides no account creation.
- **Assumption**: Eligible permanent instance is Docker-backed WordPress and can
  produce logical database export and bounded uploads archive on its host.
- **Assumption**: Git source identity and sanitized recovery metadata are sufficient
  to reacquire compatible code separately from backup.
- **Assumption**: Operator accepts automatic deletion only within confirmed 7-daily
  and 4-weekly policy after successful verification safeguards apply.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
